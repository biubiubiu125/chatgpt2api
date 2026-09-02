from __future__ import annotations

import hmac
from ipaddress import ip_address
from pathlib import Path
from threading import Event, Thread

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import auth_service
from services.config import config
from utils.log import logger


IMAGE_TRACE_HEADERS = (
    "x-request-id",
    "x-newapi-request-id",
    "x-oneapi-request-id",
    "x-channel-id",
    "x-channel-name",
)


def allowlisted_trace_headers(headers: object) -> dict[str, str]:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return {}
    result: dict[str, str] = {}
    for name in IMAGE_TRACE_HEADERS:
        value = str(getter(name) or "").strip()
        if value:
            result[name] = value[:160]
    return result


BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"
WEB_APP_ROUTES = {
    "",
    "login",
    "accounts",
    "settings",
    "proxy",
    "register",
    "logs",
    "monitor",
    "cluster",
    "docs",
    "gallery",
    "debug",
    "studio",
}


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    # compare_digest over bytes: a non-ASCII bearer token would make the str
    # form raise TypeError instead of simply failing to match.
    if auth_key and hmac.compare_digest(token.encode("utf-8"), auth_key.encode("utf-8")):
        return {"id": "admin", "name": "管理员", "role": "admin"}
    return None


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    legacy_identity = _legacy_admin_identity(token)
    if legacy_identity is not None:
        return legacy_identity
    try:
        identity = auth_service.authenticate(token)
    except Exception as exc:
        logger.error({
            "event": "auth_storage_unavailable",
            "error": str(exc),
        })
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth_storage_unavailable",
                "message": "authentication storage is temporarily unavailable",
            },
        ) from exc
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


def _is_local_request_host(hostname: str) -> bool:
    host = hostname.strip().lower()
    if not host:
        return False
    if host in {"localhost", "testserver"} or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_image_base_url(request: Request) -> str:
    image_base_url = getattr(config, "image_base_url", "")
    if image_base_url:
        return str(image_base_url).strip().rstrip("/")
    base_url = config.base_url
    if base_url:
        return base_url
    storage_settings_getter = getattr(config, "get_image_storage_settings", None)
    if callable(storage_settings_getter):
        public_base_url = str(storage_settings_getter().get("public_base_url") or "").strip().rstrip("/")
        if public_base_url:
            return public_base_url
    hostname = str(request.url.hostname or "").strip()
    if _is_local_request_host(hostname):
        return f"{request.url.scheme}://{request.url.netloc}"
    raise HTTPException(
        status_code=400,
        detail={
            "error": "base_url_required",
            "message": (
                "CHATGPT2API_BASE_URL or CHATGPT2API_IMAGE_BASE_URL is required "
                "for public image delivery"
            ),
        },
    )


def resolve_api_base_url(request: Request) -> str:
    base_url = config.base_url
    if base_url:
        return base_url
    hostname = str(request.url.hostname or "").strip()
    if _is_local_request_host(hostname):
        return f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    raise HTTPException(
        status_code=400,
        detail={
            "error": "base_url_required",
            "message": "CHATGPT2API_BASE_URL is required for non-public API hosts",
        },
    )


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


def _account_watcher_refresh_tokens(
    limited_tokens: list[str],
    expiring_tokens: list[str],
) -> list[str]:
    """Accounts that really need periodic upstream refresh.

    Normal healthy accounts are verified when they are selected for work. Polling
    every normal account on every watcher tick creates a large amount of
    upstream traffic under load, without improving image dispatch.
    """
    return list(dict.fromkeys([*limited_tokens, *expiring_tokens]))


def start_limited_account_watcher(stop_event: Event) -> Thread:
    interval_seconds = config.refresh_account_interval_minute * 60

    def worker() -> None:
        while not stop_event.is_set():
            try:
                pending_auth = account_service.list_pending_auth_verification_tokens()
                if pending_auth:
                    account_service.resume_pending_auth_verifications()
                limited_tokens = account_service.list_limited_tokens()
                normal_tokens = account_service.list_normal_tokens()
                expiring_tokens = account_service.list_expiring_access_tokens()
                keepalive_tokens = account_service.list_refresh_token_keepalive_tokens()
                tokens = _account_watcher_refresh_tokens(limited_tokens, expiring_tokens)
                expiring_token_set = set(expiring_tokens)
                keepalive_tokens = [token for token in keepalive_tokens if token not in expiring_token_set]
                if tokens:
                    print(
                        "[account-watcher] checking "
                        f"{len(limited_tokens)} limited accounts, "
                        f"{len(expiring_tokens)} expiring access tokens "
                        f"(skipping {len(normal_tokens)} normal accounts, "
                        f"recovering {len(pending_auth)} pending auth accounts)"
                    )
                    account_service.refresh_accounts(tokens)
                if keepalive_tokens:
                    print(f"[account-watcher] keepalive {len(keepalive_tokens)} refresh tokens")
                    result = account_service.keepalive_refresh_tokens(keepalive_tokens)
                    if result.get("errors"):
                        print(f"[account-watcher] keepalive errors: {result['errors']}")
            except Exception as exc:
                print(f"[account-watcher] fail {exc}")
            stop_event.wait(interval_seconds)

    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    base_dir = next(
        (
            candidate.resolve()
            for candidate in (WEB_DIST_DIR, BASE_DIR / "web-vue" / "dist")
            if (candidate / "index.html").is_file()
        ),
        None,
    )
    if base_dir is None:
        return None
    clean_path = requested_path.strip().replace("\\", "/").strip("/")
    if clean_path:
        parts = Path(clean_path).parts
        if any(part in {"", ".", ".."} for part in parts):
            return None
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
    if clean_path in WEB_APP_ROUTES:
        index = base_dir / "index.html"
        if index.is_file():
            return index
    return None
