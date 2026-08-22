from __future__ import annotations

import base64
import asyncio
import json
import time
from io import BytesIO

from PIL import Image
import pytest

from services.image_failure import ImageGenerationError
from services.log_service import LoggedCall, _image_error_response
from services.protocol import (
    openai_v1_chat_complete,
    openai_v1_image_edit,
    openai_v1_image_generations,
    openai_v1_response,
)
from services.protocol import durable_image


def png_bytes(width: int = 13, height: int = 11) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (20, 30, 40)).save(output, format="PNG")
    return output.getvalue()


class FakeDurableService:
    def __init__(self) -> None:
        self.submissions = []
        self.response_attempts = 0
        self.artifact_reads = 0
        self.recovery_checks = 0

    def submit_protocol_request(self, identity, payload, mode, idempotency_key, trace_headers):
        self.submissions.append((identity, payload, mode, idempotency_key, trace_headers))
        return {"task_id": "task-1", "status": "queued"}

    def wait_for_terminal(self, owner, task_id, timeout=None):
        count = int(self.submissions[-1][1].get("n") or 1)
        return {
            "task_id": task_id,
            "status": "success",
            "required_jobs": count,
            "succeeded_jobs": count,
            "failed_jobs": 0,
            "data": [{
                "url": f"https://cdn.example/task-1/job-{index}/image.png",
                "relative_path": f"task-1/job-{index}/image.png",
                "width": 13,
                "height": 11,
                "revised_prompt": "cat",
            } for index in range(1, count + 1)],
        }

    def read_result_artifact(self, identity, task_id, relative_path):
        assert relative_path.startswith("task-1/job-") and relative_path.endswith("/image.png")
        self.artifact_reads += 1
        return png_bytes()

    def mark_response_attempted(self, identity, task_id):
        self.response_attempts += 1

    def get_task(self, identity, task_id):
        self.recovery_checks += 1
        return {
            "task_id": task_id,
            "status": "queued",
            "required_jobs": 1,
            "succeeded_jobs": 0,
            "failed_jobs": 0,
            "data": [],
        }

    async def wait_for_terminal_async(self, owner, task_id, timeout=None):
        await asyncio.sleep(0)
        return self.wait_for_terminal(owner, task_id, timeout)


def durable_body(**changes):
    body = {
        "model": "gpt-image-2",
        "prompt": "cat",
        "n": 1,
        "response_format": "b64_json",
        "_image_task_context": {
            "identity": {"id": "owner-1", "role": "user"},
            "idempotency_key": "request-1",
            "trace_headers": {"x-newapi-request-id": "trace-1"},
            "base_url": "https://api.example",
        },
    }
    body.update(changes)
    return body


def test_images_response_uses_saved_artifact_and_final_dimensions(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)

    response = openai_v1_image_generations.handle(durable_body())

    assert base64.b64decode(response["data"][0]["b64_json"]) == png_bytes()
    assert (response["data"][0]["width"], response["data"][0]["height"]) == (13, 11)
    assert response["task_id"] == "task-1"
    assert service.response_attempts == 1


def test_url_response_revalidates_saved_artifact_before_delivery(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)

    response = openai_v1_image_generations.handle(durable_body(response_format="url"))

    assert response["data"][0]["url"].endswith("/image.png")
    assert service.artifact_reads == 1
    assert service.response_attempts == 1


def test_image_stream_completed_event_keeps_final_dimensions(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)

    events = list(openai_v1_image_generations.handle(durable_body(stream=True)))

    assert events[0] == {
        "type": "image_generation.queued",
        "status": "queued",
        "task_id": "task-1",
    }
    completed = next(event for event in events if event["type"] == "image_generation.completed")
    assert (completed["width"], completed["height"]) == (13, 11)
    assert completed["task_id"] == "task-1"


@pytest.mark.anyio
async def test_durable_image_stream_returns_before_terminal_wait(monkeypatch) -> None:
    service = FakeDurableService()

    async def slow_terminal(owner, task_id, timeout=None):
        await asyncio.sleep(0.3)
        return service.wait_for_terminal(owner, task_id, timeout)

    service.wait_for_terminal_async = slow_terminal
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(stream=True, response_format="url")
    call = LoggedCall(
        {"id": "owner-1", "role": "user"},
        "/v1/images/generations",
        "gpt-image-2",
        "stream timing",
    )
    started = time.perf_counter()

    response = await call.run(
        openai_v1_image_generations.handle,
        body,
        async_before=lambda: durable_image.prepare_submission(
            body,
            body,
            mode="generation",
            response_format="url",
        ),
    )

    assert time.perf_counter() - started < 0.2
    assert response.media_type == "text/event-stream"


