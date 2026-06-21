from __future__ import annotations

import glob
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi import requests

from services.config import DATA_DIR
from services.proxy_service import normalize_proxy_url, test_proxy


PROXY_INPUT_MODES = {"single", "url", "text", "proxy_checker_dir"}
DEFAULT_PROXY_CHECKER_DIR = "/opt/proxy-checker/repo_data"
DEFAULT_PROXY_CHECKER_PATTERN = "user_*.txt"
DEFAULT_PROXY_REFRESH_INTERVAL = 120
DEFAULT_PROXY_LEASE_SECONDS = 120
DEFAULT_PROXY_FAILURE_THRESHOLD = 2
DEFAULT_PROXY_BLACKLIST_SECONDS = 900
REGISTER_PROXY_CONNECT_TIMEOUT_SECONDS = 2.0
REGISTER_PROXY_MAX_LATENCY_MS = 2000
@dataclass
class RegisterProxySelection:
    proxy: str = ""
    source: str = "direct"
    source_label: str = "direct"
    count: int = 0
    proxy_index: int = -1
    lease_id: str = ""
    bind_to_account: bool = False
    selected_file: str = ""
    last_error: str = ""
    wait_retriable: bool = False


def normalize_proxy_input_mode(value: object) -> str:
    mode = str(value or "single").strip().lower()
    return mode if mode in PROXY_INPUT_MODES else "single"


def parse_proxy_lines(text: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\r\n,]+", str(text or "")):
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        item = normalize_proxy_url(item)
        if item and item not in seen:
            seen.add(item)
            values.append(item)
    return values


def classify_register_failure(error: object) -> str:
    text = str(error or "").lower()
    if not text:
        return "unknown_error"
    if "register_task_timeout" in text:
        return "task_timeout"
    if "register_proxy_unavailable" in text:
        return "register_proxy_unavailable"
    if "unsupported_email" in text or "email you provided is not supported" in text:
        return "unsupported_email"
    if "timed out" in text or "timeout" in text or "curl: (28)" in text:
        return "maybe_network_timeout"
    if "cloudflare" in text or "just a moment" in text or "cf-chl" in text or "status=403" in text:
        return "cloudflare_blocked"
    if "proxy" in text or "socks" in text or "connection" in text or "connect" in text or "network" in text:
        return "maybe_network_failed"
    if "mail" in text or "邮箱" in text or "验证码" in text or "verification" in text:
        return "mail_failed"
    if "token" in text or "oauth" in text:
        return "token_exchange_failed"
    if "create_account" in text or "user_register" in text or "failed to create account" in text:
        return "account_create_failed"
    return "unknown_error"


