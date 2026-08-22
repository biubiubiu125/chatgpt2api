from __future__ import annotations

import copy
from dataclasses import dataclass
import os
import threading
from pathlib import Path
import time

from services.file_lock import file_lock
from services.json_file import read_json_object, write_json_file
from services.storage.base import StorageBackend

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = Path(os.getenv("CHATGPT2API_CONFIG_FILE") or BASE_DIR / "config.json").expanduser()
VERSION_FILE = BASE_DIR / "VERSION"
BACKUP_STATE_FILE = DATA_DIR / "backup_state.json"
SECRET_MASK = "********"

DEFAULT_BACKUP_INCLUDE = {
    "config": True,
    "register": True,
    "cpa": True,
    "sub2api": True,
    "logs": True,
    "dashboard_metrics": True,
    "image_tasks": True,
    "accounts_snapshot": True,
    "auth_keys_snapshot": True,
    "images": True,
}

DEFAULT_IMAGE_STORAGE = {
    "enabled": False,
    "mode": "local",
    "webdav_url": "",
    "webdav_username": "",
    "webdav_password": "",
    "webdav_root_path": "chatgpt2api/images",
    "private_webdav_url": "",
    "private_webdav_username": "",
    "private_webdav_password": "",
    "private_webdav_root_path": "chatgpt2api/private",
    "public_base_url": "",
}

DEFAULT_CHAT_COMPLETION_CACHE = {
    "enabled": True,
    "ttl_seconds": 60,
    "max_entries": 256,
    "dedupe_inflight": True,
    "stream_cache": True,
    "normalize_messages": True,
    "drop_adjacent_duplicates": True,
    "drop_assistant_history": False,
}

DEFAULT_RUNTIME_CAPACITY = {
    "uvicorn_workers": 1,
    "text_concurrency_limit": 80,
    "image_concurrency_limit": 2000,
    "request_queue_timeout_seconds": 0.0,
}

DEFAULT_QUOTA_LIMITS = {
    "enabled": True,
    "fast_daily_limit": -1,
    "thinking_daily_limit": -1,
    "pro_daily_limit": -1,
    "image_daily_limit": -1,
    "music_daily_limit": -1,
    "video_daily_limit": -1,
}

DEFAULT_PROXY_RUNTIME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

DEFAULT_PROXY_RUNTIME = {
    "enabled": False,
    "egress_mode": "direct",
    "proxy_url": "",
    "resource_proxy_url": "",
    "skip_ssl_verify": False,
    "reset_session_status_codes": [403],
    "clearance": {
        "enabled": False,
        "mode": "none",
        "cf_cookies": "",
        "cf_clearance": "",
        "user_agent": DEFAULT_PROXY_RUNTIME_USER_AGENT,
        "browser": "chrome",
        "flaresolverr_url": "",
        "timeout_sec": 60,
        "refresh_interval": 3600,
        "warm_up_on_start": False,
    },
}

DEFAULT_THIRD_PARTY_APPS = {
    "infinite_canvas": {
        "enabled": False,
        "url": "https://canvas.best",
    },
}


def _normalize_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


def _normalize_positive_int(value: object, default: int, minimum: int = 0) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(minimum, normalized)


def _normalize_non_negative_float(value: object, default: float) -> float:
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    return max(0.0, normalized)


def _normalize_runtime_capacity_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "uvicorn_workers": _normalize_positive_int(
            source.get("uvicorn_workers"),
            int(DEFAULT_RUNTIME_CAPACITY["uvicorn_workers"]),
            1,
        ),
        "text_concurrency_limit": _normalize_positive_int(
            source.get("text_concurrency_limit"),
            int(DEFAULT_RUNTIME_CAPACITY["text_concurrency_limit"]),
            1,
        ),
        "image_concurrency_limit": _normalize_positive_int(
            source.get("image_concurrency_limit"),
            int(DEFAULT_RUNTIME_CAPACITY["image_concurrency_limit"]),
            1,
        ),
        "request_queue_timeout_seconds": _normalize_non_negative_float(
            source.get("request_queue_timeout_seconds"),
            float(DEFAULT_RUNTIME_CAPACITY["request_queue_timeout_seconds"]),
        ),
    }


