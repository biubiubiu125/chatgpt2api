from __future__ import annotations

from collections import Counter
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Callable, Iterable, Sequence, TextIO
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from services.image_failure import image_failure
from services.image_queue.database import ImageQueueDatabase, ImageQueueUnavailableError
from services.image_queue.models import (
    ImageAccountLease,
    ImageJob,
    ImageLegacyImport,
    ImageTask,
    ImageTaskArtifact,
    ImageTaskEvent,
    ImageWorkerState,
    utc_now,
)
from services.image_queue.resource_controller import ImageQueueResourcePressureError
from services.image_queue.sanitization import sanitize_event_data
from services.image_queue.types import (
    ArtifactDescriptor,
    ArtifactStatus,
    ClaimedJob,
    DeliveryStatus,
    EnqueueRequest,
    EnqueueResult,
    ImageAccountCandidate,
    JobCheckpoint,
    JobSnapshot,
    JobStage,
    JobStatus,
    TaskSnapshot,
    TaskStatus,
    TERMINAL_JOB_STATUSES,
    TERMINAL_TASK_STATUSES,
)


WORKER_DELIVERY_SNAPSHOT_KEYS = (
    "delivery_status",
    "delivery_checked_at",
    "delivery_url",
    "delivery_error",
    "delivery_failures",
)

WORKER_IDENTITY_SNAPSHOT_KEYS = (
    "instance_id",
    "process_instance_id",
    "process_started_at",
    "configured_worker_id",
)


class IdempotencyConflict(ValueError):
    code = "idempotency_conflict"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.failure = image_failure(
            self.code,
            raw_detail=message,
        ).with_public_detail(message)


class TaskStateConflict(ValueError):
    code = "task_state_conflict"


@dataclass(frozen=True, eq=False)
class PurgedTerminalTasks:
    removed: int
    artifacts: tuple[ArtifactDescriptor, ...] = ()
    task_ids: tuple[UUID, ...] = ()

    def __int__(self) -> int:
        return self.removed

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.removed == other
        if isinstance(other, PurgedTerminalTasks):
            return (
                self.removed == other.removed
                and self.artifacts == other.artifacts
                and self.task_ids == other.task_ids
            )
        return NotImplemented


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _snapshot_text(snapshot: dict[str, Any], key: str) -> str:
    return str(snapshot.get(key) or "").strip()


def _snapshot_datetime(snapshot: dict[str, Any], key: str) -> datetime | None:
    text = _snapshot_text(snapshot, key)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value or ""))
    except (TypeError, ValueError):
        return None


