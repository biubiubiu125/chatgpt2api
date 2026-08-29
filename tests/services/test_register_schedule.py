from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
from threading import Event

import pytest

from services.image_queue.types import ResourceDecision, ResourceSnapshot
from services.register_service import RegisterService


@pytest.mark.parametrize(
    ("hour", "target", "threads", "name"),
    [
        (9, 100, 4, "peak"),
        (17, 100, 4, "peak"),
        (18, 30, 2, "offpeak"),
        (1, 30, 2, "offpeak"),
    ],
)
def test_registration_window_crosses_midnight(tmp_path, hour, target, threads, name) -> None:
    service = RegisterService(tmp_path / "register.json")

    window = service.resolve_registration_window(
        datetime(2026, 7, 28, hour, tzinfo=timezone(timedelta(hours=8)))
    )

    assert (window.name, window.target_available, window.threads) == (name, target, threads)


def test_registration_pauses_while_image_resources_are_blocked(tmp_path) -> None:
    class BlockedResources:
        generation_checks = 0
        registration_checks = 0

        def sample(self):
            return object()

        def allow_new_generation(self, snapshot):
            self.generation_checks += 1
            return ResourceDecision(True, "", 1)

        def allow_new_registration(self, snapshot):
            self.registration_checks += 1
            return ResourceDecision(False, "resource_cpu", 0)

    resources = BlockedResources()
    service = RegisterService(tmp_path / "register.json", resource_controller=resources)

    assert service.should_submit_registration() is False
    assert service.get()["stats"]["pause_reason"] == "resource_cpu"
    assert resources.registration_checks == 1
    assert resources.generation_checks == 0


@pytest.mark.parametrize(
    ("peak", "offpeak"),
    [
        ("18:00-03:00", "02:00-18:00"),
        ("18:00-01:00", "02:00-18:00"),
    ],
)
def test_registration_windows_must_cover_day_without_overlap(tmp_path, peak, offpeak) -> None:
    service = RegisterService(tmp_path / "register.json")

    with pytest.raises(ValueError, match="cover 24 hours without overlap"):
        service.update({
            "register_peak": {"time_range": peak},
            "register_offpeak": {"time_range": offpeak},
        })


def test_registration_uses_injected_worker_pool(tmp_path) -> None:
    service = RegisterService(tmp_path / "register.json")
    submitted = []

    def submit(operation):
        submitted.append(operation)
        future = Future()
        future.set_result({"ok": True})
        return future

    service.set_registration_submitter(submit)
    future = service._submit_registration(7, local_executor=None)

    assert len(submitted) == 1
    assert future.result() == {"ok": True}


def test_enabled_registration_waits_for_image_queue_integrations(tmp_path, monkeypatch) -> None:
    store_file = tmp_path / "register.json"
    store_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    started = Event()
    observed = {}

    def observe_run(self) -> None:
        observed["resource_controller"] = self.resource_controller
        observed["registration_submitter"] = self._registration_submitter
        started.set()

    monkeypatch.setattr(RegisterService, "_run", observe_run)

    service = RegisterService(store_file)

    assert started.wait(0.1) is False

    controller = object()
    submitter = lambda operation: operation
    service.set_resource_controller(controller)
    service.set_registration_submitter(submitter)
    service.resume_if_enabled()

    assert started.wait(1) is True
    assert observed == {
        "resource_controller": controller,
        "registration_submitter": submitter,
    }


def test_runtime_shutdown_preserves_enabled_registration_for_next_start(tmp_path, monkeypatch) -> None:
    service = RegisterService(tmp_path / "register.json")
    service.set_resource_controller(object())
    service.set_registration_submitter(lambda operation: operation)
    monkeypatch.setattr(service, "_target_reached", lambda cfg, submitted: True)
    service.update({"enabled": True, "auto_schedule_enabled": True, "check_interval": 1})

    service.start()
    service.shutdown(timeout=2)

    assert service.get()["enabled"] is True
    restored = RegisterService(tmp_path / "register.json")
    assert restored.get()["enabled"] is True


def test_registration_normalizes_enabled_disabled_words(tmp_path) -> None:
    service = RegisterService(tmp_path / "register.json")

    service.update({
        "enabled": "enabled",
        "auto_schedule_enabled": "disabled",
        "proxy_required": "enabled",
    })

    snapshot = service.get()
    assert snapshot["enabled"] is True
    assert snapshot["auto_schedule_enabled"] is False
    assert snapshot["proxy_required"] is True



def test_registration_service_logs_core_result_when_recovery_file_unavailable(tmp_path, monkeypatch) -> None:
    service = RegisterService(tmp_path / "register.json")

    class AllowResources:
        def sample(self):
            return object()

        def allow_new_registration(self, snapshot):
            return ResourceDecision(True, "", 1)

    service.set_resource_controller(AllowResources())

    def submit(operation):
        future = Future()
        future.set_result({
            "ok": False,
            "core_ok": True,
            "error": "注册核心已完成，后续处理失败: 后续验活失败: verify failed；核心结果暂存失败: disk full",
            "result": {
                "email": "raw-user@example.com",
                "password": "",
                "access_token": "at-token",
                "refresh_token": "rt-token",
                "id_token": "id-token",
                "source_type": "web",
            },
        })
        return future

    service.set_registration_submitter(submit)
    service.update({
        "auto_schedule_enabled": False,
        "mode": "total",
        "total": 1,
        "threads": 1,
        "check_interval": 1,
    })

    service.start()
    for _ in range(50):
        snapshot = service.get()
        if snapshot["stats"].get("done") == 1 and snapshot["state"] == "idle":
            break
        Event().wait(0.02)
    service.shutdown(timeout=2)

    logs = "\n".join(str(item.get("text") or "") for item in service.get().get("logs", []))
    assert "注册核心结果未入库" in logs
    assert "raw-user@example.com" in logs
    assert "at-token" not in logs
    assert "rt-token" not in logs
    assert "id-token" not in logs
