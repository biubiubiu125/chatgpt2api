from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import time
from threading import Event
from threading import current_thread
from uuid import UUID

from services.image_failure import ImageGenerationError, image_failure
from services.cluster_settings import ClusterSettings
from services.image_queue.retry_policy import RetryPolicy
from services.image_queue.settings import ImageQueueSettings
import services.returned_url_verifier as returned_url_verifier
from services.image_queue.types import (
    ArtifactDescriptor,
    ArtifactStatus,
    ImageAccountCandidate,
    JobCheckpoint,
    ResourceDecision,
    ResourceSnapshot,
    JobStage,
    JobStatus,
    TaskStatus,
)
from services.image_queue import types as queue_types
from services.image_queue.worker import ImageWorkerManager
import pytest


class AlwaysAllowedResources:
    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            cpu_percent=10,
            available_memory_bytes=8 * 1024**3,
            memory_limit_bytes=16 * 1024**3,
            swap_in_bytes_per_second=0,
            swap_out_bytes_per_second=0,
            thread_count=10,
            file_handle_count=10,
            database_pool_percent=10,
            disk_free_bytes=20 * 1024**3,
            disk_free_percent=20,
            sampled_at=datetime.now(timezone.utc),
        )

    def allow_new_generation(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        return ResourceDecision(True, effective_limit=64)


class FakeAccounts:
    def __init__(self) -> None:
        self.candidate = ImageAccountCandidate(
            account_id=UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token-1",
        )

    def list_image_account_candidates(self):
        return [self.candidate]

    def prepare_image_account(self, account_id):
        return "token-1"

    def get_stats(self):
        return {
            "total_quota": 0,
            "unlimited_quota_count": 0,
            "unknown_quota_count": 0,
        }

    def record_managed_image_result(self, *args, **kwargs):
        return {"recorded": True}


def _complete_url_only_job(repository, claim):
    relative_path = f"{claim.job.task_id}/{claim.job.id}/{'a' * 64}.png"
    artifact = ArtifactDescriptor(
        task_id=claim.job.task_id,
        job_id=claim.job.id,
        kind="final",
        status=ArtifactStatus.READY,
        relative_path=relative_path,
        sha256="a" * 64,
        mime_type="image/png",
        byte_size=10,
        width=4,
        height=3,
        public_url="https://example.test/image.png",
    )
    repository.complete_job(
        claim,
        artifact,
        {
            "url": "https://example.test/image.png",
            "width": 4,
            "height": 3,
            "relative_path": relative_path,
            "delivery_mode": "node_url",
        },
    )


def test_worker_constructor_rejects_image_base_url_hostnames_resolving_to_private_addresses(
    repository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHATGPT2API_IMAGE_BASE_URL", "https://internal.example/images")
    monkeypatch.setattr(
        returned_url_verifier.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("127.0.0.1", 443)),
        ],
    )

    settings = ImageQueueSettings(database_url="sqlite://")

    with pytest.raises(ValueError, match="private or local address"):
        ImageWorkerManager(
            repository,
            FakeAccounts(),
            lambda claim, token: None,
            settings,
            resource_controller=AlwaysAllowedResources(),
        )


def test_thousand_queued_jobs_do_not_create_thousand_threads(
    repository,
    generation_request,
) -> None:
    for index in range(1000):
        repository.enqueue_task(replace(
            generation_request,
            idempotency_key=f"load-{index}",
            client_task_id=f"load-{index}",
            request_hash=f"{index:064x}"[-64:],
        ))
    release = Event()

    def execute(claim, token):
        release.wait(5)
        repository.release_claim(claim)

    settings = ImageQueueSettings(
        database_url="sqlite://",
        poll_interval_seconds=0.01,
        heartbeat_seconds=5,
    )
    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        settings,
        resource_controller=AlwaysAllowedResources(),
        account_concurrency=100,
    )
    manager.start()
    manager.wait_until_saturated(10)

    assert manager.executor_thread_count <= manager.internal_thread_cap
    assert manager.internal_thread_cap < 1000

    release.set()
    manager.stop(10)


def test_specialized_work_runs_in_separate_bounded_pools(repository) -> None:
    settings = ImageQueueSettings(database_url="sqlite://")
    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        lambda claim, token: None,
        settings,
        resource_controller=AlwaysAllowedResources(),
    )

    names = {
        "io": manager.submit_io(lambda: current_thread().name).result(timeout=2),
        "upscale": manager.submit_upscale(lambda: current_thread().name).result(timeout=2),
        "register": manager.submit_registration(lambda: current_thread().name).result(timeout=2),
    }

    assert names["io"].startswith("image-io")
    assert names["upscale"].startswith("image-upscale")
    assert names["register"].startswith("image-register")
    assert sum(manager.pool_limits.values()) <= settings.absolute_guard
    manager.stop(2)


