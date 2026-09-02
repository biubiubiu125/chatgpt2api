from __future__ import annotations

import json
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import Any
from uuid import UUID, uuid4

from services.config import DATA_DIR, config
from services.file_lock import file_lock
from services.image_failure import ImageFailure, classify_image_exception, image_failure
from services.image_queue.types import ImageAccountCandidate
from services.json_file import read_json_object, write_json_file
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.storage.base import StorageBackend
from utils.helper import anonymize_token

_RemoteCheckMarker = tuple[str, str, str, bool | None, str, str]
ACCOUNT_REFRESH_PROGRESS_FILE = DATA_DIR / "account_refresh_progress.json"
ACCOUNT_REFRESH_PROGRESS_TTL_SECONDS = 24 * 60 * 60
ACCOUNT_RESPONSE_HIDDEN_FIELDS = frozenset(
    {
        "access_token",
        "accessToken",
        "accesstoken",
        "refresh_token",
        "refreshToken",
        "refreshtoken",
        "id_token",
        "idToken",
        "idtoken",
        "cookie",
        "snlm0e",
        "fp",
        "fingerprint",
        "user-agent",
        "user_agent",
        "impersonate",
        "oai-device-id",
        "oai_device_id",
        "oai-session-id",
        "oai_session_id",
        "sec-ch-ua",
        "sec_ch_ua",
        "sec-ch-ua-full-version",
        "sec_ch_ua_full_version",
        "sec-ch-ua-full-version-list",
        "sec_ch_ua_full_version_list",
        "sec-ch-ua-mobile",
        "sec_ch_ua_mobile",
        "sec-ch-ua-platform",
        "sec_ch_ua_platform",
        "sec-ch-ua-platform-version",
        "sec_ch_ua_platform_version",
        "accept-language",
        "accept_language",
    }
)
ACCOUNT_RESPONSE_HIDDEN_FIELD_ALIASES = frozenset(
    str(key).strip().lower().replace("_", "").replace("-", "")
    for key in ACCOUNT_RESPONSE_HIDDEN_FIELDS
)


def is_account_response_hidden_field(key: object) -> bool:
    text = str(key or "").strip()
    normalized = text.lower().replace("_", "-")
    alias = normalized.replace("-", "")
    return (
        text in ACCOUNT_RESPONSE_HIDDEN_FIELDS
        or normalized in ACCOUNT_RESPONSE_HIDDEN_FIELDS
        or alias in ACCOUNT_RESPONSE_HIDDEN_FIELD_ALIASES
    )



class ImageAccountSelectionError(RuntimeError):
    """图片账号调度失败。

    这是“本次请求为什么没拿到账号”的错误，不等同于账号持久状态。
    账号是否限流/异常仍由远程配额确认或鉴权结果决定。
    """

    # 控制流只认两个结果：
    #   quota_exhausted -> 远程确认额度耗尽，告诉客户端别重试（429）
    #   unavailable     -> 其它一切（没号/全忙/预检失败/上游波动），可重试（503）
    DEFAULTS: dict[str, tuple[int, str, str]] = {
        "quota_exhausted": (429, "insufficient_quota", "insufficient_quota"),
        "unavailable": (503, "server_error", "no_available_account"),
    }

    def __init__(self, kind: str, message: str = "") -> None:
        defaults = self.DEFAULTS.get(kind, self.DEFAULTS["unavailable"])
        self.kind = kind if kind in self.DEFAULTS else "unavailable"
        self.status_code, self.error_type, self.code = defaults
        detail = message or self.kind.replace("_", " ")
        super().__init__(f"image_account_selection:{self.kind}; {detail}")


class OAuthRefreshError(RuntimeError):
    """Structured OAuth token refresh failure."""

    def __init__(self, status_code: int, error_code: str = "", description: str = "") -> None:
        self.status_code = int(status_code or 0)
        self.error_code = str(error_code or "").strip()
        self.description = str(description or "").strip()[:300]
        details = [f"oauth_refresh_http_{self.status_code}"]
        if self.error_code:
            details.append(self.error_code)
        if self.description and self.description.casefold() != self.error_code.casefold():
            details.append(self.description)
        super().__init__(": ".join(details))


class TerminalRefreshTokenError(OAuthRefreshError):
    """The refresh credential is revoked, expired, or otherwise unusable."""

    def __init__(self, status_code: int, error_code: str = "", description: str = "") -> None:
        super().__init__(status_code, error_code, description)
        self.failure = image_failure("auth_invalid", raw_detail=str(self))


class RefreshCredentialsChangedError(RuntimeError):
    """Refresh credentials changed while an OAuth request was in flight."""

    def __init__(self) -> None:
        message = "OAuth refresh credentials changed during refresh."
        self.failure = image_failure("upstream_unavailable", raw_detail=message)
        super().__init__(message)


