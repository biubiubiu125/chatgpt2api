"""Sub2API integration for browsing and importing ChatGPT OAuth accounts from a sub2api admin."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from curl_cffi.requests import Session

from services.account_service import account_service
from services.account_import_errors import refresh_error_entries
from services.config import DATA_DIR, config
from services.json_file import read_json_file, write_json_file


SUB2API_CONFIG_FILE = DATA_DIR / "sub2api_config.json"

# Cached JWT per server to avoid re-login on every list/import call.
# Token lifetime on sub2api defaults to 24h; we refresh 5 min before expiry.
_TOKEN_REFRESH_SKEW = 5 * 60
_account_group_lock = Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _slug_id(value: object) -> str:
    raw = _clean(value).lower()
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
        elif char.isspace():
            chars.append("-")
    return "".join(chars).strip("-_")


def _config_dict_list(key: str) -> list[dict]:
    raw = config.get().get(key)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _ensure_local_account_group(name: str, remote_group_id: str = "") -> str:
    group_name = _clean(name) or _clean(remote_group_id)
    if not group_name:
        return ""

    normalized_name = group_name.casefold()
    with _account_group_lock:
        groups = _config_dict_list("account_groups")
        for group in groups:
            if _clean(group.get("name")).casefold() == normalized_name:
                return _slug_id(group.get("id")) or _slug_id(group_name)

        base_id = _slug_id(group_name)
        if not base_id:
            digest = hashlib.sha1(f"{group_name}:{remote_group_id}".encode("utf-8")).hexdigest()[:8]
            base_id = f"sub2api-{digest}"

        existing_ids = {_slug_id(group.get("id")) for group in groups}
        group_id = base_id
        if group_id in existing_ids:
            return group_id

        groups.append({
            "id": group_id,
            "name": group_name,
            "proxy": "",
            "proxy_group_id": "",
            "enabled": True,
            "notes": "",
        })
        config.update({"account_groups": groups})
        return group_id


def _build_local_group_bindings(group_bindings: list[dict], create_account_groups: bool) -> dict[str, str]:
    if not create_account_groups:
        return {}

    account_group_ids: dict[str, str] = {}
    for binding in group_bindings:
        if not isinstance(binding, dict):
            continue
        raw_account_ids = binding.get("account_ids")
        if not isinstance(raw_account_ids, list):
            continue
        account_ids = [
            _clean(item)
            for item in raw_account_ids
            if _clean(item)
        ]
        if not account_ids:
            continue
        remote_group_id = _clean(binding.get("remote_group_id"))
        name = _clean(binding.get("name")) or remote_group_id
        local_group_id = _ensure_local_account_group(name, remote_group_id)
        if not local_group_id:
            continue
        for account_id in account_ids:
            account_group_ids[account_id] = local_group_id
    return account_group_ids


def _normalize_import_job(raw: object, *, fail_unfinished: bool) -> dict | None:
    if not isinstance(raw, dict):
        return None
    status = _clean(raw.get("status")) or "failed"
    if fail_unfinished and status in {"pending", "running"}:
        status = "failed"
    return {
        "job_id": _clean(raw.get("job_id")) or uuid.uuid4().hex,
        "status": status,
        "created_at": _clean(raw.get("created_at")) or _now_iso(),
        "updated_at": _clean(raw.get("updated_at")) or _clean(raw.get("created_at")) or _now_iso(),
        "total": int(raw.get("total") or 0),
        "completed": int(raw.get("completed") or 0),
        "added": int(raw.get("added") or 0),
        "skipped": int(raw.get("skipped") or 0),
        "refreshed": int(raw.get("refreshed") or 0),
        "failed": int(raw.get("failed") or 0),
        "errors": raw.get("errors") if isinstance(raw.get("errors"), list) else [],
    }


def _normalize_server(raw: dict) -> dict:
    return {
        "id": _clean(raw.get("id")) or _new_id(),
        "name": _clean(raw.get("name")),
        "base_url": _clean(raw.get("base_url")),
        "email": _clean(raw.get("email")),
        "password": _clean(raw.get("password")),
        "api_key": _clean(raw.get("api_key")),
        "group_id": _clean(raw.get("group_id")),
        "import_job": _normalize_import_job(raw.get("import_job"), fail_unfinished=True),
    }


class Sub2APIConfig:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = Lock()
        self._servers: list[dict] = self._load()

    def _load(self) -> list[dict]:
        raw = read_json_file(
            self._store_file,
            name="sub2api_config.json",
            default_factory=list,
            expected_types=list,
        )
        if isinstance(raw, list):
            return [_normalize_server(item) for item in raw if isinstance(item, dict)]
        return []

    def _save(self) -> None:
        write_json_file(self._store_file, self._servers)

    def list_servers(self) -> list[dict]:
        with self._lock:
            return [dict(server) for server in self._servers]

    def get_server(self, server_id: str) -> dict | None:
        with self._lock:
            for server in self._servers:
                if server["id"] == server_id:
                    return dict(server)
        return None

    def add_server(
        self,
        *,
        name: str,
        base_url: str,
        email: str,
        password: str,
        api_key: str,
        group_id: str = "",
    ) -> dict:
        server = _normalize_server({
            "id": _new_id(),
            "name": name,
            "base_url": base_url,
            "email": email,
            "password": password,
            "api_key": api_key,
            "group_id": group_id,
        })
        with self._lock:
            self._servers.append(server)
            self._save()
        _token_cache.pop(server["id"], None)
        return dict(server)

    def update_server(self, server_id: str, updates: dict) -> dict | None:
        with self._lock:
            for index, server in enumerate(self._servers):
                if server["id"] != server_id:
                    continue
                merged = {**server, **{k: v for k, v in updates.items() if v is not None}, "id": server_id}
                self._servers[index] = _normalize_server(merged)
                self._save()
                result = dict(self._servers[index])
                break
            else:
                return None
        _token_cache.pop(server_id, None)
        return result

    def delete_server(self, server_id: str) -> bool:
        with self._lock:
            before = len(self._servers)
            self._servers = [server for server in self._servers if server["id"] != server_id]
            removed = len(self._servers) < before
            if removed:
                self._save()
        if removed:
            _token_cache.pop(server_id, None)
        return removed

    def set_import_job(self, server_id: str, import_job: dict | None) -> dict | None:
        with self._lock:
            for index, server in enumerate(self._servers):
                if server["id"] != server_id:
                    continue
                next_server = dict(server)
                next_server["import_job"] = _normalize_import_job(import_job, fail_unfinished=False)
                self._servers[index] = next_server
                self._save()
                return dict(next_server)
        return None

    def get_import_job(self, server_id: str) -> dict | None:
        with self._lock:
            for server in self._servers:
                if server["id"] == server_id:
                    job = server.get("import_job")
                    return dict(job) if isinstance(job, dict) else None
        return None


# Per-server cached access token: {server_id: (jwt, expires_at_epoch)}
_token_cache: dict[str, tuple[str, float]] = {}
_token_cache_lock = Lock()


def _login(base_url: str, email: str, password: str) -> tuple[str, float]:
    url = f"{base_url.rstrip('/')}/api/v1/auth/login"
    session = Session(verify=True)
    try:
        response = session.post(
            url,
            json={"email": email, "password": password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"sub2api login failed: HTTP {response.status_code} {response.text[:200]}")
        payload = response.json()
    finally:
        session.close()

    body = _unwrap_envelope(payload)
    if not isinstance(body, dict):
        raise RuntimeError("sub2api login payload is invalid")

    token = _clean(body.get("access_token"))
    if not token:
        raise RuntimeError("sub2api login did not return access_token")

    expires_in = int(body.get("expires_in") or 3600)
    expires_at = time.time() + max(60, expires_in) - _TOKEN_REFRESH_SKEW
    return token, expires_at


def _auth_headers(server: dict) -> dict[str, str]:
    api_key = _clean(server.get("api_key"))
    if api_key:
        return {"x-api-key": api_key, "Accept": "application/json"}

    email = _clean(server.get("email"))
    password = _clean(server.get("password"))
    if not email or not password:
        raise RuntimeError("sub2api server requires email+password or api_key")

    server_id = _clean(server.get("id"))
    base_url = _clean(server.get("base_url"))

    with _token_cache_lock:
        cached = _token_cache.get(server_id)
        if cached and cached[1] > time.time():
            return {"Authorization": f"Bearer {cached[0]}", "Accept": "application/json"}

    token, expires_at = _login(base_url, email, password)
    with _token_cache_lock:
        _token_cache[server_id] = (token, expires_at)
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _extract_access_token(credentials: object) -> str:
    if not isinstance(credentials, dict):
        return ""
    for key in ("access_token", "accessToken", "token"):
        value = _clean(credentials.get(key))
        if value:
            return value
    return ""


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except Exception:
                return {}
            if isinstance(parsed, dict):
                return parsed
    return {}


def _account_section(account: dict, *keys: str) -> dict:
    for key in keys:
        value = _as_dict(account.get(key))
        if value:
            return value
    return {}


def _unwrap_account(payload: object) -> dict:
    account = _unwrap_envelope(payload)
    if isinstance(account, dict):
        for _ in range(3):
            for key in ("account", "item", "record"):
                value = account.get(key)
                if isinstance(value, dict):
                    return value
            nested = account.get("data")
            if isinstance(nested, dict):
                account = nested
                continue
            break
        return account
    return {}


def _account_id(account: dict, credentials: dict) -> str:
    for source in (account, credentials):
        for key in ("id", "account_id", "accountId", "chatgpt_account_id", "chatgptAccountId"):
            value = _clean(source.get(key))
            if value:
                return value
    return ""


def _account_email(account: dict, credentials: dict, extra: dict) -> str:
    for source in (credentials, extra, account):
        for key in ("email", "email_address", "emailAddress", "account_email", "accountEmail"):
            value = _clean(source.get(key))
            if value:
                return value
    return _clean(account.get("name"))


def _account_plan_type(account: dict, credentials: dict, extra: dict) -> str:
    for source in (credentials, extra, account):
        for key in ("plan_type", "planType", "subscription_tier", "subscriptionTier"):
            value = _clean(source.get(key))
            if value:
                return value
    return ""


def _group_value_info(value: object) -> tuple[str, str]:
    if isinstance(value, dict):
        group_id = ""
        group_name = ""
        for key in ("id", "group_id", "groupId", "account_group_id", "accountGroupId"):
            group_id = _clean(value.get(key))
            if group_id:
                break
        for key in ("name", "group_name", "groupName", "title", "label"):
            group_name = _clean(value.get(key))
            if group_name:
                break
        return group_id, group_name
    if isinstance(value, (str, int)):
        return _clean(value), ""
    return "", ""


def _account_group_info(account: dict, extra: dict) -> tuple[str, str]:
    for source in (account, extra):
        for key in ("group", "group_info", "groupInfo", "account_group", "accountGroup"):
            group_id, group_name = _group_value_info(source.get(key))
            if group_id or group_name:
                return group_id, group_name

        groups = source.get("groups")
        if isinstance(groups, list):
            for item in groups:
                group_id, group_name = _group_value_info(item)
                if group_id or group_name:
                    return group_id, group_name

    group_id = ""
    group_name = ""
    for source in (account, extra):
        for key in ("group_id", "groupId", "account_group_id", "accountGroupId"):
            group_id = _clean(source.get(key))
            if group_id:
                break
        if group_id:
            break
    for source in (account, extra):
        for key in ("group_name", "groupName", "account_group_name", "accountGroupName"):
            group_name = _clean(source.get(key))
            if group_name:
                break
        if group_name:
            break
    return group_id, group_name


def _unwrap_envelope(payload: object) -> object:
    """Peel sub2api's `{code, message, data}` envelope, returning the inner `data` field
    when present. Also handles unwrapped responses from older/alt versions."""
    if isinstance(payload, dict) and "data" in payload:
        envelope_keys = {"code", "message", "msg", "data", "success", "status"}
        if "code" in payload or set(payload.keys()).issubset(envelope_keys):
            return payload.get("data")
    return payload


def _extract_paged_items(payload: object) -> tuple[list, int]:
    """Return (items, total) from a paginated sub2api response.

    Handles both the wrapped shape `{code,data:{items,total,...}}` and a few looser
    variants (`{data:[...]}`, `[...]`, `{items:[...],total:N}`)."""
    inner = _unwrap_envelope(payload)
    if isinstance(inner, list):
        return inner, len(inner)
    if isinstance(inner, dict):
        candidates = [inner]
        nested = inner.get("data")
        if isinstance(nested, dict):
            candidates.append(nested)
        elif isinstance(nested, list):
            return nested, int(inner.get("total") or len(nested))
        for candidate in candidates:
            for key in ("items", "list", "accounts", "records", "rows"):
                value = candidate.get(key)
                if isinstance(value, list):
                    return value, int(candidate.get("total") or inner.get("total") or len(value))
    return [], 0


def list_remote_accounts(server: dict) -> list[dict]:
    """Return a flat list of OpenAI OAuth accounts from a sub2api server."""
    base_url = _clean(server.get("base_url"))
    if not base_url:
        return []

    headers = _auth_headers(server)
    group_id = _clean(server.get("group_id"))
    group_name = _clean(server.get("group_name"))

    session = Session(verify=True)
    items: list[dict] = []
    seen_ids: set[str] = set()
    try:
        page = 1
        while True:
            params: dict[str, object] = {
                "platform": "openai",
                "type": "oauth",
                "account_type": "oauth",
                "page": page,
                "page_size": 200,
            }
            if group_id:
                params["group"] = group_id
                params["group_id"] = group_id
            response = session.get(
                f"{base_url.rstrip('/')}/api/v1/admin/accounts",
                headers=headers,
                params=params,
                timeout=30,
            )
            if not response.ok:
                raise RuntimeError(f"sub2api list failed: HTTP {response.status_code} {response.text[:200]}")
            payload = response.json()

            data, total = _extract_paged_items(payload)
            if not data:
                break

            for account in data:
                if not isinstance(account, dict):
                    continue
                credentials = _account_section(account, "credentials", "credential")
                extra = _account_section(account, "extra", "metadata", "meta")
                account_id = _account_id(account, credentials)
                if not account_id or account_id in seen_ids:
                    continue
                seen_ids.add(account_id)
                remote_group_id, remote_group_name = _account_group_info(account, extra)
                items.append({
                    "id": account_id,
                    "name": _clean(account.get("name")),
                    "email": _account_email(account, credentials, extra),
                    "plan_type": _account_plan_type(account, credentials, extra),
                    "status": _clean(account.get("status")),
                    "expires_at": _clean(credentials.get("expires_at")),
                    "has_access_token": bool(_extract_access_token(credentials) or _extract_access_token(account)),
                    "has_refresh_token": bool(_clean(credentials.get("refresh_token"))),
                    "remote_group_id": remote_group_id or group_id,
                    "remote_group_name": remote_group_name or group_name,
                })

            if page * 200 >= total or len(data) < 200:
                break
            page += 1
    finally:
        session.close()

    return items


def list_remote_groups(server: dict) -> list[dict]:
    """Return OpenAI account groups from a sub2api server."""
    base_url = _clean(server.get("base_url"))
    if not base_url:
        return []

    headers = _auth_headers(server)

    session = Session(verify=True)
    items: list[dict] = []
    try:
        page = 1
        while True:
            response = session.get(
                f"{base_url.rstrip('/')}/api/v1/admin/groups",
                headers=headers,
                params={
                    "page": page,
                    "page_size": 200,
                },
                timeout=30,
            )
            if not response.ok:
                raise RuntimeError(f"sub2api groups failed: HTTP {response.status_code} {response.text[:200]}")
            payload = response.json()

            data, total = _extract_paged_items(payload)
            if not data:
                break

            for group in data:
                if not isinstance(group, dict):
                    continue
                group_id = group.get("id")
                if group_id is None:
                    continue
                items.append({
                    "id": str(group_id),
                    "name": _clean(group.get("name")),
                    "description": _clean(group.get("description")),
                    "platform": _clean(group.get("platform")),
                    "status": _clean(group.get("status")),
                    "account_count": int(group.get("account_count") or 0),
                    "active_account_count": int(group.get("active_account_count") or 0),
                })

            if page * 200 >= total or len(data) < 200:
                break
            page += 1
    finally:
        session.close()

    return items


def _extract_export_accounts(payload: object) -> list[dict]:
    body = _unwrap_envelope(payload)
    candidates = []
    if isinstance(body, dict):
        candidates.append(body)
        nested = body.get("data")
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        accounts = candidate.get("accounts")
        if isinstance(accounts, list):
            return [item for item in accounts if isinstance(item, dict)]
    return []


def _account_access_token(account: dict) -> str:
    credentials = _account_section(account, "credentials", "credential")
    return _extract_access_token(credentials) or _extract_access_token(account)


def _fetch_access_token_from_export(server: dict, account_id: str) -> tuple[str, dict]:
    base_url = _clean(server.get("base_url"))
    headers = _auth_headers(server)

    session = Session(verify=True)
    try:
        response = session.get(
            f"{base_url.rstrip('/')}/api/v1/admin/accounts/data",
            headers=headers,
            params={"ids": account_id, "include_proxies": "false"},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"data export HTTP {response.status_code}")
        payload = response.json()
    finally:
        session.close()

    accounts = _extract_export_accounts(payload)
    if not accounts:
        raise RuntimeError("data export returned no accounts")
    account = accounts[0]
    credentials = _account_section(account, "credentials", "credential")
    extra = _account_section(account, "extra", "metadata", "meta")
    access_token = _account_access_token(account)
    if not access_token:
        raise RuntimeError("data export missing access_token")
    return access_token, {
        "email": _account_email(account, credentials, extra),
        "plan_type": _account_plan_type(account, credentials, extra),
    }


def _fetch_access_tokens_for_accounts(server: dict, account_ids: list[str]) -> tuple[dict[str, tuple[str, dict]], dict[str, str]]:
    """Return exported account data keyed by requested sub2api account id.

    Uses the batch data-export endpoint to avoid one HTTP request per account.  Some
    older sub2api deployments may omit account ids in the export payload, so the
    response order is used as a fallback mapping only when the exported id cannot
    be matched to the requested ids.
    """
    ids = list(dict.fromkeys(_clean(item) for item in account_ids if _clean(item)))
    if not ids:
        return {}, {}

    base_url = _clean(server.get("base_url"))
    headers = _auth_headers(server)

    session = Session(verify=True)
    try:
        response = session.get(
            f"{base_url.rstrip('/')}/api/v1/admin/accounts/data",
            headers=headers,
            params={
                "ids": ",".join(ids),
                "timezone": "Asia/Shanghai",
                "include_proxies": "false",
            },
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"data export HTTP {response.status_code}")
        payload = response.json()
    finally:
        session.close()

    accounts = _extract_export_accounts(payload)
    if not accounts:
        raise RuntimeError("data export returned no accounts")

    requested = set(ids)
    results: dict[str, tuple[str, dict]] = {}
    errors: dict[str, str] = {}
    for index, account in enumerate(accounts):
        credentials = _account_section(account, "credentials", "credential")
        extra = _account_section(account, "extra", "metadata", "meta")
        exported_id = _account_id(account, credentials)
        account_id = exported_id if exported_id in requested else (ids[index] if index < len(ids) else exported_id)
        account_name = account_id or _account_email(account, credentials, extra) or _clean(account.get("name")) or "unknown"
        access_token = _account_access_token(account)
        if not access_token:
            errors[account_name] = "data export missing access_token"
            continue
        if not account_id:
            errors[account_name] = "data export missing account id"
            continue
        results[account_id] = (
            access_token,
            {
                "email": _account_email(account, credentials, extra),
                "plan_type": _account_plan_type(account, credentials, extra),
            },
        )

    for account_id in ids:
        if account_id not in results and account_id not in errors:
            errors[account_id] = "data export did not return this account"

    return results, errors


def _fetch_access_token_for_account(server: dict, account_id: str) -> tuple[str, dict]:
    """Return (access_token, account_meta) for a single sub2api account id."""
    export_error = ""
    try:
        return _fetch_access_token_from_export(server, account_id)
    except Exception as exc:
        export_error = str(exc) or "data export failed"

    base_url = _clean(server.get("base_url"))
    headers = _auth_headers(server)

    session = Session(verify=True)
    try:
        response = session.get(
            f"{base_url.rstrip('/')}/api/v1/admin/accounts/{account_id}",
            headers=headers,
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
    finally:
        session.close()

    account = _unwrap_account(payload)
    credentials = _account_section(account, "credentials", "credential")
    extra = _account_section(account, "extra", "metadata", "meta")
    access_token = _account_access_token(account)
    if not access_token:
        detail = f"; export fallback: {export_error}" if export_error else ""
        raise RuntimeError(f"missing access_token{detail}")
    return access_token, {
        "email": _account_email(account, credentials, extra),
        "plan_type": _account_plan_type(account, credentials, extra),
    }


class Sub2APIImportService:
    def __init__(self, sub2api_config: Sub2APIConfig):
        self._config = sub2api_config

    def start_import(
        self,
        server: dict,
        account_ids: list[str],
        *,
        group_bindings: list[dict] | None = None,
        create_account_groups: bool = True,
    ) -> dict:
        ids = list(dict.fromkeys(_clean(item) for item in account_ids if _clean(item)))
        if not ids:
            raise ValueError("account ids is required")

        server_id = _clean(server.get("id"))
        account_group_ids = _build_local_group_bindings(group_bindings or [], create_account_groups)
        job = {
            "job_id": uuid.uuid4().hex,
            "status": "pending",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "total": len(ids),
            "completed": 0,
            "added": 0,
            "skipped": 0,
            "refreshed": 0,
            "failed": 0,
            "errors": [],
        }
        saved = self._config.set_import_job(server_id, job)
        if saved is None:
            raise ValueError("server not found")

        thread = threading.Thread(
            target=self._run_import,
            args=(server_id, server, ids, account_group_ids),
            name=f"sub2api-import-{server_id}",
            daemon=True,
        )
        thread.start()
        return dict(saved.get("import_job") or job)

    def _update_job(self, server_id: str, **updates) -> None:
        current = self._config.get_import_job(server_id)
        if current is None:
            return
        next_job = {**current, **updates, "updated_at": _now_iso()}
        self._config.set_import_job(server_id, next_job)

    def _append_error(self, server_id: str, account_id: str, message: str) -> None:
        current = self._config.get_import_job(server_id)
        if current is None:
            return
        errors = list(current.get("errors") or [])
        errors.append({"name": account_id, "error": message})
        self._update_job(server_id, errors=errors, failed=len(errors))

    def _run_import(
        self,
        server_id: str,
        server: dict,
        account_ids: list[str],
        account_group_ids: dict[str, str],
    ) -> None:
        self._update_job(server_id, status="running")

        payloads: list[dict] = []
        tokens: list[str] = []
        batch_results: dict[str, tuple[str, dict]] = {}
        batch_errors: dict[str, str] = {}
        batch_error = ""
        try:
            batch_results, batch_errors = _fetch_access_tokens_for_accounts(server, account_ids)
        except Exception as exc:
            batch_error = str(exc) or "data export failed"

        def append_payload(account_id: str, token: str, meta: dict) -> None:
            payload: dict[str, object] = {
                "access_token": token,
                "source_type": "codex",
            }
            email = _clean(meta.get("email")) if isinstance(meta, dict) else ""
            plan_type = _clean(meta.get("plan_type")) if isinstance(meta, dict) else ""
            if email:
                payload["email"] = email
            if plan_type:
                payload["type"] = plan_type
            local_group_id = account_group_ids.get(account_id)
            if local_group_id:
                payload["group_id"] = local_group_id
            payloads.append(payload)
            tokens.append(token)

        for account_id in account_ids:
            try:
                if account_id in batch_results:
                    token, meta = batch_results[account_id]
                else:
                    token, meta = _fetch_access_token_for_account(server, account_id)
                append_payload(account_id, token, meta)
            except Exception as exc:
                fallback_error = str(exc) or "unknown error"
                if batch_error:
                    message = f"batch export failed: {batch_error}; fallback failed: {fallback_error}"
                elif batch_errors.get(account_id):
                    message = f"batch export skipped: {batch_errors[account_id]}; fallback failed: {fallback_error}"
                else:
                    message = fallback_error
                self._append_error(server_id, account_id, message)

            current = self._config.get_import_job(server_id) or {}
            failed = len(current.get("errors") or [])
            self._update_job(
                server_id,
                completed=int(current.get("completed") or 0) + 1,
                failed=failed,
            )

        if not payloads:
            current = self._config.get_import_job(server_id) or {}
            self._update_job(
                server_id,
                status="failed",
                completed=int(current.get("total") or 0),
                failed=len(current.get("errors") or []),
            )
            return

        try:
            add_result = account_service.add_account_items(payloads, return_items=False)
        except Exception as exc:
            self._append_error(server_id, "local_account_save", str(exc) or "local account save failed")
            current = self._config.get_import_job(server_id) or {}
            self._update_job(
                server_id,
                status="failed",
                completed=int(current.get("total") or len(account_ids)),
                added=int((locals().get("add_result") or {}).get("added") or 0),
                skipped=int((locals().get("add_result") or {}).get("skipped") or 0),
                failed=len(current.get("errors") or []),
            )
            return
        try:
            refresh_result = account_service.refresh_accounts(tokens)
        except Exception as exc:
            self._append_error(server_id, "account_refresh", str(exc) or "account refresh failed")
            current = self._config.get_import_job(server_id) or {}
            self._update_job(
                server_id,
                status="failed",
                completed=int(current.get("total") or len(account_ids)),
                added=int(add_result.get("added") or 0),
                skipped=int(add_result.get("skipped") or 0),
                failed=len(current.get("errors") or []),
            )
            return
        current = self._config.get_import_job(server_id) or {}
        refresh_errors = refresh_error_entries(refresh_result)
        errors = [*list(current.get("errors") or []), *refresh_errors]
        failed = len(errors)
        self._update_job(
            server_id,
            status="failed" if refresh_errors else "completed",
            completed=len(account_ids),
            added=int(add_result.get("added") or 0),
            skipped=int(add_result.get("skipped") or 0),
            refreshed=int(refresh_result.get("refreshed") or 0),
            failed=failed,
            errors=errors,
        )


sub2api_config = Sub2APIConfig(SUB2API_CONFIG_FILE)
sub2api_import_service = Sub2APIImportService(sub2api_config)
