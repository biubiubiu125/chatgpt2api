from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from services.image_queue.types import (
    ArtifactStatus,
    DeliveryStatus,
    JobStage,
    JobStatus,
    TaskStatus,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
# Integer autoincrement is portable across SQLite and PostgreSQL for the
# append-only event log; BigInteger variants previously caused NULL identity
# flushes under concurrent sqlite file connections.
EVENT_ID = Integer


class Base(DeclarativeBase):
    pass


class ImageQueueSchemaMigration(Base):
    __tablename__ = "image_queue_schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ImageLegacyImport(Base):
    __tablename__ = "image_legacy_imports"

    file_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ImageTask(Base):
    __tablename__ = "image_tasks"
    __table_args__ = (
        UniqueConstraint("owner_key", "idempotency_key", name="uq_image_tasks_owner_idempotency"),
        UniqueConstraint("owner_key", "client_task_id", name="uq_image_tasks_owner_client_task"),
        Index("ix_image_tasks_status_created", "status", "created_at"),
        Index("ix_image_tasks_terminal_cleanup", "status", "completed_at", "delivery_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_key: Mapped[str] = mapped_column(String(160), nullable=False)
    client_task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(24), nullable=False)
    public_model: Mapped[str] = mapped_column(String(64), default="gpt-image-2", nullable=False)
    original_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effective_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_suffix_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    required_jobs: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    succeeded_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.QUEUED.value, nullable=False)
    wait_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(24), default=DeliveryStatus.PENDING.value, nullable=False
    )
    response_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)


class ImageJob(Base):
    __tablename__ = "image_jobs"
    __table_args__ = (
        UniqueConstraint("task_id", "ordinal", name="uq_image_jobs_task_ordinal"),
        Index("ix_image_jobs_claim", "status", "available_at", "created_at"),
        Index("ix_image_jobs_lease_expiry", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_tasks.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.QUEUED.value, nullable=False)
    stage: Mapped[str] = mapped_column(String(24), default=JobStage.QUEUED.value, nullable=False)
    generate_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    save_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_urls: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list, nullable=False)
    file_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list, nullable=False)
    sediment_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, default=list, nullable=False)
    quota_consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quota_accounted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    stage_timings: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageTaskEvent(Base):
    __tablename__ = "image_task_events"
    __table_args__ = (
        Index("ix_image_task_events_task_created", "task_id", "created_at"),
        Index("ix_image_task_events_job_created", "job_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(EVENT_ID, primary_key=True, autoincrement=True)
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_tasks.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_jobs.id", ondelete="CASCADE"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ImageTaskArtifact(Base):
    __tablename__ = "image_task_artifacts"
    __table_args__ = (
        UniqueConstraint("relative_path", name="uq_image_task_artifacts_path"),
        UniqueConstraint("job_id", "kind", "sha256", name="uq_image_task_artifacts_job_kind_hash"),
        Index("ix_image_task_artifacts_task_kind", "task_id", "kind"),
        Index(
            "uq_image_task_artifacts_task_kind_ordinal",
            "task_id",
            "kind",
            "ordinal",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_tasks.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_jobs.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=ArtifactStatus.STAGING.value, nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(24), default="local", nullable=False)
    worker_id: Mapped[str] = mapped_column(String(160), default="", server_default="", nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageAccountLease(Base):
    __tablename__ = "image_account_leases"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_image_account_leases_job"),
        Index("ix_image_account_leases_expiry", "expires_at"),
    )

    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    slot_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("image_jobs.id", ondelete="CASCADE"), nullable=False
    )
    lease_owner: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    lease_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImageWorkerState(Base):
    __tablename__ = "image_worker_state"

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    effective_concurrency: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pause_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
