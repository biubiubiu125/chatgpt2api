from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from PIL import Image

from services.image_queue.database import ImageQueueUnavailableError
from services.image_queue.repository import IdempotencyConflict
from services.image_queue.types import (
    ArtifactDescriptor,
    ArtifactStatus,
    ImageAccountCandidate,
    JobCheckpoint,
    JobStage,
)
from services.image_queue.recovery import ImageRecovery


IDENTITY = {"id": "owner-1", "name": "test", "role": "user"}


def test_async_submit_returns_before_worker_runs(image_task_service, stopped_worker) -> None:
    result = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )

    assert result["status"] == "queued"
    assert result["task_id"]
    assert result["client_task_id"] == "client-1"
    assert result["queue_position"] == 1
    assert result["mode"] == "generate"
    assert result["model"] == "gpt-image-2"
    assert result["n"] == 1
    assert result["quality"] == "auto"
    assert stopped_worker.execution_count == 0
    assert stopped_worker.notify_count == 1


def test_same_idempotent_submit_reuses_persisted_task(image_task_service) -> None:
    first = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )
    second = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )

    assert first["task_id"] == second["task_id"]
    assert len(image_task_service.repository.list_tasks("owner-1")) == 1


def test_submit_rejects_mismatched_idempotency_key_and_client_task_id(image_task_service) -> None:
    image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-a",
        idempotency_key="key-a",
        prompt="same cat",
        model="gpt-image-2",
    )
    image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-b",
        idempotency_key="key-b",
        prompt="same cat",
        model="gpt-image-2",
    )

    with pytest.raises(IdempotencyConflict):
        image_task_service.submit_generation(
            IDENTITY,
            client_task_id="client-b",
            idempotency_key="key-a",
            prompt="same cat",
            model="gpt-image-2",
        )


def test_prompt_suffix_change_conflicts_with_same_idempotency_key(image_task_service) -> None:
    image_task_service.settings = replace(image_task_service.settings, prompt_suffix="suffix A")
    image_task_service.submit_generation(
        IDENTITY,
        client_task_id="suffix-sensitive",
        prompt="cat",
        model="gpt-image-2",
    )

    image_task_service.settings = replace(image_task_service.settings, prompt_suffix="suffix B")

    with pytest.raises(IdempotencyConflict):
        image_task_service.submit_generation(
            IDENTITY,
            client_task_id="suffix-sensitive",
            prompt="cat",
            model="gpt-image-2",
        )


def test_legacy_success_task_get_and_list_return_archived_result_without_artifact(
    image_task_service,
    tmp_path,
) -> None:
    legacy_path = tmp_path / "image_tasks.json"
    legacy_path.write_text(
        json.dumps({
            "tasks": [{
                "id": "legacy-success",
                "owner_id": "owner-1",
                "status": "success",
                "mode": "generate",
                "model": "gpt-image-2",
                "n": 1,
                "data": [{
                    "url": "https://example.test/images/legacy.png",
                    "width": 1024,
                    "height": 768,
                    "relative_path": "legacy/missing.png",
                }],
                "created_at": "2026-07-20T10:00:00+00:00",
                "updated_at": "2026-07-20T10:01:00+00:00",
            }],
        }),
        encoding="utf-8",
    )

    summary = ImageRecovery(image_task_service.repository).import_legacy_tasks(legacy_path)

    assert summary.imported_terminal == 1
    task = image_task_service.get_task(IDENTITY, "legacy-success")
    listed = image_task_service.list_tasks(IDENTITY, ["legacy-success"])
    assert task["status"] == "success"
    assert task["data"] == listed["items"][0]["data"]
    assert task["data"][0]["url"] == "https://example.test/images/legacy.png"
    assert task["data"][0]["width"] == 1024
    assert task["data"][0]["height"] == 768


def test_queue_position_counts_jobs_ahead_across_tasks(image_task_service) -> None:
    first = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
        n=2,
    )
    second = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-2",
        prompt="dog",
        model="gpt-image-2",
    )

    assert first["queue_position"] == 1
    assert second["queue_position"] == 3


