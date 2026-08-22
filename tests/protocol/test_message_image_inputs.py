from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.protocol.conversation import normalize_messages
from utils import helper


def test_message_base64_limit_is_checked_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_JSON_IMAGE_BYTES", 3)

    def unexpected_decode(*args, **kwargs):
        raise AssertionError("oversized base64 must be rejected before decoding")

    monkeypatch.setattr(helper.base64, "b64decode", unexpected_decode)

    with pytest.raises(HTTPException, match="too large"):
        helper.extract_image_from_message_content([
            {"type": "image_url", "image_url": "data:image/png;base64,AAAAAAAA"},
        ])


def test_message_image_count_is_checked_before_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_JSON_EDIT_IMAGES", 2)

    def unexpected_fetch(value):
        raise AssertionError("too many references must be rejected before fetching")

    monkeypatch.setattr(helper, "_decode_message_image_url", unexpected_fetch)

    with pytest.raises(HTTPException, match="up to 2"):
        helper.extract_image_from_message_content([
            {"type": "image_url", "image_url": f"https://images.example/{index}.png"}
            for index in range(3)
        ])


def test_message_images_obey_total_limit_across_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_JSON_IMAGE_TOTAL_BYTES", 3)

    with pytest.raises(HTTPException, match="total image input"):
        normalize_messages([
            {"role": "user", "content": [{"type": "image", "data": b"aa", "mime": "image/png"}]},
            {"role": "user", "content": [{"type": "image", "data": b"bb", "mime": "image/png"}]},
        ])


def test_message_remote_image_uses_safe_bounded_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import image_inputs

    captured: dict[str, object] = {}

    def download(url: str, *, max_bytes: int | None = None):
        captured.update(url=url, max_bytes=max_bytes)
        return b"png", "cat.png", "image/png"

    monkeypatch.setattr(image_inputs, "_download_image_url", download)

    result = helper.extract_image_from_message_content([
        {"type": "image_url", "image_url": "https://images.example/cat.png"},
    ])

    assert result == [(b"png", "image/png")]
    assert captured == {
        "url": "https://images.example/cat.png",
        "max_bytes": helper.MAX_JSON_IMAGE_BYTES,
    }


def test_json_edit_images_obey_total_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "MAX_JSON_IMAGE_TOTAL_BYTES", 3)

    with pytest.raises(HTTPException, match="total image input"):
        helper.normalize_json_edit_images(images=["YWE=", "YmI="])
