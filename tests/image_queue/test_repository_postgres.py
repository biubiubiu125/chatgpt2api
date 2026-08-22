from __future__ import annotations

import os
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import StringIO
from threading import Barrier, Event, Lock, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DataError

from services.image_queue import database as database_module
from services.image_queue.database import ADVISORY_LOCK_KEY, ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import (
    ArtifactDescriptor,
    ArtifactStatus,
    EnqueueRequest,
    ImageAccountCandidate,
    JobCheckpoint,
    JobStage,
    JobStatus,
    TaskStatus,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_IMAGE_QUEUE_DATABASE_URL"),
    reason="TEST_IMAGE_QUEUE_DATABASE_URL is not configured",
)


@pytest.fixture
def postgres_database() -> ImageQueueDatabase:
    database_url = os.environ["TEST_IMAGE_QUEUE_DATABASE_URL"]
    schema = f"image_queue_test_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url=database_url),
        engine=engine,
    )
    database.start()
    try:
        yield database
    finally:
        database.dispose()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _enqueue(repository: ImageQueueRepository, key: str, required_jobs: int = 1):
    return repository.enqueue_task(EnqueueRequest(
        owner_key=f"postgres-test-{key}",
        idempotency_key=key,
        client_task_id=key,
        request_hash=key.ljust(64, "0")[:64],
        task_type="generation",
        original_prompt="test",
        effective_prompt="test",
        request_payload={},
        required_jobs=required_jobs,
    )).task


@contextmanager
def _synchronize_first_task_locks(database: ImageQueueDatabase, parties: int = 2):
    barrier = Barrier(parties)
    state_lock = Lock()
    arrivals = 0

    def before_cursor_execute(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal arrivals
        normalized = " ".join(str(statement).lower().split())
        if "from image_tasks" not in normalized or "for update" not in normalized:
            return
        with state_lock:
            if arrivals >= parties:
                return
            arrivals += 1
        barrier.wait(timeout=5)

    event.listen(database.engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield
    finally:
        event.remove(database.engine, "before_cursor_execute", before_cursor_execute)


def test_postgres_claim_uses_skip_locked() -> None:
    from sqlalchemy.dialects import postgresql

    from services.image_queue.repository import claimable_job_statement

    compiled = str(claimable_job_statement().compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE SKIP LOCKED" in compiled.upper()


def test_postgres_failed_start_releases_transaction_advisory_lock(
    postgres_database,
    monkeypatch,
) -> None:
    def fail_create_all(connection) -> None:
        connection.execute(text("SELECT CAST('not-an-integer' AS INTEGER)"))

    monkeypatch.setattr(database_module.Base.metadata, "create_all", fail_create_all)
    failed_database = ImageQueueDatabase(
        postgres_database.settings,
        engine=postgres_database.engine,
    )

    with pytest.raises(DataError):
        failed_database.start()

    with postgres_database.engine.connect() as possibly_locked:
        with postgres_database.engine.connect() as observer:
            assert possibly_locked.execute(text("SELECT pg_backend_pid() ")).scalar_one() != observer.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            acquired = bool(observer.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": ADVISORY_LOCK_KEY},
            ).scalar_one())
            if acquired:
                observer.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": ADVISORY_LOCK_KEY},
                )
            possibly_locked.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": ADVISORY_LOCK_KEY},
            )
    assert acquired is True


