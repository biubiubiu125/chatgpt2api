from __future__ import annotations

import os

DEFAULT_IMAGE_RESIZE_SIDE = 4096
DEFAULT_IMAGE_RESIZE_PIXELS = DEFAULT_IMAGE_RESIZE_SIDE * DEFAULT_IMAGE_RESIZE_SIDE
ABSOLUTE_IMAGE_RESIZE_SIDE = 8192
ABSOLUTE_IMAGE_RESIZE_PIXELS = ABSOLUTE_IMAGE_RESIZE_SIDE * ABSOLUTE_IMAGE_RESIZE_SIDE


def _bounded_positive_int(value: object, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        normalized = int(value)
    except (OverflowError, TypeError, ValueError):
        normalized = default
    normalized = max(minimum, normalized)
    return min(normalized, maximum) if maximum is not None else normalized


def get_image_resize_limits() -> tuple[int, int]:
    max_side = _bounded_positive_int(
        os.getenv("CHATGPT2API_IMAGE_RESIZE_MAX_SIDE"),
        DEFAULT_IMAGE_RESIZE_SIDE,
        1,
        ABSOLUTE_IMAGE_RESIZE_SIDE,
    )
    max_pixels = _bounded_positive_int(
        os.getenv("CHATGPT2API_IMAGE_RESIZE_MAX_PIXELS"),
        DEFAULT_IMAGE_RESIZE_PIXELS,
        1,
        ABSOLUTE_IMAGE_RESIZE_PIXELS,
    )
    return max_side, max_pixels
