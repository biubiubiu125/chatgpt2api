from __future__ import annotations

from services.protocol import openai_v1_models
from utils.helper import (
    IMAGE_MODELS,
    PUBLIC_IMAGE_MODELS,
    is_supported_image_model,
)


def test_internal_image_aliases_remain_supported_but_not_public() -> None:
    assert "codex-gpt-image-2" in IMAGE_MODELS
    assert is_supported_image_model("plus-codex-gpt-image-2") is True
    assert PUBLIC_IMAGE_MODELS == {"gpt-image-2"}


def test_models_endpoint_filters_internal_image_aliases(monkeypatch) -> None:
    monkeypatch.setattr(
        openai_v1_models,
        "get_model_catalog",
        lambda: {
            "chat_models": ["gpt-5"],
            "image_models": ["gpt-image-2", "codex-gpt-image-2", "plus-codex-gpt-image-2"],
        },
    )
    monkeypatch.setattr(openai_v1_models, "_append_upstream_models", lambda data, seen: None)
    monkeypatch.setattr(
        openai_v1_models,
        "_dynamic_image_models",
        lambda: ["gpt-image-2", "codex-gpt-image-2"],
    )

    result = openai_v1_models.list_models()
    model_ids = [item["id"] for item in result["data"]]

    assert "gpt-5" in model_ids
    assert "gpt-image-2" in model_ids
    assert "codex-gpt-image-2" not in model_ids
    assert "plus-codex-gpt-image-2" not in model_ids
