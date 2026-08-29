from __future__ import annotations

from hashlib import sha256
import contextlib
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from uuid import uuid4

import pytest
from PIL import Image

from services.image_queue.artifact_service import (
    ArtifactService,
    ArtifactDescriptor,
    InvalidImageArtifact,
)
from services.image_queue.types import ArtifactStatus


def _image_bytes(format_name: str, size: tuple[int, int] = (64, 32)) -> bytes:
    image = Image.new("RGB", size, (180, 80, 40))
    buffer = BytesIO()
    image.save(buffer, format=format_name)
    return buffer.getvalue()


@pytest.fixture
def artifact_service(tmp_path: Path) -> ArtifactService:
    return ArtifactService(tmp_path / "images")


def test_invalid_image_bytes_are_rejected(artifact_service: ArtifactService) -> None:
    with pytest.raises(InvalidImageArtifact):
        artifact_service.persist_input(uuid4(), b"not-image", "bad.png", "image/png")


def test_final_artifact_is_png_and_uses_content_hash(artifact_service: ArtifactService) -> None:
    task_id, job_id = uuid4(), uuid4()

    artifact = artifact_service.persist_final(
        task_id,
        job_id,
        _image_bytes("JPEG"),
        "https://images.example",
    )

    assert artifact.absolute_path is not None
    stored = artifact.absolute_path.read_bytes()
    assert artifact.relative_path == f"{task_id}/{job_id}/{sha256(stored).hexdigest()}.png"
    assert stored.startswith(b"\x89PNG\r\n\x1a\n")
    assert (artifact.width, artifact.height) == (64, 32)
    assert artifact.byte_size == len(stored)
    assert artifact.public_url == f"https://images.example/images/{artifact.relative_path}"


def test_repeating_same_final_save_is_idempotent(artifact_service: ArtifactService) -> None:
    task_id, job_id = uuid4(), uuid4()
    payload = _image_bytes("PNG")

    first = artifact_service.persist_final(task_id, job_id, payload, "https://images.example")
    second = artifact_service.persist_final(task_id, job_id, payload, "https://images.example")

    assert second.relative_path == first.relative_path
    assert second.absolute_path == first.absolute_path
    assert first.absolute_path is not None
    assert first.absolute_path.read_bytes() == second.absolute_path.read_bytes()


