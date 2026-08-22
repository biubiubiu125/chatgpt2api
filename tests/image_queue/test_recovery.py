from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.image_queue.recovery import ImageRecovery
from services.image_queue.types import EnqueueRequest, ImageAccountCandidate, JobCheckpoint, JobStage, JobStatus


NOW = datetime.now(timezone.utc) + timedelta(minutes=1)


def test_recovery_resumes_download_when_remote_urls_exist(
    repository,
    generation_request: EnqueueRequest,
    account_candidates: list[ImageAccountCandidate],
) -> None:
    repository.enqueue_task(generation_request)
    claim = repository.claim_next_job("old-worker", account_candidates[:1], 1, NOW)
    assert claim is not None
    repository.checkpoint_job(claim, JobCheckpoint(
        stage=JobStage.DOWNLOADING,
        conversation_id="conversation-1",
        image_urls=["https://upstream.example/image.png"],
    ))

    summary = ImageRecovery(repository).recover(NOW + timedelta(seconds=91))
    restored = repository.get_job(claim.job.id)

    assert summary.reclaimed == 1
    assert summary.requeued == 1
    assert restored is not None
    assert restored.status == JobStatus.QUEUED
    assert restored.stage == JobStage.DOWNLOADING