def test_generation_pool_can_scale_above_legacy_fixed_cap(repository) -> None:
    class LargeHostResources(AlwaysAllowedResources):
        @staticmethod
        def cpu_limit_cores():
            return 128

    settings = ImageQueueSettings(
        database_url="sqlite://",
        absolute_guard=256,
        generation_concurrency_limit=128,
        generation_concurrency_hard_cap=128,
    )
    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        lambda claim, token: None,
        settings,
        resource_controller=LargeHostResources(),
    )

    assert manager.internal_thread_cap > 64
    assert sum(manager.pool_limits.values()) <= settings.absolute_guard
    manager.stop(2)


def test_auth_failure_retries_with_a_different_healthy_account(
    repository,
    generation_request,
) -> None:
    first_account = ImageAccountCandidate(
        account_id=UUID("10000000-0000-0000-0000-000000000001"),
        access_token="expired",
    )
    second_account = ImageAccountCandidate(
        account_id=UUID("10000000-0000-0000-0000-000000000002"),
        access_token="healthy",
    )
    repository.enqueue_task(generation_request)
    first = repository.claim_next_job("worker-1", [first_account, second_account], 1)
    assert first is not None and first.account_id == first_account.account_id
    error = ImageGenerationError(
        "token expired",
        failure=image_failure("auth_invalid", raw_detail="token expired"),
    )
    decision = RetryPolicy(ImageQueueSettings(database_url="sqlite://")).decision(
        first.job.stage,
        1,
        error,
        datetime.now(timezone.utc),
    )
    assert decision.retry is True
    repository.schedule_retry(
        first,
        error_code=decision.error_code,
        error_message=decision.error_message,
        next_retry_at=datetime.now(timezone.utc),
    )

    second = repository.claim_next_job("worker-2", [first_account, second_account], 1)

    assert second is not None
    assert second.account_id == second_account.account_id