def test_atomic_save_never_publishes_empty_destination(
    artifact_service: ArtifactService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("services.image_queue.artifact_service.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        artifact_service.persist_final(
            uuid4(),
            uuid4(),
            _image_bytes("PNG"),
            "https://images.example",
        )

    assert list(artifact_service.root.rglob("*.png")) == []


def test_input_artifact_is_persisted_before_queueing(artifact_service: ArtifactService) -> None:
    task_id = uuid4()

    artifact = artifact_service.persist_input(
        task_id,
        _image_bytes("WEBP", (20, 10)),
        "reference.webp",
        "image/webp",
    )

    assert artifact.job_id is None
    assert artifact.relative_path.startswith(f"{task_id}/input/")
    assert artifact.absolute_path is not None and artifact.absolute_path.is_file()
    assert (artifact.width, artifact.height) == (20, 10)


def test_remote_storage_is_called_after_local_recovery_point(tmp_path: Path) -> None:
    root = tmp_path / "images"

    class RemoteStorage:
        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            assert (root / relative_path).read_bytes() == payload
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def exists(self, relative_path: str) -> bool:
            return (root / relative_path).is_file()

        def get_bytes(self, relative_path: str) -> bytes:
            return (root / relative_path).read_bytes()

    service = ArtifactService(root, RemoteStorage())

    artifact = service.persist_final(
        uuid4(),
        uuid4(),
        _image_bytes("PNG"),
        "https://images.example",
    )

    assert artifact.absolute_path is not None and artifact.absolute_path.is_file()
    assert artifact.storage_backend == "webdav"


def test_final_artifact_verifies_remote_copy_instead_of_local_copy(tmp_path: Path) -> None:
    root = tmp_path / "images"
    payload = _image_bytes("PNG")

    class RemoteStorage:
        def __init__(self) -> None:
            self.local_payload = b""

        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            assert (root / relative_path).read_bytes() == payload
            self.local_payload = payload
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def get_artifact_bytes(self, relative_path: str) -> bytes:
            # This is the local-first reader used by the artifact path.
            return self.local_payload

        def get_remote_bytes(self, relative_path: str) -> bytes:
            return b"broken-remote-payload"

        def delete(self, relative_path: str) -> bool:
            path = root / relative_path
            path.unlink(missing_ok=True)
            return True

    service = ArtifactService(root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_final(
            uuid4(),
            uuid4(),
            payload,
            "https://images.example",
        )


def test_local_storage_does_not_require_a_remote_reader(tmp_path: Path) -> None:
    root = tmp_path / "images"
    payload = _image_bytes("PNG")

    class LocalStorage:
        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            assert (root / relative_path).read_bytes() == payload
            return SimpleNamespace(url=f"https://images.example/{relative_path}", storage="local")

        def get_remote_bytes(self, relative_path: str) -> bytes:
            raise AssertionError("local storage must not require a remote read")

    service = ArtifactService(root, LocalStorage())

    artifact = service.persist_final(
        uuid4(),
        uuid4(),
        payload,
        "https://images.example",
    )

    assert artifact.storage_backend == "local"
    assert artifact.absolute_path is not None and artifact.absolute_path.read_bytes()


def test_remote_rollback_restores_storage_index_entry(tmp_path: Path) -> None:
    from services.image_queue.artifact_service import _restore_index_entry

    relative_path = f"{uuid4()}/{uuid4()}/result.png"
    saved: dict[str, dict[str, object]] = {}

    class Storage:
        _index_lock = RLock()

        def _load_clean_index(self):
            return dict(saved)

        def _save_index(self, items):
            saved.clear()
            saved.update(items)

        def _index_file_lock_path(self):
            return tmp_path / "image-index.lock"

    previous_item = {"rel": relative_path, "webdav": True, "storage": "webdav"}

    _restore_index_entry(Storage(), relative_path, previous_item)

    assert saved[relative_path] == previous_item


def test_final_artifact_is_rolled_back_when_remote_verification_fails(tmp_path: Path) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"

    class RemoteStorage:
        def _load_clean_index(self):
            return {
                relative_path: {
                    "rel": relative_path,
                    "path": relative_path,
                    "webdav": True,
                }
            }

        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            local = artifact_root / relative_path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(payload)
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def get_bytes(self, relative_path: str) -> bytes:
            return b"broken"

        def delete(self, relative_path: str) -> bool:
            removed = False
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
                removed = True
            if remote.is_file():
                remote.unlink()
                removed = True
            return removed

    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_final(uuid4(), uuid4(), _image_bytes("PNG"), "https://images.example")

    assert list(artifact_root.rglob("*.png")) == []
    assert list(remote_root.rglob("*.png")) == []


def test_existing_final_artifact_is_restored_when_remote_verification_fails(tmp_path: Path) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    task_id, job_id = uuid4(), uuid4()
    payload = _image_bytes("PNG")
    old_payload = _image_bytes("WEBP")
    digest = sha256(payload).hexdigest()
    relative_path = f"{task_id}/{job_id}/{digest}.png"
    existing = artifact_root / relative_path
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(old_payload)

    class RemoteStorage:
        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            local = artifact_root / relative_path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(payload)
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def get_bytes(self, relative_path: str) -> bytes:
            return b"broken"

        def delete(self, relative_path: str) -> bool:
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
            if remote.is_file():
                remote.unlink()
            return True

    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_final(task_id, job_id, payload, "https://images.example")

    assert existing.read_bytes() == old_payload
    assert list(remote_root.rglob("*.png")) == []


def test_remote_only_existing_final_artifact_is_restored_when_remote_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    task_id, job_id = uuid4(), uuid4()
    payload = _image_bytes("PNG")
    old_payload = _image_bytes("WEBP")
    digest = sha256(payload).hexdigest()
    relative_path = f"{task_id}/{job_id}/{digest}.png"
    remote_before = remote_root / relative_path
    remote_before.parent.mkdir(parents=True, exist_ok=True)
    remote_before.write_bytes(old_payload)

    class RestoreClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, relative_path: str, payload: bytes, content_type=None):
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return f"https://cdn.example/{relative_path}"

    @contextlib.contextmanager
    def fake_webdav_client(settings):
        yield RestoreClient(settings)

    class RemoteStorage:
        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            local = artifact_root / relative_path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(payload)
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def get_bytes(self, relative_path: str) -> bytes:
            return b"broken"

        def delete(self, relative_path: str) -> bool:
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
            if remote.is_file():
                remote.unlink()
            return True

        def settings(self):
            return {"webdav_url": "https://cdn.example"}

    monkeypatch.setattr("services.image_queue.artifact_service._webdav_client", fake_webdav_client)
    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_final(task_id, job_id, payload, "https://images.example")

    assert not (artifact_root / relative_path).exists()
    assert remote_before.read_bytes() == old_payload


def test_private_remote_artifact_is_rolled_back_on_verification_failure(tmp_path: Path) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"

    class RemoteStorage:
        def save_private_at_path(self, relative_path: str, payload: bytes) -> object:
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(storage="private_webdav", url="")

        def get_artifact_bytes(self, relative_path: str) -> bytes:
            return b"broken"

        def delete_artifact(self, relative_path: str) -> bool:
            removed = False
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
                removed = True
            if remote.is_file():
                remote.unlink()
                removed = True
            return removed

    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_input(uuid4(), _image_bytes("PNG"), "input.png", "image/png")

    assert list(artifact_root.rglob("*.png")) == []
    assert list(remote_root.rglob("*.png")) == []


def test_private_existing_artifact_is_restored_when_verification_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    task_id = uuid4()
    payload = _image_bytes("PNG")
    old_payload = _image_bytes("WEBP")
    digest = sha256(payload).hexdigest()
    relative_path = f"{task_id}/input/0001-{digest}.png"
    local_before = artifact_root / relative_path
    remote_before = remote_root / relative_path
    local_before.parent.mkdir(parents=True, exist_ok=True)
    remote_before.parent.mkdir(parents=True, exist_ok=True)
    local_before.write_bytes(old_payload)
    remote_before.write_bytes(old_payload)

    class RestoreClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, relative_path: str, payload: bytes, content_type=None):
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return f"https://cdn.example/{relative_path}"

    @contextlib.contextmanager
    def fake_webdav_client(settings):
        yield RestoreClient(settings)

    class RemoteStorage:
        def __init__(self) -> None:
            self.calls = 0

        def save_private_at_path(self, relative_path: str, payload: bytes) -> object:
            local = artifact_root / relative_path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(payload)
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(storage="private_webdav", url="")

        def get_artifact_bytes(self, relative_path: str) -> bytes:
            self.calls += 1
            return old_payload if self.calls == 1 else b"broken"

        def delete_artifact(self, relative_path: str) -> bool:
            removed = False
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
                removed = True
            if remote.is_file():
                remote.unlink()
                removed = True
            return removed

        def settings(self):
            return {
                "webdav_url": "https://private.example",
                "webdav_username": "",
                "webdav_password": "",
                "webdav_root_path": "chatgpt2api/private",
            }

    monkeypatch.setattr("services.image_queue.artifact_service._webdav_client", fake_webdav_client)
    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.persist_input(task_id, payload, "input.png", "image/png")

    assert local_before.read_bytes() == old_payload
    assert remote_before.read_bytes() == old_payload


