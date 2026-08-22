from __future__ import annotations

import pytest

from services.image_queue.settings import ImageQueueConfigurationError, ImageQueueSettings


def test_production_queue_rejects_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "sqlite+pysqlite:///:memory:")

    with pytest.raises(ImageQueueConfigurationError, match="PostgreSQL"):
        ImageQueueSettings.from_env()


def test_database_url_prefers_dedicated_queue_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/db")
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/db")

    settings = ImageQueueSettings.from_env()

    assert settings.database_url == "postgresql://queue/db"


def test_missing_postgres_marks_queue_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("IMAGE_QUEUE_DATABASE_URL", raising=False)

    settings = ImageQueueSettings.from_env()

    assert settings.available is False
    assert settings.database_url == ""


def test_claim_max_runtime_is_loaded_with_safety_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS", "42")

    settings = ImageQueueSettings.from_env()

    assert settings.claim_max_runtime_seconds == 60
