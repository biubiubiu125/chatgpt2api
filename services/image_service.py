from __future__ import annotations

import io
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

from services.config import config
from services.image_storage_service import image_storage_service
from services.image_tags_service import load_tags, remove_tags
from utils.log import logger

THUMBNAIL_SIZE = (320, 320)


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


def _iter_local_png_files() -> list[Path]:
    return sorted((p for p in config.images_dir.rglob("*.png") if p.is_file()), key=lambda p: p.stat().st_mtime)


def _stored_png_items() -> list[dict[str, object]]:
    return image_storage_service.list_png_items_for_cleanup()


def _stored_image_rel(item: dict[str, object]) -> str:
    return str(item.get("path") or item.get("rel") or "").strip()


def _stored_image_size(item: dict[str, object]) -> int:
    try:
        return max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        return 0


def _stored_image_timestamp(item: dict[str, object]) -> float:
    created_at = str(item.get("created_at") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(created_at[:19], fmt).timestamp()
        except ValueError:
            continue
    date_text = str(item.get("date") or "").strip()
    if date_text:
        try:
            return datetime.strptime(date_text[:10], "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    rel = _stored_image_rel(item)
    rel_parts = Path(rel).parts if rel else ()
    if len(rel_parts) >= 3:
        try:
            return datetime.strptime("/".join(rel_parts[:3]), "%Y/%m/%d").timestamp()
        except ValueError:
            pass
    if rel:
        path = (config.images_dir / rel).resolve()
        if path.is_file():
            return path.stat().st_mtime
    return 0.0


def get_image_response(relative_path: str) -> FileResponse | Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if image_storage_service.has_local(relative_path):
        return FileResponse(_safe_image_path(relative_path), headers=headers)
    return Response(content=image_storage_service.get_bytes(relative_path), media_type="image/png", headers=headers)


def _thumbnail_path(relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    return config.image_thumbnails_dir / f"{rel}.png"


def thumbnail_url(base_url: str, relative_path: str) -> str:
    return f"{base_url.rstrip('/')}/image-thumbnails/{_safe_relative_path(relative_path)}"


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None


def _delete_image_entry(relative_path: str) -> bool:
    removed = image_storage_service.delete(relative_path)
    for thumbnail in (_thumbnail_path(relative_path), config.image_thumbnails_dir / _safe_relative_path(relative_path)):
        if thumbnail.is_file():
            thumbnail.unlink()
    remove_tags(relative_path)
    return removed


def ensure_thumbnail(relative_path: str) -> Path:
    target = _thumbnail_path(relative_path)
    source_mtime = 0.0
    source: Path | None = None
    if image_storage_service.has_local(relative_path):
        source = _safe_image_path(relative_path)
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
            image.save(target, format="PNG", optimize=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="failed to create thumbnail") from exc
    return target


def get_thumbnail_response(relative_path: str) -> FileResponse:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    return FileResponse(ensure_thumbnail(relative_path), headers=headers)


def get_image_download_response(relative_path: str) -> FileResponse | Response:
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    }
    if image_storage_service.has_local(relative_path):
        path = _safe_image_path(relative_path)
        headers = {**cors_headers, "Content-Disposition": f'attachment; filename="{path.name}"'}
        return FileResponse(path, filename=path.name, headers=headers)
    rel = _safe_relative_path(relative_path)
    headers = {
        **cors_headers,
        "Content-Disposition": f'attachment; filename="{Path(rel).name}"',
    }
    return Response(
        content=image_storage_service.get_bytes(rel),
        media_type="image/png",
        headers=headers,
    )


def cleanup_image_thumbnails() -> int:
    thumbnails_root = config.image_thumbnails_dir
    removed = 0
    for path in thumbnails_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(thumbnails_root).as_posix()
        if not rel.endswith(".png") or not image_storage_service.exists(rel[:-4]):
            path.unlink()
            removed += 1
    _cleanup_empty_dirs(thumbnails_root)
    return removed


def delete_images_by_retention(cutoff_timestamp: float, dry_run: bool = False) -> int:
    removed = 0
    for item in _stored_png_items():
        rel = _stored_image_rel(item)
        if not rel:
            continue
        item_timestamp = _stored_image_timestamp(item)
        if item_timestamp and item_timestamp >= cutoff_timestamp:
            continue
        if not dry_run:
            _delete_image_entry(rel)
        removed += 1

    if not dry_run:
        _cleanup_empty_dirs(config.images_dir)
        _cleanup_empty_dirs(config.image_thumbnails_dir)

    return removed


def list_images(base_url: str, start_date: str = "", end_date: str = "") -> dict[str, object]:
    config.cleanup_old_images()
    cleanup_image_thumbnails()
    all_tags = load_tags()
    items = [
        {
            **item,
            "url": str(item.get("url") or f"{base_url.rstrip('/')}/images/{item['path']}"),
            "thumbnail_url": thumbnail_url(base_url, str(item["path"])),
            "tags": all_tags.get(str(item["path"]), []),
        }
        for item in image_storage_service.list_items(base_url, start_date, end_date)
    ]
    groups: dict[str, list[dict[str, object]]] = {}
    for item in items:
        groups.setdefault(str(item["date"]), []).append(item)
    return {"items": items, "groups": [{"date": key, "items": value} for key, value in groups.items()]}


def delete_images(paths: list[str] | None = None, start_date: str = "", end_date: str = "", all_matching: bool = False) -> dict[str, int]:
    root = config.images_dir.resolve()
    targets = [
        str(item["path"])
        for item in image_storage_service.list_items("", start_date=start_date, end_date=end_date)
    ] if all_matching else (paths or [])
    removed = 0
    for item in targets:
        path = (root / item).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if _delete_image_entry(item):
            removed += 1
    _cleanup_empty_dirs(root)
    _cleanup_empty_dirs(config.image_thumbnails_dir)
    return {"removed": removed}


def download_images_zip(paths: list[str]) -> io.BytesIO:
    root = config.images_dir.resolve()
    buf = io.BytesIO()
    added = 0
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in paths:
            rel = _safe_relative_path(item)
            path = (root / rel).resolve()
            payload: bytes | None = None
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                payload = path.read_bytes()
            else:
                try:
                    payload = image_storage_service.get_bytes(rel)
                except Exception:
                    continue
            name = path.name
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
    usage = shutil.disk_usage(config.images_dir)
    total_mb = usage.total // (1024 * 1024)
    used_mb = usage.used // (1024 * 1024)
    free_mb = usage.free // (1024 * 1024)

    image_count = 0
    image_size = 0
    for item in _stored_png_items():
        image_count += 1
        image_size += _stored_image_size(item)

    return {
        "disk_total_mb": total_mb,
        "disk_used_mb": used_mb,
        "disk_free_mb": free_mb,
        "image_count": image_count,
        "image_size_mb": image_size // (1024 * 1024),
        "image_size_bytes": image_size,
    }


def compress_images(quality: int = 60) -> dict:
    saved = 0
    count = 0
    for path in _iter_local_png_files():
        try:
            orig = path.stat().st_size
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                image.save(str(path) + ".tmp", format="PNG", optimize=True)
            new_size = Path(str(path) + ".tmp").stat().st_size
            if new_size < orig:
                Path(str(path) + ".tmp").replace(path)
                saved += orig - new_size
                count += 1
            else:
                Path(str(path) + ".tmp").unlink()
        except Exception:
            pass
    return {"compressed": count, "saved_bytes": saved, "saved_mb": saved // (1024 * 1024)}


def delete_to_storage_target(target_storage_mb: int, dry_run: bool = False) -> dict:
    current = storage_stats()
    current_storage_bytes = int(current.get("image_size_bytes") or 0)
    target_storage_bytes = max(0, int(target_storage_mb)) * 1024 * 1024
    if current_storage_bytes <= target_storage_bytes and not dry_run:
        return {
            "removed": 0,
            "freed_mb": 0,
            "current_storage_mb": current.get("image_size_mb", 0),
            "target_storage_mb": target_storage_mb,
            "done": True,
            "dry_run": dry_run,
        }

    removed = 0
    freed = 0
    for item in _stored_png_items():
        if current_storage_bytes - freed <= target_storage_bytes:
            break
        rel = _stored_image_rel(item)
        if not rel:
            continue
        size = _stored_image_size(item)
        if not dry_run:
            _delete_image_entry(rel)
        freed += size
        removed += 1

    if not dry_run:
        _cleanup_empty_dirs(config.images_dir)
        _cleanup_empty_dirs(config.image_thumbnails_dir)

    current_storage_mb = max(0, (current_storage_bytes - freed) // (1024 * 1024))
    return {
        "removed": removed,
        "freed_mb": freed // (1024 * 1024),
        "current_storage_mb": current_storage_mb,
        "target_storage_mb": target_storage_mb,
        "done": current_storage_mb <= target_storage_mb,
        "dry_run": dry_run,
    }


def _auto_cleanup_worker(stop_event: threading.Event) -> None:
    while not stop_event.wait(1800):
        try:
            removed_by_days = config.cleanup_old_images()
            cleanup_image_thumbnails()
            if removed_by_days:
                logger.info(
                    {
                        "event": "image_retention_cleanup_done",
                        "removed": removed_by_days,
                        "retention_days": config.image_retention_days,
                    }
                )

            max_storage_mb = config.image_max_storage_mb
            if max_storage_mb > 0:
                stats = storage_stats()
                current_storage_mb = int(stats.get("image_size_mb") or 0)
                if current_storage_mb > max_storage_mb:
                    logger.info(
                        {
                            "event": "image_auto_cleanup",
                            "mode": "image_storage_limit",
                            "current_storage_mb": current_storage_mb,
                            "target_storage_mb": max_storage_mb,
                        }
                    )
                    result = delete_to_storage_target(max_storage_mb)
                    logger.info({"event": "image_auto_cleanup_done", "mode": "image_storage_limit", **result})
        except Exception:
            pass


def start_image_cleanup_scheduler(stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(target=_auto_cleanup_worker, args=(stop_event,), daemon=True, name="image-cleanup")
    thread.start()
    return thread
