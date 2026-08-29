from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Mapping
from urllib.parse import urlsplit

from services.database_url import (
    APP_DATABASE_NAME,
    IMAGE_QUEUE_DATABASE_NAME,
    validate_named_postgres_database,
)
from services.returned_url_verifier import ReturnedUrlVerificationError, validate_public_image_base_url


ROLE_STANDALONE = "standalone"
ROLE_API_MAIN = "api-main"
ROLE_WORKER = "worker"
WORKER_JOIN_STATUS_ACTIVATING = "activating"
WORKER_JOIN_STATUS_JOINED = "joined"
DEFAULT_WORKER_JOINED_MARKER_FILE = "/app/data/worker.joined"
WORKER_ID_MAX_LENGTH = 64


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return default
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
        return False
    return default


def normalize_node_role(value: object) -> str:
    role = _clean(value).lower().replace("_", "-")
    if not role or role in {"standalone", "single", "single-node"}:
        return ROLE_STANDALONE
    if role in {"main", "api", "api-main", "master", "primary"}:
        return ROLE_API_MAIN
    if role in {"worker", "worker-node", "node"}:
        return ROLE_WORKER
    raise ValueError("CHATGPT2API_NODE_ROLE must be one of: standalone, api-main, worker")


def _normalize_public_base_url(value: object, *, resolve_host: bool = False) -> str:
    text = _clean(value).rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("CHATGPT2API_IMAGE_BASE_URL must be an http or https URL without query or fragment")
    try:
        validate_public_image_base_url(text, resolve_host=resolve_host)
    except ReturnedUrlVerificationError as exc:
        raise ValueError(str(exc)) from exc
    return text


def _parse_unix_timestamp(value: object) -> int:
    text = _clean(value)
    if not text:
        raise ValueError("timestamp is empty")
    return int(float(text))


@dataclass(frozen=True)
class ClusterSettings:
    node_role: str = ROLE_STANDALONE
    run_api: bool = True
    run_worker: bool = True
    worker_id: str = ""
    wireguard_ip: str = ""
    image_base_url: str = ""
    cluster_id: str = ""

    @property
    def is_standalone(self) -> bool:
        return self.node_role == ROLE_STANDALONE

    @property
    def is_api_main(self) -> bool:
        return self.node_role == ROLE_API_MAIN

    @property
    def is_worker(self) -> bool:
        return self.node_role == ROLE_WORKER


def load_cluster_settings(
    env: Mapping[str, object] | None = None,
    *,
    resolve_image_base_host: bool = False,
) -> ClusterSettings:
    source = env if env is not None else os.environ
    node_role = normalize_node_role(source.get("CHATGPT2API_NODE_ROLE"))
    run_api_default = node_role != ROLE_WORKER
    run_worker_default = node_role != ROLE_API_MAIN
    settings = ClusterSettings(
        node_role=node_role,
        run_api=_normalize_bool(source.get("CHATGPT2API_RUN_API"), run_api_default),
        run_worker=_normalize_bool(source.get("CHATGPT2API_RUN_WORKER"), run_worker_default),
        worker_id=_clean(source.get("CHATGPT2API_WORKER_ID") or source.get("IMAGE_QUEUE_WORKER_ID")),
        wireguard_ip=_clean(source.get("CHATGPT2API_WIREGUARD_IP") or source.get("WIREGUARD_IP")),
        image_base_url=_normalize_public_base_url(
            source.get("CHATGPT2API_IMAGE_BASE_URL"),
            resolve_host=resolve_image_base_host,
        ),
        cluster_id=_clean(source.get("CHATGPT2API_CLUSTER_ID")),
    )
    if settings.is_worker:
        if settings.run_api:
            raise ValueError("worker role cannot run API routes")
        if not settings.run_worker:
            raise ValueError("worker role must run image worker")
    if settings.is_api_main:
        if not settings.run_api:
            raise ValueError("api-main role must run API routes")
        if settings.run_worker:
            raise ValueError("api-main role cannot run image worker")
    if settings.is_standalone and (not settings.run_api or not settings.run_worker):
        raise ValueError("standalone role must run both API routes and image worker")
    if settings.node_role == ROLE_WORKER and (
        not settings.worker_id
        or not settings.wireguard_ip
        or not settings.image_base_url
        or not settings.cluster_id
    ):
        raise ValueError(
            "worker role requires CHATGPT2API_WORKER_ID, "
            "CHATGPT2API_WIREGUARD_IP, CHATGPT2API_IMAGE_BASE_URL and "
            "CHATGPT2API_CLUSTER_ID"
        )
    if settings.is_worker and len(settings.worker_id) > WORKER_ID_MAX_LENGTH:
        raise ValueError(f"worker id must be {WORKER_ID_MAX_LENGTH} characters or fewer")
    return settings


