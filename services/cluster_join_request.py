from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Callable


class ClusterJoinRequestError(RuntimeError):
    """Raised when the host-side Worker join helper cannot finish a request."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def _write_private(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ClusterJoinRequestError(
            f"unable to write private join request file {path}: {exc}"
        ) from exc


def _read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


CREATE_OPERATION = "create"
ROTATE_OPERATION = "rotate"
SUPPORTED_OPERATIONS = (CREATE_OPERATION, ROTATE_OPERATION)


def _classify_error_status(error_message: str) -> int:
    lowered = error_message.lower()
    conflict_markers = (
        "already exists",
        "duplicate",
        "already registered",
        "do not reuse",
        "only pending, activation_failed, or joined joins can be rotated",
        "pending worker join not found",
        "worker join not found",
        # Rotating a Worker that was never created is a request-state conflict, not a
        # helper failure, so it must not read as a transient 503 the caller can retry.
        "does not exist in the worker registry",
        "worker registry does not exist",
    )
    if any(marker in lowered for marker in conflict_markers):
        return 409
    # The installer speaks Chinese when INSTALL_LANG=zh, so match its wording too.
    if "已存在" in error_message or "已被占用" in error_message or "重复" in error_message:
        return 409
    return 503


def request_worker_join_file(
    request_dir: str | Path,
    worker_no: int,
    *,
    operation: str = CREATE_OPERATION,
    timeout_seconds: float = 120,
    poll_interval_seconds: float = 0.2,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[bytes, str]:
    try:
        normalized_worker_no = int(worker_no)
    except (TypeError, ValueError) as exc:
        raise ClusterJoinRequestError("worker_no must be an integer") from exc
    if not 1 <= normalized_worker_no <= 244:
        raise ClusterJoinRequestError("worker_no must be between 1 and 244")
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in SUPPORTED_OPERATIONS:
        raise ClusterJoinRequestError(
            f"operation must be one of {', '.join(SUPPORTED_OPERATIONS)}",
            status_code=400,
        )
    if timeout_seconds <= 0:
        raise ClusterJoinRequestError("timeout_seconds must be positive")
    if poll_interval_seconds <= 0:
        raise ClusterJoinRequestError("poll_interval_seconds must be positive")

    directory = Path(request_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
    except OSError as exc:
        raise ClusterJoinRequestError(
            f"unable to prepare request directory {directory}: {exc}"
        ) from exc
    request_id = secrets.token_hex(16)
    request_path = directory / f"{request_id}.request"
    processing_path = directory / f"{request_id}.processing"
    status_path = directory / f"{request_id}.status"
    join_path = directory / f"{request_id}.join"
    error_path = directory / f"{request_id}.error"
    cancel_path = directory / f"{request_id}.cancel"
    _write_private(
        request_path,
        f"worker_no={normalized_worker_no}\noperation={normalized_operation}\n",
    )

    deadline = time.monotonic() + timeout_seconds
    completed = False
    helper_answered = False
    try:
        while time.monotonic() < deadline:
            if cancel_requested is not None and cancel_requested():
                raise ClusterJoinRequestError("Worker join request was cancelled", status_code=499)
            try:
                status_exists = status_path.is_file()
            except OSError as exc:
                raise ClusterJoinRequestError(
                    f"unable to inspect join helper status file {status_path}: {exc}"
                ) from exc
            if status_exists:
                helper_answered = True
                try:
                    status = status_path.read_text(encoding="utf-8").strip().lower()
                except OSError as exc:
                    raise ClusterJoinRequestError(
                        f"unable to read join helper status file {status_path}: {exc}"
                    ) from exc
                if status == "ok":
                    try:
                        payload = join_path.read_bytes()
                    except OSError as exc:
                        raise ClusterJoinRequestError(
                            f"unable to read generated Worker join file {join_path}: {exc}"
                        ) from exc
                    if not payload:
                        raise ClusterJoinRequestError("host-side Worker join helper returned an empty file")
                    completed = True
                    return payload, f"worker-{normalized_worker_no}.join"
                error_message = _read_optional_text(error_path)
                if error_message:
                    raise ClusterJoinRequestError(
                        f"host-side Worker join helper failed to generate the file: {error_message}",
                        status_code=_classify_error_status(error_message),
                    )
                raise ClusterJoinRequestError(
                    "host-side Worker join helper failed to generate the file"
                )
            time.sleep(poll_interval_seconds)
        raise ClusterJoinRequestError(
            "Worker join helper did not respond before the request timeout"
        )
    finally:
        if helper_answered:
            try:
                processing_path.unlink(missing_ok=True)
            except OSError:
                pass
        if not completed and not helper_answered:
            try:
                _write_private(cancel_path, "cancelled\n")
            except ClusterJoinRequestError:
                pass
            try:
                processing_path.unlink()
            except OSError:
                pass
        for path in (request_path, status_path, join_path, error_path):
            try:
                path.unlink()
            except OSError:
                pass
