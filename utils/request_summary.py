from __future__ import annotations


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(
            _text(value.get(key))
            for key in ("text", "input_text", "content", "input", "instructions", "system", "prompt")
        )
    return ""


def request_text(*values: object) -> str:
    return "\n".join(part for value in values if (part := _text(value).strip()))


def request_shape(*values: object) -> dict[str, int]:
    """Return a safe structural summary without logging prompts or image bytes."""
    stats = {
        "response_message_items": 0,
        "input_image_parts": 0,
        "image_url_parts": 0,
        "image_parts": 0,
        "data_url_images": 0,
        "remote_image_urls": 0,
        "literal_image_placeholders": 0,
    }

    def walk(value: object, key: str = "") -> None:
        if isinstance(value, str):
            text = value.strip()
            lower = text.lower()
            if "<image>" in lower:
                stats["literal_image_placeholders"] += 1
            if lower.startswith("data:image/"):
                stats["data_url_images"] += 1
            elif key in {"image_url", "url"} and lower.startswith(("http://", "https://")):
                stats["remote_image_urls"] += 1
            return
        if isinstance(value, list):
            for item in value:
                walk(item, key)
            return
        if not isinstance(value, dict):
            return
        item_type = str(value.get("type") or "").strip()
        if item_type == "message":
            stats["response_message_items"] += 1
        elif item_type == "input_image":
            stats["input_image_parts"] += 1
        elif item_type == "image_url":
            stats["image_url_parts"] += 1
        elif item_type == "image":
            stats["image_parts"] += 1
        for child_key, child in value.items():
            walk(child, str(child_key))

    for value in values:
        walk(value)
    return {key: value for key, value in stats.items() if value}
