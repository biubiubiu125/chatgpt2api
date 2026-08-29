from __future__ import annotations

import io
import os
import shutil
import threading
import time
import zipfile
from datetime import timedelta
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlparse

from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlOpt
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

from services.config import config
from services.image_storage_service import _is_private_queue_artifact, content_type_for_path, image_storage_service
from services.image_tags_service import load_tags, remove_tags
from services.image_url import build_public_thumbnail_url, build_public_thumbnail_url_for_image_url
from utils.image_tokens import verify_image_bytes
from utils.log import logger
from utils.timezone import beijing_now, parse_to_beijing_naive

THUMBNAIL_SIZE = (320, 320)
IMAGE_LIST_MAINTENANCE_INTERVAL_SECS = 300
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
MUSIC_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".vtt", ".lrc", ".txt"}
MAX_REMOTE_ZIP_ARTIFACT_BYTES = 50 * 1024 * 1024
_maintenance_lock = threading.Lock()
_last_list_maintenance_at = 0.0


def _cleanup_empty_dirs(root: Path) -> None:
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def _safe_relative_path(path: str) -> str:
    value = str(path or "").strip().replace("\\", "/").lstrip("/")
    if not value:
        raise HTTPException(status_code=404, detail="image not found")
    parts = Path(value).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(status_code=404, detail="image not found")
    return Path(*parts).as_posix()


def _safe_image_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    root = config.images_dir.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return path


def _is_public_image_relative_path(relative_path: str) -> bool:
    rel = _safe_relative_path(relative_path)
    return Path(rel).suffix.lower() in IMAGE_EXTENSIONS and not _is_private_queue_artifact(rel)


def _is_verified_image_payload(payload: bytes | None) -> bool:
    if not payload:
        return False
    try:
        verify_image_bytes(payload)
    except ValueError:
        return False
    return True


def _queue_public_final_artifacts() -> list[dict[str, object]] | None:
    try:
        from services.image_task_service import image_task_service
    except Exception:
        return None
    list_artifacts = getattr(image_task_service, "list_public_final_artifacts", None)
    if not callable(list_artifacts):
        return None
    try:
        artifacts = list_artifacts()
    except Exception:
        return None
    if not isinstance(artifacts, (list, tuple)):
        return []
    return [dict(item) for item in artifacts if isinstance(item, dict)]


def _queue_public_artifact_item(relative_path: str) -> dict[str, object] | None:
    rel = _safe_relative_path(relative_path)
    artifacts = _queue_public_final_artifacts()
    if artifacts is None:
        return None
    for artifact in artifacts:
        try:
            candidate = _safe_relative_path(str(artifact.get("path") or artifact.get("rel") or ""))
        except HTTPException:
            continue
        if candidate == rel:
            return dict(artifact)
    return None


def _queue_public_artifact_path(relative_path: str) -> Path | None:
    rel = _safe_relative_path(relative_path)
    try:
        from services.image_task_service import image_task_service
    except Exception:
        return None
    artifact_service = getattr(image_task_service, "artifact_service", None)
    root = getattr(artifact_service, "root", None)
    check_artifact = getattr(image_task_service, "is_public_final_artifact", None)
    list_artifacts = getattr(image_task_service, "list_public_final_artifacts", None)
    if root is None or (not callable(check_artifact) and not callable(list_artifacts)):
        return None
    try:
        if callable(check_artifact):
            public = bool(check_artifact(rel))
        else:
            public = _queue_public_artifact_item(rel) is not None
    except Exception:
        return None
    if not public:
        return None
    resolved_root = Path(root).resolve()
    path = (resolved_root / rel).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _queue_public_artifact_stats(gallery_root: Path) -> tuple[int, int]:
    try:
        from services.image_task_service import image_task_service
    except Exception:
        return 0, 0
    list_artifacts = getattr(image_task_service, "list_public_final_artifacts", None)
    artifact_service = getattr(image_task_service, "artifact_service", None)
    queue_root = getattr(artifact_service, "root", None)
    if queue_root is None or not callable(list_artifacts):
        return 0, 0
    queue_root = Path(queue_root).resolve()
    gallery_root = Path(gallery_root).resolve()
    same_root = queue_root == gallery_root

    count = 0
    size = 0
    try:
        artifacts = list_artifacts()
    except Exception:
        return 0, 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        rel = _clean_text(artifact.get("path") or artifact.get("rel"))
        if not rel:
            continue
        try:
            rel = _safe_relative_path(rel)
        except HTTPException:
            continue
        if not _is_public_image_relative_path(rel):
            continue
        path = (queue_root / rel).resolve()
        try:
            path.relative_to(queue_root)
        except ValueError:
            continue
        if same_root:
            if path.is_file():
                # The gallery scan above already counted local artifacts on
                # the shared root. Only URL/DB-only queue results belong here.
                continue
        else:
            try:
                path.relative_to(gallery_root)
                continue
            except ValueError:
                pass
        item_size = 0
        if path.is_file():
            try:
                item_size = path.stat().st_size
            except OSError:
                item_size = 0
        else:
            try:
                item_size = max(0, int(artifact.get("size") or artifact.get("byte_size") or 0))
            except (TypeError, ValueError):
                item_size = 0
            if item_size <= 0 and not _clean_text(artifact.get("url") or artifact.get("remote_url")):
                continue
        count += 1
        size += item_size
    return count, size


