from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from api.image_inputs import image_edit_source_request_hash, parse_image_edit_request, read_image_source_groups
from api.support import allowlisted_trace_headers, require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.account_service import account_service
from services.image_task_service import image_task_service
from services.image_queue.artifact_service import InvalidImageArtifact
from services.image_queue.idempotency import select_idempotency_key
from services.image_queue.repository import IdempotencyConflict, TaskStateConflict
from services.log_service import LoggedCall
from services.quota_service import reserve_quota


class ImageGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=4)
    size: str | None = None
    quality: str = "auto"


class ResumePollRequest(BaseModel):
    extra_timeout_secs: float = Field(default=30.0, ge=5.0, le=120.0)


class ImageTaskAssetResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str | None = None
    b64_json: str | None = None
    revised_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    path: str | None = None
    relative_path: str | None = None
    sha256: str | None = None
    storage_backend: str | None = None
    worker_id: str | None = None
    image_base_url: str | None = None
    returned_url: str | None = None
    upscaled_url: str | None = None
    delivery_mode: str | None = None


class ImageTaskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    task_id: str
    client_task_id: str = ""
    status: str
    mode: str
    model: str | None = None
    n: int = 1
    size: str | None = None
    quality: str | None = None
    required_jobs: int = 1
    succeeded_jobs: int = 0
    failed_jobs: int = 0
    queue_position: int = 0
    estimated_wait_seconds: int = 0
    estimated_start_at: str | None = None
    wait_reason: str = ""
    delivery_status: str = "pending"
    stage: str = ""
    progress: str = ""
    conversation_id: str = ""
    can_resume_poll: bool = False
    data: list[ImageTaskAssetResponse] = Field(default_factory=list)
    error_code: str = ""
    error: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class ImageTasksResponse(BaseModel):
    items: list[ImageTaskResponse] = Field(default_factory=list)
    missing_ids: list[str] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0


class ImageQuotaResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    total_quota: int
    unlimited_quota_count: int
    unknown_quota_count: int
    active_accounts: int
    limited_accounts: int
    abnormal_accounts: int
    disabled_accounts: int
    available: bool


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _image_quota_payload(stats: dict) -> dict[str, object]:
    total_quota = max(0, int(stats.get("total_quota") or 0))
    unlimited = max(0, int(stats.get("unlimited_quota_count") or 0))
    unknown = max(0, int(stats.get("unknown_quota_count") or 0))
    return {
        "total_quota": total_quota,
        "unlimited_quota_count": unlimited,
        "unknown_quota_count": unknown,
        "active_accounts": max(0, int(stats.get("active") or 0)),
        "limited_accounts": max(0, int(stats.get("limited") or 0)),
        "abnormal_accounts": max(0, int(stats.get("abnormal") or 0)),
        "disabled_accounts": max(0, int(stats.get("disabled") or 0)),
        "available": total_quota > 0 or unlimited > 0 or unknown > 0,
    }


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def _task_id_from_result(result: object) -> str:
    if isinstance(result, dict):
        return str(result.get("task_id") or "").strip()
    return ""


