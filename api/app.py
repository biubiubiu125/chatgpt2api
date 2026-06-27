from __future__ import annotations

from contextlib import asynccontextmanager
import os
from threading import Event

from anyio.to_thread import current_default_thread_limiter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api import accounts, ai, image_tasks, register, system
from api.errors import install_exception_handlers
from api.support import resolve_web_asset, start_limited_account_watcher
from services.backup_service import backup_service
from services.config import config
from services.image_service import start_image_cleanup_scheduler
from services.log_service import cleanup_old_logs, start_log_cleanup_scheduler
from services.realtime_monitor_service import realtime_monitor_service
from utils.log import logger


def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _configure_threadpool() -> None:
    tokens = _env_int("CHATGPT2API_THREAD_TOKENS", 80, 1)
    limiter = current_default_thread_limiter()
    previous = int(getattr(limiter, "total_tokens", 0) or 0)
    if previous != tokens:
        limiter.total_tokens = tokens
    realtime_monitor_service.set_threadpool(tokens=tokens, previous_tokens=previous)
    logger.info({
        "event": "runtime_threadpool_configured",
        "previous_tokens": previous,
        "tokens": tokens,
    })


def create_app() -> FastAPI:
    app_version = config.app_version

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _configure_threadpool()
        stop_event = Event()
        thread = start_limited_account_watcher(stop_event)
        cleanup_thread = start_image_cleanup_scheduler(stop_event)
        log_cleanup_thread = start_log_cleanup_scheduler(stop_event)
        backup_service.start()
        config.cleanup_old_images()
        cleanup_old_logs()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)
            cleanup_thread.join(timeout=1)
            log_cleanup_thread.join(timeout=1)
            backup_service.stop()

    app = FastAPI(title="chatgpt2api", version=app_version, lifespan=lifespan)
    install_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ai.create_router())
    app.include_router(accounts.create_router())
    app.include_router(image_tasks.create_router())
    app.include_router(register.create_router())
    app.include_router(system.create_router(app_version))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_web(full_path: str):
        asset = resolve_web_asset(full_path)
        if asset is not None:
            return FileResponse(asset)
        if full_path.strip("/").startswith("_next/"):
            raise HTTPException(status_code=404, detail="Not Found")
        fallback = resolve_web_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(fallback)

    return app
