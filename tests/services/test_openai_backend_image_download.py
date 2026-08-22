from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.image_failure import ImageDownloadError
from services.openai_backend_api import OpenAIBackendAPI


def test_image_download_aborts_before_response_exceeds_limit(monkeypatch) -> None:
    class StreamingSession:
        def get(self, _url, **kwargs):
            callback = kwargs.get("content_callback")
            if callback is None:
                return SimpleNamespace(
                    status_code=200,
                    headers={},
                    content=b"123456",
                )
            callback(b"123")
            callback(b"456")
            return SimpleNamespace(status_code=200, headers={}, content=b"")

    backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
    backend.base_url = "https://chatgpt.com"
    backend.session = StreamingSession()
    backend._headers = lambda _path: {}
    backend._signed_asset_headers = lambda: {}
    monkeypatch.setattr("services.openai_backend_api.MAX_IMAGE_DOWNLOAD_BYTES", 5)

    with pytest.raises(ImageDownloadError, match="exceeds"):
        backend.download_image_bytes(["https://cdn.example/image.png"])
