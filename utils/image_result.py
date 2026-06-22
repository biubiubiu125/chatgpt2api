from __future__ import annotations

import base64
import io
import re
import time
from collections.abc import Callable
from typing import Any

from utils.image_resize_limits import get_image_resize_limits
from utils.image_tokens import image_size_from_bytes
from utils.log import logger


class ImageResizeError(RuntimeError):
    pass


def save_image_bytes(image_data: bytes, base_url: str | None = None) -> str:
    from services.image_storage_service import image_storage_service

    return image_storage_service.save(image_data, base_url).url


def _parse_target_size(size: object) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,5})x(\d{1,5})\s*", str(size or ""), re.IGNORECASE)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    return width, height


def _resize_image_bytes(image_data: bytes, target_size: tuple[int, int] | None) -> bytes:
    if not target_size:
        return image_data
    max_side, max_pixels = get_image_resize_limits()
    if target_size[0] > max_side or target_size[1] > max_side or target_size[0] * target_size[1] > max_pixels:
        raise ImageResizeError(
            f"image resize target too large: {target_size[0]}x{target_size[1]}, "
            f"max side={max_side}, max pixels={max_pixels}"
        )
    current_size = image_size_from_bytes(image_data)
    if current_size == target_size:
        return image_data
    try:
        from PIL import Image
    except Exception as exc:
        logger.warning({
            "event": "image_resize_pillow_unavailable",
            "target_size": target_size,
            "current_size": current_size,
            "error": str(exc)[:200],
        })
        raise ImageResizeError(f"image resize failed: Pillow unavailable ({exc})") from exc
    try:
        with Image.open(io.BytesIO(image_data)) as image:
            resized = image.convert("RGBA").resize(target_size, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            resized.save(output, format="PNG")
            return output.getvalue()
    except Exception as exc:
        logger.warning({
            "event": "image_resize_failed",
            "target_size": target_size,
            "current_size": current_size,
            "error": str(exc)[:200],
        })
        raise ImageResizeError(f"image resize failed: {exc}") from exc


def format_image_result(
    items: list[dict[str, Any]],
    prompt: str,
    response_format: str,
    base_url: str | None = None,
    created: int | None = None,
    message: str = "",
    requested_size: object = None,
    save_image: Callable[[bytes, str | None], str] | None = None,
) -> dict[str, Any]:
    save = save_image or save_image_bytes
    target_size = _parse_target_size(requested_size)
    data: list[dict[str, Any]] = []
    for item in items:
        b64_json = str(item.get("b64_json") or "").strip()
        if not b64_json:
            continue
        image_bytes = base64.b64decode(b64_json)
        image_bytes = _resize_image_bytes(image_bytes, target_size)
        b64_json = base64.b64encode(image_bytes).decode("ascii")
        image_size = image_size_from_bytes(image_bytes)
        if target_size and image_size != target_size:
            actual = f"{image_size[0]}x{image_size[1]}" if image_size else "unknown"
            raise ImageResizeError(
                f"image size mismatch after resize: expected {target_size[0]}x{target_size[1]}, got {actual}"
            )
        revised_prompt = str(item.get("revised_prompt") or prompt).strip() or prompt
        if response_format == "b64_json":
            result_item = {
                "b64_json": b64_json,
                "url": save(image_bytes, base_url),
                "revised_prompt": revised_prompt,
            }
        else:
            result_item = {
                "url": save(image_bytes, base_url),
                "revised_prompt": revised_prompt,
            }
        if image_size:
            result_item["width"], result_item["height"] = image_size
            result_item["size"] = f"{image_size[0]}x{image_size[1]}"
        data.append(result_item)
    result: dict[str, Any] = {"created": created or int(time.time()), "data": data}
    if data:
        for key in ("size", "width", "height"):
            if data[0].get(key):
                result[key] = data[0][key]
    if message and not data:
        result["message"] = message
    return result
