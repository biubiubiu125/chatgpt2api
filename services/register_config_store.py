from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import Column, DateTime, String, Text, create_engine, select, text
from sqlalchemy.orm import declarative_base, sessionmaker

from services.database_url import (
    APP_DATABASE_NAME,
    APP_DATABASE_ROLE,
    ensure_database_role_marker,
    is_postgres_url,
    select_named_postgres_database,
    validate_named_postgres_database,
)
from services.json_file import read_json_object, write_json_file


Base = declarative_base()


class RegisterConfigModel(Base):
    __tablename__ = "register_config"

    key = Column(String(64), primary_key=True)
    data = Column(Text, nullable=False)


class RegisterRuntimeLeaseModel(Base):
    __tablename__ = "register_runtime_lease"

    key = Column(String(64), primary_key=True)
    owner_id = Column(String(128), nullable=False, default="")
    run_id = Column(String(128), nullable=False, default="")
    state = Column(String(32), nullable=False, default="idle")
    heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    lease_expires_at = Column(DateTime(timezone=True), nullable=False)
    stop_requested_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class FileRegisterConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        data = read_json_object(self.path, name="register.json")
        runtime = data.get("runtime") if isinstance(data, dict) else None
        if isinstance(runtime, dict):
            data["runtime"] = _normalize_runtime(runtime)
        return data

    def save(self, value: dict[str, Any]) -> None:
        write_json_file(self.path, value)

    def update_stats(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        data = self.load()
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        stats.update(dict(updates))
        data["stats"] = stats
        self.save(data)
        return data

    def info(self) -> dict[str, str]:
        return {"type": "file", "path": str(self.path)}

    def load_runtime_lease(self) -> dict[str, Any]:
        data = self.load()
        runtime = data.get("runtime") if isinstance(data, dict) else {}
        return runtime if isinstance(runtime, dict) else {}

    def try_acquire_runtime_lease(
        self,
        owner_id: str,
        run_id: str,
        *,
        state: str = "running",
        lease_seconds: int = 30,
    ) -> bool:
        data = self.load()
        runtime = _normalize_runtime(data.get("runtime") if isinstance(data, dict) else {})
        now = _now()
        active = _lease_active(runtime, now)
        if active and str(runtime.get("owner_id") or "") not in {"", owner_id}:
            return False
        data["runtime"] = _runtime_payload(owner_id, run_id, state=state, lease_seconds=lease_seconds, now=now)
        self.save(data)
        return True

    def touch_runtime_lease(
        self,
        owner_id: str,
        run_id: str,
        *,
        state: str | None = None,
        lease_seconds: int = 30,
    ) -> bool:
        data = self.load()
        runtime = _normalize_runtime(data.get("runtime") if isinstance(data, dict) else {})
        if not runtime or str(runtime.get("owner_id") or "") != owner_id or str(runtime.get("run_id") or "") != run_id:
            return False
        now = _now()
        if not _lease_active(runtime, now):
            return False
        data["runtime"] = _runtime_payload(
            owner_id,
            run_id,
            state=state or str(runtime.get("state") or "running"),
            lease_seconds=lease_seconds,
            now=now,
        )
        self.save(data)
        return True

    def release_runtime_lease(self, owner_id: str, run_id: str) -> bool:
        data = self.load()
        runtime = _normalize_runtime(data.get("runtime") if isinstance(data, dict) else {})
        if not runtime or str(runtime.get("owner_id") or "") != owner_id or str(runtime.get("run_id") or "") != run_id:
            return False
        now = _now()
        data["runtime"] = {
            **runtime,
            "state": "idle",
            "heartbeat_at": now.isoformat(),
            "lease_expires_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self.save(data)
        return True


class DatabaseRegisterConfigStore:
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
        self.Session = sessionmaker(bind=self.engine)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        session = self.Session()
        try:
            row = session.get(RegisterConfigModel, "default")
            data = _config_data_from_row(row)
            runtime_row = session.get(RegisterRuntimeLeaseModel, "default")
            if runtime_row is not None:
                data["runtime"] = _row_to_runtime(runtime_row)
            return data
        finally:
            session.close()

    def save(self, value: dict[str, Any]) -> None:
        with self._lock:
            session = self.Session()
            try:
                with session.begin():
                    row = session.execute(
                        select(RegisterConfigModel)
                        .where(RegisterConfigModel.key == "default")
                        .with_for_update()
                    ).scalar_one_or_none()
                    payload_data = _merge_config_preserving_newer_stats(
                        _config_data_from_row(row),
                        value,
                    )
                    payload = json.dumps(payload_data, ensure_ascii=False)
                    if row is None:
                        session.add(RegisterConfigModel(key="default", data=payload))
                    else:
                        row.data = payload
            except Exception:
                raise
            finally:
                session.close()

    def update_stats(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            session = self.Session()
            try:
                with session.begin():
                    row = session.execute(
                        select(RegisterConfigModel)
                        .where(RegisterConfigModel.key == "default")
                        .with_for_update()
                    ).scalar_one_or_none()
                    data = _config_data_from_row(row)
                    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
                    stats.update(dict(updates))
                    data["stats"] = stats
                    payload = json.dumps(data, ensure_ascii=False)
                    if row is None:
                        session.add(RegisterConfigModel(key="default", data=payload))
                    else:
                        row.data = payload
                    return data
            finally:
                session.close()

    def info(self) -> dict[str, str]:
        return {"type": "database", "database_url": _mask_password(self.database_url)}

    def load_runtime_lease(self) -> dict[str, Any]:
        session = self.Session()
        try:
            row = session.get(RegisterRuntimeLeaseModel, "default")
            return _row_to_runtime(row) if row is not None else {}
        finally:
            session.close()

    def try_acquire_runtime_lease(
        self,
        owner_id: str,
        run_id: str,
        *,
        state: str = "running",
        lease_seconds: int = 30,
    ) -> bool:
        now = _now()
        session = self.Session()
        try:
            with session.begin():
                row = session.execute(
                    select(RegisterRuntimeLeaseModel).where(RegisterRuntimeLeaseModel.key == "default").with_for_update()
                ).scalar_one_or_none()
                if row is not None:
                    active = _lease_active(_row_to_runtime(row), now)
                    if active and str(row.owner_id or "") not in {"", owner_id}:
                        return False
                else:
                    row = RegisterRuntimeLeaseModel(key="default")
                    session.add(row)
                _apply_runtime_lease_row(row, owner_id, run_id, state=state, lease_seconds=lease_seconds, now=now)
                return True
        finally:
            session.close()

    def touch_runtime_lease(
        self,
        owner_id: str,
        run_id: str,
        *,
        state: str | None = None,
        lease_seconds: int = 30,
    ) -> bool:
        now = _now()
        session = self.Session()
        try:
            with session.begin():
                row = session.execute(
                    select(RegisterRuntimeLeaseModel).where(RegisterRuntimeLeaseModel.key == "default").with_for_update()
                ).scalar_one_or_none()
                if row is None or str(row.owner_id or "") != owner_id or str(row.run_id or "") != run_id:
                    return False
                if not _lease_active(_row_to_runtime(row), now):
                    return False
                _apply_runtime_lease_row(row, owner_id, run_id, state=state or str(row.state or "running"), lease_seconds=lease_seconds, now=now)
                return True
        finally:
            session.close()

    def release_runtime_lease(self, owner_id: str, run_id: str) -> bool:
        now = _now()
        session = self.Session()
        try:
            with session.begin():
                row = session.execute(
                    select(RegisterRuntimeLeaseModel).where(RegisterRuntimeLeaseModel.key == "default").with_for_update()
                ).scalar_one_or_none()
                if row is None or str(row.owner_id or "") != owner_id or str(row.run_id or "") != run_id:
                    return False
                _apply_runtime_lease_row(row, owner_id, run_id, state="idle", lease_seconds=0, now=now)
                row.lease_expires_at = now
                row.stop_requested_at = None
                return True
        finally:
            session.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _config_data_from_row(row: RegisterConfigModel | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        data = json.loads(str(row.data or "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("register config database contains invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("register config database must contain a JSON object")
    return data


def _merge_config_preserving_newer_stats(
    existing: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(incoming)
    existing_stats = existing.get("stats") if isinstance(existing.get("stats"), dict) else {}
    incoming_stats = incoming.get("stats") if isinstance(incoming.get("stats"), dict) else {}
    existing_updated_at = _parse_datetime(existing_stats.get("updated_at"))
    incoming_updated_at = _parse_datetime(incoming_stats.get("updated_at"))
    if existing_stats and existing_updated_at and (
        incoming_updated_at is None or existing_updated_at > incoming_updated_at
    ):
        merged["stats"] = dict(existing_stats)
    return merged


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _normalize_runtime(runtime: object) -> dict[str, Any]:
    data = runtime if isinstance(runtime, dict) else {}
    normalized = {
        "owner_id": str(data.get("owner_id") or "").strip(),
        "run_id": str(data.get("run_id") or "").strip(),
        "state": str(data.get("state") or "idle").strip() or "idle",
        "heartbeat_at": _parse_datetime(data.get("heartbeat_at")),
        "lease_expires_at": _parse_datetime(data.get("lease_expires_at")),
        "stop_requested_at": _parse_datetime(data.get("stop_requested_at")),
        "created_at": _parse_datetime(data.get("created_at")),
        "updated_at": _parse_datetime(data.get("updated_at")),
    }
    return {key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in normalized.items() if value is not None or key in {"owner_id", "run_id", "state"}}


def _runtime_payload(
    owner_id: str,
    run_id: str,
    *,
    state: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    lease_expires_at = current + timedelta(seconds=max(1, int(lease_seconds or 0)))
    payload = {
        "owner_id": owner_id,
        "run_id": run_id,
        "state": state,
        "heartbeat_at": current.isoformat(),
        "lease_expires_at": lease_expires_at.isoformat(),
        "updated_at": current.isoformat(),
    }
    if state == "stopping":
        payload["stop_requested_at"] = current.isoformat()
    return payload


def _apply_runtime_lease_row(
    row: RegisterRuntimeLeaseModel,
    owner_id: str,
    run_id: str,
    *,
    state: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    row.owner_id = owner_id
    row.run_id = run_id
    row.state = state
    row.heartbeat_at = current
    row.lease_expires_at = current + timedelta(seconds=max(1, int(lease_seconds or 0)))
    row.updated_at = current
    row.stop_requested_at = current if state == "stopping" else None
    if row.created_at is None:
        row.created_at = current


def _row_to_runtime(row: RegisterRuntimeLeaseModel | None) -> dict[str, Any]:
    if row is None:
        return {}
    runtime = {
        "owner_id": row.owner_id,
        "run_id": row.run_id,
        "state": row.state,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else "",
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else "",
        "stop_requested_at": row.stop_requested_at.isoformat() if row.stop_requested_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }
    return runtime


def _lease_active(runtime: Mapping[str, Any], now: datetime | None = None) -> bool:
    expires_at = _parse_datetime(runtime.get("lease_expires_at")) if isinstance(runtime, dict) else None
    state = str(runtime.get("state") or "") if isinstance(runtime, dict) else ""
    return bool(expires_at and expires_at > (now or _now()) and state in {"running", "stopping"})


def create_register_config_store(path: Path):
    database_url = ""
    if os.getenv("APP_DATABASE_URL", "").strip():
        database_url = select_named_postgres_database(
            dedicated_url=os.getenv("APP_DATABASE_URL"),
            fallback_url="",
            expected_name=APP_DATABASE_NAME,
            role="app",
        )
    elif (
        os.getenv("STORAGE_BACKEND", "").strip().lower() in {"postgres", "postgresql"}
        or is_postgres_url(os.getenv("DATABASE_URL"))
    ):
        database_url = select_named_postgres_database(
            dedicated_url=os.getenv("DATABASE_URL"),
            fallback_url="",
            expected_name=APP_DATABASE_NAME,
            role="app",
        )
    if database_url:
        return DatabaseRegisterConfigStore(database_url)
    return FileRegisterConfigStore(path)


def _mask_password(url: str) -> str:
    if "://" not in url:
        return url
    try:
        protocol, rest = url.split("://", 1)
        if "@" in rest:
            credentials, host = rest.split("@", 1)
            if ":" in credentials:
                username, _ = credentials.split(":", 1)
                return f"{protocol}://{username}:****@{host}"
        return url
    except Exception:
        return url
