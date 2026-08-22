from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Iterator, Mapping

from services.cluster_settings import ROLE_STANDALONE, load_cluster_settings
from services.image_failure import ImageGenerationError, classify_image_exception, image_failure
from services.image_delivery import is_url_only_result
from services.image_task_service import image_task_service
from services.protocol.conversation import ImageOutput


_PREPARED_RESULT_KEY = "_durable_image_prepared_result"
_SUBMISSION_KEY = "_durable_image_submission"
_ALLOWED_RESPONSE_FORMATS = {"b64_json", "url"}


def normalize_response_format(value: object, default: str = "b64_json") -> str:
    response_format = str(value or default).strip() or default
    if response_format not in _ALLOWED_RESPONSE_FORMATS:
        raise ImageGenerationError(
            "response_format must be one of: b64_json, url",
            failure=image_failure(
                "invalid_image_input",
                raw_detail=f"unsupported response_format: {response_format}",
            ),
        )
    if load_cluster_settings().node_role != ROLE_STANDALONE:
        return "url"
    return response_format


def _resolve_response_format(
    submission: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    fallback: str,
) -> str:
    return normalize_response_format(
        submission.get("response_format")
        or request_payload.get("response_format")
        or fallback,
        "b64_json",
    )


def _protocol_wait_timeout() -> float:
    settings = getattr(image_task_service, "settings", None)
    return float(getattr(settings, "protocol_wait_timeout_seconds", 300) or 300)


def _task_error(exc: Exception, task_id: str) -> ImageGenerationError:
    resolved_task_id = str(task_id or "").strip()
    if isinstance(exc, ImageGenerationError):
        if not exc.task_id and resolved_task_id:
            exc.task_id = resolved_task_id
        return exc
    failure = (
        image_failure("image_task_pending", raw_detail="image task is still running")
        if isinstance(exc, TimeoutError)
        else classify_image_exception(exc)
    )
    if isinstance(exc, TimeoutError):
        message = (
            "image task is still running; poll /api/image-tasks/{task_id} or "
            "retry with the same idempotency key"
        )
    else:
        message = str(exc or "image task failed")
    return ImageGenerationError(message, failure=failure, task_id=resolved_task_id)


def has_durable_context(body: Mapping[str, Any]) -> bool:
    return isinstance(body.get("_image_task_context"), dict)


def _context(body: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, object]]:
    context = body.get("_image_task_context")
    if not isinstance(context, dict):
        raise ValueError("durable image task context is required")
    identity = context.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("durable image task identity is required")
    return context, identity


def _submit(
    body: Mapping[str, Any],
    payload: Mapping[str, Any],
    mode: str,
    response_format: str,
) -> tuple[dict[str, Any], dict[str, object], dict[str, Any], str]:
    context, identity = _context(body)
    request_payload = dict(payload)
    request_payload["base_url"] = str(context.get("base_url") or request_payload.get("base_url") or "")
    request_payload["response_format"] = response_format
    trace_headers = dict(context.get("trace_headers") or {}) if isinstance(context.get("trace_headers"), dict) else {}
    call_id = str(body.get("_call_id") or "").strip()
    if call_id:
        trace_headers["call_id"] = call_id[:160]
    submitted = image_task_service.submit_protocol_request(
        identity,
        request_payload,
        mode,
        str(context.get("idempotency_key") or ""),
        trace_headers,
    )
    return context, identity, request_payload, str(submitted.get("task_id") or "")


