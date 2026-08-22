from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from services.image_queue.repository import ImageQueueRepository
from services.image_queue.types import JobStage, LegacyImportSummary, RecoverySummary, TaskStatus


class ImageRecovery:
    def __init__(self, repository: ImageQueueRepository) -> None:
        self.repository = repository

    def recover(self, now: datetime | None = None) -> RecoverySummary:
        reclaimed = self.repository.reclaim_expired_leases(now)
        resumed_downloads = 0
        resumed_generation = 0
        for job in self.repository.list_recoverable_jobs():
            if job.stage == JobStage.SAVING:
                stage = JobStage.SAVING
                resumed_downloads += 1
            elif job.image_urls or job.conversation_id or job.file_ids or job.sediment_ids:
                stage = JobStage.DOWNLOADING if job.image_urls else JobStage.RESOLVING
                resumed_downloads += 1
            else:
                stage = JobStage.GENERATING
                resumed_generation += 1
            self.repository.requeue_job_for_recovery(job.id, stage, now)
        requeued = resumed_downloads + resumed_generation
        return RecoverySummary(
            reclaimed=reclaimed,
            requeued=requeued,
            resumed_downloads=resumed_downloads,
            resumed_generation=resumed_generation,
        )

    @staticmethod
    def _datetime(value: object) -> datetime:
        text = str(value or "").strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _result_data(value: object) -> list[dict[str, Any]]:
        allowed = {"url", "path", "b64_json", "revised_prompt", "width", "height"}
        if not isinstance(value, list):
            return []
        return [
            {key: item[key] for key in allowed if key in item}
            for item in value
            if isinstance(item, dict)
        ]

    def import_legacy_tasks(self, path: str | Path) -> LegacyImportSummary:
        source = Path(path)
        if not source.is_file():
            return LegacyImportSummary()
        try:
            payload = source.read_bytes()
        except OSError as exc:
            return LegacyImportSummary(error=str(exc))
        file_sha256 = sha256(payload).hexdigest()
        if self.repository.legacy_import_exists(file_sha256):
            return LegacyImportSummary(file_sha256=file_sha256, skipped_file=True)
        imported_terminal = 0
        interrupted = 0
        ignored = 0
        try:
            decoded = json.loads(payload.decode("utf-8-sig"))
            items = decoded.get("tasks") if isinstance(decoded, dict) else decoded
            if not isinstance(items, list):
                raise ValueError("legacy image task file must contain a task list")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            summary = LegacyImportSummary(file_sha256=file_sha256, ignored=1, error=str(exc))
            self.repository.record_legacy_import(file_sha256, str(source), asdict(summary))
            return summary

        for item in items:
            if not isinstance(item, dict):
                ignored += 1
                continue
            client_task_id = str(item.get("id") or "").strip()
            owner_key = str(item.get("owner_id") or "").strip()
            legacy_status = str(item.get("status") or "").strip().lower()
            if not client_task_id or not owner_key or legacy_status not in {"queued", "running", "success", "error"}:
                ignored += 1
                continue
            is_interrupted = legacy_status in {"queued", "running"}
            status = TaskStatus.SUCCESS if legacy_status == "success" else TaskStatus.FAILED
            error_code = "legacy_interrupted" if is_interrupted else str(item.get("error_code") or "legacy_failed")
            error_message = (
                "legacy image task was interrupted before durable checkpoints were available"
                if is_interrupted
                else str(item.get("error") or "legacy image task failed")
            )
            result_data = self._result_data(item.get("data"))
            try:
                requested_jobs = int(item.get("n") or len(result_data) or 1)
            except (TypeError, ValueError):
                requested_jobs = len(result_data) or 1
            required_jobs = max(1, min(4, requested_jobs))
            normalized = {
                "owner_key": owner_key,
                "client_task_id": client_task_id,
                "status": status.value,
                "task_type": "edit" if item.get("mode") == "edit" else "generation",
                "public_model": str(item.get("model") or "gpt-image-2"),
                "required_jobs": required_jobs,
                "request_hash": sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
                "request_payload": {
                    "legacy_import": True,
                    "size": str(item.get("size") or ""),
                    "quality": str(item.get("quality") or "auto"),
                },
                "result_data": result_data,
                "error_code": None if status == TaskStatus.SUCCESS else error_code,
                "error_message": None if status == TaskStatus.SUCCESS else error_message,
                "created_at": self._datetime(item.get("created_at")),
                "updated_at": self._datetime(item.get("updated_at") or item.get("created_at")),
            }
            created = self.repository.import_legacy_record(normalized, file_sha256)
            if created:
                if is_interrupted:
                    interrupted += 1
                else:
                    imported_terminal += 1

        summary = LegacyImportSummary(
            file_sha256=file_sha256,
            imported_terminal=imported_terminal,
            interrupted=interrupted,
            ignored=ignored,
        )
        self.repository.record_legacy_import(file_sha256, str(source), asdict(summary))
        return summary