def _normalize_quota_limits_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    normalized: dict[str, object] = {
        "enabled": _normalize_bool(source.get("enabled"), bool(DEFAULT_QUOTA_LIMITS["enabled"])),
    }
    for key in (
        "fast_daily_limit",
        "thinking_daily_limit",
        "pro_daily_limit",
        "image_daily_limit",
        "music_daily_limit",
        "video_daily_limit",
    ):
        try:
            limit = int(source.get(key))
        except (OverflowError, TypeError, ValueError):
            limit = int(DEFAULT_QUOTA_LIMITS[key])
        normalized[key] = max(-1, limit)
    return normalized


def _normalize_backup_include(value: object) -> dict[str, bool]:
    source = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_BACKUP_INCLUDE)
    for key in normalized:
        normalized[key] = _normalize_bool(source.get(key), normalized[key])
    return normalized


def _normalize_backup_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "enabled": _normalize_bool(source.get("enabled"), False),
        "provider": "cloudflare_r2",
        "account_id": str(source.get("account_id") or "").strip(),
        "access_key_id": str(source.get("access_key_id") or "").strip(),
        "secret_access_key": str(source.get("secret_access_key") or "").strip(),
        "bucket": str(source.get("bucket") or "").strip(),
        "prefix": str(source.get("prefix") or "backups").strip().strip("/") or "backups",
        "interval_minutes": _normalize_positive_int(source.get("interval_minutes"), 360, 1),
        "rotation_keep": _normalize_positive_int(source.get("rotation_keep"), 10, 0),
        "encrypt": _normalize_bool(source.get("encrypt"), True),
        "passphrase": str(source.get("passphrase") or "").strip(),
        "include": _normalize_backup_include(source.get("include")),
    }


def _normalize_backup_state(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "last_started_at": str(source.get("last_started_at") or "").strip() or None,
        "last_finished_at": str(source.get("last_finished_at") or "").strip() or None,
        "last_status": str(source.get("last_status") or "idle").strip() or "idle",
        "last_error": str(source.get("last_error") or "").strip() or None,
        "last_object_key": str(source.get("last_object_key") or "").strip() or None,
    }


def _normalize_image_storage_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    mode = str(source.get("mode") or "local").strip().lower()
    if mode not in {"local", "webdav", "both"}:
        mode = "local"
    enabled = _normalize_bool(source.get("enabled"), False)
    if not enabled:
        mode = "local"
    root_path = str(source.get("webdav_root_path") or DEFAULT_IMAGE_STORAGE["webdav_root_path"]).strip().strip("/")
    private_root_path = str(
        source.get("private_webdav_root_path") or DEFAULT_IMAGE_STORAGE["private_webdav_root_path"]
    ).strip().strip("/")
    return {
        "enabled": enabled,
        "mode": mode,
        "webdav_url": str(source.get("webdav_url") or "").strip().rstrip("/"),
        "webdav_username": str(source.get("webdav_username") or "").strip(),
        "webdav_password": str(source.get("webdav_password") or "").strip(),
        "webdav_root_path": root_path or str(DEFAULT_IMAGE_STORAGE["webdav_root_path"]),
        "private_webdav_url": str(source.get("private_webdav_url") or "").strip().rstrip("/"),
        "private_webdav_username": str(source.get("private_webdav_username") or "").strip(),
        "private_webdav_password": str(source.get("private_webdav_password") or "").strip(),
        "private_webdav_root_path": private_root_path or str(DEFAULT_IMAGE_STORAGE["private_webdav_root_path"]),
        "public_base_url": str(source.get("public_base_url") or "").strip().rstrip("/"),
    }


def _normalize_chat_completion_cache_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        "enabled": _normalize_bool(source.get("enabled"), DEFAULT_CHAT_COMPLETION_CACHE["enabled"]),
        "ttl_seconds": _normalize_positive_int(
            source.get("ttl_seconds"),
            int(DEFAULT_CHAT_COMPLETION_CACHE["ttl_seconds"]),
            0,
        ),
        "max_entries": _normalize_positive_int(
            source.get("max_entries"),
            int(DEFAULT_CHAT_COMPLETION_CACHE["max_entries"]),
            1,
        ),
        "dedupe_inflight": _normalize_bool(
            source.get("dedupe_inflight"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["dedupe_inflight"]),
        ),
        "stream_cache": _normalize_bool(
            source.get("stream_cache"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["stream_cache"]),
        ),
        "normalize_messages": _normalize_bool(
            source.get("normalize_messages"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["normalize_messages"]),
        ),
        "drop_adjacent_duplicates": _normalize_bool(
            source.get("drop_adjacent_duplicates"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["drop_adjacent_duplicates"]),
        ),
        "drop_assistant_history": _normalize_bool(
            source.get("drop_assistant_history"),
            bool(DEFAULT_CHAT_COMPLETION_CACHE["drop_assistant_history"]),
        ),
    }


