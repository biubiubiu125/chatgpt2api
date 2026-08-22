from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from urllib.parse import urlsplit

from services.image_url import build_public_image_url


URL_ONLY_DELIVERY_MODE = "node_url"
URL_ONLY_DELIVERY_MODES = {
    URL_ONLY_DELIVERY_MODE,
    "url_only",
    "node-local",
    "node_local_url",
}


def is_url_only_delivery_mode(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in URL_ONLY_DELIVERY_MODES:
        return True
    return normalized.replace("-", "_") in URL_ONLY_DELIVERY_MODES


def _is_local_or_private_host(hostname: str) -> bool:
    value = str(hostname or "").strip().strip("[]").lower()
    if not value:
        return True
    if (
        value == "localhost"
        or value.endswith(".localhost")
        or value.endswith(".local")
        or value == "host.docker.internal"
    ):
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def is_url_only_result(item: Mapping[str, object]) -> bool:
    url = str(item.get("url") or "").strip()
    if not url:
        return False
    explicit_mode = is_url_only_delivery_mode(item.get("delivery_mode"))
    returned_url = str(item.get("returned_url") or "").strip()
    if returned_url and returned_url != url:
        return False
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or _is_local_or_private_host(parsed_url.hostname or "")
    ):
        return False
    image_base_url = str(item.get("image_base_url") or "").strip().rstrip("/")
    relative_path = str(item.get("relative_path") or "").strip().lstrip("/")
    if explicit_mode:
        return bool(relative_path)
    worker_id = str(item.get("worker_id") or "").strip()
    if not image_base_url or not relative_path:
        return False
    if not worker_id:
        return False
    expected_url = build_public_image_url(image_base_url, relative_path)
    parsed_base = urlsplit(image_base_url)
    if _is_local_or_private_host(parsed_base.hostname or ""):
        return False
    return (
        parsed_base.scheme in {"http", "https"}
        and parsed_url.scheme == parsed_base.scheme
        and parsed_url.netloc == parsed_base.netloc
        and url.rstrip("/") == expected_url.rstrip("/")
    )


def url_only_result_matches_base_url(
    item: Mapping[str, object],
    base_url: object,
) -> bool:
    """Require a URL-only result to resolve to its authoritative worker base."""
    url = str(item.get("returned_url") or item.get("url") or "").strip()
    relative_path = str(item.get("relative_path") or "").strip().lstrip("/")
    base = str(base_url or "").strip()
    if not url or not relative_path or not base:
        return False
    expected_url = build_public_image_url(base, relative_path)
    parsed_url = urlsplit(url)
    parsed_expected = urlsplit(expected_url)
    return (
        parsed_url.scheme in {"http", "https"}
        and parsed_url.scheme.lower() == parsed_expected.scheme.lower()
        and parsed_url.netloc.lower() == parsed_expected.netloc.lower()
        and parsed_url.path.rstrip("/") == parsed_expected.path.rstrip("/")
        and not parsed_url.query
        and not parsed_url.fragment
    )
