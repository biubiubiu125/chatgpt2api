from __future__ import annotations

from contextlib import contextmanager
from threading import RLock
from typing import Iterator

from sqlalchemy import Engine, Index, UniqueConstraint, create_engine, func, inspect, select, text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from services.image_queue.models import Base, ImageJob, ImageQueueSchemaMigration, ImageTask, ImageTaskArtifact, utc_now
from services.image_queue.settings import ImageQueueConfigurationError, ImageQueueSettings
from services.database_url import IMAGE_QUEUE_DATABASE_ROLE, ensure_database_role_marker


SCHEMA_VERSION = 8
ADVISORY_LOCK_KEY = "chatgpt2api-image-queue-v1"


class ImageQueueUnavailableError(RuntimeError):
    code = "image_queue_unavailable"


def _constraint_columns(columns) -> tuple[str, ...]:
    return tuple(str(column.name) for column in columns)


def _quote_name(connection, value: str) -> str:
    return connection.dialect.identifier_preparer.quote(value)


def _table_sql(connection, table) -> str:
    return connection.dialect.identifier_preparer.format_table(table)


def _unique_sets(inspector, table_name: str) -> set[tuple[str, ...]]:
    unique_sets = {
        tuple(str(column) for column in constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("column_names")
    }
    unique_sets.update({
        tuple(str(column) for column in index.get("column_names") or ())
        for index in inspector.get_indexes(table_name)
        if index.get("unique") and index.get("column_names")
    })
    return unique_sets


def _add_missing_column(connection, table, column) -> None:
    statement = (
        f"ALTER TABLE {_table_sql(connection, table)} "
        f"ADD COLUMN {_quote_name(connection, column.name)} {column.type.compile(dialect=connection.dialect)}"
    )
    connection.execute(text(statement))


def _create_unique_index(connection, table, name: str, column_names: tuple[str, ...]) -> None:
    columns = ", ".join(_quote_name(connection, column) for column in column_names)
    statement = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_quote_name(connection, name)} "
        f"ON {_table_sql(connection, table)} ({columns})"
    )
    connection.execute(text(statement))


def _set_null_default(connection, table, column_name: str, value: object) -> None:
    column = table.c[column_name]
    connection.execute(
        table.update()
        .where(column.is_(None))
        .values({column_name: value})
    )


def _update_sql(connection, statement: str, parameters: dict[str, object] | None = None) -> None:
    connection.execute(text(statement), parameters or {})


def _backfill_task_defaults(connection, columns: set[str]) -> None:
    table_name = _table_sql(connection, ImageTask.__table__)
    now = utc_now()
    if "owner_id" in columns:
        _update_sql(
            connection,
            f"UPDATE {table_name} SET owner_key = owner_id "
            "WHERE owner_key IS NULL AND owner_id IS NOT NULL AND owner_id <> ''",
        )
    _set_null_default(connection, ImageTask.__table__, "owner_key", "legacy-migrated")
    _update_sql(
        connection,
        f"UPDATE {table_name} SET client_task_id = CAST(id AS TEXT) "
        "WHERE client_task_id IS NULL OR client_task_id = ''",
    )
    _update_sql(
        connection,
        f"UPDATE {table_name} SET idempotency_key = 'migrated:' || CAST(id AS TEXT) "
        "WHERE idempotency_key IS NULL OR idempotency_key = ''",
    )
    if "mode" in columns:
        _update_sql(
            connection,
            f"UPDATE {table_name} SET task_type = CASE WHEN mode = 'edit' THEN 'edit' ELSE 'generation' END "
            "WHERE task_type IS NULL OR task_type = ''",
        )
    _set_null_default(connection, ImageTask.__table__, "task_type", "generation")
    if "model" in columns:
        _update_sql(
            connection,
            f"UPDATE {table_name} SET public_model = model "
            "WHERE (public_model IS NULL OR public_model = '') AND model IS NOT NULL AND model <> ''",
        )
    _set_null_default(connection, ImageTask.__table__, "public_model", "gpt-image-2")
    _set_null_default(connection, ImageTask.__table__, "request_hash", "legacy_migrated")
    _set_null_default(connection, ImageTask.__table__, "original_prompt", "")
    _set_null_default(connection, ImageTask.__table__, "effective_prompt", "")
    _set_null_default(connection, ImageTask.__table__, "request_payload", {})
    if "n" in columns:
        _update_sql(
            connection,
            f"UPDATE {table_name} SET required_jobs = n "
            "WHERE required_jobs IS NULL AND n IS NOT NULL AND n > 0",
        )
    _set_null_default(connection, ImageTask.__table__, "required_jobs", 1)
    _update_sql(connection, f"UPDATE {table_name} SET status = 'failed' WHERE status = 'error'")
    _set_null_default(connection, ImageTask.__table__, "succeeded_jobs", 0)
    _set_null_default(connection, ImageTask.__table__, "failed_jobs", 0)
    _update_sql(
        connection,
        f"UPDATE {table_name} SET succeeded_jobs = required_jobs "
        "WHERE status = 'success' AND succeeded_jobs = 0",
    )
    _update_sql(
        connection,
        f"UPDATE {table_name} SET failed_jobs = required_jobs "
        "WHERE status IN ('failed', 'canceled') AND failed_jobs = 0",
    )
    _set_null_default(connection, ImageTask.__table__, "delivery_status", "pending")
    _set_null_default(connection, ImageTask.__table__, "cancel_requested", False)
    _set_null_default(connection, ImageTask.__table__, "created_at", now)
    _update_sql(
        connection,
        f"UPDATE {table_name} SET queued_at = created_at WHERE queued_at IS NULL",
    )
    _set_null_default(connection, ImageTask.__table__, "updated_at", now)
    _update_sql(
        connection,
        f"UPDATE {table_name} SET started_at = created_at WHERE status = 'success' AND started_at IS NULL",
    )
    _update_sql(
        connection,
        f"UPDATE {table_name} SET completed_at = updated_at "
        "WHERE status IN ('success', 'failed', 'canceled') AND completed_at IS NULL",
    )
    _set_null_default(connection, ImageTask.__table__, "version", 1)