def test_queued_task_reports_current_worker_resource_pause_reason(image_task_service) -> None:
    queued = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="resource-paused",
        prompt="cat",
        model="gpt-image-2",
    )
    image_task_service.repository.update_worker_state(
        "worker-resource-paused",
        resource_snapshot={"cpu_percent": 96.0},
        effective_concurrency=0,
        pause_reason="resource_cpu",
    )

    task = image_task_service.get_task(IDENTITY, queued["task_id"])
    listed = image_task_service.list_tasks(IDENTITY, [queued["task_id"]])

    assert task["wait_reason"] == "resource_cpu"
    assert listed["items"][0]["wait_reason"] == "resource_cpu"


def test_task_list_uses_bounded_batch_queries(image_task_service) -> None:
    from sqlalchemy import event

    for index in range(5):
        image_task_service.submit_generation(
            IDENTITY,
            client_task_id=f"batch-list-{index}",
            prompt=f"cat {index}",
            model="gpt-image-2",
        )
    statements = []

    def record_statement(*args):
        statements.append(args[2])

    event.listen(image_task_service.database.engine, "before_cursor_execute", record_statement)
    try:
        result = image_task_service.list_tasks(IDENTITY, [], limit=5, offset=0)
    finally:
        event.remove(image_task_service.database.engine, "before_cursor_execute", record_statement)

    assert len(result["items"]) == 5
    selects = [item for item in statements if str(item).lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 3


def test_waiter_timeout_does_not_cancel_persisted_task(image_task_service) -> None:
    queued = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )

    with pytest.raises(TimeoutError):
        image_task_service.wait_for_terminal("owner-1", queued["task_id"], timeout=0.03)

    assert image_task_service.get_task(IDENTITY, queued["task_id"])["status"] == "queued"


def test_external_submit_rejects_non_gpt_image_2(image_task_service) -> None:
    with pytest.raises(ValueError, match="only gpt-image-2"):
        image_task_service.submit_generation(
            IDENTITY,
            client_task_id="client-1",
            prompt="cat",
            model="dall-e-3",
        )


def test_failed_enqueue_discards_private_input_artifacts(image_task_service, monkeypatch) -> None:
    output = BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format="PNG")

    def fail_enqueue(request):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(image_task_service.repository, "enqueue_task", fail_enqueue)

    with pytest.raises(RuntimeError, match="database write failed"):
        image_task_service.submit_edit(
            IDENTITY,
            client_task_id="edit-orphan",
            prompt="edit",
            model="gpt-image-2",
            images=[(output.getvalue(), "input.png", "image/png")],
        )

    assert list(image_task_service.artifact_service.root.rglob("*.png")) == []


def test_edit_inputs_are_persisted_as_artifacts_not_database_blobs(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format="PNG")

    task = image_task_service.submit_edit(
        IDENTITY,
        client_task_id="edit-1",
        prompt="make it brighter",
        model="gpt-image-2",
        images=[(output.getvalue(), "input.png", "image/png")],
    )

    context = image_task_service.repository.get_execution_request(task["task_id"])
    artifacts = image_task_service.repository.list_artifacts(context["task_id"])
    assert context["request_payload"]["input_artifacts"] == [artifacts[0].relative_path]
    assert artifacts[0].kind == "input"
    assert artifacts[0].width == 8
    assert "images" not in context["request_payload"]


def test_duplicate_edit_inputs_keep_distinct_ordered_artifacts(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (8, 6), (20, 40, 60)).save(output, format="PNG")
    image = (output.getvalue(), "same.png", "image/png")

    task = image_task_service.submit_edit(
        IDENTITY,
        client_task_id="duplicate-inputs",
        prompt="use both references",
        model="gpt-image-2",
        images=[image, image],
    )

    artifacts = [
        item
        for item in image_task_service.repository.list_artifacts(UUID(task["task_id"]))
        if item.kind == "input"
    ]
    assert len(artifacts) == 2
    assert [item.ordinal for item in artifacts] == [1, 2]
    assert artifacts[0].relative_path != artifacts[1].relative_path


