from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from api.image_inputs import image_edit_source_request_hash, parse_image_edit_request, read_image_source_groups
from api.support import allowlisted_trace_headers, require_identity, resolve_api_base_url, resolve_image_base_url
from services.content_filter import check_request, request_shape, request_text
from services.editable_file_task_service import EditableFileTaskConflict, editable_file_task_service
from services.image_queue.idempotency import select_idempotency_key
from services.image_queue.repository import IdempotencyConflict
from services.image_queue.resource_controller import ImageQueueResourcePressureError
from services.image_task_service import image_task_service
from services.log_service import LoggedCall
from services.protocol import (
    anthropic_v1_messages,
    openai_v1_chat_complete,
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_models,
    openai_v1_response,
    openai_search,
    durable_image,
)
from services.quota_service import reserve_quota
from utils.helper import has_response_image_generation_tool, is_image_chat_request


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    quality: str = "auto"
    response_format: str = "b64_json"
    history_disabled: bool = True
    stream: bool | None = None
    client_task_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    prompt: str | None = None
    n: int | None = None
    stream: bool | None = None
    modalities: list[str] | None = None
    messages: list[dict[str, object]] | None = None


class ResponseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    input: object | None = None
    tools: list[dict[str, object]] | None = None
    tool_choice: object | None = None
    stream: bool | None = None


class AnthropicMessageRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[dict[str, object]] | None = None
    system: object | None = None
    stream: bool | None = None


class SearchRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class EditableFileTaskRequest(BaseModel):
    prompt: str = ""
    kind: str = "ppt"
    base64_images: list[str] = Field(default_factory=list)
    client_task_id: str | None = None


TRACE_REQUEST_HEADERS = {
    "x-request-id": "x_request_id",
    "x-newapi-request-id": "x_newapi_request_id",
    "x-oneapi-request-id": "x_oneapi_request_id",
    "x-channel-id": "x_channel_id",
    "x-channel-name": "x_channel_name",
}


def attach_trace_headers(call: LoggedCall, request: Request) -> None:
    if not call._trace_image_perf():
        return
    headers: dict[str, str] = {}
    for header, field in TRACE_REQUEST_HEADERS.items():
        value = str(request.headers.get(header) or "").strip()
        if value:
            headers[field] = value[:160]
    if headers:
        existing = call.trace_metadata.get("request_headers")
        if isinstance(existing, dict):
            existing.update(headers)
        else:
            call.trace_metadata["request_headers"] = headers


def attach_image_task_context(
    payload: dict[str, object],
    identity: dict[str, object],
    request: Request,
) -> None:
    try:
        idempotency_key = select_idempotency_key(
            request.headers,
            str(payload.get("client_task_id") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "idempotency_key_required",
                "message": (
                    "image requests require Idempotency-Key, X-NewAPI-Request-Id, "
                    "X-OneAPI-Request-Id, or client_task_id"
                ),
            },
        )
    payload["_image_task_context"] = {
        "identity": dict(identity),
        "idempotency_key": idempotency_key,
        "quota_idempotency_key": idempotency_key,
        "quota_idempotency_aliases": [
            client_task_id
            for client_task_id in [str(payload.get("client_task_id") or "").strip()]
            if client_task_id and client_task_id != idempotency_key
        ],
        "trace_headers": allowlisted_trace_headers(request.headers),
        "base_url": resolve_image_base_url(request),
    }


def require_editable_task_id(body: EditableFileTaskRequest, request: Request) -> str:
    try:
        client_task_id = select_idempotency_key(
            request.headers,
            str(body.client_task_id or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    if not client_task_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "idempotency_key_required",
                "message": (
                    "editable file tasks require Idempotency-Key, X-NewAPI-Request-Id, "
                    "X-OneAPI-Request-Id, or client_task_id"
                ),
            },
        )
    return client_task_id


