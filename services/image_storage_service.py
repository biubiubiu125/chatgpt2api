from __future__ import annotations

import hashlib
import io
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlparse
from uuid import UUID

from curl_cffi import requests
from fastapi import HTTPException
from PIL import Image

from services.config import DATA_DIR, config
from services.file_lock import file_lock
from services.json_file import read_json_object, write_json_file
from services.image_url import build_public_image_url
from utils.image_tokens import verify_image_bytes
from utils.timezone import beijing_datetime_from_timestamp, beijing_now, beijing_now_str

IMAGE_INDEX_FILE = DATA_DIR / "image_index.json"
IMAGE_INDEX_LOCK = Lock()
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _is_private_queue_artifact(rel: str) -> bool:
    parts = Path(rel).parts
    if len(parts) < 3:
        return False
    try:
        UUID(parts[0])
    except (TypeError, ValueError):
        return False
    if parts[1] in {"input", "mask"}:
        return True
    if len(parts) >= 4 and parts[2] in {"d", "u"}:
        try:
            UUID(parts[1])
        except (TypeError, ValueError):
            return False
        return True
    return False


def _has_image_extension(path: str) -> bool:
    try:
        safe_rel = _safe_relative_path(path)
    except HTTPException:
        return False
    return Path(safe_rel).suffix.lower() in IMAGE_EXTENSIONS


class ImageStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredImage:
    rel: str
    url: str
    storage: str
    size: int


def _clean(value: object) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return beijing_now_str()


def _mtime_date(path: Path) -> str:
    return beijing_datetime_from_timestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _mtime_datetime(path: Path) -> str:
    return beijing_datetime_from_timestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _image_dimensions(payload: bytes) -> tuple[int, int] | None:
    try:
        verified = verify_image_bytes(payload)
        return verified.width, verified.height
    except ValueError:
        return None


def _atomic_write_local(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == payload:
        return
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".image-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _is_image_rel(path: str) -> bool:
    try:
        safe_rel = _safe_relative_path(path)
    except HTTPException:
        return False
    return _has_image_extension(safe_rel) and not _is_private_queue_artifact(safe_rel)


def _local_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return path


def _read_json_object(path: Path) -> dict[str, object]:
    data = read_json_object(path, name=path.name)
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, data: dict[str, object]) -> None:
    write_json_file(path, data)


