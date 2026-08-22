from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote

import psutil

from services.account_service import account_service
from services.config import DATA_DIR, config
from services.content_filter import request_text
from services.file_lock import file_lock, release_file_lock, try_acquire_file_lock
from services.image_queue.resource_controller import ImageQueueResourcePressureError
from services.json_file import read_json_file, write_json_file
from services.log_service import LOG_TYPE_CALL, log_service
from services.openai_backend_api import EDITABLE_FILE_MODEL, OpenAIBackendAPI
from utils.helper import new_uuid
from utils.timezone import beijing_from_timestamp, beijing_now_str

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}
EDITABLE_FILE_PLAN_TYPES = ("Plus", "Team", "Pro", "Enterprise")
EDITABLE_FILE_ROOT = DATA_DIR / "files"
EDITABLE_FILE_TASKS_PATH = DATA_DIR / "editable_file_tasks.json"
CLIENT_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUNNING_TASK_REMOTE_RECOVERY_SECONDS = 2 * 60 * 60
MAX_EDITABLE_IMAGE_COUNT = 16
MAX_EDITABLE_IMAGE_CHARS = 10_000_000
MAX_EDITABLE_TOTAL_IMAGE_CHARS = 30_000_000


class EditableFileTaskConflict(ValueError):
    pass