def test_local_recovery_claim_skips_account_token_preparation(
    repository,
    generation_request,
    account_candidates,
) -> None:
    repository.enqueue_task(generation_request)
    original = repository.claim_next_job("worker-old", account_candidates[:1], 1)
    assert original is not None
    assert repository.record_artifact(original, ArtifactDescriptor(
        task_id=original.job.task_id,
        job_id=original.job.id,
        kind="upscaled",
        status=ArtifactStatus.READY,
        relative_path=f"{original.job.task_id}/{original.job.id}/u/local.png",
        sha256="a" * 64,
        mime_type="image/png",
        byte_size=10,
        width=4,
        height=3,
    )) is True
    assert repository.checkpoint_job(original, JobCheckpoint(stage=JobStage.SAVING)) is True
    repository.reclaim_expired_leases(original.lease_expires_at)
    local_claim = repository.claim_next_job("worker-local", [], 1, original.lease_expires_at)
    assert local_claim is not None and local_claim.account_slot == -1

    class NoAccounts(FakeAccounts):
        def prepare_image_account(self, account_id):
            raise AssertionError("local recovery requested an account token")

    received = []
    manager = ImageWorkerManager(
        repository,
        NoAccounts(),
        lambda claim, token: (received.append(token), repository.release_claim(claim)),
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(local_claim)

    assert received == [""]
    manager.stop(2)


def test_dispatch_adopts_local_crash_artifact_before_listing_accounts(
    repository,
    generation_request,
    account_candidates,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "services.image_queue.worker.load_cluster_settings",
        lambda *args, **kwargs: ClusterSettings(),
    )
    repository.lease_seconds = 30
    repository.enqueue_task(generation_request)
    original = repository.claim_next_job("worker-before-crash", account_candidates[:1], 1)
    assert original is not None
    assert repository.checkpoint_job(original, JobCheckpoint(stage=JobStage.SAVING)) is True
    repository.reclaim_expired_leases(original.lease_expires_at)
    recovered = ArtifactDescriptor(
        task_id=original.job.task_id,
        job_id=original.job.id,
        kind="final",
        status=ArtifactStatus.READY,
        relative_path=f"{original.job.task_id}/{original.job.id}/{'b' * 64}.png",
        sha256="b" * 64,
        mime_type="image/png",
        byte_size=10,
        width=4,
        height=3,
        storage_backend="private_local",
    )
    callback_calls: list[bool] = []
    executed = Event()

    class NoAccounts(FakeAccounts):
        def list_image_account_candidates(self):
            assert callback_calls
            return []

        def prepare_image_account(self, account_id):
            raise AssertionError("local recovery requested an account token")

    def adopt_local() -> int:
        callback_calls.append(True)
        return 0

    def execute(claim, token):
        assert token == ""
        executed.set()
        repository.release_claim(claim)

    assert repository.adopt_recovery_artifact(original.job.id, recovered) is True
    assert (
        repository.requeue_job_for_recovery(
            original.job.id,
            JobStage.SAVING,
            now=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        is True
    )
    manager = ImageWorkerManager(
        repository,
        NoAccounts(),
        execute,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
        local_recovery_callback=adopt_local,
        local_recovery_available_callback=lambda job, artifacts: bool(artifacts),
    )

    manager._dispatch_once()
    manager._dispatch_once()

    assert executed.wait(2)
    manager.stop(2)


def test_successful_job_is_not_reclassified_when_account_bookkeeping_fails(
    repository,
    generation_request,
) -> None:
    task = repository.enqueue_task(generation_request).task

    class BookkeepingFailure(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.failure_calls = 0

        def record_managed_image_result(self, account_id, *, success, **kwargs):
            if success:
                raise RuntimeError("bookkeeping unavailable")
            self.failure_calls += 1

    accounts = BookkeepingFailure()
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)

    def execute(completed_claim, token):
        repository.complete_job(
            completed_claim,
            ArtifactDescriptor(
                task_id=completed_claim.job.task_id,
                job_id=completed_claim.job.id,
                kind="final",
                status=ArtifactStatus.READY,
                relative_path=f"{completed_claim.job.task_id}/{completed_claim.job.id}/{'a' * 64}.png",
                sha256="a" * 64,
                mime_type="image/png",
                byte_size=10,
                width=4,
                height=3,
                public_url="https://example.test/image.png",
            ),
            {"url": "https://example.test/image.png", "width": 4, "height": 3},
        )

    manager = ImageWorkerManager(
        repository,
        accounts,
        execute,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    assert repository.get_task("owner-1", task.id).status == TaskStatus.SUCCESS
    assert accounts.failure_calls == 0
    manager.stop(2)


def test_successful_url_only_job_is_accounted_once(
    repository,
    generation_request,
) -> None:
    class RecordingAccounts(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.results = []

        def record_managed_image_result(self, account_id, *, success, **kwargs):
            self.results.append(kwargs | {"success": success})
            return {"recorded": True}

    accounts = RecordingAccounts()
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)
    assert claim is not None

    def execute(completed_claim, token):
        _complete_url_only_job(repository, completed_claim)

    manager = ImageWorkerManager(
        repository,
        accounts,
        execute,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    assert len(accounts.results) == 1
    assert accounts.results[0]["success"] is True
    assert accounts.results[0]["quota_consumed"] is True
    persisted = repository.get_job(claim.job.id)
    assert persisted is not None and persisted.quota_accounted is True
    manager.stop(2)


def test_reconcile_unaccounted_url_only_success_is_accounted(
    repository,
    generation_request,
) -> None:
    class RecordingAccounts(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.results = []

        def record_managed_image_result(self, account_id, *, success, **kwargs):
            self.results.append(kwargs | {"success": success})
            return {"recorded": True}

    accounts = RecordingAccounts()
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)
    assert claim is not None
    _complete_url_only_job(repository, claim)

    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda completed_claim, token: None,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._reconcile_unaccounted_quotas()

    assert len(accounts.results) == 1
    assert accounts.results[0]["success"] is True
    assert accounts.results[0]["quota_consumed"] is True
    persisted = repository.get_job(claim.job.id)
    assert persisted is not None and persisted.quota_accounted is True
    manager.stop(2)


def test_canceled_running_job_does_not_penalize_account(
    repository,
    generation_request,
) -> None:
    task = repository.enqueue_task(generation_request).task

    class RecordingAccounts(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.results = []

        def record_managed_image_result(self, *args, **kwargs):
            self.results.append((args, kwargs))

    accounts = RecordingAccounts()
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)
    assert claim is not None

    def execute(running_claim, token):
        repository.request_cancel(generation_request.owner_key, task.id)
        raise RuntimeError("image task was canceled")

    manager = ImageWorkerManager(
        repository,
        accounts,
        execute,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    assert accounts.results == []
    assert repository.list_jobs(task.id)[0].status.value == "canceled"
    manager.stop(2)


def test_worker_reclaims_lease_that_expires_after_startup(
    repository,
    generation_request,
) -> None:
    repository.lease_seconds = 0.2
    repository.enqueue_task(generation_request)
    accounts = FakeAccounts()
    old_claim = repository.claim_next_job(
        "worker-before-restart",
        accounts.list_image_account_candidates(),
        1,
    )
    assert old_claim is not None
    executed = Event()

    def execute(claim, token):
        executed.set()
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        accounts,
        execute,
        ImageQueueSettings(
            database_url="sqlite://",
            poll_interval_seconds=0.01,
            heartbeat_seconds=0.05,
        ),
        resource_controller=AlwaysAllowedResources(),
    )
    manager.start()
    try:
        assert executed.wait(2)
    finally:
        manager.stop(2)


def test_dispatch_loop_survives_transient_resource_sampling_error(
    repository,
    generation_request,
) -> None:
    repository.enqueue_task(generation_request)
    executed = Event()

    class FlakyResources(AlwaysAllowedResources):
        def __init__(self) -> None:
            self.calls = 0

        def sample(self) -> ResourceSnapshot:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("resource sampler unavailable")
            return super().sample()

    def execute(claim, token):
        executed.set()
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=FlakyResources(),
    )
    manager.start()
    try:
        assert executed.wait(3)
    finally:
        manager.stop(2)


def test_heartbeat_loop_survives_transient_database_error(
    repository,
    generation_request,
    monkeypatch,
) -> None:
    repository.enqueue_task(generation_request)
    started = Event()
    release = Event()
    heartbeat_retried = Event()
    original_heartbeat = repository.heartbeat_claims
    heartbeat_calls = 0

    def flaky_heartbeat(*args, **kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            raise RuntimeError("database temporarily unavailable")
        heartbeat_retried.set()
        return original_heartbeat(*args, **kwargs)

    monkeypatch.setattr(repository, "heartbeat_claims", flaky_heartbeat)

    def execute(claim, token):
        started.set()
        release.wait(3)
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        ImageQueueSettings(
            database_url="sqlite://",
            poll_interval_seconds=0.01,
            heartbeat_seconds=0.05,
        ),
        resource_controller=AlwaysAllowedResources(),
    )
    manager.start()
    try:
        assert started.wait(1)
        assert heartbeat_retried.wait(1)
    finally:
        release.set()
        manager.stop(2)


def test_expired_lease_is_not_reclaimed_while_same_worker_is_still_executing(
    repository,
    generation_request,
) -> None:
    repository.lease_seconds = 0.2
    repository.enqueue_task(generation_request)
    release = Event()
    first_started = Event()
    duplicate_started = Event()
    calls = []

    def execute(claim, token):
        calls.append((claim.job.id, claim.lease_version))
        if len(calls) == 1:
            first_started.set()
        else:
            duplicate_started.set()
        release.wait(2)
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        ImageQueueSettings(
            database_url="sqlite://",
            poll_interval_seconds=0.01,
            heartbeat_seconds=0.05,
        ),
        resource_controller=AlwaysAllowedResources(),
    )
    repository.heartbeat_claims = lambda *args, **kwargs: 0
    manager.start()
    try:
        assert first_started.wait(1)
        assert duplicate_started.wait(0.7) is False
        assert len(calls) == 1
    finally:
        release.set()
        manager.stop(2)


def test_claim_over_max_runtime_is_no_longer_heartbeated_or_protected(
    repository,
    generation_request,
) -> None:
    repository.lease_seconds = 0.2
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job(
        "worker-overdue",
        FakeAccounts().list_image_account_candidates(),
        1,
    )
    assert claim is not None
    future: Future[None] = Future()
    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        lambda _claim, _token: None,
        ImageQueueSettings(
            database_url="sqlite://",
            claim_max_runtime_seconds=1,
        ),
        resource_controller=AlwaysAllowedResources(),
    )
    with manager._lock:
        manager._futures[future] = claim
        manager._claim_started_at[claim.job.id] = time.monotonic() - 2

    assert manager._active_claims() == []
    assert repository.reclaim_expired_leases(
        now=claim.lease_expires_at,
        protected_claims=manager._active_claims(),
    ) == 1

    future.cancel()
    manager.stop(1)


def test_worker_does_not_exceed_resource_target_concurrency(
    repository,
    generation_request,
) -> None:
    for index in range(3):
        repository.enqueue_task(replace(
            generation_request,
            idempotency_key=f"target-{index}",
            client_task_id=f"target-{index}",
            request_hash=f"{index + 100:064x}"[-64:],
        ))
    started = 0
    first_started = Event()
    release = Event()

    class OneSlotResources(AlwaysAllowedResources):
        def allow_new_generation(self, snapshot: ResourceSnapshot) -> ResourceDecision:
            return ResourceDecision(True, effective_limit=1)

    def execute(claim, token):
        nonlocal started
        started += 1
        first_started.set()
        release.wait(2)
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=OneSlotResources(),
        account_concurrency=3,
    )
    manager.start()
    try:
        assert first_started.wait(1)
        Event().wait(0.1)
        assert started == 1
    finally:
        release.set()
        manager.stop(2)


