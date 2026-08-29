from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.env_loader import load_env_file_into_environment

load_env_file_into_environment(os.getenv("CHATGPT2API_ENV_FILE"))

from sqlalchemy import create_engine, text

from services.database_url import (
    APP_DATABASE_NAME,
    APP_DATABASE_ROLE,
    IMAGE_QUEUE_DATABASE_NAME,
    IMAGE_QUEUE_DATABASE_ROLE,
    ensure_database_role_marker,
    validate_named_postgres_database,
)


@dataclass(frozen=True)
class DatabaseTarget:
    url: str
    expected_name: str
    role: str


def _target(url: str, expected_name: str, role: str) -> DatabaseTarget | None:
    value = str(url or "").strip()
    if not value:
        return None
    return DatabaseTarget(
        url=validate_named_postgres_database(value, expected_name, role=role),
        expected_name=expected_name,
        role=role,
    )


def _targets() -> list[DatabaseTarget]:
    targets: list[DatabaseTarget] = []
    queue = _target(
        os.getenv("IMAGE_QUEUE_DATABASE_URL", ""),
        IMAGE_QUEUE_DATABASE_NAME,
        "image queue",
    )
    if queue is not None:
        targets.append(queue)
    storage_backend_env = str(os.getenv("STORAGE_BACKEND", "") or "").strip().lower()
    database_url = str(os.getenv("DATABASE_URL", "") or "").strip()
    dedicated_app_url = str(os.getenv("APP_DATABASE_URL", "") or "").strip()
    if storage_backend_env in {"json", "sqlite", "sqlite3", "git"}:
        storage_backend = storage_backend_env
    elif dedicated_app_url:
        storage_backend = "postgres"
    elif database_url.lower().startswith(("sqlite:", "sqlite3:")):
        storage_backend = "sqlite"
    else:
        storage_backend = storage_backend_env or "postgres"

    configured_app_url = dedicated_app_url if dedicated_app_url else database_url if storage_backend == "postgres" else ""
    if storage_backend == "postgres" and not configured_app_url:
        raise ValueError("APP_DATABASE_URL or DATABASE_URL is required when STORAGE_BACKEND=postgres")
    app = _target(configured_app_url, APP_DATABASE_NAME, "app")
    if app is not None:
        targets.append(app)
    if not targets:
        raise ValueError("IMAGE_QUEUE_DATABASE_URL is required")
    return targets


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(str(os.getenv(name, "") or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def bootstrap_target(
    target: DatabaseTarget,
    *,
    attempts: int,
    delay_seconds: float,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        engine = create_engine(target.url, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT 1"))
                ensure_database_role_marker(
                    connection,
                    (
                        IMAGE_QUEUE_DATABASE_ROLE
                        if target.expected_name == IMAGE_QUEUE_DATABASE_NAME
                        else APP_DATABASE_ROLE
                    ),
                    create_if_missing=True,
                )
            return
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(
                f"database bootstrap attempt {attempt}/{attempts} failed for "
                f"{target.expected_name}: {exc}; retrying in {delay_seconds:g}s",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)
        finally:
            engine.dispose()
    assert last_error is not None
    raise last_error


def main() -> int:
    try:
        attempts = _env_int("CHATGPT2API_DATABASE_BOOTSTRAP_ATTEMPTS", 30)
        delay_seconds = _env_float("CHATGPT2API_DATABASE_BOOTSTRAP_DELAY_SECONDS", 2.0)
        for target in _targets():
            bootstrap_target(
                target,
                attempts=attempts,
                delay_seconds=delay_seconds,
            )
        print("database role markers are ready")
        return 0
    except Exception as exc:
        print(f"database bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
