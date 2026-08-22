from __future__ import annotations

import re
from collections.abc import Mapping

from services.image_failure import ImageFailure, public_image_error_message


_URL_RE = re.compile(r"(?i)\b(?:https?|socks5h?|socks5|postgres(?:ql)?(?:\+\w+)?)://[^\s\"'<>]+")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(^|[_-])("
    r"authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"access[-_]?token|refresh[-_]?token|id[-_]?token|api[-_]?key|"
    r"password|secret|token"
    r")($|[_-])"
)
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|access_token|"
    r"refresh_token|id[_-]?token|api[_-]?key|password|secret|token)\b(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
ALLOWED_IMAGE_TRACE_HEADERS = {
    "x-request-id",
    "x-newapi-request-id",
    "x-oneapi-request-id",
    "x-channel-id",
    "x-channel-name",
    "call_id",
}


def _safe_text(value: object, limit: int = 4000) -> str:
    text = str(value or "").strip()
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", text)
    return text[:limit]


def _is_sensitive_key(key: object) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "").strip()))


def sanitize_event_data(value: object, *, _depth: int = 0) -> object:
    if _depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            sanitized[key_text] = "[redacted]" if _is_sensitive_key(key_text) else sanitize_event_data(
                item,
                _depth=_depth + 1,
            )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_event_data(item, _depth=_depth + 1) for item in list(value)[:200]]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[bytes {len(bytes(value))}]"
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(value)


def sanitize_trace_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in headers.items():
        key_text = str(key or "").strip().lower()
        if key_text not in ALLOWED_IMAGE_TRACE_HEADERS:
            continue
        text = _safe_text(value, 160)
        if text:
            result[key_text] = text
    return result


def safe_queue_error_message(error: BaseException, failure: ImageFailure) -> str:
    message = public_image_error_message(failure, error).strip()
    if not message:
        message = "Image generation failed. Please try again."
    message = _URL_RE.sub("[url redacted]", message)
    message = _BEARER_RE.sub("Bearer [redacted]", message)
    message = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[redacted]", message)
    return message[:1000]
