from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from typing import Any, Mapping

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from services.database_url import (
    APP_DATABASE_NAME,
    APP_DATABASE_ROLE,
    IMAGE_QUEUE_DATABASE_NAME,
    ensure_database_role_marker,
    is_postgres_url,
    validate_named_postgres_database,
)


Base = declarative_base()


class WorkerJoinTokenModel(Base):
    __tablename__ = "chatgpt2api_worker_join_token"

    token = Column(String(128), primary_key=True)
    cluster_id = Column(String(128), nullable=False, index=True)
    nonce = Column(String(128), nullable=False, unique=True, index=True)
    worker_id = Column(String(64), nullable=False, unique=True, index=True)
    worker_no = Column(Integer, nullable=False, unique=True, index=True)
    wireguard_ip = Column(String(64), nullable=False, unique=True, index=True)
    wireguard_server_ip = Column(String(64), nullable=False)
    wireguard_server_endpoint = Column(Text, nullable=False)
    wireguard_port = Column(Integer, nullable=False)
    wireguard_server_public_key = Column(Text, nullable=False)
    wireguard_worker_private_key = Column(Text, nullable=False)
    wireguard_worker_public_key = Column(Text, nullable=False)
    app_database_url = Column(Text, nullable=False)
    image_queue_database_url = Column(Text, nullable=False)
    signing_public_key_b64 = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    joined_at = Column(DateTime(timezone=True), nullable=True)


