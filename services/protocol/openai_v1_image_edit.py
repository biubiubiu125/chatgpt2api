from __future__ import annotations

from io import BytesIO
from typing import Any, Iterator

from PIL import Image

from services.image_failure import ImageGenerationError, image_failure
from services.protocol.conversation import (
    count_text_tokens,
    stream_image_chunks,
)
from services.protocol import durable_image
from utils.image_tokens import count_image_inputs_tokens, count_image_output_items_tokens, image_usage


def _composite_mask(
    images: list[tuple[bytes, str, str]],
    masks: list[tuple[bytes, str, str]],
) -> list[tuple[bytes, str, str]]:
    """将 mask 的 alpha 通道合成到图片中，标识需要编辑的区域。

    mask 的透明区域（低 alpha）= 需要编辑的区域，
    mask 的不透明区域（高 alpha）= 保留的区域。
    如果无 mask 则返回原图。
    """
    if not masks:
        return images
    result: list[tuple[bytes, str, str]] = []
    for i, (data, filename, mime_type) in enumerate(images):
        mask_data = masks[i][0] if i < len(masks) else masks[-1][0]
        img = Image.open(BytesIO(data)).convert("RGBA")
        mask_img = Image.open(BytesIO(mask_data))
        if mask_img.mode == "RGBA":
            alpha = mask_img.split()[3]
        elif mask_img.mode == "L":
            alpha = mask_img
        else:
            alpha = mask_img.convert("L")
        alpha = alpha.resize(img.size, Image.LANCZOS)
        img.putalpha(alpha)
        buf = BytesIO()
        img.save(buf, format="PNG")
        result.append((buf.getvalue(), filename, "image/png"))
    return result


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    images = body.get("images") or []
    masks = body.get("mask") or []
    images = _composite_mask(images, masks)
    model = str(body.get("model") or "gpt-image-2")
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    response_format = durable_image.normalize_response_format(body.get("response_format"), "b64_json")
    if not durable_image.has_durable_context(body):
        raise ImageGenerationError(
            "durable image task context is required",
            failure=image_failure(
                "durable_context_required",
                raw_detail="image edit requests must enter the PostgreSQL durable queue",
            ),
        )
    if not images and not durable_image.submission_task_id(body):
        raise ImageGenerationError(
            "image is required",
            failure=image_failure("invalid_image_input"),
        )
    if body.get("stream"):
        return stream_image_chunks(
            durable_image.stream_outputs(
                body,
                body,
                mode="edit",
                response_format=response_format,
                model=model,
            ),
            event_prefix="image_edit",
            partial_images=body.get("partial_images"),
            usage_builder=lambda data: image_usage(
                input_text_tokens=count_text_tokens(prompt, model),
                input_image_tokens=count_image_inputs_tokens(images, model),
                output_tokens=count_image_output_items_tokens(data, size, quality),
            ),
        )
    result = durable_image.execute(body, body, mode="edit", response_format=response_format)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        input_image_tokens=count_image_inputs_tokens(images, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