def test_postprocessing_job_releases_generation_capacity(
    repository,
    generation_request,
) -> None:
    class OneGenerationSlot(AlwaysAllowedResources):
        def allow_new_generation(self, snapshot: ResourceSnapshot) -> ResourceDecision:
            return ResourceDecision(True, effective_limit=1)

    repository.enqueue_task(replace(generation_request, required_jobs=2))
    accounts = FakeAccounts()
    first = repository.claim_next_job(
        "worker-1",
        accounts.list_image_account_candidates(),
        2,
    )
    assert first is not None
    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda claim, token: repository.release_claim(claim),
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=OneGenerationSlot(),
        account_concurrency=2,
    )
    pending = Future()
    with manager._lock:
        manager._futures[pending] = first
    manager.note_claim_stage(first, JobStage.SAVING)

    manager._dispatch_once()

    with manager._lock:
        assert len(manager._futures) == 2
    pending.cancel()
    manager.stop(2)


def test_postprocessing_saturation_keeps_a_separate_generation_window(
    repository,
    generation_request,
    monkeypatch,
) -> None:
    monkeypatch.setattr("services.image_queue.worker.os.cpu_count", lambda: 1)
    repository.enqueue_task(replace(generation_request, required_jobs=5))
    accounts = FakeAccounts()
    claims = [
        repository.claim_next_job(
            f"worker-{index}",
            accounts.list_image_account_candidates(),
            5,
        )
        for index in range(4)
    ]
    assert all(claim is not None for claim in claims)
    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda claim, token: repository.release_claim(claim),
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=AlwaysAllowedResources(),
        account_concurrency=5,
    )
    pending = [Future() for _ in claims]
    with manager._lock:
        manager._futures.update(dict(zip(pending, claims)))
    for claim in claims:
        manager.note_claim_stage(claim, JobStage.SAVING)

    manager._dispatch_once()

    with manager._lock:
        assert len(manager._futures) == 5
    for future in pending:
        future.cancel()
    manager.stop(2)


