from __future__ import annotations

from typing import Any

from services.account_service import account_service
from services.model_catalog_service import get_model_catalog
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import is_non_public_image_model


UPSTREAM_MODELS_TIMEOUT_SECS = 2.0


def _model_item(model: str) -> dict[str, Any]:
    return {
        "id": model,
        "object": "model",
        "created": 0,
        "owned_by": "chatgpt2api",
        "permission": [],
        "root": model,
        "parent": None,
    }


def _append_model(data: list[Any], seen: set[str], model: object) -> None:
    model_id = str(model or "").strip()
    if not model_id or model_id in seen:
        return
    if is_non_public_image_model(model_id):
        return
    seen.add(model_id)
    data.append(_model_item(model_id))


def _append_models(data: list[Any], seen: set[str], models: object) -> None:
    if not isinstance(models, list):
        return
    for model in models:
        _append_model(data, seen, model)


def _append_upstream_models(
    data: list[Any],
    seen: set[str],
    *,
    timeout_secs: float = UPSTREAM_MODELS_TIMEOUT_SECS,
) -> None:
    try:
        with OpenAIBackendAPI() as backend:
            result = backend.list_models(timeout_secs=timeout_secs)
    except Exception:
        return
    upstream_data = result.get("data")
    if not isinstance(upstream_data, list):
        return
    for item in upstream_data:
        if not isinstance(item, dict):
            continue
        _append_model(data, seen, item.get("id"))


def _dynamic_image_models() -> list[str]:
    accounts = account_service.list_accounts()
    available_image_accounts = [
        account
        for account in accounts
        if isinstance(account, dict)
           and account_service._is_image_account_available(account)
    ]
    return ["gpt-image-2"] if available_image_accounts else []


def list_models(
    *,
    upstream_timeout_secs: float = UPSTREAM_MODELS_TIMEOUT_SECS,
) -> dict[str, Any]:
    catalog = get_model_catalog()
    data: list[Any] = []
    seen: set[str] = set()

    _append_models(data, seen, catalog.get("chat_models"))
    _append_upstream_models(data, seen, timeout_secs=upstream_timeout_secs)
    _append_models(data, seen, catalog.get("image_models"))
    _append_models(data, seen, _dynamic_image_models())

    return {"object": "list", "data": data}
