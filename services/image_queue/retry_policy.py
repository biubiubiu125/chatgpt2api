from __future__ import annotations

from datetime import datetime, timedelta
import random

from services.image_failure import classify_image_exception
from services.image_queue.sanitization import safe_queue_error_message
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import JobStage, RetryDecision


class RetryPolicy:
    def __init__(self, settings: ImageQueueSettings, random_source: random.Random | None = None) -> None:
        self.settings = settings
        self.random = random_source or random.Random()

    def _budget(self, stage: JobStage) -> int:
        if stage in {JobStage.RESOLVING, JobStage.DOWNLOADING}:
            return self.settings.download_attempts
        if stage in {JobStage.TRANSFORMING, JobStage.SAVING}:
            return self.settings.save_attempts
        return self.settings.generation_attempts

    def decision(
        self,
        stage: JobStage | str,
        attempts: int,
        error: BaseException,
        now: datetime,
    ) -> RetryDecision:
        resolved_stage = stage if isinstance(stage, JobStage) else JobStage(str(stage))
        failure = classify_image_exception(error)
        used = max(1, int(attempts or 1))
        transient = (
            failure.retryable
            or failure.status_code in {408, 429, 500, 502, 503, 504}
            or failure.code == "auth_invalid"
        )
        if failure.code in {
            "content_policy_violation",
            "invalid_image_input",
            "upstream_text_reply",
            "no_image_generated",
            "unsupported_model",
        }:
            transient = False
        error_message = safe_queue_error_message(error, failure)
        if not transient or used >= self._budget(resolved_stage):
            return RetryDecision(False, error_code=failure.code, error_message=error_message)
        delay = min(300.0, 5.0 * (3 ** (used - 1))) + self.random.uniform(0.0, 1.0)
        return RetryDecision(
            True,
            next_retry_at=now + timedelta(seconds=delay),
            error_code=failure.code,
            error_message=error_message,
        )
