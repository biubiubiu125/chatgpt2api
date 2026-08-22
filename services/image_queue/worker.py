from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import datetime, timezone
import os
from threading import Event, RLock, Thread
import time
from typing import Any, Callable, Sequence, TypeVar
from uuid import uuid4

from services.cluster_settings import load_cluster_settings
from services.image_failure import classify_image_exception
from services.config import config
from services.image_delivery import is_url_only_result
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.resource_controller import ResourceController
from services.image_queue.retry_policy import RetryPolicy
from services.image_queue.scheduler import is_generation_stage, is_recovery_stage, plan_claim_dispatch
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import ArtifactDescriptor, ClaimedJob, JobSnapshot, ResourceDecision
from services.image_queue.types import JobStage, LocalArtifactRecoveryUnavailable
from utils.log import logger


T = TypeVar("T")


class ImageWorkerManager:
    def __init__(
        self,
        repository: ImageQueueRepository,
        account_service: Any,
        executor: Callable[[ClaimedJob, str], None],
        settings: ImageQueueSettings,
        *,
        resource_controller: ResourceController | None = None,
        retry_policy: RetryPolicy | None = None,
        account_concurrency: int | None = None,
        state_change_callback: Callable[[object], None] | None = None,
        local_recovery_callback: Callable[[], int] | None = None,
        local_recovery_available_callback: Callable[[JobSnapshot, Sequence[ArtifactDescriptor]], bool] | None = None,
        terminal_cleanup_callback: Callable[[Sequence[ArtifactDescriptor]], bool | None] | None = None,
    ) -> None:
        self.repository = repository
        self.account_service = account_service
        self.execute_job = executor
        self.settings = settings
        self.resource_controller = resource_controller or ResourceController(
            settings,
            database=repository.database,
        )
        self.retry_policy = retry_policy or RetryPolicy(settings)
        self.state_change_callback = state_change_callback
        self.local_recovery_callback = local_recovery_callback
        self.local_recovery_available_callback = local_recovery_available_callback
        self.terminal_cleanup_callback = terminal_cleanup_callback
        cpu_limit = getattr(self.resource_controller, "cpu_limit_cores", lambda: None)()
        try:
            cpu = max(1, int(float(cpu_limit))) if cpu_limit is not None else max(1, int(os.cpu_count() or 1))
        except (TypeError, ValueError):
            cpu = max(1, int(os.cpu_count() or 1))
        # Reserve non-generation floors first. absolute_guard is a hard ceiling, not a fill target.
        floors = {
            "recovery": max(4, min(32, cpu * 2)),
            "io": max(4, min(32, cpu * 2)),
            "upscale": max(1, min(16, cpu)),
            "register": max(1, min(4, max(1, cpu // 2))),
        }
        floor_total = sum(floors.values())
        generation_floor = 8
        if floor_total + generation_floor > settings.absolute_guard:
            scale = max(0.1, (settings.absolute_guard - generation_floor) / max(1, floor_total))
            floors = {name: max(1, int(value * scale)) for name, value in floors.items()}
            # Keep recovery usable even after scaling.
            floors["recovery"] = max(floors["recovery"], min(4, max(1, settings.absolute_guard // 8)))
            floor_total = sum(floors.values())
            if floor_total + generation_floor > settings.absolute_guard:
                excess = floor_total + generation_floor - settings.absolute_guard
                for name in ("io", "upscale", "register"):
                    if excess <= 0:
                        break
                    cut = min(max(0, floors[name] - 1), excess)
                    floors[name] -= cut
                    excess -= cut
                floor_total = sum(floors.values())
        generation_cap = max(
            1,
            min(
                settings.absolute_guard - floor_total,
                int(settings.generation_concurrency_limit),
                int(settings.generation_concurrency_hard_cap),
            ),
        )
        self.pool_limits = {
            "generation": generation_cap,
            **floors,
        }
        self.internal_thread_cap = self.pool_limits["generation"]
        self.recovery_thread_cap = self.pool_limits["recovery"]
        self.pending_claim_cap = min(
            settings.absolute_guard,
            max(
                self.internal_thread_cap + self.recovery_thread_cap,
                (self.internal_thread_cap + self.recovery_thread_cap) * 2,
            ),
        )
        self.generation_slot_cap = self.internal_thread_cap
        self.account_concurrency = max(
            1,
            int(account_concurrency or config.image_account_concurrency or 1),
        )
        self._executors_shutdown = False
        self._create_executors()
        self._stop = Event()
        self._wake = Event()
        self._lock = RLock()
        self._futures: dict[Future[None], ClaimedJob] = {}
        self._claim_stages: dict[object, str] = {}
        self._claim_started_at: dict[object, float] = {}
        self._overdue_claims_logged: set[object] = set()
        self._recent_error = ""
        self._fatal_error = ""
        self._dispatcher: Thread | None = None
        self._heartbeat: Thread | None = None
        self._started_at_monotonic = 0.0
        self._recovery_interval = max(
            0.1,
            min(float(settings.heartbeat_seconds), max(0.1, float(repository.lease_seconds) / 2.0)),
        )
        self._next_recovery_at = 0.0
        self.cluster_settings = load_cluster_settings()
        instance = str(getattr(settings, "instance_id", "") or os.getenv("IMAGE_QUEUE_INSTANCE_ID") or "").strip()
        suffix = uuid4()
        configured_worker_id = self.cluster_settings.worker_id
        if configured_worker_id:
            self.worker_id = configured_worker_id
            # A cluster worker id is stable across container restarts.  Its
            # instance identity must be stable too, otherwise the fresh
            # process is rejected by the active-worker conflict guard before
            # its previous heartbeat has expired.
            self.instance_id = instance or f"{configured_worker_id}-instance"
        else:
            self.worker_id = f"image-worker-{instance}-{suffix}" if instance else f"image-worker-{suffix}"
            self.instance_id = f"{instance or self.worker_id}-{suffix}"
        self.process_instance_id = str(uuid4())
        self.process_started_at = datetime.now(timezone.utc)
        self._worker_state_snapshot: dict[str, Any] = {}
        self._worker_state_effective_concurrency = 0
        self._worker_state_pause_reason = ""

    @staticmethod
    def _is_worker_identity_conflict(exc: Exception) -> bool:
        return "worker identity conflict" in str(exc or "").lower()

    def _set_fatal_error(self, exc: Exception) -> None:
        message = str(exc or "fatal image worker error")[:300]
        with self._lock:
            self._fatal_error = message
            self._recent_error = message
        self._stop.set()
        self._wake.set()

    @property
    def fatal_error(self) -> str:
        with self._lock:
            return self._fatal_error

    def _create_executors(self) -> None:
        self._generation_executor = ThreadPoolExecutor(
            max_workers=self.internal_thread_cap,
            thread_name_prefix="image-worker",
        )
        self._recovery_executor = ThreadPoolExecutor(
            max_workers=self.recovery_thread_cap,
            thread_name_prefix="image-recovery",
        )
        self._io_executor = ThreadPoolExecutor(
            max_workers=self.pool_limits["io"],
            thread_name_prefix="image-io",
        )
        self._upscale_executor = ThreadPoolExecutor(
            max_workers=self.pool_limits["upscale"],
            thread_name_prefix="image-upscale",
        )
        self._registration_executor = ThreadPoolExecutor(
            max_workers=self.pool_limits["register"],
            thread_name_prefix="image-register",
        )
        self._executors_shutdown = False

    @property
    def executor_thread_count(self) -> int:
        return sum(
            len(getattr(executor, "_threads", ()))
            for executor in (
                self._generation_executor,
                self._recovery_executor,
                self._io_executor,
                self._upscale_executor,
                self._registration_executor,
            )
        )

    def _active_recovery_count(self) -> int:
        with self._lock:
            return sum(
                1
                for claim in self._futures.values()
                if is_recovery_stage(self._claim_stages.get(claim.job.id, claim.job.stage.value))
            )

    def submit_io(self, operation: Callable[[], T]) -> Future[T]:
        return self._io_executor.submit(operation)

    def submit_upscale(self, operation: Callable[[], T]) -> Future[T]:
        return self._upscale_executor.submit(operation)

    def submit_registration(self, operation: Callable[[], T]) -> Future[T]:
        return self._registration_executor.submit(operation)

    def note_claim_stage(self, claim: ClaimedJob, stage: JobStage | str) -> None:
        value = stage.value if isinstance(stage, JobStage) else str(stage)
        with self._lock:
            self._claim_stages[claim.job.id] = value

    def _active_generation_count(self) -> int:
        with self._lock:
            return sum(
                1
                for claim in self._futures.values()
                if is_generation_stage(self._claim_stages.get(claim.job.id, claim.job.stage.value))
            )

    def _allow_recovery(self, snapshot: object) -> ResourceDecision:
        allow_recovery = getattr(self.resource_controller, "allow_recovery", None)
        if callable(allow_recovery):
            return allow_recovery(snapshot)
        return ResourceDecision(True, effective_limit=self.recovery_thread_cap)

    def start(self) -> None:
        if self._dispatcher and self._dispatcher.is_alive():
            return
        with self._lock:
            active = [future for future in self._futures if not future.done()]
            if active:
                raise RuntimeError("image worker cannot restart while claims are still active")
            self._futures.clear()
            self._claim_stages.clear()
            self._claim_started_at.clear()
            self._overdue_claims_logged.clear()
            self._fatal_error = ""
            if self._executors_shutdown:
                self._create_executors()
        self._stop.clear()
        self._started_at_monotonic = time.monotonic()
        self._dispatcher = Thread(target=self._dispatch_loop, name="image-dispatcher", daemon=True)
        self._heartbeat = Thread(target=self._heartbeat_loop, name="image-heartbeat", daemon=True)
        self._dispatcher.start()
        self._heartbeat.start()
        logger.info({
            "event": "image_worker_started",
            "worker_id": self.worker_id,
            "instance_id": self.instance_id,
            "generation_workers": self.internal_thread_cap,
            "recovery_workers": self.recovery_thread_cap,
            "io_workers": self.pool_limits.get("io"),
            "upscale_workers": self.pool_limits.get("upscale"),
            "register_workers": self.pool_limits.get("register"),
            "generation_slot_cap": self.generation_slot_cap,
            "pending_claim_cap": self.pending_claim_cap,
        })

    def stop(self, timeout: float | None = None) -> bool:
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        for thread in (self._dispatcher, self._heartbeat):
            if thread and thread.is_alive():
                remaining = max(0.0, deadline - time.monotonic()) if timeout is not None else None
                thread.join(remaining)
        with self._lock:
            pending = set(self._futures)
        while pending:
            remaining = None if timeout is None else max(0.0, deadline - time.monotonic())
            if remaining is not None and remaining <= 0:
                break
            interval = self.settings.heartbeat_seconds if remaining is None else min(
                self.settings.heartbeat_seconds,
                remaining,
            )
            _, pending = wait(pending, timeout=max(0.01, float(interval)))
            if pending:
                claims = self._active_claims()
                if claims:
                    try:
                        self.repository.heartbeat_claims(self.worker_id, claims)
                    except Exception as exc:
                        logger.error({
                            "event": "image_worker_shutdown_heartbeat_failed",
                            "worker_id": self.worker_id,
                            "claim_count": len(claims),
                            "error": str(exc),
                        })
        drained = not pending
        for executor in (
            self._generation_executor,
            self._recovery_executor,
            self._io_executor,
            self._upscale_executor,
            self._registration_executor,
        ):
            executor.shutdown(wait=drained, cancel_futures=False)
        with self._lock:
            completed = [future for future in self._futures if future.done()]
            for future in completed:
                claim = self._futures.pop(future, None)
                if claim is not None:
                    self._claim_stages.pop(claim.job.id, None)
                    self._claim_started_at.pop(claim.job.id, None)
                    self._overdue_claims_logged.discard(claim.job.id)
            self._executors_shutdown = True
        return drained

    def notify(self) -> None:
        self._wake.set()

    def wait_until_saturated(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                active = len(self._futures)
            cap = self.internal_thread_cap + self.recovery_thread_cap
            if active >= cap or active == 0:
                return
            time.sleep(0.01)

    def _active_claims(self) -> list[ClaimedJob]:
        """Return in-flight claims that are still allowed to extend leases."""
        now = time.monotonic()
        max_runtime = max(1.0, float(self.settings.claim_max_runtime_seconds))
        overdue: list[ClaimedJob] = []
        heartbeatable: list[ClaimedJob] = []
        with self._lock:
            active = list(self._futures.values())
            for claim in active:
                started_at = self._claim_started_at.get(claim.job.id, now)
                if now - started_at >= max_runtime:
                    if claim.job.id not in self._overdue_claims_logged:
                        self._overdue_claims_logged.add(claim.job.id)
                        overdue.append(claim)
                    continue
                heartbeatable.append(claim)
        for claim in overdue:
            logger.error({
                "event": "image_worker_claim_max_runtime_exceeded",
                "worker_id": self.worker_id,
                "task_id": str(claim.job.task_id),
                "job_id": str(claim.job.id),
                "lease_version": claim.lease_version,
                "max_runtime_seconds": max_runtime,
            })
        return heartbeatable

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            self._heartbeat_worker_state()
            claims = self._active_claims()
            if claims:
                try:
                    self.repository.heartbeat_claims(self.worker_id, claims)
                except Exception as exc:
                    self._recent_error = str(exc)[:300]
                    logger.error({
                        "event": "image_worker_heartbeat_failed",
                        "worker_id": self.worker_id,
                        "claim_count": len(claims),
                        "error": str(exc),
                    })
            if self._stop.wait(self.settings.heartbeat_seconds):
                return

    def _heartbeat_worker_state(self) -> None:
        with self._lock:
            snapshot = dict(self._worker_state_snapshot)
            current_concurrency = len(self._futures)
            effective_concurrency = max(0, int(self._worker_state_effective_concurrency))
            pause_reason = str(self._worker_state_pause_reason or "")
        snapshot.update({
            "node_role": self.cluster_settings.node_role,
            "run_api": bool(self.cluster_settings.run_api),
            "run_worker": bool(self.cluster_settings.run_worker),
            "current_concurrency": current_concurrency,
            "effective_concurrency": effective_concurrency,
            "remaining_capacity": max(0, effective_concurrency - self._active_generation_count()),
            "recent_error": self._recent_error,
            "instance_id": self.instance_id,
            "process_instance_id": self.process_instance_id,
            "process_started_at": self.process_started_at.isoformat(),
        })
        if self.cluster_settings.worker_id:
            snapshot["configured_worker_id"] = self.cluster_settings.worker_id
        if self.cluster_settings.wireguard_ip:
            snapshot["wireguard_ip"] = self.cluster_settings.wireguard_ip
        if self.cluster_settings.image_base_url:
            snapshot["image_base_url"] = self.cluster_settings.image_base_url
        try:
            self.repository.update_worker_state(
                self.worker_id,
                resource_snapshot=snapshot,
                effective_concurrency=effective_concurrency,
                pause_reason=pause_reason,
            )
        except Exception as exc:
            self._recent_error = str(exc)[:300]
            if self._is_worker_identity_conflict(exc):
                self._set_fatal_error(exc)
                logger.error({
                    "event": "image_worker_identity_conflict",
                    "worker_id": self.worker_id,
                    "error": str(exc),
                })
                return
            logger.error({
                "event": "image_worker_state_heartbeat_failed",
                "worker_id": self.worker_id,
                "error": str(exc),
            })

    def _dispatch_loop(self) -> None:
        backoff_seconds = max(1.0, float(self.settings.poll_interval_seconds))
        while not self._stop.is_set():
            try:
                self._dispatch_once()
                backoff_seconds = max(1.0, float(self.settings.poll_interval_seconds))
            except Exception as exc:
                self._recent_error = str(exc)[:300]
                if self._is_worker_identity_conflict(exc):
                    self._set_fatal_error(exc)
                    logger.error({
                        "event": "image_worker_identity_conflict",
                        "worker_id": self.worker_id,
                        "error": str(exc),
                    })
                    return
                logger.error({
                    "event": "image_worker_dispatch_failed",
                    "worker_id": self.worker_id,
                    "retry_in_seconds": backoff_seconds,
                    "error": str(exc),
                })
                self._wake.wait(backoff_seconds)
                self._wake.clear()
                backoff_seconds = min(30.0, backoff_seconds * 2.0)

    def _expire_pending_tasks(self) -> None:
        pending_ttl_seconds = int(self.settings.pending_ttl_seconds)
        if pending_ttl_seconds <= 0:
            return
        expired_task_ids = self.repository.expire_pending_tasks(
            pending_ttl_seconds=pending_ttl_seconds,
        )
        if self.state_change_callback is None:
            return
        for task_id in expired_task_ids:
            try:
                self.state_change_callback(task_id)
            except Exception as exc:
                logger.error({
                    "event": "image_worker_state_change_callback_failed",
                    "task_id": str(task_id),
                    "error": str(exc),
                })

    def _dispatch_once(self) -> None:
        with self._lock:
            completed = [future for future in self._futures if future.done()]
            for future in completed:
                claim = self._futures.pop(future, None)
                if claim is not None:
                    self._claim_stages.pop(claim.job.id, None)
                    self._claim_started_at.pop(claim.job.id, None)
                    self._overdue_claims_logged.discard(claim.job.id)
                error = future.exception()
                if error is not None:
                    logger.error({
                        "event": "image_worker_claim_failed_unhandled",
                        "worker_id": self.worker_id,
                        "job_id": str(claim.job.id) if claim is not None else "",
                        "error": str(error),
                    })
            capacity = self.pending_claim_cap - len(self._futures)
        now = time.monotonic()
        self._expire_pending_tasks()
        if now >= self._next_recovery_at:
            self.repository.reclaim_expired_leases(
                protected_claims=self._active_claims(),
            )
            self._reconcile_unaccounted_quotas()
            purged = self.repository.purge_terminal_tasks(
                retention_seconds=self.settings.terminal_retention_seconds,
                worker_id=self.worker_id,
                include_unowned=not self.cluster_settings.is_worker,
            )
            cleanup_ok = not purged.artifacts
            if purged.artifacts and self.terminal_cleanup_callback is not None:
                try:
                    cleanup_result = self.terminal_cleanup_callback(purged.artifacts)
                    cleanup_ok = cleanup_result is not False
                except Exception as exc:
                    cleanup_ok = False
                    logger.error({
                        "event": "image_worker_terminal_artifact_cleanup_failed",
                        "artifact_count": len(purged.artifacts),
                        "error": str(exc),
                    })
            if cleanup_ok and purged.task_ids:
                try:
                    self.repository.finalize_terminal_tasks(
                        purged.task_ids,
                        worker_id=self.worker_id,
                        include_unowned=not self.cluster_settings.is_worker,
                    )
                except Exception as exc:
                    logger.error({
                        "event": "image_worker_terminal_task_finalize_failed",
                        "task_count": len(purged.task_ids),
                        "error": str(exc),
                    })
            if self.local_recovery_callback is not None:
                self.local_recovery_callback()
            self._next_recovery_at = now + self._recovery_interval
        snapshot = self.resource_controller.sample()
        generation_decision = self.resource_controller.allow_new_generation(snapshot)
        recovery_decision = self._allow_recovery(snapshot)
        candidates: Sequence[Any] = ()
        account_stats = {}
        try:
            account_stats = dict(self.account_service.get_stats())
        except Exception as exc:
            self._recent_error = str(exc)[:300]
        snapshot_data = asdict(snapshot)
        snapshot_data["sampled_at"] = snapshot.sampled_at.isoformat()
        snapshot_data["node_role"] = self.cluster_settings.node_role
        snapshot_data["run_api"] = bool(self.cluster_settings.run_api)
        snapshot_data["run_worker"] = bool(self.cluster_settings.run_worker)
        if self.cluster_settings.worker_id:
            snapshot_data["configured_worker_id"] = self.cluster_settings.worker_id
        if self.cluster_settings.wireguard_ip:
            snapshot_data["wireguard_ip"] = self.cluster_settings.wireguard_ip
        if self.cluster_settings.image_base_url:
            snapshot_data["image_base_url"] = self.cluster_settings.image_base_url
        snapshot_data["upstream_error_rate"] = float(
            getattr(self.resource_controller, "upstream_error_rate", lambda: 0.0)()
        )
        plan = plan_claim_dispatch(
            generation=generation_decision,
            recovery=recovery_decision,
            active_generation_count=self._active_generation_count(),
            pending_capacity=capacity,
            generation_hard_limit=self.generation_slot_cap,
        )
        effective_generation_limit = max(0, int(plan.generation_limit))
        active_generation_count = self._active_generation_count()
        if plan.allow_generation or plan.allow_recovery:
            candidates = self.account_service.list_image_account_candidates()
        with self._lock:
            current_concurrency = len(self._futures)
        snapshot_data["current_concurrency"] = current_concurrency
        snapshot_data["current_generation_concurrency"] = active_generation_count
        snapshot_data["effective_concurrency"] = effective_generation_limit
        snapshot_data["remaining_capacity"] = max(0, effective_generation_limit - active_generation_count)
        snapshot_data["available_account_count"] = len(candidates)
        snapshot_data["available_quota"] = max(0, int(account_stats.get("total_quota") or 0))
        snapshot_data["unlimited_quota_count"] = max(0, int(account_stats.get("unlimited_quota_count") or 0))
        snapshot_data["unknown_quota_count"] = max(0, int(account_stats.get("unknown_quota_count") or 0))
        snapshot_data["recent_error"] = self._recent_error
        snapshot_data["instance_id"] = self.instance_id
        snapshot_data["process_instance_id"] = self.process_instance_id
        snapshot_data["process_started_at"] = self.process_started_at.isoformat()
        pause_reason = plan.pause_reason or generation_decision.reason or recovery_decision.reason
        with self._lock:
            self._worker_state_snapshot = dict(snapshot_data)
            self._worker_state_effective_concurrency = effective_generation_limit
            self._worker_state_pause_reason = pause_reason
        self.repository.update_worker_state(
            self.worker_id,
            resource_snapshot=snapshot_data,
            effective_concurrency=effective_generation_limit,
            pause_reason=pause_reason,
        )
        recovery_capacity = self.recovery_thread_cap - self._active_recovery_count()
        allow_recovery = plan.allow_recovery and recovery_capacity > 0
        allow_generation = plan.allow_generation
        if capacity <= 0 or (not allow_generation and not allow_recovery):
            self._wake.wait(self.settings.poll_interval_seconds)
            self._wake.clear()
            return
        try:
            claim = self.repository.claim_next_job(
                self.worker_id,
                candidates,
                self.account_concurrency,
                allow_generation=allow_generation,
                recovery_only=allow_recovery and not allow_generation,
                prefer_recovery=allow_recovery,
                local_artifact_available=self.local_recovery_available_callback,
                allow_unowned_local_artifacts=not self.cluster_settings.is_worker,
                expected_process_instance_id=self.process_instance_id,
            )
        except Exception as exc:
            self._recent_error = str(exc)[:300]
            # claim_next_job already swallows IntegrityError races; any remaining
            # failure must not tear down the dispatcher with long backoff only.
            logger.error({
                "event": "image_worker_claim_next_failed",
                "worker_id": self.worker_id,
                "error": str(exc),
            })
            self._wake.wait(self.settings.poll_interval_seconds)
            self._wake.clear()
            return
        if claim is None:
            self._wake.wait(self.settings.poll_interval_seconds)
            self._wake.clear()
            return
        # Route recovery/saving work to the dedicated recovery pool so generation
        # pressure cannot starve checkpoint resume.
        if is_recovery_stage(claim.job.stage):
            if recovery_capacity <= 0:
                try:
                    self.repository.release_claim(claim)
                except Exception as exc:
                    logger.error({
                        "event": "image_worker_recovery_release_failed",
                        "job_id": str(claim.job.id),
                        "error": str(exc),
                    })
                self._wake.wait(self.settings.poll_interval_seconds)
                self._wake.clear()
                return
            future = self._recovery_executor.submit(self._run_claim, claim)
        else:
            future = self._generation_executor.submit(self._run_claim, claim)
        with self._lock:
            self._futures[future] = claim
            self._claim_stages.setdefault(claim.job.id, claim.job.stage.value)
            self._claim_started_at[claim.job.id] = time.monotonic()

    def _run_claim(self, claim: ClaimedJob) -> None:
        initial_stage = claim.job.stage
        self.note_claim_stage(claim, initial_stage)
        accountless_recovery = claim.account_slot < 0
        try:
            if (
                self.repository.is_cancel_requested(claim.job.task_id)
                and not bool(getattr(claim.job, "quota_consumed", False))
            ):
                self.repository.release_claim(claim)
                return
            access_token = (
                ""
                if accountless_recovery
                else self.account_service.prepare_image_account(claim.account_id)
            )
            self.execute_job(claim, access_token)
        except LocalArtifactRecoveryUnavailable as exc:
            self.repository.schedule_retry(
                claim,
                error_code="local_artifact_unavailable",
                error_message=str(exc),
                next_retry_at=datetime.now(timezone.utc),
            )
            return
        except Exception as exc:
            self._recent_error = str(exc)[:300]
            current_job = self.repository.get_job(claim.job.id)
            if (
                self.repository.is_cancel_requested(claim.job.task_id)
                and not bool(getattr(current_job, "quota_consumed", False))
            ):
                self.repository.release_claim(claim)
                return
            stage = current_job.stage if current_job is not None else claim.job.stage
            attempts = self._stage_attempts(current_job or claim.job, stage)
            decision = self.retry_policy.decision(stage, attempts, exc, datetime.now(timezone.utc))
            failure = classify_image_exception(exc)
            self._note_upstream_outcome(success=False, failure=failure, stage=stage)
            self._record_claim_account_result(
                claim,
                current_job,
                success=False,
                failure=failure,
                error=exc,
            )
            if decision.retry and decision.next_retry_at is not None:
                self.repository.schedule_retry(
                    claim,
                    error_code=decision.error_code,
                    error_message=decision.error_message,
                    next_retry_at=decision.next_retry_at,
                )
            else:
                self.repository.fail_job(
                    claim,
                    error_code=decision.error_code,
                    error_message=decision.error_message,
                )
            return
        else:
            current_job = self.repository.get_job(claim.job.id)
            self._note_upstream_outcome(success=True, stage=getattr(current_job, "stage", initial_stage))
            self._record_claim_account_result(
                claim,
                current_job,
                success=True,
            )
        finally:
            if self.state_change_callback is not None:
                try:
                    self.state_change_callback(claim.job.task_id)
                except Exception as exc:
                    logger.error({
                        "event": "image_worker_state_change_callback_failed",
                        "task_id": str(claim.job.task_id),
                        "error": str(exc),
                    })

    def _note_upstream_outcome(
        self,
        *,
        success: bool,
        failure: Any | None = None,
        stage: Any = None,
    ) -> None:
        note = getattr(self.resource_controller, "note_upstream_outcome", None)
        if not callable(note):
            return
        stage_value = getattr(stage, "value", str(stage or ""))
        # Only generation/resolving pressure should freeze new generation claims.
        if not success and stage_value in {
            JobStage.TRANSFORMING.value,
            JobStage.SAVING.value,
        }:
            return
        status_code = getattr(failure, "status_code", None) if failure is not None else None
        error_code = str(getattr(failure, "code", "") or "") if failure is not None else ""
        try:
            note(success=success, status_code=status_code, error_code=error_code)
        except Exception as exc:
            logger.error({
                "event": "image_worker_upstream_outcome_note_failed",
                "error": str(exc),
            })

    @staticmethod
    def _stage_attempts(job: Any, stage: Any) -> int:
        stage_value = getattr(stage, "value", str(stage))
        if stage_value in {"resolving", "downloading"}:
            return max(1, int(job.download_attempts))
        if stage_value in {"transforming", "saving"}:
            return max(1, int(job.save_attempts))
        return max(1, int(job.generate_attempts))

    def _record_claim_account_result(
        self,
        claim: ClaimedJob,
        current_job: Any | None,
        *,
        success: bool,
        **values: Any,
    ) -> None:
        job = current_job or claim.job
        if int(getattr(job, "lease_version", 0) or 0) != int(claim.lease_version):
            return
        quota_consumed = bool(
            getattr(job, "quota_consumed", False)
            and not getattr(job, "quota_accounted", False)
        )
        if success and quota_consumed and is_url_only_result(getattr(job, "result_payload", {}) or {}):
            return
        if claim.account_slot < 0 and not quota_consumed:
            return
        recorded = self._record_account_result(
            claim.account_id,
            success=success,
            quota_consumed=quota_consumed,
            idempotency_key=(f"image-job:{claim.job.id}" if quota_consumed else ""),
            **values,
        )
        if recorded and quota_consumed:
            self.repository.mark_quota_accounted(claim.job.id, claim.account_id)

    def _reconcile_unaccounted_quotas(self) -> None:
        for job in self.repository.list_unaccounted_terminal_quota_jobs():
            if job.account_id is None:
                continue
            if job.status.value == "success" and is_url_only_result(getattr(job, "result_payload", {}) or {}):
                continue
            recorded = self._record_account_result(
                job.account_id,
                success=job.status.value == "success",
                quota_consumed=True,
                idempotency_key=f"image-job:{job.id}",
            )
            if recorded:
                self.repository.mark_quota_accounted(job.id, job.account_id)

    def _record_account_result(self, account_id: object, **values: Any) -> bool:
        try:
            result = self.account_service.record_managed_image_result(account_id, **values)
            return result is not None
        except Exception as exc:
            logger.error({
                "event": "image_account_result_bookkeeping_failed",
                "account_id": str(account_id),
                "success": bool(values.get("success")),
                "error": str(exc),
            })
            return False