def _backfill_job_ordinals(connection) -> None:
    table_name = _table_sql(connection, ImageJob.__table__)
    rows = connection.execute(text(
        f"SELECT id, task_id FROM {table_name} WHERE ordinal IS NULL ORDER BY task_id, id"
    )).mappings().all()
    counters: dict[str, int] = {}
    for row in rows:
        task_key = str(row.get("task_id") or row.get("id") or "")
        counters[task_key] = counters.get(task_key, 0) + 1
        connection.execute(
            text(f"UPDATE {table_name} SET ordinal = :ordinal WHERE id = :id"),
            {"ordinal": counters[task_key], "id": row["id"]},
        )


def _backfill_job_defaults(connection) -> None:
    table_name = _table_sql(connection, ImageJob.__table__)
    now = utc_now()
    _backfill_job_ordinals(connection)
    _set_null_default(connection, ImageJob.__table__, "generate_attempts", 0)
    _set_null_default(connection, ImageJob.__table__, "download_attempts", 0)
    _set_null_default(connection, ImageJob.__table__, "save_attempts", 0)
    _set_null_default(connection, ImageJob.__table__, "available_at", now)
    _set_null_default(connection, ImageJob.__table__, "lease_version", 0)
    _set_null_default(connection, ImageJob.__table__, "image_urls", [])
    _set_null_default(connection, ImageJob.__table__, "file_ids", [])
    _set_null_default(connection, ImageJob.__table__, "sediment_ids", [])
    _set_null_default(connection, ImageJob.__table__, "quota_consumed", False)
    _set_null_default(connection, ImageJob.__table__, "quota_accounted", False)
    _set_null_default(connection, ImageJob.__table__, "result_payload", {})
    _set_null_default(connection, ImageJob.__table__, "stage_timings", {})
    _set_null_default(connection, ImageJob.__table__, "created_at", now)
    _set_null_default(connection, ImageJob.__table__, "updated_at", now)
    _update_sql(connection, f"UPDATE {table_name} SET status = 'failed' WHERE status = 'error'")
    _update_sql(connection, f"UPDATE {table_name} SET stage = 'failed' WHERE stage = 'error'")
    _update_sql(
        connection,
        f"UPDATE {table_name} SET started_at = created_at WHERE status = 'success' AND started_at IS NULL",
    )
    _update_sql(
        connection,
        f"UPDATE {table_name} SET completed_at = updated_at "
        "WHERE status IN ('success', 'failed', 'canceled') AND completed_at IS NULL",
    )


def _backfill_artifact_defaults(connection) -> None:
    table = ImageTaskArtifact.__table__
    if inspect(connection).has_table(table.name):
        _set_null_default(connection, table, "worker_id", "")


def _backfill_schema_defaults(connection) -> None:
    inspector = inspect(connection)
    if inspector.has_table("image_tasks"):
        _backfill_task_defaults(
            connection,
            {str(column["name"]) for column in inspector.get_columns("image_tasks")},
        )
    if inspector.has_table("image_jobs"):
        _backfill_job_defaults(connection)
    if inspector.has_table("image_task_artifacts"):
        _backfill_artifact_defaults(connection)


