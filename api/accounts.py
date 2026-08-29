from __future__ import annotations

import asyncio
import io
import json
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from services.auth_service import auth_service

from api.support import (
    require_admin,
    sanitize_cpa_pool,
    sanitize_cpa_pools,
    sanitize_sub2api_server,
    sanitize_sub2api_servers,
)
from services.account_service import account_service, is_account_response_hidden_field
from services.config import config
from services.cpa_service import cpa_config, cpa_import_service, list_remote_files
from services.oauth_login_service import OAuthLoginError, oauth_login_service
from services.sub2api_service import (
    list_remote_accounts as sub2api_list_remote_accounts,
    list_remote_groups as sub2api_list_remote_groups,
    sub2api_config,
    sub2api_import_service,
)
from utils.helper import anonymize_token
from utils.log import logger



class UserKeyCreateRequest(BaseModel):
    name: str = ""


class UserKeyUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    key: str | None = None


class AccountCreateRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    refresh: bool = True
    return_items: bool = True


class AccountDeleteRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)


class AccountImportCleanupRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)
    remove: bool = False


class AccountRefreshRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)


class AccountExportRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)
    format: Literal["json", "zip"] = "json"


class AccountUpdateRequest(BaseModel):
    access_token: str = ""
    type: str | None = None
    source_type: str | None = None
    status: Literal["正常", "限流", "异常", "禁用"] | None = None
    quota: int | None = None
    proxy: str | None = None
    group_id: str | None = None


class AccountBatchUpdateRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)
    status: Literal["正常", "限流", "异常", "禁用"] | None = None


class AccountGroupBindRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)
    group_id: str = ""


class AccountGroupRequest(BaseModel):
    id: str = ""
    name: str = ""
    proxy: str = ""
    proxy_group_id: str = ""
    enabled: bool = True
    notes: str = ""
    create_only: bool = False


class CPAPoolCreateRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    secret_key: str = ""


class CPAPoolUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    secret_key: str | None = None


class CPAImportRequest(BaseModel):
    names: list[str] = Field(default_factory=list)


class Sub2APIServerCreateRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    email: str = ""
    password: str = ""
    api_key: str = ""
    group_id: str = ""


class Sub2APIServerUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    email: str | None = None
    password: str | None = None
    api_key: str | None = None
    group_id: str | None = None


class Sub2APIImportGroupBinding(BaseModel):
    remote_group_id: str = ""
    name: str = ""
    account_ids: list[str] = Field(default_factory=list)


class Sub2APIImportRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list)
    group_bindings: list[Sub2APIImportGroupBinding] = Field(default_factory=list)
    create_account_groups: bool = True


class OAuthLoginStartRequest(BaseModel):
    """起始 OAuth 桥。email_hint 可选，仅用于让 OpenAI 登录页预填邮箱。"""
    email_hint: str = ""


class OAuthLoginFinishRequest(BaseModel):
    """提交 callback。callback 既可以是完整 URL 也可以只填 code。"""
    session_id: str = ""
    callback: str = ""


def _oauth_finish_log_summary(session_id: str, callback: str) -> str:
    session_text = str(session_id or "").strip()
    callback_text = str(callback or "").strip()
    parsed = urlparse(callback_text)
    query = parse_qs(parsed.query, keep_blank_values=True)
    callback_kind = "url" if parsed.scheme and parsed.netloc else ("raw_code" if callback_text else "empty")
    has_code = "code" in query or (callback_kind == "raw_code" and bool(callback_text))
    has_state = "state" in query
    return (
        f"session_id_present={bool(session_text)}, "
        f"session_id_len={len(session_text)}, "
        f"callback_present={bool(callback_text)}, "
        f"callback_len={len(callback_text)}, "
        f"callback_kind={callback_kind}, "
        f"callback_has_code={has_code}, "
        f"callback_has_state={has_state}"
    )


def _account_payload_token(item: dict[str, Any]) -> str:
    return str(item.get("access_token") or item.get("accessToken") or "").strip()


def _unique_tokens(tokens: list[str]) -> list[str]:
    return list(dict.fromkeys(str(token or "").strip() for token in tokens if str(token or "").strip()))


