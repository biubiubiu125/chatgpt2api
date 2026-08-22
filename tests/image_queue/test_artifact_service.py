from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from services.image_queue.artifact_service import (
    ArtifactService,
    InvalidImageArtifact,
)


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
