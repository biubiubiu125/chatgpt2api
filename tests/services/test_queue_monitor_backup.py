from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
import tarfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from services.backup_service import BackupError, BackupService, CloudflareR2Client
from services.config import ConfigStore
from services.image_queue.database import ImageQueueDatabase
from services.image_queue.repository import ImageQueueRepository
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import ImageAccountCandidate
from services.realtime_monitor_service import RealtimeMonitorService


IDENTITY = {"id": "owner-1", "role": "user"}


def test_r2_file_upload_streams_file_body(tmp_path) -> None:
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            received.append(self.rfile.read(int(self.headers["content-length"])))
            self.send_response(200)
            self.send_header("ETag", '"streamed"')
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class LocalClient(CloudflareR2Client):
        @property
        def endpoint(self) -> str:
            return f"http://127.0.0.1:{server.server_port}"

    source = tmp_path / "backup.tar.gz"
    source.write_bytes(b"streamed-backup" * 1024)
    client = LocalClient({
        "account_id": "test",
        "access_key_id": "access",
        "secret_access_key": "secret",
        "bucket": "bucket",
    })
    try:
        result = client.upload_file(
            "backups/test.tar.gz",
            source,
            content_type="application/gzip",
        )
    finally:
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result["etag"] == "streamed"
    assert received == [source.read_bytes()]


def test_monitor_reports_postgres_queue_and_terminal_window() -> None:
    monitor = RealtimeMonitorService()
    monitor.set_image_queue_provider(lambda: {
        "queued": 12,
        "running": 3,
        "saving": 2,
        "retrying": 1,
        "success": 20,
        "failed": 4,
        "canceled": 1,
    })

    snapshot = monitor.snapshot()

    assert snapshot["image_queue"]["queued"] == 12
    assert snapshot["window"]["label"] == "结束窗口"


def test_backup_uses_logical_database_export_not_legacy_json() -> None:
    backup = BackupService()
    backup.set_image_queue_provider(lambda: {"tasks": [{"id": "task-1"}], "artifacts": []})
    payload = backup._build_backup_archive(
        {
            "include": {
                "image_tasks": True,
                "config": False,
                "register": False,
                "cpa": False,
                "sub2api": False,
                "logs": False,
                "dashboard_metrics": False,
                "accounts_snapshot": False,
                "auth_keys_snapshot": False,
                "images": False,
            }
        },
        trigger="test",
    )

    with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
        names = archive.getnames()
        exported = json.load(archive.extractfile("data/image-queue.json"))
    assert "data/image-queue.json" in names
    assert "data/image_tasks.json" not in names
    assert exported["tasks"][0]["id"] == "task-1"


def test_backup_defaults_include_durable_image_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("services.config.DATA_DIR", tmp_path)
    store = ConfigStore(tmp_path / "config.json")

    assert store.get_backup_settings()["include"]["images"] is True


def test_backup_archive_restores_queue_payload_and_image_files(tmp_path) -> None:
    queue_payload = {"version": 2, "tasks": [], "jobs": [], "events": [], "artifacts": []}
    image_payload = b"saved-image"
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        queue_bytes = json.dumps(queue_payload).encode("utf-8")
        queue_info = tarfile.TarInfo("data/image-queue.json")
        queue_info.size = len(queue_bytes)
        archive.addfile(queue_info, BytesIO(queue_bytes))
        image_info = tarfile.TarInfo("data/images/task-1/job-1/image.png")
        image_info.size = len(image_payload)
        archive.addfile(image_info, BytesIO(image_payload))
    restored_payloads = []
    backup = BackupService()
    backup.set_image_queue_restore_provider(lambda payload: restored_payloads.append(payload) or {"tasks": 0})

    result = backup.restore_archive_payload(buffer.getvalue(), artifact_root=tmp_path / "images")

    assert restored_payloads == [queue_payload]
    assert (tmp_path / "images" / "task-1" / "job-1" / "image.png").read_bytes() == image_payload
    assert result["restored_images"] == 1


def test_backup_restore_rolls_back_image_files_when_queue_restore_fails(tmp_path) -> None:
    image_payload = b"saved-image"
    relative_path = "task-1/job-1/image.png"
    queue_payload = {
        "version": 2,
        "tasks": [],
        "jobs": [],
        "events": [],
        "artifacts": [{
            "relative_path": relative_path,
            "sha256": __import__("hashlib").sha256(image_payload).hexdigest(),
            "backup_file_included": True,
        }],
    }
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        queue_bytes = json.dumps(queue_payload).encode("utf-8")
        queue_info = tarfile.TarInfo("data/image-queue.json")
        queue_info.size = len(queue_bytes)
        archive.addfile(queue_info, BytesIO(queue_bytes))
        image_info = tarfile.TarInfo(f"data/images/{relative_path}")
        image_info.size = len(image_payload)
        archive.addfile(image_info, BytesIO(image_payload))
    backup = BackupService()
    backup.set_image_queue_restore_provider(
        lambda payload: (_ for _ in ()).throw(BackupError("db restore failed"))
    )
    root = tmp_path / "images"

    with pytest.raises(BackupError, match="db restore failed"):
        backup.restore_archive_payload(buffer.getvalue(), artifact_root=root)

    assert not (root / relative_path).exists()