def test_postgres_logical_backup_uses_one_repeatable_snapshot(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    jobs_select_reached = Event()
    continue_backup = Event()

    def pause_before_jobs(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(str(statement).lower().split())
        if current_thread().name.startswith("queue-backup") and "from image_jobs" in normalized:
            jobs_select_reached.set()
            assert continue_backup.wait(5)

    event.listen(postgres_database.engine, "before_cursor_execute", pause_before_jobs)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="queue-backup") as executor:
            exported_future = executor.submit(repository.logical_backup)
            assert jobs_select_reached.wait(5)
            _enqueue(repository, uuid4().hex)
            continue_backup.set()
            exported = exported_future.result(timeout=10)
    finally:
        event.remove(postgres_database.engine, "before_cursor_execute", pause_before_jobs)
        continue_backup.set()

    assert exported["tasks"] == []
    assert exported["jobs"] == []


def test_postgres_streaming_backup_uses_one_repeatable_snapshot(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    jobs_select_reached = Event()
    continue_backup = Event()

    def pause_before_jobs(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(str(statement).lower().split())
        if current_thread().name.startswith("queue-stream") and "from image_jobs" in normalized:
            jobs_select_reached.set()
            assert continue_backup.wait(5)

    def export() -> dict[str, object]:
        output = StringIO()
        repository.write_logical_backup(output)
        return json.loads(output.getvalue())

    event.listen(postgres_database.engine, "before_cursor_execute", pause_before_jobs)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="queue-stream") as executor:
            exported_future = executor.submit(export)
            assert jobs_select_reached.wait(5)
            _enqueue(repository, uuid4().hex)
            continue_backup.set()
            exported = exported_future.result(timeout=10)
    finally:
        event.remove(postgres_database.engine, "before_cursor_execute", pause_before_jobs)
        continue_backup.set()

    assert exported["tasks"] == []
    assert exported["jobs"] == []


def test_postgres_persists_and_claims_single_image_job(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    unique = uuid4().hex
    task = _enqueue(repository, unique)
    candidate = ImageAccountCandidate(account_id=uuid4(), access_token="test-token")

    claim = repository.claim_next_job(f"worker-{unique}", [candidate], 1)

    assert claim is not None and claim.job.task_id == task.id
    assert repository.get_job(claim.job.id).status == JobStatus.LEASED
    repository.release_claim(claim)


def test_postgres_queue_context_includes_worker_pause_reason(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    task = _enqueue(repository, uuid4().hex)
    repository.update_worker_state(
        "worker-resource-paused",
        resource_snapshot={"cpu_percent": 96.0},
        effective_concurrency=0,
        pause_reason="resource_cpu",
    )

    positions, pause_reason = repository.queue_context([task.id])

    assert positions == {task.id: 1}
    assert pause_reason == "resource_cpu"


def test_postgres_concurrent_claims_skip_locked_jobs(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    tasks = [_enqueue(repository, uuid4().hex) for _ in range(2)]
    candidates = [
        ImageAccountCandidate(account_id=uuid4(), access_token=f"token-{index}")
        for index in range(2)
    ]
    barrier = Barrier(2)

    def claim(index: int):
        barrier.wait()
        return repository.claim_next_job(f"worker-{index}", [candidates[index]], 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, range(2)))

    assert all(claim is not None for claim in claims)
    assert {claim.job.id for claim in claims} == {
        job.id
        for task in tasks
        for job in repository.list_jobs(task.id)
    }
    for claim in claims:
        repository.release_claim(claim)


def test_postgres_account_lease_enforces_single_slot(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    for _ in range(2):
        _enqueue(repository, uuid4().hex)
    candidate = ImageAccountCandidate(account_id=uuid4(), access_token="shared-token")
    barrier = Barrier(2)

    def claim(index: int):
        barrier.wait()
        return repository.claim_next_job(f"worker-{index}", [candidate], 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, range(2)))

    acquired = [claim for claim in claims if claim is not None]
    assert len(acquired) == 1
    repository.release_claim(acquired[0])


def test_postgres_cancel_and_completion_do_not_deadlock(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    key = uuid4().hex
    task = _enqueue(repository, key)
    claim = repository.claim_next_job(
        "worker-completing",
        [ImageAccountCandidate(account_id=uuid4(), access_token="token")],
        1,
    )
    assert claim is not None
    artifact = ArtifactDescriptor(
        task_id=task.id,
        job_id=claim.job.id,
        kind="final",
        status=ArtifactStatus.READY,
        relative_path=f"{task.id}/{claim.job.id}/{'a' * 64}.png",
        sha256="a" * 64,
        mime_type="image/png",
        byte_size=10,
        width=4,
        height=3,
        public_url="https://images.example/result.png",
    )
    barrier = Barrier(2)

    def complete():
        barrier.wait()
        return repository.complete_job(
            claim,
            artifact,
            {"url": artifact.public_url, "width": 4, "height": 3},
        )

    def cancel():
        barrier.wait()
        return repository.request_cancel(task.owner_key, task.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed_future = executor.submit(complete)
        canceled_future = executor.submit(cancel)
        completed_future.result(timeout=10)
        canceled_future.result(timeout=10)

    final = repository.get_task(task.owner_key, task.id)
    assert final is not None and final.status in {TaskStatus.SUCCESS, TaskStatus.CANCELED}


def test_postgres_concurrent_multi_job_completion_aggregates_success(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    key = uuid4().hex
    task = _enqueue(repository, key, required_jobs=2)
    candidates = [
        ImageAccountCandidate(account_id=uuid4(), access_token=f"token-{index}")
        for index in range(2)
    ]
    claims = [
        repository.claim_next_job(f"worker-{index}", candidates, 1)
        for index in range(2)
    ]
    assert all(claim is not None for claim in claims)
    barrier = Barrier(2)

    def complete(index: int):
        claim = claims[index]
        digest = str(index + 1) * 64
        artifact = ArtifactDescriptor(
            task_id=task.id,
            job_id=claim.job.id,
            kind="final",
            status=ArtifactStatus.READY,
            relative_path=f"{task.id}/{claim.job.id}/{digest}.png",
            sha256=digest,
            mime_type="image/png",
            byte_size=10,
            width=4,
            height=3,
            public_url=f"https://images.example/{index}.png",
        )
        barrier.wait()
        return repository.complete_job(
            claim,
            artifact,
            {"url": artifact.public_url, "width": 4, "height": 3},
        )

    with _synchronize_first_task_locks(postgres_database):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(complete, index) for index in range(2)]
            for future in futures:
                future.result(timeout=10)

    final = repository.get_task(task.owner_key, task.id)
    assert final is not None
    assert final.status == TaskStatus.SUCCESS
    assert final.succeeded_jobs == 2
    assert len(final.data) == 2


def test_postgres_concurrent_multi_job_checkpoints_do_not_deadlock(postgres_database) -> None:
    repository = ImageQueueRepository(postgres_database)
    key = uuid4().hex
    task = _enqueue(repository, key, required_jobs=2)
    candidates = [
        ImageAccountCandidate(account_id=uuid4(), access_token=f"token-{index}")
        for index in range(2)
    ]
    claims = [
        repository.claim_next_job(f"worker-{index}", candidates, 1)
        for index in range(2)
    ]
    assert all(claim is not None for claim in claims)
    start = Barrier(2)

    def checkpoint(index: int):
        claim = claims[index]
        start.wait()
        return repository.checkpoint_job(
            claim,
            JobCheckpoint(
                stage=JobStage.DOWNLOADING,
                conversation_id=f"conversation-{index}",
                image_urls=(f"https://images.example/{index}.png",),
            ),
        )

    with _synchronize_first_task_locks(postgres_database):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(checkpoint, index) for index in range(2)]
            assert [future.result(timeout=10) for future in futures] == [True, True]

    jobs = repository.list_jobs(task.id)
    assert [job.stage for job in jobs] == [JobStage.DOWNLOADING, JobStage.DOWNLOADING]
