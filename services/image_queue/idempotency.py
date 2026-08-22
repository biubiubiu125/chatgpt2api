from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from services.image_queue.settings import ImageQueueSettings


PUBLIC_IMAGE_MODEL = "gpt-image-2"
PUBLIC_IMAGE_MODELS = {PUBLIC_IMAGE_MODEL}
PROMPT_SUFFIX_VERSION = "v1"
IDEMPOTENCY_KEY_MAX_LENGTH = 200


class UnsupportedImageModel(ValueError):
    code = "unsupported_model"

_HASH_EXCLUDED_KEYS = {
    "authorization",
    "cookie",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "base_url",
    "client_task_id",
    "idempotency_key",
    "progress_callback",
    "_call_id",
    "_trace_image_perf",
    "_image_result_callback",
    "_image_task_context",
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _clean_replay_key(value: object, label: str) -> str:
    cleaned = _clean(value)
    if len(cleaned) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError(f"{label} must be at most {IDEMPOTENCY_KEY_MAX_LENGTH} characters")
    return cleaned


def select_idempotency_key(headers: Mapping[str, object], client_task_id: str = "") -> str:
    normalized = {str(key).strip().lower(): value for key, value in headers.items()}
    for name, label in (
        ("idempotency-key", "Idempotency-Key"),
        ("x-newapi-request-id", "X-NewAPI-Request-Id"),
        ("x-oneapi-request-id", "X-OneAPI-Request-Id"),
    ):
        value = _clean_replay_key(normalized.get(name), label)
        if value:
            return value
    return _clean_replay_key(client_task_id, "client_task_id")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.strip().lower() in _HASH_EXCLUDED_KEYS:
                continue
            normalized[key] = _canonical_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized_items = [_canonical_value(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    if isinstance(value, (bytes, bytearray, memoryview)):
        payload = bytes(value)
        return {"$bytes_sha256": hashlib.sha256(payload).hexdigest(), "$bytes_length": len(payload)}
    if isinstance(value, (Path, UUID)):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def canonical_request_hash(payload: Mapping[str, Any]) -> str:
    canonical = _canonical_value(payload)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_effective_prompt(prompt: str, settings: ImageQueueSettings) -> tuple[str, str | None]:
    original = str(prompt or "").strip()
    suffix = str(settings.prompt_suffix or "").strip()
    if not settings.prompt_suffix_enabled or not suffix:
        return original, None
    if original.endswith(suffix):
        return original, PROMPT_SUFFIX_VERSION
    return f"{original}\n\n{suffix}" if original else suffix, PROMPT_SUFFIX_VERSION


def require_public_image_model(model: object) -> str:
    normalized = str(model or PUBLIC_IMAGE_MODEL).strip().lower() or PUBLIC_IMAGE_MODEL
    if normalized not in PUBLIC_IMAGE_MODELS:
        message = f"unsupported image model; only {PUBLIC_IMAGE_MODEL} is available"
        raise UnsupportedImageModel(message)
    return PUBLIC_IMAGE_MODEL
