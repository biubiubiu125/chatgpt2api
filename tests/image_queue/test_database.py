from __future__ import annotations

from contextlib import contextmanager
from threading import RLock

from sqlalchemy import create_engine, inspect, text

from services.image_queue import database as database_module
from services.image_queue.database import ImageQueueDatabase, SCHEMA_VERSION
from services.image_queue.settings import ImageQueueSettings


def test_postgres_start_uses_transaction_scoped_advisory_lock(monkeypatch) -> None:
    statements: list[str] = []

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return 5

        class _Mappings:
            @staticmethod
            def first():
                return {"role": "image_queue", "created_at": "now", "updated_at": "now"}

        def mappings(self):
            return self._Mappings()

    class Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def execute(self, statement, _parameters=None):
            statements.append(str(statement))
            return Result()

    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()

    monkeypatch.setattr(database_module.Base.metadata, "create_all", lambda _connection: None)
    monkeypatch.setattr(database_module, "_apply_schema_migrations", lambda _connection, _current_version: None)
    monkeypatch.setattr(database_module, "_validate_schema", lambda _connection: None)
    database = ImageQueueDatabase.__new__(ImageQueueDatabase)
    database.engine = Engine()
    database._lock = RLock()
    database._started = False

    database.start()

    assert any("pg_advisory_xact_lock" in statement for statement in statements)
    assert not any("pg_advisory_unlock" in statement for statement in statements)


def test_postgres_start_bootstraps_an_empty_database_role_marker(monkeypatch) -> None:
    marker_calls: list[bool] = []

    class Result:
        @staticmethod
        def scalar_one_or_none():
            return 0

    class Connection:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        def execute(self, _statement, _parameters=None):
            return Result()

    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()

    def ensure_marker(_connection, _role, *, create_if_missing):
        marker_calls.append(create_if_missing)
        return {"role": "image_queue"}

    monkeypatch.setattr(database_module, "ensure_database_role_marker", ensure_marker)
    monkeypatch.setattr(database_module.Base.metadata, "create_all", lambda _connection: None)
    monkeypatch.setattr(database_module, "_apply_schema_migrations", lambda _connection, _current_version: None)
    monkeypatch.setattr(database_module, "_validate_schema", lambda _connection: None)
    database = ImageQueueDatabase.__new__(ImageQueueDatabase)
    database.engine = Engine()
    database._lock = RLock()
    database._started = False

    database.start()

    assert marker_calls == [True]


def test_schema_creates_every_durable_queue_table(sqlite_queue_database: ImageQueueDatabase) -> None:
    table_names = set(inspect(sqlite_queue_database.engine).get_table_names())

    assert {
        "image_tasks",
        "image_jobs",
        "image_task_events",
        "image_task_artifacts",
        "image_account_leases",
        "image_worker_state",
        "image_legacy_imports",
        "image_queue_schema_migrations",
    }.issubset(table_names)


def test_schema_contains_task_and_job_uniqueness(sqlite_queue_database: ImageQueueDatabase) -> None:
    inspector = inspect(sqlite_queue_database.engine)
    task_constraints = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("image_tasks")
    }
    job_constraints = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("image_jobs")
    }

    assert ("owner_key", "idempotency_key") in task_constraints
    assert ("owner_key", "client_task_id") in task_constraints
    assert ("task_id", "ordinal") in job_constraints


def test_schema_v5_adds_result_payload_terminal_index_quota_flags_and_artifact_ordinal() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE image_queue_schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO image_queue_schema_migrations (version, applied_at) "
            "VALUES (1, CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE image_jobs (id VARCHAR(36) PRIMARY KEY, "
            "status VARCHAR(24) NOT NULL DEFAULT 'queued', "
            "stage VARCHAR(24) NOT NULL DEFAULT 'queued')"
        ))
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url="sqlite+pysqlite:///:memory:"),
        engine=engine,
        allow_non_postgres=True,
    )

    database.start()

    columns = {item["name"] for item in inspect(engine).get_columns("image_jobs")}
    with engine.connect() as connection:
        versions = connection.execute(text(
            "SELECT version FROM image_queue_schema_migrations ORDER BY version"
        )).scalars().all()
    assert "result_payload" in columns
    task_indexes = {item["name"] for item in inspect(engine).get_indexes("image_tasks")} if inspect(engine).has_table("image_tasks") else set()
    assert {"quota_consumed", "quota_accounted"}.issubset(columns)
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("image_task_artifacts")}
    assert "ordinal" in artifact_columns
    assert versions == [1, SCHEMA_VERSION]
    assert "ix_image_tasks_terminal_cleanup" in task_indexes
    database.dispose()
    engine.dispose()


def test_schema_v5_adds_terminal_cleanup_index_quota_flags_and_artifact_ordinal_to_v2_database() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE image_queue_schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO image_queue_schema_migrations (version, applied_at) "
            "VALUES (2, CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "CREATE TABLE image_tasks ("
            "id VARCHAR(36) PRIMARY KEY, status VARCHAR(24) NOT NULL, "
            "completed_at DATETIME, delivery_status VARCHAR(24) NOT NULL)"
        ))
        connection.execute(text(
            "CREATE TABLE image_jobs (id VARCHAR(36) PRIMARY KEY, "
            "status VARCHAR(24) NOT NULL DEFAULT 'queued', "
            "stage VARCHAR(24) NOT NULL DEFAULT 'queued')"
        ))
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url="sqlite+pysqlite:///:memory:"),
        engine=engine,
        allow_non_postgres=True,
    )

    database.start()

    indexes = {item["name"] for item in inspect(engine).get_indexes("image_tasks")}
    with engine.connect() as connection:
        versions = connection.execute(text(
            "SELECT version FROM image_queue_schema_migrations ORDER BY version"
        )).scalars().all()
    job_columns = {item["name"] for item in inspect(engine).get_columns("image_jobs")}
    assert "ix_image_tasks_terminal_cleanup" in indexes
    assert {"quota_consumed", "quota_accounted"}.issubset(job_columns)
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("image_task_artifacts")}
    assert "ordinal" in artifact_columns
    assert versions == [2, SCHEMA_VERSION]
    database.dispose()
    engine.dispose()
