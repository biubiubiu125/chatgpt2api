from __future__ import annotations

import json

from services.image_queue.recovery import ImageRecovery
from services.image_queue.types import TaskStatus


def test_legacy_terminal_task_is_imported_and_unfinished_task_is_interrupted(
    repository,
    tmp_path,
) -> None:
    legacy_path = tmp_path / "image_tasks.json"
    payload = {
        "tasks": [
            {
                "id": "success-old",
                "owner_id": "owner-1",
                "status": "success",
                "mode": "generate",
                "model": "gpt-image-2",
                "n": 1,
                "size": "1024x1024",
                "quality": "high",
                "data": [{"url": "https://example.test/images/old.png", "width": 1024, "height": 1024}],
                "created_at": "2026-07-20T10:00:00+00:00",
                "updated_at": "2026-07-20T10:01:00+00:00",
            },
            {
                "id": "running-old",
                "owner_id": "owner-1",
                "status": "running",
                "mode": "edit",
                "model": "gpt-image-2",
                "n": 1,
                "created_at": "2026-07-20T11:00:00+00:00",
                "updated_at": "2026-07-20T11:01:00+00:00",
            },
        ]
    }
    legacy_path.write_text(json.dumps(payload), encoding="utf-8")
    original = legacy_path.read_bytes()
    recovery = ImageRecovery(repository)

    summary = recovery.import_legacy_tasks(legacy_path)

    assert summary.imported_terminal == 1
    assert summary.interrupted == 1
    imported = repository.get_task_by_client_id("owner-1", "success-old")
    assert imported is not None and imported.status == TaskStatus.SUCCESS
    assert imported.data[0]["width"] == 1024
    interrupted = repository.get_task_by_client_id("owner-1", "running-old")
    assert interrupted is not None and interrupted.status == TaskStatus.FAILED
    assert interrupted.error_code == "legacy_interrupted"
    assert legacy_path.read_bytes() == original

    repeated = recovery.import_legacy_tasks(legacy_path)
    assert repeated.skipped_file is True
    assert repeated.imported_terminal == 0
    assert repeated.interrupted == 0