def _download_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_export_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (clean or fallback)[:80]


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _coerce_bool(value: object, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = _clean_text(value).lower()
    if raw in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
        return False
    return default


def _slug_id(value: object) -> str:
    raw = _clean_text(value).lower()
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    return "".join(chars).strip("-_")


def _config_dict_list(key: str) -> list[dict[str, Any]]:
    raw = config.get().get(key)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _account_group_id(value: object) -> str:
    return _slug_id(value)


def _account_group_proxy_reference(proxy: object, proxy_group_id: object = "") -> str:
    raw = _clean_text(proxy)
    if raw.lower() == "global":
        return ""
    if raw:
        return raw
    legacy_group_id = _clean_text(proxy_group_id)
    return f"group:{legacy_group_id}" if legacy_group_id else ""


def _proxy_group_id_from_reference(proxy: object) -> str:
    raw = _clean_text(proxy)
    if raw.lower().startswith("group:"):
        return _clean_text(raw.split(":", 1)[1])
    return ""


def _account_group_payload(groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    accounts = account_service.list_accounts()
    counts: dict[str, int] = {}
    for account in accounts:
        group_id = _clean_text(account.get("group_id"))
        if group_id:
            counts[group_id] = counts.get(group_id, 0) + 1
    normalized_groups = []
    for group in groups if groups is not None else _config_dict_list("account_groups"):
        group_id = _account_group_id(group.get("id"))
        if not group_id:
            continue
        proxy = _account_group_proxy_reference(group.get("proxy"), group.get("proxy_group_id"))
        normalized_groups.append(
            {
                "id": group_id,
                "name": _clean_text(group.get("name")) or group_id,
                "proxy": proxy,
                "proxy_group_id": _proxy_group_id_from_reference(proxy),
                "enabled": _coerce_bool(group.get("enabled"), True),
                "notes": _clean_text(group.get("notes")),
                "account_count": counts.get(group_id, 0),
            }
        )
    return {
        "groups": normalized_groups,
        "proxy_groups": _config_dict_list("proxy_groups"),
    }


def _upsert_account_group(body: AccountGroupRequest) -> dict[str, Any]:
    group_id = _account_group_id(body.id or body.name)
    if not group_id:
        raise ValueError("account group id is required")
    groups = _config_dict_list("account_groups")
    exists = any(_account_group_id(group.get("id")) == group_id for group in groups)
    if body.create_only and exists:
        raise ValueError("account group already exists")
    proxy = _account_group_proxy_reference(body.proxy, body.proxy_group_id)
    item = {
        "id": group_id,
        "name": body.name.strip() or group_id,
        "proxy": proxy,
        "proxy_group_id": _proxy_group_id_from_reference(proxy),
        "enabled": body.enabled,
        "notes": body.notes.strip(),
    }
    next_groups = [group for group in groups if _account_group_id(group.get("id")) != group_id]
    next_groups.append(item)
    updated = config.update({"account_groups": next_groups})
    return {"group": item, **_account_group_payload(updated.get("account_groups", []))}


def _account_status_category(account: dict[str, Any]) -> Literal["normal", "limited", "abnormal", "disabled"]:
    status = _clean_text(account.get("status"))
    return {
        "正常": "normal",
        "限流": "limited",
        "异常": "abnormal",
        "禁用": "disabled",
    }.get(status, "normal")


def _account_status_label(
        category: Literal["normal", "limited", "abnormal", "disabled"],
) -> str:
    return {
        "normal": "正常",
        "limited": "限流",
        "abnormal": "异常",
        "disabled": "禁用",
    }[category]


def _account_status_payload(account: dict[str, Any]) -> dict[str, str]:
    category = _account_status_category(account)
    return {
        "status_category": category,
        "status_label": _account_status_label(category),
    }


def _account_public_id(account: dict[str, Any]) -> str:
    return _clean_text(account.get("queue_account_id")) or _clean_text(account.get("id"))


def _sanitize_account_item(account: dict[str, Any]) -> dict[str, Any]:
    item = {
        str(key): value
        for key, value in dict(account or {}).items()
        if not is_account_response_hidden_field(key)
    }
    account_id = _account_public_id(account)
    if account_id:
        item["id"] = account_id
        item["queue_account_id"] = account_id
    token = _clean_text(account.get("access_token") or account.get("accessToken"))
    if token:
        item["access_token_preview"] = anonymize_token(token)
    return {**item, **_account_status_payload(account)}


def _sanitize_account_items(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [_sanitize_account_item(item) for item in items or [] if isinstance(item, dict)]


def _resolve_account_access_token(value: object) -> str:
    candidate = _clean_text(value)
    if not candidate:
        return ""
    get_account = getattr(account_service, "get_account", None)
    if callable(get_account):
        account = get_account(candidate)
        if account:
            return _clean_text(account.get("access_token")) or candidate
    get_account_by_id = getattr(account_service, "get_account_by_id", None)
    if callable(get_account_by_id):
        account = get_account_by_id(candidate)
        if account:
            return _clean_text(account.get("access_token")) or candidate
    return candidate


def _resolve_account_access_tokens(values: list[str]) -> list[str]:
    return _unique_tokens(_resolve_account_access_token(value) for value in values)


def _account_for_api(account: dict[str, Any]) -> dict[str, Any]:
    return _sanitize_account_item(account)


def _status_matches_filter(account: dict[str, Any], status_filter: str) -> bool:
    status_filter = status_filter.strip().lower()
    if not status_filter or status_filter == "all":
        return True
    if status_filter in {"normal", "limited", "abnormal", "disabled"}:
        return _account_status_category(account) == status_filter
    status = _clean_text(account.get("status"))
    status_map = {
        "normal": "\u6b63\u5e38",
        "limited": "\u9650\u6d41",
        "abnormal": "\u5f02\u5e38",
        "disabled": "\u7981\u7528",
    }
    expected = status_map.get(status_filter)
    return status == expected if expected else status.lower() == status_filter


def _account_matches_keyword(account: dict[str, Any], keyword: str) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return True
    fields = (
        account.get("access_token"),
        account.get("email"),
        account.get("user_id"),
        account.get("type"),
        account.get("source_type"),
        account.get("status"),
        account.get("proxy"),
        account.get("group_id"),
    )
    return any(needle in _clean_text(value).lower() for value in fields)


def _account_matches_group(account: dict[str, Any], group_id: str) -> bool:
    group_id = group_id.strip()
    if not group_id or group_id == "all":
        return True
    current = _clean_text(account.get("group_id"))
    if group_id == "__ungrouped__":
        return not current
    return current == group_id


def _accounts_page(
        *,
        page: int,
        page_size: int,
        keyword: str,
        status: str,
        group_id: str,
) -> dict[str, Any]:
    items = account_service.list_accounts()
    filtered = [
        item for item in items
        if _account_matches_keyword(item, keyword)
        and _status_matches_filter(item, status)
        and _account_matches_group(item, group_id)
    ]
    safe_page = max(1, page)
    safe_page_size = max(1, min(page_size, 500))
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size
    return {
        "items": [_account_for_api(item) for item in filtered[start:end]],
        "total": len(filtered),
        "all_total": len(items),
        "page": safe_page,
        "page_size": safe_page_size,
    }


def _import_abnormal_tokens(access_tokens: list[str]) -> list[str]:
    tokens = _resolve_account_access_tokens(access_tokens)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        account = account_service.get_account(token)
        if not account or not _status_matches_filter(account, "abnormal"):
            continue
        current_token = _clean_text(account.get("access_token")) or token
        if current_token and current_token not in seen:
            seen.add(current_token)
            result.append(current_token)
    return result


def _sub2api_remote_error(operation: str, server_id: str, exc: Exception) -> HTTPException:
    logger.warning({
        "event": "sub2api_remote_request_failed",
        "operation": operation,
        "server_id": server_id,
        "error_type": type(exc).__name__,
    })
    return HTTPException(status_code=502, detail={"error": "sub2api request failed"})


def _account_zip_bytes(items: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(items, start=1):
            raw_name = item.get("email") or item.get("account_id") or f"account-{index:03d}"
            base_name = _safe_export_name(raw_name, f"account-{index:03d}")
            name = base_name
            suffix = 2
            while name in used_names:
                name = f"{base_name}-{suffix}"
                suffix += 1
            used_names.add(name)
            archive.writestr(
                f"{name}.json",
                json.dumps(item, ensure_ascii=False, indent=2) + "\n",
            )
    return buf.getvalue()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/auth/users")
    async def list_user_keys(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": await run_in_threadpool(auth_service.list_keys, role="user")}

    @router.post("/api/auth/users")
    async def create_user_key(body: UserKeyCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            def build_payload() -> dict:
                item, raw_key = auth_service.create_key(role="user", name=body.name)
                return {"item": item, "key": raw_key, "items": auth_service.list_keys(role="user")}

            return await run_in_threadpool(build_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/auth/users/{key_id}")
    async def update_user_key(
            key_id: str,
            body: UserKeyUpdateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        updates = {
            key: value
            for key, value in {
                "name": body.name,
                "enabled": body.enabled,
                "key": body.key,
            }.items()
            if value is not None
        }
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "还没有检测到改动，请修改后再保存"})
        try:
            def build_payload() -> dict:
                item = auth_service.update_key(key_id, updates, role="user")
                if item is None:
                    raise HTTPException(status_code=404, detail={"error": "这条用户密钥不存在，可能已经被删除"})
                return {"item": item, "items": auth_service.list_keys(role="user")}

            return await run_in_threadpool(build_payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.delete("/api/auth/users/{key_id}")
    async def delete_user_key(key_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            if not auth_service.delete_key(key_id, role="user"):
                raise HTTPException(status_code=404, detail={"error": "这条用户密钥不存在，可能已经被删除"})
            return {"items": auth_service.list_keys(role="user")}

        return await run_in_threadpool(build_payload)

    @router.get("/api/accounts")
    async def get_accounts(
            page: int = Query(default=1, ge=1),
            page_size: int = Query(default=500, ge=1, le=500),
            keyword: str = "",
            status: str = "all",
            group_id: str = "all",
            authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return await run_in_threadpool(
            _accounts_page,
            page=page,
            page_size=page_size,
            keyword=keyword,
            status=status,
            group_id=group_id,
        )

    @router.get("/api/account-groups")
    async def list_account_groups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return await run_in_threadpool(_account_group_payload)

    @router.post("/api/account-groups")
    async def save_account_group(body: AccountGroupRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(_upsert_account_group, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.delete("/api/account-groups/{group_id}")
    async def delete_account_group(group_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            normalized = _account_group_id(group_id)
            groups = _config_dict_list("account_groups")
            next_groups = [group for group in groups if _account_group_id(group.get("id")) != normalized]
            if len(next_groups) == len(groups):
                raise HTTPException(status_code=404, detail={"error": "account group not found"})
            updated = config.update({"account_groups": next_groups})
            for account in account_service.list_accounts():
                if _clean_text(account.get("group_id")) == normalized:
                    account_service.update_account(account.get("access_token", ""), {"group_id": ""}, quiet=True)
            return {
                "deleted": normalized,
                **_account_group_payload(updated.get("account_groups", [])),
                "items": _sanitize_account_items(account_service.list_accounts()),
            }

        return await run_in_threadpool(build_payload)

    @router.post("/api/accounts")
    async def create_accounts(body: AccountCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        account_payloads = [item for item in body.accounts if isinstance(item, dict)]
        payload_tokens = [_account_payload_token(item) for item in account_payloads]
        tokens = _unique_tokens([*body.tokens, *payload_tokens])
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        def build_payload() -> dict:
            if account_payloads:
                result = account_service.add_account_items(account_payloads, return_items=body.return_items)
                payload_token_set = set(_unique_tokens(payload_tokens))
                extra_tokens = [token for token in tokens if token not in payload_token_set]
                if extra_tokens:
                    extra_result = account_service.add_accounts(extra_tokens, return_items=body.return_items)
                    result["added"] = int(result.get("added") or 0) + int(extra_result.get("added") or 0)
                    result["skipped"] = int(result.get("skipped") or 0) + int(extra_result.get("skipped") or 0)
            else:
                result = account_service.add_accounts(tokens, return_items=body.return_items)
            if not body.refresh:
                return {
                    **result,
                    "refreshed": 0,
                    "errors": [],
                    "items": _sanitize_account_items(result.get("items", [])) if body.return_items else [],
                }
            refresh_result = account_service.refresh_accounts(tokens)
            return {
                **result,
                "refreshed": refresh_result.get("refreshed", 0),
                "errors": refresh_result.get("errors", []),
                "items": _sanitize_account_items(refresh_result.get("items", result.get("items", []))) if body.return_items else [],
            }

        return await run_in_threadpool(build_payload)

    @router.delete("/api/accounts")
    async def delete_accounts(body: AccountDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        tokens = _resolve_account_access_tokens([str(token or "").strip() for token in body.tokens if str(token or "").strip()])
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        return await run_in_threadpool(account_service.delete_accounts, tokens, return_items=False)

    @router.post("/api/accounts/import-cleanup")
    async def cleanup_imported_abnormal_accounts(
            body: AccountImportCleanupRequest,
            authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        tokens = _resolve_account_access_tokens(body.access_tokens)
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        def build_payload() -> dict:
            abnormal_tokens = _import_abnormal_tokens(tokens)
            removed = 0
            if body.remove and abnormal_tokens:
                removed = int(account_service.delete_accounts(abnormal_tokens, return_items=False).get("removed") or 0)
            return {
                "checked": len(tokens),
                "abnormal": len(abnormal_tokens),
                "removed": removed,
            }

        return await run_in_threadpool(build_payload)

    @router.post("/api/accounts/refresh")
    async def refresh_accounts(body: AccountRefreshRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_tokens = _resolve_account_access_tokens([str(token or "").strip() for token in body.access_tokens if str(token or "").strip()])
        if not access_tokens:
            access_tokens = await run_in_threadpool(account_service.list_tokens)
        if not access_tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})

        progress_id = str(uuid.uuid4())
        await run_in_threadpool(account_service.init_refresh_progress, progress_id, len(access_tokens))

        async def _do_refresh():
            try:
                await run_in_threadpool(account_service.refresh_accounts, access_tokens, progress_id)
            except Exception as e:
                await run_in_threadpool(account_service.finish_refresh_progress, progress_id, error=str(e))

        asyncio.create_task(_do_refresh())

        return {"progress_id": progress_id}

    @router.get("/api/accounts/refresh/progress/{progress_id}")
    async def get_refresh_progress(progress_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        progress = await run_in_threadpool(account_service.get_refresh_progress, progress_id)
        if progress is None:
            raise HTTPException(status_code=404, detail={"error": "progress not found"})
        return progress

    @router.post("/api/accounts/export")
    async def export_accounts(body: AccountExportRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_tokens = _resolve_account_access_tokens(body.access_tokens)
        items = await run_in_threadpool(account_service.build_export_items, access_tokens)
        if not items:
            raise HTTPException(
                status_code=400,
                detail={"error": "没有可导出的完整账号，需要同时有 access_token、refresh_token 和 id_token"},
            )

        timestamp = _download_timestamp()
        if body.format == "zip":
            content = await run_in_threadpool(_account_zip_bytes, items)
            return Response(
                content,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="codex-accounts-{timestamp}.zip"'},
            )

        payload: dict[str, Any] | list[dict[str, Any]] = items[0] if len(items) == 1 else items
        content = await run_in_threadpool(
            lambda: json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return Response(
            content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="codex-accounts-{timestamp}.json"'},
        )

    @router.post("/api/accounts/update")
    async def update_account(body: AccountUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_token = _resolve_account_access_token(body.access_token)
        if not access_token:
            raise HTTPException(status_code=400, detail={"error": "access_token is required"})
        updates = {
            key: value
            for key, value in {
                "type": body.type,
                "source_type": body.source_type,
                "status": body.status,
                "quota": body.quota,
                "proxy": body.proxy,
                "group_id": body.group_id,
            }.items()
            if value is not None
        }
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "还没有检测到改动，请修改后再保存"})
        def build_payload() -> dict:
            existed = account_service.get_account(access_token) is not None
            account = account_service.update_account(access_token, updates)
            if account is None:
                if existed and account_service.get_account(access_token) is None:
                    return {"item": None, "removed": 1, "items": _sanitize_account_items(account_service.list_accounts())}
                raise HTTPException(status_code=404, detail={"error": "account not found"})
            return {"item": _account_for_api(account), "items": _sanitize_account_items(account_service.list_accounts())}

        return await run_in_threadpool(build_payload)

    @router.post("/api/accounts/batch-update")
    async def batch_update_accounts(body: AccountBatchUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_tokens = _resolve_account_access_tokens(body.access_tokens)
        if not access_tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        updates = {key: value for key, value in {"status": body.status}.items() if value is not None}
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "no updates provided"})
        def build_payload() -> dict:
            updated = 0
            removed = 0
            errors: list[str] = []
            for token in access_tokens:
                existed = account_service.get_account(token) is not None
                account = account_service.update_account(token, updates, quiet=True)
                if account is None:
                    if existed and account_service.get_account(token) is None:
                        updated += 1
                        removed += 1
                    else:
                        errors.append(f"{anonymize_token(token)} not found")
                else:
                    updated += 1
            return {
                "updated": updated,
                "removed": removed,
                "errors": errors,
                "items": _sanitize_account_items(account_service.list_accounts()),
            }

        return await run_in_threadpool(build_payload)

    @router.post("/api/accounts/group")
    async def bind_accounts_group(body: AccountGroupBindRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_tokens = _resolve_account_access_tokens(body.access_tokens)
        if not access_tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        group_id = "" if body.group_id.strip() == "__ungrouped__" else _account_group_id(body.group_id)
        def build_payload() -> dict:
            if group_id and not any(group.get("id") == group_id for group in _account_group_payload()["groups"]):
                raise HTTPException(status_code=404, detail={"error": "account group not found"})
            updated = 0
            errors: list[str] = []
            for token in access_tokens:
                account = account_service.update_account(token, {"group_id": group_id}, quiet=True)
                if account is None:
                    errors.append(f"{anonymize_token(token)} not found")
                else:
                    updated += 1
            return {
                "updated": updated,
                "errors": errors,
                "group_id": group_id,
                **_account_group_payload(),
                "items": _sanitize_account_items(account_service.list_accounts()),
            }

        return await run_in_threadpool(build_payload)

    @router.post("/api/accounts/oauth/start")
    async def start_oauth_login(
            body: OAuthLoginStartRequest,
            authorization: str | None = Header(default=None),
    ):
        """登记一次 PKCE 会话，返回可让用户浏览器打开的 authorize URL。"""
        require_admin(authorization)
        try:
            return await run_in_threadpool(oauth_login_service.start, body.email_hint)
        except OAuthLoginError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/accounts/oauth/finish")
    async def finish_oauth_login(
            body: OAuthLoginFinishRequest,
            authorization: str | None = Header(default=None),
    ):
        """收用户从浏览器抓回的 callback URL / code，换出 token 三件套并落盘。"""
        require_admin(authorization)
        print(
            f"[oauth-login] finish called: {_oauth_finish_log_summary(body.session_id, body.callback)}",
            flush=True,
        )
        try:
            tokens = await run_in_threadpool(oauth_login_service.finish, body.session_id, body.callback)
        except OAuthLoginError as exc:
            print(f"[oauth-login] finish rejected: {exc}", flush=True)
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

        payload = {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "id_token": tokens["id_token"],
            "source_type": "web",
        }
        add_result = await run_in_threadpool(account_service.add_account_items, [payload])
        refresh_result = await run_in_threadpool(
            account_service.refresh_accounts, [tokens["access_token"]]
        )
        return {
            **add_result,
            "refreshed": refresh_result.get("refreshed", 0),
            "errors": refresh_result.get("errors", []),
            "items": _sanitize_account_items(refresh_result.get("items", add_result.get("items", []))),
        }

    @router.get("/api/cpa/pools")
    async def list_cpa_pools(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        pools = await run_in_threadpool(cpa_config.list_pools)
        return {"pools": sanitize_cpa_pools(pools)}

    @router.post("/api/cpa/pools")
    async def create_cpa_pool(body: CPAPoolCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not body.base_url.strip():
            raise HTTPException(status_code=400, detail={"error": "base_url is required"})
        if not body.secret_key.strip():
            raise HTTPException(status_code=400, detail={"error": "secret_key is required"})
        def build_payload() -> dict:
            pool = cpa_config.add_pool(name=body.name, base_url=body.base_url, secret_key=body.secret_key)
            return {"pool": sanitize_cpa_pool(pool), "pools": sanitize_cpa_pools(cpa_config.list_pools())}

        return await run_in_threadpool(build_payload)

    @router.post("/api/cpa/pools/{pool_id}")
    async def update_cpa_pool(pool_id: str, body: CPAPoolUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            pool = cpa_config.update_pool(pool_id, body.model_dump(exclude_none=True))
            if pool is None:
                raise HTTPException(status_code=404, detail={"error": "pool not found"})
            return {"pool": sanitize_cpa_pool(pool), "pools": sanitize_cpa_pools(cpa_config.list_pools())}

        return await run_in_threadpool(build_payload)

    @router.delete("/api/cpa/pools/{pool_id}")
    async def delete_cpa_pool(pool_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            if not cpa_config.delete_pool(pool_id):
                raise HTTPException(status_code=404, detail={"error": "pool not found"})
            return {"pools": sanitize_cpa_pools(cpa_config.list_pools())}

        return await run_in_threadpool(build_payload)

    @router.get("/api/cpa/pools/{pool_id}/files")
    async def cpa_pool_files(pool_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        pool = await run_in_threadpool(cpa_config.get_pool, pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        return {"pool_id": pool_id, "files": await run_in_threadpool(list_remote_files, pool)}

    @router.post("/api/cpa/pools/{pool_id}/import")
    async def cpa_pool_import(pool_id: str, body: CPAImportRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        pool = await run_in_threadpool(cpa_config.get_pool, pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        try:
            job = await run_in_threadpool(cpa_import_service.start_import, pool, body.names)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"import_job": job}

    @router.get("/api/cpa/pools/{pool_id}/import")
    async def cpa_pool_import_progress(pool_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        pool = await run_in_threadpool(cpa_config.get_pool, pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        return {"import_job": pool.get("import_job")}

    @router.get("/api/sub2api/servers")
    async def list_sub2api_servers(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        servers = await run_in_threadpool(sub2api_config.list_servers)
        return {"servers": sanitize_sub2api_servers(servers)}

    @router.post("/api/sub2api/servers")
    async def create_sub2api_server(body: Sub2APIServerCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not body.base_url.strip():
            raise HTTPException(status_code=400, detail={"error": "base_url is required"})
        has_login = body.email.strip() and body.password.strip()
        has_api_key = bool(body.api_key.strip())
        if not has_login and not has_api_key:
            raise HTTPException(status_code=400, detail={"error": "email+password or api_key is required"})
        def build_payload() -> dict:
            server = sub2api_config.add_server(
                name=body.name,
                base_url=body.base_url,
                email=body.email,
                password=body.password,
                api_key=body.api_key,
                group_id=body.group_id,
            )
            return {"server": sanitize_sub2api_server(server), "servers": sanitize_sub2api_servers(sub2api_config.list_servers())}

        return await run_in_threadpool(build_payload)

    @router.post("/api/sub2api/servers/{server_id}")
    async def update_sub2api_server(server_id: str, body: Sub2APIServerUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            server = sub2api_config.update_server(server_id, body.model_dump(exclude_none=True))
            if server is None:
                raise HTTPException(status_code=404, detail={"error": "server not found"})
            return {"server": sanitize_sub2api_server(server), "servers": sanitize_sub2api_servers(sub2api_config.list_servers())}

        return await run_in_threadpool(build_payload)

    @router.delete("/api/sub2api/servers/{server_id}")
    async def delete_sub2api_server(server_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        def build_payload() -> dict:
            if not sub2api_config.delete_server(server_id):
                raise HTTPException(status_code=404, detail={"error": "server not found"})
            return {"servers": sanitize_sub2api_servers(sub2api_config.list_servers())}

        return await run_in_threadpool(build_payload)

    @router.get("/api/sub2api/servers/{server_id}/groups")
    async def sub2api_server_groups(server_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        server = await run_in_threadpool(sub2api_config.get_server, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "server not found"})
        try:
            groups = await run_in_threadpool(sub2api_list_remote_groups, server)
        except Exception as exc:
            raise _sub2api_remote_error("groups", server_id, exc) from exc
        return {"server_id": server_id, "groups": groups}

    @router.get("/api/sub2api/servers/{server_id}/accounts")
    async def sub2api_server_accounts(
        server_id: str,
        group_id: str | None = Query(default=None),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        server = await run_in_threadpool(sub2api_config.get_server, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "server not found"})
        if group_id is not None:
            server = {**server, "group_id": group_id}
        try:
            accounts = await run_in_threadpool(sub2api_list_remote_accounts, server)
        except Exception as exc:
            raise _sub2api_remote_error("accounts", server_id, exc) from exc
        return {"server_id": server_id, "accounts": accounts}

    @router.post("/api/sub2api/servers/{server_id}/import")
    async def sub2api_server_import(server_id: str, body: Sub2APIImportRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        server = await run_in_threadpool(sub2api_config.get_server, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "server not found"})
        try:
            job = await run_in_threadpool(
                sub2api_import_service.start_import,
                server,
                body.account_ids,
                group_bindings=[binding.model_dump() for binding in body.group_bindings],
                create_account_groups=body.create_account_groups,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"import_job": job}

    @router.get("/api/sub2api/servers/{server_id}/import")
    async def sub2api_server_import_progress(server_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        server = await run_in_threadpool(sub2api_config.get_server, server_id)
        if server is None:
            raise HTTPException(status_code=404, detail={"error": "server not found"})
        return {"import_job": server.get("import_job")}

    return router
