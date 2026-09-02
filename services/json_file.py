from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def read_json_file(
    path: Path,
    *,
    name: str | None = None,
    default_factory: Callable[[], Any] = dict,
    expected_types: type | tuple[type, ...] | None = None,
) -> Any:
    label = name or path.name
    for candidate, is_backup in ((path, False), (_backup_path(path), True)):
        if not candidate.exists():
            continue
        if candidate.is_dir():
            print(
                f"Warning: {label} at '{candidate}' is a directory; ignoring it.",
                file=sys.stderr,
            )
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if expected_types is not None and not isinstance(data, expected_types):
            continue
        if is_backup:
            print(
                f"Warning: {label} at '{path}' is unreadable; recovered from backup '{candidate.name}'.",
                file=sys.stderr,
            )
        return data
    return default_factory()


def read_json_object(path: Path, *, name: str | None = None) -> dict[str, Any]:
    return read_json_file(path, name=name, default_factory=dict, expected_types=dict)


def _write_text_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        # rename() alone is atomic for readers but says nothing about durability:
        # a crash can leave the directory entry pointing at unflushed data, which
        # surfaces as an empty or truncated file. fsync the payload before the
        # rename, then fsync the directory so the entry itself survives.
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _fsync_directory(directory: Path) -> None:
    # Windows cannot open a directory handle for fsync; on POSIX a missing
    # directory fsync is the difference between a durable rename and a lost one.
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_json_file(path: Path, data: Any, *, backup: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"'{path}' is a directory; expected a JSON file")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _write_text_atomic(path, content)

    if not backup:
        return
    backup_target = _backup_path(path)
    try:
        _write_text_atomic(backup_target, content)
    except OSError as exc:
        print(f"Warning: failed to update backup for '{path}': {exc}", file=sys.stderr)