def test_image_edit_stream_starts_with_durable_task_id(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        stream=True,
        response_format="url",
        images=[(png_bytes(), "input.png", "image/png")],
    )

    events = list(openai_v1_image_edit.handle(body))

    assert events[0] == {
        "type": "image_edit.queued",
        "status": "queued",
        "task_id": "task-1",
    }


def test_durable_stream_emits_heartbeat_while_task_is_running(monkeypatch) -> None:
    service = FakeDurableService()
    original_wait = service.wait_for_terminal
    attempts = 0

    def wait_with_one_timeout(owner, task_id, timeout=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("still running")
        return original_wait(owner, task_id, timeout)

    service.wait_for_terminal = wait_with_one_timeout
    monkeypatch.setattr(durable_image, "image_task_service", service)

    events = list(openai_v1_image_generations.handle(durable_body(stream=True)))

    assert [event["type"] for event in events] == [
        "image_generation.queued",
        "image_generation.in_progress",
        "image_generation.completed",
    ]
    assert all(event["task_id"] == "task-1" for event in events)


def test_url_image_stream_emits_one_completed_event_per_job(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)

    events = list(openai_v1_image_generations.handle(durable_body(
        stream=True,
        response_format="url",
        n=2,
    )))

    completed = [event for event in events if event["type"] == "image_generation.completed"]
    assert len(completed) == 2
    assert all(event["url"].endswith("/image.png") for event in completed)


def test_chat_response_keeps_markdown_and_adds_image_results(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(messages=[{"role": "user", "content": "draw a cat"}])

    response = openai_v1_chat_complete.handle(body)

    message = response["choices"][0]["message"]
    assert "![" in message["content"]
    assert (message["image_results"][0]["width"], message["image_results"][0]["height"]) == (13, 11)
    assert response["task_id"] == "task-1"


def test_responses_item_carries_final_dimensions(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        input="draw a cat",
        tools=[{"type": "image_generation", "size": "1024x1024"}],
    )

    response = openai_v1_response.handle(body)

    item = response["output"][0]
    assert (item["width"], item["height"]) == (13, 11)
    assert response["task_id"] == "task-1"


def test_responses_image_request_preserves_requested_image_count(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        input="draw two cats",
        n=2,
        tools=[{"type": "image_generation", "size": "1024x1024"}],
    )

    response = openai_v1_response.handle(body)

    image_items = [item for item in response["output"] if item["type"] == "image_generation_call"]
    assert service.submissions[-1][1]["n"] == 2
    assert len(image_items) == 2


def test_responses_image_instructions_are_part_of_durable_prompt(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        input="draw a cat",
        instructions="make it watercolor",
        tools=[{"type": "image_generation", "size": "1024x1024"}],
    )

    response = openai_v1_response.handle(body)

    assert response["task_id"] == "task-1"
    assert service.submissions[-1][1]["prompt"] == "make it watercolor\n\ndraw a cat"


def test_responses_stream_completed_event_carries_task_id(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        stream=True,
        input="draw a cat",
        tools=[{"type": "image_generation", "size": "1024x1024"}],
    )

    events = list(openai_v1_response.handle(body))

    assert events[0]["type"] == "response.created"
    assert events[0]["task_id"] == "task-1"
    assert events[0]["response"]["task_id"] == "task-1"
    completed = next(event for event in events if event["type"] == "response.completed")
    assert completed["task_id"] == "task-1"
    assert completed["response"]["task_id"] == "task-1"


def test_chat_stream_final_image_delta_carries_dimensions(monkeypatch) -> None:
    service = FakeDurableService()
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(
        stream=True,
        messages=[{"role": "user", "content": "draw a cat"}],
    )

    chunks = list(openai_v1_chat_complete.handle(body))

    assert chunks[0]["task_id"] == "task-1"
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    image_chunk = next(chunk for chunk in chunks if chunk["choices"][0]["delta"].get("image_results"))
    result = image_chunk["choices"][0]["delta"]["image_results"][0]
    assert (result["width"], result["height"]) == (13, 11)
    assert image_chunk["task_id"] == "task-1"


def test_one_failed_required_job_returns_task_failure_with_recovery_id(monkeypatch) -> None:
    service = FakeDurableService()

    def failed(owner, task_id, timeout=None):
        return {
            "task_id": task_id,
            "status": "failed",
            "required_jobs": 2,
            "succeeded_jobs": 1,
            "failed_jobs": 1,
            "error_code": "upstream_5xx",
            "error": "one image failed",
            "data": [{"url": "https://cdn.example/saved.png", "width": 13, "height": 11}],
        }

    service.wait_for_terminal = failed
    monkeypatch.setattr(durable_image, "image_task_service", service)

    with pytest.raises(ImageGenerationError) as exc_info:
        openai_v1_image_generations.handle(durable_body(n=2))

    assert exc_info.value.task_id == "task-1"
    assert service.response_attempts == 1
    response = _image_error_response(exc_info.value)
    assert response.headers["x-image-task-id"] == "task-1"
    assert json.loads(response.body)["error"]["task_id"] == "task-1"


@pytest.mark.anyio
async def test_async_prepare_waits_without_using_sync_waiter(monkeypatch) -> None:
    service = FakeDurableService()

    def forbidden(*args, **kwargs):
        raise AssertionError("synchronous terminal waiter was used")

    service.wait_for_terminal = forbidden

    async def terminal(owner, task_id, timeout=None):
        return {
            "task_id": task_id,
            "status": "success",
            "required_jobs": 1,
            "succeeded_jobs": 1,
            "failed_jobs": 0,
            "data": [{
                "url": "https://cdn.example/task-1/job-1/image.png",
                "relative_path": "task-1/job-1/image.png",
                "width": 13,
                "height": 11,
            }],
        }

    service.wait_for_terminal_async = terminal
    monkeypatch.setattr(durable_image, "image_task_service", service)
    body = durable_body(response_format="url")

    await durable_image.prepare(body, body, mode="generation", response_format="url")
    response = openai_v1_image_generations.handle(body)

    assert response["task_id"] == "task-1"
    assert len(service.submissions) == 1


@pytest.mark.anyio
async def test_async_prepare_timeout_keeps_background_task_recoverable(monkeypatch) -> None:
    service = FakeDurableService()

    async def timed_out(owner, task_id, timeout=None):
        assert timeout == 300
        raise TimeoutError("image task is still running")

    service.settings = type("Settings", (), {"protocol_wait_timeout_seconds": 300})()
    service.wait_for_terminal_async = timed_out
    monkeypatch.setattr(durable_image, "image_task_service", service)

    with pytest.raises(ImageGenerationError) as exc_info:
        await durable_image.prepare(
            durable_body(),
            durable_body(),
            mode="generation",
            response_format="url",
        )

    assert exc_info.value.task_id == "task-1"
    assert exc_info.value.code == "image_task_pending"
    response = _image_error_response(exc_info.value)
    assert response.status_code == 504
    assert response.headers["x-image-task-id"] == "task-1"


def test_result_artifact_error_keeps_submitted_task_id(monkeypatch) -> None:
    service = FakeDurableService()

    def missing_artifact(identity, task_id, relative_path):
        raise ValueError("saved artifact disappeared")

    service.read_result_artifact = missing_artifact
    monkeypatch.setattr(durable_image, "image_task_service", service)

    with pytest.raises(ImageGenerationError) as exc_info:
        openai_v1_image_generations.handle(durable_body())

    assert exc_info.value.task_id == "task-1"
    assert _image_error_response(exc_info.value).headers["x-image-task-id"] == "task-1"


def test_result_artifact_error_triggers_recovery_check(monkeypatch) -> None:
    service = FakeDurableService()

    def missing_artifact(identity, task_id, relative_path):
        raise ValueError("saved artifact disappeared")

    service.read_result_artifact = missing_artifact
    monkeypatch.setattr(durable_image, "image_task_service", service)

    with pytest.raises(ImageGenerationError) as exc_info:
        openai_v1_image_generations.handle(durable_body())

    assert exc_info.value.task_id == "task-1"
    assert exc_info.value.code == "image_task_pending"
    assert service.recovery_checks == 1
    assert service.response_attempts == 0