def test_claim_execution_persists_verified_final_artifact(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"), access_token="token")],
        1,
    )
    cleaned = []

    def generate(request):
        request.checkpoint_callback(JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="conversation-1"))
        item = request.image_result_formatter(
            output.getvalue(),
            {"conversation_id": "conversation-1", "image_urls": ["https://upstream/image.png"]},
        )
        return [SimpleNamespace(kind="result", data=[item], conversation_id="conversation-1")]

    image_task_service.job_generator = generate
    image_task_service.conversation_cleanup = lambda token, conversation_id: cleaned.append((token, conversation_id))

    image_task_service.execute_claim(claim, "claimed-token")

    completed = image_task_service.get_task(IDENTITY, task["task_id"])
    artifacts = image_task_service.repository.list_artifacts(claim.job.task_id)
    assert completed["status"] == "success"
    assert completed["delivery_status"] == "response_attempted"
    assert completed["data"][0]["width"] == 12
    assert completed["data"][0]["height"] == 9
    assert artifacts[-1].kind == "final"
    assert artifacts[-1].absolute_path is None
    assert cleaned == [("claimed-token", "conversation-1")]


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_successful_task_requeues_when_final_artifact_is_not_deliverable(
    image_task_service,
    stopped_worker,
    damage,
) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id=f"repair-final-{damage}",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)

    def generate(request):
        request.checkpoint_callback(JobCheckpoint(
            stage=JobStage.DOWNLOADING,
            conversation_id="conversation-1",
            image_urls=["https://upstream/image.png"],
        ))
        item = request.image_result_formatter(
            output.getvalue(),
            {"conversation_id": "conversation-1", "image_urls": ["https://upstream/image.png"]},
        )
        return [SimpleNamespace(kind="result", data=[item], conversation_id="conversation-1")]

    image_task_service.job_generator = generate
    image_task_service.conversation_cleanup = lambda token, conversation_id: None
    image_task_service.execute_claim(claim, "claimed-token")
    persisted = image_task_service.repository.get_task("owner-1", task["task_id"])
    final_path = image_task_service.artifact_service.root / persisted.data[0]["relative_path"]
    if damage == "missing":
        final_path.unlink()
    else:
        final_path.write_bytes(b"not a valid image")

    recovering = image_task_service.get_task(IDENTITY, task["task_id"])

    assert recovering["status"] == "saving"
    assert recovering["data"] == []
    assert recovering["delivery_status"] == "pending"
    assert stopped_worker.notify_count == 2

    recovery_claim = image_task_service.repository.claim_next_job("worker-repair", [], 1)
    assert recovery_claim is not None and recovery_claim.account_slot == -1
    image_task_service.job_generator = lambda request: (_ for _ in ()).throw(AssertionError("regenerated"))
    image_task_service.backend_factory = lambda token: (_ for _ in ()).throw(AssertionError("downloaded again"))
    image_task_service.execute_claim(recovery_claim, "")

    repaired = image_task_service.get_task(IDENTITY, task["task_id"])
    repaired_path = image_task_service.artifact_service.root / repaired["data"][0]["relative_path"]
    assert repaired["status"] == "success"
    assert repaired_path.is_file()


def test_running_cancel_stops_before_transform_and_save(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="cancel-running",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(
            account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token",
        )],
        1,
    )
    assert claim is not None
    upscaled = []

    def generate(request):
        image_task_service.cancel(IDENTITY, task["task_id"])
        request.cancel_requested_callback()
        return [SimpleNamespace(kind="result", data=[])]

    image_task_service.job_generator = generate
    image_task_service.upscaler = lambda data, size: upscaled.append(True) or data

    with pytest.raises(RuntimeError, match="canceled"):
        image_task_service.execute_claim(claim, "claimed-token")

    assert upscaled == []
    assert image_task_service.get_task(IDENTITY, task["task_id"])["status"] == "canceled"
    assert not any(
        artifact.kind == "final"
        for artifact in image_task_service.repository.list_artifacts(claim.job.task_id)
    )


def test_list_tasks_marks_success_result_delivery_attempted(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="listed-success",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"), access_token="token")],
        1,
    )
    assert claim is not None
    artifact = image_task_service.artifact_service.persist_final(
        claim.job.task_id,
        claim.job.id,
        output.getvalue(),
        "https://api.example",
    )
    image_task_service.repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 12, "height": 9},
    )

    result = image_task_service.list_tasks(IDENTITY, [task["task_id"]])

    assert result["items"][0]["delivery_status"] == "response_attempted"


