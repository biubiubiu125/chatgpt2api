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
from services.register import openai_register


REGISTER_FILE = DATA_DIR / "register.json"


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
            return json.loads(json.dumps({**self._config, "logs": self._logs[-300:]}, ensure_ascii=False))

    def _inject_proxy_to_mail(self) -> None:
        proxy = str(self._config.get("proxy") or "").strip()
        if proxy and isinstance(self._config.get("mail"), dict):
            self._config["mail"]["proxy"] = proxy

    @staticmethod
    def _inject_proxy_to_run_config(run_config: dict) -> dict:
        next_config = dict(run_config)
        proxy = str(next_config.get("proxy") or "").strip()
        if proxy and isinstance(next_config.get("mail"), dict):
            next_config["mail"] = {**next_config["mail"], "proxy": proxy}
        return next_config

    def update(self, updates: dict) -> dict:
        with self._lock:
            self._config = _normalize({**self._config, **updates})
            self._inject_proxy_to_mail()
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
                return self.get()
            run_config = self._inject_proxy_to_run_config(_normalize({**self._config, **(run_overrides or {})}))
            self._config["enabled"] = True
            self._inject_proxy_to_mail()
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

    def _append_log(self, text: str, color: str = "") -> None:
        with self._lock:
            self._logs.append({"time": _now(), "text": str(text), "level": str(color or "info")})
            self._logs = self._logs[-300:]

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
                if metrics["current_available"] < min_available and not running:
                    trigger_log = (
                        f"自动补号触发：当前正常账号={metrics['current_available']}，"
                        f"阈值={min_available}，本轮注册={batch_total}"
                    )
                    self.start_auto_refill(batch_total, trigger_log=trigger_log)
            if stop_event.wait(interval):
                break


register_service = RegisterService(REGISTER_FILE)