def validate_cluster_database_environment(
    settings: ClusterSettings | None = None,
    env: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    """Validate the dedicated PostgreSQL URLs required by cluster roles."""
    resolved_settings = settings or load_cluster_settings(env)
    if resolved_settings.is_standalone:
        return "", ""
    source = env if env is not None else os.environ
    app_url = _clean(source.get("APP_DATABASE_URL"))
    queue_url = _clean(source.get("IMAGE_QUEUE_DATABASE_URL"))
    if not app_url:
        raise ValueError(
            f"APP_DATABASE_URL is required for {resolved_settings.node_role} and must use {APP_DATABASE_NAME}"
        )
    if not queue_url:
        raise ValueError(
            f"IMAGE_QUEUE_DATABASE_URL is required for {resolved_settings.node_role} and must use {IMAGE_QUEUE_DATABASE_NAME}"
        )
    return (
        validate_named_postgres_database(app_url, APP_DATABASE_NAME, role="app"),
        validate_named_postgres_database(queue_url, IMAGE_QUEUE_DATABASE_NAME, role="image_queue"),
    )


def is_cluster_public_path(path: str, *, allow_images: bool = True) -> bool:
    normalized = "/" + _clean(path).lstrip("/")
    if normalized in {"/health", "/health/live"}:
        return True
    if not allow_images:
        return False
    return bool(
        normalized.startswith("/images/")
        or normalized.startswith("/image-thumbnails/")
    )


def _marker_pairs(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeError(f"worker join marker is missing: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"worker join marker is unreadable: {path}") from exc
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            result[key.strip()] = value.strip()
    return result


def _promote_worker_join_marker(path: Path, payload: Mapping[str, object]) -> None:
    updated = {str(key): str(value) for key, value in payload.items()}
    updated["status"] = WORKER_JOIN_STATUS_JOINED
    updated.pop("activation_expires_at", None)
    updated.setdefault("joined_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            "\n".join(f"{key}={updated[key]}" for key in sorted(updated)) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_worker_join_marker(
    settings: ClusterSettings | None = None,
    *,
    marker_file: str | os.PathLike[str] | None = None,
    allow_activating: bool = True,
) -> tuple[Path, str]:
    resolved_settings = settings or load_cluster_settings()
    if not resolved_settings.is_worker or not resolved_settings.run_worker:
        return Path(str(marker_file or "")), WORKER_JOIN_STATUS_JOINED
    marker = Path(
        str(
            marker_file
            or os.getenv("CHATGPT2API_WORKER_JOINED_MARKER_FILE")
            or DEFAULT_WORKER_JOINED_MARKER_FILE
        )
    )
    payload = _marker_pairs(marker)
    worker_id = _clean(payload.get("worker_id"))
    wireguard_ip = _clean(payload.get("wireguard_ip"))
    if worker_id != resolved_settings.worker_id or wireguard_ip != resolved_settings.wireguard_ip:
        raise RuntimeError(
            "worker join marker does not match runtime metadata: "
            f"expected {resolved_settings.worker_id}/{resolved_settings.wireguard_ip}, "
            f"got {worker_id or '-'} / {wireguard_ip or '-'}"
        )
    marker_cluster_id = _clean(payload.get("cluster_id"))
    if resolved_settings.cluster_id and marker_cluster_id != resolved_settings.cluster_id:
        raise RuntimeError(
            "worker join marker cluster id does not match runtime metadata: "
            f"expected {resolved_settings.cluster_id}, got {marker_cluster_id or '-'}"
        )
    marker_status = _clean(payload.get("status")).lower()
    activation_expired = False
    if marker_status == WORKER_JOIN_STATUS_ACTIVATING:
        if not allow_activating:
            raise RuntimeError("worker join marker is not finalized")
        try:
            activation_expires_at = _parse_unix_timestamp(payload.get("activation_expires_at"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("worker join marker activation expiry is missing") from exc
        activation_expired = activation_expires_at <= int(time.time())
    elif marker_status != WORKER_JOIN_STATUS_JOINED:
        raise RuntimeError("worker join marker is not finalized")
    app_database_url = _clean(os.getenv("APP_DATABASE_URL"))
    if app_database_url:
        join_token = _clean(payload.get("join_token"))
        join_token_digest = _clean(payload.get("join_token_sha256"))
        if not join_token and not join_token_digest:
            raise RuntimeError("worker join marker does not contain a join token digest")
        try:
            from services.cluster_join_store import ClusterJoinStore

            store = ClusterJoinStore(app_database_url)
            if join_token:
                joined = store.validate_joined_worker(
                    token=join_token,
                    worker_id=worker_id,
                    wireguard_ip=wireguard_ip,
                    cluster_id=resolved_settings.cluster_id,
                    marker_status=marker_status,
                )
            else:
                joined = store.validate_joined_worker_token_digest(
                    token_digest=join_token_digest,
                    worker_id=worker_id,
                    wireguard_ip=wireguard_ip,
                    cluster_id=resolved_settings.cluster_id,
                    marker_status=marker_status,
                )
            if joined is None and marker_status == WORKER_JOIN_STATUS_JOINED and join_token_digest:
                joined = store.activate_worker_join_by_token_digest(
                    token_digest=join_token_digest,
                    worker_id=worker_id,
                    wireguard_ip=wireguard_ip,
                    cluster_id=resolved_settings.cluster_id,
                )
            if (
                joined is not None
                and marker_status == WORKER_JOIN_STATUS_ACTIVATING
                and str(joined.get("status", "")).strip().lower() == WORKER_JOIN_STATUS_JOINED
            ):
                _promote_worker_join_marker(marker, payload)
                marker_status = WORKER_JOIN_STATUS_JOINED
        except Exception as exc:
            raise RuntimeError("worker join status cannot be verified against app database") from exc
        if joined is None:
            if marker_status == WORKER_JOIN_STATUS_ACTIVATING and activation_expired:
                raise RuntimeError("worker join marker activation window expired")
            raise RuntimeError("worker join status is not active in app database")
    elif activation_expired:
        raise RuntimeError("worker join marker activation window expired")
    return marker, marker_status


def ensure_worker_joined_marker(
    settings: ClusterSettings | None = None,
    *,
    marker_file: str | os.PathLike[str] | None = None,
    allow_activating: bool = True,
) -> Path:
    marker, _status = _validate_worker_join_marker(
        settings,
        marker_file=marker_file,
        allow_activating=allow_activating,
    )
    return marker


def worker_join_marker_status(
    settings: ClusterSettings | None = None,
    *,
    marker_file: str | os.PathLike[str] | None = None,
    allow_activating: bool = True,
) -> str:
    _marker, status = _validate_worker_join_marker(
        settings,
        marker_file=marker_file,
        allow_activating=allow_activating,
    )
    return status
