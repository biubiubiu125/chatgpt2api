from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config import DATA_DIR, config
from services.log_service import LOG_TYPE_CALL, log_service
from services.account_service import account_service
from utils.helper import is_codex_image_model, parse_image_size
from utils.request_summary import request_text

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
MAX_TASK_GROUP_WORKERS = 20


def _default_generation_handler(payload: dict[str, Any]) -> dict[str, Any]:
    from services.protocol import openai_v1_image_generations

    return openai_v1_image_generations.handle(payload)


def _default_edit_handler(payload: dict[str, Any]) -> dict[str, Any]:
    from services.protocol import openai_v1_image_edit

    return openai_v1_image_edit.handle(payload)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _task_group_key(owner_id: str, group_id: str, task_id: str) -> str:
    group = _clean(group_id) or _clean(task_id)
    return f"{owner_id}:{group}"


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _collect_account_emails(value: object) -> list[str]:
    emails: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"_account_email", "account_email"} and isinstance(item, str) and item.strip():
                emails.append(item.strip())
            elif key in {"_account_emails", "account_emails"} and isinstance(item, list):
                emails.extend(str(email).strip() for email in item if str(email or "").strip())
            else:
                emails.extend(_collect_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(_collect_account_emails(item))
    return list(dict.fromkeys(emails))


def _reference_image_count(payload: dict[str, Any], result: object = None) -> int:
    if isinstance(result, dict):
        raw = result.get("_reference_image_count")
        try:
            count = int(raw)
            if count > 0:
                return count
        except (TypeError, ValueError):
            pass
    images = payload.get("images")
    return len(images) if isinstance(images, list) else 0


def _account_ref_from_token(access_token: str) -> dict[str, str]:
    access_token = _clean(access_token)
    if not access_token:
        return {}
    account = account_service.get_account(access_token) or {}
    account_id = _clean(account.get("account_id") or account.get("user_id"))
    account_email = _clean(account.get("email"))
    ref: dict[str, str] = {}
    if account_id:
        ref["account_id"] = account_id
    if account_email:
        ref["account_email"] = account_email
    return ref


def _is_image_token_invalid_exception(exc: Exception) -> bool:
    try:
        from services.protocol.conversation import is_token_invalid_exception

        return is_token_invalid_exception(exc)
    except Exception:
        text = str(exc or "").lower()
        return (
            "token_revoked" in text
            or "token_invalidated" in text
            or "authentication token has been invalidated" in text
            or "invalidated oauth token" in text
        )


def _serialize_image_input(item: object) -> dict[str, str] | None:
    if not isinstance(item, tuple) or len(item) < 3:
        return None
    data, filename, mime_type = item[:3]
    if not isinstance(data, (bytes, bytearray)):
        return None
    return {
        "data_b64": base64.b64encode(bytes(data)).decode("ascii"),
        "filename": _clean(filename, "image.png"),
        "mime_type": _clean(mime_type, "image/png"),
    }


def _deserialize_image_input(item: object) -> tuple[bytes, str, str] | None:
    if not isinstance(item, dict):
        return None
    data_b64 = _clean(item.get("data_b64"))
    if not data_b64:
        return None
    try:
        data = base64.b64decode(data_b64)
    except Exception:
        return None
    return (
        data,
        _clean(item.get("filename"), "image.png"),
        _clean(item.get("mime_type"), "image/png"),
    )


def _serialize_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "progress_callback":
            continue
        if key in {"images", "mask"}:
            items = [
                encoded
                for raw in (value or [])
                if (encoded := _serialize_image_input(raw)) is not None
            ]
            snapshot[key] = items
            continue
        try:
            json.dumps(value)
            snapshot[key] = value
        except TypeError:
            snapshot[key] = str(value)
    return snapshot


def _deserialize_task_payload(snapshot: object) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    payload: dict[str, Any] = {}
    for key, value in snapshot.items():
        if key in {"images", "mask"}:
            payload[key] = [
                decoded
                for raw in (value if isinstance(value, list) else [])
                if (decoded := _deserialize_image_input(raw)) is not None
            ]
        else:
            payload[key] = value
    return payload


def _record_backend_proxy_result(backend: object, ok: bool) -> None:
    try:
        from services.proxy_service import record_backend_proxy_result

        record_backend_proxy_result(backend, ok)
    except Exception:
        return


def _is_proxy_transport_error(error_message: object) -> bool:
    try:
        from services.proxy_service import is_proxy_transport_error

        return is_proxy_transport_error(error_message)
    except Exception:
        text = str(error_message or "").lower()
        return any(
            marker in text
            for marker in (
                "proxy",
                "connection reset",
                "connection refused",
                "connection timed out",
                "operation timed out",
                "tls",
                "ssl",
                "curl",
            )
        )


def _openai_backend_api_class() -> type:
    from services.openai_backend_api import OpenAIBackendAPI

    return OpenAIBackendAPI


def _resolve_account_token(account_id: str = "", account_email: str = "") -> str:
    normalized_id = _clean(account_id)
    normalized_email = _clean(account_email).lower()
    if not normalized_id and not normalized_email:
        return ""
    for account in account_service.list_accounts():
        token = _clean(account.get("access_token"))
        if not token:
            continue
        candidate_id = _clean(account.get("account_id") or account.get("user_id"))
        if normalized_id and candidate_id == normalized_id:
            return account_service.refresh_access_token(token, event="image_resume_poll") or token
        candidate_email = _clean(account.get("email")).lower()
        if normalized_email and candidate_email == normalized_email:
            return account_service.refresh_access_token(token, event="image_resume_poll") or token
    return ""


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": task.get("id"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "group_id": task.get("group_id"),
        "model": task.get("model"),
        "size": task.get("size"),
        "quality": task.get("quality"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("conversation_id"):
        item["conversation_id"] = task.get("conversation_id")
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("usage") is not None:
        item["usage"] = task.get("usage")
    if task.get("error"):
        item["error"] = task.get("error")
    if task.get("progress"):
        item["progress"] = task.get("progress")
    if task.get("duration_ms") is not None:
        item["duration_ms"] = task.get("duration_ms")
    if task.get("reference_image_count") is not None:
        item["reference_image_count"] = task.get("reference_image_count")
    if task.get("status") in (TASK_STATUS_RUNNING, TASK_STATUS_QUEUED):
        if task.get("status") == TASK_STATUS_RUNNING:
            # RUNNING 状态仅在 started_ts 被设置后（image_stream_resolve_start）才计时
            base_ts = task.get("started_ts")
        else:
            # QUEUED 状态从 created_ts 开始计时（排队等待中）
            base_ts = task.get("created_ts") or task.get("updated_ts")
        if base_ts:
            item["elapsed_secs"] = round(time.time() - base_ts, 1)
    return item


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = _default_generation_handler,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = _default_edit_handler,
        retention_days_getter: Callable[[], int] | None = None,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runtime: dict[str, dict[str, Any]] = {}
        self._group_active: dict[str, int] = {}
        self._group_queues: dict[str, list[str]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._tasks = self._load_locked()
            changed = self._recover_unfinished_locked()
            changed = self._cleanup_locked() or changed
            if changed:
                self._save_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        group_id: str | None = None,
        prompt: str,
        model: str,
        size: str | None,
        aspect_ratio: str | None = None,
        quality: str = "auto",
        base_url: str = "",
    ) -> dict[str, Any]:
        normalized_size = parse_image_size(size, aspect_ratio)
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": normalized_size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, group_id=group_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        group_id: str | None = None,
        prompt: str,
        model: str,
        size: str | None,
        aspect_ratio: str | None = None,
        quality: str = "auto",
        base_url: str = "",
        images: list[tuple[bytes, str, str]] | None = None,
        masks: list[tuple[bytes, str, str]] | None = None,
    ) -> dict[str, Any]:
        normalized_size = parse_image_size(size, aspect_ratio)
        payload = {
            "prompt": prompt,
            "images": images or [],
            "mask": masks or [],
            "model": model,
            "n": 1,
            "size": normalized_size,
            "quality": quality,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, group_id=group_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            if self._cleanup_locked():
                self._save_locked()
            items = []
            missing_ids = []
            for task_id in requested_ids:
                task = self._tasks.get(_task_key(owner, task_id))
                if task is None:
                    missing_ids.append(task_id)
                else:
                    items.append(_public_task(task))
            if not requested_ids:
                items = [
                    _public_task(task)
                    for task in self._tasks.values()
                    if task.get("owner_id") == owner
                ]
                items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                missing_ids = []
            return {"items": items, "missing_ids": missing_ids}

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        group_id: str | None,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        group_key = _task_group_key(owner, group_id or task_id, task_id)
        normalized_group_id = _clean(group_id) or task_id
        now = _now_iso()
        with self._lock:
            cleaned = self._cleanup_locked()
            task = self._tasks.get(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "group_id": normalized_group_id,
                "group_key": group_key,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "quality": _clean(payload.get("quality"), "auto"),
                "payload": _serialize_task_payload(payload),
                "reference_image_count": _reference_image_count(payload),
                "created_at": now,
                "updated_at": now,
                "created_ts": time.time(),
            }
            self._tasks[key] = task
            self._runtime[key] = {"payload": payload, "identity": dict(identity)}
            self._save_locked()
            self._enqueue_task_locked(group_key, key)
            self._schedule_group_locked(group_key)
        return _public_task(task)

    def _enqueue_task_locked(self, group_key: str, key: str) -> None:
        queue = self._group_queues.setdefault(group_key, [])
        if key not in queue:
            queue.append(key)

    def _schedule_group_locked(self, group_key: str) -> None:
        queue = self._group_queues.setdefault(group_key, [])
        while queue and self._group_active.get(group_key, 0) < MAX_TASK_GROUP_WORKERS:
            key = queue.pop(0)
            task = self._tasks.get(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                continue
            runtime = self._runtime.get(key) or {}
            payload = dict(runtime.get("payload") or {})
            if not payload:
                payload = _deserialize_task_payload(task.get("payload"))
            identity = dict(runtime.get("identity") or {})
            if not payload:
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "task runtime payload is missing"
                self._save_locked()
                continue
            mode = _clean(task.get("mode"), "generate")
            model = _clean(task.get("model"), "gpt-image-2")
            self._group_active[group_key] = self._group_active.get(group_key, 0) + 1
            thread = threading.Thread(
                target=self._run_task,
                args=(key, mode, payload, identity, model, group_key),
                name=f"image-task-{_clean(task.get('id'))[:16]}",
                daemon=True,
            )
            thread.start()
        if not queue:
            self._group_queues.pop(group_key, None)

    def _finish_group_task(self, group_key: str) -> None:
        if not group_key:
            return
        with self._lock:
            active = max(0, int(self._group_active.get(group_key, 0)) - 1)
            if active:
                self._group_active[group_key] = active
            else:
                self._group_active.pop(group_key, None)
            self._schedule_group_locked(group_key)

    def _run_task(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
        group_key: str = "",
    ) -> None:
        started = time.time()
        self._update_task(key, status=TASK_STATUS_RUNNING, error="")
        # 创建进度回调，每个步骤完成后更新任务状态
        def progress_callback(step: str) -> None:
            if step == "image_stream_resolve_start":
                self._update_task(key, started_ts=time.time())
            self._update_task(key, progress=step)
        # 将进度回调添加到 payload 中（handler 会提取并传递给 ConversationRequest）
        payload_with_progress = {**payload, "progress_callback": progress_callback}
        try:
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload_with_progress)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            account_email = _clean(result.get("_account_email") or result.get("account_email"))
            account_emails = _collect_account_emails(result)
            access_token = _clean(result.get("_access_token"))
            account_ref = _account_ref_from_token(access_token)
            reference_image_count = _reference_image_count(payload, result)
            if not account_email and account_ref.get("account_email"):
                account_email = account_ref["account_email"]
            if account_email and account_email not in account_emails:
                account_emails.insert(0, account_email)
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                error = RuntimeError(message)
                if account_email:
                    setattr(error, "account_email", account_email)
                if account_emails:
                    setattr(error, "account_emails", account_emails)
                if account_ref.get("account_id"):
                    setattr(error, "account_id", account_ref["account_id"])
                raise error
            usage = result.get("usage")
            duration_ms = int((time.time() - started) * 1000)
            account_id = account_ref.get("account_id", "")
            self._update_task(
                key,
                status=TASK_STATUS_SUCCESS,
                data=data,
                usage=usage,
                error="",
                progress="",
                duration_ms=duration_ms,
                account_email=account_email,
                account_emails=account_emails,
                account_id=account_id,
                conversation_id="",
                reference_image_count=reference_image_count,
            )
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
                account_email=account_email,
                account_emails=account_emails,
                reference_image_count=reference_image_count,
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            account_email = _clean(getattr(exc, "account_email", ""))
            account_emails = _collect_account_emails({"account_emails": getattr(exc, "account_emails", []), "account_email": account_email})
            account_id = _clean(getattr(exc, "account_id", ""))
            conversation_id = _clean(getattr(exc, "conversation_id", ""))
            reference_image_count = _reference_image_count(payload)
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[],
                              duration_ms=duration_ms,
                              reference_image_count=reference_image_count,
                              **({"conversation_id": conversation_id} if conversation_id else {}),
                              **({"account_email": account_email} if account_email else {}),
                              **({"account_emails": account_emails} if account_emails else {}),
                              **({"account_id": account_id} if account_id else {}))
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
                account_email=account_email,
                account_emails=account_emails,
                reference_image_count=reference_image_count,
            )
        finally:
            with self._lock:
                self._runtime.pop(key, None)
            self._finish_group_task(group_key)

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
        account_email: str = "",
        account_emails: list[str] | None = None,
        reference_image_count: int = 0,
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if account_email:
            detail["account_email"] = account_email
        clean_emails = _collect_account_emails({"account_emails": account_emails or [], "account_email": account_email})
        if clean_emails:
            detail["account_emails"] = clean_emails
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        if reference_image_count:
            detail["reference_image_count"] = max(0, int(reference_image_count))
        try:
            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return
            task.update(updates)
            task["updated_at"] = _now_iso()
            task["updated_ts"] = time.time()
            self._save_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": status,
                "mode": "edit" if item.get("mode") == "edit" else "generate",
                "group_id": _clean(item.get("group_id")),
                "group_key": _clean(item.get("group_key")),
                "model": _clean(item.get("model"), "gpt-image-2"),
                "size": _clean(item.get("size")),
                "quality": _clean(item.get("quality"), "auto"),
                "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {},
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
                "created_ts": item.get("created_ts"),
                "updated_ts": item.get("updated_ts"),
                "started_ts": item.get("started_ts"),
                "duration_ms": item.get("duration_ms"),
            }
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            usage = item.get("usage")
            if isinstance(usage, dict):
                task["usage"] = usage
            try:
                reference_image_count = int(item.get("reference_image_count") or 0)
            except (TypeError, ValueError):
                reference_image_count = 0
            if reference_image_count:
                task["reference_image_count"] = reference_image_count
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            account_email = _clean(item.get("account_email"))
            if account_email:
                task["account_email"] = account_email
            account_emails = _collect_account_emails(item.get("account_emails"))
            if account_emails:
                task["account_emails"] = account_emails
            account_id = _clean(item.get("account_id"))
            if account_id:
                task["account_id"] = account_id
            conversation_id = _clean(item.get("conversation_id"))
            if conversation_id:
                task["conversation_id"] = conversation_id
            access_token = _clean(item.get("access_token"))
            if access_token:
                account_ref = _account_ref_from_token(access_token)
                if account_ref.get("account_id"):
                    task["account_id"] = account_ref["account_id"]
                if not task.get("account_email") and account_ref.get("account_email"):
                    task["account_email"] = account_ref["account_email"]
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _save_locked(self) -> None:
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"tasks": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        for task in self._tasks.values():
            if task.get("status") in UNFINISHED_STATUSES:
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "服务已重启，未完成的图片任务已中断"
                task["updated_at"] = _now_iso()
                changed = True
        return changed

    def _cleanup_locked(self) -> bool:
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        return bool(removed_keys)

    def resume_poll(
        self,
        identity: dict[str, object],
        task_id: str,
        extra_timeout_secs: float = 30.0,
    ) -> dict[str, Any]:
        """恢复对已超时任务的轮询，额外等待 extra_timeout_secs 秒。"""
        owner = _owner_id(identity)
        key = _task_key(owner, _clean(task_id))
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            model = task.get("model", "gpt-image-2")
            if is_codex_image_model(model):
                raise ValueError("codex image tasks do not support resume poll")
            error_msg = _clean(task.get("error"))
            error_text = error_msg.lower()
            if "超时" not in error_msg and "timeout" not in error_text and "timed out" not in error_text:
                raise ValueError("task error is not a timeout error")
            conversation_id = _clean(task.get("conversation_id"))
            if not conversation_id:
                raise ValueError("task has no conversation_id")
            account_id = _clean(task.get("account_id"))
            account_email = _clean(task.get("account_email"))
            mode = task.get("mode", "generate")
            requested_size = _clean(task.get("size"))
            payload = _deserialize_task_payload(task.get("payload"))
            response_format = _clean(payload.get("response_format"), "url")
            base_url = _clean(payload.get("base_url"))

        access_token = _resolve_account_token(account_id, account_email)
        if not access_token:
            raise ValueError("task account is not available for resume poll")

        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                raise ValueError("task not found")
            if task.get("status") != TASK_STATUS_ERROR:
                raise ValueError("task is not in error state")
            # 将任务状态重置为 running
            self._update_task(key, status=TASK_STATUS_RUNNING, error="")

        # 启动新线程继续轮询
        thread = threading.Thread(
            target=self._run_resume_poll,
            args=(key, conversation_id, extra_timeout_secs, dict(identity), mode, model, access_token, account_email, requested_size, payload, response_format, base_url),
            name=f"image-resume-{_clean(task_id)[:16]}",
            daemon=True,
        )
        thread.start()
        return _public_task(task)

    def _run_resume_poll(
        self,
        key: str,
        conversation_id: str,
        extra_timeout_secs: float,
        identity: dict[str, object],
        mode: str,
        model: str,
        access_token: str,
        account_email: str = "",
        requested_size: str = "",
        payload: dict[str, Any] | None = None,
        response_format: str = "url",
        base_url: str = "",
    ) -> None:
        """后台线程：继续轮询已有 conversation_id 的图片结果。"""
        started = time.time()
        backend = None
        try:
            from utils.image_result import format_image_result

            OpenAIBackendAPI = _openai_backend_api_class()
            backend = OpenAIBackendAPI(access_token=access_token)
            file_ids, sediment_ids = backend._poll_image_results(
                conversation_id,
                extra_timeout_secs,
            )
            if not file_ids and not sediment_ids:
                raise RuntimeError(
                    f"继续等待 {extra_timeout_secs} 秒后仍未找到图片结果。"
                )

            image_urls = backend.resolve_conversation_image_urls(
                conversation_id, file_ids, sediment_ids, poll=False,
            )
            if not image_urls:
                raise RuntimeError("图片 URL 解析失败")

            image_items = [
                {"b64_json": __import__("base64").b64encode(image_data).decode("ascii")}
                for image_data in backend.download_image_bytes(image_urls)
            ]
            # 获取 task 的原始 prompt（从 _public_task 的 mode 判断）
            data = format_image_result(
                image_items,
                "",  # prompt 已不重要，结果已经拿到了
                response_format,
                base_url,
                int(time.time()),
                requested_size=requested_size,
            )["data"]
            self._update_task(key, status=TASK_STATUS_SUCCESS, data=data, error="", duration_ms=int((time.time() - started) * 1000))
            _record_backend_proxy_result(backend, True)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成（续轮询）",
                status="success",
                urls=_collect_image_urls(data),
                account_email=account_email,
                account_emails=[account_email] if account_email else [],
            )
        except Exception as exc:
            error_message = str(exc) or "resume poll failed"
            if backend is not None:
                _record_backend_proxy_result(backend, not _is_proxy_transport_error(error_message))
            if _is_image_token_invalid_exception(exc):
                account_service.remove_invalid_token(access_token, "image_resume_poll")
                if payload:
                    self._update_task(
                        key,
                        status=TASK_STATUS_QUEUED,
                        error="",
                        data=[],
                        progress="retry_after_token_revoked",
                    )
                    with self._lock:
                        self._runtime[key] = {"payload": dict(payload), "identity": dict(identity)}
                    logger_payload = {
                        "event": "image_resume_poll_invalid_token_regenerate",
                        "account_email": account_email,
                        "conversation_id": conversation_id,
                        "task_key": key,
                    }
                    try:
                        from utils.log import logger

                        logger.warning(logger_payload)
                    except Exception:
                        pass
                    self._run_task(key, mode, dict(payload), dict(identity), model, "")
                    return
                error_message = f"{error_message}; original request payload missing, cannot regenerate"
            duration_ms = int((time.time() - started) * 1000)
            self._update_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[], duration_ms=duration_ms)
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败（续轮询）",
                status="failed",
                error=error_message,
                account_email=account_email,
                account_emails=[account_email] if account_email else [],
            )


image_task_service = ImageTaskService(DATA_DIR / "image_tasks.json")
