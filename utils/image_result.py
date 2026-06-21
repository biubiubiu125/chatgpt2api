from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

from utils.image_tokens import image_size_from_bytes


def save_image_bytes(image_data: bytes, base_url: str | None = None) -> str:
    from services.image_storage_service import image_storage_service

    return image_storage_service.save(image_data, base_url).url


def format_image_result(
    items: list[dict[str, Any]],
    prompt: str,
    response_format: str,
    base_url: str | None = None,
    created: int | None = None,
    message: str = "",
    save_image: Callable[[bytes, str | None], str] | None = None,
) -> dict[str, Any]:
    save = save_image or save_image_bytes
    data: list[dict[str, Any]] = []
    for item in items:
        b64_json = str(item.get("b64_json") or "").strip()
        if not b64_json:
            continue
        image_bytes = base64.b64decode(b64_json)
        image_size = image_size_from_bytes(image_bytes)
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