def test_backup_uses_configured_queue_artifact_root(tmp_path) -> None:
    artifact_root = tmp_path / "custom-artifacts"
    artifact_path = artifact_root / "task-1" / "job-1" / "image.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"saved-image")
    queue_payload = {
        "version": 2,
        "tasks": [],
        "jobs": [],
        "events": [],
        "artifacts": [{
            "relative_path": "task-1/job-1/image.png",
            "storage_backend": "both",
            "sha256": __import__("hashlib").sha256(b"saved-image").hexdigest(),
        }],
    }
    backup = BackupService()
    backup.set_image_queue_provider(lambda: queue_payload)
    backup.set_image_queue_artifact_root(artifact_root)

    archive = backup._build_backup_archive(
        {"include": {"image_tasks": True, "images": True}},
        trigger="custom-root",
    )

    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as package:
        names = package.getnames()
        exported = json.load(package.extractfile("data/image-queue.json"))
    assert "data/images/task-1/job-1/image.png" in names
    assert exported["artifacts"][0]["backup_file_included"] is True


def test_backup_rejects_artifact_bytes_that_do_not_match_database_snapshot(tmp_path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_path = artifact_root / "task-1" / "job-1" / "image.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"database-version")
    queue_payload = {
        "version": 2,
        "tasks": [],
        "jobs": [],
        "events": [],
        "artifacts": [{
            "relative_path": "task-1/job-1/image.png",
            "storage_backend": "local",
            "sha256": __import__("hashlib").sha256(b"database-version").hexdigest(),
        }],
    }

    def export_then_change_file():
        artifact_path.write_bytes(b"changed-after-database-export")
        return queue_payload

    backup = BackupService()
    backup.set_image_queue_provider(export_then_change_file)
    backup.set_image_queue_artifact_root(artifact_root)

    with pytest.raises(ValueError, match="checksum"):
        backup._build_backup_archive(
            {"include": {"image_tasks": True, "images": True}},
            trigger="changed-artifact",
        )


def test_backup_restore_allows_artifact_already_removed_by_retention(
    tmp_path,
    monkeypatch,
) -> None:
    missing_path = "task-1/job-1/missing.png"
    queue_payload = {
        "version": 2,
        "tasks": [],
        "jobs": [],
        "events": [],
        "artifacts": [{
            "relative_path": missing_path,
            "storage_backend": "local",
            "sha256": "a" * 64,
        }],
    }
    fake_config = SimpleNamespace(
        app_version="test",
        images_dir=tmp_path / "images",
        get_storage_backend=lambda: SimpleNamespace(get_backend_info=lambda: {"type": "local"}),
    )
    monkeypatch.setattr("services.backup_service.config", fake_config)
    backup = BackupService()
    backup.set_image_queue_provider(lambda: queue_payload)
    archive = backup._build_backup_archive(
        {"include": {"image_tasks": True, "images": True}},
        trigger="retention",
    )
    restored_payloads = []
    backup.set_image_queue_restore_provider(lambda payload: restored_payloads.append(payload) or {"tasks": 0})

    result = backup.restore_archive_payload(archive, artifact_root=tmp_path / "restored")

    assert result["restored_images"] == 0
    assert restored_payloads[0]["artifacts"][0]["backup_file_included"] is False


def test_backup_rejects_missing_artifact_for_active_task(tmp_path) -> None:
    queue_payload = {
        "version": 2,
        "tasks": [{"id": "task-1", "status": "running", "delivery_status": "pending"}],
        "jobs": [{"id": "job-1", "task_id": "task-1", "status": "running"}],
        "events": [],
        "artifacts": [{
            "task_id": "task-1",
            "job_id": "job-1",
            "relative_path": "task-1/job-1/missing.png",
            "storage_backend": "local",
            "sha256": "a" * 64,
        }],
    }
    backup = BackupService()
    backup.set_image_queue_provider(lambda: queue_payload)
    backup.set_image_queue_artifact_root(tmp_path / "artifacts")

    with pytest.raises(BackupError, match="active image queue artifact"):
        backup._build_backup_archive(
            {"include": {"image_tasks": True, "images": True}},
            trigger="active-missing",
        )


def test_scheduled_backup_builds_and_uploads_from_files(tmp_path, monkeypatch) -> None:
    uploaded: list[tuple[str, bytes]] = []

    class FakeClient:
        prefix = "backups"

        def __init__(self, _settings):
            pass

        def validate(self):
            pass

        def upload_file(self, key, source, **_kwargs):
            uploaded.append((key, source.read_bytes()))
            return {"key": key, "etag": "test"}

        def close(self):
            pass

    fake_config = SimpleNamespace(
        app_version="test",
        images_dir=tmp_path / "images",
        get_storage_backend=lambda: SimpleNamespace(get_backend_info=lambda: {"type": "local"}),
        get_backup_settings=lambda: {
            "encrypt": False,
            "rotation_keep": 0,
            "include": {"image_tasks": False, "images": False},
        },
    )
    monkeypatch.setattr("services.backup_service.config", fake_config)
    monkeypatch.setattr("services.backup_service.CloudflareR2Client", FakeClient)
    backup = BackupService()
    monkeypatch.setattr(
        backup,
        "_build_backup_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("memory archive used")),
    )

    result = backup._run_backup_once(trigger="streaming")

    assert result["size"] == len(uploaded[0][1])
    assert uploaded[0][0].endswith(".tar.gz")


def test_file_backup_uses_streaming_queue_export_provider(tmp_path, monkeypatch) -> None:
    streamed = []

    class QueueProvider:
        def logical_backup(self):
            raise AssertionError("eager logical export used")

        def write_logical_backup(self, output, *, artifact_transform=None):
            streamed.append(True)
            output.write(json.dumps({
                "version": 2,
                "tasks": [],
                "jobs": [],
                "events": [],
                "artifacts": [],
                "account_leases": [],
                "workers": [],
                "legacy_imports": [],
            }))

    fake_config = SimpleNamespace(
        app_version="test",
        images_dir=tmp_path / "images",
        get_storage_backend=lambda: SimpleNamespace(get_backend_info=lambda: {"type": "local"}),
    )
    monkeypatch.setattr("services.backup_service.config", fake_config)
    provider = QueueProvider()
    backup = BackupService()
    backup.set_image_queue_provider(provider.logical_backup)
    destination = tmp_path / "backup.tar.gz"

    backup._build_backup_archive_file(
        {"include": {"image_tasks": True, "images": False}},
        trigger="streaming-queue",
        destination=destination,
    )

    with tarfile.open(destination, mode="r:gz") as archive:
        exported = json.load(archive.extractfile("data/image-queue.json"))
    assert streamed == [True]
    assert exported["version"] == 2


def test_logical_queue_backup_restores_into_empty_database(
    image_task_service,
) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="restore-source",
        prompt="cat",
        model="gpt-image-2",
    )
    exported = image_task_service.repository.logical_backup()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url="sqlite+pysqlite:///:memory:"),
        engine=engine,
        allow_non_postgres=True,
    )
    database.start()
    try:
        restored = ImageQueueRepository(database)

        summary = restored.restore_logical_backup(exported)

        snapshot = restored.get_task("owner-1", task["task_id"])
        assert summary["tasks"] == 1
        assert snapshot is not None and str(snapshot.id) == task["task_id"]
        assert len(restored.list_jobs(snapshot.id)) == 1
    finally:
        database.dispose()


