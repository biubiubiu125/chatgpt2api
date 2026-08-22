from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from services.config import DATA_DIR, config
from services.file_lock import file_lock
from services.json_file import read_json_object, write_json_file

QUOTA_SCOPES = {"fast", "thinking", "pro", "image", "music", "video"}
QUOTA_LIMIT_KEYS = {
    "fast": "fast_daily_limit",
    "thinking": "thinking_daily_limit",
    "pro": "pro_daily_limit",
    "image": "image_daily_limit",
    "music": "music_daily_limit",
    "video": "video_daily_limit",
}
QUOTA_USAGE_PATH = DATA_DIR / "quota_usage.json"
QUOTA_RESERVATION_TTL_SECONDS = 15 * 60
QUOTA_RESERVATION_PROTOCOL_GRACE_SECONDS = 60


class QuotaExceededError(RuntimeError):
    def __init__(self, scope: str, limit: int) -> None:
        self.scope = scope
        self.limit = limit
        super().__init__(f"{scope} daily quota exceeded")


@dataclass
class QuotaReservation:
    service: "QuotaService"
    today: str
    reservation_id: str
    identity_id: str = ""
    scope: str = ""
    dedupe_key: str = ""
    committed: bool = False
    canceled: bool = False

    def commit(self) -> None:
        if self.committed or self.canceled or not self.reservation_id:
            return
        self.service.commit_reservation(
            self.today,
            self.reservation_id,
            identity_id=self.identity_id,
            scope=self.scope,
            dedupe_key=self.dedupe_key,
        )
        self.committed = True

    def cancel(self) -> None:
        if self.committed or self.canceled or not self.reservation_id:
            return
        self.service.cancel_reservation(self.today, self.reservation_id)
        self.canceled = True


def _today_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _identity_id(identity: dict[str, object]) -> str:
    for key in ("id", "name", "role"):
        value = str(identity.get(key) or "").strip()
        if value:
            return value[:160]
    return "anonymous"


def _normalize_scope(scope: str) -> str:
    value = str(scope or "").strip().lower()
    return value if value in QUOTA_SCOPES else "fast"


