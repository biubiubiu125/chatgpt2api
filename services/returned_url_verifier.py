from __future__ import annotations

from ipaddress import ip_address
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from curl_cffi import CurlOpt, requests as curl_requests


class ReturnedUrlVerificationError(RuntimeError):
    pass


class _ReturnedUrlResponseTooLarge(RuntimeError):
    pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_URL_OPENER = build_opener(_NoRedirectHandler)


class _PinnedResponse:
    def __init__(self, response, session, payload: bytes | None = None) -> None:  # noqa: ANN001
        self.status = int(response.status_code)
        self.headers = response.headers
        self._payload = bytes(response.content) if payload is None else bytes(payload)
        self._session = session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        self._session.close()
        return False

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._payload if limit < 0 else self._payload[:limit]


def urlopen(request: Request, timeout: float):  # noqa: ANN201
    parsed = urlsplit(request.full_url)
    host = str(parsed.hostname or "").strip()
    verified_addresses = tuple(
        str(value or "").strip()
        for value in getattr(request, "_chatgpt2api_verified_addresses", ())
        if str(value or "").strip()
    )
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        return _URL_OPENER.open(request, timeout=timeout)
    if host and verified_addresses:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        max_bytes = max(0, int(getattr(request, "_chatgpt2api_max_bytes", 0) or 0))
        last_error: Exception | None = None
        for address in verified_addresses:
            session = None
            payload = bytearray()
            too_large = False

            def receive(chunk: bytes) -> int:
                nonlocal too_large
                if max_bytes > 0 and len(payload) + len(chunk) > max_bytes:
                    too_large = True
                    raise _ReturnedUrlResponseTooLarge()
                payload.extend(chunk)
                return len(chunk)

            try:
                session = curl_requests.Session(
                    allow_redirects=False,
                    trust_env=False,
                    curl_options={
                        CurlOpt.RESOLVE: [f"{host}:{port}:{_curl_resolve_address(address)}"],
                    },
                )
                request_kwargs = {
                    "headers": dict(request.header_items()),
                    "timeout": timeout,
                    "allow_redirects": False,
                }
                if max_bytes > 0:
                    request_kwargs["content_callback"] = receive
                response = session.request(
                    request.get_method(),
                    request.full_url,
                    **request_kwargs,
                )
                bounded_payload = bytes(payload) if max_bytes > 0 else None
                return _PinnedResponse(response, session, bounded_payload)
            except Exception as exc:
                last_error = _ReturnedUrlResponseTooLarge() if too_large else exc
                if session is not None:
                    session.close()
        if isinstance(last_error, _ReturnedUrlResponseTooLarge):
            raise last_error
        raise URLError(str(last_error or "no verified address could be reached")) from last_error
    return _URL_OPENER.open(request, timeout=timeout)


def _looks_like_image(payload: bytes) -> bool:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if payload.startswith(b"\xff\xd8\xff"):
        return True
    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        return True
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return True
    return False


def _curl_resolve_address(address: str) -> str:
    value = str(address or "").strip()
    if ":" in value and not value.startswith("["):
        return f"[{value}]"
    return value


def _reject_private_or_local_address(address: str) -> None:
    try:
        parsed_ip = ip_address(address)
    except ValueError:
        return
    if not parsed_ip.is_global:
        raise ReturnedUrlVerificationError("returned image URL resolves to a private or local address")


def _validate_public_host(parsed, *, resolve_dns: bool = True) -> tuple[str, ...]:  # noqa: ANN001
    if parsed.username is not None or parsed.password is not None:
        raise ReturnedUrlVerificationError("returned image URL must not include credentials")
    host = str(parsed.hostname or "").strip()
    if not host:
        raise ReturnedUrlVerificationError("returned image URL must include a host")
    lowered = host.rstrip(".").lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise ReturnedUrlVerificationError("returned image URL resolves to a private or local address")
    try:
        parsed.port
    except ValueError as exc:
        raise ReturnedUrlVerificationError("returned image URL port is invalid") from exc
    try:
        ip_address(host)
    except ValueError:
        if not resolve_dns:
            return ()
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise ReturnedUrlVerificationError("returned image URL port is invalid") from exc
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ReturnedUrlVerificationError("returned image URL host could not be resolved") from exc
        addresses = {
            str(info[4][0])
            for info in infos
            if len(info) >= 5 and info[4]
        }
        if not addresses:
            raise ReturnedUrlVerificationError("returned image URL host could not be resolved")
        for address in addresses:
            _reject_private_or_local_address(address)
        return tuple(sorted(addresses))
    _reject_private_or_local_address(host)
    return (host,)