def require_non_empty_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": f"{field_name} is required"})
    return text


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def _editable_task_id_from_result(result: object) -> str:
    if not isinstance(result, dict):
        return ""
    for key in ("task_id", "id", "taskId"):
        value = str(result.get(key) or "").strip()
        if value:
            return value
    return ""


def _commit_editable_quota_or_raise(quota, result: object, idempotency_key: str) -> None:
    try:
        quota.commit()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "quota_commit_failed",
                    "message": (
                        "editable file task was created but quota state could not be committed; "
                        "poll the task_id or retry with the same client_task_id"
                    ),
                    "task_id": _editable_task_id_from_result(result),
                    "idempotency_key": str(idempotency_key or ""),
                }
            },
        ) from exc


async def _attach_existing_protocol_submission(
    payload: dict[str, object],
    identity: dict[str, object],
    *,
    task_type: str,
    source_request_hash: str,
) -> bool:
    try:
        existing_submission = await run_in_threadpool(
            image_task_service.replay_existing_protocol_submission,
            identity,
            client_task_id=str(payload.get("client_task_id") or ""),
            idempotency_key=str(payload["_image_task_context"].get("idempotency_key") or ""),
            task_type=task_type,
            source_request_hash=source_request_hash,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": exc.code, "message": str(exc)},
        ) from exc
    if existing_submission is None:
        return False
    durable_image.attach_submission(payload, existing_submission)
    return True


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        try:
            return await run_in_threadpool(openai_v1_models.list_models)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        prompt = require_non_empty_text(payload.get("prompt"), "prompt")
        payload["prompt"] = prompt
        payload["base_url"] = resolve_image_base_url(request)
        attach_image_task_context(payload, identity, request)
        call = LoggedCall(identity, "/v1/images/generations", body.model, "文生图", request_text=prompt)
        attach_trace_headers(call, request)
        call.attach_trace_metadata(payload)
        await filter_or_log(call, prompt)
        return await call.run(
            openai_v1_image_generations.handle,
            payload,
            async_before=lambda: durable_image.prepare_request(
                payload,
                payload,
                mode="generation",
                response_format=str(payload.get("response_format") or "b64_json"),
            ),
        )

    @router.post("/v1/images/edits")
    async def edit_images(
            request: Request,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        payload["base_url"] = resolve_image_base_url(request)
        attach_image_task_context(payload, identity, request)
        source_request_hash = image_edit_source_request_hash(payload, image_sources, mask_sources)
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/v1/images/edits", model, "图生图", request_text=prompt)
        attach_trace_headers(call, request)
        call.attach_trace_metadata(payload)
        await filter_or_log(call, prompt)
        async def prepare_edit_request():
            replayed = await _attach_existing_protocol_submission(
                payload,
                identity,
                task_type="edit",
                source_request_hash=source_request_hash,
            )
            if not replayed:
                images, masks = await read_image_source_groups(image_sources, mask_sources)
                payload["images"] = images
                if masks:
                    payload["mask"] = masks
                if source_request_hash:
                    payload["source_request_hash"] = source_request_hash
            return await durable_image.prepare_request(
                payload,
                payload,
                mode="edit",
                response_format=str(payload.get("response_format") or "b64_json"),
            )
        return await call.run(
            openai_v1_image_edit.handle,
            payload,
            async_before=prepare_edit_request,
        )

    @router.post("/v1/chat/completions")
    async def create_chat_completion(body: ChatCompletionRequest, request: Request, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("prompt"), payload.get("messages"))
        image_chat = is_image_chat_request(payload)
        if image_chat:
            payload["base_url"] = resolve_image_base_url(request)
            attach_image_task_context(payload, identity, request)
        call = LoggedCall(
            identity,
            "/v1/chat/completions",
            model,
            "聊天生图" if image_chat else "文本生成",
            request_text=request_preview,
            request_shape=request_shape(payload.get("messages")),
            image_request=image_chat,
        )
        attach_trace_headers(call, request)
        call.attach_trace_metadata(payload)
        await filter_or_log(call, request_preview)
        async_before = None
        if image_chat:
            async def prepare_chat_image_request():
                task_type, source_request_hash = await run_in_threadpool(
                    openai_v1_chat_complete.durable_image_replay_fingerprint,
                    payload,
                )
                await _attach_existing_protocol_submission(
                    payload,
                    identity,
                    task_type=task_type,
                    source_request_hash=source_request_hash,
                )
                durable_payload, mode, response_format = await run_in_threadpool(
                    openai_v1_chat_complete.durable_image_request,
                    payload,
                )
                return await durable_image.prepare_request(
                    payload,
                    durable_payload,
                    mode=mode,
                    response_format=response_format,
                )

            async_before = prepare_chat_image_request
        return await call.run(openai_v1_chat_complete.handle, payload, async_before=async_before)

    @router.post("/v1/responses")
    async def create_response(body: ResponseCreateRequest, request: Request, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("input"), payload.get("instructions"))
        image_response = has_response_image_generation_tool(payload)
        if image_response:
            payload["base_url"] = resolve_image_base_url(request)
            attach_image_task_context(payload, identity, request)
        call = LoggedCall(
            identity,
            "/v1/responses",
            model,
            "Responses",
            request_text=request_preview,
            request_shape=request_shape(payload.get("input")),
            image_request=image_response,
        )
        attach_trace_headers(call, request)
        call.attach_trace_metadata(payload)
        await filter_or_log(call, request_preview)
        async_before = None
        if image_response:
            async def prepare_response_image_request():
                task_type, source_request_hash = await run_in_threadpool(
                    openai_v1_response.durable_image_replay_fingerprint,
                    payload,
                )
                await _attach_existing_protocol_submission(
                    payload,
                    identity,
                    task_type=task_type,
                    source_request_hash=source_request_hash,
                )
                durable_payload, mode, response_format = await run_in_threadpool(
                    openai_v1_response.durable_image_request,
                    payload,
                )
                return await durable_image.prepare_request(
                    payload,
                    durable_payload,
                    mode=mode,
                    response_format=response_format,
                )

            async_before = prepare_response_image_request
        return await call.run(openai_v1_response.handle, payload, async_before=async_before)

    @router.post("/v1/messages")
    async def create_message(
            body: AnthropicMessageRequest,
            authorization: str | None = Header(default=None),
            x_api_key: str | None = Header(default=None, alias="x-api-key"),
            anthropic_version: str | None = Header(default=None, alias="anthropic-version"),
    ):
        identity = require_identity(authorization or (f"Bearer {x_api_key}" if x_api_key else None))
        payload = body.model_dump(mode="python")
        model = str(payload.get("model") or "auto")
        request_preview = request_text(payload.get("system"), payload.get("messages"), payload.get("tools"))
        call = LoggedCall(identity, "/v1/messages", model, "Messages", request_text=request_preview)
        await filter_or_log(call, request_preview)
        return await call.run(anthropic_v1_messages.handle, payload, sse="anthropic")

    @router.post("/v1/search")
    async def search(body: SearchRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        call = LoggedCall(identity, "/v1/search", openai_search.MODEL, "搜索", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        return await call.run(openai_search.handle, body.model_dump(mode="python"))

    @router.get("/v1/editable-file-tasks")
    async def list_editable_file_tasks(ids: str = "", authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        task_ids = [item.strip() for item in ids.split(",") if item.strip()]
        return await run_in_threadpool(editable_file_task_service.list_tasks, identity, task_ids)

    @router.post("/v1/editable-file-tasks")
    async def create_editable_file_task(body: EditableFileTaskRequest, request: Request, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        kind = (body.kind or "ppt").strip().lower()
        if kind not in {"ppt", "psd"}:
            raise HTTPException(status_code=400, detail={"error": "kind must be ppt or psd"})
        client_task_id = require_editable_task_id(body, request)
        endpoint = f"/v1/{kind}/generations"
        call = LoggedCall(identity, endpoint, "gpt-5-5-thinking", f"{kind.upper()} generation task", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        quota = reserve_quota(
            identity,
            endpoint,
            "gpt-5-5-thinking",
            image_request=True,
            idempotency_key=client_task_id,
        )
        submit = editable_file_task_service.submit_psd if kind == "psd" else editable_file_task_service.submit_ppt
        try:
            result = await run_in_threadpool(
                submit,
                identity,
                client_task_id=client_task_id,
                prompt=body.prompt,
                base64_images=body.base64_images,
                base_url=resolve_api_base_url(request),
            )
        except EditableFileTaskConflict as exc:
            quota.cancel()
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except ImageQueueResourcePressureError as exc:
            quota.cancel()
            raise HTTPException(
                status_code=503,
                detail={"error": exc.code, "reason": exc.reason, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            quota.cancel()
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception:
            quota.cancel()
            raise
        _commit_editable_quota_or_raise(quota, result, client_task_id)
        call.log("已入队", result, status="queued")
        return result

    @router.get("/files/{file_path:path}")
    @router.head("/files/{file_path:path}")
    async def download_editable_file(file_path: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        try:
            path = await run_in_threadpool(editable_file_task_service.file_path_for_identity, identity, file_path)
        except Exception as exc:
            raise HTTPException(status_code=404, detail={"error": "file not found"}) from exc
        return FileResponse(path, filename=path.name)

    @router.post("/v1/ppt/generations")
    async def create_ppt_task(body: EditableFileTaskRequest, request: Request, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        client_task_id = require_editable_task_id(body, request)
        call = LoggedCall(identity, "/v1/ppt/generations", "gpt-5-5-thinking", "PPT生成任务", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        quota = reserve_quota(
            identity,
            "/v1/ppt/generations",
            "gpt-5-5-thinking",
            image_request=True,
            idempotency_key=client_task_id,
        )
        try:
            result = await run_in_threadpool(
                editable_file_task_service.submit_ppt,
                identity,
                client_task_id=client_task_id,
                prompt=body.prompt,
                base64_images=body.base64_images,
                base_url=resolve_api_base_url(request),
            )
        except EditableFileTaskConflict as exc:
            quota.cancel()
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except ImageQueueResourcePressureError as exc:
            quota.cancel()
            raise HTTPException(
                status_code=503,
                detail={"error": exc.code, "reason": exc.reason, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            quota.cancel()
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception:
            quota.cancel()
            raise
        _commit_editable_quota_or_raise(quota, result, client_task_id)
        call.log("已入队", result, status="queued")
        return result

    @router.post("/v1/psd/generations")
    async def create_psd_task(body: EditableFileTaskRequest, request: Request, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        client_task_id = require_editable_task_id(body, request)
        call = LoggedCall(identity, "/v1/psd/generations", "gpt-5-5-thinking", "PSD生成任务", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        quota = reserve_quota(
            identity,
            "/v1/psd/generations",
            "gpt-5-5-thinking",
            image_request=True,
            idempotency_key=client_task_id,
        )
        try:
            result = await run_in_threadpool(
                editable_file_task_service.submit_psd,
                identity,
                client_task_id=client_task_id,
                prompt=body.prompt,
                base64_images=body.base64_images,
                base_url=resolve_api_base_url(request),
            )
        except EditableFileTaskConflict as exc:
            quota.cancel()
            raise HTTPException(status_code=409, detail={"error": str(exc)}) from exc
        except ImageQueueResourcePressureError as exc:
            quota.cancel()
            raise HTTPException(
                status_code=503,
                detail={"error": exc.code, "reason": exc.reason, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            quota.cancel()
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception:
            quota.cancel()
            raise
        _commit_editable_quota_or_raise(quota, result, client_task_id)
        call.log("已入队", result, status="queued")
        return result

    return router
