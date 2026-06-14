from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from services.account_service import account_service
from services.config import DATA_DIR
from services.log_service import LOG_TYPE_ACCOUNT, log_service
from services.register import mail_provider, openai_register


REGISTER_FILE = DATA_DIR / "register.json"


def _serialize_outlook_pool(credentials: list[dict]) -> str:
    return "\n".join(
        f'{c["email"]}----{c.get("password", "")}----{c["client_id"]}----{c["refresh_token"]}' for c in credentials
    )


def _merge_outlook_pool(old_text: str, new_text: str) -> str:
    """合并已存邮箱池与新导入文本，按邮箱去重，新导入的同名邮箱覆盖旧凭据。"""
    merged: dict[str, dict] = {}
    for credential in mail_provider.parse_outlook_credentials(old_text or ""):
        merged[credential["email"].strip().lower()] = credential
    for credential in mail_provider.parse_outlook_credentials(new_text or ""):
        merged[credential["email"].strip().lower()] = credential
    return _serialize_outlook_pool(list(merged.values()))


def _provider_id(value: object) -> str:
    text = str(value or "").strip()
    return text if text else uuid.uuid4().hex


def _normalize_mail_providers(cfg: dict) -> None:
    mail = cfg.get("mail")
    if not isinstance(mail, dict):
        return
    providers = mail.get("providers")
    if not isinstance(providers, list):
        mail["providers"] = []
        return
    for provider in providers:
        if isinstance(provider, dict):
            provider["provider_id"] = _provider_id(provider.get("provider_id") or provider.get("id"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_auto_refill_config() -> dict:
    return {
        "enabled": False,
        "min_available": 30,
        "batch_total": 100,
        "check_interval": 300,
    }


def _default_config() -> dict:
    return {
        **openai_register.config,
        "mode": "total",
        "target_quota": 100,
        "target_available": 10,
        "check_interval": 5,
        "auto_refill": _default_auto_refill_config(),
        "enabled": False,
        "stats": {
            "success": 0,
            "fail": 0,
            "done": 0,
            "running": 0,
            "threads": openai_register.config["threads"],
            "elapsed_seconds": 0,
            "avg_seconds": 0,
            "success_rate": 0,
            "current_quota": 0,
            "current_available": 0,
        },
    }


def _normalize(raw: dict) -> dict:
    cfg = _default_config()
    cfg.update({k: v for k, v in raw.items() if k not in {"stats", "logs"}})
    cfg["total"] = max(1, int(cfg.get("total") or 1))
    cfg["threads"] = max(1, int(cfg.get("threads") or 1))
    cfg["mode"] = str(cfg.get("mode") or "total").strip() if str(cfg.get("mode") or "total").strip() in {"total", "quota", "available"} else "total"
    cfg["target_quota"] = max(1, int(cfg.get("target_quota") or 1))
    cfg["target_available"] = max(1, int(cfg.get("target_available") or 1))
    cfg["check_interval"] = max(1, int(cfg.get("check_interval") or 5))
    cfg["proxy"] = str(cfg.get("proxy") or "").strip()
    auto_refill_raw = cfg.get("auto_refill") if isinstance(cfg.get("auto_refill"), dict) else {}
    auto_refill = {**_default_auto_refill_config(), **auto_refill_raw}
    auto_refill["enabled"] = bool(auto_refill.get("enabled"))
    auto_refill["min_available"] = max(1, int(auto_refill.get("min_available") or 1))
    auto_refill["batch_total"] = max(1, int(auto_refill.get("batch_total") or 1))
    auto_refill["check_interval"] = max(10, int(auto_refill.get("check_interval") or 300))
    cfg["auto_refill"] = auto_refill
    if isinstance(cfg.get("mail"), dict):
        cfg["mail"].pop("proxy", None)
    _normalize_mail_providers(cfg)
    cfg["enabled"] = bool(cfg.get("enabled"))
    stats = {**_default_config()["stats"], **(raw.get("stats") if isinstance(raw.get("stats"), dict) else {}),
             "threads": cfg["threads"]}
    cfg["stats"] = stats
    return cfg


class RegisterService:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = threading.RLock()
        self._runner: threading.Thread | None = None
        self._logs: list[dict] = []
        openai_register.register_log_sink = self._append_log
        self._config = self._load()
        if self._config["enabled"]:
            self.start()

    def _load(self) -> dict:
        try:
            return _normalize(json.loads(self._store_file.read_text(encoding="utf-8")))
        except Exception:
            return _normalize({})

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(json.dumps(self._config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get(self) -> dict:
        with self._lock:
            snapshot = json.loads(json.dumps({**self._config, "logs": self._logs[-300:]}, ensure_ascii=False))
        self._redact_outlook_pools(snapshot)
        return snapshot

    @staticmethod
    def _mask_email(email: str) -> str:
        local, sep, domain = str(email or "").partition("@")
        if not sep:
            return "***"
        masked = (local[:2] + "***" + local[-1:]) if len(local) > 2 else (local[:1] + "***")
        return f"{masked}@{domain}"

    def _redact_outlook_pools(self, snapshot: dict) -> None:
        """把 outlook_token 邮箱池里的密码/refresh_token 从对外输出中抹掉，仅保留脱敏预览与统计。

        mailboxes 改为只写导入框（输出为空），避免把密码与 refresh_token 通过 GET/SSE 反复广播。
        """
        mail = snapshot.get("mail")
        if not isinstance(mail, dict):
            return
        providers = mail.get("providers")
        if not isinstance(providers, list):
            return
        for provider in providers:
            if not isinstance(provider, dict) or provider.get("type") != "outlook_token":
                continue
            credentials = mail_provider.parse_outlook_credentials(str(provider.get("mailboxes") or ""))
            provider["mailboxes"] = ""
            provider["mailboxes_count"] = len(credentials)
            provider["mailboxes_preview"] = [self._mask_email(c["email"]) for c in credentials]
            provider["mailboxes_stats"] = mail_provider.outlook_token_pool_stats(credentials)

    def _drop_mail_proxy(self) -> None:
        if isinstance(self._config.get("mail"), dict):
            self._config["mail"].pop("proxy", None)

    def _merge_outlook_pools(self, updates: dict) -> None:
        """对 outlook_token provider：把前端新导入的 mailboxes 与已存池按邮箱合并去重。

        前端 mailboxes 是只写导入框，留空表示不改动；填入的新行追加/覆盖已存凭据。
        优先按 provider_id 对齐；旧配置没有 provider_id 时才按数组下标兼容。
        """
        mail = updates.get("mail")
        if not isinstance(mail, dict) or not isinstance(mail.get("providers"), list):
            return
        old_mail = self._config.get("mail") if isinstance(self._config.get("mail"), dict) else {}
        old_providers = old_mail.get("providers") if isinstance(old_mail.get("providers"), list) else []
        old_by_id = {
            str(item.get("provider_id") or "").strip(): item
            for item in old_providers
            if isinstance(item, dict) and item.get("type") == "outlook_token" and str(item.get("provider_id") or "").strip()
        }
        old_outlook_providers = [item for item in old_providers if isinstance(item, dict) and item.get("type") == "outlook_token"]
        outlook_without_id_index = 0
        for index, provider in enumerate(mail["providers"]):
            if not isinstance(provider, dict) or provider.get("type") != "outlook_token":
                continue
            incoming_provider_id = str(provider.get("provider_id") or provider.get("id") or "").strip()
            provider["provider_id"] = _provider_id(incoming_provider_id)
            old = (old_by_id.get(incoming_provider_id) or {}) if incoming_provider_id else {}
            if not old and not incoming_provider_id and outlook_without_id_index < len(old_outlook_providers):
                old = old_outlook_providers[outlook_without_id_index]
                outlook_without_id_index += 1
            if not old and not incoming_provider_id and index < len(old_providers) and isinstance(old_providers[index], dict):
                old = old_providers[index]
            old_text = str(old.get("mailboxes") or "") if old.get("type") == "outlook_token" else ""
            new_text = str(provider.get("mailboxes") or "")
            provider["mailboxes"] = _merge_outlook_pool(old_text, new_text) if (old_text or new_text) else ""
            for key in ("mailboxes_count", "mailboxes_preview", "mailboxes_stats"):
                provider.pop(key, None)

    def _prune_unused_outlook_pools(self) -> int:
        mail = self._config.get("mail")
        if not isinstance(mail, dict):
            return 0
        providers = mail.get("providers")
        if not isinstance(providers, list):
            return 0
        total_removed = 0
        for provider in providers:
            if not isinstance(provider, dict) or provider.get("type") != "outlook_token":
                continue
            credentials = mail_provider.parse_outlook_credentials(str(provider.get("mailboxes") or ""))
            kept, removed = mail_provider.prune_outlook_unused_credentials(credentials)
            if removed:
                provider["mailboxes"] = _serialize_outlook_pool(kept)
                total_removed += removed
            for key in ("mailboxes_count", "mailboxes_preview", "mailboxes_stats"):
                provider.pop(key, None)
        return total_removed

    @staticmethod
    def _inject_proxy_to_run_config(run_config: dict) -> dict:
        next_config = dict(run_config)
        proxy = str(next_config.get("proxy") or "").strip()
        if proxy and isinstance(next_config.get("mail"), dict):
            next_config["mail"] = {**next_config["mail"], "proxy": proxy}
        return next_config

    def update(self, updates: dict) -> dict:
        with self._lock:
            self._merge_outlook_pools(updates)
            self._config = _normalize({**self._config, **updates})
            self._drop_mail_proxy()
            openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
            self._save()
            return self.get()

    def start(self) -> dict:
        return self._start(trigger="manual")

    def _start(
        self,
        trigger: str = "manual",
        run_overrides: dict | None = None,
        trigger_log: str | None = None,
    ) -> dict:
        with self._lock:
            if self._runner and self._runner.is_alive():
                self._config["enabled"] = True
                self._save()
                if trigger == "auto_refill":
                    current_available = int(self._config.get("stats", {}).get("current_available") or 0)
                    auto_refill = self._config.get("auto_refill") if isinstance(self._config.get("auto_refill"), dict) else {}
                    self._log_auto_refill_decision(
                        started=False,
                        reason="register_task_running",
                        current_available=current_available,
                        min_available=max(1, int(auto_refill.get("min_available") or 1)),
                        batch_total=max(1, int((run_overrides or {}).get("total") or auto_refill.get("batch_total") or 1)),
                        message=trigger_log or "",
                    )
                return self.get()
            run_config = self._inject_proxy_to_run_config(_normalize({**self._config, **(run_overrides or {})}))
            self._config["enabled"] = True
            self._drop_mail_proxy()
            self._logs = []
            metrics = self._pool_metrics()
            self._config["stats"] = {
                "job_id": uuid.uuid4().hex,
                "success": 0,
                "fail": 0,
                "done": 0,
                "running": 0,
                "threads": run_config["threads"],
                **metrics,
                "started_at": _now(),
                "updated_at": _now(),
                "trigger": trigger,
                "run_mode": run_config["mode"],
                "run_total": run_config["total"],
            }
            openai_register.config.update({k: run_config[k] for k in ("mail", "proxy", "total", "threads")})
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": time.time()})
            self._save()
            self._runner = threading.Thread(target=self._run, args=(run_config, trigger), daemon=True, name="openai-register")
            self._runner.start()
            if trigger_log:
                self._append_log(trigger_log, "yellow")
            if trigger == "auto_refill":
                self._log_auto_refill_decision(
                    started=True,
                    reason="below_min_available",
                    current_available=int(metrics.get("current_available") or 0),
                    min_available=max(1, int(run_config.get("auto_refill", {}).get("min_available") or 1)),
                    batch_total=max(1, int(run_config.get("total") or 1)),
                    message=trigger_log or "",
                )
            self._append_log(f"注册任务启动，模式={run_config['mode']}，线程数={run_config['threads']}，触发={trigger}", "yellow")
            return self.get()

    def start_auto_refill(self, batch_total: int, trigger_log: str | None = None) -> dict:
        if not trigger_log:
            trigger_log = f"自动补号触发：本轮注册={max(1, int(batch_total or 1))}"
        return self._start(
            trigger="auto_refill",
            run_overrides={"mode": "total", "total": batch_total},
            trigger_log=trigger_log,
        )

    def stop(self) -> dict:
        with self._lock:
            self._config["enabled"] = False
            self._config["stats"]["updated_at"] = _now()
            self._save()
            self._append_log("已请求停止注册任务，正在等待当前运行任务结束", "yellow")
            return self.get()

    def reset(self) -> dict:
        with self._lock:
            self._logs = []
            self._config["stats"] = {"success": 0, "fail": 0, "done": 0, "running": 0, "threads": self._config["threads"], "elapsed_seconds": 0, "avg_seconds": 0, "success_rate": 0, **self._pool_metrics(), "updated_at": _now()}
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": 0.0})
            self._save()
            return self.get()

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._runner and self._runner.is_alive())

    def reset_outlook_pool(self, scope: str = "all") -> dict:
        scope = str(scope or "all").strip().lower()
        if scope == "unused":
            with self._lock:
                removed = self._prune_unused_outlook_pools()
                openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
                self._save()
                self._append_log(f"已清空 Outlook 邮箱池未使用邮箱，移除 {removed} 个", "yellow")
            return self.get()
        scope = "failed" if str(scope) == "failed" else "all"
        cleared = mail_provider.reset_outlook_token_pool_state(scope)
        with self._lock:
            self._append_log(
                f"已重置 Outlook 邮箱池状态（范围={'仅失败/占用' if scope == 'failed' else '全部'}），清除 {cleared} 条记录",
                "yellow",
            )
        return self.get()

    def _append_log(self, text: str, color: str = "") -> None:
        with self._lock:
            self._logs.append({"time": _now(), "text": str(text), "level": str(color or "info")})
            self._logs = self._logs[-300:]

    @staticmethod
    def _add_account_log(summary: str, detail: dict) -> None:
        try:
            log_service.add(LOG_TYPE_ACCOUNT, summary, detail)
        except Exception:
            pass

    def _log_auto_refill_decision(
        self,
        *,
        started: bool,
        reason: str,
        current_available: int,
        min_available: int,
        batch_total: int,
        message: str = "",
    ) -> None:
        detail = {
            "trigger": "auto_refill",
            "started": started,
            "reason": reason,
            "current_available": current_available,
            "min_available": min_available,
            "batch_total": batch_total,
        }
        if message:
            detail["message"] = message
        self._add_account_log("自动补号启动" if started else "自动补号跳过", detail)

    def _pool_metrics(self) -> dict:
        items = account_service.list_accounts()
        normal = [item for item in items if item.get("status") == "正常"]
        return {
            "current_quota": sum(int(item.get("quota") or 0) for item in normal if not item.get("image_quota_unknown")),
            "current_available": len(normal),
        }

    def _target_reached(self, cfg: dict, submitted: int) -> bool:
        mode = str(cfg.get("mode") or "total")
        metrics = self._pool_metrics()
        self._bump(**metrics)
        if mode == "quota":
            reached = metrics["current_quota"] >= int(cfg.get("target_quota") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，当前剩余额度={metrics['current_quota']}，目标额度={cfg.get('target_quota')}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        if mode == "available":
            reached = metrics["current_available"] >= int(cfg.get("target_available") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，目标账号={cfg.get('target_available')}，当前剩余额度={metrics['current_quota']}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        return submitted >= int(cfg.get("total") or 1)

    def _bump(self, **updates) -> None:
        with self._lock:
            self._config["stats"].update(updates)
            stats = self._config["stats"]
            started_at = str(stats.get("started_at") or "")
            if started_at:
                try:
                    elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds())
                except Exception:
                    elapsed = 0.0
                done = int(stats.get("done") or 0)
                success = int(stats.get("success") or 0)
                fail = int(stats.get("fail") or 0)
                stats["elapsed_seconds"] = round(elapsed, 1)
                stats["avg_seconds"] = round(elapsed / success, 1) if success else 0
                stats["success_rate"] = round(success * 100 / max(1, success + fail), 1)
            self._config["stats"]["updated_at"] = _now()
            self._save()

    def _run(self, run_config: dict | None = None, trigger: str = "manual") -> None:
        base_config = dict(run_config or self.get())
        threads = int(base_config["threads"])
        submitted, done, success, fail = 0, 0, 0, 0
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = set()
            while True:
                cfg = dict(base_config if trigger == "auto_refill" else self.get())
                while self.get()["enabled"] and not self._target_reached(cfg, submitted) and len(futures) < threads:
                    submitted += 1
                    futures.add(executor.submit(openai_register.worker, submitted))
                self._bump(running=len(futures), done=done, success=success, fail=fail)
                if not futures and (not self.get()["enabled"] or str(cfg.get("mode") or "total") == "total"):
                    break
                if not futures:
                    time.sleep(max(1, int(cfg.get("check_interval") or 5)))
                    continue
                finished, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += 1
                    try:
                        result = future.result()
                        success += 1 if result.get("ok") else 0
                        fail += 0 if result.get("ok") else 1
                    except Exception:
                        fail += 1
        self._bump(running=0, done=done, success=success, fail=fail, finished_at=_now())
        with self._lock:
            self._config["enabled"] = False
            self._save()
        self._append_log(f"注册任务结束，成功{success}，失败{fail}", "yellow")

    def start_auto_refill_watcher(self, stop_event: threading.Event) -> threading.Thread:
        thread = threading.Thread(
            target=self._auto_refill_loop,
            args=(stop_event,),
            daemon=True,
            name="register-auto-refill",
        )
        thread.start()
        return thread

    def _auto_refill_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            cfg = self.get()
            auto_refill = cfg.get("auto_refill") if isinstance(cfg.get("auto_refill"), dict) else {}
            interval = max(10, int(auto_refill.get("check_interval") or 300))
            if auto_refill.get("enabled"):
                metrics = self._pool_metrics()
                min_available = max(1, int(auto_refill.get("min_available") or 1))
                batch_total = max(1, int(auto_refill.get("batch_total") or 1))
                running = bool(cfg.get("enabled")) or self.is_running()
                if metrics["current_available"] < min_available and running:
                    self._log_auto_refill_decision(
                        started=False,
                        reason="register_task_running",
                        current_available=metrics["current_available"],
                        min_available=min_available,
                        batch_total=batch_total,
                    )
                elif metrics["current_available"] < min_available:
                    trigger_log = (
                        f"自动补号触发：当前正常账号={metrics['current_available']}，"
                        f"阈值={min_available}，本轮注册={batch_total}"
                    )
                    self.start_auto_refill(batch_total, trigger_log=trigger_log)
                else:
                    self._log_auto_refill_decision(
                        started=False,
                        reason="enough_available",
                        current_available=metrics["current_available"],
                        min_available=min_available,
                        batch_total=batch_total,
                    )
            if stop_event.wait(interval):
                break


register_service = RegisterService(REGISTER_FILE)