def validate_public_image_base_url(value: object, *, resolve_host: bool = False) -> str:
    text = str(value or "").strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ReturnedUrlVerificationError(
            "worker image base URL must be an http or https URL without query or fragment"
        )
    _validate_public_host(parsed, resolve_dns=resolve_host)
    path = parsed.path.rstrip("/")
    if path not in {"", "/images"}:
        raise ReturnedUrlVerificationError("worker image base URL path must be empty or /images")
    return text


def _validate_allowed_base_url(url: str, allowed_base_url: str) -> None:
    base = str(allowed_base_url or "").strip().rstrip("/")
    if not base:
        return
    parsed_url = urlsplit(url)
    parsed_base = urlsplit(base)
    if (
        parsed_base.scheme not in {"http", "https"}
        or not parsed_base.netloc
        or parsed_base.query
        or parsed_base.fragment
    ):
        raise ReturnedUrlVerificationError("worker image base URL must be an http or https URL without query or fragment")
    if parsed_url.scheme.lower() != parsed_base.scheme.lower() or parsed_url.netloc.lower() != parsed_base.netloc.lower():
        raise ReturnedUrlVerificationError("returned image URL is outside the worker image base URL")
    base_path = parsed_base.path.rstrip("/")
    if not base_path:
        return
    target_path = parsed_url.path.rstrip("/")
    if target_path == base_path or target_path.startswith(f"{base_path}/"):
        return
    raise ReturnedUrlVerificationError("returned image URL is outside the worker image base URL")


def _verify_once(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    allowed_base_url: str = "",
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReturnedUrlVerificationError("returned image URL must be an http or https URL")
    _validate_allowed_base_url(url, allowed_base_url)
    verified_addresses = _validate_public_host(parsed)
    byte_limit = max(512, int(max_bytes or 65536))
    request = Request(
        url,
        headers={
            "Accept": "image/*,*/*;q=0.8",
            "Range": f"bytes=0-{byte_limit - 1}",
            "User-Agent": "chatgpt2api-returned-url-verifier/1.0",
        },
        method="GET",
    )
    setattr(request, "_chatgpt2api_verified_addresses", verified_addresses)
    setattr(request, "_chatgpt2api_max_bytes", byte_limit)
    try:
        with urlopen(request, timeout=max(0.5, float(timeout_seconds or 5.0))) as response:
            status = int(getattr(response, "status", 0) or response.getcode() or 0)
            if status not in {200, 206}:
                raise ReturnedUrlVerificationError(f"returned image URL responded with HTTP {status}")
            headers = getattr(response, "headers", {}) or {}
            content_type = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            payload = response.read(byte_limit)
            _validate_public_host(parsed)
    except HTTPError as exc:
        raise ReturnedUrlVerificationError(f"returned image URL responded with HTTP {exc.code}") from exc
    except URLError as exc:
        raise ReturnedUrlVerificationError(f"returned image URL fetch failed: {exc.reason}") from exc
    except _ReturnedUrlResponseTooLarge as exc:
        raise ReturnedUrlVerificationError(
            f"returned image URL exceeds the {byte_limit}-byte verification limit"
        ) from exc
    except TimeoutError as exc:
        raise ReturnedUrlVerificationError("returned image URL fetch timed out") from exc
    if not payload:
        raise ReturnedUrlVerificationError("returned image URL returned empty content")
    if content_type and not content_type.startswith("image/"):
        raise ReturnedUrlVerificationError(f"returned image URL content-type is not image/*: {content_type}")
    if not _looks_like_image(payload):
        raise ReturnedUrlVerificationError("returned image URL content is not a recognized image")


def verify_returned_image_url(
    url: str,
    *,
    timeout_seconds: float = 5.0,
    attempts: int = 3,
    max_bytes: int = 65536,
    allowed_base_url: str = "",
) -> None:
    last_error: Exception | None = None
    total_attempts = max(1, int(attempts or 1))
    for index in range(total_attempts):
        try:
            _verify_once(
                url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
                allowed_base_url=allowed_base_url,
            )
            return
        except ReturnedUrlVerificationError as exc:
            last_error = exc
        if index + 1 < total_attempts:
            time.sleep(min(1.0, 0.2 * (index + 1)))
    raise ReturnedUrlVerificationError(str(last_error or "returned image URL verification failed"))