def _submission(
    body: Mapping[str, Any],
    payload: Mapping[str, Any],
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    response_format = normalize_response_format(response_format, "b64_json")
    existing = body.get(_SUBMISSION_KEY)
    if isinstance(existing, dict) and str(existing.get("task_id") or ""):
        return existing
    _context_value, identity, request_payload, task_id = _submit(
        body,
        payload,
        mode,
        response_format,
    )
    if not task_id:
        raise RuntimeError("durable image task submission did not return a task id")
    submitted = {
        "identity": identity,
        "request_payload": request_payload,
        "task_id": task_id,
        "response_format": response_format,
    }
    if isinstance(body, dict):
        body[_SUBMISSION_KEY] = submitted
    return submitted


def _raise_after_response_attempt(identity: Mapping[str, object], task_id: str, error: ImageGenerationError) -> None:
    image_task_service.mark_response_attempted(identity, task_id)
    raise error


async def prepare_submission(
    body: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _submission,
        body,
        payload,
        mode,
        response_format,
    )


async def prepare_request(
    body: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    if body.get("stream"):
        return await prepare_submission(
            body,
            payload,
            mode=mode,
            response_format=response_format,
        )
    return await prepare(
        body,
        payload,
        mode=mode,
        response_format=response_format,
    )


def _result(
    identity: Mapping[str, object],
    task_id: str,
    terminal: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    response_format: str,
) -> dict[str, Any]:
    status = str(terminal.get("status") or "")
    required = int(terminal.get("required_jobs") or 0)
    succeeded = int(terminal.get("succeeded_jobs") or 0)
    error_code = str(terminal.get("error_code") or "image_job_failed")
    error_message = str(terminal.get("error") or terminal.get("error_message") or "image generation failed")
    if status != "success":
        image_task_service.mark_response_attempted(identity, task_id)
        raise ImageGenerationError(
            error_message,
            failure=image_failure(error_code, raw_detail=error_message),
            task_id=task_id,
        )
    if required <= 0 or succeeded <= 0:
        image_task_service.mark_response_attempted(identity, task_id)
        raise ImageGenerationError(
            error_message,
            failure=image_failure(error_code, raw_detail=error_message),
            task_id=task_id,
        )

    data: list[dict[str, Any]] = []
    image_urls: list[str] = []
    legacy_import = bool(request_payload.get("legacy_import") or terminal.get("legacy_import"))
    cluster_url_only = load_cluster_settings().node_role != ROLE_STANDALONE
    for raw_item in terminal.get("data") or []:
        if not isinstance(raw_item, dict):
            continue
        width = int(raw_item.get("width") or 0)
        height = int(raw_item.get("height") or 0)
        if width <= 0 or height <= 0:
            _raise_after_response_attempt(
                identity,
                task_id,
                ImageGenerationError(
                    "saved image dimensions are unavailable",
                    failure=image_failure("invalid_image_result"),
                    task_id=task_id,
                ),
            )
        item = {
            "revised_prompt": str(raw_item.get("revised_prompt") or request_payload.get("prompt") or ""),
            "width": width,
            "height": height,
        }
        url = str(raw_item.get("url") or "").strip()
        relative_path = str(raw_item.get("relative_path") or "").strip()
        if legacy_import and response_format != "b64_json" and url:
            item["url"] = url
            image_urls.append(url)
            data.append(item)
            continue
        if legacy_import and response_format == "b64_json" and raw_item.get("b64_json"):
            item["b64_json"] = str(raw_item.get("b64_json") or "")
            if url:
                image_urls.append(url)
            data.append(item)
            continue
        if response_format != "b64_json" and (cluster_url_only or is_url_only_result(raw_item)):
            if not url:
                _raise_after_response_attempt(
                    identity,
                    task_id,
                    ImageGenerationError(
                        "saved image URL is unavailable",
                        failure=image_failure("invalid_image_result"),
                        task_id=task_id,
                    ),
                )
            item["url"] = url
            image_urls.append(url)
            data.append(item)
            continue
        try:
            payload_bytes = image_task_service.read_result_artifact(identity, task_id, relative_path)
        except Exception as exc:
            recover = getattr(image_task_service, "get_task", None)
            if callable(recover):
                try:
                    recover(identity, task_id)
                except Exception:
                    pass
            raise ImageGenerationError(
                "saved image artifact is being repaired",
                failure=image_failure(
                    "image_task_pending",
                    raw_detail=str(exc or "saved image artifact is unavailable"),
                ),
                task_id=task_id,
            ) from exc
        if response_format == "b64_json":
            item["b64_json"] = base64.b64encode(payload_bytes).decode("ascii")
        else:
            if not url:
                _raise_after_response_attempt(
                    identity,
                    task_id,
                    ImageGenerationError(
                        "saved image URL is unavailable",
                        failure=image_failure("invalid_image_result"),
                        task_id=task_id,
                    ),
                )
            item["url"] = url
        if url:
            image_urls.append(url)
        data.append(item)
    if len(data) != required:
        _raise_after_response_attempt(
            identity,
            task_id,
            ImageGenerationError(
                "durable image task returned incomplete results",
                failure=image_failure("image_job_failed"),
                task_id=task_id,
            ),
        )
    image_task_service.mark_response_attempted(identity, task_id)
    return {
        "created": int(time.time()),
        "data": data,
        "_image_urls": image_urls,
        "task_id": task_id,
    }


async def prepare(
    body: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    prepared = body.get(_PREPARED_RESULT_KEY)
    if isinstance(prepared, dict):
        return prepared
    submission = await prepare_submission(
        body,
        payload,
        mode=mode,
        response_format=response_format,
    )
    identity = submission["identity"]
    request_payload = submission["request_payload"]
    task_id = str(submission["task_id"])
    response_format = _resolve_response_format(submission, request_payload, response_format)
    try:
        terminal = await image_task_service.wait_for_terminal_async(
            identity,
            task_id,
            timeout=_protocol_wait_timeout(),
        )
        prepared = await asyncio.to_thread(
            _result,
            identity,
            task_id,
            terminal,
            request_payload,
            response_format,
        )
    except Exception as exc:
        raise _task_error(exc, task_id) from exc
    body[_PREPARED_RESULT_KEY] = prepared
    return prepared


def execute(
    body: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    prepared = body.get(_PREPARED_RESULT_KEY)
    if isinstance(prepared, dict):
        return dict(prepared)
    submission = _submission(body, payload, mode, response_format)
    identity = submission["identity"]
    request_payload = submission["request_payload"]
    task_id = str(submission["task_id"])
    response_format = _resolve_response_format(submission, request_payload, response_format)
    try:
        terminal = image_task_service.wait_for_terminal(
            identity,
            task_id,
            timeout=_protocol_wait_timeout(),
        )
        return _result(identity, task_id, terminal, request_payload, response_format)
    except Exception as exc:
        raise _task_error(exc, task_id) from exc


def submission_task_id(body: Mapping[str, Any]) -> str:
    submission = body.get(_SUBMISSION_KEY)
    return str(submission.get("task_id") or "") if isinstance(submission, dict) else ""


def attached_submission(body: Mapping[str, Any]) -> dict[str, Any] | None:
    submission = body.get(_SUBMISSION_KEY)
    if not isinstance(submission, dict):
        return None
    task_id = str(submission.get("task_id") or "").strip()
    identity = submission.get("identity")
    request_payload = submission.get("request_payload")
    if not task_id or not isinstance(identity, Mapping) or not isinstance(request_payload, Mapping):
        return None
    return submission


def attach_submission(body: dict[str, Any], submission: Mapping[str, Any]) -> None:
    task_id = str(submission.get("task_id") or "").strip()
    identity = submission.get("identity")
    request_payload = submission.get("request_payload")
    if not task_id or not isinstance(identity, Mapping) or not isinstance(request_payload, Mapping):
        return
    attached = {
        "identity": dict(identity),
        "request_payload": dict(request_payload),
        "task_id": task_id,
    }
    task_type = str(submission.get("task_type") or "").strip()
    if task_type:
        attached["task_type"] = task_type
    response_format = str(submission.get("response_format") or request_payload.get("response_format") or "").strip()
    if response_format:
        attached["response_format"] = response_format
    body[_SUBMISSION_KEY] = attached


def ensure_submission(
    body: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
) -> dict[str, Any]:
    return _submission(body, payload, mode, response_format)


def stream_outputs(
    body: dict[str, Any],
    payload: Mapping[str, Any],
    *,
    mode: str,
    response_format: str,
    model: str,
) -> Iterator[ImageOutput]:
    submission = _submission(body, payload, mode, response_format)
    identity = submission["identity"]
    request_payload = submission["request_payload"]
    task_id = str(submission["task_id"])
    response_format = _resolve_response_format(submission, request_payload, response_format)
    total = max(1, int(request_payload.get("n") or 1))
    yield ImageOutput(
        kind="progress",
        model=model,
        index=0,
        total=total,
        upstream_event_type="queued",
        task_id=task_id,
    )

    prepared = body.get(_PREPARED_RESULT_KEY)
    if isinstance(prepared, dict):
        yield as_output(prepared, model)
        return

    timeout = _protocol_wait_timeout()
    deadline = time.monotonic() + timeout
    heartbeat_seconds = min(15.0, max(1.0, timeout))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _task_error(TimeoutError("image task is still running"), task_id)
        try:
            terminal = image_task_service.wait_for_terminal(
                identity,
                task_id,
                timeout=min(heartbeat_seconds, remaining),
            )
        except TimeoutError:
            if time.monotonic() >= deadline:
                raise _task_error(TimeoutError("image task is still running"), task_id)
            yield ImageOutput(
                kind="progress",
                model=model,
                index=0,
                total=total,
                upstream_event_type="in_progress",
                task_id=task_id,
            )
            continue
        try:
            result = _result(
                identity,
                task_id,
                terminal,
                request_payload,
                response_format,
            )
        except Exception as exc:
            raise _task_error(exc, task_id) from exc
        body[_PREPARED_RESULT_KEY] = result
        yield as_output(result, model)
        return


def as_output(result: Mapping[str, Any], model: str) -> ImageOutput:
    return ImageOutput(
        kind="result",
        model=model,
        index=1,
        total=max(1, len(result.get("data") or [])),
        created=int(result.get("created") or time.time()),
        data=[dict(item) for item in result.get("data") or [] if isinstance(item, dict)],
        image_urls=[str(item) for item in result.get("_image_urls") or [] if str(item)],
        task_id=str(result.get("task_id") or ""),
    )