def _queue_local_artifact_candidates(
    gallery_root: Path,
    artifacts: list[dict[str, object]],
    protected: set[str],
) -> tuple[Path | None, list[tuple[Path, str]]]:
    try:
        from services.image_task_service import image_task_service
    except Exception:
        return None, []
    artifact_service = getattr(image_task_service, "artifact_service", None)
    queue_root = getattr(artifact_service, "root", None)
    if queue_root is None:
        return None, []
    queue_root = Path(queue_root).resolve()
    gallery_root = Path(gallery_root).resolve()
    if queue_root == gallery_root:
        return queue_root, []
    try:
        if os.stat(queue_root).st_dev != os.stat(gallery_root).st_dev:
            return queue_root, []
    except OSError:
        return queue_root, []

    candidates: list[tuple[Path, str]] = []
    for artifact in artifacts:
        rel = _clean_text(artifact.get("path") or artifact.get("rel"))
        if not rel:
            continue
        try:
            rel = _safe_relative_path(rel)
        except HTTPException:
            continue
        if rel in protected or not _is_public_image_relative_path(rel):
            continue
        path = (queue_root / rel).resolve()
        try:
            path.relative_to(queue_root)
            path.relative_to(gallery_root)
        except ValueError:
            pass
        else:
            # A queue root nested under the gallery root is already covered by
            # the gallery scan and must not be counted twice.
            continue
        if path.is_file():
            candidates.append((path, rel))
    return queue_root, candidates


def _delete_queue_public_final_artifact(relative_path: str) -> bool | None:
    rel = _safe_relative_path(relative_path)
    if not _is_public_image_relative_path(rel):
        return None
    try:
        from services.image_task_service import image_task_service
    except Exception:
        return None
    check_artifact = getattr(image_task_service, "is_public_final_artifact", None)
    delete_artifact = getattr(image_task_service, "delete_public_final_artifact", None)
    if not callable(check_artifact):
        return None
    try:
        if not bool(check_artifact(rel)):
            return None
    except Exception as exc:
        logger.warning({
            "event": "queue_public_artifact_delete_probe_failed",
            "relative_path": rel,
            "error": str(exc),
        })
        return None
    if not callable(delete_artifact):
        logger.warning({
            "event": "queue_public_artifact_delete_unavailable",
            "relative_path": rel,
        })
        return False
    try:
        return bool(delete_artifact(rel))
    except Exception as exc:
        logger.warning({
            "event": "queue_public_artifact_delete_failed",
            "relative_path": rel,
            "error": str(exc),
        })
        return False


def _queue_retention_cleanup_targets(retention_days: int) -> list[tuple[str, int]]:
    artifacts = _queue_public_final_artifacts()
    if artifacts is None:
        return []
    cutoff = beijing_now().replace(tzinfo=None) - timedelta(days=_retention_days(retention_days, config.image_retention_days))
    targets: list[tuple[str, int]] = []
    for artifact in artifacts:
        rel = _clean_text(artifact.get("path") or artifact.get("rel"))
        if not rel:
            continue
        try:
            rel = _safe_relative_path(rel)
        except HTTPException:
            continue
        if not _is_public_image_relative_path(rel):
            continue
        created = parse_to_beijing_naive(artifact.get("created_at"))
        if created is None:
            day = _clean_text(artifact.get("date"))
            created = parse_to_beijing_naive(f"{day} 00:00:00")
        if created is None or created >= cutoff:
            continue
        try:
            size = max(0, int(artifact.get("size") or artifact.get("byte_size") or 0))
        except (TypeError, ValueError):
            size = 0
        targets.append((rel, size))
    return targets


def _local_public_image_path(relative_path: str) -> Path | None:
    rel = _safe_relative_path(relative_path)
    if _is_private_queue_artifact(rel):
        return None
    if image_storage_service.has_local(rel):
        return _safe_image_path(rel)
    return _queue_public_artifact_path(rel)


