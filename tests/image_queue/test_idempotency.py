from __future__ import annotations

from services.image_queue.idempotency import (
    build_effective_prompt,
    canonical_request_hash,
    require_public_image_model,
    select_idempotency_key,
)
from services.image_queue.settings import ImageQueueSettings


def test_idempotency_header_wins_over_newapi_and_client_task_id() -> None:
    headers = {
        "Idempotency-Key": " primary-42 ",
        "X-NewAPI-Request-Id": "newapi-42",
    }

    assert select_idempotency_key(headers, "client-42") == "primary-42"


def test_newapi_request_id_is_used_when_idempotency_header_missing() -> None:
    headers = {"X-NewAPI-Request-Id": " newapi-42 "}

    assert select_idempotency_key(headers, "client-42") == "newapi-42"


def test_client_task_id_is_last_idempotency_fallback() -> None:
    assert select_idempotency_key({}, " client-42 ") == "client-42"


def test_request_hash_ignores_trace_and_base_url_but_not_prompt() -> None:
    first = canonical_request_hash({"prompt": "cat", "base_url": "https://a", "_call_id": "1"})
    second = canonical_request_hash({"prompt": "cat", "base_url": "https://b", "_call_id": "2"})
    third = canonical_request_hash({"prompt": "dog", "base_url": "https://a", "_call_id": "1"})

    assert first == second
    assert first != third


def test_request_hash_uses_image_bytes_without_storing_them() -> None:
    first = canonical_request_hash({"images": [b"first-image"]})
    same = canonical_request_hash({"images": [b"first-image"]})
    changed = canonical_request_hash({"images": [b"second-image"]})

    assert first == same
    assert first != changed


def test_prompt_suffix_is_appended_once() -> None:
    settings = ImageQueueSettings(database_url="postgresql://queue/db")

    effective, version = build_effective_prompt("画一只猫", settings)
    repeated, repeated_version = build_effective_prompt(effective, settings)

    assert effective.endswith(settings.prompt_suffix)
    assert repeated == effective
    assert version == repeated_version == "v1"


def test_disabled_prompt_suffix_preserves_original_prompt() -> None:
    settings = ImageQueueSettings(
        database_url="postgresql://queue/db",
        prompt_suffix_enabled=False,
    )

    effective, version = build_effective_prompt(" 画一只猫 ", settings)

    assert effective == "画一只猫"
    assert version is None


def test_external_image_model_rejects_codex_alias() -> None:
    try:
        require_public_image_model("codex-gpt-image-2")
    except ValueError as exc:
        assert "gpt-image-2" in str(exc)
    else:
        raise AssertionError("internal image model was accepted as a public model")
