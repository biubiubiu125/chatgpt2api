from __future__ import annotations

import glob
import json
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.proxy_service import normalize_proxy_url


PROXY_INPUT_MODES = {"auto", "single", "proxy_checker_dir"}
PROXY_SELECTION_STRATEGIES = {"round_robin", "random"}
DEFAULT_PROXY_CHECKER_DIR = ""
DEFAULT_PROXY_CHECKER_PATTERN = "user_*.txt"
DEFAULT_PROXY_REFRESH_INTERVAL = 120
DEFAULT_PROXY_SELECTION_STRATEGY = "round_robin"


@dataclass
class RegisterProxySelection:
    proxy: str = ""
    source: str = "auto"
    source_label: str = "自动代理"
    count: int = 0
    proxy_index: int = -1
    bind_to_account: bool = False
    selected_file: str = ""
    last_error: str = ""
    wait_retriable: bool = False


@dataclass(frozen=True)
class _ProxyEntry:
    proxy: str
    source: str
    source_label: str
    selected_file: str = ""
    bind_to_account: bool = False


def normalize_proxy_input_mode(value: object) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in PROXY_INPUT_MODES else "auto"


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
        self._single_proxy = ""
        self._proxy_checker_dir = ""
        self._proxy_checker_pattern = DEFAULT_PROXY_CHECKER_PATTERN
        self._refresh_interval = DEFAULT_PROXY_REFRESH_INTERVAL
        self._bind_proxy_checker = True
        self._selection_strategy = DEFAULT_PROXY_SELECTION_STRATEGY
        self._proxies: list[str] = []
        self._proxy_index = 0
        self._last_fetch = 0.0
        self._last_error = ""
        self._selected_file = ""
        self._load_state()

    def configure(self, cfg: dict[str, Any]) -> None:
        with self._lock:
            next_single_proxy = normalize_proxy_url(str(cfg.get("proxy") or ""))
            next_proxy_checker_dir = str(cfg.get("proxy_checker_dir") or "").strip()
            next_proxy_checker_pattern = str(cfg.get("proxy_checker_pattern") or DEFAULT_PROXY_CHECKER_PATTERN).strip()
            next_selection_strategy = self._normalize_selection_strategy(cfg.get("proxy_selection_strategy"))
            current_source_key = self._source_key(self._single_proxy, self._proxy_checker_dir, self._proxy_checker_pattern)
            next_source_key = self._source_key(next_single_proxy, next_proxy_checker_dir, next_proxy_checker_pattern)
            source_changed = next_source_key != current_source_key
            strategy_changed = next_selection_strategy != self._selection_strategy
            checker_changed = (
                next_proxy_checker_dir,
                next_proxy_checker_pattern,
            ) != (
                self._proxy_checker_dir,
                self._proxy_checker_pattern,
            )
            self._single_proxy = next_single_proxy
            self._proxy_checker_dir = next_proxy_checker_dir
            self._proxy_checker_pattern = next_proxy_checker_pattern
            self._refresh_interval = self._positive_int(cfg.get("proxy_refresh_interval"), DEFAULT_PROXY_REFRESH_INTERVAL)
            self._bind_proxy_checker = bool(cfg.get("proxy_bind_proxy_checker", True))
            self._selection_strategy = next_selection_strategy
            if source_changed or strategy_changed:
                self._proxy_index = 0
            if checker_changed or (not next_proxy_checker_dir and self._proxies):
                self._proxies = []
                self._selected_file = ""
                self._save_state_locked()
            self._last_fetch = 0.0
            self._last_error = ""

    def prepare(self, force: bool = True) -> None:
        with self._lock:
            self._refresh_locked(force=force)

    def next_proxy(self) -> RegisterProxySelection:
        with self._lock:
            self._refresh_locked(force=False)
            entries = self._build_entries_locked()
            if not entries:
                self._refresh_locked(force=True)
                entries = self._build_entries_locked()
            if not entries:
                return RegisterProxySelection(
                    source=self._configured_source_locked(),
                    source_label=self._configured_source_label_locked(),
                    count=0,
                    selected_file=self._selected_file,
                    last_error=self._last_error or "没有可用注册代理",
                    wait_retriable=True,
                )

            if self._selection_strategy == "random":
                index = random.randrange(len(entries))
            else:
                index = self._proxy_index % len(entries)
                self._proxy_index = (index + 1) % len(entries)
            entry = entries[index]
            return RegisterProxySelection(
                proxy=entry.proxy,
                source=entry.source,
                source_label=entry.source_label,
                count=len(entries),
                proxy_index=index,
                bind_to_account=entry.bind_to_account,
                selected_file=entry.selected_file,
                last_error=self._last_error,
                wait_retriable=False,
            )

    def report(self, selection: RegisterProxySelection | None, ok: bool, reason: str = "", error: object = "") -> None:
        return None

    def state(self) -> dict[str, Any]:
        with self._lock:
            entries = self._build_entries_locked()
            count = len(entries)
            has_single = any(entry.source == "single" for entry in entries)
            has_checker = any(entry.source == "proxy_checker_dir" for entry in entries)
            single_available = has_single
            proxy_checker_cached = bool(self._proxies and self._last_error)
            using_cached = proxy_checker_cached
            strategy_label = "随机" if self._selection_strategy == "random" else "轮询"
            if count <= 0:
                status = "waiting"
                usage_label = "等待代理恢复"
            elif using_cached:
                status = "cached"
                usage_label = f"使用可用代理（{strategy_label}，含上轮 Proxy Checker 缓存）"
            elif has_single and has_checker:
                status = "ready"
                usage_label = f"单代理 + Proxy Checker {strategy_label}"
            elif has_single:
                status = "ready"
                usage_label = "单代理"
            else:
                status = "ready"
                usage_label = f"Proxy Checker {strategy_label}"
            return {
                "mode": "auto",
                "source_label": self._configured_source_label_locked(),
                "count": count,
                "last_error": self._last_error,
                "last_fetch": int(self._last_fetch) if self._last_fetch else 0,
                "selected_file": self._selected_file,
                "status": status,
                "usage_label": usage_label,
                "using_cached": using_cached,
                "wait_retriable": count <= 0,
                "selection_strategy": self._selection_strategy,
                "single_available": single_available,
                "proxy_checker_cached": proxy_checker_cached,
                "source_counts": {
                    "single": 1 if has_single else 0,
                    "proxy_checker_dir": sum(1 for entry in entries if entry.source == "proxy_checker_dir"),
                },
            }

    def _refresh_locked(self, force: bool) -> None:
        if not force and self._last_fetch and time.time() - self._last_fetch < self._refresh_interval:
            return
        self._refresh_proxy_checker_dir_locked()

    def _refresh_proxy_checker_dir_locked(self) -> None:
        self._last_fetch = time.time()
        if not self._proxy_checker_dir:
            if self._proxies:
                self._proxies = []
                self._save_state_locked()
            self._selected_file = ""
            self._last_error = ""
            return
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
        proxy_checker_dir = str(data.get("proxy_checker_dir") or "").strip()
        if not proxy_checker_dir:
            try:
                self._state_file.unlink(missing_ok=True)
            except Exception:
                pass
            return
        self._proxy_checker_dir = proxy_checker_dir
        self._proxy_checker_pattern = str(data.get("proxy_checker_pattern") or DEFAULT_PROXY_CHECKER_PATTERN).strip()
        self._proxies = proxies
        self._selected_file = str(data.get("selected_file") or "")

    def _save_state_locked(self) -> None:
        if self._state_file is None:
            return
        if not self._proxies:
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
                        "mode": "auto",
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

    def _build_entries_locked(self) -> list[_ProxyEntry]:
        entries: list[_ProxyEntry] = []
        seen: set[str] = set()
        if self._single_proxy:
            seen.add(self._single_proxy)
            entries.append(_ProxyEntry(proxy=self._single_proxy, source="single", source_label="单代理"))
        for proxy in self._proxies:
            if proxy in seen:
                continue
            seen.add(proxy)
            entries.append(
                _ProxyEntry(
                    proxy=proxy,
                    source="proxy_checker_dir",
                    source_label="Proxy Checker 目录",
                    selected_file=self._selected_file,
                    bind_to_account=self._bind_proxy_checker,
                )
            )
        return entries

    def _configured_source_label_locked(self) -> str:
        has_single = bool(self._single_proxy)
        has_checker = bool(self._proxy_checker_dir)
        if has_single and has_checker:
            return "单代理 + Proxy Checker 目录"
        if has_single:
            return "单代理"
        if has_checker:
            return "Proxy Checker 目录"
        return "自动代理"

    def _configured_source_locked(self) -> str:
        has_single = bool(self._single_proxy)
        has_checker = bool(self._proxy_checker_dir)
        if has_checker and not has_single:
            return "proxy_checker_dir"
        if has_single and not has_checker:
            return "single"
        return "auto"

    @staticmethod
    def _source_key(single_proxy: str, proxy_checker_dir: str, proxy_checker_pattern: str) -> tuple[str, str, str]:
        return (single_proxy, proxy_checker_dir, proxy_checker_pattern)

    @staticmethod
    def _positive_int(value: object, default: int) -> int:
        try:
            return max(1, int(value))
        except Exception:
            return default

    @staticmethod
    def _normalize_selection_strategy(value: object) -> str:
        strategy = str(value or DEFAULT_PROXY_SELECTION_STRATEGY).strip().lower()
        return strategy if strategy in PROXY_SELECTION_STRATEGIES else DEFAULT_PROXY_SELECTION_STRATEGY


register_proxy_pool = RegisterProxyPool(Path(__file__).resolve().parents[2] / "data" / "register_proxy_state.json")


__all__ = [
    "DEFAULT_PROXY_CHECKER_DIR",
    "DEFAULT_PROXY_CHECKER_PATTERN",
    "DEFAULT_PROXY_REFRESH_INTERVAL",
    "DEFAULT_PROXY_SELECTION_STRATEGY",
    "PROXY_INPUT_MODES",
    "PROXY_SELECTION_STRATEGIES",
    "RegisterProxySelection",
    "classify_register_failure",
    "normalize_proxy_input_mode",
    "parse_proxy_lines",
    "register_proxy_pool",
]