def test_claim_execution_uses_bounded_io_and_upscale_pools(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="pooled-client",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(
            account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token",
        )],
        1,
    )
    pool_calls = []

    class RecordingPools:
        def _submit(self, name, operation):
            pool_calls.append(name)
            future = Future()
            try:
                future.set_result(operation())
            except Exception as exc:
                future.set_exception(exc)
            return future

        def submit_io(self, operation):
            return self._submit("io", operation)

        def submit_upscale(self, operation):
            return self._submit("upscale", operation)

        def stop(self, timeout=None):
            return None

    def generate(request):
        item = request.image_result_formatter(
            output.getvalue(),
            {"conversation_id": "conversation-1", "image_urls": ["https://upstream/image.png"]},
        )
        return [SimpleNamespace(kind="result", data=[item], conversation_id="conversation-1")]

    image_task_service.worker = RecordingPools()
    image_task_service.job_generator = generate
    image_task_service.conversation_cleanup = lambda token, conversation_id: None

    image_task_service.execute_claim(claim, "claimed-token")

    assert image_task_service.get_task(IDENTITY, task["task_id"])["status"] == "success"
    assert "upscale" in pool_calls
    assert pool_calls.count("io") >= 2


def test_upscale_fallback_is_persisted_as_task_event(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (12, 9), (80, 100, 120)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="upscale-fallback",
        prompt="cat",
        model="gpt-image-2",
        size="1024x1024",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"), access_token="token")],
        1,
    )
    assert claim is not None

    def generate(request):
        item = request.image_result_formatter(
            output.getvalue(),
            {"conversation_id": "conversation-1", "image_urls": ["https://upstream/image.png"]},
        )
        return [SimpleNamespace(kind="result", data=[item], conversation_id="conversation-1")]

    image_task_service.job_generator = generate
    image_task_service.conversation_cleanup = lambda token, conversation_id: None
    image_task_service.upscaler = lambda data, size: SimpleNamespace(
        payload=data,
        event_type="upscale_fallback_original",
        event_data={"error": "upscale runtime failed"},
    )

    image_task_service.execute_claim(claim, "claimed-token")

    events = image_task_service.repository.logical_backup()["events"]
    fallback = next(item for item in events if item["event_type"] == "upscale_fallback_original")
    assert fallback["task_id"] == task["task_id"]
    assert fallback["job_id"] == str(claim.job.id)
    assert fallback["event_data"]["error"] == "upscale runtime failed"


def test_start_recovers_expired_work_before_worker_dispatch(image_task_service, stopped_worker) -> None:
    order = []
    image_task_service.recovery = SimpleNamespace(recover=lambda: order.append("recover"))
    stopped_worker.start = lambda: order.append("worker")

    image_task_service.start()

    assert order == ["recover", "worker"]


def test_stop_keeps_owned_database_alive_when_worker_did_not_drain() -> None:
    disposed = []
    service = __import__("services.image_task_service", fromlist=["ImageTaskService"]).ImageTaskService(
        worker=SimpleNamespace(stop=lambda timeout=None: False),
    )
    service.database = SimpleNamespace(dispose=lambda: disposed.append(True))
    service._owns_database = True

    service.stop(timeout=0.01)

    assert disposed == []


def test_resume_poll_persists_requested_timeout(image_task_service) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="resume-timeout",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(
            account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token",
        )],
        1,
    )
    image_task_service.repository.checkpoint_job(
        claim,
        JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="conversation-1"),
    )
    image_task_service.repository.fail_job(
        claim,
        error_code="image_poll_timeout",
        error_message="timed out",
    )

    image_task_service.resume_poll(IDENTITY, task["task_id"], 120.0)

    context = image_task_service.repository.get_execution_request(task["task_id"])
    assert context["request_payload"]["resume_poll_timeout_seconds"] == 120.0


def test_failed_startup_rejects_new_submissions(image_task_service) -> None:
    image_task_service.recovery = SimpleNamespace(
        import_legacy_tasks=lambda path: None,
        recover=lambda: (_ for _ in ()).throw(RuntimeError("recovery failed")),
    )

    with pytest.raises(RuntimeError, match="recovery failed"):
        image_task_service.start()

    with pytest.raises(ImageQueueUnavailableError, match="did not start"):
        image_task_service.submit_generation(
            IDENTITY,
            client_task_id="startup-failed",
            prompt="cat",
            model="gpt-image-2",
        )


def test_resume_poll_requeues_failed_job_from_remote_checkpoint(image_task_service, stopped_worker) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )
    claim = image_task_service.repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"), access_token="token")],
        1,
    )
    image_task_service.repository.checkpoint_job(
        claim,
        JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="conversation-1"),
    )
    image_task_service.repository.fail_job(
        claim,
        error_code="image_poll_timeout",
        error_message="timed out",
    )
    previous_notifications = stopped_worker.notify_count

    resumed = image_task_service.resume_poll(IDENTITY, task["task_id"], 30)

    assert resumed["status"] == "queued"
    assert image_task_service.repository.list_jobs(claim.job.task_id)[0].stage == JobStage.RESOLVING
    assert stopped_worker.notify_count == previous_notifications + 1


