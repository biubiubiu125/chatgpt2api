from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SAVING = "saving"
    RETRYING = "retrying"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class JobStage(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    GENERATING = "generating"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    TRANSFORMING = "transforming"
    SAVING = "saving"
    RETRY_WAIT = "retry_wait"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class ArtifactStatus(StrEnum):
    STAGING = "staging"
    READY = "ready"
    INVALID = "invalid"


class LocalArtifactRecoveryUnavailable(RuntimeError):
    pass


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    RESPONSE_ATTEMPTED = "response_attempted"
    ACKNOWLEDGED = "acknowledged"


TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCESS,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
}
TERMINAL_JOB_STATUSES = {
    JobStatus.SUCCESS,
    JobStatus.FAILED,
    JobStatus.CANCELED,
}


@dataclass(frozen=True)
class ArtifactDescriptor:
    task_id: UUID
    job_id: UUID | None
    kind: str
    status: ArtifactStatus
    relative_path: str
    sha256: str
    mime_type: str
    byte_size: int
    width: int
    height: int
    public_url: str = ""
    storage_backend: str = "local"
    absolute_path: Path | None = None
    source_url: str = ""
    ordinal: int | None = None
    worker_id: str = ""


@dataclass(frozen=True)
class EnqueueRequest:
    owner_key: str
    idempotency_key: str
    request_hash: str
    task_type: str
    original_prompt: str
    effective_prompt: str
    request_payload: dict[str, Any]
    required_jobs: int
    client_task_id: str = ""
    public_model: str = "gpt-image-2"
    prompt_suffix_version: str | None = None
    task_id: UUID | None = None
    input_artifacts: tuple[ArtifactDescriptor, ...] = ()


@dataclass(frozen=True)
class EnqueueResult:
    task: "TaskSnapshot"
    created: bool


@dataclass(frozen=True)
class TaskSnapshot:
    id: UUID
    owner_key: str
    client_task_id: str
    idempotency_key: str
    status: TaskStatus
    required_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    task_type: str = "generation"
    public_model: str = "gpt-image-2"
    request_hash: str = ""
    request_payload: dict[str, Any] = field(default_factory=dict)
    wait_reason: str = ""
    error_code: str = ""
    error_message: str = ""
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    stage: str = ""
    progress: str = ""
    conversation_id: str = ""
    can_resume_poll: bool = False
    data: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class JobSnapshot:
    id: UUID
    task_id: UUID
    ordinal: int
    status: JobStatus
    stage: JobStage
    available_at: datetime | None = None
    generate_attempts: int = 0
    download_attempts: int = 0
    save_attempts: int = 0
    account_id: UUID | None = None
    conversation_id: str = ""
    image_urls: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    sediment_ids: list[str] = field(default_factory=list)
    quota_consumed: bool = False
    quota_accounted: bool = False
    result_payload: dict[str, Any] = field(default_factory=dict)
    lease_version: int = 0


@dataclass(frozen=True)
class ClaimedJob:
    job: JobSnapshot
    lease_token: UUID
    lease_version: int
    lease_owner: str
    lease_expires_at: datetime
    account_id: UUID
    account_slot: int


@dataclass(frozen=True)
class JobCheckpoint:
    stage: JobStage
    conversation_id: str = ""
    image_urls: list[str] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)
    sediment_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImageAccountCandidate:
    account_id: UUID
    access_token: str
    plan_type: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_percent: float
    available_memory_bytes: int
    memory_limit_bytes: int
    swap_in_bytes_per_second: int
    swap_out_bytes_per_second: int
    thread_count: int
    file_handle_count: int
    database_pool_percent: float
    disk_free_bytes: int
    disk_free_percent: float
    sampled_at: datetime


@dataclass(frozen=True)
class ResourceDecision:
    allowed: bool
    reason: str = ""
    effective_limit: int = 0


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    next_retry_at: datetime | None = None
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class RecoverySummary:
    reclaimed: int = 0
    requeued: int = 0
    resumed_downloads: int = 0
    resumed_generation: int = 0


@dataclass(frozen=True)
class LegacyImportSummary:
    file_sha256: str = ""
    imported_terminal: int = 0
    interrupted: int = 0
    ignored: int = 0
    skipped_file: bool = False
    error: str = ""


@dataclass(frozen=True)
class RegistrationWindow:
    name: str
    target_available: int
    time_range: str
    threads: int = 1