def _positive_env_seconds(name: str) -> int:
    try:
        value = int(float(os.getenv(name, "0") or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _reservation_ttl_seconds() -> int:
    protocol_wait = _positive_env_seconds("IMAGE_QUEUE_PROTOCOL_WAIT_TIMEOUT_SECONDS")
    configured_ttl = _positive_env_seconds("CHATGPT2API_QUOTA_RESERVATION_TTL_SECONDS")
    return max(
        QUOTA_RESERVATION_TTL_SECONDS,
        configured_ttl,
        protocol_wait + QUOTA_RESERVATION_PROTOCOL_GRACE_SECONDS if protocol_wait else 0,
    )


def quota_scope_for_request(endpoint: str, model: str, *, image_request: bool = False) -> str:
    lowered_endpoint = str(endpoint or "").lower()
    lowered_model = str(model or "").lower()
    if image_request or "image" in lowered_endpoint or "/ppt/" in lowered_endpoint or "/psd/" in lowered_endpoint:
        return "image"
    if "music" in lowered_endpoint or "music" in lowered_model or "audio" in lowered_model:
        return "music"
    if "video" in lowered_endpoint or "video" in lowered_model:
        return "video"
    if (
        "thinking" in lowered_model
        or "reasoning" in lowered_model
        or re.search(r"\bo[13](?:[-_]|$)", lowered_model)
    ):
        return "thinking"
    if "pro" in lowered_model:
        return "pro"
    return "fast"


class QuotaService:
    def __init__(
        self,
        path: Path = QUOTA_USAGE_PATH,
        *,
        settings_provider: Callable[[], dict[str, object]] | None = None,
        today_provider: Callable[[], str] | None = None,
    ) -> None:
        self.path = path
        self.settings_provider = settings_provider or config.get_quota_limits_settings
        self.today_provider = today_provider or _today_beijing

    def consume(
        self,
        identity: dict[str, object],
        scope: str,
        *,
        idempotency_key: str = "",
    ) -> None:
        settings = self.settings_provider()
        if not settings.get("enabled", True):
            return
        normalized_scope = _normalize_scope(scope)
        limit = self._limit(settings, normalized_scope)
        if limit < 0:
            return
        identity_id = _identity_id(identity)
        today = str(self.today_provider()).strip()
        dedupe_key = self._dedupe_key(identity_id, normalized_scope, idempotency_key)

        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            state = self._load(today)
            idempotency = state.setdefault("idempotency", {})
            if dedupe_key and idempotency.get(dedupe_key):
                return
            usage = state.setdefault("usage", {})
            user_usage = usage.setdefault(identity_id, {})
            current = max(0, int(user_usage.get(normalized_scope) or 0))
            if current >= limit:
                raise QuotaExceededError(normalized_scope, limit)
            user_usage[normalized_scope] = current + 1
            if dedupe_key:
                idempotency[dedupe_key] = True
            write_json_file(self.path, state)

    def reserve(
        self,
        identity: dict[str, object],
        scope: str,
        *,
        idempotency_key: str = "",
    ) -> QuotaReservation:
        settings = self.settings_provider()
        if not settings.get("enabled", True):
            return QuotaReservation(self, "", "")
        normalized_scope = _normalize_scope(scope)
        limit = self._limit(settings, normalized_scope)
        if limit < 0:
            return QuotaReservation(self, "", "")
        identity_id = _identity_id(identity)
        today = str(self.today_provider()).strip()
        dedupe_key = self._dedupe_key(identity_id, normalized_scope, idempotency_key)
        reservation_id = uuid4().hex

        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            state = self._load(today)
            idempotency = state.setdefault("idempotency", {})
            if dedupe_key and idempotency.get(dedupe_key):
                return QuotaReservation(self, today, "")
            reservations = self._active_reservations(state)
            if dedupe_key:
                for active_reservation_id, item in reservations.items():
                    if item.get("dedupe_key") == dedupe_key:
                        return QuotaReservation(
                            self,
                            today,
                            active_reservation_id,
                            identity_id=identity_id,
                            scope=normalized_scope,
                            dedupe_key=dedupe_key,
                        )
            usage = state.setdefault("usage", {})
            user_usage = usage.setdefault(identity_id, {})
            current = max(0, int(user_usage.get(normalized_scope) or 0))
            pending = sum(
                1
                for item in reservations.values()
                if item.get("identity_id") == identity_id and item.get("scope") == normalized_scope
            )
            if current + pending >= limit:
                raise QuotaExceededError(normalized_scope, limit)
            reservations[reservation_id] = {
                "identity_id": identity_id,
                "scope": normalized_scope,
                "dedupe_key": dedupe_key,
                "created_at": time.time(),
            }
            state["reservations"] = reservations
            write_json_file(self.path, state)
        return QuotaReservation(
            self,
            today,
            reservation_id,
            identity_id=identity_id,
            scope=normalized_scope,
            dedupe_key=dedupe_key,
        )

    def commit_reservation(
        self,
        today: str,
        reservation_id: str,
        *,
        identity_id: str = "",
        scope: str = "",
        dedupe_key: str = "",
    ) -> None:
        if not reservation_id:
            return
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            state = self._load(today)
            reservations = self._active_reservations(state)
            reservation = reservations.pop(reservation_id, None)
            if not isinstance(reservation, dict):
                if identity_id:
                    normalized_scope = _normalize_scope(scope)
                    idempotency = state.setdefault("idempotency", {})
                    if not (dedupe_key and idempotency.get(dedupe_key)):
                        usage = state.setdefault("usage", {})
                        user_usage = usage.setdefault(identity_id, {})
                        current = max(0, int(user_usage.get(normalized_scope) or 0))
                        user_usage[normalized_scope] = current + 1
                        if dedupe_key:
                            idempotency[dedupe_key] = True
                state["reservations"] = reservations
                write_json_file(self.path, state)
                return
            identity_id = str(reservation.get("identity_id") or "")
            scope = _normalize_scope(str(reservation.get("scope") or ""))
            dedupe_key = str(reservation.get("dedupe_key") or "")
            idempotency = state.setdefault("idempotency", {})
            if not (dedupe_key and idempotency.get(dedupe_key)):
                usage = state.setdefault("usage", {})
                user_usage = usage.setdefault(identity_id, {})
                current = max(0, int(user_usage.get(scope) or 0))
                user_usage[scope] = current + 1
                if dedupe_key:
                    idempotency[dedupe_key] = True
            state["reservations"] = reservations
            write_json_file(self.path, state)

    def cancel_reservation(self, today: str, reservation_id: str) -> None:
        if not reservation_id:
            return
        with file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            state = self._load(today)
            reservations = self._active_reservations(state)
            if reservation_id in reservations:
                reservations.pop(reservation_id, None)
                state["reservations"] = reservations
                write_json_file(self.path, state)

    @staticmethod
    def _limit(settings: dict[str, object], scope: str) -> int:
        try:
            return int(settings.get(QUOTA_LIMIT_KEYS[scope]))
        except (OverflowError, TypeError, ValueError):
            return -1

    @staticmethod
    def _dedupe_key(identity_id: str, scope: str, idempotency_key: str) -> str:
        key = str(idempotency_key or "").strip()
        if not key:
            return ""
        return f"{identity_id}|{scope}|{key[:200]}"

    @staticmethod
    def _active_reservations(state: dict[str, object]) -> dict[str, dict[str, object]]:
        raw = state.get("reservations")
        if not isinstance(raw, dict):
            return {}
        cutoff = time.time() - _reservation_ttl_seconds()
        active: dict[str, dict[str, object]] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            try:
                created_at = float(value.get("created_at") or 0)
            except (TypeError, ValueError):
                created_at = 0
            if created_at < cutoff:
                continue
            active[str(key)] = value
        return active

    def _load(self, today: str) -> dict[str, object]:
        state = read_json_object(self.path, name=self.path.name)
        if state.get("date") != today:
            return {"date": today, "usage": {}, "idempotency": {}, "reservations": {}}
        usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
        idempotency = state.get("idempotency") if isinstance(state.get("idempotency"), dict) else {}
        reservations = self._active_reservations(state)
        return {"date": today, "usage": usage, "idempotency": idempotency, "reservations": reservations}


quota_service = QuotaService()


def enforce_quota(
    identity: dict[str, object],
    endpoint: str,
    model: str,
    *,
    image_request: bool = False,
    idempotency_key: str = "",
) -> None:
    scope = quota_scope_for_request(endpoint, model, image_request=image_request)
    try:
        quota_service.consume(identity, scope, idempotency_key=idempotency_key)
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "scope": exc.scope,
                "limit": exc.limit,
            },
        ) from exc


def reserve_quota(
    identity: dict[str, object],
    endpoint: str,
    model: str,
    *,
    image_request: bool = False,
    idempotency_key: str = "",
) -> QuotaReservation:
    scope = quota_scope_for_request(endpoint, model, image_request=image_request)
    try:
        return quota_service.reserve(identity, scope, idempotency_key=idempotency_key)
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "scope": exc.scope,
                "limit": exc.limit,
            },
        ) from exc
