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


def _png_bytes(size: tuple[int, int] = (8, 4)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 4), (20, 40, 60)).save(output, format=image_format)
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


@pytest.mark.parametrize("reader_name", ["get_bytes", "get_published_bytes", "get_remote_bytes"])
def test_unindexed_public_webdav_object_is_not_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reader_name: str,
) -> None:
    calls: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            calls.append("init")

        def get(self, rel):
            calls.append(rel)
            return b"unindexed object"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")

    with pytest.raises(HTTPException) as exc_info:
        getattr(service, reader_name)("2026/08/27/unindexed.png")

    assert exc_info.value.status_code == 404
    assert calls == []


def test_index_string_false_webdav_is_not_treated_as_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = "2026/08/25/image.png"
    calls: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            calls.append("init")

        def get(self, requested):
            calls.append(requested)
            return _png_bytes()

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")
    storage_module.write_json_file(
        service.index_file,
        {
            "items": {
                rel: {
                    "rel": rel,
                    "webdav": "false",
                    "local": "false",
                    "storage": "webdav",
                }
            }
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        service.get_bytes(rel)

    assert exc_info.value.status_code == 404
    assert calls == []


def test_index_storage_webdav_without_boolean_flags_is_treated_as_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = "2026/08/25/image.png"
    calls: list[str] = []
    payload = _png_bytes()

    class FakeWebDAVClient:
        def __init__(self, settings):
            calls.append("init")

        def get(self, requested):
            calls.append(requested)
            return payload

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")
    storage_module.write_json_file(
        service.index_file,
        {
            "items": {
                rel: {
                    "rel": rel,
                    "storage": "webdav",
                }
            }
        },
    )

    assert service.get_bytes(rel) == payload
    assert calls == ["init", rel]


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


def test_thumbnail_write_does_not_publish_partial_file_on_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images_dir = tmp_path / "images"
    thumbnails_dir = tmp_path / "thumbnails"
    rel = "2026/08/27/source.png"
    source = images_dir / rel
    source.parent.mkdir(parents=True)
    source.write_bytes(_png_bytes())
    target = thumbnails_dir / f"{rel}.png"
    monkeypatch.setattr(
        image_service,
        "config",
        SimpleNamespace(images_dir=images_dir, image_thumbnails_dir=thumbnails_dir),
    )
    monkeypatch.setattr(
        image_service,
        "image_storage_service",
        SimpleNamespace(has_local=lambda path: True, exists=lambda path: True),
    )

    def fail_after_partial_write(self, fp, *args, **kwargs):
        if hasattr(fp, "write"):
            fp.write(b"partial thumbnail")
        else:
            partial_target = Path(fp)
            partial_target.parent.mkdir(parents=True, exist_ok=True)
            partial_target.write_bytes(b"partial thumbnail")
        raise OSError("thumbnail save failed")

    monkeypatch.setattr(image_service.Image.Image, "save", fail_after_partial_write)

    with pytest.raises(HTTPException) as exc_info:
        image_service.ensure_thumbnail(rel)

    assert exc_info.value.status_code == 422
    assert not target.exists()
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


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


def test_historical_private_webdav_storage_only_copy_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    private_rel = f"{task_id}/input/secret.png"
    thumbnails_dir = tmp_path / "thumbnails"
    cached = thumbnails_dir / f"{private_rel}.png"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_png_bytes())
    deleted: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            deleted.append(rel)
            return True

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
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        private_rel: {"rel": private_rel, "storage": "private_webdav"},
    })

    result = service.cleanup_private_public_copies()

    assert result == {"removed": 1, "failed": 0}
    assert deleted == [private_rel]
    assert not cached.exists()
    assert service._load_index() == {}


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

        def put(self, rel, payload, content_type=None):
            uploaded.append(rel)
            return f"https://cdn.example/{rel}"

    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")

    listed = service.list_items("https://api.example")
    result = service.sync_all()

    assert [item["rel"] for item in listed] == [public_rel]
    assert uploaded == [public_rel]
    assert result["uploaded"] == 1


def test_private_queue_artifacts_remain_hidden_when_cleanup_delete_fails(
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

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            if rel == private_rel:
                raise OSError("temporary delete failure")
            return True

    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        private_rel: {"rel": private_rel, "storage": "private_webdav"},
        public_rel: {"rel": public_rel, "webdav": True, "local": True},
    })

    listed = service.list_items("https://api.example")

    assert [item["rel"] for item in listed] == [public_rel]
    assert private_rel in service._load_index()


