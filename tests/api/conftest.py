from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from api import image_tasks
from api.errors import install_exception_handlers
from services.image_queue.artifact_service import ArtifactService
from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings
from services.image_task_service import ImageTaskService


class StoppedWorker:
    def notify(self) -> None:
        return None

    def stop(self, timeout: float | None = None) -> None:
        return None


@pytest.fixture
def api_image_task_service(tmp_path: Path, monkeypatch) -> ImageTaskService:
    settings = ImageQueueSettings(
        database_url="sqlite+pysqlite:///:memory:",
        artifact_root=tmp_path / "images",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database = ImageQueueDatabase(settings, engine=engine, allow_non_postgres=True)
    database.start()
    service = ImageTaskService(
        settings=settings,
        database=database,
        repository=ImageQueueRepository(database),
        artifact_service=ArtifactService(settings.artifact_root),
        worker=StoppedWorker(),
    )
    monkeypatch.setattr(image_tasks, "image_task_service", service)
    monkeypatch.setattr(image_tasks, "require_identity", lambda authorization: {"id": "owner-1", "role": "user"})

    async def allow(call, text):
        return None

    monkeypatch.setattr(image_tasks, "filter_or_log", allow)
    try:
        yield service
    finally:
        service.stop(1)
        database.dispose()


@pytest.fixture
def image_queue_client(api_image_task_service: ImageTaskService) -> TestClient:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(image_tasks.create_router())
    return TestClient(app)