def _url_matches_artifact_path(url: str, relative_path: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    decoded_path = unquote(parsed.path or "").replace("\\", "/").lstrip("/")
    rel = _safe_relative_path(relative_path)
    return decoded_path == rel or decoded_path.endswith(f"/{rel}")


def _fetch_public_artifact_url(relative_path: str) -> bytes | None:
    rel = _safe_relative_path(relative_path)
    artifact = _queue_public_artifact_item(rel)
    if not artifact:
        return None
    url = _clean_text(artifact.get("url") or artifact.get("remote_url"))
    if not url or not _url_matches_artifact_path(url, rel):
        return None
    content_length = _clean_text(artifact.get("size") or artifact.get("byte_size"))
    if content_length.isdigit() and int(content_length) > MAX_REMOTE_ZIP_ARTIFACT_BYTES:
        return None
    received = bytearray()
    too_large = False

    def receive(chunk: bytes) -> int:
        nonlocal too_large
        if len(received) + len(chunk) > MAX_REMOTE_ZIP_ARTIFACT_BYTES:
            too_large = True
            raise ValueError("artifact exceeds zip download limit")
        received.extend(chunk)
        return len(chunk)

    try:
        response = curl_requests.get(
            url,
            headers={"Accept": "image/*,*/*;q=0.8", "User-Agent": "chatgpt2api image zip fetcher"},
            timeout=60,
            allow_redirects=False,
            content_callback=receive,
            curl_options={CurlOpt.NOPROXY: "*"},
        )
    except Exception:
        return None
    if too_large or not 200 <= int(getattr(response, "status_code", 0) or 0) < 300:
        return None
    response_length = _clean_text(getattr(response, "headers", {}).get("content-length") if getattr(response, "headers", None) else "")
    if response_length.isdigit() and int(response_length) > MAX_REMOTE_ZIP_ARTIFACT_BYTES:
        return None
    media_type = _clean_text(getattr(response, "headers", {}).get("content-type") if getattr(response, "headers", None) else "").split(";", 1)[0].lower()
    if media_type and not media_type.startswith("image/") and media_type not in {"application/octet-stream", "binary/octet-stream"}:
        return None
    payload = bytes(received)
    return payload if _is_verified_image_payload(payload) else None


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }


def get_local_image_response(relative_path: str) -> FileResponse:
    headers = _cors_headers()
    if not _is_public_image_relative_path(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    path = _local_public_image_path(relative_path)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, headers=headers)