def test_failure_after_upstream_image_marks_quota_consumed_once(
    repository,
    generation_request,
) -> None:
    class RecordingAccounts(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.results = []

        def record_managed_image_result(self, account_id, **kwargs):
            self.results.append(kwargs)
            return {"recorded": True}

    accounts = RecordingAccounts()
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)
    assert claim is not None

    def fail_while_saving(running_claim, token):
        repository.checkpoint_job(
            running_claim,
            JobCheckpoint(
                stage=JobStage.SAVING,
                image_urls=["https://signed.example/image.png"],
            ),
        )
        raise OSError("disk temporarily unavailable")

    manager = ImageWorkerManager(
        repository,
        accounts,
        fail_while_saving,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    assert accounts.results[0]["success"] is False
    assert accounts.results[0]["quota_consumed"] is True
    persisted = repository.get_job(claim.job.id)
    assert persisted is not None and persisted.quota_consumed is True
    assert persisted.quota_accounted is True
    manager.stop(2)


def test_quota_checkpoint_is_accounted_after_worker_crash_and_recovery(
    repository,
    generation_request,
) -> None:
    class RecordingAccounts(FakeAccounts):
        def __init__(self) -> None:
            super().__init__()
            self.results = []

        def record_managed_image_result(self, account_id, **kwargs):
            self.results.append(kwargs)
            return {"recorded": True}

    accounts = RecordingAccounts()
    repository.enqueue_task(generation_request)
    crashed = repository.claim_next_job("worker-crashed", accounts.list_image_account_candidates(), 1)
    assert crashed is not None
    repository.checkpoint_job(
        crashed,
        JobCheckpoint(
            stage=JobStage.SAVING,
            image_urls=["https://signed.example/image.png"],
        ),
    )
    recovery_time = crashed.lease_expires_at + timedelta(seconds=1)
    repository.reclaim_expired_leases(recovery_time)
    recovered = repository.claim_next_job(
        "worker-recovered",
        accounts.list_image_account_candidates(),
        1,
        recovery_time,
    )
    assert recovered is not None
    assert recovered.job.quota_consumed is True
    assert recovered.job.quota_accounted is False
    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda claim, token: repository.release_claim(claim),
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(recovered)

    assert accounts.results[0]["quota_consumed"] is True
    persisted = repository.get_job(recovered.job.id)
    assert persisted is not None and persisted.quota_accounted is True
    manager.stop(2)


def test_missing_account_keeps_quota_unaccounted_for_later_reconciliation(
    repository,
    generation_request,
) -> None:
    class MissingAccount(FakeAccounts):
        def record_managed_image_result(self, account_id, **kwargs):
            return None

    accounts = MissingAccount()
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("worker-1", accounts.list_image_account_candidates(), 1)
    assert claim is not None

    def fail_after_image(running_claim, token):
        repository.checkpoint_job(
            running_claim,
            JobCheckpoint(
                stage=JobStage.SAVING,
                image_urls=["https://signed.example/image.png"],
            ),
        )
        raise OSError("save failed")

    manager = ImageWorkerManager(
        repository,
        accounts,
        fail_after_image,
        ImageQueueSettings(database_url="sqlite://"),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    persisted = repository.get_job(claim.job.id)
    assert persisted is not None and persisted.quota_consumed is True
    assert persisted.quota_accounted is False
    manager.stop(2)


def test_stop_reports_undrained_claim_instead_of_disposing_under_it(
    repository,
    generation_request,
) -> None:
    repository.enqueue_task(generation_request)
    started = Event()
    release = Event()

    def execute(claim, token):
        started.set()
        release.wait(2)
        repository.release_claim(claim)

    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        execute,
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=AlwaysAllowedResources(),
    )
    manager.start()
    assert started.wait(1)

    assert manager.stop(0.01) is False

    release.set()
    manager.stop(2)


def test_worker_recreates_executors_after_clean_restart(repository) -> None:
    manager = ImageWorkerManager(
        repository,
        FakeAccounts(),
        lambda claim, token: None,
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=AlwaysAllowedResources(),
    )

    assert manager.stop(1) is True
    manager.start()
    try:
        assert manager.submit_io(lambda: "ok").result(timeout=1) == "ok"
    finally:
        manager.stop(1)


def test_stale_local_recovery_does_not_exhaust_save_retry_budget(
    repository,
    generation_request,
) -> None:
    repository.enqueue_task(generation_request)
    accounts = FakeAccounts()
    claim = repository.claim_next_job(
        "worker-1",
        accounts.list_image_account_candidates(),
        1,
    )
    assert claim is not None
    assert repository.checkpoint_job(claim, JobCheckpoint(stage=JobStage.SAVING)) is True
    stale_error = getattr(queue_types, "LocalArtifactRecoveryUnavailable", RuntimeError)
    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda claimed, token: (_ for _ in ()).throw(
            stale_error("local recovery artifacts are unavailable")
        ),
        ImageQueueSettings(database_url="sqlite://", save_attempts=1),
        resource_controller=AlwaysAllowedResources(),
    )

    manager._run_claim(claim)

    assert repository.get_job(claim.job.id).status == JobStatus.RETRY_WAIT
    manager.stop(1)


def test_saturated_worker_still_refreshes_monitoring_state(
    repository,
    generation_request,
) -> None:
    repository.enqueue_task(generation_request)
    accounts = FakeAccounts()
    claim = repository.claim_next_job(
        "worker-1",
        accounts.list_image_account_candidates(),
        1,
    )
    assert claim is not None
    manager = ImageWorkerManager(
        repository,
        accounts,
        lambda claimed, token: None,
        ImageQueueSettings(database_url="sqlite://", poll_interval_seconds=0.01),
        resource_controller=AlwaysAllowedResources(),
    )
    pending = [Future() for _ in range(manager.internal_thread_cap)]
    with manager._lock:
        manager._futures.update({future: claim for future in pending})
    updates = []
    original_update = repository.update_worker_state
    repository.update_worker_state = lambda *args, **kwargs: updates.append((args, kwargs))
    try:
        manager._dispatch_once()
    finally:
        repository.update_worker_state = original_update
        for future in pending:
            future.cancel()
        manager.stop(1)

    assert updates
