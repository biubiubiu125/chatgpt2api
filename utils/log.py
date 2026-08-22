import base64
import binascii
from collections import deque
import json
import logging
import re
from threading import Lock
from typing import Any

from utils.timezone import beijing_now_str


class Logger:
    _DATA_URL_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+")
    _JSON_B64_RE = re.compile(r'("b64_json"\s*:\s*")([A-Za-z0-9+/=]+)(")')
    _SAFE_TOKEN_COUNT_KEYS = {
        "cached_tokens",
        "completion_tokens",
        "image_tokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "reasoning_tokens",
        "text_tokens",
        "total_tokens",
    }

    def __init__(self, name: str = "chatgpt2api") -> None:
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._records: deque[dict[str, Any]] = deque(maxlen=1000)
        self._records_lock = Lock()
        self._sequence = 0

    def _enabled(self, level: str) -> bool:
        try:
            from services.config import config
            levels = set(config.log_levels)
        except Exception:
            levels = set()
        return level in (levels or {"info", "warning", "error"})

    def _mask_string(self, value: str, keep: int = 10) -> str:
        if len(value) <= keep:
            return value
        return value[:keep] + "..."

    def _mask_base64(self, value: str) -> str:
        if value.startswith("data:") and ";base64," in value:
            header, _, data = value.partition(",")
            return f"{header},{self._mask_string(data, 24)} (base64 len={len(data)})"
        return f"{self._mask_string(value, 24)} (base64 len={len(value)})"

    def _mask_secret(self, value: Any) -> Any:
        if isinstance(value, str):
            return "" if not value else f"***redacted*** (len={len(value)})"
        if value is None:
            return None
        return "***redacted***"

    def _normalize_key(self, key: object) -> str:
        value = str(key or "").strip()
        value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        value = re.sub(r"[^A-Za-z0-9]+", "_", value)
        return value.strip("_").lower()

    def _is_sensitive_key(self, key: object) -> bool:
        lowered_key = self._normalize_key(key)
        if not lowered_key or lowered_key in self._SAFE_TOKEN_COUNT_KEYS or lowered_key.startswith("public_"):
            return False
        return (
            lowered_key == "dx"
            or lowered_key == "authorization"
            or lowered_key in {"api_key", "admin_key", "secret_key", "password"}
            or lowered_key.endswith("_api_key")
            or lowered_key.endswith("_admin_key")
            or lowered_key.endswith("_secret_key")
            or lowered_key.endswith("_password")
            or lowered_key.endswith("_token")
            or lowered_key.endswith("_tokens")
            or lowered_key.endswith("_authorization")
            or "secret" in lowered_key
        )

    def _is_base64_string(self, value: str) -> bool:
        if len(value) < 64 or len(value) % 4 != 0:
            return False
        if not any(char in value for char in "+/="):
            return False
        try:
            base64.b64decode(value, validate=True)
            return True
        except (binascii.Error, ValueError):
            return False

    def _sanitize_string(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("data:") and ";base64," in stripped:
            return self._mask_base64(stripped)
        if self._is_base64_string(stripped):
            return self._mask_base64(stripped)
        sanitized = self._DATA_URL_RE.sub(lambda match: self._mask_base64(match.group(0)), value)
        sanitized = self._JSON_B64_RE.sub(
            lambda match: f'{match.group(1)}{self._mask_base64(match.group(2))}{match.group(3)}',
            sanitized,
        )
        if sanitized != value:
            return sanitized
        return value

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered_key = str(key).lower()
                if self._is_sensitive_key(key):
                    sanitized[key] = self._mask_secret(item)
                elif isinstance(item, str) and ("base64" in lowered_key or lowered_key == "b64_json"):
                    sanitized[key] = self._mask_base64(item)
                else:
                    sanitized[key] = self._sanitize(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, str):
            return self._sanitize_string(value)
        return value

    def _message(self, value: Any) -> str:
        sanitized = self._sanitize(value)
        if isinstance(sanitized, str):
            return sanitized
        return json.dumps(sanitized, ensure_ascii=False, default=str)

    def _record(self, level: str, message: str) -> None:
        with self._records_lock:
            self._sequence += 1
            self._records.append(
                {
                    "id": f"runtime-{self._sequence}",
                    "time": beijing_now_str(),
                    "level": level,
                    "message": message,
                    "source": "memory",
                }
            )

    def get_records(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit or 200), 1), 1000)
        with self._records_lock:
            return list(reversed(list(self._records)))[:safe_limit]

    def debug(self, message: Any) -> None:
        if self._enabled("debug"):
            formatted = self._message(message)
            self._record("debug", formatted)
            self._logger.debug(formatted)

    def info(self, message: Any) -> None:
        if self._enabled("info"):
            formatted = self._message(message)
            self._record("info", formatted)
            self._logger.info(formatted)

    def warning(self, message: Any) -> None:
        if self._enabled("warning"):
            formatted = self._message(message)
            self._record("warning", formatted)
            self._logger.warning(formatted)

    def error(self, message: Any) -> None:
        if self._enabled("error"):
            formatted = self._message(message)
            self._record("error", formatted)
            self._logger.error(formatted)


logger = Logger()
