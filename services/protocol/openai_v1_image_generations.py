from __future__ import annotations

from typing import Any, Iterator

from services.image_failure import ImageGenerationError, image_failure
from services.protocol.conversation import (
    count_text_tokens,
    stream_image_chunks,
)
from services.protocol import durable_image
from utils.image_tokens import count_image_output_items_tokens, image_usage


def _require_durable(body: dict[str, Any]) -> None:
    if not durable_image.has_durable_context(body):
        raise ImageGenerationError(
            "durable image task context is required",
            failure=image_failure(
                "durable_context_required",
                raw_detail="image requests must enter the PostgreSQL durable queue",
            ),
        )


def handle(body: dict[str, Any]) -> dict[str, Any] | Iterator[dict[str, Any]]:
    prompt = str(body.get("prompt") or "")
    model = str(body.get("model") or "gpt-image-2")
    size = body.get("size")
    quality = str(body.get("quality") or "auto")
    response_format = durable_image.normalize_response_format(body.get("response_format"), "b64_json")
    _require_durable(body)
    if body.get("stream"):
        return stream_image_chunks(
            durable_image.stream_outputs(
                body,
                body,
                mode="generation",
                response_format=response_format,
                model=model,
            ),
            event_prefix="image_generation",
            usage_builder=lambda data: image_usage(
                input_text_tokens=count_text_tokens(prompt, model),
                output_tokens=count_image_output_items_tokens(data, size, quality),
            ),
        )
    result = durable_image.execute(body, body, mode="generation", response_format=response_format)
    result["usage"] = image_usage(
        input_text_tokens=count_text_tokens(prompt, model),
        output_tokens=count_image_output_items_tokens(result.get("data"), size, quality),
    )
    return result
