from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.proxy_service import normalize_proxy_url


@dataclass
class RegisterProxySelection:
    proxy: str = ""
    source: str = "single"
    source_label: str = "单代理"
    count: int = 0
    proxy_index: int = -1
    bind_to_account: bool = False
    last_error: str = ""
    wait_retriable: bool = False


def parse_proxy_lines(text: str) -> list[str]:
    proxy = normalize_proxy_url(str(text or "").strip())
    return [proxy] if proxy else []


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
        self._single_proxy = ""

    def configure(self, cfg: dict[str, Any]) -> None:
        self._single_proxy = normalize_proxy_url(str(cfg.get("proxy") or ""))

    def prepare(self, force: bool = True) -> None:
        return None

    def next_proxy(self) -> RegisterProxySelection:
        if not self._single_proxy:
            return RegisterProxySelection(
                count=0,
                last_error="没有可用注册单代理",
                wait_retriable=True,
            )
        return RegisterProxySelection(
            proxy=self._single_proxy,
            count=1,
            proxy_index=0,
        )

    def report(self, selection: RegisterProxySelection | None, ok: bool, reason: str = "", error: object = "") -> None:
        return None

    def state(self) -> dict[str, Any]:
        count = 1 if self._single_proxy else 0
        return {
            "mode": "single",
            "source_label": "单代理",
            "count": count,
            "last_error": "" if count else "没有可用注册单代理",
            "last_fetch": 0,
            "status": "ready" if count else "waiting",
            "usage_label": "单代理" if count else "等待单代理配置",
            "using_cached": False,
            "wait_retriable": count <= 0,
            "selection_strategy": "single",
            "single_available": bool(count),
            "source_counts": {
                "single": count,
            },
        }


register_proxy_pool = RegisterProxyPool()


__all__ = [
    "RegisterProxySelection",
    "classify_register_failure",
    "parse_proxy_lines",
    "register_proxy_pool",
]