def test_reclaimed_job_downloads_saved_urls_without_regeneration(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (10, 7), (1, 2, 3)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    first_claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)
    image_task_service.repository.checkpoint_job(
        first_claim,
        JobCheckpoint(
            stage=JobStage.DOWNLOADING,
            conversation_id="conversation-1",
            image_urls=["https://upstream/image.png"],
        ),
    )
    image_task_service.repository.release_claim(first_claim)
    resumed_claim = image_task_service.repository.claim_next_job("worker-2", [candidate], 1)

    class Backend:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def download_image_bytes(self, urls):
            assert urls == ["https://upstream/image.png"]
            return [output.getvalue()]

    image_task_service.backend_factory = lambda token: Backend()
    image_task_service.job_generator = lambda request: (_ for _ in ()).throw(AssertionError("regenerated"))
    image_task_service.conversation_cleanup = lambda token, conversation_id: None

    image_task_service.execute_claim(resumed_claim, "claimed-token")

    completed = image_task_service.get_task(IDENTITY, task["task_id"])
    assert completed["status"] == "success"
    assert (completed["data"][0]["width"], completed["data"][0]["height"]) == (10, 7)


def test_expired_saved_url_is_refreshed_from_conversation(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (10, 7), (1, 2, 3)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="expired-url",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    first_claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)
    image_task_service.repository.checkpoint_job(
        first_claim,
        JobCheckpoint(
            stage=JobStage.DOWNLOADING,
            conversation_id="conversation-1",
            image_urls=["https://upstream/expired.png"],
            file_ids=["file-1"],
        ),
    )
    image_task_service.repository.release_claim(first_claim)
    resumed_claim = image_task_service.repository.claim_next_job("worker-2", [candidate], 1)
    downloads = []

    class Backend:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def download_image_bytes(self, urls):
            downloads.append(list(urls))
            if "expired.png" in urls[0]:
                raise TimeoutError("signed URL expired")
            return [output.getvalue()]

        def resolve_conversation_image_urls(
            self,
            conversation_id,
            file_ids,
            sediment_ids,
            poll=False,
            cancel_callback=None,
        ):
            assert conversation_id == "conversation-1"
            assert file_ids == ["file-1"]
            assert callable(cancel_callback)
            return ["https://upstream/refreshed.png"]

    image_task_service.backend_factory = lambda token: Backend()
    image_task_service.conversation_cleanup = lambda token, conversation_id: None

    image_task_service.execute_claim(resumed_claim, "claimed-token")

    assert downloads == [
        ["https://upstream/expired.png"],
        ["https://upstream/refreshed.png"],
    ]
    assert image_task_service.get_task(IDENTITY, task["task_id"])["status"] == "success"


def test_recovery_uses_downloaded_artifact_without_remote_access(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (13, 8), (4, 5, 6)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="downloaded-crash",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    first_claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)
    image_task_service.repository.checkpoint_job(
        first_claim,
        JobCheckpoint(
            stage=JobStage.TRANSFORMING,
            conversation_id="conversation-1",
            image_urls=["https://upstream/expired.png"],
        ),
    )
    image_task_service.artifact_service.persist_stage(
        first_claim.job.task_id,
        first_claim.job.id,
        output.getvalue(),
        "downloaded",
    )
    image_task_service.repository.release_claim(first_claim)
    resumed_claim = image_task_service.repository.claim_next_job("worker-2", [candidate], 1)
    image_task_service.backend_factory = lambda token: (_ for _ in ()).throw(AssertionError("remote access"))
    image_task_service.job_generator = lambda request: (_ for _ in ()).throw(AssertionError("regenerated"))
    image_task_service.conversation_cleanup = lambda token, conversation_id: None

    image_task_service.execute_claim(resumed_claim, "claimed-token")

    completed = image_task_service.get_task(IDENTITY, task["task_id"])
    kinds = {item.kind for item in image_task_service.repository.list_artifacts(first_claim.job.task_id)}
    assert completed["status"] == "success"
    assert (completed["data"][0]["width"], completed["data"][0]["height"]) == (13, 8)
    assert {"downloaded", "upscaled", "final"}.issubset(kinds)


