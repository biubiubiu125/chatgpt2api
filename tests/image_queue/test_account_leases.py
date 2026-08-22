from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

import pytest

from services.account_service import AccountService
from services.image_failure import image_failure
from services.image_queue.types import ImageAccountCandidate
from services.storage.base import StorageBackend


class MemoryStorage(StorageBackend):
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = deepcopy(accounts)
        self.saved: list[list[dict[str, Any]]] = []
        self.fail_next_account_save = False

    def load_accounts(self) -> list[dict[str, Any]]:
        return deepcopy(self.accounts)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        if self.fail_next_account_save:
            self.fail_next_account_save = False
            raise RuntimeError("simulated account storage failure")
        self.accounts = deepcopy(accounts)
        self.saved.append(deepcopy(accounts))

    def load_auth_keys(self) -> list[dict[str, Any]]:
        return []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        pass

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


@pytest.fixture
def stored_account() -> dict[str, Any]:
    return {
        "access_token": "old-token",
        "refresh_token": "refresh-token",
        "status": "正常",
        "type": "Plus",
        "source_type": "web",
        "quota": 5,
        "image_quota_unknown": False,
    }


def test_missing_account_id_is_generated_and_persisted(stored_account: dict[str, Any]) -> None:
    storage = MemoryStorage([stored_account])

    service = AccountService(storage)
    account = service.get_account("old-token")

    assert account is not None
    assert UUID(str(account["queue_account_id"]))
    assert storage.saved[-1][0]["queue_account_id"] == account["queue_account_id"]


def test_account_id_survives_access_token_rotation(stored_account: dict[str, Any]) -> None:
    service = AccountService(MemoryStorage([stored_account]))
    before = service.get_account("old-token")["queue_account_id"]

    service._apply_refreshed_tokens(
        "old-token",
        {"access_token": "rotated-token", "refresh_token": "refresh-token"},
        "test",
        expected_access_token="old-token",
        expected_refresh_token="refresh-token",
    )

    assert service.get_account("rotated-token")["queue_account_id"] == before


def test_candidate_listing_does_not_increment_in_memory_slots(stored_account: dict[str, Any]) -> None:
    service = AccountService(MemoryStorage([stored_account]))

    candidates = service.list_image_account_candidates()

    assert candidates and isinstance(candidates[0], ImageAccountCandidate)
    assert service._image_inflight == {}


def test_get_account_by_id_returns_copy(stored_account: dict[str, Any]) -> None:
    service = AccountService(MemoryStorage([stored_account]))
    account = service.get_account("old-token")

    found = service.get_account_by_id(UUID(str(account["queue_account_id"])))

    assert found == account
    assert found is not account


def test_prepare_image_account_returns_preflight_token(
    stored_account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AccountService(MemoryStorage([stored_account]))
    account_id = UUID(str(service.get_account("old-token")["queue_account_id"]))
    monkeypatch.setattr(
        service,
        "fetch_remote_info",
        lambda token, event, image_scope=False: {"access_token": "rotated-token", "status": "正常"},
    )

    assert service.prepare_image_account(account_id) == "rotated-token"


def test_managed_result_does_not_release_legacy_slot(
    stored_account: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = AccountService(MemoryStorage([stored_account]))
    account_id = UUID(str(service.get_account("old-token")["queue_account_id"]))
    service._image_inflight["old-token"] = 2
    monkeypatch.setattr(service, "_schedule_account_refresh_after_image_failure", lambda *args, **kwargs: True)

    service.record_managed_image_result(
        account_id,
        success=False,
        failure=image_failure("upstream_unavailable"),
        error="temporary upstream failure",
        quota_consumed=False,
    )

    assert service._image_inflight["old-token"] == 2
    assert service.get_account("old-token")["fail"] == 1


def test_managed_quota_accounting_key_is_idempotent_across_restart(
    stored_account: dict[str, Any],
) -> None:
    stored_account["limits_progress"] = [{
        "feature_name": "image_gen",
        "remaining": 5,
    }]
    storage = MemoryStorage([stored_account])
    service = AccountService(storage)
    account_id = UUID(str(service.get_account("old-token")["queue_account_id"]))

    first = service.record_managed_image_result(
        account_id,
        success=True,
        quota_consumed=True,
        idempotency_key="job-quota-1",
    )
    second = service.record_managed_image_result(
        account_id,
        success=True,
        quota_consumed=True,
        idempotency_key="job-quota-1",
    )
    restarted = AccountService(storage)
    third = restarted.record_managed_image_result(
        account_id,
        success=True,
        quota_consumed=True,
        idempotency_key="job-quota-1",
    )

    assert first is not None and second is not None and third is not None
    assert third["quota"] == 4
    assert third["success"] == 1


def test_failed_quota_save_rolls_back_memory_and_can_retry(
    stored_account: dict[str, Any],
) -> None:
    stored_account["limits_progress"] = [{
        "feature_name": "image_gen",
        "remaining": 5,
    }]
    storage = MemoryStorage([stored_account])
    service = AccountService(storage)
    account_id = UUID(str(service.get_account("old-token")["queue_account_id"]))
    storage.fail_next_account_save = True

    with pytest.raises(RuntimeError, match="simulated account storage failure"):
        service.record_managed_image_result(
            account_id,
            success=True,
            quota_consumed=True,
            idempotency_key="job-quota-retry",
        )

    after_failure = service.get_account("old-token")
    assert after_failure is not None
    assert after_failure["quota"] == 5
    assert after_failure.get("success", 0) == 0
    assert "job-quota-retry" not in after_failure.get("_image_quota_accounting_keys", [])

    retried = service.record_managed_image_result(
        account_id,
        success=True,
        quota_consumed=True,
        idempotency_key="job-quota-retry",
    )
    restarted = AccountService(storage)
    persisted = restarted.get_account_by_id(account_id)

    assert retried is not None and persisted is not None
    assert persisted["quota"] == 4
    assert persisted["success"] == 1
    assert "job-quota-retry" in persisted["_image_quota_accounting_keys"]