def _backup_datetime(value: object, default: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid backup datetime: {text}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def claimable_job_statement(
    now: datetime | None = None,
    excluded_job_ids: Sequence[UUID] = (),
    after_sort_key: tuple[datetime, datetime, int, UUID] | None = None,
    recovery_only: bool = False,
    generation_only: bool = False,
):
    claim_time = now or func.now()
    statement = (
        select(ImageJob)
        .join(ImageTask, ImageTask.id == ImageJob.task_id)
        .where(ImageJob.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]))
        .where(ImageJob.available_at <= claim_time)
        .where(ImageTask.cancel_requested.is_(False))
        .order_by(ImageJob.available_at, ImageJob.created_at, ImageJob.ordinal, ImageJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if excluded_job_ids:
        statement = statement.where(ImageJob.id.not_in(list(excluded_job_ids)))
    if after_sort_key is not None:
        available_at, created_at, ordinal, job_id = after_sort_key
        statement = statement.where(or_(
            ImageJob.available_at > available_at,
            and_(ImageJob.available_at == available_at, ImageJob.created_at > created_at),
            and_(
                ImageJob.available_at == available_at,
                ImageJob.created_at == created_at,
                ImageJob.ordinal > ordinal,
            ),
            and_(
                ImageJob.available_at == available_at,
                ImageJob.created_at == created_at,
                ImageJob.ordinal == ordinal,
                ImageJob.id > job_id,
            ),
        ))
    if recovery_only:
        statement = statement.where(ImageJob.stage.not_in([
            JobStage.QUEUED.value,
            JobStage.LEASED.value,
            JobStage.GENERATING.value,
        ]))
    elif generation_only:
        statement = statement.where(ImageJob.stage.in_([
            JobStage.QUEUED.value,
            JobStage.LEASED.value,
            JobStage.GENERATING.value,
            JobStage.RETRY_WAIT.value,
        ]))
    return statement


class ImageQueueRepository:
    def __init__(self, database: ImageQueueDatabase) -> None:
        self.database = database
        self.lease_seconds = database.settings.lease_seconds
        self.claim_max_runtime_seconds = database.settings.claim_max_runtime_seconds
        self.recovery_account_timeout_seconds = database.settings.recovery_account_timeout_seconds
        self.delivery_grace_seconds = database.settings.delivery_grace_seconds
        self.terminal_retention_seconds = database.settings.terminal_retention_seconds

    @staticmethod
    def _event(
        session: Session,
        *,
        task_id: UUID,
        event_type: str,
        job_id: UUID | None = None,
        attempt: int = 0,
        from_status: str | None = None,
        to_status: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        session.add(ImageTaskEvent(
            task_id=task_id,
            job_id=job_id,
            attempt=max(0, int(attempt)),
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            event_data=sanitize_event_data(data or {}),
        ))

    @staticmethod
    def _find_task(
        session: Session,
        owner_key: str,
        task_or_client_id: object,
        *,
        lock: bool = False,
    ) -> ImageTask | None:
        task_id = _uuid(task_or_client_id)
        identifier = str(task_or_client_id or "").strip()
        owner = str(owner_key or "").strip()
        matches: list[ImageTask] = []
        seen: set[UUID] = set()

        def add_match(task: ImageTask | None) -> None:
            if task is not None and task.id not in seen:
                matches.append(task)
                seen.add(task.id)

        if task_id is not None:
            statement = select(ImageTask).where(
                ImageTask.owner_key == owner,
                ImageTask.id == task_id,
            )
            if lock:
                statement = statement.with_for_update()
            add_match(session.execute(statement).scalar_one_or_none())
        if not identifier:
            return matches[0] if matches else None
        for predicate in (
            ImageTask.client_task_id == identifier,
            ImageTask.idempotency_key == identifier,
        ):
            statement = (
                select(ImageTask)
                .where(ImageTask.owner_key == owner, predicate)
                .order_by(ImageTask.created_at, ImageTask.id)
                .limit(1)
            )
            if lock:
                statement = statement.with_for_update()
            add_match(session.execute(statement).scalar_one_or_none())
        if task_id is not None and len(matches) > 1:
            raise IdempotencyConflict("image task identifier matches multiple tasks")
        return matches[0] if matches else None

    @staticmethod
    def _job_snapshot(job: ImageJob) -> JobSnapshot:
        return JobSnapshot(
            id=job.id,
            task_id=job.task_id,
            ordinal=int(job.ordinal),
            status=JobStatus(job.status),
            stage=JobStage(job.stage),
            available_at=job.available_at,
            generate_attempts=int(job.generate_attempts or 0),
            download_attempts=int(job.download_attempts or 0),
            save_attempts=int(job.save_attempts or 0),
            account_id=job.account_id,
            conversation_id=str(job.conversation_id or ""),
            image_urls=list(job.image_urls or []),
            file_ids=list(job.file_ids or []),
            sediment_ids=list(job.sediment_ids or []),
            quota_consumed=bool(job.quota_consumed),
            quota_accounted=bool(job.quota_accounted),
            result_payload=dict(job.result_payload or {}),
            lease_version=int(job.lease_version or 0),
        )

    @staticmethod
    def _artifact_descriptor(item: ImageTaskArtifact) -> ArtifactDescriptor:
        return ArtifactDescriptor(
            task_id=item.task_id,
            job_id=item.job_id,
            kind=item.kind,
            ordinal=item.ordinal,
            status=ArtifactStatus(item.status),
            relative_path=item.relative_path,
            sha256=item.sha256,
            mime_type=item.mime_type,
            byte_size=int(item.byte_size),
            width=int(item.width),
            height=int(item.height),
            storage_backend=item.storage_backend,
            source_url=str(item.source_url or ""),
            worker_id=str(item.worker_id or ""),
        )

    @staticmethod
    def _private_artifact_payload(descriptor: ArtifactDescriptor) -> bytes | None:
        if descriptor.kind not in {"input", "mask"}:
            return None
        if descriptor.absolute_path is None:
            raise ValueError("private input artifact payload path is missing")
        try:
            payload = descriptor.absolute_path.read_bytes()
        except OSError as exc:
            raise ValueError("private input artifact payload is unreadable") from exc
        if len(payload) != int(descriptor.byte_size):
            raise ValueError("private input artifact payload size mismatch")
        if sha256(payload).hexdigest() != descriptor.sha256:
            raise ValueError("private input artifact payload checksum mismatch")
        return payload

    @staticmethod
    def _job_recovery_stage(job: ImageJob) -> str:
        if job.image_urls:
            return JobStage.DOWNLOADING.value
        if job.conversation_id or job.file_ids or job.sediment_ids:
            return JobStage.RESOLVING.value
        return str(job.stage or "")

    @classmethod
    def _task_representative_job(cls, jobs: Sequence[ImageJob]) -> ImageJob | None:
        ordered = list(jobs)
        for statuses in (
            {JobStatus.RUNNING.value, JobStatus.LEASED.value},
            {JobStatus.RETRY_WAIT.value},
            {JobStatus.QUEUED.value},
            {JobStatus.FAILED.value},
            {JobStatus.SUCCESS.value},
        ):
            for job in ordered:
                if job.status in statuses:
                    return job
        return ordered[0] if ordered else None

    @staticmethod
    def _job_can_resume_poll(job: ImageJob) -> bool:
        return bool(job.image_urls or job.conversation_id or job.file_ids or job.sediment_ids)

    @classmethod
    def _snapshot_from_jobs(cls, task: ImageTask, jobs: Sequence[ImageJob]) -> TaskSnapshot:
        representative = cls._task_representative_job(jobs)
        stage = cls._job_recovery_stage(representative) if representative is not None else str(task.status or "")
        can_resume_poll = any(
            job.status == JobStatus.FAILED.value and cls._job_can_resume_poll(job)
            for job in jobs
        )
        data = [
            dict(job.result_payload or {})
            for job in jobs
            if job.status == JobStatus.SUCCESS.value and isinstance(job.result_payload, dict) and job.result_payload
        ]
        return TaskSnapshot(
            id=task.id,
            owner_key=task.owner_key,
            client_task_id=str(task.client_task_id or ""),
            idempotency_key=str(task.idempotency_key or ""),
            status=TaskStatus(task.status),
            required_jobs=int(task.required_jobs or 0),
            succeeded_jobs=int(task.succeeded_jobs or 0),
            failed_jobs=int(task.failed_jobs or 0),
            task_type=str(task.task_type or "generation"),
            public_model=str(task.public_model or "gpt-image-2"),
            request_hash=str(task.request_hash or ""),
            request_payload=dict(task.request_payload or {}),
            wait_reason=str(task.wait_reason or ""),
            error_code=str(task.error_code or ""),
            error_message=str(task.error_message or ""),
            delivery_status=DeliveryStatus(task.delivery_status),
            stage=stage,
            progress=stage,
            conversation_id=str(getattr(representative, "conversation_id", "") or ""),
            can_resume_poll=can_resume_poll,
            data=data,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
        )

    @classmethod
    def _task_snapshot(cls, session: Session, task: ImageTask) -> TaskSnapshot:
        jobs = session.execute(
            select(ImageJob)
            .where(ImageJob.task_id == task.id)
            .order_by(ImageJob.ordinal)
        ).scalars().all()
        return cls._snapshot_from_jobs(task, jobs)

    @classmethod
    def _task_snapshots(cls, session: Session, tasks: Sequence[ImageTask]) -> list[TaskSnapshot]:
        if not tasks:
            return []
        task_ids = [task.id for task in tasks]
        jobs = session.execute(
            select(ImageJob)
            .where(ImageJob.task_id.in_(task_ids))
            .order_by(ImageJob.task_id, ImageJob.ordinal)
        ).scalars().all()
        jobs_by_task: dict[UUID, list[ImageJob]] = {task_id: [] for task_id in task_ids}
        for job in jobs:
            jobs_by_task.setdefault(job.task_id, []).append(job)
        return [cls._snapshot_from_jobs(task, jobs_by_task.get(task.id, ())) for task in tasks]

    @staticmethod
    def _lock_identifier_namespace(
        session: Session,
        owner_key: str,
        identifiers: Iterable[object],
    ) -> None:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            return
        values = sorted({
            str(identifier or "").strip()
            for identifier in identifiers
            if str(identifier or "").strip()
        })
        for identifier in values:
            lock_key = int.from_bytes(
                sha256(f"{owner_key}\0{identifier}".encode("utf-8")).digest()[:8],
                "big",
                signed=True,
            )
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_key},
            )

    @staticmethod
    def _lock_backlog_namespace(session: Session) -> None:
        if session.bind is None or session.bind.dialect.name != "postgresql":
            return
        lock_key = int.from_bytes(sha256(b"image-task-backlog").digest()[:8], "big", signed=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )

    @staticmethod
    def _matching_existing_task(session: Session, request: EnqueueRequest) -> ImageTask | None:
        identifiers = [
            value
            for value in {
                str(request.idempotency_key or "").strip(),
                str(request.client_task_id or "").strip(),
            }
            if value
        ]
        if not identifiers:
            return None
        matches = session.execute(
            select(ImageTask).where(
                ImageTask.owner_key == request.owner_key,
                or_(
                    ImageTask.idempotency_key.in_(identifiers),
                    ImageTask.client_task_id.in_(identifiers),
                ),
            )
        ).scalars().all()
        if not matches:
            return None
        unique = {task.id: task for task in matches}
        if len(unique) > 1:
            raise IdempotencyConflict("idempotency key and client_task_id refer to different image tasks")
        return next(iter(unique.values()))

    def count_backlog_tasks(self, session: Session | None = None) -> int:
        statement = select(func.count(ImageTask.id)).where(
            ImageTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.RETRYING.value]),
            ImageTask.started_at.is_(None),
            ImageTask.completed_at.is_(None),
        )
        if session is not None:
            return int(session.execute(statement).scalar_one())
        with self.database.session() as session_obj:
            return int(session_obj.execute(statement).scalar_one())

    def enqueue_task(self, request: EnqueueRequest, *, max_backlog: int | None = None) -> EnqueueResult:
        if not str(request.owner_key or "").strip():
            raise ValueError("owner_key is required")
        if not str(request.request_hash or "").strip():
            raise ValueError("request_hash is required")
        required_jobs = max(1, int(request.required_jobs or 1))
        try:
            with self.database.session() as session:
                self._lock_identifier_namespace(
                    session,
                    request.owner_key,
                    (request.idempotency_key, request.client_task_id),
                )
                existing = self._matching_existing_task(session, request)
                if existing is not None:
                    if existing.request_hash != request.request_hash:
                        raise IdempotencyConflict("idempotency key was already used with a different request")
                    return EnqueueResult(task=self._task_snapshot(session, existing), created=False)
                if max_backlog is not None:
                    backlog_limit = max(1, int(max_backlog))
                    self._lock_backlog_namespace(session)
                    if self.count_backlog_tasks(session) >= backlog_limit:
                        raise ImageQueueResourcePressureError("resource_backlog")

                task = ImageTask(
                    **({"id": request.task_id} if request.task_id is not None else {}),
                    owner_key=request.owner_key,
                    client_task_id=request.client_task_id or None,
                    idempotency_key=request.idempotency_key or None,
                    request_hash=request.request_hash,
                    task_type=request.task_type,
                    public_model=request.public_model,
                    original_prompt=request.original_prompt,
                    effective_prompt=request.effective_prompt,
                    prompt_suffix_version=request.prompt_suffix_version,
                    request_payload=dict(request.request_payload),
                    required_jobs=required_jobs,
                    status=TaskStatus.QUEUED.value,
                    wait_reason="queued",
                )
                session.add(task)
                session.flush()
                for artifact in request.input_artifacts:
                    if artifact.task_id != task.id or artifact.job_id is not None:
                        raise ValueError("input artifact does not belong to enqueued task")
                    session.add(self._artifact_row(
                        artifact,
                        payload_blob=self._private_artifact_payload(artifact),
                    ))
                for ordinal in range(1, required_jobs + 1):
                    session.add(ImageJob(
                        task_id=task.id,
                        ordinal=ordinal,
                        status=JobStatus.QUEUED.value,
                        stage=JobStage.QUEUED.value,
                    ))
                self._event(
                    session,
                    task_id=task.id,
                    event_type="task_queued",
                    to_status=TaskStatus.QUEUED.value,
                    data={
                        "required_jobs": required_jobs,
                        "public_model": request.public_model,
                        "trace_headers": dict(request.request_payload.get("trace_headers") or {}),
                    },
                )
                session.flush()
                return EnqueueResult(task=self._task_snapshot(session, task), created=True)
        except IntegrityError as exc:
            with self.database.session() as session:
                existing = self._matching_existing_task(session, request)
                if existing is not None and existing.request_hash == request.request_hash:
                    return EnqueueResult(task=self._task_snapshot(session, existing), created=False)
            raise IdempotencyConflict("idempotency key was already used with a different request") from exc

    def expire_pending_tasks(
        self,
        *,
        pending_ttl_seconds: int,
        now: datetime | None = None,
    ) -> list[UUID]:
        pending_ttl_seconds = max(1, int(pending_ttl_seconds or 0))
        current_time = _now(now)
        cutoff = current_time - timedelta(seconds=pending_ttl_seconds)
        with self.database.session() as session:
            jobs = session.execute(
                select(ImageJob)
                .join(ImageTask, ImageTask.id == ImageJob.task_id)
                .where(ImageTask.status.notin_([status.value for status in TERMINAL_TASK_STATUSES]))
                .where(ImageTask.completed_at.is_(None))
                .where(ImageJob.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]))
                .where(or_(
                    and_(
                        ImageJob.status == JobStatus.QUEUED.value,
                        or_(ImageJob.created_at <= cutoff, ImageTask.queued_at <= cutoff),
                    ),
                    and_(
                        ImageJob.status == JobStatus.RETRY_WAIT.value,
                        ImageJob.available_at <= cutoff,
                    ),
                ))
                .order_by(ImageJob.created_at, ImageJob.id)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            if not jobs:
                return []
            task_ids = list(dict.fromkeys(job.task_id for job in jobs))
            tasks = {
                task.id: task
                for task in session.execute(
                    select(ImageTask)
                    .where(ImageTask.id.in_(task_ids))
                    .with_for_update()
                ).scalars()
            }
            expired_task_ids: list[UUID] = []
            for job in jobs:
                task = tasks.get(job.task_id)
                if task is None or TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                    continue
                previous_status = job.status
                job.status = JobStatus.FAILED.value
                job.stage = JobStage.FAILED.value
                job.error_code = "queue_timeout"
                job.error_message = f"图片任务在队列中等待超过 {pending_ttl_seconds} 秒，已失败，请重新生成"
                job.completed_at = current_time
                job.updated_at = current_time
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                if task.id not in expired_task_ids:
                    expired_task_ids.append(task.id)
                self._event(
                    session,
                    task_id=task.id,
                    job_id=job.id,
                    event_type="job_pending_expired",
                    from_status=previous_status,
                    to_status=job.status,
                    data={"pending_ttl_seconds": pending_ttl_seconds},
                )
            for task_id in expired_task_ids:
                task = tasks.get(task_id)
                if task is None:
                    continue
                task.wait_reason = "queue_timeout"
                self._aggregate_task(session, task)
                self._event(
                    session,
                    task_id=task.id,
                    event_type="task_pending_expired",
                    to_status=task.status,
                    data={
                        "pending_ttl_seconds": pending_ttl_seconds,
                        "queued_at": task.queued_at.isoformat(),
                        "expired_at": current_time.isoformat(),
                    },
                )
            session.flush()
            return expired_task_ids

    def get_task(self, owner_key: str, task_or_client_id: object) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = self._find_task(session, owner_key, task_or_client_id)
            return self._task_snapshot(session, task) if task is not None else None

    def get_task_by_id(self, task_id: object) -> TaskSnapshot | None:
        resolved = _uuid(task_id)
        if resolved is None:
            return None
        with self.database.session() as session:
            task = session.get(ImageTask, resolved)
            return self._task_snapshot(session, task) if task is not None else None

    def get_task_by_client_id(self, owner_key: str, client_task_id: str) -> TaskSnapshot | None:
        return self.get_task(owner_key, client_task_id)

    def get_task_by_idempotency_key(self, owner_key: str, idempotency_key: str) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = session.execute(select(ImageTask).where(
                ImageTask.owner_key == owner_key,
                ImageTask.idempotency_key == idempotency_key,
            )).scalar_one_or_none()
            return self._task_snapshot(session, task) if task is not None else None

    def list_tasks(
        self,
        owner_key: str,
        identifiers: Sequence[object] | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskSnapshot]:
        with self.database.session() as session:
            if identifiers:
                identifier_values = [str(value or "").strip() for value in identifiers if str(value or "").strip()]
                task_ids = [value for value in (_uuid(item) for item in identifier_values) if value is not None]
                conditions = [
                    ImageTask.client_task_id.in_(identifier_values),
                    ImageTask.idempotency_key.in_(identifier_values),
                ]
                if task_ids:
                    conditions.append(ImageTask.id.in_(task_ids))
                matched = session.execute(
                    select(ImageTask).where(
                        ImageTask.owner_key == owner_key,
                        or_(*conditions),
                    )
                ).scalars().all()
                by_task_id = {str(task.id): task for task in matched}
                by_client_id = {str(task.client_task_id): task for task in matched if task.client_task_id}
                by_idempotency_key = {str(task.idempotency_key): task for task in matched if task.idempotency_key}
                tasks = []
                seen: set[UUID] = set()
                for identifier in identifier_values:
                    candidates = [
                        task
                        for task in (
                            by_task_id.get(identifier),
                            by_client_id.get(identifier),
                            by_idempotency_key.get(identifier),
                        )
                        if task is not None
                    ]
                    unique_candidates = {task.id: task for task in candidates}
                    if _uuid(identifier) is not None and len(unique_candidates) > 1:
                        raise IdempotencyConflict("image task identifier matches multiple tasks")
                    task = next(iter(unique_candidates.values()), None)
                    if task is not None and task.id not in seen:
                        tasks.append(task)
                        seen.add(task.id)
            else:
                tasks = session.execute(
                    select(ImageTask)
                    .where(ImageTask.owner_key == owner_key)
                    .order_by(ImageTask.updated_at.desc())
                    .offset(max(0, int(offset)))
                    .limit(min(500, max(1, int(limit))))
                ).scalars().all()
            return self._task_snapshots(session, tasks)

    def list_jobs(self, task_id: UUID) -> list[JobSnapshot]:
        with self.database.session() as session:
            jobs = session.execute(
                select(ImageJob)
                .where(ImageJob.task_id == task_id)
                .order_by(ImageJob.ordinal)
            ).scalars().all()
            return [self._job_snapshot(job) for job in jobs]

    def get_job(self, job_id: UUID) -> JobSnapshot | None:
        with self.database.session() as session:
            job = session.get(ImageJob, job_id)
            return self._job_snapshot(job) if job is not None else None

    def get_execution_request(self, task_id: object) -> dict[str, Any] | None:
        resolved = _uuid(task_id)
        if resolved is None:
            return None
        with self.database.session() as session:
            task = session.get(ImageTask, resolved)
            if task is None:
                return None
            return {
                "task_id": task.id,
                "task_type": str(task.task_type or "generation"),
                "public_model": str(task.public_model or "gpt-image-2"),
                "original_prompt": str(task.original_prompt or ""),
                "effective_prompt": str(task.effective_prompt or ""),
                "request_payload": dict(task.request_payload or {}),
            }

    def queue_position(self, task_id: object) -> int:
        resolved = _uuid(task_id)
        if resolved is None:
            return 0
        queued_statuses = [JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]
        with self.database.session() as session:
            target = session.execute(
                select(
                    ImageJob.available_at,
                    ImageJob.created_at,
                    ImageJob.ordinal,
                    ImageJob.id,
                )
                .where(
                    ImageJob.task_id == resolved,
                    ImageJob.status.in_(queued_statuses),
                )
                .order_by(
                    ImageJob.available_at,
                    ImageJob.created_at,
                    ImageJob.ordinal,
                    ImageJob.id,
                )
                .limit(1)
            ).one_or_none()
            if target is None:
                return 0
            available_at, created_at, ordinal, job_id = target
            ahead = session.execute(
                select(func.count(ImageJob.id)).where(
                    ImageJob.status.in_(queued_statuses),
                    or_(
                        ImageJob.available_at < available_at,
                        and_(
                            ImageJob.available_at == available_at,
                            ImageJob.created_at < created_at,
                        ),
                        and_(
                            ImageJob.available_at == available_at,
                            ImageJob.created_at == created_at,
                            ImageJob.ordinal < ordinal,
                        ),
                        and_(
                            ImageJob.available_at == available_at,
                            ImageJob.created_at == created_at,
                            ImageJob.ordinal == ordinal,
                            ImageJob.id < job_id,
                        ),
                    ),
                )
            ).scalar_one()
            return int(ahead) + 1

    def queue_context(self, task_ids: Sequence[object]) -> tuple[dict[UUID, int], str]:
        resolved_ids = [value for value in (_uuid(item) for item in task_ids) if value is not None]
        if not resolved_ids:
            return {}, ""
        with self.database.session() as session:
            queued_statuses = [JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]
            now = utc_now()
            ranked = select(
                ImageJob.task_id.label("task_id"),
                func.row_number().over(order_by=(
                    ImageJob.available_at,
                    ImageJob.created_at,
                    ImageJob.ordinal,
                    ImageJob.id,
                )).label("position"),
            ).where(ImageJob.status.in_(queued_statuses)).subquery()
            worker_pause_reason = (
                select(ImageWorkerState.pause_reason)
                .where(
                    ImageWorkerState.heartbeat_at
                    >= now - timedelta(seconds=max(60.0, self.lease_seconds * 2.0))
                )
                .order_by(ImageWorkerState.heartbeat_at.desc())
                .limit(1)
                .scalar_subquery()
            )
            rows = session.execute(
                select(
                    ranked.c.task_id,
                    func.min(ranked.c.position),
                    worker_pause_reason.label("worker_pause_reason"),
                )
                .where(ranked.c.task_id.in_(resolved_ids))
                .group_by(ranked.c.task_id, worker_pause_reason)
            ).all()
            positions = {task_id: int(position) for task_id, position, _pause_reason in rows}
            pause_reason = str(rows[0][2] or "") if rows else ""
            return positions, pause_reason

    def queue_positions(self, task_ids: Sequence[object]) -> dict[UUID, int]:
        positions, _pause_reason = self.queue_context(task_ids)
        return positions

    def list_recoverable_jobs(self) -> list[JobSnapshot]:
        with self.database.session() as session:
            jobs = session.execute(
                select(ImageJob)
                .where(ImageJob.status == JobStatus.QUEUED.value)
                .where(ImageJob.stage.notin_([
                    JobStage.QUEUED.value,
                    JobStage.RETRY_WAIT.value,
                    JobStage.SUCCESS.value,
                    JobStage.FAILED.value,
                    JobStage.CANCELED.value,
                ]))
            ).scalars().all()
            return [self._job_snapshot(job) for job in jobs]

    def list_local_recovery_candidates(self) -> list[JobSnapshot]:
        with self.database.session() as session:
            ready_artifact = select(ImageTaskArtifact.id).where(
                ImageTaskArtifact.job_id == ImageJob.id,
                ImageTaskArtifact.kind.in_(["downloaded", "upscaled", "final"]),
                ImageTaskArtifact.status == ArtifactStatus.READY.value,
            ).exists()
            failed_timeout = and_(
                ImageJob.status == JobStatus.FAILED.value,
                ImageJob.error_code == "image_claim_timeout",
                ImageJob.stage == JobStage.FAILED.value,
            )
            jobs = session.execute(
                select(ImageJob)
                .join(ImageTask, ImageTask.id == ImageJob.task_id)
                .where(or_(
                    and_(
                        ImageJob.status.in_([JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value]),
                        ImageJob.stage.in_([
                            JobStage.RESOLVING.value,
                            JobStage.DOWNLOADING.value,
                            JobStage.TRANSFORMING.value,
                            JobStage.SAVING.value,
                        ]),
                    ),
                    failed_timeout,
                ))
                .where(ImageTask.cancel_requested.is_(False))
                .where(~ready_artifact)
                .order_by(ImageJob.available_at, ImageJob.created_at, ImageJob.ordinal, ImageJob.id)
                .limit(100)
            ).scalars().all()
            return [self._job_snapshot(job) for job in jobs]

    def requeue_job_for_recovery(
        self,
        job_id: UUID,
        stage: JobStage,
        now: datetime | None = None,
    ) -> bool:
        recovery_time = _now(now)
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob)
                .where(ImageJob.id == job_id)
                .with_for_update()
            ).scalar_one_or_none()
            if job is None or job.status in {status.value for status in TERMINAL_JOB_STATUSES}:
                return False
            if job.status not in {JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value}:
                return False
            if job.lease_owner is not None or job.lease_token is not None:
                return False
            self._task_for_update(session, job.task_id)
            previous_stage = job.stage
            job.status = JobStatus.QUEUED.value
            job.stage = stage.value
            job.available_at = recovery_time
            job.next_retry_at = None
            job.updated_at = recovery_time
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_recovered",
                from_status=previous_stage,
                to_status=stage.value,
                data={"checkpoint": stage.value},
            )
            return True

    def resume_failed_task(
        self,
        owner_key: str,
        task_or_client_id: object,
        extra_timeout_secs: float = 30.0,
    ) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = self._find_task(session, owner_key, task_or_client_id, lock=True)
            if task is None:
                return None
            if task.cancel_requested:
                raise ValueError("canceled image task cannot be resumed")
            try:
                resume_timeout = min(120.0, max(5.0, float(extra_timeout_secs)))
            except (TypeError, ValueError):
                resume_timeout = 30.0
            request_payload = dict(task.request_payload or {})
            request_payload["resume_poll_timeout_seconds"] = resume_timeout
            task.request_payload = request_payload
            jobs = session.execute(
                select(ImageJob)
                .where(ImageJob.task_id == task.id, ImageJob.status == JobStatus.FAILED.value)
                .with_for_update()
            ).scalars().all()
            resumed = 0
            for job in jobs:
                if job.image_urls:
                    stage = JobStage.DOWNLOADING
                elif job.conversation_id or job.file_ids or job.sediment_ids:
                    stage = JobStage.RESOLVING
                else:
                    continue
                previous_status = job.status
                job.status = JobStatus.QUEUED.value
                job.stage = stage.value
                job.error_code = None
                job.error_message = None
                job.available_at = utc_now()
                job.next_retry_at = None
                job.completed_at = None
                job.updated_at = utc_now()
                if stage in {JobStage.DOWNLOADING, JobStage.RESOLVING}:
                    job.download_attempts = 0
                resumed += 1
                self._event(
                    session,
                    task_id=task.id,
                    job_id=job.id,
                    event_type="job_manually_resumed",
                    from_status=previous_status,
                    to_status=job.status,
                    data={"checkpoint": stage.value, "resume_poll_timeout_seconds": resume_timeout},
                )
            if resumed == 0:
                raise ValueError("image task has no recoverable remote checkpoint")
            task.error_code = None
            task.error_message = None
            task.delivery_status = DeliveryStatus.PENDING.value
            task.response_attempted_at = None
            task.delivery_acked_at = None
            task.completed_at = None
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    @staticmethod
    def _clear_orphan_account_leases(
        session: Session,
        job_id: UUID,
        *,
        now: datetime,
    ) -> None:
        """Drop only expired account leases for a claimable job.

        Must not delete an in-flight winner's lease while a concurrent claimer
        still holds a stale SELECT of the same queued job.
        """
        session.execute(delete(ImageAccountLease).where(
            ImageAccountLease.job_id == job_id,
            ImageAccountLease.expires_at <= now,
        ))

    @staticmethod
    def _clear_expired_inactive_account_leases(
        session: Session,
        *,
        now: datetime,
    ) -> None:
        session.execute(delete(ImageAccountLease).where(
            ImageAccountLease.expires_at <= now,
            ImageAccountLease.job_id.in_(
                select(ImageJob.id).where(ImageJob.status.not_in([
                    JobStatus.LEASED.value,
                    JobStatus.RUNNING.value,
                ]))
            ),
        ))

    @staticmethod
    def _acquire_account_slot(
        session: Session,
        *,
        candidate: ImageAccountCandidate,
        account_concurrency: int,
        job: ImageJob,
        worker_id: str,
        lease_token: UUID,
        lease_version: int,
        expires_at: datetime,
        now: datetime,
    ) -> int | None:
        for slot_no in range(max(1, int(account_concurrency))):
            values = {
                "account_id": candidate.account_id,
                "slot_no": slot_no,
                "job_id": job.id,
                "lease_owner": worker_id,
                "lease_token": lease_token,
                "lease_version": lease_version,
                "expires_at": expires_at,
                "heartbeat_at": now,
            }
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = (
                    postgresql_insert(ImageAccountLease)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["account_id", "slot_no"])
                    .returning(ImageAccountLease.slot_no)
                )
                # job_id uniqueness races surface as IntegrityError and are retried
                # by claim_next_job without crashing the dispatcher.
                acquired = session.execute(statement).scalar_one_or_none()
                if acquired is not None:
                    return int(acquired)
                continue
            existing = session.get(ImageAccountLease, {
                "account_id": candidate.account_id,
                "slot_no": slot_no,
            })
            if existing is not None:
                continue
            session.add(ImageAccountLease(**values))
            session.flush()
            return slot_no
        return None

    def claim_next_job(
        self,
        worker_id: str,
        account_candidates: Sequence[ImageAccountCandidate],
        account_concurrency: int,
        now: datetime | None = None,
        *,
        allow_generation: bool = True,
        recovery_only: bool = False,
        prefer_recovery: bool = True,
        local_artifact_available: Callable[[JobSnapshot, Sequence[ArtifactDescriptor]], bool] | None = None,
        allow_unowned_local_artifacts: bool = True,
        expected_process_instance_id: str = "",
    ) -> ClaimedJob | None:
        claim_time = _now(now)
        swallow_sqlalchemy_races = self.database.engine is not None and self.database.engine.dialect.name == "sqlite"
        # A few attempts cover SKIP LOCKED races, unique lease collisions, and
        # SQLite connection noise without bubbling into the dispatcher loop.
        for _attempt in range(5):
            try:
                return self._claim_next_job_once(
                    worker_id,
                    account_candidates,
                    account_concurrency,
                    claim_time,
                    allow_generation=allow_generation,
                    recovery_only=recovery_only,
                    prefer_recovery=prefer_recovery,
                    local_artifact_available=local_artifact_available,
                    allow_unowned_local_artifacts=allow_unowned_local_artifacts,
                    expected_process_instance_id=expected_process_instance_id,
                )
            except IntegrityError:
                continue
            except SQLAlchemyError:
                # Includes sqlite "no more rows" / nested-transaction races under
                # concurrent claimers. Returning None lets the worker poll again.
                if not swallow_sqlalchemy_races:
                    raise
                continue
            except ImageQueueUnavailableError:
                if not swallow_sqlalchemy_races:
                    raise
                continue
        return None

    def _claim_next_job_once(
        self,
        worker_id: str,
        account_candidates: Sequence[ImageAccountCandidate],
        account_concurrency: int,
        claim_time: datetime,
        *,
        allow_generation: bool = True,
        recovery_only: bool = False,
        prefer_recovery: bool = True,
        local_artifact_available: Callable[[JobSnapshot, Sequence[ArtifactDescriptor]], bool] | None = None,
        allow_unowned_local_artifacts: bool = True,
        expected_process_instance_id: str = "",
    ) -> ClaimedJob | None:
        with self.database.session() as session:
            self._ensure_worker_process_current(
                session,
                worker_id,
                expected_process_instance_id=expected_process_instance_id,
            )
            self._clear_expired_inactive_account_leases(session, now=claim_time)
            after_sort_key: tuple[datetime, datetime, int, UUID] | None = None
            # When the recovery pool is saturated, skip the recovery pass so we
            # do not claim a saving/download job only to release it (which would
            # burn attempt counters).
            recovery_pass = bool(prefer_recovery or recovery_only)
            if not recovery_pass and not allow_generation:
                return None
            while True:
                job = session.execute(
                    claimable_job_statement(
                        claim_time,
                        after_sort_key=after_sort_key,
                        recovery_only=recovery_pass,
                        generation_only=(not recovery_pass and not recovery_only),
                    )
                ).scalar_one_or_none()
                if job is None:
                    if recovery_pass and allow_generation and not recovery_only:
                        recovery_pass = False
                        after_sort_key = None
                        continue
                    return None
                after_sort_key = (job.available_at, job.created_at, job.ordinal, job.id)
                self._clear_orphan_account_leases(session, job.id, now=claim_time)
                lease_token = uuid4()
                lease_version = int(job.lease_version or 0) + 1
                expires_at = claim_time + timedelta(seconds=self.lease_seconds)
                chosen: ImageAccountCandidate | None = None
                slot_no: int | None = None
                candidates = list(account_candidates)
                restricted_candidates = False
                accountless_recovery = False
                local_artifacts: list[ArtifactDescriptor] = []
                foreign_local_artifacts = False
                local_recovery_stage = job.stage not in {
                    JobStage.QUEUED.value,
                    JobStage.LEASED.value,
                    JobStage.GENERATING.value,
                }
                if local_recovery_stage:
                    local_artifact_rows = session.execute(
                        select(ImageTaskArtifact)
                        .where(
                            ImageTaskArtifact.job_id == job.id,
                            ImageTaskArtifact.kind.in_(["downloaded", "upscaled", "final"]),
                            ImageTaskArtifact.status == ArtifactStatus.READY.value,
                        )
                        .order_by(ImageTaskArtifact.kind, ImageTaskArtifact.created_at.desc())
                    ).scalars().all()
                    local_artifact_descriptors = [
                        self._artifact_descriptor(item) for item in local_artifact_rows
                    ]
                    current_worker_id = str(worker_id or "").strip()
                    if current_worker_id:
                        if allow_unowned_local_artifacts:
                            local_artifacts = local_artifact_descriptors
                        else:
                            foreign_local_artifacts = any(
                                artifact.worker_id
                                and artifact.worker_id != current_worker_id
                                for artifact in local_artifact_descriptors
                            )
                            local_artifacts = [
                                artifact
                                for artifact in local_artifact_descriptors
                                if artifact.worker_id == current_worker_id
                                or (allow_unowned_local_artifacts and not artifact.worker_id)
                            ]
                    else:
                        local_artifacts = local_artifact_descriptors
                local_recovery = bool(local_artifacts)
                if local_recovery and local_artifact_available is not None:
                    local_recovery = bool(local_artifact_available(self._job_snapshot(job), local_artifacts))
                has_remote_checkpoint = bool(
                    job.conversation_id or job.image_urls or job.file_ids or job.sediment_ids
                )
                if foreign_local_artifacts and not local_artifacts and not has_remote_checkpoint:
                    task = self._task_for_update(session, job.task_id)
                    updated_at = job.updated_at
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    expired = claim_time >= updated_at + timedelta(
                        seconds=max(30, int(self.recovery_account_timeout_seconds))
                    )
                    if expired:
                        previous_status = job.status
                        job.status = JobStatus.FAILED.value
                        job.stage = JobStage.FAILED.value
                        job.error_code = "worker_local_recovery_unavailable"
                        job.error_message = "local recovery artifact belongs to another worker"
                        job.completed_at = claim_time
                        job.updated_at = claim_time
                        self._event(
                            session,
                            task_id=job.task_id,
                            job_id=job.id,
                            event_type="job_failed",
                            attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                            from_status=previous_status,
                            to_status=job.status,
                            data={"error_code": job.error_code},
                        )
                        if task is not None:
                            self._aggregate_task(session, task)
                    elif task is not None:
                        task.wait_reason = "worker_local_recovery"
                        task.updated_at = claim_time
                    continue
                if local_recovery:
                    chosen = ImageAccountCandidate(
                        account_id=job.account_id or UUID(int=0),
                        access_token="",
                    )
                    slot_no = -1
                elif job.image_urls and (
                    job.account_id is None
                    or not any(candidate.account_id == job.account_id for candidate in candidates)
                ):
                    # Persisted signed URLs can be downloaded without the account
                    # that created the conversation. Same-origin URLs will fail via
                    # the normal bounded download retry path instead of being lost
                    # solely because an account was removed.
                    chosen = ImageAccountCandidate(
                        account_id=job.account_id or UUID(int=0),
                        access_token="",
                    )
                    slot_no = -2
                    accountless_recovery = True
                elif has_remote_checkpoint and job.account_id is not None:
                    restricted_candidates = True
                    candidates = [candidate for candidate in candidates if candidate.account_id == job.account_id]
                    if not candidates:
                        task = self._task_for_update(session, job.task_id)
                        updated_at = job.updated_at
                        if updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=timezone.utc)
                        expired = claim_time >= updated_at + timedelta(
                            seconds=max(30, int(self.recovery_account_timeout_seconds))
                        )
                        if expired:
                            previous_status = job.status
                            job.status = JobStatus.FAILED.value
                            job.stage = JobStage.FAILED.value
                            job.error_code = "recovery_account_unavailable"
                            job.error_message = "original account for remote image recovery is unavailable"
                            job.completed_at = claim_time
                            job.updated_at = claim_time
                            self._event(
                                session,
                                task_id=job.task_id,
                                job_id=job.id,
                                event_type="job_failed",
                                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                                from_status=previous_status,
                                to_status=job.status,
                                data={"error_code": job.error_code},
                            )
                            if task is not None:
                                self._aggregate_task(session, task)
                            continue
                        if task is not None:
                            task.wait_reason = "recovery_account"
                            task.updated_at = claim_time
                        continue
                elif (
                    job.error_code == "auth_invalid"
                    and job.account_id is not None
                    and len(candidates) > 1
                ):
                    restricted_candidates = True
                    candidates = [candidate for candidate in candidates if candidate.account_id != job.account_id]
                if not local_recovery and not accountless_recovery:
                    for candidate in candidates:
                        slot_no = self._acquire_account_slot(
                            session,
                            candidate=candidate,
                            account_concurrency=account_concurrency,
                            job=job,
                            worker_id=worker_id,
                            lease_token=lease_token,
                            lease_version=lease_version,
                            expires_at=expires_at,
                            now=claim_time,
                        )
                        if slot_no is not None:
                            chosen = candidate
                            break
                task = session.get(ImageTask, job.task_id)
                if chosen is None or slot_no is None:
                    if task is not None:
                        task.wait_reason = "account_capacity"
                        task.updated_at = claim_time
                    if not recovery_pass and not restricted_candidates:
                        return None
                    continue

                previous_status = job.status
                previous_stage = job.stage
                next_stage = (
                    JobStage.LEASED.value
                    if job.stage in {JobStage.QUEUED.value, JobStage.RETRY_WAIT.value}
                    else job.stage
                )
                generate_attempts = int(job.generate_attempts or 0)
                download_attempts = int(job.download_attempts or 0)
                save_attempts = int(job.save_attempts or 0)
                if job.stage in {JobStage.DOWNLOADING.value, JobStage.RESOLVING.value}:
                    download_attempts += 1
                elif job.stage in {JobStage.TRANSFORMING.value, JobStage.SAVING.value}:
                    save_attempts += 1
                else:
                    generate_attempts += 1
                timings = dict(job.stage_timings or {})
                stamp = claim_time.isoformat()
                if previous_stage and previous_stage != next_stage:
                    entry = dict(timings.get(previous_stage) or {})
                    entry.setdefault("started_at", stamp)
                    entry["ended_at"] = stamp
                    timings[previous_stage] = entry
                entry = dict(timings.get(next_stage) or {})
                entry.setdefault("started_at", stamp)
                entry.pop("ended_at", None)
                timings[next_stage] = entry

                # Atomic ownership transfer: works on PostgreSQL SKIP LOCKED and
                # also prevents SQLite double-claim because only one UPDATE wins.
                claimed = session.execute(
                    update(ImageJob)
                    .where(
                        ImageJob.id == job.id,
                        ImageJob.status.in_([
                            JobStatus.QUEUED.value,
                            JobStatus.RETRY_WAIT.value,
                        ]),
                        ImageJob.lease_version == int(job.lease_version or 0),
                    )
                    .values(
                        status=JobStatus.LEASED.value,
                        stage=next_stage,
                        generate_attempts=generate_attempts,
                        download_attempts=download_attempts,
                        save_attempts=save_attempts,
                        stage_timings=timings,
                        lease_owner=worker_id,
                        lease_token=lease_token,
                        lease_version=lease_version,
                        lease_expires_at=expires_at,
                        heartbeat_at=claim_time,
                        account_id=(chosen.account_id if slot_no >= 0 else job.account_id),
                        started_at=job.started_at or claim_time,
                        updated_at=claim_time,
                    )
                )
                if int(claimed.rowcount or 0) != 1:
                    # Lost the race — drop the account lease we may have inserted.
                    if slot_no is not None and slot_no >= 0:
                        session.execute(delete(ImageAccountLease).where(
                            ImageAccountLease.job_id == job.id,
                            ImageAccountLease.lease_token == lease_token,
                            ImageAccountLease.lease_version == lease_version,
                        ))
                    continue

                if task is not None:
                    task.status = TaskStatus.RUNNING.value
                    task.wait_reason = None
                    task.started_at = task.started_at or claim_time
                    task.updated_at = claim_time
                    task.version += 1
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="job_leased",
                    attempt=generate_attempts + download_attempts + save_attempts,
                    from_status=previous_status,
                    to_status=JobStatus.LEASED.value,
                    data={
                        "worker_id": worker_id,
                        "account_id": str(chosen.account_id),
                        "slot_no": slot_no,
                    },
                )
                session.flush()
                session.refresh(job)
                return ClaimedJob(
                    job=self._job_snapshot(job),
                    lease_token=lease_token,
                    lease_version=lease_version,
                    lease_owner=worker_id,
                    lease_expires_at=expires_at,
                    account_id=chosen.account_id,
                    account_slot=slot_no,
                )

    @staticmethod
    def _claimed_job(session: Session, claim: ClaimedJob, *, lock: bool = True) -> ImageJob | None:
        statement = select(ImageJob).where(
            ImageJob.id == claim.job.id,
            ImageJob.lease_token == claim.lease_token,
            ImageJob.lease_version == claim.lease_version,
            ImageJob.lease_owner == claim.lease_owner,
            ImageJob.lease_expires_at.is_not(None),
            ImageJob.lease_expires_at > utc_now(),
        )
        if lock:
            statement = statement.with_for_update()
        return session.execute(statement).scalar_one_or_none()

    @staticmethod
    def _task_for_update(session: Session, task_id: UUID) -> ImageTask | None:
        return session.execute(
            select(ImageTask)
            .where(ImageTask.id == task_id)
            .with_for_update()
        ).scalar_one_or_none()

    def _claimed_job_and_task(
        self,
        session: Session,
        claim: ClaimedJob,
    ) -> tuple[ImageJob | None, ImageTask | None]:
        job = self._claimed_job(session, claim)
        if job is None:
            return None, None
        return job, self._task_for_update(session, job.task_id)

    @staticmethod
    def _release_account_lease(session: Session, claim: ClaimedJob) -> None:
        session.execute(delete(ImageAccountLease).where(
            ImageAccountLease.job_id == claim.job.id,
            ImageAccountLease.lease_token == claim.lease_token,
            ImageAccountLease.lease_version == claim.lease_version,
        ))

    def checkpoint_job(self, claim: ClaimedJob, checkpoint: JobCheckpoint) -> bool:
        with self.database.session() as session:
            job, task = self._claimed_job_and_task(session, claim)
            if job is None:
                return False
            previous_stage = job.stage
            previous_group = self._attempt_group(previous_stage)
            next_group = self._attempt_group(checkpoint.stage.value)
            if next_group != previous_group:
                if next_group == "download":
                    job.download_attempts += 1
                elif next_group == "save":
                    job.save_attempts += 1
            now = utc_now()
            self._touch_stage_timing(job, previous_stage, checkpoint.stage.value, now)
            job.stage = checkpoint.stage.value
            job.status = JobStatus.RUNNING.value
            if checkpoint.stage in {JobStage.TRANSFORMING, JobStage.SAVING}:
                self._release_account_lease(session, claim)
            if checkpoint.conversation_id:
                job.conversation_id = checkpoint.conversation_id
            if checkpoint.image_urls:
                job.image_urls = list(checkpoint.image_urls)
                job.quota_consumed = True
            if checkpoint.file_ids:
                job.file_ids = list(checkpoint.file_ids)
            if checkpoint.sediment_ids:
                job.sediment_ids = list(checkpoint.sediment_ids)
            job.updated_at = now
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_checkpoint",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_stage,
                to_status=job.stage,
                data={
                    "conversation_id": job.conversation_id or "",
                    "image_urls": list(job.image_urls or []),
                    "file_ids": list(job.file_ids or []),
                    "sediment_ids": list(job.sediment_ids or []),
                },
            )
            if task is not None:
                self._aggregate_task(session, task)
            return True

    @staticmethod
    def _touch_stage_timing(
        job: ImageJob,
        previous_stage: str,
        next_stage: str,
        now: datetime,
    ) -> None:
        timings = dict(job.stage_timings or {})
        previous = str(previous_stage or "").strip()
        nxt = str(next_stage or "").strip()
        stamp = now.isoformat()
        if previous and previous != nxt:
            entry = dict(timings.get(previous) or {})
            entry.setdefault("started_at", stamp)
            entry["ended_at"] = stamp
            timings[previous] = entry
        if nxt:
            entry = dict(timings.get(nxt) or {})
            entry.setdefault("started_at", stamp)
            entry.pop("ended_at", None)
            timings[nxt] = entry
        job.stage_timings = timings

    def mark_quota_consumed(self, claim: ClaimedJob) -> bool:
        with self.database.session() as session:
            job, _task = self._claimed_job_and_task(session, claim)
            if job is None:
                return False
            if not job.quota_consumed:
                job.quota_consumed = True
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="quota_consumed",
                    attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                )
            return True

    def mark_quota_accounted(self, job_id: UUID, account_id: UUID) -> bool:
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob).where(ImageJob.id == job_id).with_for_update()
            ).scalar_one_or_none()
            if job is None or not job.quota_consumed or job.quota_accounted:
                return False
            if job.account_id is not None and job.account_id != account_id:
                return False
            self._task_for_update(session, job.task_id)
            job.quota_accounted = True
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="quota_accounted",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                data={"account_id": str(account_id)},
            )
            return True

    def list_unaccounted_terminal_quota_jobs(self, limit: int = 100) -> list[JobSnapshot]:
        with self.database.session() as session:
            jobs = session.execute(
                select(ImageJob)
                .where(
                    ImageJob.quota_consumed.is_(True),
                    ImageJob.quota_accounted.is_(False),
                    ImageJob.account_id.is_not(None),
                    ImageJob.status.in_([status.value for status in TERMINAL_JOB_STATUSES]),
                )
                .order_by(ImageJob.completed_at, ImageJob.created_at)
                .limit(max(1, min(1000, int(limit))))
            ).scalars().all()
            return [self._job_snapshot(job) for job in jobs]

    def list_unaccounted_quota_jobs_for_task(
        self,
        owner_key: str,
        task_id: UUID,
        limit: int = 100,
    ) -> list[JobSnapshot]:
        owner = str(owner_key or "").strip()
        with self.database.session() as session:
            task = session.execute(
                select(ImageTask.id).where(ImageTask.id == task_id, ImageTask.owner_key == owner)
            ).scalar_one_or_none()
            if task is None:
                return []
            jobs = session.execute(
                select(ImageJob)
                .where(
                    ImageJob.task_id == task_id,
                    ImageJob.quota_consumed.is_(True),
                    ImageJob.quota_accounted.is_(False),
                    ImageJob.account_id.is_not(None),
                    ImageJob.status.in_([status.value for status in TERMINAL_JOB_STATUSES]),
                )
                .order_by(ImageJob.completed_at, ImageJob.created_at, ImageJob.ordinal)
                .limit(max(1, min(1000, int(limit))))
            ).scalars().all()
            return [self._job_snapshot(job) for job in jobs]

    def heartbeat_claims(
        self,
        worker_id: str,
        claims: Iterable[ClaimedJob],
        now: datetime | None = None,
    ) -> int:
        heartbeat_at = _now(now)
        expires_at = heartbeat_at + timedelta(seconds=self.lease_seconds)
        updated = 0
        with self.database.session() as session:
            for claim in claims:
                if claim.lease_owner != worker_id:
                    continue
                result = session.execute(update(ImageJob).where(
                    ImageJob.id == claim.job.id,
                    ImageJob.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]),
                    ImageJob.lease_owner == worker_id,
                    ImageJob.lease_token == claim.lease_token,
                    ImageJob.lease_version == claim.lease_version,
                    ImageJob.lease_expires_at > heartbeat_at,
                ).values(heartbeat_at=heartbeat_at, lease_expires_at=expires_at))
                if int(result.rowcount or 0) != 1:
                    continue
                session.execute(update(ImageAccountLease).where(
                    ImageAccountLease.job_id == claim.job.id,
                    ImageAccountLease.lease_token == claim.lease_token,
                    ImageAccountLease.lease_version == claim.lease_version,
                    ImageAccountLease.expires_at > heartbeat_at,
                ).values(heartbeat_at=heartbeat_at, expires_at=expires_at))
                updated += 1
        return updated

    @staticmethod
    def _artifact_row(
        descriptor: ArtifactDescriptor,
        *,
        worker_id: str = "",
        payload_blob: bytes | None = None,
    ) -> ImageTaskArtifact:
        return ImageTaskArtifact(
            task_id=descriptor.task_id,
            job_id=descriptor.job_id,
            kind=descriptor.kind,
            ordinal=descriptor.ordinal,
            status=descriptor.status.value,
            storage_backend=descriptor.storage_backend,
            worker_id=str(worker_id or "").strip(),
            relative_path=descriptor.relative_path,
            sha256=descriptor.sha256,
            mime_type=descriptor.mime_type,
            byte_size=descriptor.byte_size,
            width=descriptor.width,
            height=descriptor.height,
            source_url=descriptor.source_url or None,
            payload_blob=payload_blob,
            ready_at=utc_now() if descriptor.status.value == "ready" else None,
        )

    def get_artifact_payload(self, relative_path: str) -> bytes:
        path = str(relative_path or "").strip()
        if not path:
            raise FileNotFoundError("artifact payload path is empty")
        with self.database.session() as session:
            artifact = session.execute(
                select(ImageTaskArtifact).where(ImageTaskArtifact.relative_path == path)
            ).scalar_one_or_none()
            if artifact is None or artifact.payload_blob is None:
                raise FileNotFoundError("artifact payload is not stored in queue")
            payload = bytes(artifact.payload_blob)
            if len(payload) != int(artifact.byte_size):
                raise ValueError("artifact payload size mismatch")
            if sha256(payload).hexdigest() != artifact.sha256:
                raise ValueError("artifact payload checksum mismatch")
            return payload

    def record_artifact(
        self,
        claim: ClaimedJob,
        artifact: ArtifactDescriptor,
        *,
        worker_id: str | None = None,
    ) -> bool:
        with self.database.session() as session:
            job, _task = self._claimed_job_and_task(session, claim)
            if job is None:
                return False
            if artifact.task_id != job.task_id or artifact.job_id != job.id:
                raise ValueError("artifact does not belong to claimed job")
            resolved_worker_id = str(worker_id or claim.lease_owner or job.lease_owner or "").strip()
            existing = session.execute(select(ImageTaskArtifact).where(
                ImageTaskArtifact.relative_path == artifact.relative_path,
            )).scalar_one_or_none()
            was_ready = existing is not None and existing.status == ArtifactStatus.READY.value
            if existing is None:
                session.add(self._artifact_row(artifact, worker_id=resolved_worker_id))
            else:
                if existing.task_id != artifact.task_id or existing.job_id != artifact.job_id:
                    raise ValueError("artifact path belongs to a different image job")
                existing.kind = artifact.kind
                existing.ordinal = artifact.ordinal
                existing.status = artifact.status.value
                existing.storage_backend = artifact.storage_backend
                existing.worker_id = resolved_worker_id or existing.worker_id or ""
                existing.sha256 = artifact.sha256
                existing.mime_type = artifact.mime_type
                existing.byte_size = artifact.byte_size
                existing.width = artifact.width
                existing.height = artifact.height
                existing.source_url = artifact.source_url or None
                existing.ready_at = utc_now() if artifact.status == ArtifactStatus.READY else None
            if not was_ready:
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="artifact_ready",
                    attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                    data={
                        "kind": artifact.kind,
                        "artifact_path": artifact.relative_path,
                        "sha256": artifact.sha256,
                    },
                )
            return True

    def invalidate_recovery_artifacts(
        self,
        claim: ClaimedJob,
        *,
        kinds: Sequence[str] = ("downloaded", "upscaled", "final"),
    ) -> int:
        with self.database.session() as session:
            job, _task = self._claimed_job_and_task(session, claim)
            if job is None:
                return 0
            artifacts = session.execute(
                select(ImageTaskArtifact)
                .where(
                    ImageTaskArtifact.job_id == job.id,
                    ImageTaskArtifact.kind.in_(tuple(kinds)),
                    ImageTaskArtifact.status == ArtifactStatus.READY.value,
                )
                .with_for_update()
            ).scalars().all()
            for artifact in artifacts:
                artifact.status = ArtifactStatus.INVALID.value
                artifact.ready_at = None
            if artifacts:
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="local_recovery_artifacts_invalidated",
                    attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                    data={
                        "paths": [artifact.relative_path for artifact in artifacts],
                        "kinds": sorted({artifact.kind for artifact in artifacts}),
                    },
                )
            return len(artifacts)

    def adopt_recovery_artifact(
        self,
        job_id: UUID,
        artifact: ArtifactDescriptor,
        *,
        worker_id: str = "",
    ) -> bool:
        if artifact.kind not in {"downloaded", "upscaled", "final"}:
            raise ValueError("only image job output artifacts can be recovered")
        if artifact.status != ArtifactStatus.READY:
            raise ValueError("only ready artifacts can be recovered")
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob)
                .where(ImageJob.id == job_id)
                .with_for_update()
            ).scalar_one_or_none()
            recoverable_timeout_failure = (
                job is not None
                and job.status == JobStatus.FAILED.value
                and job.error_code == "image_claim_timeout"
            )
            if job is None or (
                job.status not in {JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value}
                and not recoverable_timeout_failure
            ):
                return False
            task = self._task_for_update(session, job.task_id)
            if artifact.task_id != job.task_id or artifact.job_id != job.id:
                raise ValueError("artifact does not belong to recoverable job")
            existing = session.execute(select(ImageTaskArtifact.id).where(
                ImageTaskArtifact.relative_path == artifact.relative_path,
            )).scalar_one_or_none()
            if existing is not None:
                return False
            session.add(self._artifact_row(artifact, worker_id=worker_id))
            if recoverable_timeout_failure:
                recovery_time = utc_now()
                previous_status = job.status
                job.status = JobStatus.QUEUED.value
                job.stage = {
                    "downloaded": JobStage.TRANSFORMING.value,
                    "upscaled": JobStage.SAVING.value,
                    "final": JobStage.SAVING.value,
                }[artifact.kind]
                job.available_at = recovery_time
                job.next_retry_at = None
                job.completed_at = None
                job.error_code = None
                job.error_message = None
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                job.updated_at = recovery_time
                task.error_code = None
                task.error_message = None
                task.completed_at = None
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="job_recovered",
                    from_status=previous_status,
                    to_status=job.status,
                    data={
                        "kind": artifact.kind,
                        "artifact_path": artifact.relative_path,
                        "sha256": artifact.sha256,
                        "recovery": "timeout",
                    },
                )
                self._aggregate_task(session, task)
                return True
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="artifact_recovered",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                data={
                    "kind": artifact.kind,
                    "artifact_path": artifact.relative_path,
                    "sha256": artifact.sha256,
                },
            )
            return True

    def record_claim_event(
        self,
        claim: ClaimedJob,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        with self.database.session() as session:
            job, _task = self._claimed_job_and_task(session, claim)
            if job is None:
                return False
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type=str(event_type),
                attempt=max(job.generate_attempts, job.download_attempts, job.save_attempts),
                data=data,
            )
            return True

    def complete_job(
        self,
        claim: ClaimedJob,
        artifact: ArtifactDescriptor,
        result_payload: dict[str, Any],
    ) -> TaskSnapshot | None:
        with self.database.session() as session:
            job, task = self._claimed_job_and_task(session, claim)
            if job is None:
                return None
            if task is not None and task.cancel_requested and task.status == TaskStatus.CANCELED.value and not bool(job.quota_consumed):
                previous_status = job.status
                now = utc_now()
                self._release_account_lease(session, claim)
                job.status = JobStatus.CANCELED.value
                job.stage = JobStage.CANCELED.value
                job.completed_at = job.completed_at or now
                job.updated_at = now
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="job_canceled",
                    from_status=previous_status,
                    to_status=job.status,
                )
                self._aggregate_task(session, task)
                session.flush()
                return self._task_snapshot(session, task)
            if artifact.task_id != job.task_id or artifact.job_id != job.id:
                raise ValueError("artifact does not belong to claimed job")
            resolved_worker_id = str(claim.lease_owner or job.lease_owner or "").strip()
            existing_artifact = session.execute(select(ImageTaskArtifact).where(
                ImageTaskArtifact.relative_path == artifact.relative_path,
            )).scalar_one_or_none()
            if existing_artifact is None:
                session.add(self._artifact_row(artifact, worker_id=resolved_worker_id))
            else:
                if existing_artifact.task_id != artifact.task_id or existing_artifact.job_id != artifact.job_id:
                    raise ValueError("artifact path belongs to a different image job")
                if existing_artifact.kind != artifact.kind or existing_artifact.sha256 != artifact.sha256:
                    raise ValueError("artifact path conflicts with a different image artifact")
                existing_artifact.ordinal = artifact.ordinal
                existing_artifact.status = artifact.status.value
                existing_artifact.storage_backend = artifact.storage_backend
                existing_artifact.worker_id = resolved_worker_id or existing_artifact.worker_id or ""
                existing_artifact.mime_type = artifact.mime_type
                existing_artifact.byte_size = artifact.byte_size
                existing_artifact.width = artifact.width
                existing_artifact.height = artifact.height
                existing_artifact.source_url = artifact.source_url or None
                existing_artifact.ready_at = utc_now() if artifact.status == ArtifactStatus.READY else None
            previous_status = job.status
            previous_stage = job.stage
            now = utc_now()
            self._touch_stage_timing(job, previous_stage, JobStage.SUCCESS.value, now)
            job.quota_consumed = True
            job.status = JobStatus.SUCCESS.value
            job.stage = JobStage.SUCCESS.value
            job.result_payload = {
                **dict(result_payload),
                "url": str(result_payload.get("url") or artifact.public_url or ""),
                "width": artifact.width,
                "height": artifact.height,
                "relative_path": artifact.relative_path,
            }
            job.error_code = None
            job.error_message = None
            job.completed_at = now
            job.updated_at = now
            self._release_account_lease(session, claim)
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_succeeded",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_status,
                to_status=job.status,
                data={"artifact_path": artifact.relative_path, "sha256": artifact.sha256},
            )
            if task is None:
                return None
            self._aggregate_task(session, task)
            session.flush()
            return self._task_snapshot(session, task)

    def requeue_undeliverable_result(
        self,
        owner_key: str,
        task_id: UUID,
        job_id: UUID,
        *,
        local_recovery_kind: str = "",
        error_message: str = "",
    ) -> TaskSnapshot | None:
        if local_recovery_kind not in {"", "downloaded", "upscaled"}:
            raise ValueError("invalid local recovery artifact kind")
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob)
                .where(ImageJob.id == job_id, ImageJob.task_id == task_id)
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                return None
            task = session.execute(
                select(ImageTask)
                .where(ImageTask.id == task_id, ImageTask.owner_key == owner_key)
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                return None
            if job.status != JobStatus.SUCCESS.value:
                return self._task_snapshot(session, task)
            if local_recovery_kind == "upscaled":
                recovery_stage = JobStage.SAVING
                discard_kinds = ["final"]
            elif local_recovery_kind == "downloaded":
                recovery_stage = JobStage.TRANSFORMING
                discard_kinds = ["final", "upscaled"]
            elif job.image_urls:
                recovery_stage = JobStage.DOWNLOADING
                discard_kinds = ["final", "upscaled", "downloaded"]
            elif job.conversation_id or job.file_ids or job.sediment_ids:
                recovery_stage = JobStage.RESOLVING
                discard_kinds = ["final", "upscaled", "downloaded"]
            else:
                return None
            previous_status = job.status
            session.execute(delete(ImageTaskArtifact).where(
                ImageTaskArtifact.job_id == job.id,
                ImageTaskArtifact.kind.in_(discard_kinds),
            ))
            job.status = JobStatus.QUEUED.value
            job.stage = recovery_stage.value
            job.result_payload = {}
            job.error_code = None
            job.error_message = None
            job.available_at = utc_now()
            job.next_retry_at = None
            job.completed_at = None
            job.updated_at = utc_now()
            task.delivery_status = DeliveryStatus.PENDING.value
            task.response_attempted_at = None
            task.delivery_acked_at = None
            task.completed_at = None
            task.error_code = None
            task.error_message = None
            self._event(
                session,
                task_id=task.id,
                job_id=job.id,
                event_type="result_artifact_recovery_queued",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_status,
                to_status=job.status,
                data={
                    "recovery_stage": recovery_stage.value,
                    "error": str(error_message or "final artifact is not deliverable")[:500],
                },
            )
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    def fail_undeliverable_result(
        self,
        owner_key: str,
        task_id: UUID,
        job_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> TaskSnapshot | None:
        failed_at = utc_now()
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob)
                .where(ImageJob.id == job_id, ImageJob.task_id == task_id)
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                return None
            task = session.execute(
                select(ImageTask)
                .where(ImageTask.id == task_id, ImageTask.owner_key == owner_key)
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                return None
            if job.status != JobStatus.SUCCESS.value:
                return self._task_snapshot(session, task)
            previous_status = job.status
            previous_stage = job.stage
            self._touch_stage_timing(job, previous_stage, JobStage.FAILED.value, failed_at)
            job.status = JobStatus.FAILED.value
            job.stage = JobStage.FAILED.value
            job.result_payload = {}
            job.error_code = error_code
            job.error_message = error_message
            job.available_at = failed_at
            job.completed_at = failed_at
            job.updated_at = failed_at
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="result_url_unreachable",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_status,
                to_status=job.status,
                data={
                    "error_code": error_code,
                    "error_message": error_message[:1000],
                },
            )
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    @staticmethod
    def _increment_attempt(job: ImageJob) -> None:
        if job.stage in {JobStage.DOWNLOADING.value, JobStage.RESOLVING.value}:
            job.download_attempts += 1
        elif job.stage in {JobStage.TRANSFORMING.value, JobStage.SAVING.value}:
            job.save_attempts += 1
        else:
            job.generate_attempts += 1

    @staticmethod
    def _attempt_group(stage: str) -> str:
        if stage in {JobStage.DOWNLOADING.value, JobStage.RESOLVING.value}:
            return "download"
        if stage in {JobStage.TRANSFORMING.value, JobStage.SAVING.value}:
            return "save"
        return "generate"

    def schedule_retry(
        self,
        claim: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
        next_retry_at: datetime,
    ) -> TaskSnapshot | None:
        with self.database.session() as session:
            job, task = self._claimed_job_and_task(session, claim)
            if job is None:
                return None
            previous_status = job.status
            job.status = JobStatus.RETRY_WAIT.value
            job.error_code = error_code
            job.error_message = error_message
            job.next_retry_at = next_retry_at
            job.available_at = next_retry_at
            job.updated_at = utc_now()
            self._release_account_lease(session, claim)
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_retry_scheduled",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_status,
                to_status=job.status,
                data={"error_code": error_code, "next_retry_at": next_retry_at.isoformat()},
            )
            if task is None:
                return None
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    def fail_job(
        self,
        claim: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
    ) -> TaskSnapshot | None:
        with self.database.session() as session:
            job, task = self._claimed_job_and_task(session, claim)
            if job is None:
                return None
            previous_status = job.status
            job.status = JobStatus.FAILED.value
            job.stage = JobStage.FAILED.value
            job.error_code = error_code
            job.error_message = error_message
            job.completed_at = utc_now()
            job.updated_at = utc_now()
            self._release_account_lease(session, claim)
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_failed",
                from_status=previous_status,
                to_status=job.status,
                data={"error_code": error_code, "error_message": error_message[:1000]},
            )
            if task is None:
                return None
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    def fail_timed_out_claim(
        self,
        claim: ClaimedJob,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
        quota_consumed: bool = False,
    ) -> TaskSnapshot | None:
        failed_at = _now(now)
        with self.database.session() as session:
            job = session.execute(
                select(ImageJob)
                .where(
                    ImageJob.id == claim.job.id,
                    ImageJob.lease_token == claim.lease_token,
                    ImageJob.lease_version == claim.lease_version,
                    ImageJob.lease_owner == claim.lease_owner,
                    ImageJob.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]),
                )
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                return None
            task = self._task_for_update(session, job.task_id)
            previous_status = job.status
            previous_stage = job.stage
            self._touch_stage_timing(job, previous_stage, JobStage.FAILED.value, failed_at)
            job.status = JobStatus.FAILED.value
            job.stage = JobStage.FAILED.value
            job.error_code = error_code
            job.error_message = error_message
            job.available_at = failed_at
            job.completed_at = failed_at
            job.updated_at = failed_at
            if quota_consumed or bool(job.quota_consumed):
                job.quota_consumed = True
            self._release_account_lease(session, claim)
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            self._event(
                session,
                task_id=job.task_id,
                job_id=job.id,
                event_type="job_claim_timed_out",
                attempt=job.generate_attempts + job.download_attempts + job.save_attempts,
                from_status=previous_status,
                to_status=job.status,
                data={"error_code": error_code, "error_message": error_message[:1000]},
            )
            if task is None:
                return None
            self._aggregate_task(session, task)
            return self._task_snapshot(session, task)

    def release_claim(self, claim: ClaimedJob) -> bool:
        with self.database.session() as session:
            job, task = self._claimed_job_and_task(session, claim)
            if job is None:
                return False
            self._release_account_lease(session, claim)
            previous_status = job.status
            if task is not None and task.cancel_requested:
                job.status = JobStatus.CANCELED.value
                job.stage = JobStage.CANCELED.value
                job.completed_at = utc_now()
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="job_canceled",
                    from_status=previous_status,
                    to_status=job.status,
                )
            else:
                # Preserve checkpoint stage so a temporary capacity release does
                # not force a full re-generation.
                job.status = JobStatus.QUEUED.value
                if job.stage in {
                    JobStage.QUEUED.value,
                    JobStage.LEASED.value,
                    JobStage.RETRY_WAIT.value,
                }:
                    job.stage = JobStage.QUEUED.value
                elif job.stage == JobStage.SAVING.value:
                    job.stage = JobStage.SAVING.value
                elif job.image_urls:
                    job.stage = JobStage.DOWNLOADING.value
                elif job.conversation_id or job.file_ids or job.sediment_ids:
                    job.stage = JobStage.RESOLVING.value
                elif job.stage in {
                    JobStage.GENERATING.value,
                    JobStage.TRANSFORMING.value,
                }:
                    # Keep progress stage when already mid-pipeline.
                    pass
                else:
                    job.stage = JobStage.QUEUED.value
                job.available_at = utc_now()
            job.lease_owner = None
            job.lease_token = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            if task is not None:
                self._aggregate_task(session, task)
            return True

    def reclaim_expired_leases(
        self,
        now: datetime | None = None,
        *,
        protected_claims: Iterable[ClaimedJob] = (),
    ) -> int:
        reclaim_time = _now(now)
        reclaim_time = _as_utc(reclaim_time) or datetime.now(timezone.utc)
        protected_lease_keys = {
            (claim.job.id, claim.lease_token, int(claim.lease_version))
            for claim in protected_claims
        }
        with self.database.session() as session:
            jobs = session.execute(
                select(ImageJob)
                .where(ImageJob.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]))
                .where(ImageJob.lease_expires_at.is_not(None))
                .where(ImageJob.lease_expires_at <= reclaim_time)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            jobs = [
                job
                for job in jobs
                if (job.id, job.lease_token, int(job.lease_version or 0))
                not in protected_lease_keys
            ]
            owner_ids = {
                str(job.lease_owner or "").strip()
                for job in jobs
                if str(job.lease_owner or "").strip()
            }
            active_owner_ids: set[str] = set()
            if owner_ids:
                worker_fresh_cutoff = reclaim_time - timedelta(
                    seconds=max(60.0, float(self.lease_seconds) * 2.0)
                )
                rows = session.execute(
                    select(ImageWorkerState.worker_id, ImageWorkerState.heartbeat_at).where(
                        ImageWorkerState.worker_id.in_(sorted(owner_ids))
                    )
                ).all()
                for worker_id, heartbeat_at in rows:
                    heartbeat_at = _as_utc(heartbeat_at)
                    if heartbeat_at is not None and heartbeat_at > worker_fresh_cutoff:
                        active_owner_ids.add(str(worker_id or "").strip())

            claim_runtime_cutoff = reclaim_time - timedelta(
                seconds=max(float(self.claim_max_runtime_seconds), float(self.lease_seconds))
            )

            def claim_runtime_expired(job: ImageJob) -> bool:
                heartbeat_at = _as_utc(job.heartbeat_at or job.started_at or job.updated_at)
                if heartbeat_at is None:
                    return True
                return heartbeat_at <= claim_runtime_cutoff

            jobs = [
                job
                for job in jobs
                if (
                    str(job.lease_owner or "").strip() not in active_owner_ids
                    or claim_runtime_expired(job)
                )
            ]
            task_ids = {job.task_id for job in jobs}
            tasks = {
                task_id: self._task_for_update(session, task_id)
                for task_id in sorted(task_ids, key=str)
            }
            for job in jobs:
                session.execute(delete(ImageAccountLease).where(ImageAccountLease.job_id == job.id))
                previous_status = job.status
                previous_stage = job.stage
                task = tasks.get(job.task_id)
                canceled = bool(task is not None and task.cancel_requested)
                if canceled:
                    job.status = JobStatus.CANCELED.value
                    job.stage = JobStage.CANCELED.value
                    job.completed_at = reclaim_time
                else:
                    job.status = JobStatus.QUEUED.value
                    if previous_stage == JobStage.SAVING.value:
                        job.stage = JobStage.SAVING.value
                    elif job.image_urls:
                        job.stage = JobStage.DOWNLOADING.value
                    elif job.conversation_id or job.file_ids or job.sediment_ids:
                        job.stage = JobStage.RESOLVING.value
                    else:
                        job.stage = JobStage.GENERATING.value
                    job.available_at = reclaim_time
                job.lease_version = int(job.lease_version or 0) + 1
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                job.updated_at = reclaim_time
                self._event(
                    session,
                    task_id=job.task_id,
                    job_id=job.id,
                    event_type="job_canceled_after_lease_expiry" if canceled else "lease_expired",
                    from_status=previous_status,
                    to_status=job.status,
                    data={"lease_version": int(job.lease_version)},
                )
            for task_id in task_ids:
                task = tasks.get(task_id)
                if task is not None:
                    self._aggregate_task(session, task)
            return len(jobs)

    def request_cancel(self, owner_key: str, task_or_client_id: object) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = self._find_task(session, owner_key, task_or_client_id)
            if task is None:
                return None
            jobs = session.execute(
                select(ImageJob)
                .where(ImageJob.task_id == task.id)
                .order_by(ImageJob.id)
                .with_for_update()
            ).scalars().all()
            task = session.execute(
                select(ImageTask)
                .where(ImageTask.id == task.id, ImageTask.owner_key == owner_key)
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                return None
            if TaskStatus(task.status) in TERMINAL_TASK_STATUSES:
                return self._task_snapshot(session, task)
            if not task.cancel_requested:
                now = utc_now()
                task.cancel_requested = True
                defer_terminal_cancel = any(
                    job.status not in TERMINAL_JOB_STATUSES
                    and (bool(job.quota_consumed) or job.stage in {JobStage.TRANSFORMING.value, JobStage.SAVING.value})
                    for job in jobs
                )
                for job in jobs:
                    if job.status not in {JobStatus.QUEUED.value, JobStatus.RETRY_WAIT.value}:
                        continue
                    previous_status = job.status
                    job.status = JobStatus.CANCELED.value
                    job.stage = JobStage.CANCELED.value
                    job.completed_at = now
                    job.updated_at = now
                    self._event(
                        session,
                        task_id=task.id,
                        job_id=job.id,
                        event_type="job_canceled",
                        from_status=previous_status,
                        to_status=job.status,
                    )
                if defer_terminal_cancel:
                    self._aggregate_task(session, task)
                else:
                    task.status = TaskStatus.CANCELED.value
                    task.completed_at = now
                    task.updated_at = now
                    task.version += 1
                self._event(
                    session,
                    task_id=task.id,
                    event_type="task_canceled",
                    to_status=task.status,
                )
            return self._task_snapshot(session, task)

    def is_cancel_requested(self, task_id: UUID) -> bool:
        with self.database.session() as session:
            value = session.execute(
                select(ImageTask.cancel_requested).where(ImageTask.id == task_id)
            ).scalar_one_or_none()
            return bool(value)

    def mark_response_attempted(self, owner_key: str, task_or_client_id: object) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = self._find_task(session, owner_key, task_or_client_id, lock=True)
            if task is None:
                return None
            if task.delivery_status != DeliveryStatus.ACKNOWLEDGED.value:
                task.delivery_status = DeliveryStatus.RESPONSE_ATTEMPTED.value
                task.response_attempted_at = task.response_attempted_at or utc_now()
            return self._task_snapshot(session, task)

    def mark_responses_attempted(self, owner_key: str, task_ids: Sequence[UUID]) -> None:
        ids = [value for value in task_ids if isinstance(value, UUID)]
        if not ids:
            return
        with self.database.session() as session:
            session.execute(
                update(ImageTask)
                .where(
                    ImageTask.owner_key == owner_key,
                    ImageTask.id.in_(ids),
                    ImageTask.delivery_status != DeliveryStatus.ACKNOWLEDGED.value,
                )
                .values(
                    delivery_status=DeliveryStatus.RESPONSE_ATTEMPTED.value,
                    response_attempted_at=func.coalesce(ImageTask.response_attempted_at, utc_now()),
                )
                .execution_options(synchronize_session=False)
            )

    def acknowledge(self, owner_key: str, task_or_client_id: object) -> TaskSnapshot | None:
        with self.database.session() as session:
            task = self._find_task(session, owner_key, task_or_client_id, lock=True)
            if task is None:
                return None
            terminal_with_results = bool(
                task.status in {TaskStatus.FAILED.value, TaskStatus.CANCELED.value}
                and int(task.succeeded_jobs or 0) > 0
            )
            if task.status != TaskStatus.SUCCESS.value and not terminal_with_results:
                raise TaskStateConflict("only successful or partial completed image results can be acknowledged")
            if task.delivery_status == DeliveryStatus.ACKNOWLEDGED.value:
                return self._task_snapshot(session, task)
            task.delivery_status = DeliveryStatus.ACKNOWLEDGED.value
            task.delivery_acked_at = utc_now()
            self._event(session, task_id=task.id, event_type="delivery_acknowledged")
            return self._task_snapshot(session, task)

    def list_artifacts(self, task_id: UUID) -> list[ArtifactDescriptor]:
        with self.database.session() as session:
            artifacts = session.execute(
                select(ImageTaskArtifact)
                .where(ImageTaskArtifact.task_id == task_id)
                .order_by(ImageTaskArtifact.created_at, ImageTaskArtifact.relative_path)
            ).scalars().all()
            return [self._artifact_descriptor(item) for item in artifacts]

    def list_public_final_artifacts(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.execute(
                select(ImageTaskArtifact, ImageJob)
                .join(ImageJob, ImageJob.id == ImageTaskArtifact.job_id)
                .join(ImageTask, ImageTask.id == ImageTaskArtifact.task_id)
                .where(
                    ImageTaskArtifact.kind == "final",
                    ImageTaskArtifact.status == ArtifactStatus.READY.value,
                    ImageJob.status == JobStatus.SUCCESS.value,
                    ImageTask.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
                )
                .order_by(ImageTaskArtifact.created_at.desc(), ImageTaskArtifact.relative_path.desc())
            ).all()
            items: list[dict[str, Any]] = []
            for artifact, job in rows:
                payload = dict(job.result_payload or {})
                created_at = artifact.created_at
                created_text = created_at.isoformat() if created_at is not None else ""
                items.append({
                    "rel": artifact.relative_path,
                    "path": artifact.relative_path,
                    "name": artifact.relative_path.rsplit("/", 1)[-1],
                    "date": created_text[:10],
                    "size": int(artifact.byte_size or 0),
                    "created_at": created_text,
                    "storage": str(artifact.storage_backend or "local"),
                    "local": str(artifact.storage_backend or "") in {"local", "both"},
                    "webdav": str(artifact.storage_backend or "") in {"webdav", "both"},
                    "remote_url": str(payload.get("url") or ""),
                    "url": str(payload.get("url") or ""),
                    "width": int(artifact.width or 0),
                    "height": int(artifact.height or 0),
                })
            return items

    def is_public_final_artifact(self, relative_path: object) -> bool:
        rel = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            return False
        with self.database.session() as session:
            return session.execute(
                select(ImageTaskArtifact.id)
                .join(ImageJob, ImageJob.id == ImageTaskArtifact.job_id)
                .join(ImageTask, ImageTask.id == ImageTaskArtifact.task_id)
                .where(
                    ImageTaskArtifact.relative_path == rel,
                    ImageTaskArtifact.kind == "final",
                    ImageTaskArtifact.status == ArtifactStatus.READY.value,
                    ImageJob.status == JobStatus.SUCCESS.value,
                    ImageTask.status.in_([status.value for status in TERMINAL_TASK_STATUSES]),
                )
                .limit(1)
            ).scalar_one_or_none() is not None

    @staticmethod
    def _aggregate_task(session: Session, task: ImageTask) -> None:
        jobs = session.execute(
            select(ImageJob)
            .where(ImageJob.task_id == task.id)
            .order_by(ImageJob.ordinal)
        ).scalars().all()
        counts = Counter(job.status for job in jobs)
        task.succeeded_jobs = counts[JobStatus.SUCCESS.value]
        task.failed_jobs = counts[JobStatus.FAILED.value]
        now = utc_now()
        all_terminal = bool(jobs) and all(JobStatus(job.status) in TERMINAL_JOB_STATUSES for job in jobs)
        has_failed = counts[JobStatus.FAILED.value] > 0
        failed = next((job for job in jobs if job.status == JobStatus.FAILED.value), None)

        def apply_active_status(wait_reason: str | None = None) -> None:
            if any(job.stage in {JobStage.TRANSFORMING.value, JobStage.SAVING.value} for job in jobs):
                task.status = TaskStatus.SAVING.value
                task.wait_reason = wait_reason or None
                return
            if counts[JobStatus.RETRY_WAIT.value]:
                task.status = TaskStatus.RETRYING.value
                task.wait_reason = wait_reason or "scheduled_retry"
                return
            if counts[JobStatus.LEASED.value] or counts[JobStatus.RUNNING.value]:
                task.status = TaskStatus.RUNNING.value
                task.wait_reason = wait_reason or None
                task.started_at = task.started_at or now
                return
            task.status = TaskStatus.QUEUED.value
            task.wait_reason = wait_reason or task.wait_reason or "queued"

        if jobs and task.succeeded_jobs == task.required_jobs:
            task.status = TaskStatus.SUCCESS.value
            task.wait_reason = None
            task.error_code = None
            task.error_message = None
            task.completed_at = now
        elif task.cancel_requested and all_terminal:
            task.status = TaskStatus.CANCELED.value
            task.completed_at = task.completed_at or now
        elif has_failed and all_terminal:
            task.status = TaskStatus.FAILED.value
            task.error_code = str((failed.error_code if failed is not None else None) or "image_job_failed")
            task.error_message = str((failed.error_message if failed is not None else None) or "image job failed")
            task.completed_at = now
        elif all_terminal:
            task.status = TaskStatus.FAILED.value
            task.error_code = task.error_code or "image_job_failed"
            task.error_message = task.error_message or "image job failed"
            task.completed_at = now
        elif task.cancel_requested:
            task.completed_at = None
            apply_active_status("canceling")
        elif has_failed:
            task.error_code = str((failed.error_code if failed is not None else None) or "image_job_failed")
            task.error_message = str((failed.error_message if failed is not None else None) or "image job failed")
            task.completed_at = None
            apply_active_status("sibling_jobs_running")
        else:
            apply_active_status()
        task.updated_at = now
        task.version += 1

    def queue_snapshot(self) -> dict[str, Any]:
        with self.database.session() as session:
            now = utc_now()
            task_counts = dict(session.execute(
                select(ImageTask.status, func.count(ImageTask.id)).group_by(ImageTask.status)
            ).all())
            job_counts = dict(session.execute(
                select(ImageJob.status, func.count(ImageJob.id)).group_by(ImageJob.status)
            ).all())
            stage_counts = dict(session.execute(
                select(ImageJob.stage, func.count(ImageJob.id)).group_by(ImageJob.stage)
            ).all())
            oldest = session.execute(
                select(func.min(ImageTask.created_at)).where(ImageTask.status == TaskStatus.QUEUED.value)
            ).scalar_one_or_none()
            timing_rows = session.execute(
                select(
                    ImageTask.status,
                    ImageTask.queued_at,
                    ImageTask.started_at,
                    ImageTask.completed_at,
                )
                .order_by(ImageTask.created_at.desc())
                .limit(1000)
            ).all()
            queue_wait_samples = []
            duration_samples = []
            for status, queued_at, started_at, completed_at in timing_rows:
                queue_end = started_at or (now if status in {
                    TaskStatus.QUEUED.value,
                    TaskStatus.RETRYING.value,
                } else None)
                if queued_at is not None and queue_end is not None:
                    queue_wait_samples.append(self._elapsed_seconds(queued_at, queue_end))
                duration_end = completed_at or (now if started_at is not None else None)
                if started_at is not None and duration_end is not None:
                    duration_samples.append(self._elapsed_seconds(started_at, duration_end))
            workers = session.execute(
                select(ImageWorkerState)
                .where(ImageWorkerState.heartbeat_at >= now - timedelta(seconds=max(60.0, self.lease_seconds * 2.0)))
                .order_by(ImageWorkerState.heartbeat_at.desc())
            ).scalars().all()
            flattened = {status.value: int(task_counts.get(status.value, 0)) for status in TaskStatus}
            flattened.update({
                "tasks": {str(key): int(value) for key, value in task_counts.items()},
                "jobs": {str(key): int(value) for key, value in job_counts.items()},
                "job_stages": {str(key): int(value) for key, value in stage_counts.items()},
                "oldest_queued_at": oldest.isoformat() if oldest else None,
                "queue_wait_p90_seconds": self._percentile_seconds(queue_wait_samples, 0.90),
                "duration_p90_seconds": self._percentile_seconds(duration_samples, 0.90),
                "active_leases": int(session.execute(
                    select(func.count()).select_from(ImageAccountLease).where(ImageAccountLease.expires_at > now)
                ).scalar_one()),
                "unacknowledged_success": int(session.execute(
                    select(func.count(ImageTask.id)).where(
                        ImageTask.status == TaskStatus.SUCCESS.value,
                        ImageTask.delivery_status != DeliveryStatus.ACKNOWLEDGED.value,
                    )
                ).scalar_one()),
                "workers": [{
                    "worker_id": item.worker_id,
                    "heartbeat_at": item.heartbeat_at.isoformat(),
                    "heartbeat_age_seconds": self._elapsed_seconds(item.heartbeat_at, now),
                    "effective_concurrency": int(item.effective_concurrency),
                    "pause_reason": item.pause_reason,
                    "resource_snapshot": dict(item.resource_snapshot or {}),
                } for item in workers],
            })
            return flattened

    def _ensure_worker_process_current(
        self,
        session: Session,
        worker_id: str,
        *,
        expected_process_instance_id: str = "",
    ) -> None:
        expected = str(expected_process_instance_id or "").strip()
        if not expected:
            return
        state = session.execute(
            select(ImageWorkerState)
            .where(ImageWorkerState.worker_id == worker_id)
            .with_for_update()
        ).scalar_one_or_none()
        if state is None:
            raise RuntimeError(f"worker identity conflict for {worker_id}: worker state is not registered")
        actual = _snapshot_text(dict(state.resource_snapshot or {}), "process_instance_id")
        if actual != expected:
            raise RuntimeError(
                f"worker identity conflict for {worker_id}: "
                f"active process {actual or 'unknown'}"
            )

    def current_worker_pause_reason(self) -> str:
        with self.database.session() as session:
            now = utc_now()
            pause_reason = session.execute(
                select(ImageWorkerState.pause_reason)
                .where(
                    ImageWorkerState.heartbeat_at
                    >= now - timedelta(seconds=max(60.0, self.lease_seconds * 2.0))
                )
                .order_by(ImageWorkerState.heartbeat_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            return str(pause_reason or "")

    def purge_terminal_tasks(
        self,
        *,
        worker_id: str = "",
        include_unowned: bool = True,
        retention_seconds: int,
        now: datetime | None = None,
    ) -> PurgedTerminalTasks:
        current_time = _now(now)
        retention_cutoff = current_time - timedelta(seconds=max(1, int(retention_seconds)))
        delivery_cutoff = self._delivery_cutoff(current_time)
        delivery_finished = or_(
            ImageTask.delivery_status == DeliveryStatus.ACKNOWLEDGED.value,
            and_(
                ImageTask.delivery_status == DeliveryStatus.RESPONSE_ATTEMPTED.value,
                ImageTask.response_attempted_at.is_not(None),
                ImageTask.response_attempted_at <= delivery_cutoff,
            ),
        )
        success_retention_finished = or_(
            ImageTask.delivery_status == DeliveryStatus.ACKNOWLEDGED.value,
            and_(
                ImageTask.delivery_status == DeliveryStatus.RESPONSE_ATTEMPTED.value,
                ImageTask.response_attempted_at.is_not(None),
                ImageTask.response_attempted_at <= delivery_cutoff,
                ImageTask.response_attempted_at <= retention_cutoff,
            ),
        )
        with self.database.session() as session:
            # Keep the purge predicate aligned with protected_artifact_paths():
            # successful artifacts remain until both delivery and retention
            # protection windows have elapsed.
            task_ids = tuple(session.execute(
                select(ImageTask.id).where(
                    ImageTask.completed_at.is_not(None),
                    ImageTask.completed_at <= retention_cutoff,
                    or_(
                        and_(
                            ImageTask.status == TaskStatus.SUCCESS.value,
                            success_retention_finished,
                        ),
                        and_(
                            ImageTask.status.in_([
                                TaskStatus.FAILED.value,
                                TaskStatus.CANCELED.value,
                            ]),
                            or_(
                                ImageTask.succeeded_jobs <= 0,
                                delivery_finished,
                            ),
                        ),
                    ),
                )
            ).scalars().all())
            if not task_ids:
                return PurgedTerminalTasks(removed=0)
            cleanup_worker_id = str(worker_id or "").strip()
            artifact_statement = select(ImageTaskArtifact).where(ImageTaskArtifact.task_id.in_(task_ids))
            if cleanup_worker_id:
                worker_filters = [ImageTaskArtifact.worker_id == cleanup_worker_id]
                if include_unowned:
                    worker_filters.extend((
                        ImageTaskArtifact.worker_id.is_(None),
                        ImageTaskArtifact.worker_id == "",
                    ))
                artifact_statement = artifact_statement.where(
                    or_(*worker_filters)
                )
            artifacts = tuple(
                self._artifact_descriptor(item)
                for item in session.execute(artifact_statement).scalars().all()
            )
            if not cleanup_worker_id:
                result = session.execute(delete(ImageTask).where(ImageTask.id.in_(task_ids)))
                return PurgedTerminalTasks(
                    removed=int(result.rowcount or 0),
                    artifacts=artifacts,
                    task_ids=task_ids,
                )
            return PurgedTerminalTasks(
                removed=0,
                artifacts=artifacts,
                task_ids=task_ids,
            )

    def finalize_terminal_tasks(
        self,
        task_ids: Sequence[UUID],
        *,
        worker_id: str = "",
        include_unowned: bool = True,
    ) -> int:
        normalized_task_ids = tuple(
            item for item in task_ids if str(item or "").strip()
        )
        if not normalized_task_ids:
            return 0
        cleanup_worker_id = str(worker_id or "").strip()
        with self.database.session() as session:
            artifact_statement = delete(ImageTaskArtifact).where(ImageTaskArtifact.task_id.in_(normalized_task_ids))
            if cleanup_worker_id:
                worker_filters = [ImageTaskArtifact.worker_id == cleanup_worker_id]
                if include_unowned:
                    worker_filters.extend((
                        ImageTaskArtifact.worker_id.is_(None),
                        ImageTaskArtifact.worker_id == "",
                    ))
                artifact_statement = artifact_statement.where(
                    or_(*worker_filters)
                )
            session.execute(artifact_statement)
            remaining_task_ids = {
                item
                for item in session.execute(
                    select(ImageTaskArtifact.task_id).where(ImageTaskArtifact.task_id.in_(normalized_task_ids))
                ).scalars().all()
            }
            finalizable_task_ids = [
                task_id for task_id in normalized_task_ids if task_id not in remaining_task_ids
            ]
            if not finalizable_task_ids:
                return 0
            result = session.execute(delete(ImageTask).where(ImageTask.id.in_(finalizable_task_ids)))
            return int(result.rowcount or 0)

    @staticmethod
    def _elapsed_seconds(start: datetime, end: datetime) -> int:
        if (start.tzinfo is None) != (end.tzinfo is None):
            end = end.replace(tzinfo=start.tzinfo)
        return max(0, int((end - start).total_seconds()))

    @staticmethod
    def _percentile_seconds(values: Sequence[int], percentile: float) -> int:
        if not values:
            return 0
        ordered = sorted(max(0, int(value)) for value in values)
        index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
        return ordered[index]

    @staticmethod
    def _export_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _delivery_cutoff(self, now: datetime) -> datetime:
        return now - timedelta(seconds=max(1, int(self.delivery_grace_seconds)))

    def _terminal_retention_cutoff(self, now: datetime) -> datetime:
        return now - timedelta(seconds=max(1, int(self.terminal_retention_seconds)))

    @staticmethod
    def _sql_delivery_still_protected(delivery_cutoff: datetime):
        return or_(
            ImageTask.delivery_status == DeliveryStatus.PENDING.value,
            and_(
                ImageTask.delivery_status == DeliveryStatus.RESPONSE_ATTEMPTED.value,
                or_(
                    ImageTask.response_attempted_at.is_(None),
                    ImageTask.response_attempted_at > delivery_cutoff,
                ),
            ),
        )

    @staticmethod
    def _sql_success_artifact_still_protected(delivery_cutoff: datetime, retention_cutoff: datetime):
        return or_(
            ImageTask.delivery_status == DeliveryStatus.PENDING.value,
            and_(
                ImageTask.delivery_status == DeliveryStatus.RESPONSE_ATTEMPTED.value,
                or_(
                    ImageTask.response_attempted_at.is_(None),
                    ImageTask.response_attempted_at > delivery_cutoff,
                    ImageTask.response_attempted_at > retention_cutoff,
                ),
            ),
        )

    @staticmethod
    def _task_delivery_still_protected(task: ImageTask, delivery_cutoff: datetime) -> bool:
        if task.delivery_status == DeliveryStatus.PENDING.value:
            return True
        if task.delivery_status != DeliveryStatus.RESPONSE_ATTEMPTED.value:
            return False
        attempted_at = task.response_attempted_at
        if attempted_at is None:
            return True
        if attempted_at.tzinfo is None and delivery_cutoff.tzinfo is not None:
            attempted_at = attempted_at.replace(tzinfo=delivery_cutoff.tzinfo)
        elif attempted_at.tzinfo is not None and delivery_cutoff.tzinfo is None:
            attempted_at = attempted_at.replace(tzinfo=None)
        return attempted_at > delivery_cutoff

    @staticmethod
    def _task_success_artifact_still_protected(
        task: ImageTask,
        delivery_cutoff: datetime,
        retention_cutoff: datetime,
    ) -> bool:
        if task.delivery_status == DeliveryStatus.PENDING.value:
            return True
        if task.delivery_status != DeliveryStatus.RESPONSE_ATTEMPTED.value:
            return False
        attempted_at = task.response_attempted_at
        if attempted_at is None:
            return True
        if attempted_at.tzinfo is None and delivery_cutoff.tzinfo is not None:
            attempted_at = attempted_at.replace(tzinfo=delivery_cutoff.tzinfo)
        elif attempted_at.tzinfo is not None and delivery_cutoff.tzinfo is None:
            attempted_at = attempted_at.replace(tzinfo=None)
        return attempted_at > delivery_cutoff or attempted_at > retention_cutoff

    def protected_artifact_paths(self) -> set[str]:
        current_time = utc_now()
        delivery_cutoff = self._delivery_cutoff(current_time)
        delivery_protected = self._sql_delivery_still_protected(delivery_cutoff)
        success_artifact_protected = self._sql_success_artifact_still_protected(
            delivery_cutoff,
            self._terminal_retention_cutoff(current_time),
        )
        active_statuses = {
            TaskStatus.QUEUED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.SAVING.value,
            TaskStatus.RETRYING.value,
        }
        with self.database.session() as session:
            paths = session.execute(
                select(ImageTaskArtifact.relative_path)
                .join(ImageTask, ImageTask.id == ImageTaskArtifact.task_id)
                .outerjoin(ImageJob, ImageJob.id == ImageTaskArtifact.job_id)
                .where(or_(
                    ImageTask.status.in_(active_statuses),
                    and_(
                        ImageTask.status == TaskStatus.SUCCESS.value,
                        success_artifact_protected,
                    ),
                    and_(
                        ImageTask.status.in_([TaskStatus.FAILED.value, TaskStatus.CANCELED.value]),
                        ImageJob.status == JobStatus.SUCCESS.value,
                        delivery_protected,
                    ),
                ))
            ).scalars().all()
            return {str(path) for path in paths if str(path or "").strip()}

    def write_logical_backup(
        self,
        output: TextIO,
        *,
        artifact_transform: Callable[[dict[str, object]], dict[str, object]] | None = None,
    ) -> None:
        with self.database.session() as session:
            if self.database.engine is not None and self.database.engine.dialect.name == "postgresql":
                session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                ))
            exported_at = utc_now()
            active_statuses = {
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.SAVING.value,
                TaskStatus.RETRYING.value,
            }
            delivery_protected = self._sql_delivery_still_protected(self._delivery_cutoff(exported_at))
            success_artifact_protected = self._sql_success_artifact_still_protected(
                self._delivery_cutoff(exported_at),
                self._terminal_retention_cutoff(exported_at),
            )
            protected_paths = {
                str(path)
                for path in session.execute(
                    select(ImageTaskArtifact.relative_path)
                    .join(ImageTask, ImageTask.id == ImageTaskArtifact.task_id)
                    .outerjoin(ImageJob, ImageJob.id == ImageTaskArtifact.job_id)
                    .where(or_(
                        ImageTask.status.in_(active_statuses),
                        and_(
                            ImageTask.status == TaskStatus.SUCCESS.value,
                            success_artifact_protected,
                        ),
                        and_(
                            ImageTask.status.in_([TaskStatus.FAILED.value, TaskStatus.CANCELED.value]),
                            ImageJob.status == JobStatus.SUCCESS.value,
                            delivery_protected,
                        ),
                    ))
                ).scalars()
                if str(path or "").strip()
            }

            def task_item(item: ImageTask) -> dict[str, object]:
                return {
                    "id": str(item.id),
                    "owner_key": item.owner_key,
                    "client_task_id": item.client_task_id,
                    "idempotency_key": item.idempotency_key,
                    "request_hash": item.request_hash,
                    "task_type": item.task_type,
                    "public_model": item.public_model,
                    "original_prompt": item.original_prompt,
                    "effective_prompt": item.effective_prompt,
                    "prompt_suffix_version": item.prompt_suffix_version,
                    "request_payload": dict(item.request_payload or {}),
                    "required_jobs": int(item.required_jobs),
                    "succeeded_jobs": int(item.succeeded_jobs),
                    "failed_jobs": int(item.failed_jobs),
                    "status": item.status,
                    "wait_reason": item.wait_reason,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "delivery_status": item.delivery_status,
                    "response_attempted_at": self._export_datetime(item.response_attempted_at),
                    "delivery_acked_at": self._export_datetime(item.delivery_acked_at),
                    "cancel_requested": bool(item.cancel_requested),
                    "created_at": self._export_datetime(item.created_at),
                    "queued_at": self._export_datetime(item.queued_at),
                    "started_at": self._export_datetime(item.started_at),
                    "updated_at": self._export_datetime(item.updated_at),
                    "completed_at": self._export_datetime(item.completed_at),
                    "version": int(item.version),
                }

            def job_item(item: ImageJob) -> dict[str, object]:
                return {
                    "id": str(item.id),
                    "task_id": str(item.task_id),
                    "ordinal": int(item.ordinal),
                    "status": item.status,
                    "stage": item.stage,
                    "stage_timings": dict(item.stage_timings or {}),
                    "generate_attempts": int(item.generate_attempts),
                    "download_attempts": int(item.download_attempts),
                    "save_attempts": int(item.save_attempts),
                    "account_id": str(item.account_id) if item.account_id else None,
                    "conversation_id": item.conversation_id,
                    "image_urls": list(item.image_urls or []),
                    "file_ids": list(item.file_ids or []),
                    "sediment_ids": list(item.sediment_ids or []),
                    "quota_consumed": bool(item.quota_consumed),
                    "quota_accounted": bool(item.quota_accounted),
                    "result_payload": dict(item.result_payload or {}),
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "created_at": self._export_datetime(item.created_at),
                    "available_at": self._export_datetime(item.available_at),
                    "next_retry_at": self._export_datetime(item.next_retry_at),
                    "lease_version": int(item.lease_version),
                    "started_at": self._export_datetime(item.started_at),
                    "completed_at": self._export_datetime(item.completed_at),
                    "updated_at": self._export_datetime(item.updated_at),
                }

            def event_item(item: ImageTaskEvent) -> dict[str, object]:
                return {
                    "id": int(item.id),
                    "task_id": str(item.task_id),
                    "job_id": str(item.job_id) if item.job_id else None,
                    "attempt": int(item.attempt),
                    "event_type": item.event_type,
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "event_data": dict(item.event_data or {}),
                    "created_at": self._export_datetime(item.created_at),
                }

            def artifact_item(item: ImageTaskArtifact) -> dict[str, object]:
                exported: dict[str, object] = {
                    "id": str(item.id),
                    "task_id": str(item.task_id),
                    "job_id": str(item.job_id) if item.job_id else None,
                    "kind": item.kind,
                    "ordinal": item.ordinal,
                    "status": item.status,
                    "storage_backend": item.storage_backend,
                    "worker_id": item.worker_id,
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "mime_type": item.mime_type,
                    "byte_size": int(item.byte_size),
                    "width": int(item.width),
                    "height": int(item.height),
                    "source_url": item.source_url,
                    "backup_required": str(item.relative_path or "") in protected_paths,
                    "created_at": self._export_datetime(item.created_at),
                    "ready_at": self._export_datetime(item.ready_at),
                }
                if item.payload_blob is not None:
                    exported["payload_blob_b64"] = base64.b64encode(
                        bytes(item.payload_blob)
                    ).decode("ascii")
                return artifact_transform(exported) if artifact_transform is not None else exported

            def lease_item(item: ImageAccountLease) -> dict[str, object]:
                return {
                    "account_id": str(item.account_id),
                    "slot_no": int(item.slot_no),
                    "job_id": str(item.job_id),
                    "lease_owner": item.lease_owner,
                    "lease_version": int(item.lease_version),
                    "expires_at": self._export_datetime(item.expires_at),
                }

            def worker_item(item: ImageWorkerState) -> dict[str, object]:
                return {
                    "worker_id": item.worker_id,
                    "heartbeat_at": self._export_datetime(item.heartbeat_at),
                    "resource_snapshot": dict(item.resource_snapshot or {}),
                    "effective_concurrency": int(item.effective_concurrency),
                    "pause_reason": item.pause_reason,
                }

            def legacy_item(item: ImageLegacyImport) -> dict[str, object]:
                return {
                    "file_sha256": item.file_sha256,
                    "source_path": item.source_path,
                    "summary": dict(item.summary or {}),
                    "imported_at": self._export_datetime(item.imported_at),
                }

            collections = (
                ("tasks", select(ImageTask).order_by(ImageTask.created_at), task_item),
                ("jobs", select(ImageJob).order_by(ImageJob.created_at, ImageJob.ordinal), job_item),
                ("events", select(ImageTaskEvent).order_by(ImageTaskEvent.id), event_item),
                ("artifacts", select(ImageTaskArtifact).order_by(ImageTaskArtifact.created_at), artifact_item),
                ("account_leases", select(ImageAccountLease).order_by(ImageAccountLease.account_id, ImageAccountLease.slot_no), lease_item),
                ("workers", select(ImageWorkerState).order_by(ImageWorkerState.worker_id), worker_item),
                ("legacy_imports", select(ImageLegacyImport).order_by(ImageLegacyImport.imported_at), legacy_item),
            )
            output.write('{"version":2,"exported_at":')
            json.dump(exported_at.isoformat(), output, ensure_ascii=False)
            for name, statement, serializer in collections:
                output.write(f',"{name}":[')
                first = True
                rows = session.execute(
                    statement.execution_options(yield_per=500)
                ).scalars()
                for row in rows:
                    if not first:
                        output.write(",")
                    json.dump(serializer(row), output, ensure_ascii=False, separators=(",", ":"))
                    first = False
                output.write("]")
            output.write("}")

    def logical_backup(self) -> dict[str, Any]:
        with self.database.session() as session:
            if self.database.engine is not None and self.database.engine.dialect.name == "postgresql":
                session.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                ))
            tasks = session.execute(select(ImageTask).order_by(ImageTask.created_at)).scalars().all()
            jobs = session.execute(select(ImageJob).order_by(ImageJob.created_at, ImageJob.ordinal)).scalars().all()
            events = session.execute(select(ImageTaskEvent).order_by(ImageTaskEvent.id)).scalars().all()
            artifacts = session.execute(select(ImageTaskArtifact).order_by(ImageTaskArtifact.created_at)).scalars().all()
            leases = session.execute(select(ImageAccountLease).order_by(ImageAccountLease.account_id, ImageAccountLease.slot_no)).scalars().all()
            workers = session.execute(select(ImageWorkerState).order_by(ImageWorkerState.worker_id)).scalars().all()
            legacy_imports = session.execute(
                select(ImageLegacyImport).order_by(ImageLegacyImport.imported_at)
            ).scalars().all()
            exported_at = utc_now()
            tasks_by_id = {item.id: item for item in tasks}
            jobs_by_id = {item.id: item for item in jobs}
            delivery_cutoff = self._delivery_cutoff(exported_at)

            def backup_required(artifact: ImageTaskArtifact) -> bool:
                task = tasks_by_id.get(artifact.task_id)
                if task is None:
                    return False
                if task.status not in {item.value for item in TERMINAL_TASK_STATUSES}:
                    return True
                if not self._task_delivery_still_protected(task, delivery_cutoff):
                    if task.status == TaskStatus.SUCCESS.value:
                        return self._task_success_artifact_still_protected(
                            task,
                            delivery_cutoff,
                            self._terminal_retention_cutoff(exported_at),
                        )
                    return False
                if task.status == TaskStatus.SUCCESS.value:
                    return self._task_success_artifact_still_protected(
                        task,
                        delivery_cutoff,
                        self._terminal_retention_cutoff(exported_at),
                    )
                job = jobs_by_id.get(artifact.job_id)
                return bool(job is not None and job.status == JobStatus.SUCCESS.value)

            return {
                "version": 2,
                "exported_at": exported_at.isoformat(),
                "tasks": [{
                    "id": str(item.id),
                    "owner_key": item.owner_key,
                    "client_task_id": item.client_task_id,
                    "idempotency_key": item.idempotency_key,
                    "request_hash": item.request_hash,
                    "task_type": item.task_type,
                    "public_model": item.public_model,
                    "original_prompt": item.original_prompt,
                    "effective_prompt": item.effective_prompt,
                    "prompt_suffix_version": item.prompt_suffix_version,
                    "request_payload": dict(item.request_payload or {}),
                    "required_jobs": int(item.required_jobs),
                    "succeeded_jobs": int(item.succeeded_jobs),
                    "failed_jobs": int(item.failed_jobs),
                    "status": item.status,
                    "wait_reason": item.wait_reason,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "delivery_status": item.delivery_status,
                    "response_attempted_at": self._export_datetime(item.response_attempted_at),
                    "delivery_acked_at": self._export_datetime(item.delivery_acked_at),
                    "cancel_requested": bool(item.cancel_requested),
                    "created_at": self._export_datetime(item.created_at),
                    "queued_at": self._export_datetime(item.queued_at),
                    "started_at": self._export_datetime(item.started_at),
                    "updated_at": self._export_datetime(item.updated_at),
                    "completed_at": self._export_datetime(item.completed_at),
                    "version": int(item.version),
                } for item in tasks],
                "jobs": [{
                    "id": str(item.id),
                    "task_id": str(item.task_id),
                    "ordinal": int(item.ordinal),
                    "status": item.status,
                    "stage": item.stage,
                    "stage_timings": dict(item.stage_timings or {}),
                    "generate_attempts": int(item.generate_attempts),
                    "download_attempts": int(item.download_attempts),
                    "save_attempts": int(item.save_attempts),
                    "account_id": str(item.account_id) if item.account_id else None,
                    "conversation_id": item.conversation_id,
                    "image_urls": list(item.image_urls or []),
                    "file_ids": list(item.file_ids or []),
                    "sediment_ids": list(item.sediment_ids or []),
                    "quota_consumed": bool(item.quota_consumed),
                    "quota_accounted": bool(item.quota_accounted),
                    "result_payload": dict(item.result_payload or {}),
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                    "created_at": self._export_datetime(item.created_at),
                    "available_at": self._export_datetime(item.available_at),
                    "next_retry_at": self._export_datetime(item.next_retry_at),
                    "lease_version": int(item.lease_version),
                    "started_at": self._export_datetime(item.started_at),
                    "completed_at": self._export_datetime(item.completed_at),
                    "updated_at": self._export_datetime(item.updated_at),
                } for item in jobs],
                "events": [{
                    "id": int(item.id),
                    "task_id": str(item.task_id),
                    "job_id": str(item.job_id) if item.job_id else None,
                    "attempt": int(item.attempt),
                    "event_type": item.event_type,
                    "from_status": item.from_status,
                    "to_status": item.to_status,
                    "event_data": dict(item.event_data or {}),
                    "created_at": self._export_datetime(item.created_at),
                } for item in events],
                "artifacts": [{
                    "id": str(item.id),
                    "task_id": str(item.task_id),
                    "job_id": str(item.job_id) if item.job_id else None,
                    "kind": item.kind,
                    "ordinal": item.ordinal,
                    "status": item.status,
                    "storage_backend": item.storage_backend,
                    "worker_id": item.worker_id,
                    "relative_path": item.relative_path,
                    "sha256": item.sha256,
                    "mime_type": item.mime_type,
                    "byte_size": int(item.byte_size),
                    "width": int(item.width),
                    "height": int(item.height),
                    "source_url": item.source_url,
                    "backup_required": backup_required(item),
                    "created_at": self._export_datetime(item.created_at),
                    "ready_at": self._export_datetime(item.ready_at),
                    **(
                        {
                            "payload_blob_b64": base64.b64encode(
                                bytes(item.payload_blob)
                            ).decode("ascii")
                        }
                        if item.payload_blob is not None
                        else {}
                    ),
                } for item in artifacts],
                "account_leases": [{
                    "account_id": str(item.account_id),
                    "slot_no": int(item.slot_no),
                    "job_id": str(item.job_id),
                    "lease_owner": item.lease_owner,
                    "lease_version": int(item.lease_version),
                    "expires_at": self._export_datetime(item.expires_at),
                } for item in leases],
                "workers": [{
                    "worker_id": item.worker_id,
                    "heartbeat_at": self._export_datetime(item.heartbeat_at),
                    "resource_snapshot": dict(item.resource_snapshot or {}),
                    "effective_concurrency": int(item.effective_concurrency),
                    "pause_reason": item.pause_reason,
                } for item in workers],
                "legacy_imports": [{
                    "file_sha256": item.file_sha256,
                    "source_path": item.source_path,
                    "summary": dict(item.summary or {}),
                    "imported_at": self._export_datetime(item.imported_at),
                } for item in legacy_imports],
            }

    def restore_logical_backup(self, payload: dict[str, Any]) -> dict[str, int]:
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != 2:
            raise ValueError("unsupported image queue backup version")
        collections = {
            name: payload.get(name, [])
            for name in ("tasks", "jobs", "events", "artifacts", "legacy_imports")
        }
        if any(not isinstance(items, list) for items in collections.values()):
            raise ValueError("image queue backup collections must be arrays")
        with self.database.session() as session:
            if session.execute(select(func.count()).select_from(ImageTask)).scalar_one():
                raise TaskStateConflict("image queue restore requires an empty database")
            now = utc_now()
            task_ids: list[UUID] = []
            for raw in collections["tasks"]:
                if not isinstance(raw, dict) or (task_id := _uuid(raw.get("id"))) is None:
                    raise ValueError("image queue backup contains an invalid task")
                created_at = _backup_datetime(raw.get("created_at"), now) or now
                status = TaskStatus(str(raw.get("status") or TaskStatus.QUEUED.value))
                delivery = DeliveryStatus(str(raw.get("delivery_status") or DeliveryStatus.PENDING.value))
                session.add(ImageTask(
                    id=task_id,
                    owner_key=str(raw.get("owner_key") or ""),
                    client_task_id=str(raw.get("client_task_id") or "") or None,
                    idempotency_key=str(raw.get("idempotency_key") or "") or None,
                    request_hash=str(raw.get("request_hash") or ""),
                    task_type=str(raw.get("task_type") or "generation"),
                    public_model=str(raw.get("public_model") or "gpt-image-2"),
                    original_prompt=str(raw.get("original_prompt") or ""),
                    effective_prompt=str(raw.get("effective_prompt") or ""),
                    prompt_suffix_version=str(raw.get("prompt_suffix_version") or "") or None,
                    request_payload=dict(raw.get("request_payload") or {}),
                    required_jobs=max(1, int(raw.get("required_jobs") or 1)),
                    succeeded_jobs=max(0, int(raw.get("succeeded_jobs") or 0)),
                    failed_jobs=max(0, int(raw.get("failed_jobs") or 0)),
                    status=status.value,
                    wait_reason=str(raw.get("wait_reason") or "") or None,
                    error_code=str(raw.get("error_code") or "") or None,
                    error_message=str(raw.get("error_message") or "") or None,
                    delivery_status=delivery.value,
                    response_attempted_at=_backup_datetime(raw.get("response_attempted_at")),
                    delivery_acked_at=_backup_datetime(raw.get("delivery_acked_at")),
                    cancel_requested=bool(raw.get("cancel_requested")),
                    created_at=created_at,
                    queued_at=_backup_datetime(raw.get("queued_at"), created_at) or created_at,
                    started_at=_backup_datetime(raw.get("started_at")),
                    completed_at=_backup_datetime(raw.get("completed_at")),
                    updated_at=_backup_datetime(raw.get("updated_at"), created_at) or created_at,
                    version=max(1, int(raw.get("version") or 1)),
                ))
                task_ids.append(task_id)
            session.flush()

            restored_job_ids: set[UUID] = set()
            for raw in collections["jobs"]:
                if not isinstance(raw, dict):
                    raise ValueError("image queue backup contains an invalid job")
                job_id = _uuid(raw.get("id"))
                task_id = _uuid(raw.get("task_id"))
                if job_id is None or task_id is None:
                    raise ValueError("image queue backup contains an invalid job id")
                created_at = _backup_datetime(raw.get("created_at"), now) or now
                status = JobStatus(str(raw.get("status") or JobStatus.QUEUED.value))
                stage = JobStage(str(raw.get("stage") or JobStage.QUEUED.value))
                if status in {JobStatus.LEASED, JobStatus.RUNNING}:
                    status = JobStatus.QUEUED
                inferred_quota_consumed = stage in {
                    JobStage.DOWNLOADING,
                    JobStage.TRANSFORMING,
                    JobStage.SAVING,
                    JobStage.SUCCESS,
                }
                quota_consumed = bool(raw.get("quota_consumed", inferred_quota_consumed))
                quota_accounted = bool(raw.get(
                    "quota_accounted",
                    quota_consumed and status in TERMINAL_JOB_STATUSES,
                ))
                session.add(ImageJob(
                    id=job_id,
                    task_id=task_id,
                    ordinal=max(1, int(raw.get("ordinal") or 1)),
                    status=status.value,
                    stage=stage.value,
                    generate_attempts=max(0, int(raw.get("generate_attempts") or 0)),
                    download_attempts=max(0, int(raw.get("download_attempts") or 0)),
                    save_attempts=max(0, int(raw.get("save_attempts") or 0)),
                    available_at=_backup_datetime(raw.get("available_at"), now) or now,
                    next_retry_at=_backup_datetime(raw.get("next_retry_at")),
                    lease_version=max(0, int(raw.get("lease_version") or 0)),
                    account_id=_uuid(raw.get("account_id")),
                    conversation_id=str(raw.get("conversation_id") or "") or None,
                    image_urls=list(raw.get("image_urls") or []),
                    file_ids=list(raw.get("file_ids") or []),
                    sediment_ids=list(raw.get("sediment_ids") or []),
                    quota_consumed=quota_consumed,
                    quota_accounted=quota_accounted,
                    result_payload=dict(raw.get("result_payload") or {}),
                    stage_timings=dict(raw.get("stage_timings") or {}),
                    error_code=str(raw.get("error_code") or "") or None,
                    error_message=str(raw.get("error_message") or "") or None,
                    created_at=created_at,
                    updated_at=_backup_datetime(raw.get("updated_at"), created_at) or created_at,
                    started_at=_backup_datetime(raw.get("started_at")),
                    completed_at=_backup_datetime(raw.get("completed_at")),
                ))
                restored_job_ids.add(job_id)
            session.flush()

            for raw in collections["artifacts"]:
                if not isinstance(raw, dict):
                    raise ValueError("image queue backup contains an invalid artifact")
                artifact_id = _uuid(raw.get("id"))
                task_id = _uuid(raw.get("task_id"))
                job_id = _uuid(raw.get("job_id"))
                if artifact_id is None or task_id is None:
                    raise ValueError("image queue backup contains an invalid artifact id")
                payload_blob: bytes | None = None
                encoded_payload = raw.get("payload_blob_b64")
                if encoded_payload not in (None, ""):
                    try:
                        payload_blob = base64.b64decode(
                            str(encoded_payload),
                            validate=True,
                        )
                    except (ValueError, TypeError) as exc:
                        raise ValueError("image queue backup contains an invalid artifact payload") from exc
                    if len(payload_blob) != max(0, int(raw.get("byte_size") or 0)):
                        raise ValueError("image queue backup artifact payload size mismatch")
                    if sha256(payload_blob).hexdigest() != str(raw.get("sha256") or ""):
                        raise ValueError("image queue backup artifact payload checksum mismatch")
                session.add(ImageTaskArtifact(
                    id=artifact_id,
                    task_id=task_id,
                    job_id=job_id,
                    kind=str(raw.get("kind") or "final"),
                    ordinal=(
                        max(1, int(raw.get("ordinal")))
                        if raw.get("ordinal") not in (None, "")
                        else None
                    ),
                    status=ArtifactStatus(str(raw.get("status") or ArtifactStatus.READY.value)).value,
                    storage_backend=str(raw.get("storage_backend") or "local"),
                    worker_id=str(raw.get("worker_id") or ""),
                    relative_path=str(raw.get("relative_path") or ""),
                    sha256=str(raw.get("sha256") or ""),
                    mime_type=str(raw.get("mime_type") or "image/png"),
                    byte_size=max(0, int(raw.get("byte_size") or 0)),
                    width=max(0, int(raw.get("width") or 0)),
                    height=max(0, int(raw.get("height") or 0)),
                    source_url=str(raw.get("source_url") or "") or None,
                    payload_blob=payload_blob,
                    created_at=_backup_datetime(raw.get("created_at"), now) or now,
                    ready_at=_backup_datetime(raw.get("ready_at")),
                ))

            for raw in collections["events"]:
                if not isinstance(raw, dict) or (task_id := _uuid(raw.get("task_id"))) is None:
                    raise ValueError("image queue backup contains an invalid event")
                session.add(ImageTaskEvent(
                    task_id=task_id,
                    job_id=_uuid(raw.get("job_id")),
                    attempt=max(0, int(raw.get("attempt") or 0)),
                    event_type=str(raw.get("event_type") or "restored_event"),
                    from_status=str(raw.get("from_status") or "") or None,
                    to_status=str(raw.get("to_status") or "") or None,
                    event_data=dict(raw.get("event_data") or {}),
                    created_at=_backup_datetime(raw.get("created_at"), now) or now,
                ))

            for raw in collections["legacy_imports"]:
                if not isinstance(raw, dict) or not str(raw.get("file_sha256") or ""):
                    continue
                session.add(ImageLegacyImport(
                    file_sha256=str(raw["file_sha256"]),
                    source_path=str(raw.get("source_path") or "backup"),
                    summary=dict(raw.get("summary") or {}),
                    imported_at=_backup_datetime(raw.get("imported_at"), now) or now,
                ))
            for task_id in task_ids:
                task = session.get(ImageTask, task_id)
                if task is not None:
                    self._aggregate_task(session, task)
            return {
                "tasks": len(task_ids),
                "jobs": len(restored_job_ids),
                "events": len(collections["events"]),
                "artifacts": len(collections["artifacts"]),
            }

    def legacy_import_exists(self, file_sha256: str) -> bool:
        with self.database.session() as session:
            return session.get(ImageLegacyImport, str(file_sha256)) is not None

    def import_legacy_record(self, record: dict[str, Any], file_sha256: str) -> bool:
        owner_key = str(record["owner_key"])
        client_task_id = str(record["client_task_id"])
        with self.database.session() as session:
            if self._find_task(session, owner_key, client_task_id) is not None:
                return False
            status = TaskStatus(str(record["status"]))
            required_jobs = max(1, int(record.get("required_jobs") or 1))
            created_at = record["created_at"]
            updated_at = record["updated_at"]
            error_code = str(record.get("error_code") or "") or None
            error_message = str(record.get("error_message") or "") or None
            succeeded_jobs = required_jobs if status == TaskStatus.SUCCESS else 0
            failed_jobs = required_jobs if status == TaskStatus.FAILED else 0
            legacy_key_hash = sha256(
                f"{owner_key}\0{client_task_id}".encode("utf-8")
            ).hexdigest()[:32]
            task = ImageTask(
                owner_key=owner_key,
                client_task_id=client_task_id,
                idempotency_key=f"legacy:{file_sha256[:16]}:{legacy_key_hash}",
                request_hash=str(record["request_hash"]),
                task_type=str(record.get("task_type") or "generation"),
                public_model=str(record.get("public_model") or "gpt-image-2"),
                original_prompt="",
                effective_prompt="",
                request_payload=dict(record.get("request_payload") or {}),
                required_jobs=required_jobs,
                succeeded_jobs=succeeded_jobs,
                failed_jobs=failed_jobs,
                status=status.value,
                error_code=error_code,
                error_message=error_message,
                delivery_status=DeliveryStatus.PENDING.value,
                created_at=created_at,
                queued_at=created_at,
                started_at=created_at if status == TaskStatus.SUCCESS else None,
                completed_at=updated_at,
                updated_at=updated_at,
            )
            session.add(task)
            session.flush()
            results = list(record.get("result_data") or [])
            for ordinal in range(1, required_jobs + 1):
                result_payload = results[ordinal - 1] if ordinal <= len(results) else {}
                job_status = JobStatus.SUCCESS if status == TaskStatus.SUCCESS else JobStatus.FAILED
                job_stage = JobStage.SUCCESS if status == TaskStatus.SUCCESS else JobStage.FAILED
                session.add(ImageJob(
                    task_id=task.id,
                    ordinal=ordinal,
                    status=job_status.value,
                    stage=job_stage.value,
                    result_payload=dict(result_payload) if isinstance(result_payload, dict) else {},
                    error_code=error_code,
                    error_message=error_message,
                    created_at=created_at,
                    started_at=created_at if status == TaskStatus.SUCCESS else None,
                    completed_at=updated_at,
                    updated_at=updated_at,
                ))
            self._event(
                session,
                task_id=task.id,
                event_type="legacy_task_imported",
                to_status=status.value,
                data={"file_sha256": file_sha256},
            )
            return True

    def record_legacy_import(self, file_sha256: str, source_path: str, summary: dict[str, Any]) -> None:
        with self.database.session() as session:
            if session.get(ImageLegacyImport, file_sha256) is None:
                session.add(ImageLegacyImport(
                    file_sha256=file_sha256,
                    source_path=str(source_path),
                    summary=dict(summary),
                ))

    def update_worker_state(
        self,
        worker_id: str,
        *,
        resource_snapshot: dict[str, Any],
        effective_concurrency: int,
        pause_reason: str = "",
    ) -> None:
        swallow_sqlalchemy_races = self.database.engine is not None and self.database.engine.dialect.name == "sqlite"
        for attempt in range(5):
            try:
                self._update_worker_state_once(
                    worker_id,
                    resource_snapshot=resource_snapshot,
                    effective_concurrency=effective_concurrency,
                    pause_reason=pause_reason,
                )
                return
            except IntegrityError:
                if attempt < 4:
                    continue
                raise
            except SQLAlchemyError:
                if swallow_sqlalchemy_races and attempt < 4:
                    continue
                raise
            except ImageQueueUnavailableError:
                if swallow_sqlalchemy_races and attempt < 4:
                    continue
                raise

    def delete_worker_state(self, worker_id: str) -> bool:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            return False
        with self.database.session() as session:
            result = session.execute(
                delete(ImageWorkerState).where(ImageWorkerState.worker_id == normalized_worker_id)
            )
            return bool(result.rowcount or 0)

    def _update_worker_state_once(
        self,
        worker_id: str,
        *,
        resource_snapshot: dict[str, Any],
        effective_concurrency: int,
        pause_reason: str = "",
    ) -> None:
        with self.database.session() as session:
            now = utc_now()
            snapshot = dict(resource_snapshot)
            state = session.execute(
                select(ImageWorkerState)
                .where(ImageWorkerState.worker_id == worker_id)
                .with_for_update()
            ).scalar_one_or_none()
            current_instance_id = _snapshot_text(snapshot, "instance_id")
            if state is not None:
                previous_snapshot = dict(state.resource_snapshot or {})
                previous_instance_id = _snapshot_text(previous_snapshot, "instance_id")
                heartbeat_age = (
                    now - state.heartbeat_at.astimezone(timezone.utc)
                    if state.heartbeat_at.tzinfo is not None
                    else now - state.heartbeat_at.replace(tzinfo=timezone.utc)
                ).total_seconds()
                active_heartbeat = heartbeat_age <= max(60.0, self.lease_seconds * 2.0)
                if active_heartbeat:
                    if (
                        current_instance_id
                        and previous_instance_id
                        and previous_instance_id != current_instance_id
                    ):
                        raise RuntimeError(
                            f"worker identity conflict for {worker_id}: "
                            f"active instance {previous_instance_id}"
                        )
                    current_process_id = _snapshot_text(snapshot, "process_instance_id")
                    previous_process_id = _snapshot_text(previous_snapshot, "process_instance_id")
                    if (
                        current_instance_id
                        and previous_instance_id
                        and current_instance_id == previous_instance_id
                        and current_process_id
                        and previous_process_id
                        and current_process_id != previous_process_id
                    ):
                        current_started_at = _snapshot_datetime(snapshot, "process_started_at")
                        previous_started_at = (
                            _snapshot_datetime(previous_snapshot, "process_started_at")
                            or _as_utc(state.started_at)
                        )
                        active_job_claims = int(session.execute(
                            select(func.count())
                            .select_from(ImageJob)
                            .where(
                                ImageJob.lease_owner == worker_id,
                                ImageJob.status.in_([JobStatus.LEASED.value, JobStatus.RUNNING.value]),
                                ImageJob.lease_expires_at.is_not(None),
                                ImageJob.lease_expires_at > now,
                            )
                        ).scalar_one())
                        active_account_claims = int(session.execute(
                            select(func.count())
                            .select_from(ImageAccountLease)
                            .where(
                                ImageAccountLease.lease_owner == worker_id,
                                ImageAccountLease.expires_at > now,
                            )
                        ).scalar_one())
                        active_claims = max(active_job_claims, active_account_claims)
                        if (
                            current_started_at is None
                            or previous_started_at is None
                            or current_started_at < previous_started_at
                            or active_claims > 0
                        ):
                            raise RuntimeError(
                                f"worker identity conflict for {worker_id}: "
                                f"active process {previous_process_id} on instance {previous_instance_id}"
                            )
                for key in WORKER_IDENTITY_SNAPSHOT_KEYS:
                    if not _snapshot_text(snapshot, key) and _snapshot_text(previous_snapshot, key):
                        snapshot[key] = previous_snapshot[key]
                for key in WORKER_DELIVERY_SNAPSHOT_KEYS:
                    if key not in snapshot and key in previous_snapshot:
                        snapshot[key] = previous_snapshot[key]
            if state is None:
                state = ImageWorkerState(worker_id=worker_id, heartbeat_at=now)
                session.add(state)
            state.heartbeat_at = now
            state.resource_snapshot = snapshot
            state.effective_concurrency = max(0, int(effective_concurrency))
            state.pause_reason = pause_reason or None

    def record_worker_delivery_status(
        self,
        worker_id: str,
        *,
        healthy: bool,
        url: str,
        error: str = "",
        checked_at: datetime | None = None,
    ) -> bool:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            return False
        checked = checked_at or utc_now()
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        else:
            checked = checked.astimezone(timezone.utc)
        with self.database.session() as session:
            state = session.execute(
                select(ImageWorkerState)
                .where(ImageWorkerState.worker_id == normalized_worker_id)
                .with_for_update()
            ).scalar_one_or_none()
            if state is None:
                return False
            snapshot = dict(state.resource_snapshot or {})
            try:
                previous_failures = int(float(str(snapshot.get("delivery_failures") or "0")))
            except (TypeError, ValueError):
                previous_failures = 0
            snapshot.update({
                "delivery_status": "healthy" if healthy else "unhealthy",
                "delivery_checked_at": checked.isoformat(),
                "delivery_url": str(url or "").strip(),
                "delivery_error": "" if healthy else str(error or "")[:500],
                "delivery_failures": 0 if healthy else max(0, previous_failures) + 1,
            })
            state.resource_snapshot = snapshot
            return True

    def worker_image_base_url(self, worker_id: str) -> str:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            return ""
        with self.database.session() as session:
            snapshot = session.execute(
                select(ImageWorkerState.resource_snapshot)
                .where(ImageWorkerState.worker_id == normalized_worker_id)
            ).scalar_one_or_none()
            return str((dict(snapshot or {})).get("image_base_url") or "").strip()
