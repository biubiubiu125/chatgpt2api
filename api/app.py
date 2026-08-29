from __future__ import annotations

from contextlib import asynccontextmanager
import os
from threading import Event, Thread

from anyio.to_thread import current_default_thread_limiter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from api import accounts, ai, image_tasks, prompts, register, system
from api.errors import install_exception_handlers
from api.support import resolve_web_asset, start_limited_account_watcher
from services.cluster_settings import (
    is_cluster_public_path,
    load_cluster_settings,
    worker_join_marker_status,
    WORKER_JOIN_STATUS_JOINED,
)
from services.account_service import account_service
from services.backup_service import backup_service
from services.config import config
from services.editable_file_task_service import editable_file_task_service
from services.dashboard_metrics_service import dashboard_metrics_service
from services.image_service import start_image_cleanup_scheduler
from services.image_queue.database import ImageQueueUnavailableError
from services.image_queue.settings import ImageQueueConfigurationError
from services.image_task_service import image_task_service
from services.log_service import cleanup_old_logs, start_log_cleanup_scheduler
from services.register_service import register_service
from services.realtime_monitor_service import realtime_monitor_service
from services.threadpool_governor import ThreadPoolGovernor
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


def _env_float(
    name: str,
    default: float,
    minimum: float = 0.05,
    maximum: float | None = None,
) -> float:
    try:
        value = float(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _resolve_threadpool_ceiling() -> int:
    runtime = config.get_runtime_capacity_settings()
    return _env_int(
        "CHATGPT2API_THREAD_TOKENS",
        int(runtime.get("text_concurrency_limit") or 80),
        1,
    )


def _configure_threadpool() -> int:
    tokens = _resolve_threadpool_ceiling()
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
    return tokens


def _build_threadpool_governor(
    *,
    resource_controller,
    limiter,
    ceiling: int,
) -> ThreadPoolGovernor:
    def on_update(*, previous_tokens: int, tokens: int, snapshot, ceiling: int) -> None:
        realtime_monitor_service.set_threadpool(tokens=tokens, previous_tokens=previous_tokens)
        logger.info({
            "event": "runtime_threadpool_adjusted",
            "previous_tokens": previous_tokens,
            "tokens": tokens,
            "ceiling": ceiling,
            "cpu_percent": getattr(snapshot, "cpu_percent", None),
        })

    return ThreadPoolGovernor(
        resource_controller=resource_controller,
        limiter=limiter,
        ceiling=ceiling,
        on_update=on_update,
    )


def _configure_image_queue_integrations(cluster_settings=None, *, on_resource_controller=None) -> None:
    repository = getattr(image_task_service, "repository", None)
    if repository is not None:
        realtime_monitor_service.set_image_queue_provider(repository.queue_snapshot)
        backup_service.set_image_queue_provider(repository.logical_backup)
        backup_service.set_image_queue_restore_provider(repository.restore_logical_backup)
        config.set_image_retention_protection(repository.protected_artifact_paths)
    artifact_service = getattr(image_task_service, "artifact_service", None)
    backup_service.set_image_queue_artifact_root(
        getattr(artifact_service, "root", None) if artifact_service is not None else None
    )
    worker = getattr(image_task_service, "worker", None)
    resource_controller = getattr(worker, "resource_controller", None) if worker is not None else None
    register_service.set_resource_controller(resource_controller)
    editable_file_task_service.set_resource_controller(resource_controller)
    if on_resource_controller is not None:
        on_resource_controller(resource_controller)
    register_service.set_registration_submitter(
        getattr(worker, "submit_registration", None) if worker is not None else None
    )
    if (cluster_settings or load_cluster_settings()).run_worker:
        register_service.resume_if_enabled()


def _start_image_queue_retry(
    stop_event: Event,
    cluster_settings=None,
    *,
    on_recovered=None,
    on_resource_controller=None,
) -> Thread:
    retry_seconds = _env_float("IMAGE_QUEUE_STARTUP_RETRY_SECONDS", 5.0, 0.05, 300.0)
    resolved_cluster_settings = cluster_settings or load_cluster_settings()

    def retry() -> None:
        while not stop_event.wait(retry_seconds):
            try:
                if resolved_cluster_settings.is_worker and resolved_cluster_settings.run_worker:
                    marker_status = worker_join_marker_status(resolved_cluster_settings)
                    if marker_status != WORKER_JOIN_STATUS_JOINED:
                        logger.warning({
                            "event": "worker_join_activation_pending",
                            "status": marker_status,
                            "impact": "worker public routes remain available; image queue is not claiming jobs",
                        })
                        continue
                image_task_service.start()
                _configure_image_queue_integrations(
                    resolved_cluster_settings,
                    on_resource_controller=on_resource_controller,
                )
                if on_recovered is not None:
                    on_recovered()
            except (
                ImageQueueUnavailableError,
                ImageQueueConfigurationError,
                SQLAlchemyError,
                RuntimeError,
            ) as exc:
                logger.warning({
                    "event": "image_queue_startup_retry_failed",
                    "error": str(exc),
                })
                continue
            logger.info({"event": "image_queue_startup_recovered"})
            return

    thread = Thread(target=retry, name="image-queue-startup-retry", daemon=True)
    thread.start()
    return thread


def create_app() -> FastAPI:
    app_version = config.app_version
    cluster_settings = load_cluster_settings(resolve_image_base_host=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        threadpool_ceiling = _configure_threadpool() or _resolve_threadpool_ceiling()
        thread_limiter = current_default_thread_limiter()
        stop_event = Event()
        retry_thread: Thread | None = None
        register_scheduler_thread: Thread | None = None
        account_watcher_thread: Thread | None = None
        image_cleanup_thread: Thread | None = None
        log_cleanup_thread: Thread | None = None
        threadpool_governor: ThreadPoolGovernor | None = None
        run_main_services = not cluster_settings.is_worker
        queue_started = False

        def start_threadpool_governor(resource_controller) -> None:
            nonlocal threadpool_governor
            if resource_controller is None or threadpool_governor is not None:
                return
            try:
                threadpool_governor = _build_threadpool_governor(
                    resource_controller=resource_controller,
                    limiter=thread_limiter,
                    ceiling=threadpool_ceiling,
                )
                threadpool_governor.start()
            except Exception as exc:
                logger.error({
                    "event": "runtime_threadpool_governor_start_failed",
                    "error": str(exc),
                })
                threadpool_governor = None

        def start_register_scheduler_once() -> None:
            nonlocal register_scheduler_thread
            if not cluster_settings.run_worker or register_scheduler_thread is not None:
                return
            register_scheduler_thread = register_service.start_auto_scheduler(stop_event)

        # Until PostgreSQL confirms exactly which artifacts are disposable, all
        # existing files are durable queue state and must be retained.
        config.set_image_retention_protection(config.all_image_paths)
        backup_service.set_register_config_provider(register_service._runtime_config)
        worker_queue_ready = True
        if cluster_settings.is_worker and cluster_settings.run_worker:
            marker_status = worker_join_marker_status(cluster_settings)
            worker_queue_ready = marker_status == WORKER_JOIN_STATUS_JOINED
            if not worker_queue_ready:
                logger.warning({
                    "event": "worker_join_activation_pending",
                    "status": marker_status,
                    "impact": "worker public routes remain available; image queue is not claiming jobs",
                })
        try:
            if worker_queue_ready:
                image_task_service.start()
                queue_started = True
                _configure_image_queue_integrations(
                    cluster_settings,
                    on_resource_controller=start_threadpool_governor,
                )
                logger.info({
                    "event": "image_queue_integrations_ready",
                    "note": (
                        "multi-instance deployments require sticky sessions or "
                        "shared PostgreSQL plus node-owned image URL delivery"
                    ),
                })
            else:
                retry_thread = _start_image_queue_retry(
                    stop_event,
                    cluster_settings,
                    on_recovered=start_register_scheduler_once,
                    on_resource_controller=start_threadpool_governor,
                )
        except (ImageQueueUnavailableError, ImageQueueConfigurationError, SQLAlchemyError) as exc:
            logger.error({
                "event": "image_queue_startup_unavailable",
                "error": str(exc),
                "impact": "image APIs return 503 until PostgreSQL is available; text APIs remain available",
            })
            retry_thread = _start_image_queue_retry(
                stop_event,
                cluster_settings,
                on_recovered=start_register_scheduler_once,
                on_resource_controller=start_threadpool_governor,
            )
        if run_main_services:
            account_service.cleanup_auto_remove_accounts()
            account_watcher_thread = start_limited_account_watcher(stop_event)
            image_cleanup_thread = start_image_cleanup_scheduler(stop_event)
            log_cleanup_thread = start_log_cleanup_scheduler(stop_event)
            backup_service.start()
            editable_file_task_service.start()
        if queue_started:
            start_register_scheduler_once()
        if run_main_services:
            config.cleanup_old_images()
            cleanup_old_logs()
        try:
            yield
        finally:
            stop_event.set()
            if retry_thread is not None:
                retry_thread.join(timeout=1)
            if register_scheduler_thread is not None:
                register_scheduler_thread.join(timeout=1)
            register_service.shutdown(timeout=30)
            if run_main_services:
                editable_file_task_service.stop(timeout=30)
            image_task_service.stop(timeout=30)
            register_service.set_registration_submitter(None)
            for thread in (
                account_watcher_thread,
                image_cleanup_thread,
                log_cleanup_thread,
            ):
                if thread is not None:
                    thread.join(timeout=1)
            if threadpool_governor is not None:
                threadpool_governor.stop(timeout=5)
            if run_main_services:
                try:
                    dashboard_metrics_service.flush()
                except Exception as exc:
                    logger.error({"event": "dashboard_metrics_shutdown_flush_failed", "error": str(exc)})
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
    if not cluster_settings.run_api:
        @app.middleware("http")
        async def cluster_api_disabled_guard(request, call_next):
            if not is_cluster_public_path(request.url.path, allow_images=cluster_settings.run_worker):
                return JSONResponse(
                    {
                        "detail": {
                            "error": "api_disabled_for_node_role",
                            "message": "this node only exposes cluster public endpoints",
                            "node_role": cluster_settings.node_role,
                        }
                    },
                    status_code=403,
                )
            return await call_next(request)

    if cluster_settings.run_api:
        app.include_router(ai.create_router())
        app.include_router(accounts.create_router())
        app.include_router(image_tasks.create_router())
        app.include_router(prompts.create_router())
        app.include_router(register.create_router())
        app.include_router(system.create_router(app_version))
    else:
        app.include_router(system.create_worker_public_router(app_version))

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def serve_web(full_path: str):
        asset = resolve_web_asset(full_path)
        if asset is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(asset)

    return app