def test_streaming_logical_backup_marks_active_artifacts_required(
    image_task_service,
) -> None:
    image = BytesIO()
    Image.new("RGB", (9, 6), (10, 20, 30)).save(image, format="PNG")
    image_task_service.submit_edit(
        IDENTITY,
        client_task_id="streaming-export",
        prompt="edit",
        model="gpt-image-2",
        images=[(image.getvalue(), "input.png", "image/png")],
    )
    output = StringIO()

    image_task_service.repository.write_logical_backup(output)
    exported = json.loads(output.getvalue())

    assert len(exported["tasks"]) == 1
    assert len(exported["jobs"]) == 1
    assert exported["artifacts"][0]["backup_required"] is True


def test_durable_image_backup_round_trip_restores_database_and_artifact(
    image_task_service,
    tmp_path,
    monkeypatch,
) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="round-trip",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)
    image = BytesIO()
    Image.new("RGB", (7, 5), (3, 4, 5)).save(image, format="PNG")
    artifact = image_task_service.artifact_service.persist_final(
        claim.job.task_id,
        claim.job.id,
        image.getvalue(),
        "https://api.example",
    )
    image_task_service.repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "relative_path": artifact.relative_path, "width": 7, "height": 5},
    )
    monkeypatch.setattr("services.config.DATA_DIR", image_task_service.settings.artifact_root.parent)
    backup = BackupService()
    backup.set_image_queue_provider(image_task_service.repository.logical_backup)
    archive = backup._build_backup_archive(
        {
            "include": {
                "image_tasks": True,
                "images": True,
            },
        },
        trigger="round-trip",
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database = ImageQueueDatabase(
        ImageQueueSettings(database_url="sqlite+pysqlite:///:memory:"),
        engine=engine,
        allow_non_postgres=True,
    )
    database.start()
    try:
        restored = ImageQueueRepository(database)
        backup.set_image_queue_restore_provider(restored.restore_logical_backup)
        restored_root = tmp_path / "r"

        result = backup.restore_archive_payload(
            archive,
            artifact_root=restored_root,
        )

        snapshot = restored.get_task("owner-1", task["task_id"])
        assert snapshot is not None and snapshot.status.value == "success"
        assert result["restored_images"] >= 1
        assert (restored_root / artifact.relative_path).is_file()
    finally:
        database.dispose()


def test_repository_backup_and_retention_protect_unacknowledged_result(image_task_service) -> None:
    task = image_task_service.submit_generation(
        IDENTITY,
        client_task_id="client-1",
        prompt="cat",
        model="gpt-image-2",
    )
    candidate = ImageAccountCandidate(
        account_id=__import__("uuid").UUID("10000000-0000-0000-0000-000000000001"),
        access_token="token",
    )
    claim = image_task_service.repository.claim_next_job("worker-1", [candidate], 1)
    image = BytesIO()
    Image.new("RGB", (5, 4), (1, 2, 3)).save(image, format="PNG")
    artifact = image_task_service.artifact_service.persist_final(
        claim.job.task_id,
        claim.job.id,
        image.getvalue(),
        "https://api.example",
    )
    image_task_service.repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "relative_path": artifact.relative_path, "width": 5, "height": 4},
    )

    backup = image_task_service.repository.logical_backup()
    protected = image_task_service.repository.protected_artifact_paths()

    assert backup["tasks"][0]["id"] == task["task_id"]
    assert artifact.relative_path in protected
    image_task_service.acknowledge(IDENTITY, task["task_id"])
    assert artifact.relative_path not in image_task_service.repository.protected_artifact_paths()


