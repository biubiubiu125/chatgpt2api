from __future__ import annotations

from datetime import datetime, timezone
import random

import pytest

from services.image_failure import ImageFailureError, image_failure
from services.image_queue.retry_policy import RetryPolicy
from services.image_queue.settings import ImageQueueSettings
from utils.helper import UpstreamHTTPError


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_transient_upstream_errors_retry(status_code: int) -> None:
    policy = RetryPolicy(ImageQueueSettings(database_url="postgresql://test"), random.Random(1))
    error = UpstreamHTTPError("image generation", status_code, "upstream")
    decision = policy.decision("generating", 1, error, NOW)
    assert decision.retry is True
    assert decision.next_retry_at is not None and decision.next_retry_at > NOW


def test_text_without_image_is_terminal() -> None:
    policy = RetryPolicy(ImageQueueSettings(database_url="postgresql://test"))
    error = ImageFailureError("text", failure=image_failure("no_image_generated"))
    assert policy.decision("generating", 1, error, NOW).retry is False


def test_retry_error_message_redacts_urls_and_credentials() -> None:
    policy = RetryPolicy(ImageQueueSettings(database_url="postgresql://test"), random.Random(1))
    raw = (
        "download failed https://cdn.example/image.png?X-Amz-Signature=secret "
        "Authorization: Bearer top-secret access_token=token-value password=proxy-pass"
    )
    error = ImageFailureError(
        raw,
        failure=image_failure("invalid_image_input", raw_detail=raw),
    )

    decision = policy.decision("generating", 1, error, NOW)

    assert "secret" not in decision.error_message
    assert "token-value" not in decision.error_message
    assert "proxy-pass" not in decision.error_message
    assert "cdn.example" not in decision.error_message
