from __future__ import annotations

from io import BytesIO

from curl_cffi.const import CurlOpt
import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request
from starlette.datastructures import Headers, UploadFile

from api import image_inputs


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 4), (40, 80, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_private_remote_image_url_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        image_inputs.validate_remote_image_url("http://127.0.0.1/admin.png")

    assert exc_info.value.status_code == 400
    assert "public" in str(exc_info.value.detail)


def test_public_remote_image_url_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        image_inputs.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("1.1.1.1", port))],
    )

    assert image_inputs.validate_remote_image_url("https://images.example/cat.png") == "https://images.example/cat.png"


@pytest.mark.anyio
async def test_uploaded_image_obeys_same_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_REFERENCE_BYTES", 16)
    reads: list[int] = []

    class TrackedBytesIO(BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    upload = UploadFile(
        TrackedBytesIO(_png_bytes()),
        filename="cat.png",
        headers=Headers({"content-type": "image/png"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await image_inputs.read_image_sources([upload])

    assert exc_info.value.status_code == 400
    assert "50MB" in str(exc_info.value.detail)
    assert reads and -1 not in reads


def test_remote_download_pins_validated_dns_address(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _png_bytes()
    captured: dict = {}

    monkeypatch.setattr(
        image_inputs.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("1.1.1.1", port))],
    )

    class Response:
        status_code = 200
        headers = {"content-type": "image/png", "content-length": str(len(payload))}

    def get(url, **kwargs):
        captured.update(kwargs)
        kwargs["content_callback"](payload)
        return Response()

    monkeypatch.setattr(image_inputs.requests, "get", get)

    image, _, _ = image_inputs._download_image_url("https://images.example/cat.png")

    assert image == payload
    assert captured["curl_options"][CurlOpt.RESOLVE] == ["images.example:443:1.1.1.1"]
    assert captured["curl_options"][CurlOpt.NOPROXY] == "*"


def test_remote_download_bypasses_global_proxy_to_keep_dns_pin_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _png_bytes()
    captured: dict = {}
    monkeypatch.setattr(
        image_inputs.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("1.1.1.1", port))],
    )

    class Response:
        status_code = 200
        headers = {"content-type": "image/png"}

    def get(url, **kwargs):
        captured.update(kwargs)
        kwargs["content_callback"](payload)
        return Response()

    monkeypatch.setattr(image_inputs.requests, "get", get)

    image_inputs._download_image_url("https://images.example/cat.png")

    assert "proxy" not in captured
    assert captured.get("verify", True) is True
    assert captured["curl_options"][CurlOpt.RESOLVE] == ["images.example:443:1.1.1.1"]
    assert captured["curl_options"][CurlOpt.NOPROXY] == "*"


def test_remote_download_stops_while_stream_exceeds_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_REFERENCE_BYTES", 16)
    monkeypatch.setattr(
        image_inputs.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("1.1.1.1", port))],
    )

    def get(url, **kwargs):
        kwargs["content_callback"](b"a" * 10)
        kwargs["content_callback"](b"b" * 10)
        raise AssertionError("size callback must abort the request")

    monkeypatch.setattr(image_inputs.requests, "get", get)

    with pytest.raises(HTTPException) as exc_info:
        image_inputs._download_image_url("https://images.example/cat.png")

    assert "16 bytes" in str(exc_info.value.detail)


def test_remote_download_accepts_a_stricter_caller_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        image_inputs.socket,
        "getaddrinfo",
        lambda host, port, type=0: [(2, 1, 6, "", ("1.1.1.1", port))],
    )

    def get(url, **kwargs):
        kwargs["content_callback"](b"a" * 9)
        kwargs["content_callback"](b"b" * 9)
        raise AssertionError("size callback must abort the request")

    monkeypatch.setattr(image_inputs.requests, "get", get)

    with pytest.raises(HTTPException) as exc_info:
        image_inputs._download_image_url("https://images.example/cat.png", max_bytes=16)

    assert "16 bytes" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_invalid_uploaded_image_is_rejected_before_enqueue() -> None:
    upload = UploadFile(
        BytesIO(b"not-an-image"),
        filename="bad.png",
        headers=Headers({"content-type": "image/png"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await image_inputs.read_image_sources([upload])

    assert exc_info.value.status_code == 400
    assert "invalid image" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_mask_without_image_is_rejected_before_enqueue() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await image_inputs.read_image_source_groups([], [(_png_bytes(), "mask.png", "image/png")])

    assert exc_info.value.status_code == 400
    assert "image is required" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_image_reference_count_and_total_bytes_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _png_bytes()
    source = (payload, "cat.png", "image/png")
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_REFERENCE_COUNT", 1)

    with pytest.raises(HTTPException, match="too many image references"):
        await image_inputs.read_image_sources([source, source])

    monkeypatch.setattr(image_inputs, "MAX_IMAGE_REFERENCE_COUNT", 16)
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_REFERENCE_TOTAL_BYTES", len(payload))
    with pytest.raises(HTTPException, match="total image input exceeds"):
        await image_inputs.read_image_sources([source, source])


@pytest.mark.anyio
async def test_chunked_json_body_is_stopped_at_request_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_EDIT_REQUEST_BYTES", 12)
    chunks = iter([b'{"prompt":', b'"cat","image":"abc"}'])

    async def receive():
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/images/edits",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )

    with pytest.raises(HTTPException) as exc_info:
        await image_inputs.parse_image_edit_request(request)

    assert exc_info.value.status_code == 413