def test_retention_cleanup_skips_protected_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("services.config.DATA_DIR", tmp_path)
    store = ConfigStore(tmp_path / "config.json")
    protected = store.images_dir / "task-1" / "job-1" / "image.png"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"saved")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(protected, (old, old))
    store.set_image_retention_protection(lambda: {"task-1/job-1/image.png"})

    removed = store.cleanup_old_images()

    assert removed == 0
    assert protected.exists()


def test_all_image_paths_provider_blocks_cleanup_while_queue_database_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("services.config.DATA_DIR", tmp_path)
    store = ConfigStore(tmp_path / "config.json")
    artifact = store.images_dir / "task-1" / "job-1" / "image.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"durable-image")
    old = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(artifact, (old, old))
    store.set_image_retention_protection(store.all_image_paths)

    removed = store.cleanup_old_images()

    assert removed == 0
    assert artifact.exists()


def test_all_gallery_mutations_skip_protected_queue_artifact(tmp_path, monkeypatch) -> None:
    import shutil

    from services import image_service

    monkeypatch.setattr("services.config.DATA_DIR", tmp_path)
    store = ConfigStore(tmp_path / "config.json")
    protected = store.images_dir / "task-1" / "job-1" / "image.png"
    protected.parent.mkdir(parents=True)
    image = Image.new("RGB", (64, 64), (20, 40, 60))
    image.save(protected, format="PNG", compress_level=0)
    original = protected.read_bytes()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(protected, (old, old))
    store.set_image_retention_protection(lambda: {"task-1/job-1/image.png"})
    monkeypatch.setattr(image_service, "config", store)
    monkeypatch.setattr(
        image_service.image_storage_service,
        "delete",
        lambda path: (_ for _ in ()).throw(AssertionError(f"deleted protected path: {path}")),
    )

    preview = image_service.preview_image_retention_cleanup(30)
    cleanup = image_service.cleanup_image_retention(30)
    deleted = image_service.delete_images(["task-1/job-1/image.png"])
    compressed = image_service.compress_images()
    free_mb = shutil.disk_usage(store.images_dir).free // (1024 * 1024)
    pressure = image_service.delete_to_target(free_mb + 1, dry_run=True)

    assert preview["removed"] == 0
    assert cleanup["removed"] == 0
    assert deleted == {"removed": 0, "protected": 1}
    assert compressed["compressed"] == 0
    assert pressure["removed"] == 0
    assert protected.read_bytes() == original
