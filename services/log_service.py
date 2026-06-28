from __future__ import annotations

import hashlib
import json
import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from services.config import DATA_DIR
from services.protocol.error_response import anthropic_error_response, openai_error_response
from utils.helper import anthropic_sse_stream, sse_json_stream

LOG_TYPE_CALL = "call"
LOG_TYPE_ACCOUNT = "account"
INTERNAL_RESPONSE_KEYS = {
    "_account_email",
    "_account_emails",
    "_access_token",
    "_conversation_id",
    "_reference_image_count",
    "_tried_account_emails",
    "_tried_account_ids",
    "_fallback_count",
    "_fallback_index",
    "_fallback_counts_by_index",
    "_fallback_limit",
    "_fallback_reason",
    "_fallback_events",
}


def _exception_log_fields(exc: Exception) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            fields["status_code"] = int(status_code)
        except (TypeError, ValueError):
            fields["status_code"] = str(status_code)
    for attr, key in (("error_type", "error_type"), ("code", "error_code"), ("param", "error_param")):
        value = getattr(exc, attr, None)
        if value is not None and value != "":
            fields[key] = value
    fields = _merge_fallback_context(fields, _collect_fallback_context(exc))
    return fields


class LogService:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _legacy_id(raw_line: str, line_number: int) -> str:
        payload = f"{line_number}:{raw_line}".encode("utf-8", errors="ignore")
        return hashlib.sha1(payload).hexdigest()[:24]

    def _parse_line(self, raw_line: str, line_number: int) -> dict[str, Any] | None:
        try:
            item = json.loads(raw_line)
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        parsed = dict(item)
        parsed["id"] = str(parsed.get("id") or self._legacy_id(raw_line, line_number))
        return parsed

    @staticmethod
    def _serialize_item(item: dict[str, Any]) -> str:
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _matches_filters(item: dict[str, Any], *, type: str = "", start_date: str = "", end_date: str = "") -> bool:
        t = str(item.get("time") or "")
        day = t[:10]
        if type and item.get("type") != type:
            return False
        if start_date and day < start_date:
            return False
        if end_date and day > end_date:
            return False
        return True

    def add(self, type: str, summary: str = "", detail: dict[str, Any] | None = None, **data: Any) -> None:
        item = {
            "id": uuid4().hex,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": type,
            "summary": summary,
            "detail": detail or data,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(self._serialize_item(item) + "\n")

    def list(self, type: str = "", start_date: str = "", end_date: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
        items: list[dict[str, Any]] = []
        for line_number in range(len(lines) - 1, -1, -1):
            item = self._parse_line(lines[line_number], line_number)
            if item is None:
                continue
            if not self._matches_filters(item, type=type, start_date=start_date, end_date=end_date):
                continue
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def delete(self, ids: list[str]) -> dict[str, int]:
        target_ids = {str(item or "").strip() for item in ids if str(item or "").strip()}
        with self._lock:
            if not self.path.exists() or not target_ids:
                return {"removed": 0}
            lines = self.path.read_text(encoding="utf-8").splitlines()
            kept_lines: list[str] = []
            removed = 0
            for line_number, raw_line in enumerate(lines):
                item = self._parse_line(raw_line, line_number)
                if item is None:
                    kept_lines.append(raw_line)
                    continue
                if str(item.get("id") or "") in target_ids:
                    removed += 1
                    continue
                kept_lines.append(self._serialize_item(item))
            content = "\n".join(kept_lines)
            if content:
                content += "\n"
            self.path.write_text(content, encoding="utf-8")
            return {"removed": removed}


log_service = LogService(DATA_DIR / "logs.jsonl")


def _collect_urls(value: object) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" and isinstance(item, str):
                urls.append(item)
            elif key == "urls" and isinstance(item, list):
                urls.extend(str(url) for url in item if isinstance(url, str))
            else:
                urls.extend(_collect_urls(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_collect_urls(item))
    return urls


def _collect_account_emails(value: object) -> list[str]:
    emails: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"_account_email", "account_email"} and isinstance(item, str) and item.strip():
                emails.append(item.strip())
            elif key in {"_account_emails", "account_emails"} and isinstance(item, list):
                emails.extend(str(email).strip() for email in item if str(email or "").strip())
            else:
                emails.extend(_collect_account_emails(item))
    elif isinstance(value, list):
        for item in value:
            emails.extend(_collect_account_emails(item))
    return emails


def _dedupe_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    return _dedupe_strings([str(item or "") for item in values])


def _safe_nonnegative_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _safe_positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _fallback_events(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(value)]
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _merge_fallback_context(base: dict[str, Any], incoming: dict[str, Any] | None) -> dict[str, Any]:
    if not incoming:
        return base
    merged = dict(base)
    for key in ("tried_account_emails", "tried_account_ids"):
        values = _dedupe_strings([*_string_list(merged.get(key)), *_string_list(incoming.get(key))])
        if values:
            merged[key] = values
    incoming_counts = incoming.get("_fallback_counts_by_index")
    if isinstance(incoming_counts, dict):
        merged_counts = dict(merged.get("_fallback_counts_by_index") or {})
        for raw_index, raw_count in incoming_counts.items():
            index = _safe_positive_int(raw_index)
            count = _safe_nonnegative_int(raw_count)
            if index is None or count is None:
                continue
            key = str(index)
            merged_counts[key] = max(_safe_nonnegative_int(merged_counts.get(key)) or 0, count)
        if merged_counts:
            existing_count = _safe_nonnegative_int(merged.get("fallback_count")) or 0
            indexed_count = max(_safe_nonnegative_int(value) or 0 for value in merged_counts.values())
            merged["_fallback_counts_by_index"] = merged_counts
            merged["fallback_count"] = max(existing_count, indexed_count)
    for key in ("fallback_count", "fallback_limit"):
        incoming_value = _safe_nonnegative_int(incoming.get(key))
        if incoming_value is None:
            continue
        existing_value = _safe_nonnegative_int(merged.get(key))
        if key == "fallback_count" and merged.get("_fallback_counts_by_index"):
            merged[key] = max(existing_value or 0, incoming_value)
        else:
            merged[key] = max(existing_value or 0, incoming_value)
    reason = str(incoming.get("fallback_reason") or "").strip()
    if reason and not str(merged.get("fallback_reason") or "").strip():
        merged["fallback_reason"] = reason
    events = [*_fallback_events(merged.get("fallback_events")), *_fallback_events(incoming.get("fallback_events"))]
    if events:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for event in events:
            marker = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(event)
        merged["fallback_events"] = deduped
    return merged


def _finalize_fallback_context(context: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(context)
    existing_count = _safe_nonnegative_int(finalized.get("fallback_count"))
    counts = finalized.pop("_fallback_counts_by_index", None)
    if isinstance(counts, dict) and counts:
        indexed_count = max(_safe_nonnegative_int(value) or 0 for value in counts.values())
        finalized["fallback_count"] = max(existing_count or 0, indexed_count)
    elif existing_count is not None:
        finalized["fallback_count"] = existing_count
    finalized.pop("_fallback_index", None)
    return finalized


def _collect_fallback_context(value: object) -> dict[str, Any]:
    context: dict[str, Any] = {}

    def merge_value(key: str, item: object) -> None:
        nonlocal context
        if key in {"_tried_account_emails", "tried_account_emails"}:
            context = _merge_fallback_context(context, {"tried_account_emails": _string_list(item)})
            return
        if key in {"_tried_account_ids", "tried_account_ids"}:
            context = _merge_fallback_context(context, {"tried_account_ids": _string_list(item)})
            return
        if key in {"_fallback_count", "fallback_count"}:
            context = _merge_fallback_context(context, {"fallback_count": _safe_nonnegative_int(item)})
            return
        if key == "_fallback_counts_by_index" and isinstance(item, dict):
            context = _merge_fallback_context(context, {"_fallback_counts_by_index": item})
            return
        if key in {"_fallback_limit", "fallback_limit"}:
            context = _merge_fallback_context(context, {"fallback_limit": _safe_nonnegative_int(item)})
            return
        if key in {"_fallback_reason", "fallback_reason"}:
            context = _merge_fallback_context(context, {"fallback_reason": str(item or "").strip()})
            return
        if key in {"_fallback_events", "fallback_events"}:
            context = _merge_fallback_context(context, {"fallback_events": _fallback_events(item)})

    def visit(item: object) -> None:
        if isinstance(item, dict):
            fallback_count = _safe_nonnegative_int(item.get("_fallback_count", item.get("fallback_count")))
            fallback_index = _safe_positive_int(item.get("_fallback_index", item.get("index")))
            if fallback_count is not None and fallback_index is not None:
                merge_value("_fallback_counts_by_index", {str(fallback_index): fallback_count})
            for key, nested in item.items():
                if key in {
                    "_tried_account_emails",
                    "tried_account_emails",
                    "_tried_account_ids",
                    "tried_account_ids",
                    "_fallback_count",
                    "_fallback_index",
                    "_fallback_counts_by_index",
                    "fallback_count",
                    "_fallback_limit",
                    "fallback_limit",
                    "_fallback_reason",
                    "fallback_reason",
                    "_fallback_events",
                    "fallback_events",
                }:
                    merge_value(key, nested)
                else:
                    visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
            return
        for attr in (
            "tried_account_emails",
            "tried_account_ids",
            "fallback_count",
            "fallback_index",
            "fallback_limit",
            "fallback_reason",
            "fallback_events",
        ):
            if hasattr(item, attr):
                merge_value(attr, getattr(item, attr, None))

    visit(value)
    return context


def _collect_conversation_ids(value: object) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "_conversation_id" and isinstance(item, str) and item.strip():
                ids.append(item.strip())
            else:
                ids.extend(_collect_conversation_ids(item))
    elif isinstance(value, list):
        for item in value:
            ids.extend(_collect_conversation_ids(item))
    return ids


def _collect_reference_image_counts(value: object) -> list[int]:
    counts: list[int] = []
    if isinstance(value, dict):
        raw = value.get("_reference_image_count")
        if raw is not None:
            try:
                counts.append(max(0, int(raw)))
            except (TypeError, ValueError):
                pass
        for item in value.values():
            counts.extend(_collect_reference_image_counts(item))
    elif isinstance(value, list):
        for item in value:
            counts.extend(_collect_reference_image_counts(item))
    return counts


def _strip_internal_response_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _strip_internal_response_fields(item)
            for key, item in value.items()
            if key not in INTERNAL_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_response_fields(item) for item in value]
    return value


def _request_excerpt(text: object, limit: int = 1000) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _image_error_response(exc: Exception) -> JSONResponse:
    from services.protocol.conversation import public_image_error_message

    message = public_image_error_message(str(exc))
    if "no available image quota" in message.lower():
        return openai_error_response(
            {
                "error": {
                    "message": "no available image quota",
                    "type": "insufficient_quota",
                    "param": None,
                    "code": "insufficient_quota",
                }
            },
            429,
        )
    if hasattr(exc, "to_openai_error") and hasattr(exc, "status_code"):
        return JSONResponse(status_code=int(exc.status_code), content=exc.to_openai_error())
    return openai_error_response(message, 502)


def _protocol_error_response(exc: Exception, status_code: int, sse: str) -> JSONResponse:
    message = str(exc)
    if sse == "anthropic":
        return anthropic_error_response(message, status_code)
    return openai_error_response(message, status_code)


def _next_item(items):
    try:
        return True, next(items)
    except StopIteration:
        return False, None


@dataclass
class LoggedCall:
    identity: dict[str, object]
    endpoint: str
    model: str
    summary: str
    started: float = field(default_factory=time.time)
    request_text: str = ""
    request_shape: dict[str, int] | None = None

    async def run(self, handler, *args, sse: str = "openai"):
        from services.protocol.conversation import ImageGenerationError

        try:
            result = await run_in_threadpool(handler, *args)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     account_emails=getattr(exc, "account_emails", []),
                     conversation_id=getattr(exc, "conversation_id", ""),
                     extra=_exception_log_fields(exc))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     account_emails=getattr(exc, "account_emails", []))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)

        if isinstance(result, dict):
            self.log("调用完成", result)
            return _strip_internal_response_fields(dict(result))

        sender = anthropic_sse_stream if sse == "anthropic" else sse_json_stream
        try:
            has_first, first = await run_in_threadpool(_next_item, result)
        except ImageGenerationError as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     account_emails=getattr(exc, "account_emails", []),
                     conversation_id=getattr(exc, "conversation_id", ""),
                     extra=_exception_log_fields(exc))
            return _image_error_response(exc)
        except HTTPException as exc:
            self.log("调用失败", status="failed", error=str(exc.detail))
            raise
        except Exception as exc:
            self.log("调用失败", status="failed", error=str(exc), account_email=getattr(exc, "account_email", ""),
                     account_emails=getattr(exc, "account_emails", []))
            if self.endpoint.startswith("/v1/images"):
                return _image_error_response(exc)
            return _protocol_error_response(exc, 502, sse)
        if not has_first:
            self.log("流式调用结束")
            return StreamingResponse(sender(()), media_type="text/event-stream")
        return StreamingResponse(sender(self.stream(itertools.chain([first], result))), media_type="text/event-stream")

    def stream(self, items):
        urls: list[str] = []
        account_emails: list[str] = []
        conversation_ids: list[str] = []
        reference_counts: list[int] = []
        fallback_context: dict[str, Any] = {}
        failed = False
        try:
            for item in items:
                urls.extend(_collect_urls(item))
                account_emails.extend(_collect_account_emails(item))
                conversation_ids.extend(_collect_conversation_ids(item))
                reference_counts.extend(_collect_reference_image_counts(item))
                fallback_context = _merge_fallback_context(fallback_context, _collect_fallback_context(item))
                yield _strip_internal_response_fields(item)
        except Exception as exc:
            failed = True
            extra = _finalize_fallback_context(_merge_fallback_context(dict(fallback_context), _exception_log_fields(exc)))
            if reference_counts:
                extra["reference_image_count"] = max(reference_counts)
            self.log(
                "流式调用失败",
                status="failed",
                error=str(exc),
                urls=urls,
                account_email=(account_emails[0] if account_emails else getattr(exc, "account_email", "")),
                account_emails=[*account_emails, *list(getattr(exc, "account_emails", []) or [])],
                conversation_id=(conversation_ids[0] if conversation_ids else getattr(exc, "conversation_id", "")),
                extra=extra,
            )
            if self.endpoint.startswith("/v1/images") and not hasattr(exc, "to_openai_error"):
                from services.protocol.conversation import ImageGenerationError, public_image_error_message

                raise ImageGenerationError(public_image_error_message(str(exc))) from exc
            raise
        finally:
            if not failed:
                extra = _finalize_fallback_context(fallback_context)
                if reference_counts:
                    extra["reference_image_count"] = max(reference_counts)
                self.log("流式调用结束", urls=urls, account_email=account_emails[0] if account_emails else "",
                         account_emails=account_emails,
                         conversation_id=conversation_ids[0] if conversation_ids else "",
                         extra=extra or None)

    def log(self, suffix: str, result: object = None, status: str = "success", error: str = "",
            urls: list[str] | None = None, account_email: str = "", account_emails: list[str] | None = None,
            conversation_id: str = "", extra: dict[str, Any] | None = None) -> None:
        detail = {
            "key_id": self.identity.get("id"),
            "key_name": self.identity.get("name"),
            "role": self.identity.get("role"),
            "endpoint": self.endpoint,
            "model": self.model,
            "started_at": datetime.fromtimestamp(self.started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_ms": int((time.time() - self.started) * 1000),
            "status": status,
        }
        request_excerpt = _request_excerpt(self.request_text)
        if request_excerpt:
            detail["request_text"] = request_excerpt
        if self.request_shape:
            detail["request_shape"] = self.request_shape
        if error:
            detail["error"] = error
        if extra:
            detail.update(extra)
        emails = _dedupe_strings([*(account_emails or []), *_collect_account_emails(result)])
        email = str(account_email or "").strip() or (emails[0] if emails else "")
        if email:
            detail["account_email"] = email
        if emails:
            detail["account_emails"] = emails
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            conv_ids = _collect_conversation_ids(result)
            conv_id = conv_ids[0] if conv_ids else ""
        if conv_id:
            detail["conversation_id"] = conv_id
        collected_urls = [] if self.endpoint.startswith("/v1/images") else [*(urls or []), *_collect_urls(result)]
        if collected_urls and not self.endpoint.startswith("/v1/search"):
            detail["urls"] = list(dict.fromkeys(collected_urls))
        reference_counts = _collect_reference_image_counts(result)
        if reference_counts:
            detail["reference_image_count"] = max(reference_counts)
        detail = _merge_fallback_context(detail, _collect_fallback_context(result))
        detail = _finalize_fallback_context(detail)
        log_service.add(LOG_TYPE_CALL, f"{self.summary}{suffix}", detail)
