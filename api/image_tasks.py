from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.image_inputs import parse_image_edit_request, read_image_sources
from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.image_task_service import image_task_service
from services.log_service import LoggedCall


class ImageGenerationTaskRequest(BaseModel):
    client_task_id: str = Field(..., min_length=1)
    group_id: str | None = None
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-image-2"
    n: Any = 1
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str = "auto"
    response_format: str = "b64_json"


class ResumePollRequest(BaseModel):
    extra_timeout_secs: float = Field(default=30.0, ge=5.0, le=120.0)


def _parse_task_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


TASK_URL_KEYS = {"url", "urls", "image_url", "remote_url", "thumbnail_url", "view_path", "thumbnail_path"}


def _strip_image_urls(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_image_urls(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_image_urls(item)
            for key, item in value.items()
            if key not in TASK_URL_KEYS
        }
    return value


async def filter_or_log(call: LoggedCall, text: str) -> None:
    try:
        await run_in_threadpool(check_request, text)
    except HTTPException as exc:
        call.log("调用失败", status="failed", error=str(exc.detail))
        raise


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/image-tasks")
    async def list_image_tasks(
        ids: str = Query(default=""),
        include_image_data: bool = Query(default=True),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        result = await run_in_threadpool(image_task_service.list_tasks, identity, _parse_task_ids(ids), include_image_data)
        return _strip_image_urls(result)

    @router.post("/api/image-tasks/generations")
    async def create_generation_task(
        body: ImageGenerationTaskRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/generations", body.model, "文生图任务", request_text=body.prompt), body.prompt)
        try:
            result = await run_in_threadpool(
                image_task_service.submit_generation,
                identity,
                client_task_id=body.client_task_id,
                group_id=body.group_id,
                prompt=body.prompt,
                model=body.model,
                n=body.n,
                size=body.size,
                aspect_ratio=body.aspect_ratio,
                quality=body.quality,
                response_format="b64_json",
                base_url=resolve_image_base_url(request),
            )
            return _strip_image_urls(result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/edits")
    async def create_edit_task(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        payload, image_sources, mask_sources = await parse_image_edit_request(request)
        if payload["n"] != 1:
            raise HTTPException(status_code=400, detail={"error": "image task n must be 1"})
        client_task_id = str(payload.get("client_task_id") or "").strip()
        if not client_task_id:
            raise HTTPException(status_code=400, detail={"error": "client_task_id is required"})
        group_id = str(payload.get("group_id") or "").strip() or None
        prompt = str(payload["prompt"])
        model = str(payload["model"])
        await filter_or_log(LoggedCall(identity, "/api/image-tasks/edits", model, "图生图任务", request_text=prompt), prompt)
        images = await read_image_sources(image_sources)
        masks = await read_image_sources(mask_sources) if mask_sources else None
        try:
            result = await run_in_threadpool(
                image_task_service.submit_edit,
                identity,
                client_task_id=client_task_id,
                group_id=group_id,
                prompt=prompt,
                model=model,
                n=payload["n"],
                size=payload["size"],
                aspect_ratio=payload.get("aspect_ratio"),
                quality=payload["quality"],
                response_format="b64_json",
                base_url=resolve_image_base_url(request),
                images=images,
                masks=masks,
            )
            return _strip_image_urls(result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/image-tasks/{task_id}/resume-poll")
    async def resume_image_poll(
        task_id: str,
        body: ResumePollRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            result = await run_in_threadpool(
                image_task_service.resume_poll,
                identity,
                task_id,
                body.extra_timeout_secs,
            )
            return _strip_image_urls(result)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router
