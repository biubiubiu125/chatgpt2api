from __future__ import annotations

from urllib.parse import urlsplit


def build_public_image_url(base_url: object, relative_path: object) -> str:
    prefix = str(base_url or "").strip().rstrip("/")
    rel = str(relative_path or "").strip().lstrip("/")
    if not rel:
        return prefix
    if not prefix:
        return f"/images/{rel}"
    parsed = urlsplit(prefix)
    if parsed.scheme and parsed.netloc:
        prefix = parsed._replace(query="", fragment="").geturl().rstrip("/")
    path = urlsplit(prefix).path.rstrip("/")
    if path.endswith("/images"):
        return f"{prefix}/{rel}"
    return f"{prefix}/images/{rel}"


def build_public_thumbnail_url(base_url: object, relative_path: object) -> str:
    prefix = str(base_url or "").strip().rstrip("/")
    rel = str(relative_path or "").strip().lstrip("/")
    if not rel:
        return prefix
    if not prefix:
        return f"/image-thumbnails/{rel}"
    parsed = urlsplit(prefix)
    if parsed.scheme and parsed.netloc:
        prefix = parsed._replace(query="", fragment="").geturl().rstrip("/")
    path = urlsplit(prefix).path.rstrip("/")
    if path.endswith("/images"):
        prefix = prefix[: -len("/images")]
    elif path.endswith("/image-thumbnails"):
        return f"{prefix}/{rel}"
    return f"{prefix}/image-thumbnails/{rel}"


def build_public_thumbnail_url_for_image_url(image_url: object, relative_path: object) -> str:
    raw = str(image_url or "").strip().rstrip("/")
    rel = str(relative_path or "").strip().lstrip("/")
    if not raw or not rel:
        return ""
    image_suffix = f"/images/{rel}".rstrip("/")
    thumbnail_suffix = f"/image-thumbnails/{rel}".rstrip("/")

    if raw == image_suffix:
        return thumbnail_suffix
    if raw == image_suffix.lstrip("/"):
        return thumbnail_suffix

    parsed = urlsplit(raw)
    if not (parsed.scheme and parsed.netloc):
        return ""
    clean_url = parsed._replace(query="", fragment="").geturl().rstrip("/")
    path = parsed.path.rstrip("/")
    if path.endswith(thumbnail_suffix):
        return clean_url
    if not path.endswith(image_suffix):
        return ""
    base_path = path[: -len(image_suffix)].rstrip("/")
    base = parsed._replace(path=base_path, query="", fragment="").geturl().rstrip("/")
    return build_public_thumbnail_url(base, rel)