class AccountService:
    """账号池服务，使用 token -> account 的 dict 保存账号。"""

    _ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 24 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_SECONDS = 3 * 24 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_ERROR_BACKOFF_SECONDS = 6 * 60 * 60
    _REFRESH_TOKEN_KEEPALIVE_BATCH_SIZE = 3
    _TOKEN_REFRESH_ERROR_BACKOFF_SECONDS = 5 * 60
    _POOL_HEALTH_REFRESH_BATCH_SIZE = 10
    _IMAGE_FAILURE_REFRESH_DEDUP_SECONDS = 30
    _IMAGE_FAILURE_REFRESH_MAX_CONCURRENT = 2
    _IMAGE_QUOTA_ACCOUNTING_KEY_LIMIT = 512
    _OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
    _OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
    _OAUTH_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    )
    _OAUTH_SEC_CH_UA = '"Chromium";v="146", "Google Chrome";v="146", "Not_A Brand";v="99"'
    _OAUTH_SEC_CH_UA_FULL_VERSION = '"146.0.0.0"'
    _OAUTH_SEC_CH_UA_FULL_VERSION_LIST = (
        '"Chromium";v="146.0.0.0", '
        '"Google Chrome";v="146.0.0.0", '
        '"Not_A Brand";v="99.0.0.0"'
    )
    _TERMINAL_REFRESH_ERROR_CODES = frozenset({
        "invalid_grant",
        "invalid_refresh_token",
        "refresh_token_invalidated",
    })
    _TERMINAL_REFRESH_MESSAGE_FRAGMENTS = ("session has ended",)
    # 刷新进度追踪
    _refresh_progress: dict[str, dict] = {}
    _refresh_progress_lock = Lock()

    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self._lock = Lock()
        self._token_refresh_lock = Lock()
        self._oauth_refresh_flights_lock = Lock()
        self._oauth_refresh_flights: dict[tuple[str, str], Future[str]] = {}
        self._image_auth_condition = Condition(Lock())
        self._image_auth_active = 0
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._persisted_accounts: dict[str, dict] = {}
        self._accounts = self._load_accounts()
        self._image_inflight: dict[str, int] = {}
        self._image_failure_refresh_lock = Lock()
        self._image_failure_refresh_active: set[str] = set()
        self._image_failure_refresh_active_scopes: dict[str, str] = {}
        self._image_failure_refresh_rerun: set[str] = set()
        self._image_failure_refresh_pending: deque[str] = deque()
        self._image_failure_refresh_pending_set: set[str] = set()
        self._image_failure_refresh_pending_scopes: dict[str, str] = {}
        self._image_failure_refresh_started_at: dict[str, float] = {}
        self._token_aliases: dict[str, str] = {}
        self._cumulative_total = self._load_cumulative_total()
        self._refresh_progress_file = self._get_refresh_progress_file()

    def reset_after_fork(self) -> None:
        """Rebuild locks and in-memory concurrency state after a fork.

        A forked child inherits the parent's locks in whatever state they were
        in at fork time, plus in-flight counters and refresh futures that belong
        to work only the parent can finish. Both are fatal in the child: an
        inherited held lock deadlocks, and inherited image slots make the child
        wait for a slot the parent owns. The child re-reads accounts from
        storage so it starts from committed state rather than a stale snapshot.
        """
        self._lock = Lock()
        self._token_refresh_lock = Lock()
        self._oauth_refresh_flights_lock = Lock()
        self._oauth_refresh_flights = {}
        self._image_auth_condition = Condition(Lock())
        self._image_auth_active = 0
        self._image_slot_condition = Condition(self._lock)
        self._image_inflight = {}
        self._image_failure_refresh_lock = Lock()
        self._image_failure_refresh_active = set()
        self._image_failure_refresh_active_scopes = {}
        self._image_failure_refresh_rerun = set()
        self._image_failure_refresh_pending = deque()
        self._image_failure_refresh_pending_set = set()
        self._image_failure_refresh_pending_scopes = {}
        self._image_failure_refresh_started_at = {}
        self._token_aliases = {}
        self._persisted_accounts = {}
        self._accounts = self._load_accounts()

    def _get_cumulative_file(self) -> Path:
        storage_path = getattr(self.storage, "file_path", None)
        if isinstance(storage_path, Path):
            return storage_path.with_name(".cumulative_total")
        return DATA_DIR / ".cumulative_total"

    def _get_refresh_progress_file(self) -> Path:
        storage_path = getattr(self.storage, "file_path", None)
        if isinstance(storage_path, Path):
            return storage_path.with_name("account_refresh_progress.json")
        return ACCOUNT_REFRESH_PROGRESS_FILE

    def _load_cumulative_total(self) -> int:
        try:
            f = self._get_cumulative_file()
            if f.exists():
                return int(f.read_text().strip())
        except Exception:
            pass
        return len(self._accounts)

    def _save_cumulative_total(self) -> None:
        try:
            self._get_cumulative_file().write_text(str(self._cumulative_total))
        except Exception:
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        try:
            payload = str(token or "").split(".")[1]
            payload += "=" * ((4 - len(payload) % 4) % 4)
            import base64
            import json
            data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            try:
                parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _timestamp_to_iso(value: object) -> str:
        try:
            ts = int(value)
        except (TypeError, ValueError):
            return ""
        tz = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz).isoformat()

    def _load_accounts(self) -> dict[str, dict]:
        accounts = self.storage.load_accounts()
        loaded: dict[str, dict] = {}
        previous: dict[str, dict] = {}
        changed = False
        for item in accounts:
            normalized = self._normalize_account(item)
            if normalized is None:
                changed = True
                continue
            previous[normalized["access_token"]] = dict(normalized)
            if (
                normalized.get("last_remote_check_result") == "pending"
                and normalized.get("pending_auth_scope") != "image"
            ):
                normalized["last_remote_check_result"] = "error"
                normalized["last_remote_check_error"] = (
                    normalized.get("last_remote_check_error")
                    or "Account verification was interrupted by a service restart."
                )
                normalized["last_remote_check_error_at"] = datetime.now(timezone.utc).isoformat()
                normalized["pending_auth_remove_invalid"] = None
                normalized["pending_auth_scope"] = None
                normalized.pop("pending_auth_verification_id", None)
                changed = True
            if normalized != item:
                changed = True
            loaded[normalized["access_token"]] = normalized
        if changed:
            saved = self.storage.save_accounts_merged(list(loaded.values()), list(previous.values()))
            loaded = {
                normalized["access_token"]: normalized
                for item in saved
                if (normalized := self._normalize_account(item)) is not None
            }
        self._persisted_accounts = deepcopy(loaded)
        return loaded

    def _save_accounts(self) -> None:
        saved = self.storage.save_accounts_merged(
            list(self._accounts.values()),
            list(self._persisted_accounts.values()),
        )
        if isinstance(saved, list):
            self._accounts = {
                normalized["access_token"]: normalized
                for item in saved
                if (normalized := self._normalize_account(item)) is not None
            }
        self._persisted_accounts = deepcopy(self._accounts)

    @staticmethod
    def _is_account_selectable(
        account: dict,
        *,
        allow_limited: bool,
        allow_image_pending: bool = False,
    ) -> bool:
        if not isinstance(account, dict):
            return False
        allowed_statuses = {"正常", "限流"} if allow_limited else {"正常"}
        if account.get("status") not in allowed_statuses:
            return False
        if account.get("last_remote_check_result") != "pending":
            return True
        return bool(
            allow_image_pending
            and account.get("pending_auth_scope") == "image"
        )

    @classmethod
    def _is_image_account_available(cls, account: dict) -> bool:
        if not cls._is_account_selectable(account, allow_limited=False):
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        # quota 是展示/预估值，不能作为持久调度开关。
        # 只有远程确认后写入的“限流”状态才代表图片额度耗尽；否则 quota=0 也要允许进入预检，
        # 避免本地扣减或额度重置不同步时把账号锁死在候选池外。
        return account.get("status") == "正常" or int(account.get("quota") or 0) > 0

    @classmethod
    def _is_unlimited_image_quota_account(cls, account: dict) -> bool:
        if not isinstance(account, dict) or not bool(account.get("image_quota_unknown")):
            return False
        account_type = (cls._normalize_account_type(account.get("type")) or "").lower()
        return account_type in {"pro", "prolite"}

    @classmethod
    def _account_matches_plan_type(cls, account: dict, plan_type: str | None = None) -> bool:
        if not plan_type:
            return True
        normalized_plan = cls._normalize_account_type(plan_type)
        normalized_account = cls._normalize_account_type(account.get("type"))
        if not normalized_plan or not normalized_account:
            return False
        return normalized_plan.lower() == normalized_account.lower()

    @classmethod
    def _account_matches_source_type(cls, account: dict, source_type: str | None = None) -> bool:
        if not source_type:
            return True
        return cls._normalize_source_type(account.get("source_type")) == cls._normalize_source_type(source_type)

    @classmethod
    def _account_matches_any_plan_type(cls, account: dict, plan_types: set[str] | tuple[str, ...] | None = None) -> bool:
        if not plan_types:
            return True
        normalized_account = cls._normalize_account_type(account.get("type"))
        normalized_plans = {
            normalized
            for plan_type in plan_types
            if (normalized := cls._normalize_account_type(plan_type))
        }
        return bool(normalized_account and normalized_account in normalized_plans)

    @staticmethod
    def _normalize_source_type(value: object) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"codex", "cpa", "cpa_json", "remote_cpa", "sub2api"}:
            return "codex"
        return "web"

    @staticmethod
    def _normalize_account_type(value: object) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        key = raw.lower().replace("-", "_").replace(" ", "_")
        compact = key.replace("_", "")
        aliases = {
            "free": "free",
            "plus": "Plus",
            "pro": "Pro",
            "prolite": "ProLite",
            "team": "Team",
            "business": "Team",
            "enterprise": "Enterprise",
        }
        return aliases.get(compact) or aliases.get(key)

    @staticmethod
    def _has_value(value: object) -> bool:
        return value is not None and str(value).strip() != ""

    @staticmethod
    def _bool_value(value: object, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if raw in {"0", "false", "no", "n", "off", "disabled", "none", "null", ""}:
            return False
        return default

    @classmethod
    def _normalize_account_status(cls, value: object, account: dict) -> str:
        if cls._bool_value(account.get("auto_disabled"), False):
            return "禁用"
        if account.get("enabled") is not None and not cls._bool_value(account.get("enabled"), True):
            return "禁用"
        raw = str(value or "").strip()
        if not raw:
            return "正常"
        aliases = {
            "正常": "正常",
            "normal": "正常",
            "ready": "正常",
            "限流": "限流",
            "limited": "限流",
            "rate_limited": "限流",
            "cooling": "限流",
            "backoff": "限流",
            "异常": "异常",
            "abnormal": "异常",
            "invalid": "异常",
            "error": "异常",
            "incomplete": "异常",
            "禁用": "禁用",
            "disabled": "禁用",
            "auto_disabled": "禁用",
        }
        return aliases.get(raw.lower(), aliases.get(raw, "正常"))

    @classmethod
    def _quota_value(cls, value: object, default: int = 0) -> int:
        if not cls._has_value(value):
            return max(0, int(default or 0))
        try:
            return max(0, int(float(str(value).strip())))
        except (TypeError, ValueError):
            return max(0, int(default or 0))

    @classmethod
    def _extract_image_quota_from_limits(cls, limits_progress: object) -> tuple[int | None, str | None, bool | None]:
        if not isinstance(limits_progress, list):
            return None, None, None
        if not limits_progress:
            return None, None, None

        for item in limits_progress:
            if not isinstance(item, dict):
                continue
            feature = str(
                item.get("feature_name")
                or item.get("feature")
                or item.get("name")
                or item.get("type")
                or ""
            ).strip().lower()
            if feature in {"image_gen", "image_generation", "image", "images"}:
                restore_at = str(
                    item.get("reset_after")
                    or item.get("restore_at")
                    or item.get("reset_at")
                    or ""
                ).strip() or None
                return cls._quota_value(item.get("remaining"), 0), restore_at, False

        return None, None, True

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = item.get("access_token") or item.get("accessToken") or ""
        if not access_token:
            return None
        normalized = dict(item)
        normalized.pop("accessToken", None)
        normalized["access_token"] = access_token
        try:
            normalized["queue_account_id"] = str(
                UUID(str(normalized.get("queue_account_id") or ""))
            )
        except (TypeError, ValueError, AttributeError):
            normalized["queue_account_id"] = str(uuid4())
        if str(normalized.get("type") or "").strip().lower() == "codex":
            normalized["export_type"] = "codex"
            normalized.pop("type", None)
        limits_progress = normalized.get("limits_progress")
        limits_progress = limits_progress if isinstance(limits_progress, list) else []
        derived_quota, derived_restore_at, derived_unknown = self._extract_image_quota_from_limits(limits_progress)
        has_explicit_quota = self._has_value(normalized.get("quota"))
        normalized["type"] = self._normalize_account_type(normalized.get("type"))
        normalized["status"] = self._normalize_account_status(normalized.get("status"), normalized)
        normalized.pop("enabled", None)
        normalized.pop("auto_disabled", None)
        normalized["email"] = normalized.get("email") or None
        normalized["user_id"] = normalized.get("user_id") or None
        normalized["proxy"] = str(normalized.get("proxy") or "").strip()
        source_type = normalized.get("source_type")
        if not source_type and str(normalized.get("export_type") or "").strip().lower() == "codex":
            source_type = "codex"
        normalized["source_type"] = self._normalize_source_type(source_type)
        if not has_explicit_quota and derived_quota is not None:
            normalized["quota"] = derived_quota
        normalized["quota"] = self._quota_value(normalized.get("quota"), 0)
        has_explicit_quota_state = self._has_value(normalized.get("image_quota_unknown"))
        if derived_unknown is not None and not has_explicit_quota_state:
            normalized["image_quota_unknown"] = derived_unknown
        elif not has_explicit_quota_state:
            normalized["image_quota_unknown"] = True
        normalized["image_quota_unknown"] = self._bool_value(normalized.get("image_quota_unknown"), True)
        has_confirmed_quota = (
            derived_unknown is False
            or (
                bool(normalized.get("last_remote_checked_at"))
                and not normalized["image_quota_unknown"]
            )
        )
        if normalized["status"] == "正常" and (not has_confirmed_quota or normalized["quota"] == 0):
            normalized["image_quota_unknown"] = True
        elif normalized["status"] == "限流":
            normalized["quota"] = 0
            normalized["image_quota_unknown"] = False
        normalized["limits_progress"] = limits_progress
        normalized["default_model_slug"] = normalized.get("default_model_slug") or None
        if derived_restore_at and not normalized.get("restore_at"):
            normalized["restore_at"] = derived_restore_at
        normalized["restore_at"] = normalized.get("restore_at") or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["invalid_count"] = int(normalized.get("invalid_count") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        accounting_keys: list[str] = []
        raw_accounting_keys = normalized.get("_image_quota_accounting_keys")
        if not isinstance(raw_accounting_keys, list):
            raw_accounting_keys = []
        for raw_key in raw_accounting_keys[-self._IMAGE_QUOTA_ACCOUNTING_KEY_LIMIT:]:
            key = str(raw_key or "").strip()[:160]
            if key and key not in accounting_keys:
                accounting_keys.append(key)
        normalized["_image_quota_accounting_keys"] = accounting_keys[
            -self._IMAGE_QUOTA_ACCOUNTING_KEY_LIMIT:
        ]
        normalized["last_invalid_at"] = normalized.get("last_invalid_at") or None
        normalized["last_refresh_error"] = normalized.get("last_refresh_error") or None
        normalized["last_refresh_error_at"] = normalized.get("last_refresh_error_at") or None
        normalized["last_remote_checked_at"] = normalized.get("last_remote_checked_at") or None
        normalized["last_remote_check_attempt_at"] = normalized.get("last_remote_check_attempt_at") or None
        normalized["last_remote_check_error"] = normalized.get("last_remote_check_error") or None
        normalized["last_remote_check_error_at"] = normalized.get("last_remote_check_error_at") or None
        normalized["last_remote_check_event"] = normalized.get("last_remote_check_event") or None
        remote_check_result = str(normalized.get("last_remote_check_result") or "").strip().lower()
        normalized["last_remote_check_result"] = (
            remote_check_result
            if remote_check_result in {"pending", "ok", "error", "invalid"}
            else None
        )
        pending_remove = normalized.get("pending_auth_remove_invalid")
        normalized["pending_auth_remove_invalid"] = (
            self._bool_value(pending_remove)
            if normalized["last_remote_check_result"] == "pending" and pending_remove is not None
            else None
        )
        pending_scope = str(normalized.get("pending_auth_scope") or "").strip().lower()
        normalized["pending_auth_scope"] = (
            "image" if pending_scope == "image" else "account"
        ) if normalized["last_remote_check_result"] == "pending" else None
        verification_id = str(normalized.get("pending_auth_verification_id") or "").strip()
        if normalized["last_remote_check_result"] == "pending" and verification_id:
            normalized["pending_auth_verification_id"] = verification_id
        else:
            normalized.pop("pending_auth_verification_id", None)

        normalized["last_token_refresh_at"] = normalized.get("last_token_refresh_at") or None
        normalized["last_token_refresh_error"] = normalized.get("last_token_refresh_error") or None
        normalized["last_token_refresh_error_at"] = normalized.get("last_token_refresh_error_at") or None
        for key in (
            "capability_cooldowns",
            "capability_failure_counts",
            "capability_failure_codes",
            "capability_failed_at",
        ):
            normalized.pop(key, None)
        normalized["created_at"] = normalized.get("created_at") or AccountService._now()
        return normalized

    @staticmethod
    def _jwt_exp(access_token: str) -> int:
        try:
            return int(AccountService._decode_jwt_payload(access_token).get("exp") or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _token_expires_in(cls, access_token: str) -> int | None:
        exp = cls._jwt_exp(access_token)
        if exp <= 0:
            return None
        return exp - int(time.time())

    @classmethod
    def _token_needs_refresh(cls, access_token: str, *, force: bool = False) -> bool:
        if force:
            return True
        remaining = cls._token_expires_in(access_token)
        return remaining is not None and remaining <= cls._ACCESS_TOKEN_REFRESH_SKEW_SECONDS

    @classmethod
    def _token_issued_at(cls, access_token: str) -> datetime | None:
        try:
            iat = int(cls._decode_jwt_payload(access_token).get("iat") or 0)
        except (TypeError, ValueError):
            return None
        if iat <= 0:
            return None
        return datetime.fromtimestamp(iat, tz=timezone.utc)

    @staticmethod
    def _safe_response_text(response: object, limit: int = 300) -> str:
        try:
            return str(getattr(response, "text", "") or "")[:limit]
        except Exception:
            return ""

    @staticmethod
    def _account_fingerprint(account: dict | None) -> dict[str, str]:
        if not isinstance(account, dict):
            return {}
        raw_fp = account.get("fp")
        if not isinstance(raw_fp, dict):
            return {}
        return {
            str(key).lower().replace("_", "-"): str(value).strip()
            for key, value in raw_fp.items()
            if str(value or "").strip()
        }

    @staticmethod
    def _oauth_refresh_error_fields(data: object) -> tuple[str, str]:
        if not isinstance(data, dict):
            return "", ""
        error = data.get("error")
        nested = error if isinstance(error, dict) else {}
        code = str(
            nested.get("code")
            or data.get("code")
            or (error if isinstance(error, str) else "")
            or ""
        ).strip()
        description = str(
            data.get("error_description")
            or nested.get("message")
            or data.get("message")
            or nested.get("description")
            or ""
        ).strip()
        return code, description

    @classmethod
    def _is_terminal_refresh_error(cls, status_code: int, error_code: str, description: str) -> bool:
        if status_code in {408, 429} or status_code >= 500:
            return False
        normalized_code = str(error_code or "").strip().casefold()
        normalized_description = str(description or "").strip().casefold()
        if normalized_code in cls._TERMINAL_REFRESH_ERROR_CODES:
            return True
        return 400 <= status_code < 500 and any(
            fragment in normalized_description
            for fragment in cls._TERMINAL_REFRESH_MESSAGE_FRAGMENTS
        )

    def _resolve_access_token_locked(self, access_token: str) -> str:
        token = str(access_token or "").strip()
        seen: set[str] = set()
        while token and token not in self._accounts and token in self._token_aliases and token not in seen:
            seen.add(token)
            token = self._token_aliases.get(token, token)
        return token

    def resolve_access_token(self, access_token: str) -> str:
        if not access_token:
            return ""
        with self._lock:
            return self._resolve_access_token_locked(access_token)

    def _get_account_for_token(self, access_token: str) -> tuple[str, dict | None]:
        with self._lock:
            resolved = self._resolve_access_token_locked(access_token)
            account = self._accounts.get(resolved)
            return resolved, dict(account) if account else None

    def _credential_snapshot(self, access_token: str) -> tuple[str, str, dict | None]:
        resolved, account = self._get_account_for_token(access_token)
        if not account:
            return resolved, "", None
        active_token = str(account.get("access_token") or resolved or access_token).strip()
        refresh_token = str(account.get("refresh_token") or "").strip()
        return active_token, refresh_token, account

    def _record_token_refresh_error(
        self,
        access_token: str,
        event: str,
        error: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            resolved = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(resolved)
            if current is None:
                return False
            if expected_access_token is not None and (
                resolved != str(expected_access_token or "").strip()
            ):
                return False
            if expected_refresh_token is not None and (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                return False
            next_item = dict(current)
            next_item["last_token_refresh_error"] = str(error or "refresh token failed")
            next_item["last_token_refresh_error_at"] = now
            account = self._normalize_account(next_item)
            if account is None:
                return False
            self._accounts[resolved] = account
            self._save_accounts()
        log_service.add(
            LOG_TYPE_ACCOUNT,
            "refresh_token 刷新 access_token 失败",
            {"source": event, "token": anonymize_token(resolved), "error": str(error or "")},
        )
        return True

    def _recent_token_refresh_error(self, account: dict) -> bool:
        last_error_at = self._parse_time(account.get("last_token_refresh_error_at"))
        if last_error_at is None:
            return False
        return (datetime.now(timezone.utc) - last_error_at).total_seconds() < self._TOKEN_REFRESH_ERROR_BACKOFF_SECONDS

    def _recent_refresh_token_keepalive_error(self, account: dict, now: datetime) -> bool:
        last_error_at = self._parse_time(account.get("last_token_refresh_error_at"))
        if last_error_at is None:
            return False
        return (now - last_error_at).total_seconds() < self._REFRESH_TOKEN_KEEPALIVE_ERROR_BACKOFF_SECONDS

    def _refresh_token_keepalive_anchor(self, account: dict) -> datetime | None:
        return (
            self._parse_time(account.get("last_token_refresh_at"))
            or self._token_issued_at(str(account.get("access_token") or ""))
            or self._parse_time(account.get("created_at"))
        )

    def _refresh_token_keepalive_due_at(self, account: dict, now: datetime) -> datetime | None:
        if not str(account.get("refresh_token") or "").strip():
            return None
        if account.get("status") not in {"正常", "限流"}:
            return None
        if account.get("last_remote_check_result") == "pending":
            return None
        if self._recent_refresh_token_keepalive_error(account, now):
            return None
        anchor = self._refresh_token_keepalive_anchor(account)
        if anchor is None:
            return now
        due_at = anchor + timedelta(seconds=self._REFRESH_TOKEN_KEEPALIVE_SECONDS)
        return due_at if due_at <= now else None

    @contextmanager
    def _image_auth_slot(self):
        with self._image_auth_condition:
            while self._image_auth_active >= config.image_auth_refresh_concurrency:
                self._image_auth_condition.wait(timeout=0.5)
            self._image_auth_active += 1
        try:
            yield
        finally:
            with self._image_auth_condition:
                self._image_auth_active = max(0, self._image_auth_active - 1)
                self._image_auth_condition.notify_all()

    @contextmanager
    def _oauth_refresh_slot(self, *, image_scope: bool):
        if image_scope:
            with self._image_auth_slot():
                yield
            return
        with self._token_refresh_lock:
            yield

    def _request_access_token_refresh(
        self,
        refresh_token: str,
        account: dict | None = None,
        *,
        image_scope: bool = False,
    ) -> dict[str, str]:
        from curl_cffi import requests
        from services.proxy_service import proxy_settings

        fp = self._account_fingerprint(account)
        fp.update({
            "impersonate": "chrome146",
            "user-agent": self._OAUTH_USER_AGENT,
            "sec-ch-ua": self._OAUTH_SEC_CH_UA,
            "sec-ch-ua-full-version": self._OAUTH_SEC_CH_UA_FULL_VERSION,
            "sec-ch-ua-full-version-list": self._OAUTH_SEC_CH_UA_FULL_VERSION_LIST,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-ch-ua-platform-version": '"10.0.0"',
        })
        impersonate = "chrome146"
        user_agent = self._OAUTH_USER_AGENT
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": user_agent,
        }
        if fp:
            for key, header_name in (
                ("accept-language", "Accept-Language"),
                ("sec-ch-ua", "Sec-Ch-Ua"),
                ("sec-ch-ua-full-version", "Sec-Ch-Ua-Full-Version"),
                ("sec-ch-ua-full-version-list", "Sec-Ch-Ua-Full-Version-List"),
                ("sec-ch-ua-mobile", "Sec-Ch-Ua-Mobile"),
                ("sec-ch-ua-platform", "Sec-Ch-Ua-Platform"),
                ("sec-ch-ua-platform-version", "Sec-Ch-Ua-Platform-Version"),
            ):
                value = fp.get(key)
                if value:
                    headers[header_name] = value
        session = requests.Session(**proxy_settings.build_session_kwargs(account=account, impersonate=impersonate, verify=True))
        try:
            with self._oauth_refresh_slot(image_scope=image_scope):
                response = session.post(
                    self._OAUTH_TOKEN_URL,
                    headers=headers,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": self._OAUTH_CLIENT_ID,
                    },
                    timeout=60,
                )
            raw_text = self._safe_response_text(response)
            try:
                data = response.json() if raw_text else {}
            except Exception:
                data = {}
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code != 200 or not isinstance(data, dict) or not data.get("access_token"):
                error_code, description = self._oauth_refresh_error_fields(data)
                description = description or raw_text
                error_type = (
                    TerminalRefreshTokenError
                    if self._is_terminal_refresh_error(status_code, error_code, description)
                    else OAuthRefreshError
                )
                raise error_type(status_code, error_code, description)
            return {
                "access_token": str(data.get("access_token") or "").strip(),
                "refresh_token": str(data.get("refresh_token") or refresh_token).strip(),
                "id_token": str(data.get("id_token") or "").strip(),
            }
        finally:
            session.close()

    def _apply_refreshed_tokens(
        self,
        old_access_token: str,
        token_data: dict,
        event: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        expected_access_token = expected_access_token or old_access_token
        with self._image_slot_condition:
            old_token = self._resolve_access_token_locked(old_access_token)
            current = self._accounts.get(old_token)
            if current is None:
                raise RefreshCredentialsChangedError()
            if old_token != str(expected_access_token or "").strip():
                raise RefreshCredentialsChangedError()
            if (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                raise RefreshCredentialsChangedError()
            new_token = str(token_data.get("access_token") or old_token).strip()
            if not new_token:
                return old_token
            if new_token != old_token and new_token in self._accounts:
                raise RefreshCredentialsChangedError()

            next_item = dict(current)
            next_item["access_token"] = new_token
            if token_data.get("refresh_token"):
                next_item["refresh_token"] = str(token_data.get("refresh_token") or "").strip()
            if token_data.get("id_token"):
                next_item["id_token"] = str(token_data.get("id_token") or "").strip()
            next_item["last_token_refresh_at"] = now
            next_item["last_token_refresh_error"] = None
            next_item["last_token_refresh_error_at"] = None
            next_item["invalid_count"] = 0
            next_item["last_invalid_at"] = None
            next_item["last_refresh_error"] = None
            next_item["last_refresh_error_at"] = None

            account = self._normalize_account(next_item)
            if account is None:
                return old_token

            rotated = new_token != old_token
            if rotated:
                self._accounts.pop(old_token, None)
                self._token_aliases[old_token] = new_token
                old_inflight = int(self._image_inflight.pop(old_token, 0))
                if old_inflight:
                    self._image_inflight[new_token] = int(self._image_inflight.get(new_token, 0)) + old_inflight
                with self._image_failure_refresh_lock:
                    old_pending_scope = self._image_failure_refresh_pending_scopes.pop(
                        old_token,
                        None,
                    )
                    pending = (
                        new_token if token == old_token else token
                        for token in self._image_failure_refresh_pending
                    )
                    self._image_failure_refresh_pending = deque(dict.fromkeys(pending))
                    self._image_failure_refresh_pending_set = set(
                        self._image_failure_refresh_pending
                    )
                    if old_pending_scope is not None:
                        current_scope = self._image_failure_refresh_pending_scopes.get(new_token)
                        self._image_failure_refresh_pending_scopes[new_token] = (
                            "account"
                            if "account" in {old_pending_scope, current_scope}
                            else "image"
                        )
                    old_started_at = self._image_failure_refresh_started_at.pop(
                        old_token, None
                    )
                    if old_started_at is not None:
                        self._image_failure_refresh_started_at[new_token] = max(
                            old_started_at,
                            self._image_failure_refresh_started_at.get(new_token, 0.0),
                        )
                    # Active workers retain their original key until their finally
                    # block runs. Scheduling resolves aliases to deduplicate them.
            self._accounts[new_token] = account
            self._save_accounts()
            self._image_slot_condition.notify_all()

        log_service.add(
            LOG_TYPE_ACCOUNT,
            "refresh_token 已刷新 access_token",
            {"source": event, "token": anonymize_token(new_token), "rotated": rotated},
        )
        return new_token

    def _refresh_access_token_owner(
        self,
        active_token: str,
        refresh_token: str,
        account: dict,
        *,
        event: str,
        image_scope: bool,
    ) -> str:
        try:
            if image_scope:
                token_data = self._request_access_token_refresh(
                    refresh_token,
                    account,
                    image_scope=True,
                )
            else:
                token_data = self._request_access_token_refresh(refresh_token, account)
        except TerminalRefreshTokenError as exc:
            exc.expected_access_token = active_token
            exc.expected_refresh_token = refresh_token
            current_token, current_refresh_token, current = self._credential_snapshot(active_token)
            if not current or (current_token, current_refresh_token) != (active_token, refresh_token):
                raise RefreshCredentialsChangedError() from exc
            raise
        except Exception as exc:
            recorded = self._record_token_refresh_error(
                active_token,
                event,
                str(exc or ""),
                expected_access_token=active_token,
                expected_refresh_token=refresh_token,
            )
            if recorded:
                raise
            raise RefreshCredentialsChangedError() from exc

        return self._apply_refreshed_tokens(
            active_token,
            token_data,
            event,
            expected_access_token=active_token,
            expected_refresh_token=refresh_token,
        )

    def refresh_access_token(
        self,
        access_token: str,
        *,
        force: bool = False,
        event: str = "refresh_access_token",
        remove_invalid: bool | None = None,
        raise_on_error: bool = False,
        image_scope: bool = False,
        expected_credentials: tuple[str, str] | None = None,
        skip_if_image_busy: bool = False,
    ) -> str:
        if not access_token:
            return ""
        for credential_attempt in range(2):
            resolved_token, account = self._get_account_for_token(access_token)
            if not account:
                raise RefreshCredentialsChangedError()
            active_token = str(account.get("access_token") or resolved_token or access_token)
            needs_refresh = self._token_needs_refresh(active_token, force=force)
            refresh_backoff = not force and self._recent_token_refresh_error(account)
            refresh_token = str(account.get("refresh_token") or "").strip()
            if expected_credentials is not None and (
                active_token, refresh_token
            ) != expected_credentials:
                raise RefreshCredentialsChangedError()
            if not refresh_token:
                return active_token

            key = (active_token, refresh_token)
            if skip_if_image_busy:
                with self._image_slot_condition:
                    current_token = self._resolve_access_token_locked(active_token)
                    current = self._accounts.get(current_token)
                    current_refresh_token = str(
                        (current or {}).get("refresh_token") or ""
                    ).strip()
                    if not current or (current_token, current_refresh_token) != key:
                        if expected_credentials is not None or credential_attempt > 0:
                            raise RefreshCredentialsChangedError()
                        continue
                    image_busy = int(self._image_inflight.get(current_token, 0)) > 1
                    with self._oauth_refresh_flights_lock:
                        future = self._oauth_refresh_flights.get(key)
                        owner = future is None
                        if future is None:
                            if image_busy or not needs_refresh or refresh_backoff:
                                return active_token
                            future = Future()
                            self._oauth_refresh_flights[key] = future
            else:
                with self._oauth_refresh_flights_lock:
                    future = self._oauth_refresh_flights.get(key)
                    owner = future is None
                    if future is None:
                        if not needs_refresh or refresh_backoff:
                            return active_token
                        future = Future()
                        self._oauth_refresh_flights[key] = future
            if owner:
                try:
                    result = self._refresh_access_token_owner(
                        active_token,
                        refresh_token,
                        account,
                        event=event,
                        image_scope=image_scope,
                    )
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            try:
                return future.result()
            except TerminalRefreshTokenError as exc:
                expected_access_token = str(
                    getattr(exc, "expected_access_token", active_token) or active_token
                )
                expected_refresh_token = str(
                    getattr(exc, "expected_refresh_token", refresh_token) or refresh_token
                )
                current_token, current_refresh_token, current = self._credential_snapshot(active_token)
                if current and (current_token, current_refresh_token) != (
                    expected_access_token,
                    expected_refresh_token,
                ):
                    if expected_credentials is not None:
                        raise RefreshCredentialsChangedError() from exc
                    if credential_attempt == 0:
                        continue
                error_str = str(exc)
                self.handle_invalid_token(
                    active_token,
                    event,
                    error=error_str,
                    remove=remove_invalid,
                    expected_access_token=expected_access_token,
                    expected_refresh_token=expected_refresh_token,
                    token_refresh_error=error_str,
                )
                raise
            except RefreshCredentialsChangedError:
                if credential_attempt == 0 and expected_credentials is None:
                    continue
                raise
            except Exception:
                if raise_on_error:
                    raise
                current_token, current = self._get_account_for_token(active_token)
                if current:
                    return str(current.get("access_token") or current_token or active_token)
                raise RefreshCredentialsChangedError()
            finally:
                if owner:
                    with self._oauth_refresh_flights_lock:
                        if self._oauth_refresh_flights.get(key) is future:
                            self._oauth_refresh_flights.pop(key, None)
        raise RefreshCredentialsChangedError()

    def list_expiring_access_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for account in self._accounts.values()
                if account.get("status") in {"正常", "限流"}
                and account.get("last_remote_check_result") != "pending"
                and str(account.get("refresh_token") or "").strip()
                and (token := str(account.get("access_token") or "").strip())
                and self._token_needs_refresh(token)
            ]

    def list_refresh_token_keepalive_tokens(self) -> list[str]:
        now = datetime.now(timezone.utc)
        due_items: list[tuple[datetime, str]] = []
        with self._lock:
            for account in self._accounts.values():
                due_at = self._refresh_token_keepalive_due_at(account, now)
                token = str(account.get("access_token") or "").strip()
                if due_at is not None and token:
                    due_items.append((due_at, token))
        due_items.sort(key=lambda item: item[0])
        return [token for _, token in due_items[: self._REFRESH_TOKEN_KEEPALIVE_BATCH_SIZE]]

    def keepalive_refresh_tokens(self, access_tokens: list[str]) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}

        refreshed = 0
        errors = []
        for access_token in access_tokens:
            before = self.resolve_access_token(access_token)
            try:
                after = self.refresh_access_token(before, force=True, event="refresh_token_keepalive")
            except (TerminalRefreshTokenError, RefreshCredentialsChangedError) as exc:
                errors.append({
                    "token": anonymize_token(before),
                    "error": str(exc),
                })
                continue
            account = self.get_account(after)
            if account and str(account.get("last_token_refresh_error") or "").strip():
                errors.append({
                    "token": anonymize_token(before),
                    "error": str(account.get("last_token_refresh_error") or "refresh token failed"),
                })
                continue
            if account:
                refreshed += 1

        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
        }

    def list_tokens(self) -> list[str]:
        with self._lock:
            return list(self._accounts)

    def _list_ready_candidate_tokens(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        excluded = set(excluded_tokens or set())
        return [
            token
            for item in self._accounts.values()
            if self._is_image_account_available(item)
               and self._account_matches_plan_type(item, plan_type)
               and self._account_matches_any_plan_type(item, plan_types)
               and self._account_matches_source_type(item, source_type)
               and (token := item.get("access_token") or "")
               and token not in excluded
        ]

    def _list_available_candidate_tokens(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        max_concurrency = max(1, int(config.image_account_concurrency or 1))
        return [
            token
            for token in self._list_ready_candidate_tokens(
                excluded_tokens,
                plan_type,
                source_type,
                plan_types,
            )
            if int(self._image_inflight.get(token, 0)) < max_concurrency
        ]

    def _acquire_next_candidate_token(
            self,
            excluded_tokens: set[str] | None = None,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
    ) -> str:
        with self._image_slot_condition:
            while True:
                # Token refresh can rotate an attempted account's access token while
                # this request waits for a slot. Resolve aliases on every pass so the
                # same account cannot be selected again under its refreshed token.
                resolved_excluded_tokens = {
                    self._resolve_access_token_locked(token)
                    for token in (excluded_tokens or set())
                    if token
                }
                if not self._list_ready_candidate_tokens(
                    resolved_excluded_tokens,
                    plan_type,
                    source_type,
                    plan_types,
                ):
                    raise self._no_ready_candidate_error(
                        plan_type,
                        source_type,
                        plan_types,
                        resolved_excluded_tokens,
                    )
                tokens = self._list_available_candidate_tokens(
                    resolved_excluded_tokens,
                    plan_type,
                    source_type,
                    plan_types,
                )
                if tokens:
                    access_token = tokens[self._index % len(tokens)]
                    self._index += 1
                    self._image_inflight[access_token] = int(self._image_inflight.get(access_token, 0)) + 1
                    return access_token
                self._image_slot_condition.wait(timeout=1.0)

    def _no_ready_candidate_error(
            self,
            plan_type: str | None,
            source_type: str | None,
            plan_types: set[str] | tuple[str, ...] | None,
            excluded_tokens: set[str] | None,
    ) -> "ImageAccountSelectionError":
        """没有任何 ready 候选时区分两种成因。

        这是“初筛阶段”的判据：只看本地缓存状态，此时还没走远程预检，
        不存在“预检失败”这一维度。匹配账号全部为“限流”（限流只由远程确认写入）
        -> 额度耗尽（429）；否则一律归为可重试的 unavailable（503）。

        注意：get_available_access_token 里的 429 判据更严
        （额外要求 not saw_unavailable_failure），因为那是“预检阶段”，
        会有上游波动等可重试失败混入，不能仅凭限流就下终结性结论。
        """
        excluded = set(excluded_tokens or set())
        matched = 0
        limited = 0
        for item in self._accounts.values():
            token = item.get("access_token") or ""
            if not token or token in excluded:
                continue
            if not (
                self._account_matches_plan_type(item, plan_type)
                and self._account_matches_any_plan_type(item, plan_types)
                and self._account_matches_source_type(item, source_type)
            ):
                continue
            matched += 1
            if str(item.get("status") or "") == "限流":
                limited += 1
        if matched > 0 and limited == matched:
            return ImageAccountSelectionError(
                "quota_exhausted",
                "all matched image accounts are remote-confirmed quota exhausted",
            )
        return ImageAccountSelectionError(
            "unavailable",
            "no image account is ready for current model/status filters",
        )

    def release_image_slot(self, access_token: str) -> None:
        if not access_token:
            return
        with self._image_slot_condition:
            access_token = self._resolve_access_token_locked(access_token)
            self._release_image_slot_locked(access_token)
            self._image_slot_condition.notify_all()

    def _release_image_slot_locked(self, access_token: str) -> None:
        current_inflight = int(self._image_inflight.get(access_token, 0))
        if current_inflight <= 1:
            self._image_inflight.pop(access_token, None)
        else:
            self._image_inflight[access_token] = current_inflight - 1

    def get_available_access_token(
            self,
            plan_type: str | None = None,
            source_type: str | None = None,
            plan_types: set[str] | tuple[str, ...] | None = None,
            excluded_tokens: set[str] | None = None,
    ) -> str:
        """从候选池中获取一个可用的图片生图 token。

        基于本地缓存做初筛，然后通过 fetch_remote_info 做远程验证（token 有效性、配额等）。
        限制最大尝试次数防止 token rotation 导致无限循环。
        """
        max_attempts = 20  # 防止无限循环
        externally_excluded = set(excluded_tokens or set())
        attempted_tokens: set[str] = set()
        # 控制流只保留两个出口，但最终是否能说“额度耗尽”必须谨慎：
        # 只要出现过非额度类失败，就说明不能断言全部账号都耗尽，应返回可重试的 unavailable。
        saw_remote_quota_exhausted = False
        saw_unavailable_failure = False
        for _attempt in range(max_attempts):
            try:
                access_token = self._acquire_next_candidate_token(
                    excluded_tokens=externally_excluded | attempted_tokens,
                    plan_type=plan_type,
                    source_type=source_type,
                    plan_types=plan_types,
                )
            except ImageAccountSelectionError:
                if attempted_tokens:
                    break
                raise
            attempted_tokens.add(access_token)
            try:
                if config.image_preflight_token_refresh_enabled:
                    access_token = self.refresh_access_token(
                        access_token,
                        force=True,
                        event="get_available_access_token:forced_preflight",
                        image_scope=True,
                        skip_if_image_busy=True,
                    )
                    attempted_tokens.add(access_token)
                account = self.fetch_remote_info(
                    access_token,
                    "get_available_access_token",
                    image_scope=True,
                )
            except Exception:
                # 预检失败（上游波动/网络/401 等）：这个号这次不可用，换下一个。
                # 401 已在 fetch_remote_info 内部走异常处理，这里不再二次分类。
                saw_unavailable_failure = True
                self.release_image_slot(access_token)
                continue
            # fetch_remote_info 内部可能因 token rotation 导致 access_token 变化，
            # 把新 token 也加入排除列表，防止重复尝试
            resolved = str((account or {}).get("access_token") or "")
            if resolved and resolved != access_token:
                attempted_tokens.add(resolved)
            if (
                    self._is_image_account_available(account or {})
                    and self._account_matches_plan_type(account or {}, plan_type)
                    and self._account_matches_any_plan_type(account or {}, plan_types)
                    and self._account_matches_source_type(account or {}, source_type)
            ):
                return str((account or {}).get("access_token") or access_token)
            if str((account or {}).get("status") or "") == "限流":
                saw_remote_quota_exhausted = True
            else:
                saw_unavailable_failure = True
            self.release_image_slot(access_token)
        if saw_remote_quota_exhausted and not saw_unavailable_failure:
            raise ImageAccountSelectionError(
                "quota_exhausted",
                f"all usable image accounts remote-confirmed quota exhausted after {len(attempted_tokens)} attempts",
            )
        raise ImageAccountSelectionError(
            "unavailable",
            f"no image account available after {len(attempted_tokens)} attempts",
        )

    def get_text_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        attempted = set(excluded_tokens or set())
        while True:
            with self._lock:
                candidates = [
                    token
                    for account in self._accounts.values()
                    if self._is_account_selectable(
                        account,
                        allow_limited=True,
                        allow_image_pending=True,
                    )
                       and (token := account.get("access_token") or "")
                       and token not in attempted
                ]
                if not candidates:
                    return ""
                access_token = candidates[self._index % len(candidates)]
                self._index += 1
            attempted.add(access_token)
            try:
                return self.refresh_access_token(access_token, event="get_text_access_token")
            except (TerminalRefreshTokenError, RefreshCredentialsChangedError):
                continue

    def mark_text_used(self, access_token: str) -> None:
        if not access_token:
            return
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._accounts[access_token] = account
            self._save_accounts()

    def remove_invalid_token(
        self,
        access_token: str,
        event: str,
        quiet: bool = False,
        remove: bool | None = None,
        error: str | None = None,
    ) -> bool:
        return self.handle_invalid_token(
            access_token,
            event,
            error=error,
            quiet=quiet,
            remove=remove,
            expected_access_token=access_token,
        )

    def handle_invalid_token(
        self,
        access_token: str,
        event: str,
        error: str | None = None,
        quiet: bool = False,
        remove: bool | None = None,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        expected_remote_check_marker: _RemoteCheckMarker | None = None,
        token_refresh_error: str | None = None,
    ) -> bool:
        """统一处理鉴权异常账号。

        口径固定为：先记录异常，再按“自动移除异常账号”配置删除或保留异常状态。
        """
        should_remove = config.auto_remove_invalid_accounts if remove is None else remove
        return self._apply_invalid_token_state(
            access_token,
            event,
            str(error or "invalid access token"),
            remove=bool(should_remove),
            expected_access_token=expected_access_token,
            expected_refresh_token=expected_refresh_token,
            expected_remote_check_marker=expected_remote_check_marker,
            token_refresh_error=token_refresh_error,
            quiet=quiet,
        )

    def get_account(self, access_token: str) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            account = self._accounts.get(access_token)
            return dict(account) if account else None

    def get_account_by_id(self, account_id: UUID | str) -> dict | None:
        try:
            lookup = str(UUID(str(account_id)))
        except (TypeError, ValueError, AttributeError):
            return None
        with self._lock:
            for account in self._accounts.values():
                if str(account.get("queue_account_id") or "") == lookup:
                    return dict(account)
        return None

    def list_image_account_candidates(
        self,
        plan_type: str | None = None,
        source_type: str | None = None,
        plan_types: set[str] | tuple[str, ...] | None = None,
        excluded_account_ids: set[UUID | str] | None = None,
    ) -> list[ImageAccountCandidate]:
        excluded = {str(value) for value in (excluded_account_ids or set())}
        with self._lock:
            return [
                ImageAccountCandidate(
                    account_id=UUID(str(item["queue_account_id"])),
                    access_token=str(item.get("access_token") or ""),
                    plan_type=str(item.get("type") or ""),
                    source_type=str(item.get("source_type") or ""),
                )
                for item in self._accounts.values()
                if self._is_image_account_available(item)
                and self._account_matches_plan_type(item, plan_type)
                and self._account_matches_any_plan_type(item, plan_types)
                and self._account_matches_source_type(item, source_type)
                and str(item.get("queue_account_id") or "") not in excluded
            ]

    def prepare_image_account(self, account_id: UUID | str) -> str:
        account = self.get_account_by_id(account_id)
        if account is None:
            raise ImageAccountSelectionError("unavailable", "image account no longer exists")
        access_token = str(account.get("access_token") or "")
        prepared = self.fetch_remote_info(
            access_token,
            "prepare_image_account",
            image_scope=True,
        )
        if not self._is_image_account_available(prepared or {}):
            kind = "quota_exhausted" if str((prepared or {}).get("status") or "") == "限流" else "unavailable"
            raise ImageAccountSelectionError(kind, "image account failed remote preflight")
        return str((prepared or {}).get("access_token") or access_token)

    def list_accounts(self) -> list[dict]:
        """返回所有账号的副本，并为每个账号附加当前图片在途数 image_inflight。

        image_inflight 为内存态并发计数(账号正在生成、尚未结束的图片数)。号池空闲时
        若某账号该值持续 > 0，说明其并发槽位泄漏、已被静默排除出调度，可借此在 UI 上诊断。
        """
        with self._lock:
            result = []
            for item in self._accounts.values():
                account = dict(item)
                token = account.get("access_token") or ""
                account["image_inflight"] = int(self._image_inflight.get(token, 0))
                result.append(account)
            return result

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "限流"
                   and item.get("last_remote_check_result") != "pending"
                   and (token := item.get("access_token") or "")
            ]

    def list_pending_auth_verification_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("last_remote_check_result") == "pending"
                and item.get("pending_auth_scope") == "image"
                and (token := str(item.get("access_token") or "").strip())
            ]

    def resume_pending_auth_verifications(self) -> int:
        tokens = self.list_pending_auth_verification_tokens()
        scheduled = 0
        for token in tokens:
            if self._schedule_account_refresh_after_image_failure(token):
                scheduled += 1
        return scheduled

    def _auto_remove_tokens_locked(
        self,
        *,
        remove_invalid: bool,
        remove_rate_limited: bool,
    ) -> tuple[list[str], list[str]]:
        invalid_tokens = [
            token
            for item in self._accounts.values()
            if remove_invalid
               and item.get("status") == "异常"
               and (token := item.get("access_token") or "")
        ]
        rate_limited_tokens = [
            token
            for item in self._accounts.values()
            if remove_rate_limited
               and item.get("status") == "限流"
               and (token := item.get("access_token") or "")
        ]
        return invalid_tokens, rate_limited_tokens

    def preview_auto_remove_accounts(
        self,
        *,
        remove_invalid: bool | None = None,
        remove_rate_limited: bool | None = None,
    ) -> dict[str, Any]:
        remove_invalid = config.auto_remove_invalid_accounts if remove_invalid is None else bool(remove_invalid)
        remove_rate_limited = (
            config.auto_remove_rate_limited_accounts
            if remove_rate_limited is None
            else bool(remove_rate_limited)
        )
        with self._lock:
            invalid_tokens, rate_limited_tokens = self._auto_remove_tokens_locked(
                remove_invalid=remove_invalid,
                remove_rate_limited=remove_rate_limited,
            )
        invalid = len(invalid_tokens)
        rate_limited = len(rate_limited_tokens)
        return {
            "dry_run": True,
            "invalid": invalid,
            "rate_limited": rate_limited,
            "total_removed": invalid + rate_limited,
            "auto_remove_invalid_accounts": remove_invalid,
            "auto_remove_rate_limited_accounts": remove_rate_limited,
        }

    def cleanup_auto_remove_accounts(
        self,
        *,
        remove_invalid: bool | None = None,
        remove_rate_limited: bool | None = None,
    ) -> dict[str, Any]:
        remove_invalid = config.auto_remove_invalid_accounts if remove_invalid is None else bool(remove_invalid)
        remove_rate_limited = (
            config.auto_remove_rate_limited_accounts
            if remove_rate_limited is None
            else bool(remove_rate_limited)
        )
        with self._lock:
            invalid_tokens, rate_limited_tokens = self._auto_remove_tokens_locked(
                remove_invalid=remove_invalid,
                remove_rate_limited=remove_rate_limited,
            )

        target_tokens = list(dict.fromkeys([*invalid_tokens, *rate_limited_tokens]))
        result = self.delete_accounts(target_tokens, return_items=False) if target_tokens else {"removed": 0}
        removed = int(result.get("removed") or 0)
        if removed:
            log_service.add(
                LOG_TYPE_ACCOUNT,
                "按账号自动移除策略清理账号",
                {
                    "removed": removed,
                    "invalid": len(invalid_tokens),
                    "rate_limited": len(rate_limited_tokens),
                },
            )
        return {
            "dry_run": False,
            "invalid": len(invalid_tokens),
            "rate_limited": len(rate_limited_tokens),
            "total_removed": removed,
            "auto_remove_invalid_accounts": remove_invalid,
            "auto_remove_rate_limited_accounts": remove_rate_limited,
        }

    def list_normal_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "正常"
                   and item.get("last_remote_check_result") != "pending"
                   and (token := item.get("access_token") or "")
            ]

    @classmethod
    def _pool_health_freshness_seconds(cls, freshness_seconds: int | float | None = None) -> int:
        if freshness_seconds is not None:
            try:
                return max(60, int(float(freshness_seconds)))
            except (TypeError, ValueError):
                pass
        return max(60, int(config.refresh_account_interval_minute) * 60)

    @classmethod
    def _remote_check_is_fresh(cls, account: dict, now: datetime, freshness_seconds: int) -> bool:
        checked_at = cls._parse_time(account.get("last_remote_checked_at"))
        return checked_at is not None and (now - checked_at).total_seconds() <= freshness_seconds

    @classmethod
    def _remote_check_attempt_is_recent(cls, account: dict, now: datetime, freshness_seconds: int) -> bool:
        attempted_at = cls._parse_time(account.get("last_remote_check_attempt_at"))
        return attempted_at is not None and (now - attempted_at).total_seconds() <= freshness_seconds

    @classmethod
    def _pool_health_metrics_from_accounts(
        cls,
        accounts: list[dict],
        *,
        now: datetime,
        freshness_seconds: int,
    ) -> dict[str, Any]:
        local_normal = [
            item
            for item in accounts
            if cls._is_account_selectable(item, allow_limited=False)
        ]
        confirmed_normal = [
            item
            for item in local_normal
            if cls._remote_check_is_fresh(item, now, freshness_seconds)
        ]
        latest_checked_at = ""
        for item in accounts:
            checked_at = cls._parse_time(item.get("last_remote_checked_at"))
            if checked_at is None:
                continue
            value = checked_at.isoformat()
            if not latest_checked_at or value > latest_checked_at:
                latest_checked_at = value
        return {
            "current_quota": sum(
                int(item.get("quota") or 0)
                for item in confirmed_normal
                if not item.get("image_quota_unknown")
            ),
            "current_available": len(confirmed_normal),
            "estimated_quota": sum(
                int(item.get("quota") or 0)
                for item in local_normal
                if not item.get("image_quota_unknown")
            ),
            "estimated_available": len(local_normal),
            "unconfirmed_available": max(0, len(local_normal) - len(confirmed_normal)),
            "unknown_quota_count": sum(1 for item in confirmed_normal if item.get("image_quota_unknown")),
            "pool_freshness_seconds": freshness_seconds,
            "pool_last_checked_at": latest_checked_at,
        }

    @classmethod
    def _pool_health_stale_tokens(
        cls,
        accounts: list[dict],
        *,
        now: datetime,
        freshness_seconds: int,
    ) -> list[str]:
        stale: list[tuple[float, str]] = []
        for item in accounts:
            token = str(item.get("access_token") or "").strip()
            if not token or item.get("status") != "正常":
                continue
            if cls._remote_check_is_fresh(item, now, freshness_seconds):
                continue
            if cls._remote_check_attempt_is_recent(item, now, freshness_seconds):
                continue
            checked_at = cls._parse_time(item.get("last_remote_checked_at"))
            sort_key = checked_at.timestamp() if checked_at else 0.0
            stale.append((sort_key, token))
        stale.sort(key=lambda item: item[0])
        return [token for _, token in stale]

    @staticmethod
    def _pool_health_target_reached(
        metrics: dict[str, Any],
        *,
        target_quota: int | None = None,
        target_available: int | None = None,
    ) -> bool:
        if target_quota is not None and int(metrics.get("current_quota") or 0) >= max(1, int(target_quota)):
            return True
        if target_available is not None and int(metrics.get("current_available") or 0) >= max(1, int(target_available)):
            return True
        return False

    def evaluate_account_pool(
        self,
        *,
        refresh_stale: bool = False,
        target_quota: int | None = None,
        target_available: int | None = None,
        freshness_seconds: int | float | None = None,
    ) -> dict[str, Any]:
        """Return registration-facing account pool metrics from remotely confirmed data.

        Local quota is useful for display, but registration stop decisions should not
        trust stale local quota.  This method refreshes only stale normal accounts,
        in small batches, until the requested target is confirmed or no eligible
        stale accounts remain.
        """
        freshness = self._pool_health_freshness_seconds(freshness_seconds)
        refreshed = 0
        refresh_errors: list[dict[str, Any]] = []

        while True:
            now = datetime.now(timezone.utc)
            accounts = self.list_accounts()
            metrics = self._pool_health_metrics_from_accounts(
                accounts,
                now=now,
                freshness_seconds=freshness,
            )
            if not refresh_stale:
                return {
                    **metrics,
                    "pool_refreshed": refreshed,
                    "pool_refresh_errors": refresh_errors,
                }
            if self._pool_health_target_reached(
                metrics,
                target_quota=target_quota,
                target_available=target_available,
            ):
                return {
                    **metrics,
                    "pool_refreshed": refreshed,
                    "pool_refresh_errors": refresh_errors,
                }

            stale_tokens = self._pool_health_stale_tokens(
                accounts,
                now=now,
                freshness_seconds=freshness,
            )
            if not stale_tokens:
                return {
                    **metrics,
                    "pool_refreshed": refreshed,
                    "pool_refresh_errors": refresh_errors,
                }

            batch = stale_tokens[: self._POOL_HEALTH_REFRESH_BATCH_SIZE]
            result = self.refresh_accounts(batch)
            refreshed += int(result.get("refreshed") or 0)
            refresh_errors.extend(result.get("errors") or [])

    @staticmethod
    def _account_payload_token(item: dict) -> str:
        return str(item.get("access_token") or item.get("accessToken") or "").strip()

    @staticmethod
    def _prepare_account_payload(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = AccountService._account_payload_token(item)
        if not access_token:
            return None
        payload = dict(item)
        payload.pop("accessToken", None)
        payload["access_token"] = access_token
        # CPA/Codex 导出文件里的 `type=codex` 是导出格式，不是号池套餐类型。
        if str(payload.get("type") or "").strip().lower() == "codex":
            payload["export_type"] = "codex"
            payload["source_type"] = "codex"
            payload.pop("type", None)
        if str(payload.get("export_type") or "").strip().lower() == "codex":
            payload["source_type"] = "codex"
        if payload.get("plan_type") and not payload.get("type"):
            payload["type"] = str(payload.get("plan_type") or "").strip()
        return payload

    def add_account_items(self, items: list[dict], return_items: bool = True) -> dict:
        payloads = [
            payload
            for item in items
            if (payload := self._prepare_account_payload(item)) is not None
        ]
        return self._add_account_payloads(payloads, return_items=return_items)

    def add_accounts(self, tokens: list[str], source_type: str = "web", return_items: bool = True) -> dict:
        tokens = list(dict.fromkeys(token for token in tokens if token))
        if not tokens:
            return {"added": 0, "skipped": 0, "items": self.list_accounts() if return_items else []}
        return self._add_account_payloads([
            {"access_token": token, "source_type": self._normalize_source_type(source_type)}
            for token in tokens
        ], return_items=return_items)

    def _add_account_payloads(self, payloads: list[dict], return_items: bool = True) -> dict:
        deduped: dict[str, dict] = {}
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            access_token = self._account_payload_token(payload)
            if not access_token:
                continue
            current = deduped.get(access_token, {})
            deduped[access_token] = {**current, **payload, "access_token": access_token}

        if not deduped:
            return {"added": 0, "skipped": 0, "items": self.list_accounts() if return_items else []}

        with self._lock:
            added = 0
            skipped = 0
            for access_token, payload in deduped.items():
                current = self._accounts.get(access_token)
                if current is None:
                    added += 1
                    self._cumulative_total += 1
                    self._save_cumulative_total()
                    current = {"created_at": self._now()}
                else:
                    skipped += 1
                incoming = dict(payload)
                if not incoming.get("created_at"):
                    incoming.pop("created_at", None)
                merged = {**current, **incoming, "access_token": access_token}
                incoming_type = self._normalize_account_type(incoming.get("type"))
                current_type = self._normalize_account_type(current.get("type"))
                merged["type"] = incoming_type or current_type
                account = self._normalize_account(merged)
                if account is not None:
                    self._accounts[access_token] = account
            self._save_accounts()
            items = [dict(item) for item in self._accounts.values()] if return_items else []
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def delete_accounts(self, tokens: list[str], return_items: bool = True) -> dict:
        target_set = set(token for token in tokens if token)
        if not target_set:
            return {"removed": 0, "items": self.list_accounts() if return_items else []}
        with self._lock:
            target_set = {self._resolve_access_token_locked(token) for token in target_set if token}
            removed = sum(self._accounts.pop(token, None) is not None for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            self._token_aliases = {
                old: new
                for old, new in self._token_aliases.items()
                if old not in target_set and new not in target_set
            }
            if removed:
                if self._accounts:
                    self._index %= len(self._accounts)
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = [dict(item) for item in self._accounts.values()] if return_items else []
        return {"removed": removed, "items": items}

    @staticmethod
    def _remote_check_marker(
        account: dict | None,
    ) -> _RemoteCheckMarker:
        item = account or {}
        return (
            str(item.get("last_remote_check_result") or ""),
            str(item.get("last_remote_check_attempt_at") or ""),
            str(item.get("last_remote_check_event") or ""),
            item.get("pending_auth_remove_invalid"),
            str(item.get("pending_auth_scope") or ""),
            str(item.get("pending_auth_verification_id") or ""),
        )

    def update_account(
        self,
        access_token: str,
        updates: dict,
        quiet: bool = False,
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        expected_remote_check_marker: _RemoteCheckMarker | None = None,
        preserve_disabled: bool = False,
    ) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return None
            if expected_access_token is not None and (
                access_token != str(expected_access_token or "").strip()
            ):
                return dict(current)
            if expected_refresh_token is not None and (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                return dict(current)
            if (
                expected_remote_check_marker is not None
                and self._remote_check_marker(current) != expected_remote_check_marker
            ):
                return dict(current)
            merged = {**current, **updates, "access_token": access_token}
            if preserve_disabled and current.get("status") == "禁用":
                merged["status"] = "禁用"
            if (
                current.get("status") == "限流"
                and "status" in updates
                and self._normalize_account_status(updates.get("status"), merged) == "正常"
                and "restore_at" not in updates
            ):
                merged["restore_at"] = None
            account = self._normalize_account(merged)
            if account is None:
                return None
            auto_remove_invalid = account.get("status") == "异常" and config.auto_remove_invalid_accounts
            auto_remove_limited = account.get("status") == "限流" and config.auto_remove_rate_limited_accounts
            if auto_remove_invalid or auto_remove_limited:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                message = "自动移除异常账号" if auto_remove_invalid else "自动移除额度耗尽账号"
                log_service.add(LOG_TYPE_ACCOUNT, message, {"token": anonymize_token(access_token)})
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            if not quiet:
                log_service.add(LOG_TYPE_ACCOUNT, "更新账号",
                                {"token": anonymize_token(access_token), "status": account.get("status")})
            return dict(account)
        return None

    def _record_refresh_success(
        self,
        access_token: str,
        updates: dict,
        event: str = "fetch_remote_info",
        *,
        expected_access_token: str,
        expected_refresh_token: str,
        expected_remote_check_marker: _RemoteCheckMarker,
    ) -> dict | None:
        now = datetime.now(timezone.utc).isoformat()
        return self.update_account(
            access_token,
            {
                **updates,
                "invalid_count": 0,
                "last_invalid_at": None,
                "last_refresh_error": None,
                "last_refresh_error_at": None,
                "last_remote_checked_at": now,
                "last_remote_check_attempt_at": now,
                "last_remote_check_error": None,
                "last_remote_check_error_at": None,
                "last_remote_check_event": event,
                "last_remote_check_result": "ok",
                "pending_auth_remove_invalid": None,
                "pending_auth_scope": None,
            },
            quiet=True,
            expected_access_token=expected_access_token,
            expected_refresh_token=expected_refresh_token,
            expected_remote_check_marker=expected_remote_check_marker,
            preserve_disabled=True,
        )

    def _record_remote_check_error(
        self,
        access_token: str,
        event: str,
        error: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        expected_remote_check_marker: _RemoteCheckMarker | None = None,
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return False
            if expected_access_token is not None and (
                access_token != str(expected_access_token or "").strip()
            ):
                return False
            if expected_refresh_token is not None and (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                return False
            if (
                expected_remote_check_marker is not None
                and self._remote_check_marker(current) != expected_remote_check_marker
            ):
                return False
            next_item = dict(current)
            next_item["last_remote_check_attempt_at"] = now
            next_item["last_remote_check_error"] = str(error or "remote check failed")
            next_item["last_remote_check_error_at"] = now
            next_item["last_remote_check_event"] = event
            current_result = str(current.get("last_remote_check_result") or "")
            next_item["last_remote_check_result"] = (
                "invalid" if current_result == "invalid" else "error"
            )
            next_item["pending_auth_remove_invalid"] = None
            next_item["pending_auth_scope"] = None
            account = self._normalize_account(next_item)
            if account is not None:
                self._accounts[access_token] = account
                self._save_accounts()
                return True
        return False

    def _apply_invalid_token_state(
        self,
        access_token: str,
        event: str,
        error: str,
        *,
        remove: bool,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        expected_remote_check_marker: _RemoteCheckMarker | None = None,
        token_refresh_error: str | None = None,
        quiet: bool = False,
    ) -> bool:
        _ = quiet
        now = datetime.now(timezone.utc)
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return False
            if expected_access_token is not None and (
                access_token != str(expected_access_token or "").strip()
            ):
                return False
            if expected_refresh_token is not None and (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                return False
            if (
                expected_remote_check_marker is not None
                and self._remote_check_marker(current) != expected_remote_check_marker
            ):
                return False

            terminal_already_recorded = bool(
                token_refresh_error
                and current.get("status") in {"异常", "禁用"}
                and current.get("last_remote_check_result") == "invalid"
                and str(current.get("last_token_refresh_error") or "")
                == str(token_refresh_error)
            )
            if terminal_already_recorded:
                account = dict(current)
                if not (remove and account.get("status") == "异常"):
                    return False
            else:
                next_item = dict(current)
                next_item["status"] = "禁用" if current.get("status") == "禁用" else "异常"
                next_item["quota"] = 0
                next_item["image_quota_unknown"] = True
                next_item["invalid_count"] = int(next_item.get("invalid_count") or 0) + 1
                next_item["last_invalid_at"] = now.isoformat()
                next_item["last_refresh_error"] = str(error or "invalid access token")
                next_item["last_refresh_error_at"] = now.isoformat()
                next_item["last_remote_checked_at"] = now.isoformat()
                next_item["last_remote_check_attempt_at"] = now.isoformat()
                next_item["last_remote_check_error"] = str(error or "invalid access token")
                next_item["last_remote_check_error_at"] = now.isoformat()
                next_item["last_remote_check_event"] = event
                next_item["last_remote_check_result"] = "invalid"
                next_item["pending_auth_remove_invalid"] = None
                next_item["pending_auth_scope"] = None
                if token_refresh_error:
                    next_item["last_token_refresh_error"] = str(token_refresh_error)
                    next_item["last_token_refresh_error_at"] = now.isoformat()
                account = self._normalize_account(next_item)
                if account is None:
                    return False

            final_status = str(account.get("status") or "异常")
            removed = bool(remove and final_status == "异常")
            if removed:
                self._accounts.pop(access_token, None)
                self._image_inflight.pop(access_token, None)
                self._token_aliases = {
                    old: new
                    for old, new in self._token_aliases.items()
                    if old != access_token and new != access_token
                }
                self._index = self._index % len(self._accounts) if self._accounts else 0
            else:
                self._accounts[access_token] = account
            self._save_accounts()

            if not terminal_already_recorded:
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "账号鉴权确认失效" if final_status == "异常" else "已禁用账号鉴权确认失效",
                    {
                        "source": event,
                        "token": anonymize_token(access_token),
                        "status": final_status,
                        "error": str(error or ""),
                    },
                )
            if removed:
                log_service.add(LOG_TYPE_ACCOUNT, "删除 1 个账号", {"removed": 1})
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "自动移除异常账号",
                    {"source": event, "token": anonymize_token(access_token), "error": str(error or "")},
                )
        return removed

    @staticmethod
    def _mark_remote_check_pending(
        account: dict,
        event: str,
        now: str,
        *,
        remove_invalid: bool | None = None,
        scope: str = "account",
    ) -> None:
        existing_scope = (
            str(account.get("pending_auth_scope") or "").strip().lower()
            if account.get("last_remote_check_result") == "pending"
            else ""
        )
        account["last_remote_check_result"] = "pending"
        account["last_remote_check_event"] = event
        account["last_remote_check_attempt_at"] = now
        account["last_remote_check_error"] = None
        account["last_remote_check_error_at"] = None
        account["pending_auth_verification_id"] = uuid4().hex
        account["pending_auth_scope"] = (
            "account"
            if existing_scope == "account" or scope != "image"
            else "image"
        )
        requested_remove = (
            config.auto_remove_invalid_accounts
            if remove_invalid is None
            else bool(remove_invalid)
        )
        existing_remove = account.get("pending_auth_remove_invalid")
        account["pending_auth_remove_invalid"] = bool(existing_remove) or requested_remove

    def schedule_auth_verification(
        self,
        access_token: str,
        event: str,
        *,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        remove_invalid: bool | None = None,
        scope: str = "account",
    ) -> bool:
        """Block a rejected account now and verify it in the background."""
        if not access_token:
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self._image_slot_condition:
            access_token = self._resolve_access_token_locked(access_token)
            current = self._accounts.get(access_token)
            if current is None:
                return False
            if expected_access_token is not None and access_token != expected_access_token:
                return False
            if expected_refresh_token is not None and (
                str(current.get("refresh_token") or "").strip()
                != str(expected_refresh_token or "").strip()
            ):
                return False
            next_item = dict(current)
            self._mark_remote_check_pending(
                next_item,
                event,
                now,
                remove_invalid=remove_invalid,
                scope=scope,
            )
            account = self._normalize_account(next_item)
            if account is None:
                return False
            self._accounts[access_token] = account
            self._save_accounts()
            self._image_slot_condition.notify_all()
        scheduled = self._schedule_account_refresh_after_image_failure(access_token, force=True)
        if not scheduled:
            self._record_remote_check_error(
                access_token,
                event,
                "Account verification could not be scheduled.",
            )
        return scheduled

    def _verify_pending_auth(self, access_token: str, event: str) -> None:
        active_token, refresh_token, account = self._credential_snapshot(access_token)
        if not account or account.get("last_remote_check_result") != "pending":
            return
        pending_remove = account.get("pending_auth_remove_invalid")
        remove_invalid = (
            config.auto_remove_invalid_accounts
            if pending_remove is None
            else self._bool_value(pending_remove)
        )
        initial_token = active_token
        initial_refresh_token = refresh_token
        initial_remote_check_marker = self._remote_check_marker(account)

        from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI

        def request_user_info(token: str) -> dict[str, Any]:
            with self._image_auth_slot():
                with OpenAIBackendAPI(token) as backend:
                    return backend.get_user_info()

        def reschedule_pending(token: str) -> None:
            current_token, _, current = self._credential_snapshot(token)
            if current and current.get("last_remote_check_result") == "pending":
                self._schedule_account_refresh_after_image_failure(
                    current_token,
                    force=True,
                )

        def snapshot_changed(token: str, refresh: str) -> bool:
            current_token, current_refresh, current = self._credential_snapshot(token)
            if current and (current_token, current_refresh) == (token, refresh):
                return False
            return True

        def request_user_info_for_snapshot(token: str, refresh: str) -> dict[str, Any]:
            try:
                result = request_user_info(token)
            except Exception as exc:
                if snapshot_changed(token, refresh):
                    raise RefreshCredentialsChangedError() from exc
                raise
            if snapshot_changed(token, refresh):
                raise RefreshCredentialsChangedError()
            return result

        try:
            result = request_user_info_for_snapshot(initial_token, initial_refresh_token)
        except InvalidAccessTokenError as initial_error:
            if not initial_refresh_token:
                self.handle_invalid_token(
                    initial_token,
                    event,
                    error=str(initial_error),
                    remove=remove_invalid,
                    expected_access_token=initial_token,
                    expected_refresh_token=initial_refresh_token,
                    expected_remote_check_marker=initial_remote_check_marker,
                )
                reschedule_pending(initial_token)
                return
            try:
                active_token = self.refresh_access_token(
                    initial_token,
                    force=True,
                    event=f"{event}:auth_recovery",
                    remove_invalid=remove_invalid,
                    raise_on_error=True,
                    image_scope=True,
                    expected_credentials=(initial_token, initial_refresh_token),
                )
            except TerminalRefreshTokenError:
                reschedule_pending(initial_token)
                return
            except Exception as exc:
                self._record_remote_check_error(
                    initial_token,
                    event,
                    str(exc),
                    expected_access_token=initial_token,
                    expected_refresh_token=initial_refresh_token,
                    expected_remote_check_marker=initial_remote_check_marker,
                )
                reschedule_pending(initial_token)
                return

            verification_token, verification_refresh_token, verification_account = (
                self._credential_snapshot(active_token)
            )
            if not verification_account:
                return
            verification_remote_check_marker = self._remote_check_marker(verification_account)
            try:
                result = request_user_info_for_snapshot(
                    verification_token,
                    verification_refresh_token,
                )
            except InvalidAccessTokenError as exc:
                self.handle_invalid_token(
                    verification_token,
                    event,
                    error=str(exc),
                    remove=remove_invalid,
                    expected_access_token=verification_token,
                    expected_refresh_token=verification_refresh_token,
                    expected_remote_check_marker=verification_remote_check_marker,
                )
                reschedule_pending(verification_token)
                return
            except Exception as exc:
                self._record_remote_check_error(
                    verification_token,
                    event,
                    str(exc),
                    expected_access_token=verification_token,
                    expected_refresh_token=verification_refresh_token,
                    expected_remote_check_marker=verification_remote_check_marker,
                )
                reschedule_pending(verification_token)
                return

            self._record_refresh_success(
                verification_token,
                result,
                event,
                expected_access_token=verification_token,
                expected_refresh_token=verification_refresh_token,
                expected_remote_check_marker=verification_remote_check_marker,
            )
            reschedule_pending(verification_token)
            return
        except Exception as exc:
            self._record_remote_check_error(
                initial_token,
                event,
                str(exc),
                expected_access_token=initial_token,
                expected_refresh_token=initial_refresh_token,
                expected_remote_check_marker=initial_remote_check_marker,
            )
            reschedule_pending(initial_token)
            return

        self._record_refresh_success(
            initial_token,
            result,
            event,
            expected_access_token=initial_token,
            expected_refresh_token=initial_refresh_token,
            expected_remote_check_marker=initial_remote_check_marker,
        )
        reschedule_pending(initial_token)

    def _refresh_account_after_image_failure(self, access_token: str) -> None:
        try:
            account = self.get_account(access_token) or {}
            event = str(account.get("last_remote_check_event") or "account_failure")
            if account.get("last_remote_check_result") == "pending":
                if account.get("pending_auth_scope") == "image":
                    self._verify_pending_auth(access_token, event)
                else:
                    self.fetch_remote_info(access_token, event)
            else:
                self.fetch_remote_info(access_token, event)
        except Exception as exc:
            log_service.add(
                LOG_TYPE_ACCOUNT,
                "鉴权失败后核验账号失败",
                {"token": anonymize_token(access_token), "error": str(exc)},
            )

    def _schedule_account_refresh_after_image_failure(self, access_token: str, *, force: bool = False) -> bool:
        if not access_token:
            return False
        now = time.monotonic()
        with self._lock:
            access_token = self._resolve_access_token_locked(access_token)
            account = self._accounts.get(access_token) or {}
            requested_scope = (
                "image" if account.get("pending_auth_scope") == "image" else "account"
            )
            with self._image_failure_refresh_lock:
                cutoff = now - self._IMAGE_FAILURE_REFRESH_DEDUP_SECONDS
                self._image_failure_refresh_started_at = {
                    token: started_at
                    for token, started_at in self._image_failure_refresh_started_at.items()
                    if token in self._image_failure_refresh_active or started_at >= cutoff
                }
                active_refresh_token = next(
                    (
                        token for token in self._image_failure_refresh_active
                        if self._resolve_access_token_locked(token) == access_token
                    ),
                    None,
                )
                pending_refresh_token = next(
                    (
                        token for token in self._image_failure_refresh_pending_set
                        if self._resolve_access_token_locked(token) == access_token
                    ),
                    None,
                )
                last_started_at = max(
                    (
                        started_at
                        for token, started_at in self._image_failure_refresh_started_at.items()
                        if self._resolve_access_token_locked(token) == access_token
                    ),
                    default=0.0,
                )
                if active_refresh_token is not None:
                    if force:
                        self._image_failure_refresh_rerun.add(active_refresh_token)
                    return True
                if pending_refresh_token is not None:
                    existing_scope = self._image_failure_refresh_pending_scopes.get(
                        pending_refresh_token,
                        "account",
                    )
                    self._image_failure_refresh_pending_scopes[pending_refresh_token] = (
                        "account"
                        if "account" in {existing_scope, requested_scope}
                        else "image"
                    )
                    return True
                if (
                    not force
                    and now - last_started_at < self._IMAGE_FAILURE_REFRESH_DEDUP_SECONDS
                ):
                    return False
                self._image_failure_refresh_pending.append(access_token)
                self._image_failure_refresh_pending_set.add(access_token)
                self._image_failure_refresh_pending_scopes[access_token] = requested_scope
        self._start_pending_image_failure_refreshes()
        return True

    def _start_pending_image_failure_refreshes(self) -> None:
        while True:
            with self._image_failure_refresh_lock:
                if not self._image_failure_refresh_pending:
                    return

                active_counts = {"account": 0, "image": 0}
                for token in self._image_failure_refresh_active:
                    scope = self._image_failure_refresh_active_scopes.get(token, "account")
                    active_counts[scope] = active_counts.get(scope, 0) + 1

                selected: tuple[str, str] | None = None
                for _ in range(len(self._image_failure_refresh_pending)):
                    token = self._image_failure_refresh_pending.popleft()
                    scope = self._image_failure_refresh_pending_scopes.get(token, "account")
                    limit = (
                        config.image_auth_refresh_concurrency
                        if scope == "image"
                        else self._IMAGE_FAILURE_REFRESH_MAX_CONCURRENT
                    )
                    if active_counts.get(scope, 0) < limit:
                        selected = (token, scope)
                        break
                    self._image_failure_refresh_pending.append(token)
                if selected is None:
                    return

                access_token, refresh_scope = selected
                self._image_failure_refresh_pending_set.discard(access_token)
                self._image_failure_refresh_pending_scopes.pop(access_token, None)
                self._image_failure_refresh_active.add(access_token)
                self._image_failure_refresh_active_scopes[access_token] = refresh_scope
                self._image_failure_refresh_started_at[access_token] = time.monotonic()

            def refresh(token: str = access_token) -> None:
                try:
                    self._refresh_account_after_image_failure(token)
                finally:
                    with self._lock:
                        resolved_token = self._resolve_access_token_locked(token)
                        account = self._accounts.get(resolved_token)
                        with self._image_failure_refresh_lock:
                            self._image_failure_refresh_active.discard(token)
                            self._image_failure_refresh_active_scopes.pop(token, None)
                            rerun_requested = token in self._image_failure_refresh_rerun
                            self._image_failure_refresh_rerun.discard(token)
                            if (
                                rerun_requested
                                and account is not None
                                and account.get("last_remote_check_result") == "pending"
                                and resolved_token not in self._image_failure_refresh_pending_set
                            ):
                                self._image_failure_refresh_pending.append(resolved_token)
                                self._image_failure_refresh_pending_set.add(resolved_token)
                                self._image_failure_refresh_pending_scopes[resolved_token] = (
                                    "image"
                                    if account.get("pending_auth_scope") == "image"
                                    else "account"
                                )
                    self._start_pending_image_failure_refreshes()

            (
                expected_access_token,
                expected_refresh_token,
                expected_account,
            ) = self._credential_snapshot(access_token)
            expected_remote_check_marker = self._remote_check_marker(expected_account)
            try:
                Thread(
                    target=refresh,
                    name="image-account-refresh",
                    daemon=True,
                ).start()
            except Exception as exc:
                with self._image_failure_refresh_lock:
                    self._image_failure_refresh_active.discard(access_token)
                    self._image_failure_refresh_active_scopes.pop(access_token, None)
                self._record_remote_check_error(
                    access_token,
                    "image_failure",
                    str(exc),
                    expected_access_token=expected_access_token,
                    expected_refresh_token=expected_refresh_token,
                    expected_remote_check_marker=expected_remote_check_marker,
                )
                log_service.add(
                    LOG_TYPE_ACCOUNT,
                    "image failure refresh scheduling failed",
                    {"token": anonymize_token(access_token), "error": str(exc)},
                )

    def mark_image_result(
        self,
        access_token: str,
        success: bool,
        *,
        failure: ImageFailure | None = None,
        quota_consumed: bool | None = None,
        capabilities: set[str] | tuple[str, ...] | None = None,
        expected_access_token: str | None = None,
        expected_refresh_token: str | None = None,
        release_slot: bool = True,
        idempotency_key: str = "",
    ) -> dict | None:
        # Retained as call metadata only; capability-specific account state is gone.
        _ = capabilities
        if not access_token:
            return None
        now = datetime.now(timezone.utc)
        should_verify_after_failure = False
        consumed_quota = success if quota_consumed is None else bool(quota_consumed)
        with self._image_slot_condition:
            access_token = self._resolve_access_token_locked(access_token)
            if release_slot:
                self._release_image_slot_locked(access_token)
            try:
                current = self._accounts.get(access_token)
                if current is None:
                    return None
                accounting_key = str(idempotency_key or "").strip()[:160]
                processed_keys = list(current.get("_image_quota_accounting_keys") or [])
                if accounting_key and accounting_key in processed_keys:
                    return dict(current)
                if expected_access_token is not None and access_token != expected_access_token:
                    return dict(current)
                if expected_refresh_token is not None and (
                    str(current.get("refresh_token") or "").strip()
                    != str(expected_refresh_token or "").strip()
                ):
                    return dict(current)
                next_item = dict(current)
                if accounting_key:
                    next_item["_image_quota_accounting_keys"] = (
                        processed_keys + [accounting_key]
                    )[-self._IMAGE_QUOTA_ACCOUNTING_KEY_LIMIT:]
                next_item["last_used_at"] = now.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                image_quota_unknown = bool(next_item.get("image_quota_unknown"))
                if success:
                    next_item["success"] = int(next_item.get("success") or 0) + 1
                if consumed_quota:
                    if not image_quota_unknown:
                        current_quota = max(0, int(next_item.get("quota") or 0))
                        next_item["quota"] = max(0, current_quota - 1)
                        if current_quota <= 1:
                            # 本地扣减到 0 只能说明“展示值需要远程刷新”，不能直接证明账号已限流。
                            # 下一次调度会进入远程预检，由 get_user_info 的结果决定是否写入“限流”。
                            next_item["image_quota_unknown"] = True
                            next_item["last_quota_estimated_empty_at"] = now.isoformat()
                    if next_item.get("status") == "限流":
                        # 上游已经消耗图片额度，说明远程额度已恢复。
                        next_item["status"] = "正常"
                        next_item["image_quota_unknown"] = True
                        next_item["restore_at"] = None
                if not success and failure is not None and failure.verify_account:
                    next_item["fail"] = int(next_item.get("fail") or 0) + 1
                    self._mark_remote_check_pending(
                        next_item,
                        "image_failure",
                        now.isoformat(),
                        scope="image",
                    )
                    should_verify_after_failure = True
                account = self._normalize_account(next_item)
                if account is None:
                    return None
                self._accounts[access_token] = account
                try:
                    self._save_accounts()
                except Exception:
                    self._accounts[access_token] = current
                    raise
                result = dict(account)
            finally:
                self._image_slot_condition.notify_all()
        if should_verify_after_failure:
            scheduled = self._schedule_account_refresh_after_image_failure(
                access_token,
                force=True,
            )
            if not scheduled:
                self._record_remote_check_error(
                    access_token,
                    "image_failure",
                    "Account verification could not be scheduled.",
                )
        return result

    def record_managed_image_result(
        self,
        account_id: UUID | str,
        success: bool,
        failure: ImageFailure | None = None,
        error: BaseException | str | None = None,
        quota_consumed: bool | None = None,
        idempotency_key: str = "",
    ) -> dict | None:
        account = self.get_account_by_id(account_id)
        if account is None:
            return None
        resolved_failure = failure
        if not success and resolved_failure is None and error is not None:
            resolved_failure = (
                classify_image_exception(error)
                if isinstance(error, BaseException)
                else image_failure("upstream_error", raw_detail=str(error))
            )
        return self.mark_image_result(
            str(account.get("access_token") or ""),
            success,
            failure=resolved_failure,
            quota_consumed=quota_consumed,
            release_slot=False,
            idempotency_key=idempotency_key,
        )

    def fetch_remote_info(
        self,
        access_token: str,
        event: str = "fetch_remote_info",
        remove_invalid: bool | None = None,
        *,
        image_scope: bool = False,
    ) -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")

        refresh_kwargs = {
            "event": f"{event}:preflight",
            "remove_invalid": remove_invalid,
        }
        if image_scope:
            refresh_kwargs["image_scope"] = True
        active_token = self.refresh_access_token(access_token, **refresh_kwargs)
        from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI

        def request_user_info(token: str) -> dict[str, Any]:
            if image_scope:
                with self._image_auth_slot():
                    with OpenAIBackendAPI(token) as backend:
                        return backend.get_user_info()
            with OpenAIBackendAPI(token) as backend:
                return backend.get_user_info()

        request_token, request_refresh_token, request_account = self._credential_snapshot(active_token)
        if not request_account:
            raise RefreshCredentialsChangedError()
        successful_snapshot = (request_token, request_refresh_token)
        successful_remote_check_marker = self._remote_check_marker(request_account)
        try:
            result = request_user_info(request_token)
        except InvalidAccessTokenError as exc:
            rejected_error: InvalidAccessTokenError | None = exc
            rejected_snapshot = (request_token, request_refresh_token)
            current_token, current_refresh_token, current_account = self._credential_snapshot(request_token)
            current_snapshot = (current_token, current_refresh_token)
            current_remote_check_marker = self._remote_check_marker(current_account)
            if current_account and current_snapshot != rejected_snapshot:
                try:
                    result = request_user_info(current_token)
                except InvalidAccessTokenError as current_exc:
                    rejected_error = current_exc
                    rejected_snapshot = current_snapshot
                except Exception as current_exc:
                    self._record_remote_check_error(
                        current_token,
                        event,
                        str(current_exc),
                        expected_access_token=current_token,
                        expected_refresh_token=current_refresh_token,
                        expected_remote_check_marker=current_remote_check_marker,
                    )
                    raise
                else:
                    successful_snapshot = current_snapshot
                    successful_remote_check_marker = current_remote_check_marker
                    active_token = current_token
                    rejected_error = None

            if rejected_error is not None:
                if image_scope:
                    self.schedule_auth_verification(
                        rejected_snapshot[0],
                        event,
                        expected_access_token=rejected_snapshot[0],
                        expected_refresh_token=rejected_snapshot[1],
                        remove_invalid=remove_invalid,
                        scope="image",
                    )
                    raise rejected_error

                before_refresh = self.get_account(rejected_snapshot[0]) or {}
                before_refresh_at = str(before_refresh.get("last_token_refresh_at") or "")
                before_error_at = str(before_refresh.get("last_token_refresh_error_at") or "")
                try:
                    active_token = self.refresh_access_token(
                        rejected_snapshot[0],
                        force=True,
                        event=f"{event}:invalid_access_token",
                        remove_invalid=remove_invalid,
                    )
                except (TerminalRefreshTokenError, RefreshCredentialsChangedError):
                    raise
                except Exception as refresh_exc:
                    self._record_remote_check_error(
                        rejected_snapshot[0],
                        event,
                        str(refresh_exc),
                        expected_access_token=rejected_snapshot[0],
                        expected_refresh_token=rejected_snapshot[1],
                        expected_remote_check_marker=current_remote_check_marker,
                    )
                    raise

                after_refresh = self.get_account(active_token or rejected_snapshot[0]) or {}
                after_refresh_at = str(after_refresh.get("last_token_refresh_at") or "")
                after_error_at = str(after_refresh.get("last_token_refresh_error_at") or "")
                refresh_failed = bool(after_error_at and after_error_at != before_error_at)
                refresh_succeeded = bool(after_refresh_at and after_refresh_at != before_refresh_at)
                if refresh_failed and not refresh_succeeded:
                    self._record_remote_check_error(
                        rejected_snapshot[0],
                        event,
                        str(
                            after_refresh.get("last_token_refresh_error")
                            or "refresh token failed"
                        ),
                        expected_access_token=rejected_snapshot[0],
                        expected_refresh_token=rejected_snapshot[1],
                        expected_remote_check_marker=current_remote_check_marker,
                    )
                    raise rejected_error

                verification_token, verification_refresh_token, verification_account = (
                    self._credential_snapshot(active_token)
                )
                if not verification_account:
                    raise RefreshCredentialsChangedError()
                verification_remote_check_marker = self._remote_check_marker(verification_account)
                try:
                    result = request_user_info(verification_token)
                except InvalidAccessTokenError as retry_exc:
                    self.handle_invalid_token(
                        verification_token,
                        event,
                        error=str(retry_exc),
                        remove=remove_invalid,
                        expected_access_token=verification_token,
                        expected_refresh_token=verification_refresh_token,
                        expected_remote_check_marker=verification_remote_check_marker,
                    )
                    raise
                except Exception as retry_exc:
                    self._record_remote_check_error(
                        verification_token,
                        event,
                        str(retry_exc),
                        expected_access_token=verification_token,
                        expected_refresh_token=verification_refresh_token,
                        expected_remote_check_marker=verification_remote_check_marker,
                    )
                    raise
                active_token = verification_token
                successful_snapshot = (verification_token, verification_refresh_token)
                successful_remote_check_marker = verification_remote_check_marker
        except Exception as exc:
            self._record_remote_check_error(
                request_token,
                event,
                str(exc),
                expected_access_token=request_token,
                expected_refresh_token=request_refresh_token,
                expected_remote_check_marker=successful_remote_check_marker,
            )
            raise

        updated = self._record_refresh_success(
            active_token,
            result,
            event,
            expected_access_token=successful_snapshot[0],
            expected_refresh_token=successful_snapshot[1],
            expected_remote_check_marker=successful_remote_check_marker,
        )
        if updated is not None:
            return updated
        # update_account 可能因为“自动移除额度耗尽账号”删除了远程确认限流的账号。
        # 调用方仍需要知道本次预检的真实结果，不能把它混成普通预检失败。
        if str(result.get("status") or "") == "\u9650\u6d41" and config.auto_remove_rate_limited_accounts:
            return {**result, "access_token": active_token, "_removed_after_refresh": True}
        # A deleted account must not leak its stale token back into the selection path.
        raise RefreshCredentialsChangedError()

    # ---- 刷新进度追踪 ----

    def _progress_lock_path(self) -> Path:
        return self._refresh_progress_file.with_name(f"{self._refresh_progress_file.name}.lock")

    def _load_refresh_progress_records_locked(self) -> dict[str, dict]:
        data = read_json_object(self._refresh_progress_file, name="account_refresh_progress.json")
        records = data.get("items") if isinstance(data.get("items"), dict) else {}
        now = time.time()
        normalized: dict[str, dict] = {}
        for progress_id, value in records.items():
            if not isinstance(value, dict):
                continue
            try:
                expires_at = float(value.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at and expires_at <= now:
                continue
            normalized[str(progress_id)] = dict(value)
        return normalized

    def _save_refresh_progress_records_locked(self, records: dict[str, dict]) -> None:
        write_json_file(
            self._refresh_progress_file,
            {"version": 1, "items": records},
            backup=False,
        )

    def _update_refresh_progress_record(self, progress_id: str, updater) -> dict | None:
        clean_id = str(progress_id or "").strip()
        if not clean_id:
            return None
        with self._refresh_progress_lock, file_lock(self._progress_lock_path()):
            records = self._load_refresh_progress_records_locked()
            progress = records.get(clean_id)
            updated = updater(progress)
            if updated is None:
                records.pop(clean_id, None)
                self._refresh_progress.pop(clean_id, None)
                self._save_refresh_progress_records_locked(records)
                return None
            updated = dict(updated)
            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            updated["expires_at"] = time.time() + ACCOUNT_REFRESH_PROGRESS_TTL_SECONDS
            records[clean_id] = updated
            self._refresh_progress.clear()
            self._refresh_progress.update(records)
            self._save_refresh_progress_records_locked(records)
            return dict(updated)

    def init_refresh_progress(self, progress_id: str, total: int) -> None:
        """初始化刷新进度记录。"""
        now_iso = datetime.now(timezone.utc).isoformat()

        def init(_: dict | None) -> dict:
            return {
                "total": total,
                "processed": 0,
                "done": False,
                "error": None,
                "status_counts": {"正常": 0, "限流": 0, "异常": 0, "禁用": 0},
                "total_quota": 0,
                "created_at": now_iso,
                "updated_at": now_iso,
            }

        self._update_refresh_progress_record(progress_id, init)

    def update_refresh_progress(self, progress_id: str, token: str, result_account: dict | None = None) -> None:
        """刷新单个账号后，更新进度计数。"""
        account = result_account or self.get_account(token)
        status = str(account.get("status") or "正常").strip() if account else "异常"
        quota = max(0, int(account.get("quota") or 0)) if account else 0

        def update(progress: dict | None) -> dict | None:
            if progress is None:
                return None
            progress["processed"] += 1
            progress["status_counts"][status] = progress["status_counts"].get(status, 0) + 1
            progress["total_quota"] += quota
            return progress

        self._update_refresh_progress_record(progress_id, update)

    @staticmethod
    def _refresh_progress_account_item(account: dict) -> dict:
        item = {
            str(key): value
            for key, value in dict(account or {}).items()
            if not is_account_response_hidden_field(key)
        }
        token = str((account or {}).get("access_token") or "").strip()
        if token:
            item["access_token_preview"] = anonymize_token(token)
        return item

    @classmethod
    def _sanitize_refresh_progress_result(cls, result: dict | None) -> dict | None:
        if not isinstance(result, dict):
            return result
        sanitized = dict(result)
        items = sanitized.get("items")
        if isinstance(items, list):
            sanitized["items"] = [
                cls._refresh_progress_account_item(item)
                for item in items
                if isinstance(item, dict)
            ]
        return sanitized

    def finish_refresh_progress(self, progress_id: str, result: dict | None = None, error: str | None = None) -> None:
        """标记刷新完成。"""
        def finish(progress: dict | None) -> dict | None:
            if progress is None:
                return None
            progress["done"] = True
            progress["result"] = self._sanitize_refresh_progress_result(result)
            if error:
                progress["error"] = error
            return progress

        self._update_refresh_progress_record(progress_id, finish)

    def get_refresh_progress(self, progress_id: str) -> dict | None:
        """查询刷新进度。"""
        clean_id = str(progress_id or "").strip()
        if not clean_id:
            return None
        with self._refresh_progress_lock, file_lock(self._progress_lock_path()):
            records = self._load_refresh_progress_records_locked()
            self._refresh_progress.clear()
            self._refresh_progress.update(records)
            progress = records.get(clean_id)
            if not progress:
                return None
            sanitized = dict(progress)
            if "result" in sanitized:
                sanitized["result"] = self._sanitize_refresh_progress_result(sanitized.get("result"))
            return sanitized

    def clean_refresh_progress(self, progress_id: str) -> None:
        """清理过期进度记录。"""
        clean_id = str(progress_id or "").strip()
        if not clean_id:
            return
        with self._refresh_progress_lock, file_lock(self._progress_lock_path()):
            records = self._load_refresh_progress_records_locked()
            records.pop(clean_id, None)
            self._refresh_progress.pop(clean_id, None)
            self._save_refresh_progress_records_locked(records)
    def refresh_accounts(
        self,
        access_tokens: list[str],
        progress_id: str | None = None,
        remove_invalid: bool | None = None,
    ) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            items = self.list_accounts()
            result = {"refreshed": 0, "errors": [], "items": items}
            if progress_id:
                self.finish_refresh_progress(progress_id, result)
            return result

        refreshed = 0
        errors = []
        max_workers = min(10, len(access_tokens))

        if progress_id:
            self.init_refresh_progress(progress_id, len(access_tokens))

        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futures = {
                executor.submit(self.fetch_remote_info, token, "refresh_accounts", remove_invalid): token
                for token in access_tokens
            }
            for future in as_completed(futures):
                token = futures[future]
                result_account = None
                try:
                    account = future.result()
                except (KeyboardInterrupt, SystemExit):
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise
                except Exception as exc:
                    failure = classify_image_exception(exc)
                    errors.append({
                        "token": anonymize_token(token),
                        "error": str(exc),
                        **failure.diagnostic_fields(),
                    })
                else:
                    if account is not None:
                        refreshed += 1
                        result_account = account

                if progress_id:
                    self.update_refresh_progress(progress_id, token, result_account)
        except (KeyboardInterrupt, SystemExit):
            if progress_id:
                self.finish_refresh_progress(progress_id, error="cancelled")
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True, cancel_futures=True)

        result = {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
        }

        if progress_id:
            self.finish_refresh_progress(progress_id, result)

        return result

    def build_export_items(self, access_tokens: list[str] | None = None) -> list[dict[str, Any]]:
        target_tokens = set(token for token in (access_tokens or []) if token)
        with self._lock:
            accounts = [
                dict(item)
                for item in self._accounts.values()
                if not target_tokens or str(item.get("access_token") or "") in target_tokens
            ]

        items: list[dict[str, Any]] = []
        for account in accounts:
            access_token = str(account.get("access_token") or "").strip()
            refresh_token = str(account.get("refresh_token") or "").strip()
            id_token = str(account.get("id_token") or "").strip()
            if not access_token or not refresh_token or not id_token:
                continue

            access_payload = self._decode_jwt_payload(access_token)
            id_payload = self._decode_jwt_payload(id_token)
            auth_claim = access_payload.get("https://api.openai.com/auth")
            auth_claim = auth_claim if isinstance(auth_claim, dict) else {}
            profile_claim = access_payload.get("https://api.openai.com/profile")
            profile_claim = profile_claim if isinstance(profile_claim, dict) else {}

            email = (
                str(account.get("email") or "").strip()
                or str(profile_claim.get("email") or "").strip()
                or str(id_payload.get("email") or "").strip()
            )
            account_id = (
                str(account.get("account_id") or "").strip()
                or str(auth_claim.get("chatgpt_account_id") or "").strip()
                or str(account.get("user_id") or "").strip()
            )
            item = {
                "type": str(account.get("export_type") or "codex"),
                "email": email,
                "account_id": account_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": id_token,
                "expired": self._timestamp_to_iso(access_payload.get("exp")),
                "last_refresh": self._timestamp_to_iso(access_payload.get("iat")),
            }
            password = str(account.get("password") or "").strip()
            if password:
                item["password"] = password
            fp = account.get("fp")
            if isinstance(fp, dict) and fp:
                item["fp"] = deepcopy(fp)
            items.append(item)
        return items

    def get_stats(self) -> dict:
        with self._lock:
            items = list(self._accounts.values())
        total = len(items)
        active = sum(1 for a in items if a.get("status") == "正常")
        limited = sum(1 for a in items if a.get("status") == "限流")
        abnormal = sum(1 for a in items if a.get("status") == "异常")
        disabled = sum(1 for a in items if a.get("status") == "禁用")
        normal_items = [a for a in items if a.get("status") == "正常"]
        total_quota = sum(max(0, int(a.get("quota") or 0)) for a in normal_items)
        unlimited = sum(1 for a in normal_items if self._is_unlimited_image_quota_account(a))
        unknown_quota = sum(
            1
            for a in normal_items
            if (
                bool(a.get("image_quota_unknown"))
                or (not bool(a.get("image_quota_unknown")) and max(0, int(a.get("quota") or 0)) <= 0)
            )
            and not self._is_unlimited_image_quota_account(a)
        )
        total_success = sum(int(a.get("success") or 0) for a in items)
        total_fail = sum(int(a.get("fail") or 0) for a in items)
        by_type = {}
        for a in items:
            t = a.get("type") or "unknown"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total": total,
            "cumulative_total": self._cumulative_total,
            "active": active,
            "limited": limited,
            "abnormal": abnormal,
            "disabled": disabled,
            "total_quota": total_quota,
            "unlimited_quota_count": unlimited,
            "unknown_quota_count": unknown_quota,
            "total_success": total_success,
            "total_fail": total_fail,
            "by_type": by_type,
        }

    def account_health(self) -> dict:
        stats = self.get_stats()
        return {
            "healthy": stats["active"] > 0 or stats["unlimited_quota_count"] > 0,
            "status": "ok" if stats["active"] > 0 else "degraded",
            **stats,
        }


account_service = AccountService(config.get_storage_backend())
