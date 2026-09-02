from __future__ import annotations

import os
import re
from pathlib import Path


_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_KEYS = frozenset(
    {
        "AUTH_KEY",
        "BASE_URL",
        "IMAGE_BASE_URL",
        "IMAGE_PORT",
        "PORT",
        "WORKER_ID",
        "WIREGUARD_IP",
        "NODE_ROLE",
        "CLUSTER_ID",
        "CHATGPT2API_AUTH_KEY",
        "CHATGPT2API_INSTALL_TARGET",
        "CHATGPT2API_CREATE_FIRST_WORKER",
        "CHATGPT2API_CLUSTER_JOIN_REQUEST_DIR",
        "CHATGPT2API_CLUSTER_JOIN_SIGNING_PUBLIC_KEY_FILE",
        "CHATGPT2API_JOIN_SIGNING_PUBLIC_KEY_B64",
        "CHATGPT2API_PROXY_RUNTIME_FORCE",
        "CHATGPT2API_BACKUP_PASSPHRASE",
        "CHATGPT2API_CONFIG_FILE",
        "CHATGPT2API_MONITOR_COMPLETED_LIMIT",
        "CHATGPT2API_MONITOR_EVENT_LIMIT",
        "CHATGPT2API_PORT",
        "CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS",
        "CHATGPT2API_RUNTIME_LOG_FILE",
        "CHATGPT2API_THREAD_TOKENS",
        "CHATGPT2API_IMAGE",
        "CHATGPT2API_IMAGE_DIGEST",
        "CHATGPT2API_RELEASE_REF",
        "UV_VERSION",
        "CHATGPT2API_BASE_URL",
        "CHATGPT2API_IMAGE_BASE_URL",
        "CHATGPT2API_PYTHON_BIN",
        "CHATGPT2API_IMAGE_PORT",
        "CHATGPT2API_NODE_ROLE",
        "CHATGPT2API_INSTANCE_PREFIX",
        "CHATGPT2API_COMPOSE_PROJECT_NAME",
        "COMPOSE_PROJECT_NAME",
        # The installer writes COMPOSE_PROFILES for Compose's benefit, but the same
        # .env is loaded by the Python entrypoints, so it has to be accepted here too.
        "COMPOSE_PROFILES",
        "CHATGPT2API_WARP_IMAGE",
        "CHATGPT2API_PRIVOXY_IMAGE",
        "CHATGPT2API_FLARESOLVERR_IMAGE",
        "CHATGPT2API_RUN_API",
        "CHATGPT2API_RUN_WORKER",
        "CHATGPT2API_WORKER_ID",
        "CHATGPT2API_WORKER_PUBLIC_ENTRY_MODE",
        "CHATGPT2API_WORKER_BIND_HOST",
        "CHATGPT2API_WIREGUARD_IP",
        "CHATGPT2API_CLUSTER_ID",
        "CHATGPT2API_WORKER_JOINED_MARKER_FILE",
        "CHATGPT2API_IMAGE_QUEUE_INSTANCE_ID",
        "CHATGPT2API_IMAGE_QUEUE_VERIFY_RETURNED_URL",
        "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS",
        "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS",
        "CHATGPT2API_IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES",
        "IMAGE_PROMPT_SUFFIX_ENABLED",
        "IMAGE_PROMPT_SUFFIX",
        "IMAGE_QUEUE_RECOVERY_ACCOUNT_TIMEOUT_SECONDS",
        "IMAGE_QUEUE_DELIVERY_GRACE_SECONDS",
        "IMAGE_QUEUE_TERMINAL_RETENTION_SECONDS",
        "IMAGE_QUEUE_STARTUP_RETRY_SECONDS",
        "IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS",
        "IMAGE_QUEUE_GENERATION_CONCURRENCY",
        "IMAGE_QUEUE_GENERATION_CONCURRENCY_CAP",
        "IMAGE_QUEUE_ABSOLUTE_GUARD",
        "IMAGE_QUEUE_MAX_BACKLOG",
        "IMAGE_QUEUE_PENDING_TTL_SECONDS",
        "IMAGE_QUEUE_CLAIM_MAX_RUNTIME_SECONDS",
        "CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS",
        "CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS",
        "CHATGPT2API_PROXY_RUNTIME_ENABLED",
        "CHATGPT2API_PROXY_RUNTIME_EGRESS_MODE",
        "CHATGPT2API_PROXY_RUNTIME_PROXY_URL",
        "CHATGPT2API_PROXY_RUNTIME_RESOURCE_PROXY_URL",
        "CHATGPT2API_PROXY_RUNTIME_SKIP_SSL_VERIFY",
        "CHATGPT2API_PROXY_RUNTIME_RESET_STATUS_CODES",
        "CHATGPT2API_PROXY_RUNTIME_CLEARANCE_ENABLED",
        "CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE",
        "CHATGPT2API_PROXY_RUNTIME_CLEARANCE_TIMEOUT_SEC",
        "CHATGPT2API_PROXY_RUNTIME_CLEARANCE_REFRESH_INTERVAL",
        "CHATGPT2API_PROXY_RUNTIME_WARM_UP_ON_START",
        "CHATGPT2API_PROXY_RUNTIME_BROWSER",
        "CHATGPT2API_PROXY_RUNTIME_USER_AGENT",
        "CHATGPT2API_FLARESOLVERR_URL",
        "WARP_LICENSE_KEY",
        "CHATGPT2API_PYTHON_PID_FILE",
        "MODE",
        "WITH_WARP",
        "WIREGUARD_INTERFACE",
        "WIREGUARD_SERVER_IP",
        "WIREGUARD_SERVER_ENDPOINT",
        "WIREGUARD_PORT",
        "CHATGPT2API_POSTGRES_PORT",
        "STORAGE_BACKEND",
        "APP_DATABASE_URL",
        "DATABASE_URL",
        "IMAGE_QUEUE_DATABASE_URL",
        "IMAGE_QUEUE_INSTANCE_ID",
        "IMAGE_QUEUE_VERIFY_RETURNED_URL",
        "IMAGE_QUEUE_RETURNED_URL_VERIFY_TIMEOUT_SECONDS",
        "IMAGE_QUEUE_RETURNED_URL_VERIFY_ATTEMPTS",
        "IMAGE_QUEUE_RETURNED_URL_VERIFY_MAX_BYTES",
        "POSTGRES_PASSWORD",
        "POSTGRES_ADMIN_USER",
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_PASSWORD_URLENCODED",
        "GIT_REPO_URL",
        "GIT_TOKEN",
        "GIT_BRANCH",
        "GIT_FILE_PATH",
        "GIT_AUTH_KEYS_FILE_PATH",
        "WARP_SOCKS_PORT",
        "PRIVOXY_PORT",
        "FLARESOLVERR_PORT",
        "FLARESOLVERR_LOG_LEVEL",
        "TZ",
        "CHATGPT2API_MAIN_LIVENESS_TIMEOUT_SECONDS",
        "HOST",
        "LOG_LEVEL",
        "UVICORN_WORKERS",
        "IMAGE_QUEUE_ARTIFACT_ROOT",
        "IMAGE_QUEUE_LEGACY_TASK_PATH",
        "IMAGE_QUEUE_LEASE_SECONDS",
        "IMAGE_QUEUE_HEARTBEAT_SECONDS",
        "IMAGE_QUEUE_POLL_INTERVAL_SECONDS",
        "IMAGE_QUEUE_RESULT_WAIT_POLL_SECONDS",
        "IMAGE_QUEUE_GENERATION_ATTEMPTS",
        "IMAGE_QUEUE_DOWNLOAD_ATTEMPTS",
        "IMAGE_QUEUE_SAVE_ATTEMPTS",
        "IMAGE_QUEUE_CPU_THROTTLE_PERCENT",
        "IMAGE_QUEUE_CPU_PAUSE_PERCENT",
        "IMAGE_QUEUE_CPU_RESUME_PERCENT",
        "IMAGE_QUEUE_MEMORY_THROTTLE_PERCENT",
        "IMAGE_QUEUE_MEMORY_PAUSE_PERCENT",
        "IMAGE_QUEUE_MEMORY_REJECT_PERCENT",
        "IMAGE_QUEUE_DB_POOL_SIZE",
        "IMAGE_QUEUE_DB_MAX_OVERFLOW",
        "EDITABLE_FILE_WORKERS",
        "EDITABLE_FILE_MAX_BACKLOG",
        "PROMPT_LIBRARY_DEFAULT_URL",
        "PROMPT_LIBRARY_REMOTE_URL",
    }
)


