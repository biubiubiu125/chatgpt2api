from __future__ import annotations

from services.protocol import openai_v1_models
from services.protocol.web_search_tool import has_web_search_tool, is_web_search_chat_request
from utils.helper import (
    IMAGE_MODELS,
    PUBLIC_IMAGE_MODELS,
    has_response_image_generation_tool,
    is_image_chat_request,
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
    monkeypatch.setattr(openai_v1_models, "_append_upstream_models", lambda data, seen, **kwargs: None)
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


def test_models_endpoint_caps_optional_upstream_lookup_timeout(monkeypatch) -> None:
    captured_timeouts: list[float] = []
    monkeypatch.setattr(
        openai_v1_models,
        "get_model_catalog",
        lambda: {
            "chat_models": ["gpt-5"],
            "image_models": ["gpt-image-2"],
        },
    )
    monkeypatch.setattr(openai_v1_models, "_dynamic_image_models", lambda: [])

    class SlowBackend:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def list_models(self, *, timeout_secs: float = 30.0):
            captured_timeouts.append(timeout_secs)
            raise TimeoutError("upstream models endpoint is slow")

    monkeypatch.setattr(openai_v1_models, "OpenAIBackendAPI", lambda: SlowBackend())

    result = openai_v1_models.list_models()
    model_ids = [item["id"] for item in result["data"]]

    assert captured_timeouts == [openai_v1_models.UPSTREAM_MODELS_TIMEOUT_SECS]
    assert model_ids == ["gpt-5", "gpt-image-2"]



def test_responses_tool_choice_none_does_not_route_to_durable_image() -> None:
    body = {
        "model": "gpt-5.5",
        "input": "hello",
        "tools": [{"type": "image_generation"}],
        "tool_choice": "none",
    }

    assert has_response_image_generation_tool(body) is False


def test_responses_explicit_image_tool_choice_routes_to_durable_image() -> None:
    body = {
        "model": "gpt-5.5",
        "input": "draw a cat",
        "tools": [{"type": "image_generation"}],
        "tool_choice": {"type": "image_generation"},
    }

    assert has_response_image_generation_tool(body) is True


def test_chat_modalities_image_does_not_route_text_models_to_durable_image() -> None:
    body = {
        "model": "gpt-4o",
        "modalities": ["text", "image"],
        "messages": [{"role": "user", "content": "hello"}],
    }

    assert is_image_chat_request(body) is False


def test_chat_supported_image_model_still_routes_to_durable_image() -> None:
    body = {
        "model": "gpt-image-2",
        "messages": [{"role": "user", "content": "draw a cat"}],
    }

    assert is_image_chat_request(body) is True


def test_chat_web_search_tool_choice_none_does_not_route_to_search() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "web_search_preview"}],
        "tool_choice": "none",
    }

    assert has_web_search_tool(body) is False
    assert is_web_search_chat_request(body) is False


def test_chat_explicit_web_search_tool_choice_routes_to_search() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "web_search_preview"}],
        "tool_choice": {"type": "web_search_preview"},
    }

    assert has_web_search_tool(body) is True
    assert is_web_search_chat_request(body) is True



def test_responses_explicit_non_image_tool_choice_does_not_route_to_durable_image() -> None:
    body = {
        "model": "gpt-5.5",
        "input": "latest news",
        "tools": [{"type": "image_generation"}, {"type": "web_search_preview"}],
        "tool_choice": {"type": "web_search_preview"},
    }

    assert has_response_image_generation_tool(body) is False


def test_chat_explicit_non_web_tool_choice_does_not_route_to_search() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "web_search_preview"}, {"type": "function", "function": {"name": "noop"}}],
        "tool_choice": {"type": "function", "function": {"name": "noop"}},
    }

    assert has_web_search_tool(body) is False
    assert is_web_search_chat_request(body) is False
