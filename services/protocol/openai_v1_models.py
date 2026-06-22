from __future__ import annotations

import json
import re
import time
from typing import Any

from services.account_service import account_service
from services.openai_backend_api import OpenAIBackendAPI
from services.proxy_service import (
    is_cloudflare_upstream_error,
    is_proxy_transport_error,
    record_backend_proxy_result,
)
from utils.helper import CODEX_IMAGE_MODEL
from utils.log import logger


LOCAL_TEXT_MODELS = (
    "auto",
    "gpt-5",
    "gpt-5-1",
    "gpt-5-2",
    "gpt-5-3",
    "gpt-5-3-mini",
    "gpt-5-mini",
)
LOCAL_IMAGE_MODELS = ("gpt-image-2",)


def _model_item(model_id: str, *, created: int = 0, owned_by: str = "chatgpt2api") -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": created,
        "owned_by": owned_by,
        "permission": [],
        "root": model_id,
        "parent": None,
    }


def _dynamic_image_models() -> set[str]:
    try:
        accounts = account_service.list_accounts()
    except Exception:
        accounts = []

    dynamic_models: set[str] = set()
    web_image_accounts = [
        account
        for account in accounts
        if isinstance(account, dict)
    ]
    codex_types = {
        normalized
        for account in accounts
        if isinstance(account, dict)
        and account_service._normalize_source_type(account.get("source_type")) == "codex"
        and (normalized := account_service._normalize_account_type(account.get("type")))
    }

    if web_image_accounts:
        dynamic_models.add("gpt-image-2")
    if codex_types & {"Plus", "Team", "Pro"}:
        dynamic_models.add(CODEX_IMAGE_MODEL)
    if "Plus" in codex_types:
        dynamic_models.add(f"plus-{CODEX_IMAGE_MODEL}")
    if "Team" in codex_types:
        dynamic_models.add(f"team-{CODEX_IMAGE_MODEL}")
    if "Pro" in codex_types:
        dynamic_models.add(f"pro-{CODEX_IMAGE_MODEL}")
    return dynamic_models


def _dedupe_model_ids(model_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for model_id in model_ids:
        normalized = str(model_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def list_models() -> dict[str, Any]:
    model_ids = _dedupe_model_ids([*LOCAL_IMAGE_MODELS, *LOCAL_TEXT_MODELS, *sorted(_dynamic_image_models())])
    return {"object": "list", "data": [_model_item(model_id) for model_id in model_ids]}


def _error_text(exc: Exception, *, limit: int = 1000) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(body)
    elif body:
        text = str(body)
    else:
        text = str(exc) or exc.__class__.__name__
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _probe_error_message(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if is_cloudflare_upstream_error(_error_text(exc)):
        return "匿名模型探活失败：Cloudflare 返回异常，可能是代理、出口 IP 或上游风控问题"
    if is_proxy_transport_error(exc):
        return "匿名模型探活失败：网络或代理连接中断"
    if status_code:
        return f"匿名模型探活失败：HTTP {status_code}"
    return "匿名模型探活失败：上游连接异常"


def _probe_proxy_info(backend: OpenAIBackendAPI) -> dict[str, object]:
    profile = getattr(backend, "proxy_profile", None)
    proxy_url = str(getattr(profile, "proxy_url", "") or "")
    return {
        "proxy_source": str(getattr(profile, "proxy_source", "") or "direct"),
        "has_proxy": bool(proxy_url),
    }


def probe_upstream_models() -> dict[str, Any]:
    started = time.perf_counter()
    backend = OpenAIBackendAPI()
    try:
        result = backend.list_models()
        record_backend_proxy_result(backend, True)
    except Exception as exc:
        record_backend_proxy_result(backend, not is_proxy_transport_error(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        status_code = getattr(exc, "status_code", None)
        error = _probe_error_message(exc)
        logger.warning({
            "event": "upstream_models_probe_failed",
            "status_code": status_code,
            "latency_ms": latency_ms,
            "error": error,
            **_probe_proxy_info(backend),
        })
        return {
            "ok": False,
            "status": "error",
            "probe_scope": "anonymous_models",
            "probe_scope_label": "匿名模型列表",
            "covers_image_generation": False,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "model_count": 0,
            "models": [],
            "error": error,
            **_probe_proxy_info(backend),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    data = result.get("data") if isinstance(result, dict) else []
    models = [
        str(item.get("id") or "").strip()
        for item in data
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    return {
        "ok": True,
        "status": "ok",
        "probe_scope": "anonymous_models",
        "probe_scope_label": "匿名模型列表",
        "covers_image_generation": False,
        "status_code": 200,
        "latency_ms": latency_ms,
        "model_count": len(models),
        "models": models[:50],
        "error": None,
        **_probe_proxy_info(backend),
    }
