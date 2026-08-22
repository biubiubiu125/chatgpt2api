from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.config import config


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def run() -> None:
    runtime = config.get_runtime_capacity_settings()
    uvicorn.run(
        "main:app",
        host=str(os.getenv("HOST") or "0.0.0.0"),
        port=_env_int("PORT", 80),
        access_log=False,
        log_level=str(os.getenv("LOG_LEVEL") or "info"),
        workers=_env_int("UVICORN_WORKERS", int(runtime.get("uvicorn_workers") or 1)),
    )


if __name__ == "__main__":
    run()