def _decode_single_quoted(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith("'\\''", index):
            result.append("'")
            index += 4
            continue
        if value[index] == "'":
            raise ValueError("invalid dotenv value: unescaped single quote")
        result.append(value[index])
        index += 1
    return "".join(result)


def _decode_double_quoted(value: str) -> str:
    result: list[str] = []
    index = 0
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    while index < len(value):
        character = value[index]
        if character == '"':
            raise ValueError("invalid dotenv value: unescaped double quote")
        if character == "\\":
            index += 1
            if index >= len(value) or value[index] not in escapes:
                raise ValueError("invalid dotenv value: unsupported escape")
            result.append(escapes[value[index]])
        else:
            result.append(character)
        index += 1
    return "".join(result)


def _decode_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""

    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ValueError(f"invalid dotenv value on line {line_number}")
        decoded = _decode_single_quoted(value[1:-1])
    elif value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise ValueError(f"invalid dotenv value on line {line_number}")
        decoded = _decode_double_quoted(value[1:-1])
    else:
        if any(character.isspace() for character in value):
            raise ValueError(f"invalid dotenv value on line {line_number}")
        decoded = value

    if "$((" in decoded or "$(" in decoded or "${" in decoded or "`" in decoded:
        raise ValueError(f"invalid dotenv value on line {line_number}")
    return decoded


def load_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.is_file():
        raise ValueError(f"dotenv file does not exist: {env_path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        env_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid dotenv line on line {line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid dotenv key on line {line_number}: {key}")
        if key not in _ALLOWED_KEYS:
            raise ValueError(f"unsupported dotenv key on line {line_number}: {key}")
        if key in values:
            raise ValueError(f"duplicate dotenv key on line {line_number}: {key}")
        values[key] = _decode_value(raw_value, line_number=line_number)
    return values


def load_env_file_into_environment(path: str | Path | None) -> None:
    if not path:
        return
    os.environ.update(load_env_file(path))