class ClusterJoinStore:
    ACTIVATION_STATUSES = frozenset({"activating", "joined"})

    def __init__(self, database_url: str) -> None:
        normalized_database_url = str(database_url or "").strip()
        postgres = is_postgres_url(normalized_database_url)
        if postgres:
            normalized_database_url = validate_named_postgres_database(
                normalized_database_url,
                APP_DATABASE_NAME,
                role="app",
            )
        self.database_url = normalized_database_url
        self.engine = create_engine(self.database_url, pool_pre_ping=True, pool_recycle=3600)
        with self.engine.begin() as connection:
            ensure_database_role_marker(
                connection,
                APP_DATABASE_ROLE,
                create_if_missing=True,
            )
            Base.metadata.create_all(connection)
            self._ensure_schema_columns(connection)
        self.Session = sessionmaker(bind=self.engine)
        self._scrub_worker_private_key_storage()

    @staticmethod
    def _ensure_schema_columns(connection: Any) -> None:
        """Add current join-file identity columns for tables created by older builds."""
        existing = {
            column["name"]
            for column in inspect(connection).get_columns(WorkerJoinTokenModel.__tablename__)
        }
        additions = {
            "cluster_id": "ALTER TABLE chatgpt2api_worker_join_token ADD COLUMN cluster_id VARCHAR(128) DEFAULT ''",
            "nonce": "ALTER TABLE chatgpt2api_worker_join_token ADD COLUMN nonce VARCHAR(128) DEFAULT ''",
        }
        for name, statement in additions.items():
            if name not in existing:
                connection.execute(text(statement))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_datetime(value: object) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        text = str(value or "").strip()
        if not text:
            raise ValueError("expires_at is required")
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _private_key_digest(value: object) -> str:
        return sha256(str(value or "").strip().encode("utf-8")).hexdigest()

    @staticmethod
    def _is_sha256_hex(value: object) -> bool:
        text = str(value or "").strip().lower()
        return len(text) == 64 and all(character in "0123456789abcdef" for character in text)

    @staticmethod
    def _database_url_digest(value: object, expected_name: str, role: str) -> str:
        normalized = validate_named_postgres_database(
            str(value or "").strip(),
            expected_name,
            role=role,
        )
        return sha256(normalized.encode("utf-8")).hexdigest()

    def _scrub_worker_private_key_storage(self) -> None:
        """Convert legacy raw private keys and database URLs to non-reversible digests."""
        with self.Session() as session:
            changed = False
            for row in session.execute(select(WorkerJoinTokenModel)).scalars():
                stored = str(row.wireguard_worker_private_key or "").strip()
                if stored and not self._is_sha256_hex(stored):
                    row.wireguard_worker_private_key = self._private_key_digest(stored)
                    changed = True
                for attribute, expected_name, role in (
                    ("app_database_url", APP_DATABASE_NAME, "app"),
                    ("image_queue_database_url", IMAGE_QUEUE_DATABASE_NAME, "image_queue"),
                ):
                    raw_url = str(getattr(row, attribute) or "").strip()
                    if not raw_url or self._is_sha256_hex(raw_url):
                        continue
                    try:
                        digest = self._database_url_digest(raw_url, expected_name, role)
                    except ValueError:
                        digest = sha256(raw_url.encode("utf-8")).hexdigest()
                    setattr(row, attribute, digest)
                    changed = True
            if changed:
                session.commit()

    @staticmethod
    def _normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "token",
            "cluster_id",
            "nonce",
            "worker_id",
            "worker_no",
            "wireguard_ip",
            "wireguard_server_ip",
            "wireguard_server_endpoint",
            "wireguard_port",
            "wireguard_server_public_key",
            "wireguard_worker_private_key",
            "wireguard_worker_public_key",
            "app_database_url",
            "image_queue_database_url",
            "signing_public_key_b64",
            "expires_at",
        }
        missing = sorted(key for key in required if key not in payload)
        if missing:
            raise ValueError(f"missing join payload fields: {', '.join(missing)}")
        normalized = {key: payload[key] for key in required}
        normalized["token"] = str(normalized["token"]).strip()
        normalized["cluster_id"] = str(normalized["cluster_id"]).strip()
        normalized["nonce"] = str(normalized["nonce"]).strip()
        normalized["worker_id"] = str(normalized["worker_id"]).strip()
        normalized["worker_no"] = int(normalized["worker_no"])
        normalized["wireguard_ip"] = str(normalized["wireguard_ip"]).strip()
        normalized["wireguard_server_ip"] = str(normalized["wireguard_server_ip"]).strip()
        normalized["wireguard_server_endpoint"] = str(normalized["wireguard_server_endpoint"]).strip()
        normalized["wireguard_port"] = int(normalized["wireguard_port"])
        normalized["wireguard_server_public_key"] = str(normalized["wireguard_server_public_key"]).strip()
        normalized["wireguard_worker_private_key"] = str(normalized["wireguard_worker_private_key"]).strip()
        normalized["wireguard_worker_public_key"] = str(normalized["wireguard_worker_public_key"]).strip()
        normalized["app_database_url"] = str(normalized["app_database_url"]).strip()
        normalized["image_queue_database_url"] = str(normalized["image_queue_database_url"]).strip()
        normalized["signing_public_key_b64"] = str(normalized["signing_public_key_b64"]).strip()
        normalized["expires_at"] = ClusterJoinStore._to_datetime(normalized["expires_at"])
        if not normalized["token"]:
            raise ValueError("join token is required")
        if not normalized["cluster_id"]:
            raise ValueError("cluster_id is required")
        if not normalized["nonce"]:
            raise ValueError("nonce is required")
        if not normalized["worker_id"]:
            raise ValueError("worker_id is required")
        if normalized["worker_no"] <= 0:
            raise ValueError("worker_no must be positive")
        if not normalized["wireguard_ip"]:
            raise ValueError("wireguard_ip is required")
        if not normalized["wireguard_server_ip"]:
            raise ValueError("wireguard_server_ip is required")
        if not normalized["wireguard_server_endpoint"]:
            raise ValueError("wireguard_server_endpoint is required")
        if not 1 <= normalized["wireguard_port"] <= 65535:
            raise ValueError("wireguard_port is out of range")
        for name in (
            "token",
            "cluster_id",
            "nonce",
            "worker_id",
            "wireguard_ip",
            "wireguard_server_ip",
            "wireguard_server_endpoint",
            "wireguard_server_public_key",
            "wireguard_worker_private_key",
            "wireguard_worker_public_key",
            "signing_public_key_b64",
        ):
            if len(normalized[name]) > 2048:
                raise ValueError(f"{name} is too long")
        normalized["app_database_url"] = validate_named_postgres_database(
            normalized["app_database_url"],
            APP_DATABASE_NAME,
            role="app",
        )
        normalized["image_queue_database_url"] = validate_named_postgres_database(
            normalized["image_queue_database_url"],
            IMAGE_QUEUE_DATABASE_NAME,
            role="image_queue",
        )
        return normalized

    def issue_worker_join(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_payload(payload)
        with self.Session() as session:
            existing = session.execute(
                select(WorkerJoinTokenModel).where(
                    (WorkerJoinTokenModel.token == normalized["token"])
                    | (WorkerJoinTokenModel.nonce == normalized["nonce"])
                    | (WorkerJoinTokenModel.worker_id == normalized["worker_id"])
                    | (WorkerJoinTokenModel.worker_no == normalized["worker_no"])
                    | (WorkerJoinTokenModel.wireguard_ip == normalized["wireguard_ip"])
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.worker_id == normalized["worker_id"]:
                    raise ValueError(f"worker_id already exists: {normalized['worker_id']}")
                if existing.nonce == normalized["nonce"]:
                    raise ValueError("worker join nonce already exists")
                if existing.worker_no == normalized["worker_no"]:
                    raise ValueError(f"worker number already exists: {normalized['worker_no']}")
                if existing.wireguard_ip == normalized["wireguard_ip"]:
                    raise ValueError(f"wireguard IP already exists: {normalized['wireguard_ip']}")
                raise ValueError(f"worker join token already exists: {normalized['token']}")
            now = self._now()
            row = WorkerJoinTokenModel(
                token=normalized["token"],
                cluster_id=normalized["cluster_id"],
                nonce=normalized["nonce"],
                worker_id=normalized["worker_id"],
                worker_no=normalized["worker_no"],
                wireguard_ip=normalized["wireguard_ip"],
                wireguard_server_ip=normalized["wireguard_server_ip"],
                wireguard_server_endpoint=normalized["wireguard_server_endpoint"],
                wireguard_port=normalized["wireguard_port"],
                wireguard_server_public_key=normalized["wireguard_server_public_key"],
                wireguard_worker_private_key=self._private_key_digest(
                    normalized["wireguard_worker_private_key"]
                ),
                wireguard_worker_public_key=normalized["wireguard_worker_public_key"],
                app_database_url=self._database_url_digest(
                    normalized["app_database_url"],
                    APP_DATABASE_NAME,
                    "app",
                ),
                image_queue_database_url=self._database_url_digest(
                    normalized["image_queue_database_url"],
                    IMAGE_QUEUE_DATABASE_NAME,
                    "image_queue",
                ),
                signing_public_key_b64=normalized["signing_public_key_b64"],
                expires_at=normalized["expires_at"],
                status="pending",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.execute(
                    select(WorkerJoinTokenModel).where(
                        (WorkerJoinTokenModel.token == normalized["token"])
                        | (WorkerJoinTokenModel.nonce == normalized["nonce"])
                        | (WorkerJoinTokenModel.worker_id == normalized["worker_id"])
                        | (WorkerJoinTokenModel.worker_no == normalized["worker_no"])
                        | (WorkerJoinTokenModel.wireguard_ip == normalized["wireguard_ip"])
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.worker_id == normalized["worker_id"]:
                        raise ValueError(f"worker_id already exists: {normalized['worker_id']}") from exc
                    if existing.nonce == normalized["nonce"]:
                        raise ValueError("worker join nonce already exists") from exc
                    if existing.worker_no == normalized["worker_no"]:
                        raise ValueError(f"worker number already exists: {normalized['worker_no']}") from exc
                    if existing.wireguard_ip == normalized["wireguard_ip"]:
                        raise ValueError(f"wireguard IP already exists: {normalized['wireguard_ip']}") from exc
                    raise ValueError(f"worker join token already exists: {normalized['token']}") from exc
                raise
            return self.load_worker_join(normalized["token"]) or {}

    def load_worker_join(self, token: object) -> dict[str, Any] | None:
        token_text = str(token or "").strip()
        if not token_text:
            return None
        with self.Session() as session:
            row = session.get(WorkerJoinTokenModel, token_text)
            if row is None:
                return None
            return self._row_to_dict(row)

    def consume_worker_join(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_payload(payload)
        now = self._now()
        with self.Session() as session:
            with session.begin():
                row = session.get(WorkerJoinTokenModel, normalized["token"], with_for_update=True)
                if row is None:
                    return None
                if row.status == "activating" and self._matches(row, normalized):
                    if self._to_datetime(row.expires_at) <= now:
                        return None
                    return self._row_to_dict(row)
                if row.status == "joined" and self._matches(row, normalized):
                    return self._row_to_dict(row)
                if row.status != "pending":
                    return None
                if self._to_datetime(row.expires_at) <= now:
                    return None
                if not self._matches(row, normalized):
                    return None
                row.status = "activating"
                row.joined_at = now
                row.updated_at = now
                return self._row_to_dict(row)

    def validate_worker_join(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_payload(payload)
        now = self._now()
        with self.Session() as session:
            row = session.get(WorkerJoinTokenModel, normalized["token"])
            if row is None:
                return None
            if row.status not in {"pending", "activating", "joined"}:
                return None
            if row.status != "joined" and self._to_datetime(row.expires_at) <= now:
                return None
            if not self._matches(row, normalized):
                return None
            return self._row_to_dict(row)

    def _transition_activation(
        self,
        payload: Mapping[str, Any],
        *,
        from_status: str,
        to_status: str,
        require_unexpired: bool,
    ) -> dict[str, Any] | None:
        normalized = self._normalize_payload(payload)
        now = self._now()
        with self.Session() as session:
            with session.begin():
                row = session.get(WorkerJoinTokenModel, normalized["token"], with_for_update=True)
                if row is None or row.status != from_status:
                    return None
                if require_unexpired and self._to_datetime(row.expires_at) <= now:
                    return None
                if not self._matches(row, normalized):
                    return None
                row.status = to_status
                row.updated_at = now
                return self._row_to_dict(row)

    def activate_worker_join(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        activated = self._transition_activation(
            payload,
            from_status="activating",
            to_status="joined",
            require_unexpired=True,
        )
        if activated is not None:
            return activated
        normalized = self._normalize_payload(payload)
        with self.Session() as session:
            row = session.get(WorkerJoinTokenModel, normalized["token"])
            if row is not None and row.status == "joined" and self._matches(row, normalized):
                return self._row_to_dict(row)
        return None

    def activate_worker_join_by_token_digest(
        self,
        *,
        token_digest: object,
        worker_id: object,
        wireguard_ip: object,
        cluster_id: object = "",
    ) -> dict[str, Any] | None:
        token_digest_text = str(token_digest or "").strip().lower()
        worker_id_text = str(worker_id or "").strip()
        wireguard_ip_text = str(wireguard_ip or "").strip()
        cluster_id_text = str(cluster_id or "").strip()
        if not self._is_sha256_hex(token_digest_text) or not worker_id_text or not wireguard_ip_text:
            return None
        now = self._now()
        with self.Session() as session:
            with session.begin():
                query = select(WorkerJoinTokenModel).where(
                    WorkerJoinTokenModel.worker_id == worker_id_text,
                    WorkerJoinTokenModel.wireguard_ip == wireguard_ip_text,
                    WorkerJoinTokenModel.status.in_(self.ACTIVATION_STATUSES),
                )
                if cluster_id_text:
                    query = query.where(WorkerJoinTokenModel.cluster_id == cluster_id_text)
                rows = session.execute(query.with_for_update()).scalars().all()
                for row in rows:
                    actual_digest = sha256(str(row.token or "").encode("utf-8")).hexdigest()
                    if not compare_digest(actual_digest, token_digest_text):
                        continue
                    if row.status == "joined":
                        return self._row_to_dict(row)
                    if self._to_datetime(row.expires_at) <= now:
                        return None
                    row.status = "joined"
                    row.updated_at = now
                    return self._row_to_dict(row)
        return None

    def reopen_worker_join(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_payload(payload)
        now = self._now()
        with self.Session() as session:
            with session.begin():
                row = session.get(WorkerJoinTokenModel, normalized["token"], with_for_update=True)
                if row is None or row.status != "activating":
                    return None
                if not self._matches(row, normalized):
                    return None
                row.status = "pending"
                row.joined_at = None
                row.updated_at = now
                return self._row_to_dict(row)

    def mark_activation_failed(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        normalized = self._normalize_payload(payload)
        now = self._now()
        with self.Session() as session:
            with session.begin():
                row = session.get(WorkerJoinTokenModel, normalized["token"], with_for_update=True)
                if row is None or row.status not in {"activating", "joined"}:
                    return None
                if not self._matches(row, normalized):
                    return None
                row.status = "activation_failed"
                row.updated_at = now
                return self._row_to_dict(row)

    def _runtime_activation_is_valid(
        self,
        row: WorkerJoinTokenModel,
        *,
        marker_status: object = "",
    ) -> bool:
        status = str(row.status or "").strip()
        marker_status_text = str(marker_status or "").strip().lower()
        if marker_status_text == "joined":
            return status == "joined"
        if status == "joined":
            return True
        if status != "activating":
            return False
        try:
            return self._to_datetime(row.expires_at) > self._now()
        except (TypeError, ValueError):
            return False

    def validate_joined_worker(
        self,
        *,
        token: object,
        worker_id: object,
        wireguard_ip: object,
        cluster_id: object = "",
        marker_status: object = "",
    ) -> dict[str, Any] | None:
        token_text = str(token or "").strip()
        worker_id_text = str(worker_id or "").strip()
        wireguard_ip_text = str(wireguard_ip or "").strip()
        cluster_id_text = str(cluster_id or "").strip()
        if not token_text or not worker_id_text or not wireguard_ip_text:
            return None
        with self.Session() as session:
            row = session.get(WorkerJoinTokenModel, token_text)
            if (
                row is None
                or not self._runtime_activation_is_valid(row, marker_status=marker_status)
                or row.worker_id != worker_id_text
                or row.wireguard_ip != wireguard_ip_text
                or (cluster_id_text and row.cluster_id != cluster_id_text)
            ):
                return None
            return self._row_to_dict(row)

    def validate_joined_worker_token_digest(
        self,
        *,
        token_digest: object,
        worker_id: object,
        wireguard_ip: object,
        cluster_id: object = "",
        marker_status: object = "",
    ) -> dict[str, Any] | None:
        token_digest_text = str(token_digest or "").strip().lower()
        worker_id_text = str(worker_id or "").strip()
        wireguard_ip_text = str(wireguard_ip or "").strip()
        cluster_id_text = str(cluster_id or "").strip()
        if len(token_digest_text) != 64 or not worker_id_text or not wireguard_ip_text:
            return None
        with self.Session() as session:
            query = select(WorkerJoinTokenModel).where(
                WorkerJoinTokenModel.worker_id == worker_id_text,
                WorkerJoinTokenModel.wireguard_ip == wireguard_ip_text,
                WorkerJoinTokenModel.status.in_(self.ACTIVATION_STATUSES),
            )
            if cluster_id_text:
                query = query.where(WorkerJoinTokenModel.cluster_id == cluster_id_text)
            rows = session.execute(query).scalars().all()
            for row in rows:
                if not self._runtime_activation_is_valid(row, marker_status=marker_status):
                    continue
                actual_digest = sha256(str(row.token or "").encode("utf-8")).hexdigest()
                if compare_digest(actual_digest, token_digest_text):
                    return self._row_to_dict(row)
        return None

    def revoke_worker_join(self, token: object) -> bool:
        token_text = str(token or "").strip()
        if not token_text:
            return False
        with self.Session() as session:
            with session.begin():
                row = session.get(WorkerJoinTokenModel, token_text, with_for_update=True)
                if row is None or row.status != "pending":
                    return False
                session.delete(row)
                return True

    def revoke_pending_worker(self, worker_id: object) -> dict[str, Any] | None:
        worker_id_text = str(worker_id or "").strip()
        if not worker_id_text:
            raise ValueError("worker_id is required")
        with self.Session() as session:
            with session.begin():
                row = session.execute(
                    select(WorkerJoinTokenModel)
                    .where(WorkerJoinTokenModel.worker_id == worker_id_text)
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    return None
                if row.status not in {"pending", "activation_failed", "joined"}:
                    raise ValueError(
                        f"worker {worker_id_text} is {row.status}; only pending, activation_failed, or joined joins can be rotated"
                    )
                payload = self._row_to_dict(row)
                session.delete(row)
                return payload

    def list_worker_joins(self) -> list[dict[str, Any]]:
        with self.Session() as session:
            rows = session.execute(
                select(WorkerJoinTokenModel).order_by(
                    WorkerJoinTokenModel.worker_no,
                    WorkerJoinTokenModel.worker_id,
                )
            ).scalars().all()
            return [
                {
                    "worker_id": row.worker_id,
                    "worker_no": int(row.worker_no),
                    "wireguard_ip": row.wireguard_ip,
                    "status": row.status,
                    "expires_at": row.expires_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "joined_at": row.joined_at,
                }
                for row in rows
            ]

    @staticmethod
    def _matches(row: WorkerJoinTokenModel, payload: Mapping[str, Any]) -> bool:
        expected = {
            "worker_id": row.worker_id,
            "cluster_id": row.cluster_id,
            "nonce": row.nonce,
            "worker_no": row.worker_no,
            "wireguard_ip": row.wireguard_ip,
            "wireguard_server_ip": row.wireguard_server_ip,
            "wireguard_server_endpoint": row.wireguard_server_endpoint,
            "wireguard_port": row.wireguard_port,
            "wireguard_server_public_key": row.wireguard_server_public_key,
            "wireguard_worker_private_key": row.wireguard_worker_private_key,
            "wireguard_worker_public_key": row.wireguard_worker_public_key,
            "app_database_url": row.app_database_url,
            "image_queue_database_url": row.image_queue_database_url,
            "signing_public_key_b64": row.signing_public_key_b64,
        }
        for key, expected_value in expected.items():
            actual_value = payload.get(key)
            if key == "wireguard_worker_private_key":
                actual_value = sha256(
                    str(actual_value or "").strip().encode("utf-8")
                ).hexdigest()
            elif key == "app_database_url":
                actual_value = ClusterJoinStore._database_url_digest(
                    actual_value,
                    APP_DATABASE_NAME,
                    "app",
                )
            elif key == "image_queue_database_url":
                actual_value = ClusterJoinStore._database_url_digest(
                    actual_value,
                    IMAGE_QUEUE_DATABASE_NAME,
                    "image_queue",
                )
            if str(actual_value) != str(expected_value):
                return False
        return True

    @staticmethod
    def _row_to_dict(row: WorkerJoinTokenModel) -> dict[str, Any]:
        return {
            "token": row.token,
            "worker_id": row.worker_id,
            "cluster_id": row.cluster_id,
            "nonce": row.nonce,
            "worker_no": row.worker_no,
            "wireguard_ip": row.wireguard_ip,
            "wireguard_server_ip": row.wireguard_server_ip,
            "wireguard_server_endpoint": row.wireguard_server_endpoint,
            "wireguard_port": row.wireguard_port,
            "wireguard_server_public_key": row.wireguard_server_public_key,
            "wireguard_worker_public_key": row.wireguard_worker_public_key,
            "app_database_url_sha256": row.app_database_url,
            "image_queue_database_url_sha256": row.image_queue_database_url,
            "signing_public_key_b64": row.signing_public_key_b64,
            "expires_at": row.expires_at,
            "status": row.status,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "joined_at": row.joined_at,
        }
