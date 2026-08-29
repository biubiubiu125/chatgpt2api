#!/usr/bin/env python3
"""Validate the immutable deployment release manifest."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


class ManifestError(ValueError):
    """Raised when a release manifest is missing or inconsistent."""


REQUIRED_KEYS = {
    "CHATGPT2API_RELEASE_REF",
    "CHATGPT2API_IMAGE",
    "UV_VERSION",
    "CHATGPT2API_WARP_IMAGE",
    "CHATGPT2API_PRIVOXY_IMAGE",
    "CHATGPT2API_FLARESOLVERR_IMAGE",
}
OPTIONAL_KEYS = {"CHATGPT2API_IMAGE_DIGEST"}
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS
RELEASE_REF_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
UV_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _decode_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise ManifestError(f"line {line_number}: unterminated quoted value")
        value = value[1:-1]
    if any(character in value for character in ("\r", "\n")):
        raise ManifestError(f"line {line_number}: value contains a newline")
    return value


def load_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ManifestError(f"manifest does not exist: {path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ManifestError(f"line {line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        if not KEY_RE.fullmatch(key):
            raise ManifestError(f"line {line_number}: invalid key {key!r}")
        if key not in ALLOWED_KEYS:
            raise ManifestError(f"line {line_number}: unsupported key {key}")
        if key in values:
            raise ManifestError(f"line {line_number}: duplicate key {key}")
        values[key] = _decode_value(raw_value, line_number=line_number)

    missing = sorted(REQUIRED_KEYS - values.keys())
    if missing:
        raise ManifestError(f"manifest is missing required keys: {', '.join(missing)}")
    return values


def _validate_image(key: str, value: str) -> None:
    if not IMAGE_RE.fullmatch(value):
        raise ManifestError(f"{key} must be an immutable image reference with @sha256 digest")


def validate_manifest(
    values: dict[str, str],
    *,
    expected_release_ref: str | None = None,
    expected_image_repository: str | None = None,
) -> dict[str, str]:
    release_ref = values["CHATGPT2API_RELEASE_REF"]
    if not RELEASE_REF_RE.fullmatch(release_ref):
        raise ManifestError("CHATGPT2API_RELEASE_REF must be a 40-character lowercase commit SHA")
    if expected_release_ref is not None and release_ref != expected_release_ref:
        raise ManifestError(
            "CHATGPT2API_RELEASE_REF does not match the published commit: "
            f"{release_ref} != {expected_release_ref}"
        )

    image = values["CHATGPT2API_IMAGE"]
    _validate_image("CHATGPT2API_IMAGE", image)
    if expected_image_repository is not None and not image.startswith(
        expected_image_repository + "@"
    ):
        raise ManifestError(
            "CHATGPT2API_IMAGE does not match the published repository: "
            f"{image} != {expected_image_repository}@..."
        )

    image_digest = values.get("CHATGPT2API_IMAGE_DIGEST", "")
    if image_digest:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
            raise ManifestError("CHATGPT2API_IMAGE_DIGEST must be a sha256 digest")
        if not image.endswith("@" + image_digest):
            raise ManifestError("CHATGPT2API_IMAGE and CHATGPT2API_IMAGE_DIGEST do not match")

    if not UV_VERSION_RE.fullmatch(values["UV_VERSION"]):
        raise ManifestError("UV_VERSION must use the numeric MAJOR.MINOR.PATCH format")

    for key in (
        "CHATGPT2API_WARP_IMAGE",
        "CHATGPT2API_PRIVOXY_IMAGE",
        "CHATGPT2API_FLARESOLVERR_IMAGE",
    ):
        _validate_image(key, values[key])
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--release-ref", default=None)
    parser.add_argument("--image-repository", default=None)
    args = parser.parse_args()

    try:
        values = validate_manifest(
            load_manifest(args.manifest),
            expected_release_ref=args.release_ref,
            expected_image_repository=args.image_repository,
        )
    except ManifestError as exc:
        parser.error(str(exc))
    print(
        "release manifest is valid: "
        f"{values['CHATGPT2API_RELEASE_REF']} -> {values['CHATGPT2API_IMAGE']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