def get_image_response(relative_path: str) -> FileResponse | Response:
    headers = _cors_headers()
    if not _is_public_image_relative_path(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    path = _local_public_image_path(relative_path)
    if path is not None:
        return FileResponse(path, headers=headers)
    return Response(
        content=image_storage_service.get_bytes(relative_path),
        media_type=content_type_for_path(relative_path),
        headers=headers,
    )


def _thumbnail_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    return config.image_thumbnails_dir / f"{rel}.png"


def thumbnail_url(base_url: str, relative_path: str) -> str:
    return build_public_thumbnail_url(base_url, _safe_relative_path(relative_path))


def thumbnail_url_for_image(base_url: str, image_url: object, relative_path: str) -> str:
    rel = _safe_relative_path(relative_path)
    candidate = _clean_text(image_url)
    if candidate:
        derived = build_public_thumbnail_url_for_image_url(candidate, rel)
        return derived or candidate
    return thumbnail_url(base_url, rel)


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def _write_thumbnail_atomic(target: Path, image: Image.Image) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            image.save(tmp, format="PNG", optimize=True)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def ensure_thumbnail(relative_path: str) -> Path:
    if not _is_public_image_relative_path(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    source = _local_public_image_path(relative_path)
    if source is None and not image_storage_service.exists(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    target = _thumbnail_path(relative_path)
    source_mtime = 0.0
    if source is not None:
        source_mtime = source.stat().st_mtime
    if target.exists() and (not source_mtime or target.stat().st_mtime >= source_mtime):
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        image_source = source if source is not None else io.BytesIO(image_storage_service.get_bytes(relative_path))
        with Image.open(image_source) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            _write_thumbnail_atomic(target, image)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="failed to create thumbnail") from exc
    return target


def ensure_local_thumbnail(relative_path: str) -> Path:
    if not _is_public_image_relative_path(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    if _local_public_image_path(relative_path) is None:
        raise HTTPException(status_code=404, detail="image not found")
    return ensure_thumbnail(relative_path)


def get_local_thumbnail_response(relative_path: str) -> FileResponse:
    return FileResponse(ensure_local_thumbnail(relative_path), headers=_cors_headers())


def get_thumbnail_response(relative_path: str) -> FileResponse:
    return FileResponse(ensure_thumbnail(relative_path), headers=_cors_headers())


def get_image_download_response(relative_path: str) -> FileResponse | Response:
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if not _is_public_image_relative_path(relative_path):
        raise HTTPException(status_code=404, detail="image not found")
    path = _local_public_image_path(relative_path)
    if path is not None:
        headers = {**cors_headers, "Content-Disposition": f'attachment; filename="{path.name}"'}
        return FileResponse(path, filename=path.name, headers=headers)
    rel = _safe_relative_path(relative_path)
    headers = {
        **cors_headers,
        "Content-Disposition": f'attachment; filename="{Path(rel).name}"',
    }
    remote_payload = _fetch_public_artifact_url(rel)
    if remote_payload is not None:
        return Response(
            content=remote_payload,
            media_type=content_type_for_path(rel),
            headers=headers,
        )
    return Response(
        content=image_storage_service.get_bytes(rel),
        media_type=content_type_for_path(rel),
        headers=headers,
    )


def cleanup_image_thumbnails() -> int:
    thumbnails_root = config.image_thumbnails_dir
    removed = 0
    known_sources: set[str] = set()
    try:
        for item in image_storage_service.list_items("", refresh_index=False, verify_existing=True):
            rel = _clean_text(item.get("path") or item.get("rel"))
            if rel and not _is_private_queue_artifact(rel):
                known_sources.add(rel)
    except Exception:
        return 0
    for item in _queue_public_final_artifacts() or []:
        rel = _clean_text(item.get("path") or item.get("rel"))
        if not rel:
            continue
        try:
            rel = _safe_relative_path(rel)
        except HTTPException:
            continue
        if _is_public_image_relative_path(rel):
            known_sources.add(rel)
    for path in config.images_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(config.images_dir).as_posix()
        if _is_private_queue_artifact(rel) or Path(rel).suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        known_sources.add(rel)
    for path in thumbnails_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(thumbnails_root).as_posix()
        if not rel.endswith(".png") or rel[:-4] not in known_sources:
            path.unlink()
            removed += 1
    _cleanup_empty_dirs(thumbnails_root)
    return removed


def _run_periodic_list_maintenance() -> None:
    global _last_list_maintenance_at
    now = time.time()
    if now - _last_list_maintenance_at < IMAGE_LIST_MAINTENANCE_INTERVAL_SECS:
        return
    if not _maintenance_lock.acquire(blocking=False):
        return
    try:
        now = time.time()
        if now - _last_list_maintenance_at < IMAGE_LIST_MAINTENANCE_INTERVAL_SECS:
            return
        cleanup_image_retention()
        image_storage_service.list_items("", refresh_index=False, verify_existing=True)
        cleanup_image_thumbnails()
        _last_list_maintenance_at = now
    finally:
        _maintenance_lock.release()


def _schedule_periodic_list_maintenance() -> None:
    now = time.time()
    if now - _last_list_maintenance_at < IMAGE_LIST_MAINTENANCE_INTERVAL_SECS:
        return
    if _maintenance_lock.locked():
        return
    threading.Thread(
        target=_run_periodic_list_maintenance,
        name="image-list-maintenance",
        daemon=True,
    ).start()


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _expiry_for_item(item: dict[str, object], retention_days: int) -> tuple[bool, int | None]:
    """Return display-only expiration state from created_at and retention days."""
    if retention_days <= 0:
        return False, None
    created = parse_to_beijing_naive(item.get("created_at"))
    if created is None:
        return False, None
    remaining = retention_days * 86400 - (beijing_now().replace(tzinfo=None) - created).total_seconds()
    if remaining <= 0:
        return True, 0
    return False, int(remaining)


def _media_type_for_item(item: dict[str, object]) -> str:
    explicit = _clean_text(item.get("type")).lower()
    if explicit in {"image", "video", "music"}:
        return explicit
    path = _clean_text(item.get("path") or item.get("rel") or item.get("name"))
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in MUSIC_EXTENSIONS:
        return "music"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "image"


def _matches_image_search(item: dict[str, object], tags: list[str], search: str) -> bool:
    keyword = search.strip().lower()
    if not keyword:
        return True
    values = [
        item.get("name"),
        item.get("path"),
        item.get("rel"),
        item.get("created_at"),
        item.get("storage"),
        *tags,
    ]
    return any(keyword in _clean_text(value).lower() for value in values)


def _queue_final_image_items(
    base_url: str,
    start_date: str = "",
    end_date: str = "",
) -> list[dict[str, object]]:
    from services.image_task_service import image_task_service

    list_artifacts = getattr(image_task_service, "list_public_final_artifacts", None)
    if not callable(list_artifacts):
        return []
    artifacts = list_artifacts()

    items: list[dict[str, object]] = []
    for artifact in artifacts:
        rel = _clean_text(artifact.get("path") or artifact.get("rel"))
        try:
            rel = _safe_relative_path(rel)
        except HTTPException:
            continue
        storage = _clean_text(artifact.get("storage")) or "local"
        try:
            local = bool(image_storage_service.has_local(rel))
        except Exception:
            local = bool(artifact.get("local"))
        webdav = storage in {"webdav", "both"} or bool(artifact.get("webdav"))
        url = _clean_text(artifact.get("url") or artifact.get("remote_url"))
        if not local and not webdav and not url:
            continue
        created_at = _clean_text(artifact.get("created_at"))
        day = _clean_text(artifact.get("date")) or created_at[:10]
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        item_url = url or f"{base_url.rstrip('/')}/images/{rel}"
        thumbnail = thumbnail_url_for_image(base_url, item_url, rel)
        items.append({
            **artifact,
            "rel": rel,
            "path": rel,
            "name": _clean_text(artifact.get("name")) or Path(rel).name,
            "date": day,
            "created_at": created_at,
            "url": url or f"{base_url.rstrip('/')}/images/{rel}",
            "thumbnail_url": thumbnail,
            "storage": "both" if local and webdav else ("webdav" if webdav else "local"),
            "local": local,
            "webdav": webdav,
        })
    return items


def _page_meta(total: int, limit: int, offset: int) -> tuple[int, int, int]:
    safe_limit = max(0, min(int(limit or 0), 500))
    safe_offset = max(0, int(offset or 0))
    if safe_limit <= 0:
        return 1, total, 1
    page = safe_offset // safe_limit + 1
    page_count = max(1, (total + safe_limit - 1) // safe_limit)
    return page, safe_limit, page_count


def _retention_days(value: int | float | str | None, fallback: int) -> int:
    try:
        return max(1, int(float(value or fallback)))
    except (TypeError, ValueError):
        return max(1, int(fallback))


def _retention_cleanup_targets(retention_days: int) -> list[tuple[str, int]]:
    days = _retention_days(retention_days, config.image_retention_days)
    cutoff = time.time() - days * 86400
    cutoff_datetime = beijing_now().replace(tzinfo=None) - timedelta(days=days)
    root = config.images_dir.resolve()
    protected = config.protected_image_paths()
    targets: dict[str, int] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.relative_to(root)
        except ValueError:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in protected and not _is_private_queue_artifact(relative):
            targets[relative] = stat.st_size

    try:
        indexed_items = image_storage_service.list_items(
            "",
            refresh_index=True,
            verify_existing=True,
        )
    except Exception:
        indexed_items = []
    for item in indexed_items:
        relative = _clean_text(item.get("path") or item.get("rel"))
        if not relative or relative in protected or (root / relative).is_file():
            continue
        created = parse_to_beijing_naive(item.get("created_at"))
        if created is None:
            day = _clean_text(item.get("date"))
            created = parse_to_beijing_naive(f"{day} 00:00:00")
        if created is None or created >= cutoff_datetime:
            continue
        try:
            targets[relative] = int(item.get("size") or 0)
        except (TypeError, ValueError):
            targets[relative] = 0
    return list(targets.items())


def _retention_cleanup_targets_with_queue(retention_days: int) -> list[tuple[str, int]]:
    targets = _retention_cleanup_targets(retention_days)
    seen = {rel for rel, _size in targets}
    for rel, size in _queue_retention_cleanup_targets(retention_days):
        if rel not in seen:
            targets.append((rel, size))
            seen.add(rel)
    return targets


def preview_image_retention_cleanup(retention_days: int | None = None) -> dict[str, int | bool]:
    days = _retention_days(retention_days, config.image_retention_days)
    targets = _retention_cleanup_targets_with_queue(days)
    return {
        "removed": len(targets),
        "removed_size_bytes": sum(size for _, size in targets),
        "retention_days": days,
        "dry_run": True,
    }


def cleanup_image_retention(retention_days: int | None = None) -> dict[str, int | bool]:
    days = _retention_days(retention_days, config.image_retention_days)
    targets = _retention_cleanup_targets_with_queue(days)
    removed = 0
    removed_size_bytes = 0
    for rel, size in targets:
        queue_deleted = _delete_queue_public_final_artifact(rel)
        if queue_deleted is not None:
            if queue_deleted:
                removed += 1
                removed_size_bytes += size
                for thumbnail in (_thumbnail_path(rel), config.image_thumbnails_dir / _safe_relative_path(rel)):
                    if thumbnail.is_file():
                        thumbnail.unlink()
                remove_tags(rel)
            continue
        try:
            deleted = image_storage_service.delete(rel)
        except Exception:
            continue
        if not deleted:
            continue
        removed += 1
        removed_size_bytes += size
        for thumbnail in (_thumbnail_path(rel), config.image_thumbnails_dir / _safe_relative_path(rel)):
            if thumbnail.is_file():
                thumbnail.unlink()
        remove_tags(rel)
    cleanup_image_thumbnails()
    _cleanup_empty_dirs(config.images_dir)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    return {
        "removed": removed,
        "removed_size_bytes": removed_size_bytes,
        "retention_days": days,
        "dry_run": False,
    }


def list_images(
    base_url: str,
    start_date: str = "",
    end_date: str = "",
    *,
    limit: int = 0,
    offset: int = 0,
    media_type: str = "all",
    tag: str = "",
    search: str = "",
) -> dict[str, object]:
    paged = int(limit or 0) > 0
    if paged:
        _schedule_periodic_list_maintenance()
    else:
        _run_periodic_list_maintenance()
    all_tags = load_tags()
    retention_days = config.image_retention_days
    raw_items = image_storage_service.list_items(
        base_url,
        start_date,
        end_date,
        refresh_index=not paged,
        verify_existing=True,
    )
    merged_items: dict[str, dict[str, object]] = {}
    for item in raw_items:
        path = _clean_text(item.get("path") or item.get("rel"))
        if path:
            merged_items[path] = item
    for item in _queue_final_image_items(base_url, start_date, end_date):
        path = _clean_text(item.get("path") or item.get("rel"))
        if path:
            merged_items[path] = item
    normalized_items = []
    for item in merged_items.values():
        path = str(item["path"])
        tags = all_tags.get(path, [])
        current_type = _media_type_for_item(item)
        expired, expires_in_seconds = _expiry_for_item(item, retention_days)
        item_url = str(item.get("url") or f"{base_url.rstrip('/')}/images/{path}")
        normalized_items.append({
            **item,
            "type": current_type,
            "filename": str(item.get("name") or Path(path).name),
            "url": item_url,
            "thumbnail_url": str(item.get("thumbnail_url") or thumbnail_url_for_image(base_url, item_url, path)),
            "tags": tags,
            "expired": expired,
            "expires_in_seconds": expires_in_seconds,
        })

    tag_filter = tag.strip()
    search_filter = search.strip()
    base_items = [
        item for item in normalized_items
        if (not tag_filter or tag_filter == "all" or tag_filter in item.get("tags", []))
        and _matches_image_search(item, list(item.get("tags", [])), search_filter)
    ]
    counts = {
        "all": len(base_items),
        "image": sum(1 for item in base_items if item.get("type") == "image"),
        "video": sum(1 for item in base_items if item.get("type") == "video"),
        "music": sum(1 for item in base_items if item.get("type") == "music"),
    }
    wanted_type = media_type.strip().lower()
    if wanted_type and wanted_type != "all":
        items = [item for item in base_items if item.get("type") == wanted_type]
    else:
        items = base_items

    total = len(items)
    total_size = sum(int(item.get("size") or 0) for item in items)
    page, page_size, page_count = _page_meta(total, limit, offset)
    safe_offset = max(0, int(offset or 0))
    if int(limit or 0) > 0 and total > 0 and safe_offset >= total:
        safe_offset = (page_count - 1) * page_size
        page = page_count
    page_items = items[safe_offset:safe_offset + page_size] if int(limit or 0) > 0 else items
    groups: dict[str, list[dict[str, object]]] = {}
    for item in page_items:
        groups.setdefault(str(item["date"]), []).append(item)
    return {
        "items": page_items,
        "groups": [{"date": key, "items": value} for key, value in groups.items()],
        "total": total,
        "total_size": total_size,
        "counts": counts,
        "retention_days": retention_days,
        "limit": page_size,
        "offset": safe_offset,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "has_more": page < page_count,
    }


def delete_images(paths: list[str] | None = None, start_date: str = "", end_date: str = "", all_matching: bool = False) -> dict[str, int]:
    root = config.images_dir.resolve()
    targets = [
        str(item["path"])
        for item in image_storage_service.list_items("", start_date=start_date, end_date=end_date)
    ] if all_matching else (paths or [])
    if all_matching:
        for item in _queue_public_final_artifacts() or []:
            rel = _clean_text(item.get("path") or item.get("rel"))
            if not rel:
                continue
            created_at = _clean_text(item.get("created_at"))
            day = _clean_text(item.get("date")) or created_at[:10]
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            targets.append(rel)
        targets = list(dict.fromkeys(targets))
    removed = 0
    protected_count = 0
    protected = config.protected_image_paths()
    for item in targets:
        try:
            relative = _safe_relative_path(str(item or ""))
        except HTTPException:
            continue
        if relative in protected or _is_private_queue_artifact(relative):
            protected_count += 1
            continue
        queue_deleted = _delete_queue_public_final_artifact(relative)
        if queue_deleted is not None:
            if queue_deleted:
                removed += 1
                for thumbnail in (_thumbnail_path(relative), config.image_thumbnails_dir / _safe_relative_path(relative)):
                    if thumbnail.is_file():
                        thumbnail.unlink()
                remove_tags(relative)
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        try:
            deleted = image_storage_service.delete(relative)
        except Exception:
            continue
        if not deleted:
            continue
        removed += 1
        for thumbnail in (_thumbnail_path(relative), config.image_thumbnails_dir / _safe_relative_path(relative)):
            if thumbnail.is_file():
                thumbnail.unlink()
        remove_tags(relative)
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    return {"removed": removed, "protected": protected_count}


def download_images_zip(paths: list[str]) -> io.BytesIO:
    root = config.images_dir.resolve()
    buf = io.BytesIO()
    added = 0
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in paths:
            rel = _safe_relative_path(item)
            if not _is_public_image_relative_path(rel):
                continue
            path = (root / rel).resolve()
            payload: bytes | None = None
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                payload = path.read_bytes()
            else:
                artifact_path = _queue_public_artifact_path(rel)
                if artifact_path is not None:
                    try:
                        payload = artifact_path.read_bytes()
                    except Exception:
                        payload = None
                if payload is None:
                    try:
                        payload = image_storage_service.get_bytes(rel)
                    except Exception:
                        payload = _fetch_public_artifact_url(rel)
                if payload is None:
                    continue
            if not _is_verified_image_payload(payload):
                continue
            name = Path(rel).name
            if name in used_names:
                stem = path.stem
                suffix = path.suffix
                counter = 2
                while f"{stem}_{counter}{suffix}" in used_names:
                    counter += 1
                name = f"{stem}_{counter}{suffix}"
            used_names.add(name)
            zf.writestr(name, payload)
            added += 1
    if added == 0:
        raise HTTPException(status_code=404, detail="no images found")
    buf.seek(0)
    return buf


def storage_stats() -> dict:
    import shutil
    usage = shutil.disk_usage(config.images_dir)
    total_mb = usage.total // (1024 * 1024)
    used_mb = usage.used // (1024 * 1024)
    free_mb = usage.free // (1024 * 1024)

    image_count = 0
    image_size = 0
    for p in config.images_dir.rglob("*"):
        if p.is_file() and _is_public_image_relative_path(p.relative_to(config.images_dir).as_posix()):
            image_count += 1
            image_size += p.stat().st_size

    queue_count, queue_size = _queue_public_artifact_stats(config.images_dir)
    image_count += queue_count
    image_size += queue_size

    return {
        "disk_total_mb": total_mb,
        "disk_used_mb": used_mb,
        "disk_free_mb": free_mb,
        "image_count": image_count,
        "image_size_mb": image_size // (1024 * 1024),
        "image_size_bytes": image_size,
    }


def compress_images(quality: int = 60) -> dict:
    """重新压缩所有图片，返回节省的空间"""
    saved = 0
    count = 0
    protected = config.protected_image_paths()
    queue_paths = {
        _clean_text(item.get("path") or item.get("rel"))
        for item in (_queue_public_final_artifacts() or [])
        if _clean_text(item.get("path") or item.get("rel"))
    }
    try:
        items = image_storage_service.list_items(
            "",
            refresh_index=True,
            verify_existing=True,
        )
    except Exception:
        items = []
    if not items:
        root = config.images_dir.resolve()
        items = [
            {
                "rel": path.relative_to(root).as_posix(),
                "path": path.relative_to(root).as_posix(),
                "local": True,
                "webdav": False,
            }
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in IMAGE_EXTENSIONS
            and not _is_private_queue_artifact(path.relative_to(root).as_posix())
            and path.relative_to(root).as_posix() not in queue_paths
        ]

    for item in items:
        rel = _clean_text(item.get("path") or item.get("rel"))
        if (
            not rel
            or rel in protected
            or _is_private_queue_artifact(rel)
            or rel in queue_paths
            or Path(rel).suffix.lower() not in IMAGE_EXTENSIONS
        ):
            continue
        path = (config.images_dir / _safe_relative_path(rel)).resolve()
        try:
            local = bool(item.get("local")) and path.is_file()
            original = path.read_bytes() if local else image_storage_service.get_bytes(rel)
            with Image.open(io.BytesIO(original)) as source:
                image = ImageOps.exif_transpose(source)
                image_format = str(source.format or Path(rel).suffix.lstrip(".")).upper()
                if image_format in {"JPG", "JPEG"} and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                output = io.BytesIO()
                save_kwargs: dict[str, object] = {}
                if image_format in {"JPEG", "WEBP"}:
                    save_kwargs["quality"] = max(1, min(95, int(quality)))
                elif image_format == "PNG":
                    save_kwargs["optimize"] = True
                elif image_format == "GIF":
                    save_kwargs["optimize"] = True
                image.save(output, format=image_format, **save_kwargs)
            compressed = output.getvalue()
            if len(compressed) >= len(original):
                continue
            image_storage_service.save_at_path(rel, compressed, config.base_url)
            saved += len(original) - len(compressed)
            count += 1
        except Exception:
            pass
    return {"compressed": count, "saved_bytes": saved, "saved_mb": saved // (1024 * 1024)}


def delete_to_target(target_free_mb: int, dry_run: bool = False) -> dict:
    """删除最旧的图片直到剩余空间达到 target_free_mb"""
    import shutil
    usage = shutil.disk_usage(config.images_dir)
    megabyte = 1024 * 1024
    current_free_bytes = int(usage.free)
    target_free_bytes = max(0, int(target_free_mb)) * megabyte
    current_free = current_free_bytes // megabyte
    if current_free_bytes >= target_free_bytes and not dry_run:
        return {"removed": 0, "current_free_mb": current_free, "target_free_mb": target_free_mb, "done": True}

    root = config.images_dir.resolve()
    protected = config.protected_image_paths()
    queue_artifacts = _queue_public_final_artifacts() or []
    queue_paths: set[str] = set()
    for item in queue_artifacts:
        rel = _clean_text(item.get("path") or item.get("rel"))
        if not rel:
            continue
        try:
            queue_paths.add(_safe_relative_path(rel))
        except HTTPException:
            continue
    files: list[tuple[Path, str, bool]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if _is_private_queue_artifact(rel) or rel in protected:
            continue
        files.append((path, rel, rel in queue_paths))
    queue_root, queue_candidates = _queue_local_artifact_candidates(
        root,
        queue_artifacts,
        protected,
    )
    files.extend((path, rel, True) for path, rel in queue_candidates)
    files.sort(key=lambda item: item[0].stat().st_mtime)
    removed = 0
    freed = 0
    for p, rel, is_queue_artifact in files:
        if current_free_bytes + freed >= target_free_bytes:
            break
        size = p.stat().st_size
        if not dry_run:
            if is_queue_artifact:
                queue_deleted = _delete_queue_public_final_artifact(rel)
                if not queue_deleted:
                    continue
                for tp in (_thumbnail_path(rel), config.image_thumbnails_dir / _safe_relative_path(rel)):
                    if tp.is_file():
                        tp.unlink()
                remove_tags(rel)
                freed += size
                removed += 1
                continue
            try:
                if not image_storage_service.delete(rel):
                    continue
            except Exception:
                continue
            for tp in (_thumbnail_path(rel), config.image_thumbnails_dir / _safe_relative_path(rel)):
                if tp.is_file():
                    tp.unlink()
            remove_tags(rel)
        freed += size
        removed += 1

    if not dry_run:
        _cleanup_empty_dirs(config.images_dir)
        _cleanup_empty_dirs(config.image_thumbnails_dir)
        if queue_root is not None and queue_root != root:
            _cleanup_empty_dirs(queue_root)

    return {
        "removed": removed,
        "freed_mb": freed // megabyte,
        "target_free_mb": target_free_mb,
        "current_free_mb": (current_free_bytes + freed) // megabyte,
        "done": (current_free_bytes + freed) >= target_free_bytes,
        "dry_run": dry_run,
    }

def _auto_cleanup_worker(stop_event: threading.Event) -> None:
    """后台线程：每30分钟检查存储，空间低于阈值自动清理最旧图片"""
    import shutil
    min_free_mb = getattr(config, "image_min_free_mb", None)
    if min_free_mb is None:
        min_free_mb = 500

    while not stop_event.wait(1800):  # 每30分钟
        try:
            cleanup_image_retention()
            cleanup_image_thumbnails()
            usage = shutil.disk_usage(config.images_dir)
            free_mb = usage.free // (1024 * 1024)
            if free_mb < min_free_mb:
                logger.warning({
                    "event": "image_storage_pressure",
                    "free_mb": free_mb,
                    "min_free_mb": min_free_mb,
                    "action": "new_image_submissions_paused",
                })
        except Exception:
            pass


def start_image_cleanup_scheduler(stop_event: threading.Event) -> threading.Thread:
    t = threading.Thread(target=_auto_cleanup_worker, args=(stop_event,), daemon=True, name="image-cleanup")
    t.start()
    return t
