from __future__ import annotations

from pathlib import Path

import pytest

from scripts.validate_release_manifest import ManifestError, load_manifest, validate_manifest


RELEASE_REF = "baa6567484c5a86c2e572b07d7d68cf854a0ab07"
IMAGE = "ghcr.io/example/chatgpt2api@sha256:" + "a" * 64
WARP_IMAGE = "caomingjun/warp@sha256:" + "b" * 64
PRIVOXY_IMAGE = "vimagick/privoxy@sha256:" + "c" * 64
FLARESOLVERR_IMAGE = "flaresolverr/flaresolverr@sha256:" + "d" * 64


def manifest_text(**overrides: str) -> str:
    values = {
        "CHATGPT2API_RELEASE_REF": RELEASE_REF,
        "CHATGPT2API_IMAGE": IMAGE,
        "UV_VERSION": "0.8.17",
        "CHATGPT2API_WARP_IMAGE": WARP_IMAGE,
        "CHATGPT2API_PRIVOXY_IMAGE": PRIVOXY_IMAGE,
        "CHATGPT2API_FLARESOLVERR_IMAGE": FLARESOLVERR_IMAGE,
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def write_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "release-manifest.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_current_manifest_is_valid() -> None:
    path = Path(__file__).parents[2] / "deploy" / "release-manifest.env"
    values = validate_manifest(load_manifest(path), expected_release_ref=RELEASE_REF)
    assert values["CHATGPT2API_IMAGE"].endswith("@" + "sha256:" + "c70f118780c9b6e194353b09e8530e20eeed2496cddf9f80ee36c41775178f0a")


def test_manifest_rejects_mismatched_image_digest(tmp_path: Path) -> None:
    path = write_manifest(tmp_path, manifest_text(CHATGPT2API_IMAGE_DIGEST="sha256:" + "e" * 64))
    with pytest.raises(ManifestError, match="CHATGPT2API_IMAGE"):
        validate_manifest(load_manifest(path))


def test_manifest_rejects_unknown_keys(tmp_path: Path) -> None:
    path = write_manifest(tmp_path, manifest_text(UNEXPECTED="value"))
    with pytest.raises(ManifestError, match="unsupported key"):
        load_manifest(path)


def test_manifest_can_be_checked_against_the_published_commit(tmp_path: Path) -> None:
    path = write_manifest(tmp_path, manifest_text())
    validate_manifest(load_manifest(path), expected_release_ref=RELEASE_REF)
    with pytest.raises(ManifestError, match="does not match"):
        validate_manifest(load_manifest(path), expected_release_ref="e" * 40)


def test_publish_workflow_publishes_manifest_after_build() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    build_index = workflow.index("- name: Build and push image")
    manifest_index = workflow.index("- name: Publish release manifest")
    assert manifest_index > build_index
    assert "id: build" in workflow[build_index:manifest_index]
    assert "steps.build.outputs.digest" in workflow[manifest_index:]
    assert "CHATGPT2API_RELEASE_REF=${GITHUB_SHA}" in workflow[manifest_index:]
    assert "gh release" in workflow[manifest_index:]
    assert "contents: write" in workflow


def test_publish_workflow_does_not_validate_a_prebuild_manifest_against_current_sha() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    build_index = workflow.index("- name: Build and push image")
    prebuild = workflow[:build_index]
    assert "scripts/validate_release_manifest.py" not in prebuild
    assert "git merge-base --is-ancestor" not in prebuild


def test_publish_workflow_passes_manifest_uv_version_to_docker_build() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    assert "id: release" in workflow
    assert "UV_VERSION=${{ steps.release.outputs.uv_version }}" in workflow
    assert '"uv==${{ steps.release.outputs.uv_version }}"' in workflow


def test_publish_workflow_normalizes_quoted_default_sidecar_images() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    assert "strip_optional_quotes()" in workflow
    assert "strip_optional_quotes \"$(sed -n 's/^DEFAULT_CHATGPT2API_WARP_IMAGE=//p'" in workflow
    assert "strip_optional_quotes \"$(sed -n 's/^DEFAULT_CHATGPT2API_PRIVOXY_IMAGE=//p'" in workflow
    assert "strip_optional_quotes \"$(sed -n 's/^DEFAULT_CHATGPT2API_FLARESOLVERR_IMAGE=//p'" in workflow


def test_publish_workflow_requires_a_full_image_digest() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    assert "^sha256:[0-9a-f]{64}$" in workflow
    assert 'test "${image_digest}" = sha256:*' not in workflow


def test_publish_workflow_ignores_a_manifest_from_another_commit() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    manifest_reader = workflow[workflow.index("manifest_value()") : workflow.index("strip_optional_quotes()")]
    assert "CHATGPT2API_RELEASE_REF" in manifest_reader
    assert '"${manifest_ref}" = "${GITHUB_SHA}"' in manifest_reader


def test_publish_workflow_requires_the_installer_pin_to_match_the_release_manifest() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    assert "- name: Validate installer release pin" in workflow
    assert "default_release_ref" in workflow
    assert "manifest_release_ref" in workflow
    assert "CHATGPT2API_RELEASE_REF" in workflow
    assert 'test "${default_release_ref}" = "${manifest_release_ref}"' in workflow



def test_publish_workflow_normalizes_quoted_installer_default_release_ref() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    validate_index = workflow.index("- name: Validate installer release pin")
    validate_step = workflow[validate_index: workflow.index("- name: Validate frontend regression scripts")]
    assert "strip_optional_quotes" in validate_step
    assert "DEFAULT_RELEASE_REF" in validate_step
    assert "CHATGPT2API_RELEASE_REF" in validate_step
    assert 'test "${default_release_ref}" = "${manifest_release_ref}"' in validate_step
