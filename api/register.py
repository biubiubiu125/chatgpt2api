from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.support import require_admin
from services.register.log_redaction import redact_register_snapshot
from services.register_service import register_service

class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    proxy_required: bool | None = None
    max_inflight_per_proxy: int | None = Field(default=None, ge=0)
    total: int | None = None
    threads: int | None = None
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    auto_schedule_enabled: bool | None = None
    register_peak: dict | None = None
    register_offpeak: dict | None = None
    check_interval: int | None = None


class OutlookPoolResetRequest(BaseModel):
    scope: str | None = None


class GptMailStatusRequest(BaseModel):
    provider: dict | None = None
    force: bool | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register")
    async def get_register_config(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": await run_in_threadpool(register_service.get)}

    @router.post("/api/register")
    async def update_register_config(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "register": await run_in_threadpool(
                    register_service.update,
                    body.model_dump(exclude_none=True),
                )
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/register/start")
    async def start_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": await run_in_threadpool(register_service.start)}

    @router.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": await run_in_threadpool(register_service.stop)}

    @router.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": await run_in_threadpool(register_service.reset)}

    @router.post("/api/register/outlook-pool/reset")
    async def reset_outlook_pool(body: OutlookPoolResetRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {
            "register": await run_in_threadpool(
                register_service.reset_outlook_pool,
                body.scope or "all",
            )
        }

    @router.post("/api/register/gptmail/status")
    async def get_gptmail_status(body: GptMailStatusRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "status": await run_in_threadpool(
                    register_service.gptmail_status,
                    body.provider,
                    force=bool(body.force),
                )
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/register/gptmail/refresh-key")
    async def refresh_gptmail_public_key(body: GptMailStatusRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {
                "status": await run_in_threadpool(
                    register_service.refresh_gptmail_public_key,
                    body.provider,
                    force=body.force is not False,
                )
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/register/events")
    async def register_events(authorization: str | None = Header(default=None)):
        require_admin(authorization)

        async def stream():
            last = ""
            while True:
                snapshot = await run_in_threadpool(register_service.get)
                snapshot = redact_register_snapshot(snapshot)
                payload = json.dumps(snapshot, ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