def test_missing_ready_recovery_artifact_is_invalidated_before_remote_access(
    image_task_service,
) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="stale-local-artifact",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    claimed_at = datetime.now(timezone.utc)
    first_claim = image_task_service.repository.claim_next_job(
        "worker-before-crash",
        [candidate],
        1,
        claimed_at,
    )
    stale = ArtifactDescriptor(
        task_id=first_claim.job.task_id,
        job_id=first_claim.job.id,
        kind="upscaled",
        status=ArtifactStatus.READY,
        relative_path=f"{first_claim.job.task_id}/{first_claim.job.id}/u/missing.png",
        sha256="a" * 64,
        mime_type="image/png",
        byte_size=10,
        width=8,
        height=6,
    )
    assert image_task_service.repository.record_artifact(first_claim, stale) is True
    assert image_task_service.repository.checkpoint_job(
        first_claim,
        JobCheckpoint(stage=JobStage.SAVING, conversation_id="conversation-1"),
    ) is True
    assert image_task_service.repository.reclaim_expired_leases(
        claimed_at + timedelta(seconds=91)
    ) == 1
    recovery_claim = image_task_service.repository.claim_next_job(
        "worker-after-crash",
        [candidate],
        1,
        claimed_at + timedelta(seconds=91),
    )
    assert recovery_claim is not None and recovery_claim.account_slot == -1
    image_task_service.backend_factory = lambda token: (_ for _ in ()).throw(
        AssertionError("remote backend must not run without the original account")
    )

    with pytest.raises(RuntimeError, match="local recovery artifacts are unavailable"):
        image_task_service.execute_claim(recovery_claim, "")

    artifacts = image_task_service.repository.list_artifacts(first_claim.job.task_id)
    assert artifacts[0].status == ArtifactStatus.INVALID
    assert image_task_service.repository.record_artifact(recovery_claim, stale) is True
    artifacts = image_task_service.repository.list_artifacts(first_claim.job.task_id)
    assert artifacts[0].status == ArtifactStatus.READY
    assert image_task_service.repository.invalidate_recovery_artifacts(recovery_claim) == 1
    image_task_service.repository.schedule_retry(
        recovery_claim,
        error_code="local_artifact_unavailable",
        error_message="retry with original account",
        next_retry_at=datetime.now(timezone.utc),
    )
    account_claim = image_task_service.repository.claim_next_job(
        "worker-with-account",
        [candidate],
        1,
    )
    assert account_claim is not None
    assert account_claim.account_slot == 0
    assert account_claim.account_id == candidate.account_id


def test_recovery_adopts_atomically_saved_file_before_database_commit(image_task_service) -> None:
    output = BytesIO()
    Image.new("RGB", (14, 11), (7, 8, 9)).save(output, format="PNG")
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="atomic-crash",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    claimed_at = datetime.now(timezone.utc)
    first_claim = image_task_service.repository.claim_next_job(
        "worker-before-crash",
        [candidate],
        1,
        claimed_at,
    )
    image_task_service.repository.checkpoint_job(
        first_claim,
        JobCheckpoint(
            stage=JobStage.SAVING,
            conversation_id="conversation-1",
            image_urls=["https://expired-upstream/image.png"],
        ),
    )
    saved = image_task_service.artifact_service.persist_final(
        first_claim.job.task_id,
        first_claim.job.id,
        output.getvalue(),
        "https://api.example",
    )

    ImageRecovery(image_task_service.repository).recover(claimed_at + timedelta(seconds=91))
    assert image_task_service.adopt_local_recovery_artifacts() == 1
    resumed_claim = image_task_service.repository.claim_next_job(
        "worker-after-crash",
        [],
        1,
        claimed_at + timedelta(seconds=92),
    )
    assert resumed_claim is not None and resumed_claim.account_slot == -1
    image_task_service.backend_factory = lambda token: (_ for _ in ()).throw(AssertionError("downloaded again"))
    image_task_service.job_generator = lambda request: (_ for _ in ()).throw(AssertionError("regenerated"))
    image_task_service.conversation_cleanup = lambda token, conversation_id: None

    image_task_service.execute_claim(resumed_claim, "claimed-token")

    completed = image_task_service.get_task(IDENTITY, task["task_id"])
    assert completed["status"] == "success"
    assert (completed["data"][0]["width"], completed["data"][0]["height"]) == (14, 11)
    assert saved.absolute_path is not None and saved.absolute_path.is_file()
