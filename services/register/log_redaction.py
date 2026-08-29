from __future__ import annotations

import copy
import re
from typing import Any


_QUERY_SECRET_RE = re.compile(r"([?&]key=)([^&#\s'\"<>]+)", re.IGNORECASE)
_BEARER_SECRET_RE = re.compile(r"(\bAuthorization\s*:\s*Bearer\s+)([^,\s;}\]]+)", re.IGNORECASE)
_CREDENTIAL_FIELD_RE = re.compile(
    r"(\b(?:password|access_token|refresh_token|id_token|api_key|claim_token)\b\s*\"?\s*[:=]\s*)"
    r"(?:\"([^\"]*)\"|([^,\s;}\]]+))",
    re.IGNORECASE,
)


def redact_register_log_text(text: object) -> str:
    value = str(text or "")
    if not value:
        return ""
    value = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}***", value)
    value = _BEARER_SECRET_RE.sub(lambda match: f"{match.group(1)}***", value)
    value = _CREDENTIAL_FIELD_RE.sub(
        lambda match: f'{match.group(1)}"{ "***" }"' if match.group(2) is not None else f"{match.group(1)}***",
        value,
    )
    return value


def redact_register_snapshot_inplace(snapshot: Any) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot
    logs = snapshot.get("logs")
    if not isinstance(logs, list):
        return snapshot
    for index, entry in enumerate(logs):
        if isinstance(entry, dict):
            entry["text"] = redact_register_log_text(entry.get("text"))
        elif isinstance(entry, str):
            logs[index] = redact_register_log_text(entry)
    return snapshot


def redact_register_snapshot(snapshot: Any) -> Any:
    return redact_register_snapshot_inplace(copy.deepcopy(snapshot))
