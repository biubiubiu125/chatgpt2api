from __future__ import annotations

import glob
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.proxy_service import normalize_proxy_url


PROXY_INPUT_MODES = {"single", "proxy_checker_dir"}
DEFAULT_PROXY_CHECKER_DIR = "/opt/proxy-checker/repo_data"
DEFAULT_PROXY_CHECKER_PATTERN = "user_*.txt"
DEFAULT_PROXY_REFRESH_INTERVAL = 120


@dataclass
class RegisterProxySelection:
    proxy: str = ""
    source: str = "direct"
    source_label: str = "direct"
    count: int = 0
    proxy_index: int = -1
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
        self._state_file = state_file
        self._lock = threading.RLock()
        self._mode = "single"
        self._single_proxy = ""
        self._proxy_checker_dir = DEFAULT_PROXY_CHECKER_DIR
        self._proxy_checker_pattern = DEFAULT_PROXY_CHECKER_PATTERN
        self._refresh_interval = DEFAULT_PROXY_REFRESH_INTERVAL
        self._bind_proxy_checker = True
        self._proxies: list[str] = []
        self._proxy_index = 0
        self._last_fetch = 0.0
        self._last_error = ""
        self._selected_file = ""
        self._load_state()

    def configure(self, cfg: dict[str, Any]) -> None:
        with self._lock:
            next_mode = normalize_proxy_input_mode(cfg.get("proxy_input_mode"))
            next_single_proxy = normalize_proxy_url(str(cfg.get("proxy") or ""))
            next_proxy_checker_dir = str(cfg.get("proxy_checker_dir") or DEFAULT_PROXY_CHECKER_DIR).strip()
            next_proxy_checker_pattern = str(cfg.get("proxy_checker_pattern") or DEFAULT_PROXY_CHECKER_PATTERN).strip()
            current_source_key = self._source_key(self._mode, self._single_proxy, self._proxy_checker_dir, self._proxy_checker_pattern)
            next_source_key = self._source_key(next_mode, next_single_proxy, next_proxy_checker_dir, next_proxy_checker_pattern)
            source_changed = next_source_key != current_source_key
            self._mode = next_mode
            self._single_proxy = next_single_proxy
            self._proxy_checker_dir = next_proxy_checker_dir
            self._proxy_checker_pattern = next_proxy_checker_pattern
            self._refresh_interval = self._positive_int(cfg.get("proxy_refresh_interval"), DEFAULT_PROXY_REFRESH_INTERVAL)
            self._bind_proxy_checker = bool(cfg.get("proxy_bind_proxy_checker", True))
            if source_changed:
                self._proxies = []
                self._proxy_index = 0
                self._selected_file = ""
                self._save_state_locked()
            self._last_fetch = 0.0
            self._last_error = ""

    def prepare(self, force: bool = True) -> None:
        with self._lock:
            self._refresh_locked(force=force)

    def next_proxy(self) -> RegisterProxySelection:
        with self._lock:
            self._refresh_locked(force=self._mode == "proxy_checker_dir" and not self._proxies)
            if self._mode == "single":
                if not self._single_proxy:
                    return RegisterProxySelection(source="direct", source_label="直连", count=0)
                return RegisterProxySelection(
                    proxy=self._single_proxy,
                    source="single",
                    source_label=self._source_label(),
                    count=1,
                    proxy_index=0,
                    bind_to_account=False,
                    last_error=self._last_error,
                )

            if not self._proxies:
                return RegisterProxySelection(
                    source=self._mode,
                    source_label=self._source_label(),
                    count=0,
                    selected_file=self._selected_file,
                    last_error=self._last_error or "没有可用注册代理",
                    wait_retriable=self._mode == "proxy_checker_dir",
                )

            index = self._proxy_index % len(self._proxies)
            proxy = self._proxies[index]
            self._proxy_index = (index + 1) % len(self._proxies)
            return RegisterProxySelection(
                proxy=proxy,
                source=self._mode,
                source_label=self._source_label(),
                count=len(self._proxies),
                proxy_index=index,
                bind_to_account=self._should_bind_account(),
                selected_file=self._selected_file,
                last_error=self._last_error,
            )

    def report(self, selection: RegisterProxySelection | None, ok: bool, reason: str = "", error: object = "") -> None:
        return None

    def state(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._proxies) if self._mode != "single" else (1 if self._single_proxy else 0)
            using_cached = self._mode == "proxy_checker_dir" and count > 0 and bool(self._last_error)
            wait_retriable = self._mode == "proxy_checker_dir" and count == 0
            if wait_retriable:
                status = "waiting"
                usage_label = "等待代理"
            elif using_cached:
                status = "cached"
                usage_label = "使用上一轮代理"
            elif self._mode == "single":
                status = "ready" if count else "direct"
                usage_label = "单代理" if count else "直连"
            else:
                status = "ready"
                usage_label = "直接轮询"
            return {
                "mode": self._mode,
                "source_label": self._source_label(),
                "count": count,
                "last_error": self._last_error,
                "last_fetch": int(self._last_fetch) if self._last_fetch else 0,
                "selected_file": self._selected_file,
                "status": status,
                "usage_label": usage_label,
                "using_cached": using_cached,
                "wait_retriable": wait_retriable,
            }

    def _refresh_locked(self, force: bool) -> None:
        if self._mode == "single":
            self._proxies = [self._single_proxy] if self._single_proxy else []
            self._last_fetch = time.time()
            self._selected_file = ""
            self._last_error = ""
            return
        if not force and self._last_fetch and time.time() - self._last_fetch < self._refresh_interval:
            return
        if self._mode == "proxy_checker_dir":
            self._refresh_proxy_checker_dir_locked()

    def _refresh_proxy_checker_dir_locked(self) -> None:
        self._last_fetch = time.time()
        base = Path(self._proxy_checker_dir)
        if not base.exists() or not base.is_dir():
            if self._proxies:
                self._last_error = f"Proxy Checker 目录不存在，继续使用上一轮代理: {base}"
                return
            self._last_error = f"Proxy Checker 目录不存在: {base}"
            return
        pattern = self._proxy_checker_pattern or DEFAULT_PROXY_CHECKER_PATTERN
        try:
            raw_candidates = [Path(path) for path in glob.glob(str(base / pattern))]
        except Exception as exc:
            if self._proxies:
                self._last_error = f"读取 Proxy Checker 目录失败，继续使用上一轮代理: {exc}"
                return
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
        self._save_state_locked()

    def _load_state(self) -> None:
        if self._state_file is None:
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        proxies = parse_proxy_lines("\n".join(data.get("proxies") or []))
        if not proxies:
            return
        self._mode = normalize_proxy_input_mode(data.get("mode"))
        self._proxy_checker_dir = str(data.get("proxy_checker_dir") or DEFAULT_PROXY_CHECKER_DIR).strip()
        self._proxy_checker_pattern = str(data.get("proxy_checker_pattern") or DEFAULT_PROXY_CHECKER_PATTERN).strip()
        self._proxies = proxies
        self._selected_file = str(data.get("selected_file") or "")

    def _save_state_locked(self) -> None:
        if self._state_file is None:
            return
        if self._mode != "proxy_checker_dir" or not self._proxies:
            try:
                self._state_file.unlink(missing_ok=True)
            except Exception:
                pass
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(
                    {
                        "mode": self._mode,
                        "proxy_checker_dir": self._proxy_checker_dir,
                        "proxy_checker_pattern": self._proxy_checker_pattern,
                        "selected_file": self._selected_file,
                        "proxies": self._proxies,
                        "saved_at": int(time.time()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def _source_label(self) -> str:
        return {
            "single": "单代理",
            "proxy_checker_dir": "Proxy Checker 目录",
        }.get(self._mode, self._mode)

    def _should_bind_account(self) -> bool:
        return self._mode == "proxy_checker_dir" and self._bind_proxy_checker

    @staticmethod
    def _source_key(mode: str, single_proxy: str, proxy_checker_dir: str, proxy_checker_pattern: str) -> tuple[str, ...]:
        normalized_mode = normalize_proxy_input_mode(mode)
        if normalized_mode == "single":
            return (normalized_mode, single_proxy)
        return (normalized_mode, proxy_checker_dir, proxy_checker_pattern)

    @staticmethod
    def _positive_int(value: object, default: int) -> int:
        try:
            return max(1, int(value))
        except Exception:
            return default


register_proxy_pool = RegisterProxyPool(Path(__file__).resolve().parents[2] / "data" / "register_proxy_state.json")


__all__ = [
    "DEFAULT_PROXY_CHECKER_DIR",
    "DEFAULT_PROXY_CHECKER_PATTERN",
    "DEFAULT_PROXY_REFRESH_INTERVAL",
    "PROXY_INPUT_MODES",
    "RegisterProxySelection",
    "classify_register_failure",
    "normalize_proxy_input_mode",
    "parse_proxy_lines",
    "register_proxy_pool",
]