def test_recover_final_restores_previous_local_and_remote_when_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    task_id, job_id = uuid4(), uuid4()
    payload = _image_bytes("PNG")
    old_payload = _image_bytes("WEBP")
    digest = sha256(payload).hexdigest()
    relative_path = f"{task_id}/{job_id}/{digest}.png"
    local_before = artifact_root / relative_path
    remote_before = remote_root / relative_path
    local_before.parent.mkdir(parents=True, exist_ok=True)
    remote_before.parent.mkdir(parents=True, exist_ok=True)
    local_before.write_bytes(payload)
    remote_before.write_bytes(old_payload)

    class RestoreClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, relative_path: str, payload: bytes, content_type=None):
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return f"https://cdn.example/{relative_path}"

    @contextlib.contextmanager
    def fake_webdav_client(settings):
        yield RestoreClient(settings)

    class RemoteStorage:
        def __init__(self) -> None:
            self.calls = 0

        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            local = artifact_root / relative_path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(payload)
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

        def get_bytes(self, relative_path: str) -> bytes:
            self.calls += 1
            return old_payload if self.calls == 1 else b"broken"

        def delete(self, relative_path: str) -> bool:
            removed = False
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
                removed = True
            if remote.is_file():
                remote.unlink()
                removed = True
            return removed

        def settings(self):
            return {
                "webdav_url": "https://cdn.example",
                "webdav_username": "",
                "webdav_password": "",
                "webdav_root_path": "chatgpt2api/images",
            }

    monkeypatch.setattr("services.image_queue.artifact_service._webdav_client", fake_webdav_client)
    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(InvalidImageArtifact, match="remote artifact is unreadable"):
        service.recover_final(task_id, job_id, "https://api.example")

    assert local_before.read_bytes() == payload
    assert remote_before.read_bytes() == old_payload


