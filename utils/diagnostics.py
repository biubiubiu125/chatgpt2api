from __future__ import annotations

import re
from typing import Any


EXCEPTION_DIAGNOSTIC_ATTRS: tuple[tuple[str, str], ...] = (
    ("code", "error_code"),
    ("raw_error", "raw_error"),
    ("upstream_error", "upstream_error"),
    ("upstream_error_type", "upstream_error_type"),
    ("upstream_request_id", "upstream_request_id"),
    ("can_resume_poll", "can_resume_poll"),
    ("raw_upstream_message", "raw_upstream_message"),
    ("raw_upstream_message_len", "raw_upstream_message_len"),
    ("raw_upstream_message_truncated", "raw_upstream_message_truncated"),
    ("upstream_message_preview", "upstream_message_preview"),
    ("upstream_message_len", "upstream_message_len"),
    ("upstream_message_truncated", "upstream_message_truncated"),
    ("tool_invoked", "tool_invoked"),
    ("terminal_message", "terminal_message"),
    ("blocked", "blocked"),
    ("poll_attempts", "poll_attempts"),
    ("poll_timeout_secs", "poll_timeout_secs"),
    ("stream_timeout_secs", "stream_timeout_secs"),
    ("stream_timeout_followup", "stream_timeout_followup"),
    ("last_task_error", "last_task_error"),
    ("last_conversation_snapshot", "last_conversation_snapshot"),
    ("image_attempts", "image_attempts"),
)


def diagnostic_excerpt(value: object, limit: int = 1000) -> str:
    """Return a bounded diagnostic string for logs and upstream error details."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + "...[truncated]"


REDACTED_DIAGNOSTIC_VALUE = "***redacted***"
SENSITIVE_DIAGNOSTIC_KEY_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "refresh",
    "credential",
    "session",
)
INLINE_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(Bearer)\s+([A-Za-z0-9._~+/=-]{6,})"),
    re.compile(
        r"(?i)\b(authorization|cookie|access[_-]?token|refresh[_-]?token|api[_-]?key|password|secret|token)\s*[:=]\s*([^,;\s}\]]+)"
    ),
)


def _is_sensitive_diagnostic_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_DIAGNOSTIC_KEY_PARTS)


def redact_inline_secrets(text: object) -> str:
    value = str(text or "")
    for pattern in INLINE_SECRET_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1)} {REDACTED_DIAGNOSTIC_VALUE}", value)
    return value


def sanitize_diagnostic_value(
    value: object,
    *,
    string_limit: int = 1000,
    max_depth: int = 6,
) -> Any:
    if max_depth <= 0:
        return diagnostic_excerpt(redact_inline_secrets(value), string_limit)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            sanitized[key_text] = (
                REDACTED_DIAGNOSTIC_VALUE
                if _is_sensitive_diagnostic_key(key_text)
                else sanitize_diagnostic_value(
                    item,
                    string_limit=string_limit,
                    max_depth=max_depth - 1,
                )
            )
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_diagnostic_value(
                item,
                string_limit=string_limit,
                max_depth=max_depth - 1,
            )
            for item in value
        ]
    if isinstance(value, str):
        return diagnostic_excerpt(redact_inline_secrets(value), string_limit)
    return value


def exception_diagnostic_fields(
    exc: Exception,
    *,
    include_status_code: bool = False,
    string_limit: int = 4000,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    attrs = EXCEPTION_DIAGNOSTIC_ATTRS
    if include_status_code:
        attrs = (("status_code", "status_code"), *attrs)
    for attr, key in attrs:
        if not hasattr(exc, attr):
            continue
        value = getattr(exc, attr)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            value = diagnostic_excerpt(value, string_limit)
        fields[key] = value
    followup = fields.get("stream_timeout_followup")
    if isinstance(followup, dict) and "diagnosis" not in fields:
        fields["diagnosis"] = followup
    return fields
