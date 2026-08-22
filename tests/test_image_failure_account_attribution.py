from services.image_failure import image_failure


def test_local_delivery_failure_does_not_penalize_account() -> None:
    failure = image_failure("image_queue_storage_full")

    assert failure.verify_account is False
    assert failure.switch_account is False


def test_upstream_transport_failure_still_triggers_account_verification() -> None:
    failure = image_failure("upstream_connection_timeout")

    assert failure.verify_account is True
    assert failure.switch_account is True