def _commit_quota_or_raise(quota, result: object, idempotency_key: str) -> None:
    try:
        quota.commit()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "quota_commit_failed",
                "message": (
                    "image task was created but quota state could not be committed; "
                    "poll the task_id or retry with the same idempotency key"
                ),
                "task_id": _task_id_from_result(result),
                "idempotency_key": str(idempotency_key or ""),
                "reason": str(exc),
            },
        ) from exc


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-tasks", response_model=ImageTasksResponse)
    async def list_image_tasks(
        ids: str = Query(default=""),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_task_service.list_tasks,
                identity,
                _parse_task_ids(ids),
                limit,
                offset,
            )
        except InvalidImageArtifact as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "image_result_unavailable", "message": str(exc)},
            ) from exc

    @router.get("/api/image-tasks/quota", response_model=ImageQuotaResponse)
    async def image_quota_summary(
        authorization: str | None = Header(default=None),
    ):
        require_identity(authorization)
        stats = await run_in_threadpool(account_service.get_stats)
        return _image_quota_payload(stats)

    @router.post("/api/image-tasks/generations", response_model=ImageTaskResponse)
    async def create_generation_task(
        body: ImageGenerationTaskRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        call = LoggedCall(identity, "/api/image-tasks/generations", body.model, "文生图任务", request_text=body.prompt)
        await filter_or_log(call, body.prompt)
        trace_headers = allowlisted_trace_headers(request.headers)
        trace_headers["call_id"] = call.call_id
        try:
            idempotency_key = select_idempotency_key(request.headers, body.client_task_id)
            quota = reserve_quota(
                identity,
                "/api/image-tasks/generations",
                body.model,
                image_request=True,
                idempotency_key=idempotency_key,
            )
            try:
                result = await run_in_threadpool(
                    image_task_service.submit_generation,
                    identity,
                    client_task_id=body.client_task_id,
                    prompt=body.prompt,
                    model=body.model,
                    n=body.n,
                    size=body.size,
                    quality=body.quality,
                    base_url=resolve_image_base_url(request),
                    idempotency_key=idempotency_key,
                    trace_headers=trace_headers,
                )
            except Exception:
                quota.cancel()
                raise
            _commit_quota_or_raise(quota, result, idempotency_key)
            return result
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/edits", response_model=ImageTaskResponse)
    async def create_edit_task(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        client_task_id = str(payload.get("client_task_id") or "").strip()
        if not client_task_id:
            raise HTTPException(status_code=400, detail={"error": "client_task_id is required"})
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        call = LoggedCall(identity, "/api/image-tasks/edits", model, "图生图任务", request_text=prompt)
        await filter_or_log(call, prompt)
        trace_headers = allowlisted_trace_headers(request.headers)
        trace_headers["call_id"] = call.call_id
        quota = None
        existing = None
        try:
            idempotency_key = select_idempotency_key(request.headers, client_task_id)
            quota = reserve_quota(
                identity,
                "/api/image-tasks/edits",
                model,
                image_request=True,
                idempotency_key=idempotency_key,
            )
            source_request_hash = image_edit_source_request_hash(payload, image_sources, mask_sources)
            existing = await run_in_threadpool(
                image_task_service.replay_existing_edit_task,
                identity,
                client_task_id=client_task_id,
                idempotency_key=idempotency_key,
                source_request_hash=source_request_hash,
            )
        except IdempotencyConflict as exc:
            if quota is not None:
                quota.cancel()
            raise HTTPException(
                status_code=409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            if quota is not None:
                quota.cancel()
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception:
            if quota is not None:
                quota.cancel()
            raise
        if existing is not None:
            _commit_quota_or_raise(quota, existing, idempotency_key)
            return existing
        try:
            images, resolved_masks = await read_image_source_groups(image_sources, mask_sources)
            masks = resolved_masks or None
            result = await run_in_threadpool(
                image_task_service.submit_edit,
                identity,
                client_task_id=client_task_id,
                prompt=prompt,
                model=model,
                n=payload.get("n", 1),
                size=payload["size"],
                quality=payload["quality"],
                base_url=resolve_image_base_url(request),
                images=images,
                masks=masks,
                idempotency_key=idempotency_key,
                trace_headers=trace_headers,
                response_format=payload["response_format"],
                source_request_hash=source_request_hash,
            )
        except IdempotencyConflict as exc:
            quota.cancel()
            raise HTTPException(
                status_code=409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            quota.cancel()
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception:
            quota.cancel()
            raise
        _commit_quota_or_raise(quota, result, idempotency_key)
        return result

    @router.post("/api/image-tasks/{task_id}/resume-poll", response_model=ImageTaskResponse)
    async def resume_image_poll(
        task_id: str,
        body: ResumePollRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                image_task_service.resume_poll,
                identity,
                task_id,
                body.extra_timeout_secs,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/image-tasks/{task_id}", response_model=ImageTaskResponse)
    async def get_image_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(image_task_service.get_task, identity, task_id)
        except InvalidImageArtifact as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "image_result_unavailable",
                    "message": str(exc),
                    "task_id": task_id,
                },
            ) from exc
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "image_task_not_found",
                    "message": message,
                    "task_id": task_id,
                },
            ) from exc

    @router.post("/api/image-tasks/{task_id}/cancel", response_model=ImageTaskResponse)
    async def cancel_image_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(image_task_service.cancel, identity, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/{task_id}/ack", response_model=ImageTaskResponse)
    async def acknowledge_image_task(
        task_id: str,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(image_task_service.acknowledge, identity, task_id)
        except TaskStateConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": exc.code, "message": str(exc)},
            ) from exc
        except InvalidImageArtifact as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "image_result_unavailable", "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc

    return router