def test_public_base_url_without_images_path_still_builds_images_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "local",
                "public_base_url": "https://cdn.example.com",
            },
        ),
    )
    service = ImageStorageService(tmp_path / "index.json")

    assert service._public_url("2026/08/27/a.png") == "https://cdn.example.com/images/2026/08/27/a.png"


def test_webdav_save_uses_content_type_for_image_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, rel, payload, content_type="image/png"):
            captured.append(content_type)
            return f"https://dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)

    ImageStorageService(tmp_path / "index.json").save_at_path(
        "2026/08/25/image.jpg",
        _image_bytes("JPEG"),
    )

    assert captured == ["image/jpeg"]


def test_private_webdav_save_uses_content_type_for_image_extension(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str | None] = []
    task_id = str(uuid4())
    relative_path = f"{task_id}/input/source.png"

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, rel, payload, content_type=None):
            captured.append(content_type)
            return f"https://dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example/public",
                "private_webdav_url": "https://dav.example/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)

    ImageStorageService(tmp_path / "index.json").save_private_at_path(
        relative_path,
        _png_bytes(),
    )

    assert captured == ["image/png"]


def test_private_webdav_upload_failure_preserves_existing_remote_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = str(uuid4())
    relative_path = f"{task_id}/input/source.png"
    old_payload = _png_bytes()
    put_payloads: list[bytes] = []
    delete_calls: list[str] = []

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def close(self):
            pass

        def get(self, rel):
            return old_payload

        def put_atomic(self, rel, payload, content_type=None):
            put_payloads.append(payload)
            if payload != old_payload:
                raise RuntimeError("upload failed")
            return f"https://dav.example/{rel}"

        def delete(self, rel):
            delete_calls.append(rel)
            return True

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example/public",
                "private_webdav_url": "https://dav.example/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)

    with pytest.raises(RuntimeError, match="upload failed"):
        ImageStorageService(tmp_path / "index.json").save_private_at_path(
            relative_path,
            _png_bytes(size=(32, 16)),
        )

    assert put_payloads[0] != old_payload
    assert put_payloads[1:] == [old_payload]
    assert delete_calls == []


@pytest.mark.parametrize(
    ("extension", "image_format"),
    [(".gif", "GIF"), (".bmp", "BMP")],
)
def test_supported_non_png_images_can_be_listed_and_retrieved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
    image_format: str,
) -> None:
    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {"mode": "local"},
        ),
    )
    service = ImageStorageService(tmp_path / "index.json")
    rel = f"2026/08/25/image{extension}"
    payload = _image_bytes(image_format)

    stored = service.save_at_path(rel, payload)

    assert stored.storage == "local"
    assert service.get_bytes(rel) == payload
    assert [item["rel"] for item in service.list_items("https://api.example")] == [rel]


def test_public_save_rolls_back_published_files_when_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class FakeWebDAVClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, rel, payload, content_type="image/png"):
            remote[rel] = bytes(payload)
            return f"https://dav.example/{rel}"

        def delete(self, rel):
            remote.pop(rel, None)
            return True

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FakeWebDAVClient)
    service = ImageStorageService(tmp_path / "index.json")

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)
    rel = "2026/08/25/orphan.png"

    with pytest.raises(OSError, match="index write failed"):
        service.save_at_path(rel, _png_bytes())

    assert not (tmp_path / "images" / rel).exists()
    assert rel not in remote


def test_public_save_removes_new_remote_when_previous_remote_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class FallbackDeleteClient:
        def __init__(self, settings):
            pass

        def get(self, rel):
            raise storage_module.ImageStorageError("remote fetch failed")

        def put_atomic(self, rel, payload, content_type="image/png"):
            remote[rel] = bytes(payload)
            return f"https://dav.example/{rel}"

        def delete(self, rel):
            remote.pop(rel, None)
            return True

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FallbackDeleteClient)
    rel = "2026/08/25/orphan-webdav.png"
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "webdav",
        },
    })

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)

    remote[rel] = b"old"

    with pytest.raises(OSError, match="index write failed"):
        service.save_at_path(rel, _png_bytes())

    assert remote == {}


