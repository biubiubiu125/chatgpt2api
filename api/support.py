from __future__ import annotations

import time
from pathlib import Path
from threading import Event, Thread
from typing import Any

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import auth_service
from services.config import config
from services.log_service import LOG_TYPE_ACCOUNT, log_service

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "admin", "name": "管理员", "role": "admin"}
    return None


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    identity = _legacy_admin_identity(token) or auth_service.authenticate(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})
    return identity


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "需要管理员权限才能执行这个操作"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def _refresh_account_interval_minute() -> int:
    return max(1, int(config.refresh_account_interval_minute or 1))


def _refresh_account_interval_seconds() -> int:
    return _refresh_account_interval_minute() * 60


def _count_refresh_errors(result: dict[str, Any]) -> int:
    errors = result.get("errors")
    return len(errors) if isinstance(errors, list) else 0


def _status_counts(items: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(items, list):
        return counts
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "未知").strip() or "未知"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _add_account_log(summary: str, detail: dict[str, Any]) -> None:
    try:
        log_service.add(LOG_TYPE_ACCOUNT, summary, detail)
    except Exception as exc:
        print(f"[account-watcher] log fail {exc}")


def run_account_refresh_cycle() -> dict[str, Any]:
    limited_tokens = account_service.list_limited_tokens()
    normal_tokens = account_service.list_normal_tokens()
    expiring_tokens = account_service.list_expiring_access_tokens()
    tokens = list(dict.fromkeys([*limited_tokens, *normal_tokens, *expiring_tokens]))
    interval_minute = _refresh_account_interval_minute()
    start_detail = {
        "event_type": "account_auto_refresh_start",
        "total": len(tokens),
        "limited": len(limited_tokens),
        "normal": len(normal_tokens),
        "expiring_access_tokens": len(expiring_tokens),
        "interval_minute": interval_minute,
        "full_refresh": True,
        "defer_invalid_removal": True,
    }
    _add_account_log("自动刷新账号开始", start_detail)

    try:
        if tokens:
            print(
                "[account-watcher] checking "
                f"{len(limited_tokens)} limited accounts, "
                f"{len(normal_tokens)} normal accounts, "
                f"{len(expiring_tokens)} expiring access tokens"
            )
            refresh_result = account_service.refresh_accounts(tokens, defer_invalid_removal=True)
        else:
            print("[account-watcher] no accounts to refresh")
            refresh_result = {"refreshed": 0, "errors": [], "items": [], "relogined": 0}

        _add_account_log(
            "自动刷新账号完成",
            {
                **start_detail,
                "event_type": "account_auto_refresh_finished",
                "refreshed": int(refresh_result.get("refreshed") or 0),
                "error_count": _count_refresh_errors(refresh_result),
                "relogined": int(refresh_result.get("relogined") or 0),
                "status_counts": _status_counts(refresh_result.get("items")),
            },
        )
    except Exception as exc:
        _add_account_log(
            "自动刷新账号失败",
            {**start_detail, "event_type": "account_auto_refresh_failed", "error": str(exc)},
        )
        raise

    keepalive_tokens = account_service.list_refresh_token_keepalive_tokens()
    expiring_token_set = set(expiring_tokens)
    keepalive_tokens = [token for token in keepalive_tokens if token not in expiring_token_set]
    keepalive_result: dict[str, Any] = {"refreshed": 0, "errors": [], "items": [], "relogined": 0}
    if keepalive_tokens:
        print(f"[account-watcher] keepalive {len(keepalive_tokens)} refresh tokens")
        try:
            keepalive_result = account_service.keepalive_refresh_tokens(keepalive_tokens)
            _add_account_log(
                "refresh_token 保活完成",
                {
                    "event_type": "refresh_token_keepalive_finished",
                    "total": len(keepalive_tokens),
                    "refreshed": int(keepalive_result.get("refreshed") or 0),
                    "error_count": _count_refresh_errors(keepalive_result),
                },
            )
            if keepalive_result.get("errors"):
                print(f"[account-watcher] keepalive errors: {keepalive_result['errors']}")
        except Exception as exc:
            _add_account_log(
                "refresh_token 保活失败",
                {"event_type": "refresh_token_keepalive_failed", "total": len(keepalive_tokens), "error": str(exc)},
            )
            raise

    return {
        "refresh": refresh_result,
        "keepalive": keepalive_result,
    }


def _wait_for_next_account_refresh(stop_event: Event) -> None:
    started = time.monotonic()
    while not stop_event.is_set():
        interval_seconds = float(_refresh_account_interval_seconds())
        elapsed = time.monotonic() - started
        remaining = max(0.0, interval_seconds - elapsed)
        if remaining <= 0:
            return
        if stop_event.wait(min(30.0, remaining)):
            return


def start_account_refresh_watcher(stop_event: Event) -> Thread:

    def worker() -> None:
        while not stop_event.is_set():
            try:
                run_account_refresh_cycle()
            except Exception as exc:
                print(f"[account-watcher] fail {exc}")
            _wait_for_next_account_refresh(stop_event)

    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread


def start_limited_account_watcher(stop_event: Event) -> Thread:
    return start_account_refresh_watcher(stop_event)


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
