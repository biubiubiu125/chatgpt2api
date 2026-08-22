from __future__ import annotations

from typing import Any

from utils.helper import anonymize_token


_REFRESH_ERROR_DETAIL_FIELDS = (
    "failure_code",
    "failure_scope",
    "failure_capability",
    "failure_retryable",
    "failure_account_failure",
    "failure_retry_after",
    "status_code",
    "error_type",
)


def _safe_token_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("token:") else anonymize_token(text)


def refresh_error_entries(refresh_result: object) -> list[dict[str, Any]]:
    if not isinstance(refresh_result, dict):
        return []
    raw_errors = refresh_result.get("errors")
    if not isinstance(raw_errors, list):
        return []

    entries: list[dict[str, Any]] = []
    for item in raw_errors:
        entry: dict[str, Any] = {"name": "account_refresh"}
        if isinstance(item, dict):
            message = str(item.get("error") or item.get("message") or "account refresh failed").strip()
            entry["error"] = message or "account refresh failed"
            token = _safe_token_label(item.get("token"))
            if token:
                entry["token"] = token
            for key in _REFRESH_ERROR_DETAIL_FIELDS:
                if key in item:
                    entry[key] = item[key]
        else:
            entry["error"] = str(item or "account refresh failed").strip() or "account refresh failed"
        entries.append(entry)
    return entries
