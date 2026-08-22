from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID

from PIL import Image, ImageOps

from services.image_queue.types import ArtifactDescriptor, ArtifactStatus
from services.image_url import build_public_image_url
from utils.image_tokens import VerifiedImage, verify_image_bytes


MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_ARTIFACT_PIXELS = 8192 * 8192
STAGE_DIRECTORIES = {"downloaded": "d", "upscaled": "u"}


class InvalidImageArtifact(ValueError):
    pass


def _normalize_png(payload: bytes) -> tuple[bytes, VerifiedImage]:
    if not payload:
        raise InvalidImageArtifact("image payload is empty")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise InvalidImageArtifact("image payload exceeds 50MB limit")
    try:
        verify_image_bytes(payload)
        with Image.open(BytesIO(payload)) as source:
            if source.width <= 0 or source.height <= 0 or source.width * source.height > MAX_ARTIFACT_PIXELS:
                raise InvalidImageArtifact("image pixel count exceeds limit")
            source.load()
            normalized = ImageOps.exif_transpose(source)
            has_alpha = "A" in normalized.getbands() or "transparency" in normalized.info
            normalized = normalized.convert("RGBA" if has_alpha else "RGB")
            output = BytesIO()
            normalized.save(output, format="PNG", optimize=True)
        png_bytes = output.getvalue()
        if len(png_bytes) > MAX_ARTIFACT_BYTES:
            raise InvalidImageArtifact("normalized image exceeds 50MB limit")
        verified = verify_image_bytes(png_bytes)
    except Exception as exc:
        if isinstance(exc, InvalidImageArtifact):
            raise
        raise InvalidImageArtifact("invalid image payload") from exc
    if verified.format_name != "PNG":
        raise InvalidImageArtifact("normalized artifact is not PNG")
    return png_bytes, verified


def _safe_target(root: Path, relative_path: str) -> Path:
    normalized = Path(str(relative_path or "").replace("\\", "/"))
    if normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
        raise InvalidImageArtifact("invalid artifact path")
    resolved_root = root.resolve()
    target = (resolved_root / normalized).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise InvalidImageArtifact("artifact path escapes storage root") from exc
    return target


def _native_filesystem_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if len(resolved) < 240:
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


def _read_path_bytes(path: Path) -> bytes:
    with open(_native_filesystem_path(path), "rb") as source:
        return source.read()


def _is_file(path: Path) -> bool:
    return os.path.isfile(_native_filesystem_path(path))


def _is_dir(path: Path) -> bool:
    return os.path.isdir(_native_filesystem_path(path))


def _stat_mtime_ns(path: Path) -> int:
    return os.stat(_native_filesystem_path(path)).st_mtime_ns