def _normalize_status_codes(value: object) -> list[int]:
    items = value if isinstance(value, list) else DEFAULT_PROXY_RUNTIME["reset_session_status_codes"]
    normalized: list[int] = []
    for item in items:
        if isinstance(item, bool):
            continue
        try:
            status = int(item)
        except (OverflowError, TypeError, ValueError):
            continue
        if 100 <= status <= 599 and status not in normalized:
            normalized.append(status)
    if not normalized:
        return list(DEFAULT_PROXY_RUNTIME["reset_session_status_codes"])
    return normalized


def _normalize_proxy_runtime_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    default_clearance = DEFAULT_PROXY_RUNTIME["clearance"]
    clearance_source = source.get("clearance") if isinstance(source.get("clearance"), dict) else {}

    egress_mode = str(source.get("egress_mode") or DEFAULT_PROXY_RUNTIME["egress_mode"]).strip().lower()
    if egress_mode not in {"direct", "single_proxy"}:
        egress_mode = str(DEFAULT_PROXY_RUNTIME["egress_mode"])

    clearance_mode = str(clearance_source.get("mode") or default_clearance["mode"]).strip().lower()
    if clearance_mode not in {"none", "manual", "flaresolverr"}:
        clearance_mode = str(default_clearance["mode"])

    user_agent = str(default_clearance["user_agent"])
    browser = str(clearance_source.get("browser") or default_clearance["browser"]).strip()

    existing_clearance_cookies = str(source.get("_existing_cf_cookies") or "").strip()
    existing_cf_clearance = str(source.get("_existing_cf_clearance") or "").strip()
    cf_cookies = str(clearance_source.get("cf_cookies") or "").strip()
    cf_clearance = str(clearance_source.get("cf_clearance") or "").strip()
    if not cf_cookies and _normalize_bool(clearance_source.get("has_cf_cookies"), False):
        cf_cookies = existing_clearance_cookies
    if not cf_clearance and _normalize_bool(clearance_source.get("has_cf_clearance"), False):
        cf_clearance = existing_cf_clearance

    return {
        "enabled": _normalize_bool(source.get("enabled"), bool(DEFAULT_PROXY_RUNTIME["enabled"])),
        "egress_mode": egress_mode,
        "proxy_url": str(source.get("proxy_url") or "").strip(),
        "resource_proxy_url": str(source.get("resource_proxy_url") or "").strip(),
        "skip_ssl_verify": _normalize_bool(
            source.get("skip_ssl_verify"),
            bool(DEFAULT_PROXY_RUNTIME["skip_ssl_verify"]),
        ),
        "reset_session_status_codes": _normalize_status_codes(source.get("reset_session_status_codes")),
        "clearance": {
            "enabled": _normalize_bool(clearance_source.get("enabled"), bool(default_clearance["enabled"])),
            "mode": clearance_mode,
            "cf_cookies": cf_cookies,
            "cf_clearance": cf_clearance,
            "user_agent": user_agent or str(default_clearance["user_agent"]),
            "browser": browser or str(default_clearance["browser"]),
            "flaresolverr_url": str(clearance_source.get("flaresolverr_url") or "").strip(),
            "timeout_sec": _normalize_positive_int(
                clearance_source.get("timeout_sec"),
                int(default_clearance["timeout_sec"]),
                1,
            ),
            "refresh_interval": _normalize_positive_int(
                clearance_source.get("refresh_interval"),
                int(default_clearance["refresh_interval"]),
                60,
            ),
            "warm_up_on_start": _normalize_bool(
                clearance_source.get("warm_up_on_start"),
                bool(default_clearance["warm_up_on_start"]),
            ),
        },
    }


