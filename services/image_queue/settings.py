from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket

from services.config import config
from services.database_url import IMAGE_QUEUE_DATABASE_NAME, select_named_postgres_database


DEFAULT_PENDING_TTL_SECONDS = 30 * 60
DEFAULT_PROMPT_SUFFIX = (
    "请直接生成最终图片，只输出图片结果，不要回复解释、拒绝说明、文字描述或 Markdown。"
    "高清画质，细节丰富，主体清晰，构图完整。"
)


class ImageQueueConfigurationError(RuntimeError):
    pass


def _first_env_value(name: str | tuple[str, ...], default: object) -> object:
    names = (name,) if isinstance(name, str) else name
    for candidate in names:
        raw = os.getenv(candidate)
        if raw is not None and str(raw).strip():
            return raw
    return default


def _env_int(name: str | tuple[str, ...], default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(str(_first_env_value(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def _env_float(name: str | tuple[str, ...], default: float, minimum: float = 0.0) -> float:
    try:
        value = float(str(_first_env_value(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_bool(name: str | tuple[str, ...], default: bool) -> bool:
    raw = str(_first_env_value(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
        return False
    return default


def _runtime_image_concurrency_default() -> int:
    try:
        runtime = config.get_runtime_capacity_settings()
        value = int(runtime.get("image_concurrency_limit") or 2000)
    except Exception:
        value = 2000
    # Keep the default worker pool bounded even when runtime config advertises
    # an unusually large ceiling.
    return max(1, min(value, 16))


@dataclass(frozen=True)
class ImageQueueSettings:
    database_url: str
    lease_seconds: int = 90
    heartbeat_seconds: int = 15
    claim_max_runtime_seconds: int = 30 * 60
    poll_interval_seconds: float = 0.5
    result_wait_poll_seconds: float = 0.25
    protocol_wait_timeout_seconds: int = 300
    generation_attempts: int = 3
    download_attempts: int = 5
    save_attempts: int = 5
    recovery_account_timeout_seconds: int = 900
    delivery_grace_seconds: int = 7 * 24 * 60 * 60
    terminal_retention_seconds: int = 30 * 24 * 60 * 60
    cpu_throttle_percent: float = 90.0
    cpu_pause_percent: float = 95.0
    cpu_resume_percent: float = 85.0
    memory_throttle_percent: float = 85.0
    memory_pause_percent: float = 90.0
    memory_reject_percent: float = 95.0
    absolute_guard: int = 128
    generation_concurrency_limit: int = 16
    generation_concurrency_hard_cap: int = 64
    max_backlog: int = 50
    pending_ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS
    prompt_suffix_enabled: bool = True
    prompt_suffix: str = DEFAULT_PROMPT_SUFFIX
    database_pool_size: int = 20
    database_max_overflow: int = 10
    artifact_root: Path = Path("data/images")
    legacy_task_path: Path = Path("data/image_tasks.json")
    instance_id: str = ""
    verify_returned_url: bool = True
    returned_url_verify_timeout_seconds: float = 5.0
    returned_url_verify_attempts: int = 3
    returned_url_verify_max_bytes: int = 65536

    @property
    def available(self) -> bool:
        return bool(self.database_url)

    @classmethod
    def from_env(cls) -> "ImageQueueSettings":
        try:
            database_url = select_named_postgres_database(
                dedicated_url=os.getenv("IMAGE_QUEUE_DATABASE_URL") or os.getenv("CHATGPT2API_IMAGE_QUEUE_DATABASE_URL"),
                fallback_url=os.getenv("DATABASE_URL") or os.getenv("CHATGPT2API_DATABASE_URL"),
                expected_name=IMAGE_QUEUE_DATABASE_NAME,
                role="image queue",
            )
        except ValueError as exc:
            raise ImageQueueConfigurationError(str(exc)) from exc
        root = Path(str(os.getenv("IMAGE_QUEUE_ARTIFACT_ROOT") or "data/images").strip())
        legacy_task_path = Path(str(os.getenv("IMAGE_QUEUE_LEGACY_TASK_PATH") or "data/image_tasks.json").strip())
        instance_id = str(
            os.getenv("IMAGE_QUEUE_INSTANCE_ID")
            or os.getenv("CHATGPT2API_IMAGE_QUEUE_INSTANCE_ID")
            or os.getenv("HOSTNAME")
            or socket.gethostname()
            or "chatgpt2api"
        ).strip()[:120]
        generation_hard_cap = _env_int(
            "IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP",
            64,
            1,
            64,
        )
        return cls(
            database_url=database_url,
            lease_seconds=_env_int("IMAGE_QUEUE_LEASE_SECONDS", 90, 30, 900),
            heartbeat_seconds=_env_int("IMAGE_QUEUE_HEARTBEAT_SECONDS", 15, 5, 300),
            claim_max_runtime_seconds=_env_int(
                "IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS", 30 * 60, 60, 24 * 60 * 60
            ),
            poll_interval_seconds=_env_float("IMAGE_QUEUE_POLL_INTERVAL_SECONDS", 0.5, 0.05),
            result_wait_poll_seconds=_env_float("IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS", 0.25, 0.05),
            protocol_wait_timeout_seconds=_env_int(
                "IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS", 300, 30, 3600
            ),
            generation_attempts=_env_int("IMAGE_QUEUE_GENERATION_ATTEMPTS", 3, 1, 10),
            download_attempts=_env_int("IMAGE_QUEUE_DOWNLOAD_ATTEMPTS", 5, 1, 20),
            save_attempts=_env_int("IMAGE_QUEUE_SAVE_ATTEMPTS", 5, 1, 20),
            recovery_account_timeout_seconds=_env_int(
                "IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS", 900, 30, 86400
            ),
            delivery_grace_seconds=_env_int(
                "IMAGE_QUEUE_DELIVERY_GRACE_SECONDS", 7 * 24 * 60 * 60, 3600, 30 * 24 * 60 * 60
            ),
            terminal_retention_seconds=_env_int(
                "IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS",
                30 * 24 * 60 * 60,
                24 * 60 * 60,
                365 * 24 * 60 * 60,
            ),
            cpu_throttle_percent=_env_float("IMAGE_QUEUE_CPU_THROTTLE_PERCENT", 90.0, 1.0),
            cpu_pause_percent=_env_float("IMAGE_QUEUE_CPU_PAUSE_PERCENT", 95.0, 1.0),
            cpu_resume_percent=_env_float("IMAGE_QUEUE_CPU_RESUME_PERCENT", 85.0, 1.0),
            memory_throttle_percent=_env_float("IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT", 85.0, 1.0),
            memory_pause_percent=_env_float("IMAGE_QUEUE_MEMORY_PAUSE_PERCENT", 90.0, 1.0),
            memory_reject_percent=_env_float("IMAGE_QUEUE_MEMORY_REJECT_PERCENT", 95.0, 1.0),
            absolute_guard=_env_int("IMAGE_QUEUE_ABSOLUTE_GUARD", 128, 8, 128),
            generation_concurrency_limit=_env_int(
                "IMAGE_QUEUE_GENERATION_CONCURRENCY",
                min(_runtime_image_concurrency_default(), generation_hard_cap),
                1,
                generation_hard_cap,
            ),
            generation_concurrency_hard_cap=generation_hard_cap,
            max_backlog=_env_int("IMAGE_QUEUE_MAX_BACKLOG", 50, 1, 100000),
            pending_ttl_seconds=_env_int(
                "IMAGE_QUEUE_PENDING_TTL_SECONDS",
                DEFAULT_PENDING_TTL_SECONDS,
                1,
                365 * 24 * 60 * 60,
            ),
            prompt_suffix_enabled=_env_bool("IMAGE_PROMPT_SUFFIX_ENABLED", True),
            prompt_suffix=str(os.getenv("IMAGE_PROMPT_SUFFIX") or DEFAULT_PROMPT_SUFFIX).strip(),
            database_pool_size=_env_int("IMAGE_QUEUE_DB_POOL_SIZE", 20, 2, 100),
            database_max_overflow=_env_int("IMAGE_QUEUE_DB_MAX_OVERFLOW", 10, 0, 100),
            artifact_root=root,
            legacy_task_path=legacy_task_path,
            instance_id=instance_id,
            verify_returned_url=_env_bool(
                ("IMAGE_QUEUE_VERIFY_RETURNED_URL", "CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL"),
                True,
            ),
            returned_url_verify_timeout_seconds=_env_float(
                ("IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS", "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS"),
                5.0,
                0.5,
            ),
            returned_url_verify_attempts=_env_int(
                ("IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS", "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS"),
                3,
                1,
                10,
            ),
            returned_url_verify_max_bytes=_env_int(
                ("IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES", "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES"),
                65536,
                512,
                8 * 1024 * 1024,
            ),
        )