def _repair_schema_shape(connection) -> None:
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in table_names:
            table.create(connection, checkfirst=True)
            continue
        actual_columns = {str(column["name"]) for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in actual_columns:
                _add_missing_column(connection, table, column)

    _backfill_schema_defaults(connection)

    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in table_names:
            continue
        for index in table.indexes:
            index.create(connection, checkfirst=True)

        actual_unique_sets = _unique_sets(inspector, table.name)
        for constraint in table.constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            columns = _constraint_columns(constraint.columns)
            if columns in actual_unique_sets:
                continue
            name = str(constraint.name or f"uq_{table.name}_{'_'.join(columns)}")
            _create_unique_index(connection, table, name, columns)


def _validate_schema(connection) -> None:
    inspector = inspect(connection)
    errors: list[str] = []
    table_names = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in table_names:
            errors.append(f"{table.name} missing table")
            continue
        actual_columns = {str(column["name"]) for column in inspector.get_columns(table.name)}
        expected_columns = {str(column.name) for column in table.columns}
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            errors.append(f"{table.name} missing columns {', '.join(missing_columns[:8])}")

        unique_sets = _unique_sets(inspector, table.name)
        expected_unique_sets = {
            _constraint_columns(constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        expected_unique_sets.update({
            _constraint_columns(index.columns)
            for index in table.indexes
            if index.unique
        })
        missing_uniques = sorted(expected_unique_sets - unique_sets)
        if missing_uniques:
            formatted = ", ".join("(" + ", ".join(columns) + ")" for columns in missing_uniques[:4])
            errors.append(f"{table.name} missing unique constraints {formatted}")
    if errors:
        raise ImageQueueConfigurationError(
            "image queue schema is incomplete: " + "; ".join(errors[:8])
        )


def _apply_schema_migrations(connection, current_version: int) -> None:
    _ = current_version
    _repair_schema_shape(connection)


class ImageQueueDatabase:
    def __init__(
        self,
        settings: ImageQueueSettings,
        *,
        engine: Engine | None = None,
        allow_non_postgres: bool = False,
    ) -> None:
        self.settings = settings
        self._lock = RLock()
        self._started = False
        self._owns_engine = engine is None
        if engine is None:
            if not settings.database_url:
                self.engine = None
                self._session_factory = None
                return
            if not allow_non_postgres and not settings.database_url.lower().startswith("postgresql"):
                raise ImageQueueConfigurationError("image queue requires PostgreSQL")
            engine_options: dict[str, object] = {"pool_pre_ping": True}
            if settings.database_url.lower().startswith("postgresql"):
                engine_options.update({
                    "pool_size": settings.database_pool_size,
                    "max_overflow": settings.database_max_overflow,
                })
            engine = create_engine(settings.database_url, **engine_options)
        elif not allow_non_postgres and engine.dialect.name != "postgresql":
            raise ImageQueueConfigurationError("image queue requires PostgreSQL")
        self.engine: Engine | None = engine
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    @property
    def available(self) -> bool:
        return self.engine is not None

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self.engine is None:
            raise ImageQueueUnavailableError("image queue PostgreSQL is not configured")
        with self._lock:
            if self._started:
                return
            with self.engine.begin() as connection:
                postgres = connection.dialect.name == "postgresql"
                if postgres:
                    connection.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": ADVISORY_LOCK_KEY},
                    )
                try:
                    ensure_database_role_marker(
                        connection,
                        IMAGE_QUEUE_DATABASE_ROLE,
                        create_if_missing=True,
                    )
                except ValueError as exc:
                    raise ImageQueueConfigurationError(str(exc)) from exc
                Base.metadata.create_all(connection)
                current = connection.execute(
                    select(func.max(ImageQueueSchemaMigration.version))
                ).scalar_one_or_none()
                _apply_schema_migrations(connection, int(current or 0))
                _validate_schema(connection)
                if int(current or 0) < SCHEMA_VERSION:
                    connection.execute(
                        ImageQueueSchemaMigration.__table__.insert().values(version=SCHEMA_VERSION)
                    )
            self._started = True

    @contextmanager
    def session(self) -> Iterator[Session]:
        if not self._started or self._session_factory is None:
            raise ImageQueueUnavailableError("image queue database is not started")
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except (OperationalError, InterfaceError) as exc:
            session.rollback()
            message = str(exc).lower()
            # SQLite concurrent/static-pool races are not a real outage; re-raise
            # so callers can retry. Only treat connectivity-style failures as 503.
            transient_local = any(
                token in message
                for token in (
                    "cannot start a transaction within a transaction",
                    "no more rows available",
                    "database is locked",
                )
            )
            if transient_local:
                raise
            raise ImageQueueUnavailableError("image queue PostgreSQL is unavailable") from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> None:
        with self.session() as session:
            session.execute(text("SELECT 1"))

    def pool_usage_percent(self) -> float:
        if self.engine is None:
            return 100.0
        pool = self.engine.pool
        size = int(getattr(pool, "size", lambda: 0)() or 0)
        overflow = int(getattr(pool, "overflow", lambda: 0)() or 0)
        checked_out = int(getattr(pool, "checkedout", lambda: 0)() or 0)
        capacity = max(1, size + max(0, overflow))
        return min(100.0, checked_out * 100.0 / capacity)

    def dispose(self) -> None:
        with self._lock:
            self._started = False
            if self.engine is not None and self._owns_engine:
                self.engine.dispose()

    def reset_after_fork(self) -> None:
        """Drop pooled connections inherited from the parent process.

        A forked child must never reuse a socket the parent still owns. Passing
        ``close=False`` disposes the pool without closing those connections, so
        the parent keeps working while the child opens its own on next checkout.
        Unlike :meth:`dispose` this keeps the started flag, because the schema is
        already there and the child has to keep issuing queries.
        """
        with self._lock:
            if self.engine is not None and self._owns_engine:
                self.engine.dispose(close=False)
