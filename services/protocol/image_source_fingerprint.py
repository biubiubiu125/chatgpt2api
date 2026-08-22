from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping

from services.image_queue.idempotency import canonical_request_hash


def _clean(value: object) -> str:
    return str(value or "").strip()


def _text_marker(kind: str, value: object) -> dict[str, object] | None:
    text = _clean(value)
    if not text:
        return None
    return {
        "kind": kind,
        "sha256": sha256(text.encode("utf-8")).hexdigest(),
    }


def _bytes_marker(kind: str, value: bytes) -> dict[str, object]:
    return {
        "kind": kind,
        "sha256": sha256(value).hexdigest(),
        "byte_size": len(value),
    }


def _image_url_value(value: object) -> str:
    if isinstance(value, Mapping):
        return _clean(value.get("url") or value.get("image_url"))
    return _clean(value)


def image_part_source_marker(part: Mapping[str, Any]) -> dict[str, object] | None:
    data = part.get("data")
    if isinstance(data, (bytes, bytearray)):
        return _bytes_marker("bytes", bytes(data))

    for key in ("image_url", "url"):
        marker = _text_marker("image_url", _image_url_value(part.get(key)))
        if marker is not None:
            return marker

    encoded = part.get("b64_json") or part.get("base64")
    marker = _text_marker("base64", encoded)
    if marker is not None:
        return marker

    source = part.get("source")
    if isinstance(source, Mapping) and _clean(source.get("type")) == "base64":
        marker = _text_marker("base64", source.get("data"))
        if marker is not None:
            media_type = _clean(source.get("media_type") or source.get("mime_type"))
            if media_type:
                marker["media_type"] = media_type
            return marker

    return None


def image_source_markers_from_content(content: object) -> list[dict[str, object]]:
    if not isinstance(content, list):
        return []
    markers: list[dict[str, object]] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        item_type = _clean(item.get("type"))
        if item_type not in {"image_url", "input_image", "image"} and not (
            item.get("image_url") or item.get("url") or item.get("source")
        ):
            continue
        marker = image_part_source_marker(item)
        if marker is not None:
            markers.append(marker)
    return markers


def source_request_hash(fingerprint: Mapping[str, Any]) -> str:
    return canonical_request_hash(fingerprint)