def _mtime_boundary_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def _atomic_write(root: Path, relative_path: str, payload: bytes) -> Path:
    target = _safe_target(root, relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_file(target) and _read_path_bytes(target) == payload:
        verify_image_bytes(payload)
        return target
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".artifact-",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_payload = _read_path_bytes(temporary_path)
        verify_image_bytes(temporary_payload)
        if sha256(temporary_payload).digest() != sha256(payload).digest():
            raise InvalidImageArtifact("temporary artifact checksum mismatch")
        os.replace(_native_filesystem_path(temporary_path), _native_filesystem_path(target))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(_native_filesystem_path(temporary_path))
            except FileNotFoundError:
                pass
    final_payload = _read_path_bytes(target)
    verify_image_bytes(final_payload)
    if not final_payload or sha256(final_payload).digest() != sha256(payload).digest():
        raise InvalidImageArtifact("final artifact checksum mismatch")
    return target


def _discard_local_path(root: Path, relative_path: str) -> None:
    target = _safe_target(root, relative_path)
    target.unlink(missing_ok=True)
    parent = target.parent
    resolved_root = root.resolve()
    while parent != resolved_root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _remote_reader(storage_service: Any | None) -> Any | None:
    if storage_service is None:
        return None
    reader = getattr(storage_service, "get_artifact_bytes", None)
    if not callable(reader):
        reader = getattr(storage_service, "get_published_bytes", None)
    if not callable(reader):
        reader = getattr(storage_service, "get_bytes", None)
    return reader if callable(reader) else None


def _verify_persisted_payload(
    payload: bytes,
    *,
    digest: str,
    expected_width: int,
    expected_height: int,
    label: str,
) -> None:
    try:
        verified = verify_image_bytes(payload)
    except Exception as exc:
        raise InvalidImageArtifact(f"{label} artifact is unreadable") from exc
    if sha256(payload).hexdigest() != digest:
        raise InvalidImageArtifact(f"{label} artifact checksum mismatch")
    if (verified.width, verified.height) != (expected_width, expected_height):
        raise InvalidImageArtifact(f"{label} artifact dimensions changed")


class ArtifactService:
    def __init__(self, root: Path, storage_service: Any | None = None) -> None:
        self.root = Path(root)
        self.storage_service = storage_service
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _public_url(relative_path: str, base_url: str) -> str:
        return build_public_image_url(base_url, relative_path)

    def _persist(
        self,
        *,
        task_id: UUID,
        job_id: UUID | None,
        kind: str,
        payload: bytes,
        base_url: str,
        source_url: str = "",
        ordinal: int | None = None,
    ) -> ArtifactDescriptor:
        png_bytes, verified = _normalize_png(payload)
        digest = sha256(png_bytes).hexdigest()
        if job_id is None:
            stable_ordinal = max(1, int(ordinal or 1))
            relative_path = f"{task_id}/{kind}/{stable_ordinal:04d}-{digest}.png"
        elif kind == "final":
            relative_path = f"{task_id}/{job_id}/{digest}.png"
        else:
            relative_path = f"{task_id}/{job_id}/{STAGE_DIRECTORIES.get(kind, kind)}/{digest}.png"
        target = _atomic_write(self.root, relative_path, png_bytes)
        publish_to_shared_storage = kind == "final"
        if kind in {"input", "mask"} and self.storage_service is not None:
            private_store = getattr(self.storage_service, "save_private_at_path", None)
            if callable(private_store):
                try:
                    stored = private_store(relative_path, png_bytes)
                    remote_reader = _remote_reader(self.storage_service)
                    if getattr(stored, "storage", "") == "private_webdav" and callable(remote_reader):
                        _verify_persisted_payload(
                            remote_reader(relative_path),
                            digest=digest,
                            expected_width=verified.width,
                            expected_height=verified.height,
                            label="remote",
                        )
                    return ArtifactDescriptor(
                        task_id=task_id,
                        job_id=job_id,
                        kind=kind,
                        status=ArtifactStatus.READY,
                        relative_path=relative_path,
                        sha256=digest,
                        mime_type="image/png",
                        byte_size=len(png_bytes),
                        width=verified.width,
                        height=verified.height,
                        public_url="",
                        storage_backend=stored.storage,
                        absolute_path=Path(_native_filesystem_path(target)),
                        source_url=source_url,
                        ordinal=max(1, int(ordinal or 1)) if job_id is None else None,
                    )
                except Exception:
                    raise
        if not publish_to_shared_storage:
            public_url = ""
            storage_backend = "private_local"
        elif self.storage_service is None:
            public_url = self._public_url(relative_path, base_url) if kind == "final" else ""
            storage_backend = "local" if kind == "final" else "private_local"
        else:
            try:
                stored = self.storage_service.save_at_path(relative_path, png_bytes, base_url)
                persisted = _read_path_bytes(target)
                _verify_persisted_payload(
                    persisted,
                    digest=digest,
                    expected_width=verified.width,
                    expected_height=verified.height,
                    label="stored",
                )
                remote_reader = _remote_reader(self.storage_service)
                if callable(remote_reader):
                    try:
                        remote_payload = remote_reader(relative_path)
                    except Exception as exc:
                        raise InvalidImageArtifact("remote artifact is unreadable") from exc
                    _verify_persisted_payload(
                        remote_payload,
                        digest=digest,
                        expected_width=verified.width,
                        expected_height=verified.height,
                        label="remote",
                    )
            except Exception:
                raise
            public_url = stored.url if kind == "final" else ""
            storage_backend = stored.storage
        return ArtifactDescriptor(
            task_id=task_id,
            job_id=job_id,
            kind=kind,
            status=ArtifactStatus.READY,
            relative_path=relative_path,
            sha256=digest,
            mime_type="image/png",
            byte_size=len(png_bytes),
            width=verified.width,
            height=verified.height,
            public_url=public_url,
            storage_backend=storage_backend,
            absolute_path=Path(_native_filesystem_path(target)),
            source_url=source_url,
            ordinal=max(1, int(ordinal or 1)) if job_id is None else None,
        )

    def discard(self, artifacts: list[ArtifactDescriptor] | tuple[ArtifactDescriptor, ...]) -> bool:
        root = self.root.resolve()
        deleted = True
        for artifact in artifacts:
            remote_delete = getattr(self.storage_service, "delete_artifact", None) if self.storage_service is not None else None
            if callable(remote_delete):
                try:
                    if remote_delete(artifact.relative_path) is False:
                        deleted = False
                except Exception:
                    deleted = False
                    continue
            try:
                _discard_local_path(root, artifact.relative_path)
            except (OSError, ValueError):
                deleted = False
        return deleted

    def persist_input(
        self,
        task_id: UUID,
        payload: bytes,
        filename: str,
        mime_type: str,
        *,
        kind: str = "input",
        source_url: str = "",
        ordinal: int = 1,
    ) -> ArtifactDescriptor:
        if kind not in {"input", "mask"}:
            raise ValueError("input artifact kind must be input or mask")
        return self._persist(
            task_id=task_id,
            job_id=None,
            kind=kind,
            payload=payload,
            base_url="",
            source_url=source_url or filename or mime_type,
            ordinal=ordinal,
        )

    def persist_final(
        self,
        task_id: UUID,
        job_id: UUID,
        payload: bytes,
        base_url: str,
        *,
        source_url: str = "",
    ) -> ArtifactDescriptor:
        return self._persist(
            task_id=task_id,
            job_id=job_id,
            kind="final",
            payload=payload,
            base_url=base_url,
            source_url=source_url,
        )

    def persist_stage(
        self,
        task_id: UUID,
        job_id: UUID,
        payload: bytes,
        kind: str,
        *,
        source_url: str = "",
    ) -> ArtifactDescriptor:
        if kind not in {"downloaded", "upscaled"}:
            raise ValueError("stage artifact kind must be downloaded or upscaled")
        return self._persist(
            task_id=task_id,
            job_id=job_id,
            kind=kind,
            payload=payload,
            base_url="",
            source_url=source_url,
        )

    def read(self, artifact: ArtifactDescriptor) -> bytes:
        path = _safe_target(self.root, artifact.relative_path)
        payload = _read_path_bytes(path)
        try:
            verified = verify_image_bytes(payload)
        except ValueError as exc:
            raise InvalidImageArtifact("artifact is unreadable") from exc
        if not payload or sha256(payload).hexdigest() != artifact.sha256:
            raise InvalidImageArtifact("artifact checksum mismatch")
        if (verified.width, verified.height) != (artifact.width, artifact.height):
            raise InvalidImageArtifact("artifact dimensions changed")
        return payload

    def recover_stage(
        self,
        task_id: UUID,
        job_id: UUID,
        kind: str,
        *,
        preferred_sha256: str = "",
        not_after: datetime | None = None,
    ) -> ArtifactDescriptor | None:
        if kind not in {"downloaded", "upscaled"}:
            raise ValueError("stage artifact kind must be downloaded or upscaled")
        directory = _safe_target(self.root, f"{task_id}/{job_id}/{STAGE_DIRECTORIES[kind]}")
        recovered = self._recover_single(
            directory,
            preferred_sha256=preferred_sha256,
            not_after=not_after,
        )
        if recovered is None:
            return None
        path, payload, verified, digest = recovered
        return ArtifactDescriptor(
            task_id=task_id,
            job_id=job_id,
            kind=kind,
            status=ArtifactStatus.READY,
            relative_path=path.relative_to(self.root.resolve()).as_posix(),
            sha256=digest,
            mime_type="image/png",
            byte_size=len(payload),
            width=verified.width,
            height=verified.height,
            absolute_path=Path(_native_filesystem_path(path)),
        )

    def recover_local_artifacts(
        self,
        task_id: UUID,
        job_id: UUID,
        *,
        not_after: datetime | None = None,
    ) -> tuple[ArtifactDescriptor, ...]:
        artifacts: list[ArtifactDescriptor] = []
        final_directory = _safe_target(self.root, f"{task_id}/{job_id}")
        recovered_final = self._recover_single(final_directory, not_after=not_after)
        if recovered_final is not None:
            path, payload, verified, digest = recovered_final
            artifacts.append(ArtifactDescriptor(
                task_id=task_id,
                job_id=job_id,
                kind="final",
                status=ArtifactStatus.READY,
                relative_path=path.relative_to(self.root.resolve()).as_posix(),
                sha256=digest,
                mime_type="image/png",
                byte_size=len(payload),
                width=verified.width,
                height=verified.height,
                storage_backend="private_local",
                absolute_path=Path(_native_filesystem_path(path)),
            ))
        for kind in ("upscaled", "downloaded"):
            recovered_stage = self.recover_stage(task_id, job_id, kind, not_after=not_after)
            if recovered_stage is not None:
                artifacts.append(recovered_stage)
        return tuple(artifacts)

    @staticmethod
    def _recover_single(
        directory: Path,
        *,
        preferred_sha256: str = "",
        not_after: datetime | None = None,
    ) -> tuple[Path, bytes, VerifiedImage, str] | None:
        if not _is_dir(directory):
            return None
        valid: list[tuple[Path, bytes, VerifiedImage, str, int]] = []
        mtime_limit_ns = _mtime_boundary_ns(not_after)
        paths = [directory / item.name for item in os.scandir(_native_filesystem_path(directory)) if item.name.endswith(".png")]
        for path in sorted(paths):
            try:
                mtime_ns = _stat_mtime_ns(path)
                if mtime_limit_ns is not None and mtime_ns > mtime_limit_ns:
                    continue
                payload = _read_path_bytes(path)
                verified = verify_image_bytes(payload)
                digest = sha256(payload).hexdigest()
                if verified.format_name != "PNG" or path.name != f"{digest}.png":
                    continue
                valid.append((path, payload, verified, digest, mtime_ns))
            except Exception:
                continue
        if not valid:
            return None
        preferred = str(preferred_sha256 or "").strip().lower()
        if preferred:
            for item in valid:
                if item[3] == preferred:
                    path, payload, verified, digest, _mtime = item
                    return path, payload, verified, digest
        selected = max(
            valid,
            key=lambda item: (item[4], item[0].name),
        )
        path, payload, verified, digest, _mtime = selected
        return path, payload, verified, digest

    def recover_final(
        self,
        task_id: UUID,
        job_id: UUID,
        base_url: str,
        *,
        source_url: str = "",
        preferred_sha256: str = "",
        not_after: datetime | None = None,
    ) -> ArtifactDescriptor | None:
        directory = _safe_target(self.root, f"{task_id}/{job_id}")
        recovered = self._recover_single(
            directory,
            preferred_sha256=preferred_sha256,
            not_after=not_after,
        )
        if recovered is None:
            return None
        path, payload, verified, digest = recovered
        relative_path = path.relative_to(self.root.resolve()).as_posix()
        if self.storage_service is None:
            public_url = self._public_url(relative_path, base_url)
            storage_backend = "local"
        else:
            stored = self.storage_service.save_at_path(relative_path, payload, base_url)
            remote_reader = getattr(self.storage_service, "get_published_bytes", None)
            if not callable(remote_reader):
                remote_reader = getattr(self.storage_service, "get_bytes", None)
            if callable(remote_reader):
                try:
                    remote_payload = remote_reader(relative_path)
                    remote_verified = verify_image_bytes(remote_payload)
                except Exception as exc:
                    raise InvalidImageArtifact("remote artifact is unreadable") from exc
                if sha256(remote_payload).hexdigest() != digest:
                    raise InvalidImageArtifact("remote artifact checksum mismatch")
                if (remote_verified.width, remote_verified.height) != (verified.width, verified.height):
                    raise InvalidImageArtifact("remote artifact dimensions changed")
            public_url = stored.url
            storage_backend = stored.storage
        return ArtifactDescriptor(
            task_id=task_id,
            job_id=job_id,
            kind="final",
            status=ArtifactStatus.READY,
            relative_path=relative_path,
            sha256=digest,
            mime_type="image/png",
            byte_size=len(payload),
            width=verified.width,
            height=verified.height,
            public_url=public_url,
            storage_backend=storage_backend,
            absolute_path=Path(_native_filesystem_path(path)),
            source_url=source_url,
        )
