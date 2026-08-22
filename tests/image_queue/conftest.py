from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import EnqueueRequest, ImageAccountCandidate


@pytest.fixture
def sqlite_queue_database() -> ImageQueueDatabase:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url="sqlite+pysqlite:///:memory:"),
        engine=engine,
        allow_non_postgres=True,
    )
    database.start()
    try:
        yield database
    finally:
        database.dispose()


@pytest.fixture
def repository(sqlite_queue_database: ImageQueueDatabase) -> ImageQueueRepository:
    return ImageQueueRepository(sqlite_queue_database)


@pytest.fixture
def generation_request() -> EnqueueRequest:
    return EnqueueRequest(
        owner_key="owner-1",
        idempotency_key="request-1",
        request_hash="a" * 64,
        task_type="generation",
        original_prompt="cat",
        effective_prompt="cat with detail",
        request_payload={"prompt": "cat", "model": "gpt-image-2", "n": 1},
        required_jobs=1,
        client_task_id="client-1",
    )


@pytest.fixture
def account_candidates() -> list[ImageAccountCandidate]:
    from uuid import UUID

    return [
        ImageAccountCandidate(
            account_id=UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token-1",
        ),
        ImageAccountCandidate(
            account_id=UUID("10000000-0000-0000-0000-000000000002"),
            access_token="token-2",
        ),
    ]
