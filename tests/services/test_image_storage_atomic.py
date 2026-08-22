from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from services import image_service
from services import image_storage_service as storage_module
from services.image_storage_service import ImageStorageService, _atomic_write_local


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 4), (20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


def test_atomic_local_write_supports_stable_hash_filename(tmp_path: Path) -> None:
    target = tmp_path / ("a" * 64 + ".png")
    payload = _png_bytes()

    _atomic_write_local(target, payload)

    assert target.read_bytes() == payload
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_local_write_does_not_publish_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ("b" * 64 + ".png")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("services.image_storage_service.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        _atomic_write_local(target, _png_bytes())

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_private_queue_artifact_path_is_never_served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    private = tmp_path / task_id / "input" / "secret.png"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"secret")
    monkeypatch.setattr("services.image_storage_service._local_image_path", lambda rel: tmp_path / rel)
    service = ImageStorageService()

    with pytest.raises(HTTPException) as exc_info:
        service.get_bytes(f"{task_id}/input/secret.png")

    assert exc_info.value.status_code == 404


def test_private_queue_artifact_is_not_served_by_local_file_shortcut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    private_rel = f"{task_id}/input/secret.png"
    private = tmp_path / private_rel
    private.parent.mkdir(parents=True)
    private.write_bytes(_png_bytes())
    monkeypatch.setattr(storage_module, "config", SimpleNamespace(images_dir=tmp_path))
    monkeypatch.setattr(image_service, "config", SimpleNamespace(images_dir=tmp_path))
    service = ImageStorageService(tmp_path / "index.json")
    monkeypatch.setattr(image_service, "image_storage_service", service)

    with pytest.raises(HTTPException) as exc_info:
        image_service.get_image_response(private_rel)

    assert exc_info.value.status_code == 404


def test_cached_thumbnail_cannot_bypass_private_queue_artifact_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    private_rel = f"{task_id}/input/secret.png"
    images_dir = tmp_path / "images"
    thumbnails_dir = tmp_path / "thumbnails"
    cached = thumbnails_dir / f"{private_rel}.png"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_png_bytes())
    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(images_dir=images_dir, base_url="", get_image_storage_settings=lambda: {"mode": "local"}),
    )
    monkeypatch.setattr(
        image_service,
        "config",
        SimpleNamespace(images_dir=images_dir, image_thumbnails_dir=thumbnails_dir),
    )
    monkeypatch.setattr(image_service, "image_storage_service", ImageStorageService(tmp_path / "index.json"))

    with pytest.raises(HTTPException) as exc_info:
        image_service.ensure_thumbnail(private_rel)

    assert exc_info.value.status_code == 404


def test_historical_private_webdav_copy_and_thumbnail_are_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    private_rel = f"{task_id}/input/secret.png"
    public_rel = "2026/07/28/public.png"
    thumbnails_dir = tmp_path / "thumbnails"
    cached = thumbnails_dir / f"{private_rel}.png"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_png_bytes())
    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            image_thumbnails_dir=thumbnails_dir,
            base_url="",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    deleted: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            deleted.append(rel)
            return True

    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        private_rel: {"rel": private_rel, "webdav": True, "local": True},
        public_rel: {"rel": public_rel, "webdav": True, "local": False},
    })

    result = service.cleanup_private_public_copies()

    assert result == {"removed": 1, "failed": 0}
    assert deleted == [private_rel]
    assert not cached.exists()
    assert set(service._load_index()) == {public_rel}


def test_private_queue_artifacts_are_hidden_from_gallery_and_webdav_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    job_id = str(uuid4())
    private_rel = f"{task_id}/{job_id}/d/secret.png"
    public_rel = f"{task_id}/{job_id}/public.png"
    for rel in (private_rel, public_rel):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_png_bytes())
    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(images_dir=tmp_path, base_url="", get_image_storage_settings=lambda: {"mode": "both"}),
    )
    uploaded: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def put(self, rel, payload):
            uploaded.append(rel)
            return f"https://cdn.example/{rel}"

    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")

    listed = service.list_items("https://api.example")
    result = service.sync_all()

    assert [item["rel"] for item in listed] == [public_rel]
    assert uploaded == [public_rel]
    assert result["uploaded"] == 1