class RegisterProxyPool:
    def __init__(self, state_file: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._state_file = state_file or DATA_DIR / "register_proxy_state.json"
        self._mode = "single"
        self._single_proxy = ""
        self._proxy_url = ""
        self._proxy_list_text = ""
        self._proxy_checker_dir = DEFAULT_PROXY_CHECKER_DIR
        self._proxy_checker_pattern = DEFAULT_PROXY_CHECKER_PATTERN
        self._refresh_interval = DEFAULT_PROXY_REFRESH_INTERVAL
        self._lease_seconds = DEFAULT_PROXY_LEASE_SECONDS
        self._bind_url = False
        self._bind_text = False
        self._bind_proxy_checker = True
        self._failure_threshold = DEFAULT_PROXY_FAILURE_THRESHOLD
        self._blacklist_seconds = DEFAULT_PROXY_BLACKLIST_SECONDS
        self._success_clear_failures = True
        self._proxies: list[str] = []
        self._proxy_index = 0
        self._last_fetch = 0.0
        self._last_error = ""
        self._selected_file = ""
        self._proxy_state = self._load_state()
        self._lease_seq = 0

    def configure(self, cfg: dict[str, Any]) -> None:
        with self._lock:
            next_mode = normalize_proxy_input_mode(cfg.get("proxy_input_mode"))
            next_single_proxy = normalize_proxy_url(str(cfg.get("proxy") or ""))
            next_proxy_url = str(cfg.get("proxy_url") or "").strip()
            next_proxy_list_text = str(cfg.get("proxy_list_text") or "")
            next_proxy_checker_dir = str(cfg.get("proxy_checker_dir") or DEFAULT_PROXY_CHECKER_DIR).strip()
            next_proxy_checker_pattern = str(cfg.get("proxy_checker_pattern") or DEFAULT_PROXY_CHECKER_PATTERN).strip()
            source_changed = (
                next_mode != self._mode
                or next_single_proxy != self._single_proxy
                or next_proxy_url != self._proxy_url
                or next_proxy_list_text != self._proxy_list_text
                or next_proxy_checker_dir != self._proxy_checker_dir
                or next_proxy_checker_pattern != self._proxy_checker_pattern
            )
            self._mode = next_mode
            self._single_proxy = next_single_proxy
            self._proxy_url = next_proxy_url
            self._proxy_list_text = next_proxy_list_text
            self._proxy_checker_dir = next_proxy_checker_dir
            self._proxy_checker_pattern = next_proxy_checker_pattern
            self._refresh_interval = self._positive_int(cfg.get("proxy_refresh_interval"), DEFAULT_PROXY_REFRESH_INTERVAL)
            self._lease_seconds = self._positive_int(cfg.get("proxy_lease_seconds"), DEFAULT_PROXY_LEASE_SECONDS)
            self._bind_url = bool(cfg.get("proxy_bind_url"))
            self._bind_text = bool(cfg.get("proxy_bind_text"))
            self._bind_proxy_checker = bool(cfg.get("proxy_bind_proxy_checker", True))
            self._failure_threshold = self._positive_int(cfg.get("proxy_failure_threshold"), DEFAULT_PROXY_FAILURE_THRESHOLD)
            self._blacklist_seconds = self._positive_int(cfg.get("proxy_blacklist_seconds"), DEFAULT_PROXY_BLACKLIST_SECONDS)
            self._success_clear_failures = bool(cfg.get("proxy_success_clear_failures", True))
            if source_changed:
                self._proxies = []
                self._proxy_index = 0
                self._selected_file = ""
            self._last_fetch = 0.0
            self._last_error = ""

    def prepare(self, force: bool = True) -> None:
        with self._lock:
            self._refresh_locked(force=force)

    def next_proxy(self) -> RegisterProxySelection:
        with self._lock:
            self._refresh_locked(force=False)
            if self._mode == "single" and not self._single_proxy:
                return RegisterProxySelection(
                    source="direct",
                    source_label="直连",
                    count=0,
                    bind_to_account=False,
                )
            proxy, index = self._next_available_proxy_locked()
            if not proxy:
                return RegisterProxySelection(
                    source=self._mode,
                    source_label=self._source_label(),
                    count=len(self._proxies),
                    selected_file=self._selected_file,
                    wait_retriable=self._has_active_retry_window_locked(),
                    last_error=self._last_error or "没有可用注册代理",
                )
            self._lease_seq += 1
            lease_id = f"{int(time.time())}-{self._lease_seq}"
            item = self._state_for(proxy)
            item["lease_until"] = time.time() + self._lease_seconds
            item["lease_id"] = lease_id
            self._save_state_locked()
            return RegisterProxySelection(
                proxy=proxy,
                source=self._mode,
                source_label=self._source_label(),
                count=len(self._proxies),
                proxy_index=index,
                lease_id=lease_id,
                bind_to_account=self._should_bind_account(),
                selected_file=self._selected_file,
                last_error=self._last_error,
            )

    def report(self, selection: RegisterProxySelection | None, ok: bool, reason: str = "", error: object = "") -> None:
        if selection is None or not selection.proxy:
            return
        original_reason = str(reason or "") or classify_register_failure(error)
        proxy = selection.proxy
        with self._lock:
            item = self._state_for(proxy)
            if original_reason == "stopped":
                self._release_selection_locked(item, selection)
                self._save_state_locked()
                return
            if ok:
                self._release_selection_locked(item, selection)
                item["last_success_at"] = time.time()
                if self._success_clear_failures:
                    item["failure_count"] = 0
                    item.pop("cooldown_until", None)
                    item.pop("blacklist_until", None)
                    item.pop("blacklisted", None)
                self._save_state_locked()
                return

        probe = self._probe_proxy_connectivity(proxy)

        with self._lock:
            item = self._state_for(proxy)
            self._release_selection_locked(item, selection)
            item["last_proxy_probe_at"] = time.time()
            item["last_proxy_probe_ok"] = bool(probe.get("ok"))
            item["last_proxy_probe_latency_ms"] = int(probe.get("latency_ms") or 0)
            item["last_proxy_probe_error"] = str(probe.get("error") or "")[:500]
            if probe.get("ok"):
                item["last_non_proxy_failure_at"] = time.time()
                item["last_non_proxy_failure_reason"] = original_reason
                item["last_error"] = str(error or "")[:500]
                item.pop("failure_count", None)
                item.pop("cooldown_until", None)
                item.pop("blacklist_until", None)
                item.pop("blacklisted", None)
                self._save_state_locked()
                return
            failure_reason = "proxy_connect_failed"
            failure_count = int(item.get("failure_count") or 0) + 1
            item["failure_count"] = failure_count
            item["last_failure_at"] = time.time()
            item["last_failure_reason"] = failure_reason
            item["last_error"] = str(error or "")[:500]
            item.pop("cooldown_until", None)
            if failure_count >= self._failure_threshold:
                item.pop("blacklisted", None)
                item["blacklist_until"] = time.time() + self._blacklist_seconds
            self._save_state_locked()

    def release(self, proxy: object, lease_id: object = "") -> bool:
        proxy_url = normalize_proxy_url(str(proxy or "").strip())
        if not proxy_url:
            return False
        with self._lock:
            item = self._proxy_state.get(proxy_url)
            if not isinstance(item, dict):
                return False
            current_lease_id = str(item.get("lease_id") or "")
            expected_lease_id = str(lease_id or "")
            if expected_lease_id and current_lease_id and current_lease_id != expected_lease_id:
                return False
            had_lease = "lease_until" in item or "lease_id" in item
            item.pop("lease_until", None)
            item.pop("lease_id", None)
            if had_lease:
                self._save_state_locked()
            return had_lease

    def renew(self, proxy: object, lease_id: object = "") -> bool:
        proxy_url = normalize_proxy_url(str(proxy or "").strip())
        if not proxy_url:
            return False
        expected_lease_id = str(lease_id or "")
        if not expected_lease_id:
            return False
        with self._lock:
            item = self._proxy_state.get(proxy_url)
            if not item:
                return False
            if str(item.get("lease_id") or "") != expected_lease_id:
                return False
            item["lease_until"] = time.time() + self._lease_seconds
            self._save_state_locked()
            return True

    def reset_blacklist(self) -> int:
        with self._lock:
            count = 0
            for item in self._proxy_state.values():
                reset_keys = (
                    "failure_count",
                    "cooldown_until",
                    "blacklist_until",
                    "blacklisted",
                    "lease_until",
                    "lease_id",
                    "last_error",
                    "last_failure_reason",
                    "last_non_proxy_failure_reason",
                )
                if any(key in item for key in reset_keys):
                    count += 1
                for key in (
                    "failure_count",
                    "cooldown_until",
                    "blacklist_until",
                    "blacklisted",
                    "lease_until",
                    "lease_id",
                    "last_error",
                    "last_failure_reason",
                    "last_non_proxy_failure_at",
                    "last_non_proxy_failure_reason",
                ):
                    item.pop(key, None)
            self._save_state_locked()
            return count

    def state(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            leased = 0
            blacklisted = 0
            blacklist_until = 0
            changed = False
            for proxy in self._proxies:
                item = self._proxy_state.get(proxy) or {}
                if float(item.get("lease_until") or 0) > now:
                    leased += 1
                item_blacklist_until, item_changed = self._effective_blacklist_until_locked(item, now)
                changed = changed or item_changed
                if item_blacklist_until > now:
                    blacklisted += 1
                    blacklist_until = max(blacklist_until, item_blacklist_until)
            if changed:
                self._save_state_locked()
            return {
                "mode": self._mode,
                "source_label": self._source_label(),
                "count": len(self._proxies) if self._mode != "single" else (1 if self._single_proxy else 0),
                "last_error": self._last_error,
                "last_fetch": int(self._last_fetch) if self._last_fetch else 0,
                "selected_file": self._selected_file,
                "leased_count": leased,
                "blacklist_count": blacklisted,
                "blacklist_until": int(blacklist_until) if blacklist_until else 0,
            }

    def _refresh_locked(self, force: bool) -> None:
        if self._mode == "single":
            self._proxies = [self._single_proxy] if self._single_proxy else []
            self._last_fetch = time.time()
            self._last_error = ""
            return
        if self._mode == "text":
            self._proxies = parse_proxy_lines(self._proxy_list_text)
            self._last_fetch = time.time()
            self._last_error = "" if self._proxies else "手动代理列表为空"
            return
        if not force and self._last_fetch and time.time() - self._last_fetch < self._refresh_interval:
            return
        if self._mode == "url":
            self._refresh_url_locked()
        elif self._mode == "proxy_checker_dir":
            self._refresh_proxy_checker_dir_locked()

    def _refresh_url_locked(self) -> None:
        self._last_fetch = time.time()
        if not self._proxy_url:
            self._proxies = []
            self._last_error = "代理列表 URL 为空"
            return
        try:
            resp = requests.get(self._proxy_url, timeout=15, verify=False, impersonate="chrome")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            proxies = parse_proxy_lines(resp.text)
            if not proxies:
                raise RuntimeError("代理列表为空")
            self._proxies = proxies
            self._last_error = ""
        except Exception as exc:
            self._last_error = f"拉取代理列表失败: {exc}"

    def _refresh_proxy_checker_dir_locked(self) -> None:
        self._last_fetch = time.time()
        base = Path(self._proxy_checker_dir)
        if not base.exists() or not base.is_dir():
            self._proxies = []
            self._selected_file = ""
            self._last_error = f"Proxy Checker 目录不存在: {base}"
            return
        pattern = self._proxy_checker_pattern or DEFAULT_PROXY_CHECKER_PATTERN
        try:
            raw_candidates = [Path(path) for path in glob.glob(str(base / pattern))]
        except Exception as exc:
            self._last_error = f"读取 Proxy Checker 目录失败: {exc}"
            return

        candidates: list[tuple[float, Path]] = []
        last_error = ""
        for path in raw_candidates:
            try:
                stat = path.stat()
            except OSError as exc:
                last_error = f"{path.name}: {exc}"
                continue
            if not path.is_file() or stat.st_size <= 0:
                continue
            candidates.append((stat.st_mtime, path))
        if not candidates:
            if self._proxies:
                self._last_error = f"Proxy Checker 目录暂无可读匹配文件，继续使用上一轮代理: {last_error or pattern}"
                return
            self._proxies = []
            self._selected_file = ""
            self._last_error = f"Proxy Checker 目录没有匹配文件: {last_error or pattern}"
            return

        read_errors: list[str] = []
        selected: Path | None = None
        proxies: list[str] = []
        for _mtime, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                read_errors.append(f"{path.name}: {exc}")
                continue
            current_proxies = parse_proxy_lines(text)
            if current_proxies:
                selected = path
                proxies = current_proxies
                break
            read_errors.append(f"{path.name}: 文件没有可用代理")

        if not proxies:
            if self._proxies:
                self._last_error = "Proxy Checker 文件暂时不可用，继续使用上一轮代理: " + "; ".join(read_errors[:3])
                return
            self._proxies = []
            self._selected_file = str(selected) if selected is not None else ""
            self._last_error = "Proxy Checker 文件没有可用代理: " + ("; ".join(read_errors[:3]) or pattern)
            return
        self._proxies = proxies
        self._selected_file = str(selected)
        self._last_error = ""

    def _next_available_proxy_locked(self) -> tuple[str, int]:
        if not self._proxies:
            return "", -1
        now = time.time()
        count = len(self._proxies)
        leased = 0
        blacklisted = 0
        for offset in range(count):
            index = (self._proxy_index + offset) % count
            proxy = self._proxies[index]
            item = self._proxy_state.get(proxy) or {}
            if float(item.get("lease_until") or 0) > now:
                leased += 1
                continue
            item_blacklist_until, item_changed = self._effective_blacklist_until_locked(item, now)
            if item_changed:
                self._save_state_locked()
            if item_blacklist_until > now:
                blacklisted += 1
                continue
            self._proxy_index = (index + 1) % count
            return proxy, index
        if leased and not blacklisted:
            self._last_error = "all register proxies are leased"
        elif leased:
            self._last_error = "all register proxies are leased or blacklisted"
        else:
            self._last_error = "all register proxies are blacklisted"
        return "", -1

    def _has_active_retry_window_locked(self) -> bool:
        now = time.time()
        changed = False
        for proxy in self._proxies:
            item = self._proxy_state.get(proxy) or {}
            if float(item.get("lease_until") or 0) > now:
                return True
            blacklist_until, item_changed = self._effective_blacklist_until_locked(item, now)
            changed = changed or item_changed
            if blacklist_until > now:
                if changed:
                    self._save_state_locked()
                return True
        if changed:
            self._save_state_locked()
        return False

    def _source_label(self) -> str:
        return {
            "single": "单代理",
            "url": "代理列表 URL",
            "text": "手动代理列表",
            "proxy_checker_dir": "Proxy Checker 目录",
        }.get(self._mode, self._mode)

    def _should_bind_account(self) -> bool:
        if self._mode == "url":
            return self._bind_url
        if self._mode == "text":
            return self._bind_text
        if self._mode == "proxy_checker_dir":
            return self._bind_proxy_checker
        return False

    @staticmethod
    def _effective_blacklist_until_locked(item: dict[str, Any], now: float) -> tuple[float, bool]:
        blacklist_until = float(item.get("blacklist_until") or 0)
        if blacklist_until > now:
            return blacklist_until, False
        changed = False
        if blacklist_until:
            item.pop("blacklist_until", None)
            item.pop("failure_count", None)
            item.pop("last_error", None)
            changed = True
        if bool(item.get("blacklisted")):
            item.pop("blacklisted", None)
            item.pop("failure_count", None)
            item.pop("last_error", None)
            changed = True
        return 0.0, changed

    @staticmethod
    def _probe_proxy_connectivity(proxy: str) -> dict[str, Any]:
        result = test_proxy(proxy, timeout=REGISTER_PROXY_CONNECT_TIMEOUT_SECONDS)
        latency_ms = int(result.get("latency_ms") or 0)
        result = dict(result)
        try:
            status = int(result.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        connected = status != 407 and (bool(result.get("ok")) or (status > 0 and status < 600))
        ok = connected and latency_ms <= REGISTER_PROXY_MAX_LATENCY_MS
        if connected and not ok:
            result["error"] = f"proxy latency {latency_ms}ms exceeds {REGISTER_PROXY_MAX_LATENCY_MS}ms"
        elif status == 407:
            result["error"] = result.get("error") or "HTTP 407"
        result["ok"] = ok
        return result

    def _state_for(self, proxy: str) -> dict[str, Any]:
        item = self._proxy_state.get(proxy)
        if not isinstance(item, dict):
            item = {}
            self._proxy_state[proxy] = item
        return item

    @staticmethod
    def _release_selection_locked(item: dict[str, Any], selection: RegisterProxySelection) -> bool:
        current_lease_id = str(item.get("lease_id") or "")
        expected_lease_id = str(selection.lease_id or "")
        if expected_lease_id:
            if current_lease_id != expected_lease_id:
                return False
        elif current_lease_id:
            return False
        had_lease = "lease_until" in item or "lease_id" in item
        item.pop("lease_until", None)
        item.pop("lease_id", None)
        return had_lease

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(key): value for key, value in data.items() if isinstance(value, dict)}
        except Exception:
            pass
        return {}

    def _save_state_locked(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._proxy_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _positive_int(value: object, default: int) -> int:
        try:
            return max(1, int(value))
        except Exception:
            return default

register_proxy_pool = RegisterProxyPool()


__all__ = [
    "DEFAULT_PROXY_CHECKER_DIR",
    "DEFAULT_PROXY_CHECKER_PATTERN",
    "DEFAULT_PROXY_LEASE_SECONDS",
    "DEFAULT_PROXY_REFRESH_INTERVAL",
    "PROXY_INPUT_MODES",
    "RegisterProxySelection",
    "classify_register_failure",
    "normalize_proxy_input_mode",
    "parse_proxy_lines",
    "register_proxy_pool",
]
