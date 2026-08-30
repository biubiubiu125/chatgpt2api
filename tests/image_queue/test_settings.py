from __future__ import annotations

import pytest

import services.image_queue.settings as settings_module
from services.image_queue.settings import ImageQueueConfigurationError, ImageQueueSettings


def test_production_queue_rejects_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "sqlite+pysqlite:///:memory:")

    with pytest.raises(ImageQueueConfigurationError, match="PostgreSQL"):
        ImageQueueSettings.from_env()


def test_database_url_prefers_dedicated_queue_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fallback/chatgpt2api_image_queue")
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/chatgpt2api_image_queue")

    settings = ImageQueueSettings.from_env()

    assert settings.database_url == "postgresql://queue/chatgpt2api_image_queue"


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


def test_auto_concurrency_defaults_scale_with_large_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/chatgpt2api_image_queue")
    monkeypatch.delenv("IMAGE_QUEUE_GENERATION_CONCURRENCY", raising=False)
    monkeypatch.delenv("IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP", raising=False)
    monkeypatch.delenv("IMAGE_QUEUE_ABSOLUTE_GUARD", raising=False)
    monkeypatch.setattr(settings_module, "_detected_cpu_cores", lambda: 64, raising=False)
    monkeypatch.setattr(
        settings_module,
        "_detected_available_memory_bytes",
        lambda: 256 * 1024**3,
        raising=False,
    )
    monkeypatch.setattr(
        settings_module.config,
        "get_runtime_capacity_settings",
        lambda: {"image_concurrency_limit": 2000},
    )

    settings = ImageQueueSettings.from_env()

    assert settings.generation_concurrency_hard_cap == 99999
    assert settings.generation_concurrency_limit == 128
    assert settings.absolute_guard == 276


def test_concurrency_hard_limits_clamp_explicit_oversized_values() -> None:
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        generation_concurrency_hard_cap=200000,
        generation_concurrency_limit=200000,
        absolute_guard=200000,
    )

    assert settings.generation_concurrency_hard_cap == 99999
    assert settings.generation_concurrency_limit == 99999
    assert settings.absolute_guard == 99999


def test_concurrency_overrides_inside_hard_limits_are_preserved() -> None:
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        generation_concurrency_hard_cap=512,
        generation_concurrency_limit=96,
        absolute_guard=160,
    )

    assert settings.generation_concurrency_hard_cap == 512
    assert settings.generation_concurrency_limit == 96
    assert settings.absolute_guard == 160


def test_prefixed_image_queue_env_values_are_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/chatgpt2api_image_queue")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_INSTANCE_ID", "prefixed-instance")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL", "false")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS", "5")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES", "1234")

    settings = ImageQueueSettings.from_env()

    assert settings.instance_id == "prefixed-instance"
    assert settings.verify_returned_url is False
    assert settings.returned_url_verify_timeout_seconds == 7.0
    assert settings.returned_url_verify_attempts == 5
    assert settings.returned_url_verify_max_bytes == 1234


def test_prefixed_image_queue_env_enabled_disabled_words_are_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMAGE_QUEUE_DATABASE_URL", "postgresql://queue/chatgpt2api_image_queue")
    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL", "enabled")

    enabled_settings = ImageQueueSettings.from_env()

    assert enabled_settings.verify_returned_url is True

    monkeypatch.setenv("CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL", "disabled")

    disabled_settings = ImageQueueSettings.from_env()

    assert disabled_settings.verify_returned_url is False