def _now_iso() -> str:
    return beijing_now_str()


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _owner_path_component(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _task_id(value: object) -> str:
    text = _clean(value)
    if not text:
        return new_uuid()
    if not CLIENT_TASK_ID_RE.fullmatch(text):
        raise ValueError("client_task_id may only contain letters, numbers, dot, underscore, and hyphen")
    return text


def _positive_int(value: object, default: int, *, maximum: int) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(1, min(maximum, normalized))


def _default_worker_count() -> int:
    raw = str(os.getenv("EDITABLE_FILE_WORKERS") or "").strip()
    if raw:
        return _positive_int(raw, 2, maximum=32)
    runtime = config.get_runtime_capacity_settings()
    return min(4, _positive_int(runtime.get("image_concurrency_limit"), 2, maximum=32))


def _default_backlog() -> int:
    raw = str(os.getenv("EDITABLE_FILE_MAX_BACKLOG") or "").strip()
    return _positive_int(raw, 50, maximum=100000)


def _request_hash(kind: str, prompt: str, base64_images: list[str]) -> str:
    image_hashes = [
        hashlib.sha256(str(item or "").strip().encode("utf-8")).hexdigest()
        for item in base64_images
    ]
    payload = {
        "kind": _clean(kind),
        "prompt": _clean(prompt),
        "base64_image_hashes": image_hashes,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_base64_images(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("base64_images must be a list")
    if len(value) > MAX_EDITABLE_IMAGE_COUNT:
        raise ValueError(f"base64_images supports up to {MAX_EDITABLE_IMAGE_COUNT} items")
    normalized: list[str] = []
    total_chars = 0
    for item in value:
        image = str(item or "")
        if len(image) > MAX_EDITABLE_IMAGE_CHARS:
            raise ValueError(
                f"base64_images items must be at most {MAX_EDITABLE_IMAGE_CHARS} characters"
            )
        total_chars += len(image)
        if total_chars > MAX_EDITABLE_TOTAL_IMAGE_CHARS:
            raise ValueError(
                f"base64_images total payload must be at most {MAX_EDITABLE_TOTAL_IMAGE_CHARS} characters"
            )
        normalized.append(image)
    return normalized


def _elapsed_seconds(task: dict[str, Any]) -> int:
    start = float(task.get("started_ts") or task.get("created_ts") or 0)
    end = float(task.get("ended_ts") or time.time())
    return max(0, int(end - start)) if start else 0


def _file_url(path: Path, base_url: str) -> str:
    rel = path.resolve().relative_to(EDITABLE_FILE_ROOT.resolve()).as_posix()
    prefix = str(base_url or "").strip().rstrip("/")
    return f"{prefix}/files/{quote(rel, safe='/')}" if prefix else f"/files/{quote(rel, safe='/')}"


def _editable_access_token() -> str:
    return account_service.get_available_access_token(plan_types=EDITABLE_FILE_PLAN_TYPES)


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    prompt_preview = _clean(task.get("prompt")).replace("\r", " ").replace("\n", " ")
    item = {
        "id": task.get("id"),
        "taskId": task.get("id"),
        "status": task.get("status"),
        "kind": task.get("kind"),
        "prompt_preview": prompt_preview[:200],
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "elapsed_seconds": _elapsed_seconds(task),
    }
    for key in ("result", "error"):
        if task.get(key):
            item[key] = task[key]
    return item


class EditableFileTaskService:
    def __init__(
        self,
        path: Path = EDITABLE_FILE_TASKS_PATH,
        *,
        auto_start: bool = True,
        max_backlog: int | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._queued_keys: deque[str] = deque()
        self._enqueued_keys: set[str] = set()
        self._active_keys: set[str] = set()
        self._workers: list[threading.Thread] = []
        self._worker_lock = None
        self._stopping = False
        self._start_requested = auto_start
        self._resource_controller: Any | None = None
        self._worker_host = socket.gethostname()
        self.max_backlog = max(1, int(max_backlog if max_backlog is not None else _default_backlog()))
        self.max_workers = max(1, int(max_workers if max_workers is not None else _default_worker_count()))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._condition:
            with file_lock(self._state_lock_path()):
                self._tasks = self._load_locked()
                if self._recover_unfinished_locked():
                    self._save_locked()
                self._enqueue_queued_locked()
        if auto_start:
            self.start()

    def set_resource_controller(self, resource_controller: object | None) -> None:
        self._resource_controller = resource_controller

    def _state_lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.lock")

    def start(self) -> None:
        with self._condition:
            self._start_requested = True
            if self._workers:
                return
            if self._worker_lock is None:
                self._worker_lock = try_acquire_file_lock(
                    self.path.with_name(f"{self.path.name}.worker.lock")
                )
            if self._worker_lock is None:
                return
            self._stopping = False
            for ordinal in range(self.max_workers):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"editable-file-worker-{ordinal + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def stop(self, timeout: float = 5.0) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout=max(0.0, timeout))
        alive_workers = [worker for worker in workers if worker.is_alive()]
        with self._condition:
            self._workers = alive_workers
        if alive_workers:
            return
        release_file_lock(self._worker_lock)
        self._worker_lock = None

    def run_next_for_test(self) -> bool:
        with self._condition:
            key = self._dequeue_locked()
        if not key:
            return False
        try:
            self._run_task(key)
        finally:
            with self._condition:
                self._active_keys.discard(key)
        return True

    def submit_ppt(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str = "",
        prompt: str = "",
        base64_images: list[str] | None = None,
        base_url: str = "",
    ) -> dict[str, Any]:
        return self._submit(
            identity,
            client_task_id=client_task_id,
            kind="ppt",
            prompt=prompt,
            base64_images=base64_images or [],
            base_url=base_url,
        )

    def submit_psd(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str = "",
        prompt: str = "",
        base64_images: list[str] | None = None,
        base_url: str = "",
    ) -> dict[str, Any]:
        return self._submit(
            identity,
            client_task_id=client_task_id,
            kind="psd",
            prompt=prompt,
            base64_images=base64_images or [],
            base_url=base_url,
        )

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        requested = [_clean(item) for item in task_ids if _clean(item)]
        with self._condition:
            with file_lock(self._state_lock_path()):
                self._tasks = self._load_locked()
            if requested:
                items = [
                    task
                    for task_id in requested
                    if (task := self._tasks.get(_task_key(owner, task_id)))
                ]
                return {
                    "items": [_public_task(item) for item in items],
                    "missing_ids": [
                        task_id
                        for task_id in requested
                        if _task_key(owner, task_id) not in self._tasks
                    ],
                }
            items = [task for task in self._tasks.values() if task.get("owner_id") == owner]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"items": [_public_task(item) for item in items], "missing_ids": []}

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        kind: str,
        prompt: str,
        base64_images: list[str],
        base_url: str,
    ) -> dict[str, Any]:
        if kind not in {"ppt", "psd"}:
            raise ValueError("kind must be ppt or psd")
        task_id = _task_id(client_task_id)
        prompt_text = _clean(prompt)
        if not prompt_text:
            raise ValueError("prompt is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        normalized_images = _normalize_base64_images(base64_images)
        request_hash = _request_hash(kind, prompt_text, normalized_images)
        now = _now_iso()
        with self._condition:
            with file_lock(self._state_lock_path()):
                self._tasks = self._load_locked()
                existing = self._tasks.get(key)
                if existing is not None:
                    existing_hash = _clean(existing.get("request_hash"))
                    if existing_hash and existing_hash != request_hash:
                        raise EditableFileTaskConflict("client_task_id was already used with a different request")
                    if not existing_hash and _clean(existing.get("kind")) != kind:
                        raise EditableFileTaskConflict("client_task_id was already used with a different request")
                    return _public_task(existing)
                self._ensure_submission_capacity_locked()
                ts = time.time()
                self._tasks[key] = {
                    "id": task_id,
                    "owner_id": owner,
                    "identity": _task_identity(identity),
                    "status": TASK_STATUS_QUEUED,
                    "kind": kind,
                    "model": EDITABLE_FILE_MODEL,
                    "request_hash": request_hash,
                    "prompt": prompt_text,
                    "base64_images": normalized_images,
                    "base_url": str(base_url or ""),
                    "created_at": now,
                    "updated_at": now,
                    "created_ts": ts,
                    "updated_ts": ts,
                }
                task = dict(self._tasks[key])
                self._save_locked()
            self._enqueue_locked(key)
        if self._start_requested:
            self.start()
        return _public_task(task)

    def _ensure_submission_capacity_locked(self) -> None:
        controller = self._resource_controller
        if controller is not None:
            decision = controller.allow_new_submission(controller.sample())
            if not decision.allowed:
                raise ImageQueueResourcePressureError(decision.reason or "resource_pressure")
        unfinished = sum(
            1
            for item in self._tasks.values()
            if item.get("status") in UNFINISHED_STATUSES
        )
        if unfinished >= self.max_backlog:
            raise ImageQueueResourcePressureError("resource_backlog")

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._stopping and not self._queued_keys:
                    self._condition.wait(timeout=1.0)
                    if not self._queued_keys and not self._stopping:
                        self._refresh_queued_locked()
                if self._stopping:
                    return
                key = self._dequeue_locked()
            if not key:
                continue
            try:
                self._run_task(key)
            finally:
                with self._condition:
                    self._active_keys.discard(key)

    def _enqueue_locked(self, key: str) -> None:
        if key in self._enqueued_keys or key in self._active_keys:
            return
        self._queued_keys.append(key)
        self._enqueued_keys.add(key)
        self._condition.notify()

    def _enqueue_queued_locked(self) -> None:
        for key, task in self._tasks.items():
            if task.get("status") == TASK_STATUS_QUEUED:
                self._enqueue_locked(key)

    def _refresh_queued_locked(self) -> None:
        with file_lock(self._state_lock_path()):
            self._tasks = self._load_locked()
        self._enqueue_queued_locked()

    def _dequeue_locked(self) -> str:
        while self._queued_keys:
            key = self._queued_keys.popleft()
            self._enqueued_keys.discard(key)
            task = self._tasks.get(key)
            if task is None or task.get("status") != TASK_STATUS_QUEUED:
                continue
            self._active_keys.add(key)
            return key
        return ""

    def _run_task(self, key: str) -> None:
        with self._lock:
            task = dict(self._tasks.get(key) or {})
        if not task or task.get("status") != TASK_STATUS_QUEUED:
            return

        kind = _clean(task.get("kind"), "ppt")
        prompt = str(task.get("prompt") or "")
        base64_images = [str(item or "") for item in task.get("base64_images") or []]
        identity = task.get("identity") if isinstance(task.get("identity"), dict) else {"id": task.get("owner_id")}
        base_url = str(task.get("base_url") or "")
        started = time.time()
        token = ""
        account_email = ""
        self._update_task(
            key,
            status=TASK_STATUS_RUNNING,
            error="",
            started_ts=started,
            worker_pid=os.getpid(),
            worker_host=self._worker_host,
        )
        try:
            if kind == "psd" and not base64_images:
                raise ValueError("base64_images is empty")
            token = _editable_access_token()
            account = account_service.get_account(token) or {}
            account_email = _clean(account.get("email"))
            output_root = (EDITABLE_FILE_ROOT / kind).resolve()
            output_dir = (
                output_root
                / _owner_path_component(_clean(task.get("owner_id")))
                / _clean(task.get("id"))
            ).resolve()
            output_dir.relative_to(output_root)
            with OpenAIBackendAPI(token) as backend:
                result = (
                    backend.export_psd_zip(base64_images, prompt, output_dir)
                    if kind == "psd"
                    else backend.export_ppt_zip(base64_images, prompt, output_dir)
                )
            account_service.mark_text_used(token)
            data = {
                "conversation_id": result.conversation_id,
                "primary_url": _file_url(result.primary_path, base_url),
                "zip_url": _file_url(result.zip_path, base_url),
            }
            self._update_task(
                key,
                status=TASK_STATUS_SUCCESS,
                result=data,
                account_email=account_email,
                error="",
                ended_ts=time.time(),
            )
            self._log_call(identity, kind, started, request_text(prompt), account_email=account_email, result=data)
        except Exception as exc:
            error = str(exc) or "editable file task failed"
            self._update_task(
                key,
                status=TASK_STATUS_ERROR,
                error=error,
                account_email=account_email,
                ended_ts=time.time(),
            )
            self._log_call(
                identity,
                kind,
                started,
                request_text(prompt),
                status="failed",
                error=error,
                account_email=account_email,
            )
        finally:
            if token:
                try:
                    account_service.release_image_slot(token)
                except Exception:
                    pass

    def public_file_path(self, relative_path: str) -> Path:
        raw = str(relative_path or "").replace("\\", "/").lstrip("/")
        path = (EDITABLE_FILE_ROOT / raw).resolve()
        path.relative_to(EDITABLE_FILE_ROOT.resolve())
        if not path.is_file():
            raise FileNotFoundError(raw)
        return path

    def file_path_for_identity(self, identity: dict[str, object], relative_path: str) -> Path:
        raw = str(relative_path or "").replace("\\", "/").lstrip("/")
        path = self.public_file_path(raw)
        parts = raw.split("/")
        if len(parts) < 3:
            raise FileNotFoundError(raw)
        kind = parts[0]
        owner = _owner_id(identity)
        if kind not in {"ppt", "psd"}:
            raise FileNotFoundError(raw)
        if len(parts) >= 4:
            owner_component, task_id = parts[1], parts[2]
            if owner_component != _owner_path_component(owner):
                raise FileNotFoundError(raw)
            expected_root = EDITABLE_FILE_ROOT / kind / owner_component / task_id
        else:
            task_id = parts[1]
            expected_root = EDITABLE_FILE_ROOT / kind / task_id
        if not task_id:
            raise FileNotFoundError(raw)
        path.relative_to(expected_root.resolve())
        with self._condition:
            with file_lock(self._state_lock_path()):
                self._tasks = self._load_locked()
            task = self._tasks.get(_task_key(owner, task_id))
            if (
                task is None
                or task.get("kind") != kind
                or task.get("status") != TASK_STATUS_SUCCESS
            ):
                raise FileNotFoundError(raw)
            if len(parts) == 3 and self._legacy_path_has_conflict_locked(owner, kind, task_id):
                raise FileNotFoundError(raw)
        return path

    def _legacy_path_has_conflict_locked(self, owner: str, kind: str, task_id: str) -> bool:
        return any(
            task.get("owner_id") != owner
            and task.get("id") == task_id
            and task.get("kind") == kind
            and task.get("status") == TASK_STATUS_SUCCESS
            for task in self._tasks.values()
        )

    def _update_task(self, key: str, **updates: Any) -> None:
        with self._condition:
            with file_lock(self._state_lock_path()):
                self._tasks = self._load_locked()
                task = self._tasks.get(key)
                if task is None:
                    return
                task.update(updates)
                task["updated_at"] = _now_iso()
                task["updated_ts"] = time.time()
                self._save_locked()

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        raw = read_json_file(
            self.path,
            name=self.path.name,
            default_factory=dict,
            expected_types=(dict, list),
        )
        tasks: dict[str, dict[str, Any]] = {}
        for item in (raw.get("tasks") if isinstance(raw, dict) else raw) or []:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"), TASK_STATUS_ERROR)
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR}:
                status = TASK_STATUS_ERROR
            task = {
                "id": task_id,
                "owner_id": owner,
                "status": status,
                "kind": "psd" if item.get("kind") == "psd" else "ppt",
                "model": _clean(item.get("model"), EDITABLE_FILE_MODEL),
                "request_hash": _clean(item.get("request_hash")),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
                "created_ts": _safe_float(item.get("created_ts")),
                "updated_ts": _safe_float(item.get("updated_ts")),
            }
            if isinstance(item.get("identity"), dict):
                task["identity"] = _task_identity(item.get("identity") or {})
            else:
                task["identity"] = {"id": owner}
            for field in ("prompt", "base_url"):
                if field in item:
                    task[field] = str(item.get(field) or "")
            if "base64_images" in item:
                images = item.get("base64_images")
                task["base64_images"] = [str(value or "") for value in images] if isinstance(images, list) else []
            for field in (
                "result",
                "error",
                "started_ts",
                "ended_ts",
                "account_email",
                "worker_pid",
                "worker_host",
            ):
                if item.get(field):
                    task[field] = item[field]
            tasks[_task_key(owner, task_id)] = task
        return tasks

    def _save_locked(self) -> None:
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        write_json_file(self.path, {"tasks": items})

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        for task in self._tasks.values():
            if task.get("status") not in UNFINISHED_STATUSES:
                continue
            if task.get("status") == TASK_STATUS_RUNNING and _task_owner_is_alive(task, self._worker_host):
                continue
            if not _has_recoverable_request(task):
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "task request is not recoverable after restart"
                task["ended_ts"] = time.time()
            else:
                task["status"] = TASK_STATUS_QUEUED
                task.pop("error", None)
                task.pop("ended_ts", None)
                task.pop("started_ts", None)
            task["updated_at"] = _now_iso()
            task["updated_ts"] = time.time()
            changed = True
        return changed

    def _log_call(
        self,
        identity: dict[str, object],
        kind: str,
        started: float,
        request_preview: str,
        *,
        status: str = "success",
        error: str = "",
        account_email: str = "",
        result: dict[str, str] | None = None,
    ) -> None:
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": f"/v1/{kind}/generations",
            "model": EDITABLE_FILE_MODEL,
            "started_at": beijing_from_timestamp(started),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if account_email:
            detail["account_email"] = account_email
        if error:
            detail["error"] = error
        if result:
            detail["result"] = result
        try:
            suffix = "failed" if status == "failed" else "completed"
            log_service.add(LOG_TYPE_CALL, f"{kind.upper()} generation task {suffix}", detail)
        except Exception:
            pass


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _task_identity(identity: dict[str, object]) -> dict[str, object]:
    return {
        key: identity.get(key)
        for key in ("id", "name", "role")
        if identity.get(key) not in (None, "")
    }


def _has_recoverable_request(task: dict[str, Any]) -> bool:
    return "prompt" in task and "base64_images" in task and "base_url" in task


def _task_owner_is_alive(task: dict[str, Any], current_host: str) -> bool:
    worker_host = _clean(task.get("worker_host"))
    worker_pid = _safe_float(task.get("worker_pid"))
    if worker_host and worker_host == current_host and worker_pid > 0:
        if os.name == "nt":
            return psutil.pid_exists(int(worker_pid))
        try:
            os.kill(int(worker_pid), 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    if worker_host and worker_host != current_host:
        return time.time() - _safe_float(task.get("updated_ts")) < RUNNING_TASK_REMOTE_RECOVERY_SECONDS
    return False


editable_file_task_service = EditableFileTaskService(auto_start=False)