def test_discard_cleans_local_and_remote_artifacts(tmp_path: Path) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    rel = f"{uuid4()}/input/secret.png"
    local = artifact_root / rel
    remote = remote_root / rel
    local.parent.mkdir(parents=True, exist_ok=True)
    remote.parent.mkdir(parents=True, exist_ok=True)
    payload = _image_bytes("PNG")
    local.write_bytes(payload)
    remote.write_bytes(payload)

    class RemoteStorage:
        def delete_artifact(self, relative_path: str) -> bool:
            target = remote_root / relative_path
            if target.is_file():
                target.unlink()
                return True
            return False

    service = ArtifactService(artifact_root, RemoteStorage())
    artifact = ArtifactDescriptor(
        task_id=uuid4(),
        job_id=None,
        kind="input",
        status=ArtifactStatus.READY,
        relative_path=rel,
        sha256=sha256(payload).hexdigest(),
        mime_type="image/png",
        byte_size=len(payload),
        width=8,
        height=4,
    )

    assert service.discard((artifact,)) is True
    assert not local.exists()
    assert not remote.exists()


def test_preexisting_private_artifact_is_preserved_when_private_upload_fails(tmp_path: Path) -> None:
    artifact_root = tmp_path / "images"
    remote_root = tmp_path / "remote"
    task_id = uuid4()
    payload = _image_bytes("PNG")
    digest = sha256(payload).hexdigest()
    relative_path = f"{task_id}/input/0001-{digest}.png"
    preexisting = artifact_root / relative_path
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_bytes(payload)

    class RemoteStorage:
        def save_private_at_path(self, relative_path: str, payload: bytes) -> object:
            remote = remote_root / relative_path
            remote.parent.mkdir(parents=True, exist_ok=True)
            remote.write_bytes(payload)
            raise RuntimeError("upload failed")

        def delete_artifact(self, relative_path: str) -> bool:
            removed = False
            local = artifact_root / relative_path
            remote = remote_root / relative_path
            if local.is_file():
                local.unlink()
                removed = True
            if remote.is_file():
                remote.unlink()
                removed = True
            return removed

    service = ArtifactService(artifact_root, RemoteStorage())

    with pytest.raises(RuntimeError, match="upload failed"):
        service.persist_input(task_id, payload, "input.png", "image/png")

    assert preexisting.is_file()
    assert preexisting.read_bytes() == payload
    assert list(remote_root.rglob("*.png")) == []


