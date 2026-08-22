from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from services.image_queue.artifact_service import ArtifactService
from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings
from services.image_task_service import ImageTaskService


class StoppedWorker:
    def __init__(self) -> None:
        self.execution_count = 0
        self.start_count = 0
        self.notify_count = 0

    def start(self) -> None:
        self.start_count += 1

    def stop(self, timeout: float | None = None) -> None:
        return None

    def notify(self) -> None:
        self.notify_count += 1


@pytest.fixture
def stopped_worker() -> StoppedWorker:
    return StoppedWorker()


@pytest.fixture
def image_task_service(tmp_path: Path, stopped_worker: StoppedWorker) -> ImageTaskService:
    settings = ImageQueueSettings(
        database_url="sqlite+pysqlite:///:memory:",
        result_wait_poll_seconds=0.01,
        artifact_root=tmp_path / "images",
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database = ImageQueueDatabase(
        settings,
        engine=engine,
        allow_non_postgres=True,
    )
    database.start()
    service = ImageTaskService(
        settings=settings,
        database=database,
        repository=ImageQueueRepository(database),
        artifact_service=ArtifactService(settings.artifact_root),
        worker=stopped_worker,
    )
    try:
        yield service
    finally:
        service.stop(1)
        database.dispose()