def test_delete_restores_remote_when_remote_only_and_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorableDeleteClient:
        def __init__(self, settings):
            pass

        def get(self, rel):
            return remote[rel]

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorableDeleteClient)
    rel = "2026/08/25/remote-only.png"
    payload = _png_bytes()
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "webdav",
        },
    })

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)

    with pytest.raises(OSError, match="index write failed"):
        service.delete(rel)

    assert remote[rel] == payload
    assert rel in service._load_index()


def test_delete_artifact_restores_remote_when_remote_only_and_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorablePrivateDeleteClient:
        def __init__(self, settings):
            pass

        def get(self, rel):
            return remote[rel]

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://private-dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
                "private_webdav_url": "https://private-dav.example",
                "private_webdav_root_path": "chatgpt2api/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorablePrivateDeleteClient)
    rel = f"{uuid4()}/input/secret.png"
    payload = _png_bytes()
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "private_webdav",
        },
    })

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)

    with pytest.raises(OSError, match="index write failed"):
        service.delete_artifact(rel)

    assert remote[rel] == payload
    assert rel in service._load_index()


def test_delete_returns_true_when_only_index_entry_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RemoteOnlyDeleteClient:
        def __init__(self, settings):
            pass

        def get(self, rel):
            return b"remote"

        def delete(self, rel):
            return False

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RemoteOnlyDeleteClient)
    rel = "2026/08/25/index-only.png"
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "webdav",
        },
    })

    assert service.delete(rel) is True
    assert rel not in service._load_index()


def test_delete_artifact_returns_true_when_only_index_entry_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RemoteOnlyDeleteClient:
        def __init__(self, settings):
            pass

        def get(self, rel):
            return b"remote"

        def delete(self, rel):
            return False

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
                "private_webdav_url": "https://private-dav.example",
                "private_webdav_root_path": "chatgpt2api/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RemoteOnlyDeleteClient)
    rel = f"{uuid4()}/input/index-only.png"
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "private_webdav",
        },
    })

    assert service.delete_artifact(rel) is True
    assert rel not in service._load_index()


def test_public_save_rolls_back_local_file_when_remote_upload_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingWebDAVClient:
        def __init__(self, settings):
            pass

        def put_atomic(self, rel, payload, content_type="image/png"):
            raise storage_module.ImageStorageError("remote upload failed")

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FailingWebDAVClient)
    rel = "2026/08/25/remote-failure.png"
    service = ImageStorageService(tmp_path / "index.json")

    with pytest.raises(storage_module.ImageStorageError, match="remote upload failed"):
        service.save_at_path(rel, _png_bytes())

    assert not (tmp_path / "images" / rel).exists()


def test_delete_preserves_local_and_index_when_remote_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingDeleteClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            raise storage_module.ImageStorageError("remote delete failed")

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", FailingDeleteClient)
    rel = "2026/08/25/both.png"
    local = tmp_path / "images" / rel
    local.parent.mkdir(parents=True)
    local.write_bytes(_png_bytes())
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": True,
            "webdav": True,
            "storage": "both",
        },
    })

    with pytest.raises(storage_module.ImageStorageError, match="remote delete failed"):
        service.delete(rel)

    assert local.exists()
    assert rel in service._load_index()


def test_delete_restores_remote_when_local_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorableDeleteClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorableDeleteClient)
    rel = "2026/08/25/both.png"
    payload = _png_bytes()
    local = tmp_path / "images" / rel
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": True,
            "webdav": True,
            "storage": "both",
        },
    })

    original_unlink = storage_module.Path.unlink

    def fail_unlink(self, *args, **kwargs):
        if self == local:
            raise OSError("unlink failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(storage_module.Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match="unlink failed"):
        service.delete(rel)

    assert local.exists()
    assert remote[rel] == payload
    assert rel in service._load_index()


def test_delete_restores_local_and_remote_when_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorableDeleteClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "both",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorableDeleteClient)
    rel = "2026/08/25/both.png"
    payload = _png_bytes()
    local = tmp_path / "images" / rel
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": True,
            "webdav": True,
            "storage": "both",
        },
    })

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)

    with pytest.raises(OSError, match="index write failed"):
        service.delete(rel)

    assert local.exists()
    assert remote[rel] == payload
    assert rel in service._load_index()