class WebDAVClient:
    def __init__(self, settings: dict[str, object]):
        self.url = _clean(settings.get("webdav_url")).rstrip("/")
        self.username = _clean(settings.get("webdav_username"))
        self.password = _clean(settings.get("webdav_password"))
        self.root_path = _clean(settings.get("webdav_root_path")).strip("/")
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "WebDAVClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _auth_kwargs(self) -> dict[str, object]:
        return {"auth": (self.username, self.password)} if self.username or self.password else {}

    def _request(self, method: str, url: str, **kwargs):
        response = self.session.request(method, url, timeout=30, **self._auth_kwargs(), **kwargs)
        if response.status_code >= 400 and not (method == "MKCOL" and response.status_code in {405}):
            raise ImageStorageError(f"WebDAV {method} failed: HTTP {response.status_code}")
        return response

    def remote_url(self, rel: str = "") -> str:
        parts = [part for part in [self.root_path, _safe_relative_path(rel) if rel else ""] if part]
        encoded = "/".join(quote(part, safe="") for item in parts for part in item.split("/") if part)
        return f"{self.url}/{encoded}" if encoded else self.url

    def ensure_dirs(self, rel: str) -> None:
        parts = [part for part in [self.root_path, Path(_safe_relative_path(rel)).parent.as_posix()] if part and part != "."]
        current = self.url
        for item in "/".join(parts).split("/"):
            if not item:
                continue
            current = f"{current}/{quote(item, safe='')}"
            response = self.session.request("MKCOL", current, timeout=30, **self._auth_kwargs())
            if response.status_code in {201, 405}:
                continue
            if response.status_code >= 400:
                raise ImageStorageError(f"WebDAV MKCOL failed: HTTP {response.status_code}")

    def put(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        self.ensure_dirs(rel)
        url = self.remote_url(rel)
        self._request("PUT", url, data=payload, headers={"Content-Type": content_type})
        return url

    def put_atomic(self, rel: str, payload: bytes, content_type: str = "image/png") -> str:
        safe_rel = _safe_relative_path(rel)
        temporary_rel = f"{safe_rel}.{int(time.time() * 1000)}.tmp"
        self.put(temporary_rel, payload, content_type=content_type)
        destination = self.remote_url(safe_rel)
        try:
            self._request(
                "MOVE",
                self.remote_url(temporary_rel),
                headers={"Destination": destination, "Overwrite": "T"},
            )
        except Exception:
            try:
                self.delete(temporary_rel)
            except Exception:
                pass
            raise
        return destination

    def get(self, rel: str) -> bytes:
        response = self._request("GET", self.remote_url(rel))
        return bytes(response.content)

    def delete(self, rel: str) -> bool:
        response = self.session.request("DELETE", self.remote_url(rel), timeout=30, **self._auth_kwargs())
        if response.status_code in {200, 202, 204, 404}:
            return response.status_code != 404
        raise ImageStorageError(f"WebDAV DELETE failed: HTTP {response.status_code}")

    def test(self) -> dict[str, object]:
        if not self.url:
            return {"ok": False, "status": 0, "error": "WebDAV URL is required"}
        if urlparse(self.url).scheme not in {"http", "https"}:
            return {"ok": False, "status": 0, "error": "invalid WebDAV URL"}
        test_rel = ".chatgpt2api_webdav_test.txt"
        try:
            self.put(test_rel, b"chatgpt2api webdav test\n", content_type="text/plain")
            self.delete(test_rel)
            return {"ok": True, "status": 200, "error": None}
        except ImageStorageError as exc:
            return {"ok": False, "status": 0, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "status": 0, "error": str(exc) or exc.__class__.__name__}
        finally:
            self.close()


@contextmanager
def _webdav_client(settings: dict[str, object]):
    client = WebDAVClient(settings)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
        else:
            session = getattr(client, "session", None)
            session_close = getattr(session, "close", None)
            if callable(session_close):
                session_close()


class ImageStorageService:
    def __init__(self, index_file: Path = IMAGE_INDEX_FILE):
        self.index_file = index_file
        self._index_lock = IMAGE_INDEX_LOCK

    def _index_file_lock_path(self) -> Path:
        return self.index_file.with_suffix(self.index_file.suffix + ".lock")

    def settings(self) -> dict[str, object]:
        return config.get_image_storage_settings()

    def mode(self) -> str:
        return _clean(self.settings().get("mode")) or "local"

    def _private_webdav_settings(self) -> dict[str, object]:
        settings = self.settings()
        private_url = _clean(settings.get("private_webdav_url"))
        if not private_url:
            return {}
        public_url = _clean(settings.get("webdav_url"))
        same_endpoint = private_url.rstrip("/") == public_url.rstrip("/")
        private_username = _clean(settings.get("private_webdav_username"))
        private_password = _clean(settings.get("private_webdav_password"))
        return {
            "webdav_url": private_url,
            "webdav_username": private_username or (_clean(settings.get("webdav_username")) if same_endpoint else ""),
            "webdav_password": private_password or (_clean(settings.get("webdav_password")) if same_endpoint else ""),
            "webdav_root_path": _clean(settings.get("private_webdav_root_path")) or "chatgpt2api/private",
        }

    def _load_index(self) -> dict[str, dict[str, object]]:
        raw = _read_json_object(self.index_file)
        items = raw.get("items")
        if not isinstance(items, dict):
            return {}
        return {str(key): value for key, value in items.items() if isinstance(value, dict)}

    def _load_clean_index(self) -> dict[str, dict[str, object]]:
        items = self._load_index()
        return {rel: item for rel, item in items.items() if _is_image_rel(rel)}

    def _save_index(
        self,
        items: dict[str, dict[str, object]],
        *,
        preserve_private: bool = True,
    ) -> None:
        saved_items = dict(items)
        if preserve_private:
            for rel, item in self._load_index().items():
                if _is_private_queue_artifact(rel):
                    saved_items.setdefault(rel, item)
        _write_json_object(self.index_file, {"items": saved_items})

    @staticmethod
    def _remove_private_thumbnails(rel: str) -> None:
        root_value = getattr(config, "image_thumbnails_dir", None)
        if root_value is None:
            return
        root = Path(root_value).resolve()
        for candidate in (root / f"{rel}.png", root / rel):
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
                resolved.unlink(missing_ok=True)
            except (OSError, ValueError):
                continue

    def cleanup_private_public_copies(self) -> dict[str, int]:
        removed = 0
        failed = 0
        with self._index_lock, file_lock(self._index_file_lock_path()):
            items = self._load_index()
            private_items = {
                rel: item
                for rel, item in items.items()
                if _is_private_queue_artifact(rel)
            }
            if not private_items:
                return {"removed": 0, "failed": 0}
            changed = False
            for rel, item in private_items.items():
                self._remove_private_thumbnails(rel)
                remote_removed = False
                if item.get("webdav") or item.get("remote_url"):
                    try:
                        remote_settings = self._private_webdav_settings() or self.settings()
                        with _webdav_client(remote_settings) as client:
                            remote_removed = client.delete(rel)
                    except Exception:
                        failed += 1
                        continue
                items.pop(rel, None)
                changed = True
                removed += 1
            if changed:
                self._save_index(items, preserve_private=False)
        return {"removed": removed, "failed": failed}

    def _public_url(self, rel: str, base_url: str | None = None) -> str:
        settings = self.settings()
        public_base_url = _clean(settings.get("public_base_url"))
        if public_base_url:
            return f"{public_base_url.rstrip('/')}/{_safe_relative_path(rel)}"
        return build_public_image_url(base_url or config.base_url, _safe_relative_path(rel))

    def make_relative_path(self, image_data: bytes) -> str:
        file_hash = hashlib.md5(image_data).hexdigest()
        filename = f"{int(time.time())}_{file_hash}.png"
        now = beijing_now()
        relative_dir = Path(now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"))
        return f"{relative_dir.as_posix()}/{filename}"

    def save(self, image_data: bytes, base_url: str | None = None) -> StoredImage:
        config.cleanup_old_images()
        rel = self.make_relative_path(image_data)
        return self.save_at_path(rel, image_data, base_url)

    def save_at_path(
        self,
        relative_path: str,
        image_data: bytes,
        base_url: str | None = None,
    ) -> StoredImage:
        rel = _safe_relative_path(relative_path)
        private_artifact = _is_private_queue_artifact(rel)
        if private_artifact:
            return self.save_private_at_path(rel, image_data)
        return self._save_public_at_path(rel, image_data, base_url)

    def save_private_at_path(
        self,
        relative_path: str,
        image_data: bytes,
    ) -> StoredImage:
        rel = _safe_relative_path(relative_path)
        if not _is_private_queue_artifact(rel) or not _has_image_extension(rel):
            raise ImageStorageError("invalid private image queue artifact path")
        dimensions = _image_dimensions(image_data)
        if dimensions is None:
            raise ImageStorageError("invalid image payload")
        private_settings = self._private_webdav_settings()
        if private_settings and self.mode() in {"webdav", "both"}:
            with _webdav_client(private_settings) as client:
                client.put_atomic(rel, image_data)
            return StoredImage(rel=rel, url="", storage="private_webdav", size=len(image_data))
        return StoredImage(rel=rel, url="", storage="private_local", size=len(image_data))

    def _save_public_at_path(
        self,
        rel: str,
        image_data: bytes,
        base_url: str | None = None,
    ) -> StoredImage:
        dimensions = _image_dimensions(image_data)
        if dimensions is None:
            raise ImageStorageError("invalid image payload")
        mode = self.mode()
        if mode not in {"local", "webdav", "both"}:
            mode = "local"
        stored_local = False
        stored_webdav = False
        remote_url = ""

        if mode in {"local", "both"}:
            path = _local_image_path(rel)
            _atomic_write_local(path, image_data)
            stored_local = True

        if mode in {"webdav", "both"}:
            with _webdav_client(self.settings()) as client:
                remote_url = client.put_atomic(rel, image_data)
            stored_webdav = True

        item = {
            "rel": rel,
            "path": rel,
            "name": Path(rel).name,
            "date": "-".join(rel.split("/")[:3]),
            "size": len(image_data),
            "created_at": _now_iso(),
            "storage": "both" if stored_local and stored_webdav else ("webdav" if stored_webdav else "local"),
            "local": stored_local,
            "webdav": stored_webdav,
            "remote_url": remote_url,
        }
        item["width"], item["height"] = dimensions
        with self._index_lock, file_lock(self._index_file_lock_path()):
            items = self._load_clean_index()
            items[rel] = item
            self._save_index(items)
        return StoredImage(
            rel=rel,
            url=self._public_url(rel, base_url),
            storage=str(item["storage"]),
            size=len(image_data),
        )

    def get_bytes(self, rel: str) -> bytes:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel) or _is_private_queue_artifact(safe_rel):
            raise HTTPException(status_code=404, detail="image not found")
        path = _local_image_path(safe_rel)
        if path.is_file():
            return path.read_bytes()
        item = self._load_clean_index().get(safe_rel, {})
        if item.get("webdav") or self.mode() in {"webdav", "both"}:
            with _webdav_client(self.settings()) as client:
                return client.get(safe_rel)
        raise HTTPException(status_code=404, detail="image not found")

    def get_published_bytes(self, rel: str) -> bytes:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel) or _is_private_queue_artifact(safe_rel):
            raise HTTPException(status_code=404, detail="image not found")
        path = _local_image_path(safe_rel)
        if path.is_file():
            return path.read_bytes()
        item = self._load_clean_index().get(safe_rel, {})
        if item.get("webdav") or self.mode() in {"webdav", "both"}:
            with _webdav_client(self.settings()) as client:
                return client.get(safe_rel)
        raise HTTPException(status_code=404, detail="image not found")

    def get_artifact_bytes(self, rel: str) -> bytes:
        safe_rel = _safe_relative_path(rel)
        if not _has_image_extension(safe_rel):
            raise HTTPException(status_code=404, detail="image not found")
        if not _is_private_queue_artifact(safe_rel):
            return self.get_published_bytes(safe_rel)
        path = _local_image_path(safe_rel)
        if path.is_file():
            return path.read_bytes()
        private_settings = self._private_webdav_settings()
        if private_settings and self.mode() in {"webdav", "both"}:
            with _webdav_client(private_settings) as client:
                return client.get(safe_rel)
        raise HTTPException(status_code=404, detail="image not found")

    def exists(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        if not _is_image_rel(safe_rel):
            return False
        if _local_image_path(safe_rel).is_file():
            return True
        item = self._load_clean_index().get(safe_rel, {})
        if not item.get("webdav"):
            return False
        try:
            with _webdav_client(self.settings()) as client:
                return _image_dimensions(client.get(safe_rel)) is not None
        except Exception:
            return False

    def has_local(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        return _is_image_rel(safe_rel) and _local_image_path(safe_rel).is_file()

    def list_items(
        self,
        base_url: str,
        start_date: str = "",
        end_date: str = "",
        *,
        refresh_index: bool = True,
        verify_existing: bool = True,
    ) -> list[dict[str, object]]:
        self.cleanup_private_public_copies()
        with self._index_lock, file_lock(self._index_file_lock_path()):
            indexed = self._load_clean_index()
            root = config.images_dir
            changed = False
            if refresh_index:
                for path in root.rglob("*"):
                    if not path.is_file() or not _is_image_rel(path.name):
                        continue
                    rel = path.relative_to(root).as_posix()
                    if not _is_image_rel(rel):
                        continue
                    if rel in indexed:
                        continue
                    dimensions = None
                    try:
                        dimensions = _image_dimensions(path.read_bytes())
                    except Exception:
                        dimensions = None
                    indexed[rel] = {
                        "rel": rel,
                        "path": rel,
                        "name": path.name,
                        "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else _mtime_date(path),
                        "size": path.stat().st_size,
                        "created_at": _mtime_datetime(path),
                        "storage": "local",
                        "local": True,
                        "webdav": False,
                        **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
                    }
                    changed = True

            items: list[dict[str, object]] = []
            for rel, item in list(indexed.items()):
                if not _is_image_rel(rel):
                    indexed.pop(rel, None)
                    changed = True
                    continue
                if verify_existing:
                    local = _local_image_path(rel).is_file()
                    webdav = bool(item.get("webdav"))
                    if not local and not webdav:
                        indexed.pop(rel, None)
                        changed = True
                        continue
                    storage = "both" if local and webdav else ("webdav" if webdav else "local")
                    if item.get("local") != local or item.get("storage") != storage:
                        item = {
                            **item,
                            "local": local,
                            "storage": storage,
                        }
                        indexed[rel] = item
                        changed = True
                day = str(item.get("date") or "")
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                items.append({
                    **item,
                    "rel": rel,
                    "path": rel,
                    "url": self._public_url(rel, base_url),
                })
            if changed:
                self._save_index(indexed)
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    def delete(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        if _is_private_queue_artifact(safe_rel):
            return False
        removed = False
        path = _local_image_path(safe_rel)
        if path.is_file():
            path.unlink()
            removed = True
        with self._index_lock, file_lock(self._index_file_lock_path()):
            items = self._load_clean_index()
            item = items.get(safe_rel, {})
            if item.get("webdav"):
                try:
                    with _webdav_client(self.settings()) as client:
                        removed = client.delete(safe_rel) or removed
                except ImageStorageError:
                    if not removed:
                        raise
            if safe_rel in items:
                items.pop(safe_rel, None)
                self._save_index(items)
        return removed

    def delete_artifact(self, rel: str) -> bool:
        safe_rel = _safe_relative_path(rel)
        if not _is_private_queue_artifact(safe_rel):
            return self.delete(safe_rel)
        removed = False
        path = _local_image_path(safe_rel)
        if path.is_file():
            path.unlink()
            removed = True
        with self._index_lock, file_lock(self._index_file_lock_path()):
            items = self._load_index()
            private_settings = self._private_webdav_settings()
            if private_settings and self.mode() in {"webdav", "both"}:
                try:
                    with _webdav_client(private_settings) as client:
                        removed = client.delete(safe_rel) or removed
                except ImageStorageError:
                    if not removed:
                        raise
            if safe_rel in items:
                items.pop(safe_rel, None)
                self._save_index(items, preserve_private=False)
        return removed

    def sync_all(self) -> dict[str, int]:
        self.cleanup_private_public_copies()
        settings = self.settings()
        if self.mode() not in {"webdav", "both"}:
            raise ImageStorageError("WebDAV 图片存储未启用")
        uploaded = 0
        skipped = 0
        failed = 0
        private_uploaded = 0
        private_skipped = 0
        private_failed = 0
        with self._index_lock, file_lock(self._index_file_lock_path()):
            items = self._load_index()
            with _webdav_client(settings) as client:
                for path in sorted(config.images_dir.rglob("*")):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(config.images_dir).as_posix()
                    if not _has_image_extension(rel):
                        continue
                    item = items.get(rel, {})
                    is_private = _is_private_queue_artifact(rel)
                    if is_private:
                        private_skipped += 1
                        continue
                    else:
                        if not _is_image_rel(rel):
                            continue
                        if item.get("webdav"):
                            skipped += 1
                            continue
                    try:
                        payload = path.read_bytes()
                        remote_url = client.put(rel, payload)
                        dimensions = _image_dimensions(payload)
                        items[rel] = {
                            **item,
                            "rel": rel,
                            "path": rel,
                            "name": path.name,
                            "date": "-".join(rel.split("/")[:3]) if len(rel.split("/")) >= 4 else _mtime_date(path),
                            "size": len(payload),
                            "created_at": str(item.get("created_at") or _mtime_datetime(path)),
                            "storage": "both",
                            "local": True,
                            "webdav": True,
                            "remote_url": remote_url,
                            **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
                        }
                        if is_private:
                            private_uploaded += 1
                        else:
                            uploaded += 1
                    except Exception:
                        if is_private:
                            private_failed += 1
                        else:
                            failed += 1
            self._save_index(items)
        return {
            "uploaded": uploaded,
            "skipped": skipped,
            "failed": failed,
            "private_uploaded": private_uploaded,
            "private_skipped": private_skipped,
            "private_failed": private_failed,
        }

    def test_webdav(self) -> dict[str, object]:
        return WebDAVClient(self.settings()).test()


image_storage_service = ImageStorageService()
