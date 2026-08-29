from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.image_queue.repository import (
    IdempotencyConflict,
    ImageQueueRepository,
    TaskStateConflict,
)
from services.image_queue.models import ImageJob, ImageTask, ImageWorkerState
from services.image_queue.types import (
    ArtifactDescriptor,
    ArtifactStatus,
    DeliveryStatus,
    EnqueueRequest,
    ImageAccountCandidate,
    JobCheckpoint,
    JobStage,
    JobStatus,
    TaskStatus,
)


FIXED_NOW = datetime.now(timezone.utc) + timedelta(minutes=1)


def _artifact(claim, suffix: str) -> ArtifactDescriptor:
    return ArtifactDescriptor(
        task_id=claim.job.task_id,
        job_id=claim.job.id,
        kind="final",
        status=ArtifactStatus.READY,
        relative_path=f"{claim.job.task_id}/{claim.job.id}/{suffix}.png",
        sha256=(suffix * 64)[:64],
        mime_type="image/png",
        byte_size=128,
        width=64,
        height=32,
        public_url=f"https://images.example/{suffix}.png",
    )


def test_enqueue_splits_n_into_single_image_jobs(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    request = replace(generation_request, required_jobs=4)

    result = repository.enqueue_task(request)
    jobs = repository.list_jobs(result.task.id)

    assert result.created is True
    assert [job.ordinal for job in jobs] == [1, 2, 3, 4]
    assert all(job.status == JobStatus.QUEUED for job in jobs)


def test_same_key_and_hash_returns_existing_task(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    first = repository.enqueue_task(generation_request)
    second = repository.enqueue_task(generation_request)

    assert first.created is True
    assert second.created is False
    assert second.task.id == first.task.id
    assert len(repository.list_jobs(first.task.id)) == 1


def test_same_key_with_different_hash_conflicts(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    repository.enqueue_task(generation_request)
    changed = replace(generation_request, request_hash="f" * 64)

    with pytest.raises(IdempotencyConflict):
        repository.enqueue_task(changed)


def test_mismatched_idempotency_key_and_client_task_id_conflicts(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    repository.enqueue_task(replace(
        generation_request,
        idempotency_key="key-a",
        client_task_id="client-a",
    ))
    repository.enqueue_task(replace(
        generation_request,
        idempotency_key="key-b",
        client_task_id="client-b",
        task_id=uuid4(),
    ))
    mismatched = replace(
        generation_request,
        idempotency_key="key-a",
        client_task_id="client-b",
        task_id=uuid4(),
    )

    with pytest.raises(IdempotencyConflict):
        repository.enqueue_task(mismatched)


def test_ambiguous_non_uuid_identifier_raises_conflict(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    repository.enqueue_task(replace(
        generation_request,
        idempotency_key="first-key",
        client_task_id="shared-alias",
    ))
    second = repository.enqueue_task(replace(
        generation_request,
        idempotency_key="second-key",
        client_task_id="second-client",
        task_id=uuid4(),
        request_hash="b" * 64,
    ))
    with repository.database.session() as session:
        stored = session.get(ImageTask, second.task.id)
        assert stored is not None
        stored.idempotency_key = "shared-alias"

    with pytest.raises(IdempotencyConflict):
        repository.get_task(generation_request.owner_key, "shared-alias")

    with pytest.raises(IdempotencyConflict):
        repository.list_tasks(generation_request.owner_key, ["shared-alias"])


def test_terminal_retention_preserves_never_delivered_success(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    requests = [
        replace(
            generation_request,
            idempotency_key=f"retention-{index}",
            client_task_id=f"retention-{index}",
            request_hash=f"{index + 900:064x}"[-64:],
        )
        for index in range(4)
    ]
    task_ids = [repository.enqueue_task(request).task.id for request in requests]
    old = datetime.now(timezone.utc) - timedelta(days=40)
    with repository.database.session() as session:
        tasks = [session.get(ImageTask, task_id) for task_id in task_ids]
        for task in tasks:
            task.completed_at = old
            task.updated_at = old
        tasks[0].status = TaskStatus.SUCCESS.value
        tasks[0].succeeded_jobs = 1
        tasks[0].delivery_status = DeliveryStatus.PENDING.value
        tasks[1].status = TaskStatus.SUCCESS.value
        tasks[1].succeeded_jobs = 1
        tasks[1].delivery_status = DeliveryStatus.RESPONSE_ATTEMPTED.value
        tasks[1].response_attempted_at = old
        tasks[2].status = TaskStatus.SUCCESS.value
        tasks[2].succeeded_jobs = 1
        tasks[2].delivery_status = DeliveryStatus.ACKNOWLEDGED.value
        tasks[2].delivery_acked_at = old
        tasks[3].status = TaskStatus.FAILED.value
        tasks[3].failed_jobs = 1

    removed = repository.purge_terminal_tasks(
        retention_seconds=30 * 24 * 60 * 60,
        now=datetime.now(timezone.utc),
    )

    assert removed == 3
    assert repository.get_task(generation_request.owner_key, task_ids[0]) is not None
    assert all(
        repository.get_task(generation_request.owner_key, task_id) is None
        for task_id in task_ids[1:]
    )


def test_artifact_protection_matches_terminal_database_retention(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    artifact = _artifact(claim, "retention-window")
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 64, "height": 32},
    )
    assert repository.mark_quota_accounted(claim.job.id, account_candidates[0].account_id)
    repository.mark_response_attempted(generation_request.owner_key, task.id)
    repository.delivery_grace_seconds = 7 * 24 * 60 * 60
    repository.terminal_retention_seconds = 30 * 24 * 60 * 60
    with repository.database.session() as session:
        stored = session.get(ImageTask, task.id)
        stored.response_attempted_at = datetime.now(timezone.utc) - timedelta(days=20)

    assert artifact.relative_path in repository.protected_artifact_paths()

    with repository.database.session() as session:
        stored = session.get(ImageTask, task.id)
        stored.response_attempted_at = datetime.now(timezone.utc) - timedelta(days=31)

    assert artifact.relative_path not in repository.protected_artifact_paths()


def test_terminal_purge_never_shortens_response_delivery_grace(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.delivery_grace_seconds = 30 * 24 * 60 * 60
    repository.terminal_retention_seconds = 24 * 60 * 60
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1)
    assert claim is not None
    artifact = _artifact(claim, "long-delivery-grace")
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 64, "height": 32},
    )
    repository.mark_response_attempted(generation_request.owner_key, task.id)
    now = datetime.now(timezone.utc)
    with repository.database.session() as session:
        stored = session.get(ImageTask, task.id)
        stored.completed_at = now - timedelta(days=2)
        stored.response_attempted_at = now - timedelta(days=2)

    removed = repository.purge_terminal_tasks(
        retention_seconds=repository.terminal_retention_seconds,
        now=now,
    )

    assert removed == 0
    assert repository.get_task(generation_request.owner_key, task.id) is not None


def test_acknowledge_rejects_non_success_task(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    task = repository.enqueue_task(generation_request).task

    with pytest.raises(TaskStateConflict, match="successful"):
        repository.acknowledge(generation_request.owner_key, task.id)


def test_response_attempt_cannot_regress_acknowledged_delivery(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    repository.complete_job(
        claim,
        _artifact(claim, "delivery"),
        {"url": "https://images.example/delivery.png", "width": 64, "height": 32},
    )
    repository.acknowledge(generation_request.owner_key, task.id)

    attempted = repository.mark_response_attempted(generation_request.owner_key, task.id)

    assert attempted is not None
    assert attempted.delivery_status.value == "acknowledged"


def test_delete_public_final_artifact_removes_ready_success_artifact_index(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    artifact = _artifact(claim, "gallery-delete")
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 64, "height": 32},
    )

    descriptor = repository.public_final_artifact_descriptor(artifact.relative_path)
    assert descriptor is not None
    assert descriptor.relative_path == artifact.relative_path

    assert repository.delete_public_final_artifact(artifact.relative_path) is True

    assert repository.public_final_artifact_descriptor(artifact.relative_path) is None
    assert repository.is_public_final_artifact(artifact.relative_path) is False
    assert [
        item.relative_path
        for item in repository.list_artifacts(task.id)
        if item.relative_path == artifact.relative_path
    ] == []
    refreshed = repository.get_task(generation_request.owner_key, task.id)
    assert refreshed is not None
    assert refreshed.data == []
    with pytest.raises(TaskStateConflict, match="deliverable image result is no longer available"):
        repository.acknowledge(generation_request.owner_key, task.id)


def test_invalidate_public_final_artifact_keeps_cleanup_work_for_owner_worker(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    artifact = _artifact(claim, "cluster-delete")
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 64, "height": 32},
    )

    invalidated = repository.invalidate_public_final_artifact(artifact.relative_path)

    assert invalidated is not None
    assert invalidated.status == ArtifactStatus.INVALID
    assert repository.is_public_final_artifact(artifact.relative_path) is False
    assert [
        item.relative_path
        for item in repository.list_invalid_artifacts(worker_id="worker-1")
    ] == [artifact.relative_path]
    assert repository.list_invalid_artifacts(worker_id="worker-2") == []
    refreshed = repository.get_task(generation_request.owner_key, task.id)
    assert refreshed is not None
    assert refreshed.data == []
    with pytest.raises(TaskStateConflict, match="deliverable image result is no longer available"):
        repository.acknowledge(generation_request.owner_key, task.id)


def test_release_after_cancel_keeps_running_job_canceled(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    repository.request_cancel(generation_request.owner_key, task.id)

    assert repository.release_claim(claim) is True

    job = repository.list_jobs(task.id)[0]
    snapshot = repository.get_task(generation_request.owner_key, task.id)
    assert job.status == JobStatus.CANCELED
    assert job.stage == JobStage.CANCELED
    assert snapshot is not None and snapshot.status == TaskStatus.CANCELED


def test_cancel_does_not_regress_successful_task(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    repository.complete_job(
        claim,
        _artifact(claim, "done"),
        {"url": "https://images.example/done.png", "width": 64, "height": 32},
    )

    canceled = repository.request_cancel(generation_request.owner_key, task.id)

    assert canceled is not None and canceled.status == TaskStatus.SUCCESS
    assert repository.list_jobs(task.id)[0].status == JobStatus.SUCCESS


def test_claim_records_first_attempt_before_success(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)

    assert claim is not None
    assert claim.job.generate_attempts == 1
    leased_event = next(
        event for event in repository.logical_backup()["events"]
        if event["event_type"] == "job_leased"
    )
    assert leased_event["attempt"] == 1

    completed = repository.complete_job(
        claim,
        _artifact(claim, "attempt"),
        {"url": "https://images.example/attempt.png", "width": 64, "height": 32},
    )
    assert completed is not None and completed.status == TaskStatus.SUCCESS
    assert repository.list_jobs(task.id)[0].generate_attempts == 1
    assert repository.acknowledge(generation_request.owner_key, task.id).delivery_status.value == "acknowledged"


def test_account_capacity_leaves_second_job_queued_without_claim(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(replace(generation_request, required_jobs=2))

    first = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    second = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)

    assert first is not None
    assert second is None
    jobs = repository.list_jobs(first.job.task_id)
    assert [job.status for job in jobs] == [JobStatus.LEASED, JobStatus.QUEUED]


def test_task_succeeds_only_after_every_job_succeeds(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(replace(generation_request, required_jobs=2))
    first = repository.claim_next_job("worker-1", account_candidates, 1, FIXED_NOW)
    second = repository.claim_next_job("worker-1", account_candidates, 1, FIXED_NOW)
    assert first is not None and second is not None

    after_first = repository.complete_job(
        first,
        _artifact(first, "1"),
        {"url": "https://images.example/1.png", "width": 64, "height": 32},
    )
    after_second = repository.complete_job(
        second,
        _artifact(second, "2"),
        {"url": "https://images.example/2.png", "width": 64, "height": 32},
    )

    assert after_first is not None and after_first.status == TaskStatus.RUNNING
    assert after_second is not None and after_second.status == TaskStatus.SUCCESS
    assert [item["url"] for item in after_second.data] == [
        "https://images.example/1.png",
        "https://images.example/2.png",
    ]


def test_expired_lease_cannot_overwrite_reclaimed_job(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    expired_claim = repository.claim_next_job("worker-old", account_candidates[:1], 1, FIXED_NOW)
    assert expired_claim is not None

    reclaimed = repository.reclaim_expired_leases(FIXED_NOW + timedelta(seconds=91))
    replacement = repository.claim_next_job(
        "worker-new",
        account_candidates[:1],
        1,
        FIXED_NOW + timedelta(seconds=91),
    )
    assert reclaimed == 1
    assert replacement is not None
    assert replacement.lease_version > expired_claim.lease_version

    stale = repository.complete_job(
        expired_claim,
        _artifact(expired_claim, "a"),
        {"url": "https://images.example/stale.png"},
    )
    current = repository.complete_job(
        replacement,
        _artifact(replacement, "b"),
        {"url": "https://images.example/current.png", "width": 64, "height": 32},
    )

    assert stale is None
    assert current is not None and current.status == TaskStatus.SUCCESS
    assert current.data[0]["url"] == "https://images.example/current.png"


def test_checkpoint_requires_current_fencing_token(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None
    checkpoint = JobCheckpoint(
        stage=JobStage.DOWNLOADING,
        conversation_id="conversation-1",
        image_urls=["https://upstream.example/image.png"],
    )

    assert repository.checkpoint_job(claim, checkpoint) is True
    repository.reclaim_expired_leases(FIXED_NOW + timedelta(seconds=91))
    assert repository.checkpoint_job(claim, checkpoint) is False


def test_remote_checkpoint_retry_keeps_original_account(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    original = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert original is not None
    repository.checkpoint_job(
        original,
        JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="conversation-owned-by-account-1"),
    )
    repository.schedule_retry(
        original,
        error_code="image_poll_timeout",
        error_message="temporary",
        next_retry_at=FIXED_NOW - timedelta(days=1),
    )

    resumed = repository.claim_next_job(
        "worker-2",
        [account_candidates[1], account_candidates[0]],
        1,
        FIXED_NOW,
    )

    assert resumed is not None
    assert resumed.account_id == account_candidates[0].account_id
    assert resumed.job.account_id == account_candidates[0].account_id


def test_missing_recovery_account_does_not_block_later_jobs(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    first_task = repository.enqueue_task(generation_request).task
    first = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert first is not None
    repository.checkpoint_job(
        first,
        JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="missing-account-conversation"),
    )
    repository.schedule_retry(
        first,
        error_code="image_poll_timeout",
        error_message="temporary",
        next_retry_at=FIXED_NOW - timedelta(days=1),
    )
    second_request = replace(
        generation_request,
        idempotency_key="request-2",
        client_task_id="client-2",
        request_hash="b" * 64,
    )
    second_task = repository.enqueue_task(second_request).task

    claimed = repository.claim_next_job("worker-2", account_candidates[1:], 1, FIXED_NOW)

    assert claimed is not None and claimed.job.task_id == second_task.id
    blocked = repository.get_task(generation_request.owner_key, first_task.id)
    assert blocked is not None and blocked.wait_reason == "recovery_account"


def test_restricted_recovery_jobs_do_not_starve_later_claimable_job(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(replace(generation_request, required_jobs=101)).task
    missing_account_id = uuid4()
    with repository.database.session() as session:
        jobs = session.query(ImageJob).filter(ImageJob.task_id == task.id).order_by(ImageJob.ordinal).all()
        for job in jobs[:100]:
            job.stage = JobStage.RESOLVING.value
            job.account_id = missing_account_id
            job.conversation_id = f"missing-account-{job.ordinal}"

    claimed = repository.claim_next_job(
        "worker-fair-scan",
        account_candidates[:1],
        1,
        FIXED_NOW,
    )

    assert claimed is not None
    assert claimed.job.ordinal == 101


def test_signed_image_url_can_resume_without_original_account(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    original = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert original is not None
    repository.checkpoint_job(
        original,
        JobCheckpoint(
            stage=JobStage.DOWNLOADING,
            image_urls=["https://signed.example/image.png?signature=valid"],
        ),
    )
    repository.schedule_retry(
        original,
        error_code="image_download_failed",
        error_message="temporary",
        next_retry_at=FIXED_NOW - timedelta(seconds=1),
    )

    resumed = repository.claim_next_job("worker-download", [], 1, FIXED_NOW)

    assert resumed is not None
    assert resumed.account_id == original.account_id
    assert resumed.account_slot == -2


def test_ordinary_job_without_account_does_not_block_later_signed_url_recovery(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    repository.enqueue_task(generation_request)
    recovery_request = replace(
        generation_request,
        idempotency_key="signed-recovery-behind-ordinary",
        client_task_id="signed-recovery-behind-ordinary",
        request_hash="c" * 64,
    )
    recovery_task = repository.enqueue_task(recovery_request).task
    missing_account_id = uuid4()
    with repository.database.session() as session:
        recovery_job = session.query(ImageJob).filter(ImageJob.task_id == recovery_task.id).one()
        recovery_job.stage = JobStage.DOWNLOADING.value
        recovery_job.account_id = missing_account_id
        recovery_job.image_urls = ["https://signed.example/recovered.png?signature=valid"]

    claimed = repository.claim_next_job("worker-recovery", [], 1, FIXED_NOW)

    assert claimed is not None
    assert claimed.job.task_id == recovery_task.id
    assert claimed.account_slot == -2


def test_missing_recovery_account_reaches_failed_terminal_state(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates[:1], 1)
    assert claim is not None
    repository.checkpoint_job(
        claim,
        JobCheckpoint(stage=JobStage.RESOLVING, conversation_id="lost-account-conversation"),
    )
    repository.schedule_retry(
        claim,
        error_code="image_poll_timeout",
        error_message="temporary",
        next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    repository.recovery_account_timeout_seconds = 60

    repository.claim_next_job(
        "worker-2",
        [],
        1,
        datetime.now(timezone.utc) + timedelta(seconds=61),
    )

    failed = repository.get_task(generation_request.owner_key, task.id)
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert failed.error_code == "recovery_account_unavailable"


def test_retry_releases_account_slot_for_another_job(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(replace(generation_request, required_jobs=2))
    first = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert first is not None

    task = repository.schedule_retry(
        first,
        error_code="upstream_unavailable",
        error_message="temporary",
        next_retry_at=FIXED_NOW + timedelta(seconds=5),
    )
    second = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)

    assert task is not None and task.status == TaskStatus.RETRYING
    assert second is not None and second.job.ordinal == 2


def test_full_account_capacity_does_not_scan_entire_queue(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(20):
        repository.enqueue_task(replace(
            generation_request,
            idempotency_key=f"capacity-{index}",
            client_task_id=f"capacity-{index}",
            request_hash=f"{index + 700:064x}"[-64:],
        ))
    occupied = repository.claim_next_job("worker-1", account_candidates[:1], 1)
    assert occupied is not None
    calls = 0
    original = repository._acquire_account_slot

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "_acquire_account_slot", counted)

    assert repository.claim_next_job("worker-2", account_candidates[:1], 1) is None
    assert calls == 1


def test_cancel_keeps_completed_artifacts_and_stops_queued_jobs(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(replace(generation_request, required_jobs=2))
    first = repository.claim_next_job("worker-1", account_candidates[:1], 1, FIXED_NOW)
    assert first is not None
    completed = repository.complete_job(
        first,
        _artifact(first, "c"),
        {"url": "https://images.example/c.png", "width": 64, "height": 32},
    )
    assert completed is not None

    canceled = repository.request_cancel(generation_request.owner_key, completed.id)

    assert canceled is not None and canceled.status == TaskStatus.CANCELED
    artifacts = repository.list_artifacts(completed.id)
    assert len(artifacts) == 1
    assert isinstance(artifacts[0].status, ArtifactStatus)
    jobs = repository.list_jobs(completed.id)
    assert jobs[0].status == JobStatus.SUCCESS
    assert jobs[1].status == JobStatus.CANCELED


def test_expired_running_job_stays_canceled_after_worker_crash(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-crashed", account_candidates[:1], 1, FIXED_NOW)
    assert claim is not None

    canceled = repository.request_cancel(generation_request.owner_key, task.id)
    reclaimed = repository.reclaim_expired_leases(FIXED_NOW + timedelta(seconds=91))
    job = repository.list_jobs(task.id)[0]

    assert canceled is not None and canceled.status == TaskStatus.CANCELED
    assert reclaimed == 1
    assert job.status == JobStatus.CANCELED
    assert job.stage == JobStage.CANCELED
    assert repository.claim_next_job(
        "worker-new",
        account_candidates[:1],
        1,
        FIXED_NOW + timedelta(seconds=91),
    ) is None


def test_failed_multi_image_task_can_acknowledge_partial_artifact_delivery(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(replace(generation_request, required_jobs=2))
    first = repository.claim_next_job("worker-1", account_candidates, 1, FIXED_NOW)
    second = repository.claim_next_job("worker-1", account_candidates, 1, FIXED_NOW)
    assert first is not None and second is not None
    successful_artifact = _artifact(first, "partial")
    repository.complete_job(
        first,
        successful_artifact,
        {"url": successful_artifact.public_url, "width": 64, "height": 32},
    )
    assert repository.mark_quota_accounted(first.job.id, account_candidates[0].account_id)
    failed = repository.fail_job(
        second,
        error_code="no_image_generated",
        error_message="no image",
    )

    assert failed is not None and failed.status == TaskStatus.FAILED
    assert successful_artifact.relative_path in repository.protected_artifact_paths()
    acknowledged = repository.acknowledge(generation_request.owner_key, failed.id)
    assert acknowledged is not None
    assert successful_artifact.relative_path not in repository.protected_artifact_paths()


def test_unacknowledged_standard_result_has_bounded_protection(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.delivery_grace_seconds = 60
    repository.terminal_retention_seconds = 60
    task = repository.enqueue_task(generation_request).task
    claim = repository.claim_next_job("worker-1", account_candidates, 1)
    assert claim is not None
    artifact = _artifact(claim, "bounded")
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": 64, "height": 32},
    )
    assert repository.mark_quota_accounted(claim.job.id, account_candidates[0].account_id)
    repository.mark_response_attempted(generation_request.owner_key, task.id)
    with repository.database.session() as session:
        stored = session.get(ImageTask, task.id)
        assert stored is not None
        stored.completed_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        stored.response_attempted_at = stored.completed_at

    assert artifact.relative_path not in repository.protected_artifact_paths()


def test_local_recovery_artifact_can_be_claimed_without_an_account(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    original = repository.claim_next_job("worker-old", account_candidates[:1], 1, FIXED_NOW)
    assert original is not None
    upscaled = replace(
        _artifact(original, "local"),
        kind="upscaled",
        relative_path=f"{original.job.task_id}/{original.job.id}/u/local.png",
    )
    assert repository.record_artifact(original, upscaled) is True
    assert repository.checkpoint_job(original, JobCheckpoint(stage=JobStage.SAVING)) is True
    assert repository.reclaim_expired_leases(FIXED_NOW + timedelta(seconds=91)) == 1

    recovered = repository.claim_next_job(
        "worker-new",
        [],
        1,
        FIXED_NOW + timedelta(seconds=91),
    )

    assert recovered is not None
    assert recovered.account_slot == -1
    assert recovered.job.stage == JobStage.SAVING


def test_task_listing_is_paginated_and_batches_job_loading(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    from sqlalchemy import event

    for index in range(5):
        repository.enqueue_task(replace(
            generation_request,
            idempotency_key=f"listed-{index}",
            client_task_id=f"listed-{index}",
            request_hash=f"{index + 900:064x}"[-64:],
        ))
    statements = []

    def record_statement(*args):
        statements.append(args[2])

    event.listen(repository.database.engine, "before_cursor_execute", record_statement)
    try:
        tasks = repository.list_tasks(generation_request.owner_key, limit=2, offset=0)
    finally:
        event.remove(repository.database.engine, "before_cursor_execute", record_statement)

    assert len(tasks) == 2
    selects = [item for item in statements if str(item).lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 2


def test_owner_cannot_read_another_owners_task(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    task = repository.enqueue_task(generation_request).task

    assert repository.get_task("other-owner", task.id) is None


def test_queue_snapshot_reports_percentiles_and_worker_heartbeat(
    repository: ImageQueueRepository,
    generation_request: EnqueueRequest,
) -> None:
    repository.enqueue_task(generation_request)
    repository.update_worker_state(
        "worker-1",
        resource_snapshot={"cpu_percent": 42.0},
        effective_concurrency=3,
        pause_reason=None,
    )

    snapshot = repository.queue_snapshot()

    assert snapshot["queued"] == 1
    assert snapshot["queue_wait_p90_seconds"] == 0
    assert snapshot["duration_p90_seconds"] == 0
    assert snapshot["workers"][0]["worker_id"] == "worker-1"
    assert snapshot["workers"][0]["effective_concurrency"] == 3
    assert snapshot["workers"][0]["heartbeat_at"]
    assert snapshot["workers"][0]["resource_snapshot"]["cpu_percent"] == 42.0


def test_worker_heartbeat_prunes_stale_worker_rows(repository: ImageQueueRepository) -> None:
    with repository.database.session() as session:
        session.add(ImageWorkerState(
            worker_id="stale-worker",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1),
            resource_snapshot={"cpu_percent": 99.0},
            effective_concurrency=0,
        ))

    repository.update_worker_state(
        "fresh-worker",
        resource_snapshot={"cpu_percent": 12.0},
        effective_concurrency=2,
    )

    snapshot = repository.queue_snapshot()
    assert [item["worker_id"] for item in snapshot["workers"]] == ["fresh-worker"]
