from __future__ import annotations

from contextlib import nullcontext
import queue
from types import SimpleNamespace

import pytest

from services.protocol import conversation
from services.protocol.conversation import (
    ConversationRequest,
    generate_single_image_for_job,
)
from utils.helper import iter_sse_payloads


class FakeProfile:
    image_concurrency_limit = 0


class FakeBackend:
    instances: list["FakeBackend"] = []

    def __init__(self, access_token: str, **kwargs: object) -> None:
        self.access_token = access_token
        self.proxy_profile = FakeProfile()
        self.progress_callback = None
        self.deleted: list[str] = []
        self.closed = False
        self.instances.append(self)

    def resolve_conversation_image_urls(self, *args: object, **kwargs: object) -> list[str]:
        return ["https://images.example/result.png"]

    def download_image_bytes(self, image_urls: list[str]) -> list[bytes]:
        return [b"image-bytes"]

    def delete_conversation(self, conversation_id: str) -> None:
        self.deleted.append(conversation_id)

    def close(self) -> None:
        self.closed = True


def _install_harness(monkeypatch, order: list[str]) -> None:
    FakeBackend.instances.clear()
    monkeypatch.setattr(conversation, "OpenAIBackendAPI", FakeBackend)
    monkeypatch.setattr(conversation.proxy_settings, "acquire_image_egress", lambda profile: 0)
    monkeypatch.setattr(conversation.proxy_settings, "release_image_egress", lambda profile: None)
    monkeypatch.setattr(conversation.proxy_settings, "get_fallback_proxy_reference", lambda: "")
    monkeypatch.setattr(conversation.account_service, "get_available_access_token", lambda **kwargs: (_ for _ in ()).throw(AssertionError("pool called")))
    monkeypatch.setattr(conversation.account_service, "get_account", lambda token: {"access_token": token, "email": "test@example.com"})

    def events(*args: object, **kwargs: object):
        yield {
            "type": "conversation.done",
            "conversation_id": "conversation-1",
            "file_ids": ["file-1"],
            "sediment_ids": [],
            "text": "",
            "turn_use_case": "image gen",
        }

    monkeypatch.setattr(conversation, "conversation_events", events)


def test_managed_job_uses_claimed_account_without_pool_wait(monkeypatch) -> None:
    order: list[str] = []
    _install_harness(monkeypatch, order)
    request = ConversationRequest(
        model="gpt-image-2",
        prompt="cat",
        n=1,
        managed_access_token="claimed-token",
        checkpoint_callback=lambda checkpoint: order.append("checkpoint"),
        image_result_formatter=lambda payload, context: order.append("format") or {"url": "https://cdn.example/result.png", "width": 1, "height": 1},
        defer_conversation_cleanup=True,
        message_as_error=True,
    )

    outputs = generate_single_image_for_job(request)

    assert outputs[0].kind == "result"
    assert FakeBackend.instances[0].access_token == "claimed-token"
    assert order[-1] == "format"
    assert order[0] == "checkpoint"
    assert all(item == "checkpoint" for item in order[:-1])
    assert FakeBackend.instances[0].deleted == []


def test_silent_sse_stream_is_aborted_when_job_is_canceled() -> None:
    response = SimpleNamespace(
        queue=queue.Queue(),
        quit_now=SimpleNamespace(set=lambda: None),
        curl=SimpleNamespace(close=lambda: None),
        _stream_closed=False,
    )

    def canceled() -> None:
        raise RuntimeError("image task was canceled")

    with pytest.raises(RuntimeError, match="canceled"):
        next(iter_sse_payloads(response, max_duration_secs=60, cancel_callback=canceled))

    assert response._stream_closed is True


def test_remote_checkpoint_is_persisted_before_stream_finishes(monkeypatch) -> None:
    checkpoints = []

    def interrupted_events(*args: object, **kwargs: object):
        yield {
            "type": "conversation.event",
            "conversation_id": "conversation-early",
            "file_ids": [],
            "sediment_ids": [],
            "raw": {"type": "progress"},
        }
        raise RuntimeError("container stopped")

    monkeypatch.setattr(conversation, "conversation_events", interrupted_events)
    request = ConversationRequest(
        model="gpt-image-2",
        prompt="cat",
        checkpoint_callback=checkpoints.append,
    )

    with pytest.raises(RuntimeError, match="container stopped"):
        list(conversation.stream_image_outputs(FakeBackend("token"), request))

    assert checkpoints
    assert checkpoints[0].conversation_id == "conversation-early"
