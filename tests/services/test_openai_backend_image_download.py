from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import zipfile

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
    monkeypatch.setattr("services.openai_backend_api._validate_public_host", lambda parsed: ("8.8.8.8",))

    with pytest.raises(ImageDownloadError, match="exceeds"):
        backend.download_image_bytes(["https://cdn.example/image.png"])

def test_image_download_rejects_private_signed_asset_url() -> None:
    backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
    backend.base_url = "https://chatgpt.com"
    backend.session = object()
    backend._headers = lambda _path: {}
    backend._signed_asset_headers = lambda: {}

    with pytest.raises(ImageDownloadError, match="private or local address|public address"):
        backend.download_image_bytes(["http://127.0.0.1/image.png"])


def test_image_download_disables_redirects_and_proxy_for_signed_assets(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class StreamingSession:
        def get(self, _url, **kwargs):
            calls.append(kwargs)
            callback = kwargs.get("content_callback")
            if callable(callback):
                callback(b"image-bytes")
            return SimpleNamespace(status_code=200, headers={"content-type": "image/png"}, content=b"")

    backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
    backend.base_url = "https://chatgpt.com"
    backend.session = StreamingSession()
    backend._headers = lambda _path: {}
    backend._signed_asset_headers = lambda: {}
    monkeypatch.setattr("services.openai_backend_api._validate_public_host", lambda parsed: ("8.8.8.8",))

    assert backend.download_image_bytes(["https://cdn.example/image.png"]) == [b"image-bytes"]
    assert calls[0]["allow_redirects"] is False
    assert str(calls[0]["curl_options"]).find("8.8.8.8") >= 0


def test_codex_image_responses_use_configured_session_stream(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class StreamingResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}
        text = ""

        def __init__(self) -> None:
            self.closed = False

        def iter_content(self):
            yield b'data: {"type":"response.completed","output":[]}\n\n'

        def close(self) -> None:
            self.closed = True

    response = StreamingResponse()

    class StreamingSession:
        def post(self, url, **kwargs):
            calls.append({"url": url, **kwargs})
            return response

    backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
    backend.base_url = "https://chatgpt.com"
    backend.access_token = "codex-token"
    backend.session = StreamingSession()

    monkeypatch.setattr(
        "services.openai_backend_api.account_service.get_account",
        lambda token: {"source_type": "codex", "email": "codex@example.test"},
    )
    monkeypatch.setattr(
        "services.openai_backend_api.account_service._decode_jwt_payload",
        lambda token: {},
    )
    monkeypatch.setattr(
        "services.openai_backend_api.urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("urlopen must not be used")),
    )

    events = list(backend.iter_codex_image_response_events("draw a cat"))

    assert events == [{"type": "response.completed", "output": []}]
    assert calls and calls[0]["url"] == "https://chatgpt.com/backend-api/codex/responses"
    assert calls[0]["stream"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer codex-token"
    assert response.closed is True


def test_editable_base64_image_rejects_malformed_payload() -> None:
    backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)

    with pytest.raises(ValueError, match="invalid base64 image data"):
        backend._decode_editable_base64_image("not-valid@@@", 1)


def test_editable_download_payload_rejects_non_zip_content_for_zip_artifact() -> None:
    with pytest.raises(RuntimeError, match="invalid"):
        OpenAIBackendAPI._validate_editable_download_payload(
            b"not-a-zip",
            "artifact.zip",
            "application/zip",
            set(),
            (),
            ".pptx",
        )


def test_editable_download_payload_accepts_valid_editable_artifacts() -> None:
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types />")

    OpenAIBackendAPI._validate_editable_download_payload(
        zip_buffer.getvalue(),
        "deck.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        ("presentationml.presentation",),
        ".pptx",
    )
    OpenAIBackendAPI._validate_editable_download_payload(
        b"8BPS\x00\x01psd-bytes",
        "design.psd",
        "image/vnd.adobe.photoshop",
        {"image/vnd.adobe.photoshop"},
        ("photoshop",),
        ".psd",
    )