def test_private_artifacts_are_never_published_to_remote_storage(tmp_path: Path) -> None:
    published: list[str] = []

    class RemoteStorage:
        def save_at_path(self, relative_path: str, payload: bytes, base_url: str) -> object:
            published.append(relative_path)
            return SimpleNamespace(url=f"https://cdn.example/{relative_path}", storage="webdav")

    service = ArtifactService(tmp_path / "images", RemoteStorage())
    task_id, job_id = uuid4(), uuid4()

    input_artifact = service.persist_input(task_id, _image_bytes("PNG"), "input.png", "image/png")
    stage_artifact = service.persist_stage(task_id, job_id, _image_bytes("PNG"), "downloaded")
    final_artifact = service.persist_final(task_id, job_id, _image_bytes("PNG"), "https://api.example")

    assert input_artifact.public_url == ""
    assert stage_artifact.public_url == ""
    assert published == [final_artifact.relative_path]


def test_normalized_artifact_enforces_pixel_limit(
    artifact_service: ArtifactService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.image_queue.artifact_service.MAX_ARTIFACT_PIXELS", 100)

    with pytest.raises(InvalidImageArtifact, match="pixel"):
        artifact_service.persist_input(
            uuid4(),
            _image_bytes("JPEG", (20, 20)),
            "large.jpg",
            "image/jpeg",
        )


def test_stage_artifact_is_separate_from_final_and_recoverable_after_crash(
    artifact_service: ArtifactService,
) -> None:
    task_id = uuid4()
    job_id = uuid4()
    payload = _image_bytes("WEBP", (18, 12))

    downloaded = artifact_service.persist_stage(task_id, job_id, payload, "downloaded")
    recovered = artifact_service.recover_stage(task_id, job_id, "downloaded")

    assert downloaded.kind == "downloaded"
    assert f"/{job_id}/d/" in f"/{downloaded.relative_path}"
    assert recovered is not None
    assert recovered.sha256 == downloaded.sha256
    assert artifact_service.read(recovered) == downloaded.absolute_path.read_bytes()
    assert artifact_service.recover_final(task_id, job_id, "https://api.example") is None


def test_recovery_uses_native_stat_for_long_windows_paths(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows long-path recovery regression")
    task_id = uuid4()
    job_id = uuid4()
    root = tmp_path / "images"
    for padding_length in range(0, 96):
        padded_root = tmp_path / ("r" * padding_length) / "images" if padding_length else tmp_path / "images"
        parent_path = padded_root / str(task_id) / str(job_id) / "d"
        final_path = parent_path / (("a" * 64) + ".png")
        if len(str(parent_path)) < 250 and len(str(final_path)) > 260:
            root = padded_root
            break
    else:
        pytest.skip("could not create a Windows long-path recovery fixture")
    service = ArtifactService(root)

    downloaded = service.persist_stage(
        task_id,
        job_id,
        _image_bytes("PNG", (18, 12)),
        "downloaded",
    )

    assert len(str(service.root / downloaded.relative_path)) > 260
    recovered = service.recover_stage(task_id, job_id, "downloaded")

    assert recovered is not None
    assert recovered.sha256 == downloaded.sha256


def test_recovery_selects_preferred_or_newest_valid_artifact(
    artifact_service: ArtifactService,
) -> None:
    task_id = uuid4()
    job_id = uuid4()
    first = artifact_service.persist_stage(
        task_id,
        job_id,
        _image_bytes("PNG", (12, 8)),
        "downloaded",
    )
    second = artifact_service.persist_stage(
        task_id,
        job_id,
        _image_bytes("PNG", (13, 9)),
        "downloaded",
    )
    os.utime(first.absolute_path, (1, 1))
    os.utime(second.absolute_path, (2, 2))

    preferred = artifact_service.recover_stage(
        task_id,
        job_id,
        "downloaded",
        preferred_sha256=first.sha256,
    )
    newest = artifact_service.recover_stage(task_id, job_id, "downloaded")

    assert preferred is not None and preferred.sha256 == first.sha256
    assert newest is not None and newest.sha256 == second.sha256
