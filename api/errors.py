from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.protocol.error_response import anthropic_error_response, openai_error_response
from services.image_queue.database import ImageQueueUnavailableError
from services.image_queue.resource_controller import ImageQueueResourcePressureError, ImageQueueStorageFullError


def _is_openai_compatible_path(path: str) -> bool:
    return path == "/v1" or path.startswith("/v1/")


def _is_anthropic_messages_path(path: str) -> bool:
    return path == "/v1/messages"


def _compatible_error_response(
    request: Request,
    detail: object,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    if _is_anthropic_messages_path(request.url.path):
        return anthropic_error_response(detail, status_code, headers=headers)
    return openai_error_response(detail, status_code, headers=headers)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ImageQueueStorageFullError)
    async def image_queue_storage_full_handler(request: Request, exc: ImageQueueStorageFullError) -> JSONResponse:
        detail = {"error": exc.code, "message": str(exc)}
        if _is_openai_compatible_path(request.url.path):
            return _compatible_error_response(request, detail, 507)
        return JSONResponse(status_code=507, content={"detail": detail})

    @app.exception_handler(ImageQueueResourcePressureError)
    async def image_queue_resource_pressure_handler(
        request: Request,
        exc: ImageQueueResourcePressureError,
    ) -> JSONResponse:
        detail = {"error": exc.code, "message": str(exc), "reason": exc.reason}
        if _is_openai_compatible_path(request.url.path):
            return _compatible_error_response(request, detail, 503)
        return JSONResponse(status_code=503, content={"detail": detail})

    @app.exception_handler(ImageQueueUnavailableError)
    async def image_queue_unavailable_handler(request: Request, exc: ImageQueueUnavailableError) -> JSONResponse:
        detail = {
            "error": "image_queue_unavailable",
            "message": str(exc),
        }
        if _is_openai_compatible_path(request.url.path):
            return _compatible_error_response(request, detail, 503)
        return JSONResponse(status_code=503, content={"detail": detail})

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if _is_openai_compatible_path(request.url.path):
            return _compatible_error_response(request, exc.detail, exc.status_code, exc.headers)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": jsonable_encoder(exc.detail)},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        if _is_openai_compatible_path(request.url.path):
            return _compatible_error_response(request, exc.errors(), 400)
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})