def _normalize_third_party_apps_settings(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    canvas_source = source.get("infinite_canvas") if isinstance(source.get("infinite_canvas"), dict) else {}
    return {
        "infinite_canvas": {
            "enabled": _normalize_bool(canvas_source.get("enabled"), False),
            "url": str(canvas_source.get("url") or DEFAULT_THIRD_PARTY_APPS["infinite_canvas"]["url"]).strip(),
        },
    }


def _legacy_basic_from_settings(value: object, settings: dict[str, object]) -> dict[str, object]:
    source = dict(value) if isinstance(value, dict) else {}
    source["proxy"] = str(settings.get("proxy") or "").strip()
    source["base_url"] = str(settings.get("base_url") or "").strip().rstrip("/")
    try:
        source["image_expire_hours"] = max(
            1,
            int(settings.get("image_retention_days", source.get("image_expire_hours", 30)) or 30),
        )
    except (TypeError, ValueError):
        source["image_expire_hours"] = 30
    return source


def _promote_legacy_basic_settings(data: dict[str, object]) -> dict[str, object]:
    next_data = dict(data or {})
    basic = next_data.get("basic")
    if not isinstance(basic, dict):
        return next_data
    if "proxy" not in next_data and "proxy" in basic:
        next_data["proxy"] = str(basic.get("proxy") or "").strip()
    if "base_url" not in next_data and "base_url" in basic:
        next_data["base_url"] = str(basic.get("base_url") or "").strip().rstrip("/")
    if "image_retention_days" not in next_data and "image_expire_hours" in basic:
        next_data["image_retention_days"] = basic.get("image_expire_hours")
    return next_data


def _validate_image_storage_settings(settings: dict[str, object]) -> None:
    if not _normalize_bool(settings.get("enabled"), False):
        return
    mode = str(settings.get("mode") or "").strip().lower()
    if mode not in {"webdav", "both"}:
        return
    private_url = str(settings.get("private_webdav_url") or "").strip()
    if not private_url:
        raise ValueError("图片任务私有制品必须填写 private WebDAV URL")
    public_url = str(settings.get("webdav_url") or "").strip().rstrip("/")
    private_password = str(settings.get("private_webdav_password") or "").strip()
    if private_url.rstrip("/") != public_url and not private_password:
        raise ValueError("独立 private WebDAV 端点必须填写私有密码")
    if not str(settings.get("webdav_url") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV URL")
    if not str(settings.get("webdav_password") or "").strip():
        raise ValueError("启用 WebDAV 图片存储后必须填写 WebDAV 密码")


@dataclass(frozen=True)
class LoadedSettings:
    auth_key: str
    refresh_account_interval_minute: int


def _normalize_auth_key(value: object) -> str:
    return str(value or "").strip()


PUBLIC_EXAMPLE_AUTH_KEYS = frozenset({
    "chatgpt2api",
    "your_secret_key_here",
})


def _is_invalid_auth_key(value: object) -> bool:
    normalized = _normalize_auth_key(value)
    return normalized == "" or normalized.lower() in PUBLIC_EXAMPLE_AUTH_KEYS


def _load_settings() -> LoadedSettings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_config = read_json_object(CONFIG_FILE, name="config.json")
    auth_key = _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or raw_config.get("auth-key"))
    if _is_invalid_auth_key(auth_key):
        raise ValueError(
            "❌ auth-key 未设置！\n"
            "请在环境变量 CHATGPT2API_AUTH_KEY 中设置，或者在 config.json 中填写 auth-key。"
        )

    try:
        refresh_interval = int(raw_config.get("refresh_account_interval_minute", 5))
    except (TypeError, ValueError):
        refresh_interval = 5

    return LoadedSettings(
        auth_key=auth_key,
        refresh_account_interval_minute=refresh_interval,
    )


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = _promote_legacy_basic_settings(self._load())
        self._loaded_mtime_ns = self._config_mtime_ns()
        self._storage_backend: StorageBackend | None = None
        self._image_retention_protection_provider = None
        if _is_invalid_auth_key(self.auth_key):
            raise ValueError(
                "❌ auth-key 未设置！\n"
                "请按以下任意一种方式解决：\n"
                "1. 在 Render 的 Environment 变量中添加：\n"
                "   CHATGPT2API_AUTH_KEY = your_real_auth_key\n"
                "2. 或者在 config.json 中填写：\n"
                '   "auth-key": "your_real_auth_key"'
            )

    def _load(self) -> dict[str, object]:
        return read_json_object(self.path, name="config.json")

    def _config_mtime_ns(self) -> int:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return 0

    def _lock_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.lock")

    def reload_if_changed(self) -> None:
        with self._lock:
            current_mtime_ns = self._config_mtime_ns()
            if current_mtime_ns and current_mtime_ns != self._loaded_mtime_ns:
                self.data = _promote_legacy_basic_settings(self._load())
                self._loaded_mtime_ns = current_mtime_ns

    def _save(self) -> None:
        write_json_file(self.path, self.data)
        self._loaded_mtime_ns = self._config_mtime_ns()

    @property
    def auth_key(self) -> str:
        return _normalize_auth_key(os.getenv("CHATGPT2API_AUTH_KEY") or self.data.get("auth-key"))

    @property
    def accounts_file(self) -> Path:
        return DATA_DIR / "accounts.json"

    @property
    def refresh_account_interval_minute(self) -> int:
        try:
            return int(self.data.get("refresh_account_interval_minute", 5))
        except (TypeError, ValueError):
            return 5

    @property
    def image_retention_days(self) -> int:
        try:
            return max(1, int(self.data.get("image_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @property
    def log_retention_days(self) -> int:
        try:
            return max(1, int(self.data.get("log_retention_days", 30)))
        except (TypeError, ValueError):
            return 30

    @property
    def image_poll_timeout_secs(self) -> int:
        self.reload_if_changed()
        try:
            return max(1, int(self.data.get("image_poll_timeout_secs", 60)))
        except (TypeError, ValueError):
            return 60

    @property
    def image_stream_timeout_secs(self) -> int:
        self.reload_if_changed()
        try:
            return max(1, int(self.data.get("image_stream_timeout_secs", 80)))
        except (TypeError, ValueError):
            return 80

    @property
    def text_stream_timeout_secs(self) -> int:
        self.reload_if_changed()
        try:
            return max(1, int(self.data.get("text_stream_timeout_secs", 300)))
        except (TypeError, ValueError):
            return 300

    @property
    def image_poll_interval_secs(self) -> float:
        try:
            return max(0.5, float(self.data.get("image_poll_interval_secs", 10.0)))
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_poll_initial_wait_secs(self) -> float:
        """Image generation upstream takes ~30s; polling immediately wastes requests
        and trips a transient 429. Default 10s gives the conversation document time
        to commit before the first poll."""
        try:
            return max(0.0, float(self.data.get("image_poll_initial_wait_secs", 10.0)))
        except (TypeError, ValueError):
            return 10.0

    @property
    def image_account_concurrency(self) -> int:
        try:
            return min(3, max(1, int(self.data.get("image_account_concurrency", 1))))
        except (TypeError, ValueError):
            return 1

    @property
    def image_account_retry_enabled(self) -> bool:
        self.reload_if_changed()
        return _normalize_bool(self.data.get("image_account_retry_enabled"), True)

    @property
    def image_preflight_token_refresh_enabled(self) -> bool:
        self.reload_if_changed()
        return _normalize_bool(self.data.get("image_preflight_token_refresh_enabled"), False)

    @property
    def image_upscale_enabled(self) -> bool:
        self.reload_if_changed()
        return _normalize_bool(self.data.get("image_upscale_enabled"), False)

    @property
    def image_upscale_engine(self) -> str:
        self.reload_if_changed()
        value = str(self.data.get("image_upscale_engine") or "sharp_lanczos3").strip().lower()
        return value if value in {"sharp_lanczos3", "pillow_lanczos"} else "sharp_lanczos3"

    @property
    def image_auth_refresh_concurrency(self) -> int:
        self.reload_if_changed()
        return _normalize_positive_int(
            self.data.get("image_auth_refresh_concurrency"),
            10,
            1,
        )

    @property
    def image_max_account_attempts(self) -> int:
        self.reload_if_changed()
        try:
            return max(2, int(self.data.get("image_max_account_attempts", 4)))
        except (TypeError, ValueError):
            return 4

    @property
    def image_parallel_generation(self) -> bool:
        value = self.data.get("image_parallel_generation", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_remove_conversation_after_result(self) -> bool:
        self.reload_if_changed()
        return _normalize_bool(self.data.get("image_remove_conversation_after_result"), False)

    @property
    def image_settle_enabled(self) -> bool:
        """图片二次确认机制：找到 file_ids 后等待一段时间再次确认。"""
        value = self.data.get("image_settle_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_check_before_hit_enabled(self) -> bool:
        """先check再hit：通过轮询确认 file_ids 存在后再返回，而非仅依赖 SSE 事件。"""
        value = self.data.get("image_check_before_hit_enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def image_settle_secs(self) -> float:
        """二次确认等待时间（秒）。"""
        try:
            return max(0.5, float(self.data.get("image_settle_secs", 5.0)))
        except (TypeError, ValueError):
            return 5.0

    @property
    def auto_remove_invalid_accounts(self) -> bool:
        value = self.data.get("auto_remove_invalid_accounts", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def auto_remove_rate_limited_accounts(self) -> bool:
        value = self.data.get("auto_remove_rate_limited_accounts", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @property
    def log_levels(self) -> list[str]:
        levels = self.data.get("log_levels")
        if not isinstance(levels, list):
            return []
        allowed = {"debug", "info", "warning", "error"}
        return [level for item in levels if (level := str(item or "").strip().lower()) in allowed]

    @property
    def sensitive_words(self) -> list[str]:
        words = self.data.get("sensitive_words")
        return [word for item in words if (word := str(item or "").strip())] if isinstance(words, list) else []

    @property
    def ai_review(self) -> dict[str, object]:
        value = self.data.get("ai_review")
        return value if isinstance(value, dict) else {}

    @property
    def global_system_prompt(self) -> str:
        return str(self.data.get("global_system_prompt") or "").strip()

    @property
    def images_dir(self) -> Path:
        path = DATA_DIR / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def image_thumbnails_dir(self) -> Path:
        path = DATA_DIR / "image_thumbnails"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_old_images(self) -> int:
        cutoff = time.time() - self.image_retention_days * 86400
        protected = self.protected_image_paths()
        removed = 0
        for path in self.images_dir.rglob("*"):
            relative = path.relative_to(self.images_dir).as_posix() if path.is_file() else ""
            if path.is_file() and relative not in protected and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        for path in sorted((p for p in self.images_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        return removed

    def protected_image_paths(self) -> set[str]:
        if callable(self._image_retention_protection_provider):
            try:
                return {
                    str(item or "").replace("\\", "/").lstrip("/")
                    for item in self._image_retention_protection_provider()
                    if str(item or "").strip()
                }
            except Exception:
                return {
                    path.relative_to(self.images_dir).as_posix()
                    for path in self.images_dir.rglob("*")
                    if path.is_file()
                }
        return set()

    def all_image_paths(self) -> set[str]:
        return {
            path.relative_to(self.images_dir).as_posix()
            for path in self.images_dir.rglob("*")
            if path.is_file()
        }

    def set_image_retention_protection(self, provider) -> None:
        self._image_retention_protection_provider = provider

    @property
    def base_url(self) -> str:
        return str(
            os.getenv("CHATGPT2API_BASE_URL")
            or self.data.get("base_url")
            or ""
        ).strip().rstrip("/")

    @property
    def image_base_url(self) -> str:
        return str(
            os.getenv("CHATGPT2API_IMAGE_BASE_URL")
            or self.data.get("image_base_url")
            or ""
        ).strip().rstrip("/")

    @property
    def app_version(self) -> str:
        try:
            value = VERSION_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return "0.0.0"
        return value or "0.0.0"

    def get(self) -> dict[str, object]:
        with self._lock:
            self.reload_if_changed()
            data = dict(self.data)
            data["refresh_account_interval_minute"] = self.refresh_account_interval_minute
            data["image_retention_days"] = self.image_retention_days
            data["log_retention_days"] = self.log_retention_days
            data["image_poll_timeout_secs"] = self.image_poll_timeout_secs
            data["image_stream_timeout_secs"] = self.image_stream_timeout_secs
            data["text_stream_timeout_secs"] = self.text_stream_timeout_secs
            data["image_poll_interval_secs"] = self.image_poll_interval_secs
            data["image_poll_initial_wait_secs"] = self.image_poll_initial_wait_secs
            data["image_account_concurrency"] = self.image_account_concurrency
            data["image_account_retry_enabled"] = self.image_account_retry_enabled
            data["image_preflight_token_refresh_enabled"] = self.image_preflight_token_refresh_enabled
            data["image_upscale_enabled"] = self.image_upscale_enabled
            data["image_upscale_engine"] = self.image_upscale_engine
            data["image_auth_refresh_concurrency"] = self.image_auth_refresh_concurrency
            data["image_max_account_attempts"] = self.image_max_account_attempts
            data["image_parallel_generation"] = self.image_parallel_generation
            data["image_remove_conversation_after_result"] = self.image_remove_conversation_after_result
            data["auto_remove_invalid_accounts"] = self.auto_remove_invalid_accounts
            data["auto_remove_rate_limited_accounts"] = self.auto_remove_rate_limited_accounts
            data["log_levels"] = self.log_levels
            data["sensitive_words"] = self.sensitive_words
            data["ai_review"] = self.ai_review
            data["global_system_prompt"] = self.global_system_prompt
            data["image_base_url"] = self.image_base_url
            data["backup"] = self.get_backup_settings()
            data["image_storage"] = self.get_image_storage_settings()
            data["chat_completion_cache"] = self.get_chat_completion_cache_settings()
            data["runtime_capacity"] = self.get_runtime_capacity_settings()
            data["quota_limits"] = self.get_quota_limits_settings()
            data["proxy_runtime"] = self.get_public_proxy_runtime_settings()
            data["fallback_proxy"] = self.get_proxy_fallback_settings()
            data["third_party_apps"] = self.get_third_party_apps_settings()
            data["basic"] = _legacy_basic_from_settings(data.get("basic"), data)
            data.pop("auth-key", None)
            return data

    def get_proxy_settings(self) -> str:
        return str(self.data.get("proxy") or "").strip()

    def get_proxy_fallback_settings(self) -> str:
        return str(self.data.get("fallback_proxy") or "").strip()

    def get_proxy_runtime_settings(self) -> dict[str, object]:
        return _normalize_proxy_runtime_settings(self.data.get("proxy_runtime"))

    def get_public_proxy_runtime_settings(self) -> dict[str, object]:
        runtime = copy.deepcopy(self.get_proxy_runtime_settings())
        clearance = runtime.get("clearance") if isinstance(runtime.get("clearance"), dict) else {}
        if isinstance(clearance, dict):
            cf_cookies = str(clearance.get("cf_cookies") or "").strip()
            cf_clearance = str(clearance.get("cf_clearance") or "").strip()
            clearance["cf_cookies"] = ""
            clearance["cf_clearance"] = ""
            clearance["has_cf_cookies"] = bool(cf_cookies)
            clearance["has_cf_clearance"] = bool(cf_clearance)
        return runtime

    def get_third_party_apps_settings(self) -> dict[str, object]:
        return _normalize_third_party_apps_settings(self.data.get("third_party_apps"))

    def update(self, data: dict[str, object]) -> dict[str, object]:
        with self._lock:
            with file_lock(self._lock_path()):
                self.data = _promote_legacy_basic_settings(self._load())
                self._loaded_mtime_ns = self._config_mtime_ns()
                incoming = _promote_legacy_basic_settings(dict(data or {}))
                if isinstance(incoming.get("backup"), dict):
                    backup_update = dict(incoming.get("backup") or {})
                    backup_next = dict(self.get_backup_settings())
                    backup_next.update(backup_update)
                    if isinstance(backup_update.get("include"), dict):
                        include_next = dict(backup_next.get("include") or {})
                        include_next.update(dict(backup_update.get("include") or {}))
                        backup_next["include"] = include_next
                    if str(backup_update.get("secret_access_key") or "").strip() == SECRET_MASK:
                        backup_next["secret_access_key"] = self.get_backup_settings().get("secret_access_key")
                    if str(backup_update.get("passphrase") or "").strip() == SECRET_MASK:
                        backup_next["passphrase"] = self.get_backup_settings().get("passphrase")
                    incoming["backup"] = backup_next
                if isinstance(incoming.get("image_storage"), dict):
                    storage_update = dict(incoming.get("image_storage") or {})
                    storage_next = dict(self.get_image_storage_settings())
                    storage_next.update(storage_update)
                    if str(storage_update.get("webdav_password") or "").strip() == SECRET_MASK:
                        storage_next["webdav_password"] = self.get_image_storage_settings().get("webdav_password")
                    if str(storage_update.get("private_webdav_password") or "").strip() == SECRET_MASK:
                        storage_next["private_webdav_password"] = self.get_image_storage_settings().get("private_webdav_password")
                    incoming["image_storage"] = storage_next
                if isinstance(incoming.get("ai_review"), dict):
                    ai_review_update = dict(incoming.get("ai_review") or {})
                    ai_review_next = dict(self.ai_review)
                    ai_review_next.update(ai_review_update)
                    if str(ai_review_update.get("api_key") or "").strip() == SECRET_MASK:
                        ai_review_next["api_key"] = self.ai_review.get("api_key")
                    incoming["ai_review"] = ai_review_next
                if isinstance(incoming.get("basic"), dict):
                    basic_update = dict(incoming.get("basic") or {})
                    if str(basic_update.get("api_key") or "").strip() == SECRET_MASK:
                        current_basic = _legacy_basic_from_settings(self.data.get("basic"), self.data)
                        basic_update["api_key"] = current_basic.get("api_key")
                    incoming["basic"] = basic_update
                if isinstance(incoming.get("runtime_capacity"), dict):
                    runtime_next = dict(self.get_runtime_capacity_settings())
                    runtime_next.update(dict(incoming.get("runtime_capacity") or {}))
                    incoming["runtime_capacity"] = runtime_next
                if isinstance(incoming.get("quota_limits"), dict):
                    quota_next = dict(self.get_quota_limits_settings())
                    quota_next.update(dict(incoming.get("quota_limits") or {}))
                    incoming["quota_limits"] = quota_next
                next_data = _promote_legacy_basic_settings(self.data)
                next_data.update(incoming)
                next_data = _promote_legacy_basic_settings(next_data)
                if "backup" in next_data:
                    next_data["backup"] = _normalize_backup_settings(next_data.get("backup"))
                if "image_storage" in next_data:
                    next_data["image_storage"] = _normalize_image_storage_settings(next_data.get("image_storage"))
                    _validate_image_storage_settings(next_data["image_storage"])
                if "chat_completion_cache" in next_data:
                    next_data["chat_completion_cache"] = _normalize_chat_completion_cache_settings(
                        next_data.get("chat_completion_cache")
                    )
                if "runtime_capacity" in next_data:
                    next_data["runtime_capacity"] = _normalize_runtime_capacity_settings(
                        next_data.get("runtime_capacity")
                    )
                if "quota_limits" in next_data:
                    next_data["quota_limits"] = _normalize_quota_limits_settings(
                        next_data.get("quota_limits")
                    )
                if "third_party_apps" in next_data:
                    next_data["third_party_apps"] = _normalize_third_party_apps_settings(next_data.get("third_party_apps"))
                if "proxy_runtime" in next_data:
                    incoming_runtime = next_data.get("proxy_runtime")
                    if isinstance(incoming_runtime, dict):
                        previous_clearance = self.get_proxy_runtime_settings().get("clearance")
                        if isinstance(previous_clearance, dict):
                            incoming_runtime = dict(incoming_runtime)
                            incoming_runtime["_existing_cf_cookies"] = previous_clearance.get("cf_cookies")
                            incoming_runtime["_existing_cf_clearance"] = previous_clearance.get("cf_clearance")
                    next_data["proxy_runtime"] = _normalize_proxy_runtime_settings(incoming_runtime)
                next_data["basic"] = _legacy_basic_from_settings(next_data.get("basic"), next_data)
                next_data.pop("backup_state", None)
                self.data = next_data
                self._save()
        return self.get()

    def get_backup_settings(self) -> dict[str, object]:
        return _normalize_backup_settings(self.data.get("backup"))

    def get_image_storage_settings(self) -> dict[str, object]:
        return _normalize_image_storage_settings(self.data.get("image_storage"))

    def get_chat_completion_cache_settings(self) -> dict[str, object]:
        return _normalize_chat_completion_cache_settings(self.data.get("chat_completion_cache"))

    def get_runtime_capacity_settings(self) -> dict[str, object]:
        return _normalize_runtime_capacity_settings(self.data.get("runtime_capacity"))

    def get_quota_limits_settings(self) -> dict[str, object]:
        return _normalize_quota_limits_settings(self.data.get("quota_limits"))

    def get_storage_backend(self) -> StorageBackend:
        """获取存储后端实例（单例）"""
        if self._storage_backend is None:
            from services.storage.factory import create_storage_backend
            self._storage_backend = create_storage_backend(DATA_DIR)
        return self._storage_backend


def load_backup_state() -> dict[str, object]:
    return _normalize_backup_state(read_json_object(BACKUP_STATE_FILE, name="backup_state.json"))


def save_backup_state(state: dict[str, object]) -> dict[str, object]:
    normalized = _normalize_backup_state(state)
    write_json_file(BACKUP_STATE_FILE, normalized)
    return normalized


config = ConfigStore(CONFIG_FILE)