def test_delete_artifact_restores_remote_when_local_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorablePrivateDeleteClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://private-dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
                "private_webdav_url": "https://private-dav.example",
                "private_webdav_root_path": "chatgpt2api/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorablePrivateDeleteClient)
    rel = f"{uuid4()}/input/secret.png"
    payload = _png_bytes()
    local = tmp_path / "images" / rel
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": True,
            "webdav": True,
            "storage": "private_webdav",
        },
    })

    original_unlink = storage_module.Path.unlink

    def fail_unlink(self, *args, **kwargs):
        if self == local:
            raise OSError("unlink failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(storage_module.Path, "unlink", fail_unlink)

    with pytest.raises(OSError, match="unlink failed"):
        service.delete_artifact(rel)

    assert local.exists()
    assert remote[rel] == payload
    assert rel in service._load_index()


def test_delete_artifact_restores_local_and_remote_when_index_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote: dict[str, bytes] = {}

    class RestorablePrivateDeleteClient:
        def __init__(self, settings):
            pass

        def delete(self, rel):
            remote.pop(rel, None)
            return True

        def put_atomic(self, rel, payload, content_type=None):
            remote[rel] = bytes(payload)
            return f"https://private-dav.example/{rel}"

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
                "private_webdav_url": "https://private-dav.example",
                "private_webdav_root_path": "chatgpt2api/private",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", RestorablePrivateDeleteClient)
    rel = f"{uuid4()}/input/secret.png"
    payload = _png_bytes()
    local = tmp_path / "images" / rel
    local.parent.mkdir(parents=True)
    local.write_bytes(payload)
    remote[rel] = payload
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": True,
            "webdav": True,
            "storage": "private_webdav",
        },
    })

    def fail_index(items, **kwargs):
        raise OSError("index write failed")

    monkeypatch.setattr(service, "_save_index", fail_index)

    with pytest.raises(OSError, match="index write failed"):
        service.delete_artifact(rel)

    assert local.exists()
    assert remote[rel] == payload
    assert rel in service._load_index()


def test_list_items_removes_indexed_webdav_file_after_remote_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingRemoteClient:
        def __init__(self, settings):
            pass

        def exists(self, rel):
            return False

    monkeypatch.setattr(
        storage_module,
        "config",
        SimpleNamespace(
            images_dir=tmp_path / "images",
            base_url="https://api.example",
            get_image_storage_settings=lambda: {
                "mode": "webdav",
                "webdav_url": "https://dav.example",
            },
        ),
    )
    monkeypatch.setattr(storage_module, "WebDAVClient", MissingRemoteClient)
    rel = "2026/08/25/stale.jpg"
    service = ImageStorageService(tmp_path / "index.json")
    service._save_index({
        rel: {
            "rel": rel,
            "path": rel,
            "local": False,
            "webdav": True,
            "storage": "webdav",
        },
    })

    assert service.list_items("https://api.example", verify_existing=True) == []
    assert rel not in service._load_index()

def test_webdav_client_requests_disable_redirects_and_proxy() -> None:
    from services.image_storage_service import WebDAVClient

    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        headers = {}
        content = b"ok"

    class FakeSession:
        def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, "kwargs": kwargs})
            return FakeResponse()

        def close(self) -> None:
            pass

    client = WebDAVClient({"webdav_url": "https://dav.example/root"})
    client.session = FakeSession()
    assert client.get("a.png") == b"ok"
    assert calls[0]["kwargs"]["allow_redirects"] is False
    assert str(calls[0]["kwargs"]["curl_options"]).find("*") >= 0


def test_webdav_client_get_rejects_oversized_payload(monkeypatch) -> None:
    from services import image_storage_service
    from services.image_storage_service import ImageStorageError, WebDAVClient

    monkeypatch.setattr(image_storage_service, "MAX_WEBDAV_READ_BYTES", 3)

    class FakeResponse:
        status_code = 200
        headers = {}
        content = b""

    class FakeSession:
        def request(self, method: str, url: str, **kwargs):
            callback = kwargs.get("content_callback")
            if callable(callback):
                callback(b"12")
                callback(b"34")
            return FakeResponse()

        def close(self) -> None:
            pass

    client = WebDAVClient({"webdav_url": "https://dav.example/root"})
    client.session = FakeSession()
    with pytest.raises(ImageStorageError, match="exceeds"):
        client.get("a.png")
