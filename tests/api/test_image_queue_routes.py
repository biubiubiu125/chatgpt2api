from __future__ import annotations

import base64
import json
from io import BytesIO
from threading import Event
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from api import app as app_module
from api import ai as ai_module
from api import image_tasks as image_tasks_module
from services.image_queue.database import ImageQueueUnavailableError
from services.image_queue.repository import IdempotencyConflict
from services.image_queue.types import ImageAccountCandidate, ResourceDecision
from services.log_service import LoggedCall, _image_error_response
from services.image_queue.idempotency import require_public_image_model
from services.quota_service import QuotaService


def _tiny_png_base64() -> str:
    output = BytesIO()
    Image.new("RGB", (2, 2), (20, 30, 40)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class _RecordingQuota:
    def __init__(self, events: list) -> None:
        self.events = events
        self.committed = False

    def commit(self) -> None:
        self.committed = True
        self.events.append("commit")

    def cancel(self) -> None:
        self.events.append("cancel")


def _patch_native_task_logging(monkeypatch, events: list, quota: _RecordingQuota | None = None) -> None:
    class FakeCall:
        def __init__(self, *args, **kwargs):
            self.call_id = "call-native-task-1"

        def log(self, suffix, result=None, status="success", **kwargs):
            events.append(("log", suffix, status, bool(quota and quota.committed), result, kwargs))

    monkeypatch.setattr(image_tasks_module, "LoggedCall", FakeCall)


def test_generation_route_enqueues_with_idempotency_and_trace_headers(
    image_queue_client,
    api_image_task_service,
) -> None:
    response = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "request-1",
            "X-NewAPI-Request-Id": "newapi-trace-1",
        },
        json={
            "client_task_id": "client-1",
            "prompt": "cat",
            "model": "gpt-image-2",
            "n": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["required_jobs"] == 2
    saved = api_image_task_service.repository.get_execution_request(body["task_id"])
    assert api_image_task_service.repository.get_task_by_idempotency_key("owner-1", "request-1") is not None
    assert saved["request_payload"]["trace_headers"]["x-newapi-request-id"] == "newapi-trace-1"
    assert saved["request_payload"]["trace_headers"]["call_id"]


def test_native_generation_task_logs_queued_after_quota_commit(
    image_queue_client,
    monkeypatch,
) -> None:
    events = []
    quota = _RecordingQuota(events)
    _patch_native_task_logging(monkeypatch, events, quota)
    monkeypatch.setattr(image_tasks_module, "reserve_quota", lambda *args, **kwargs: quota)

    response = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "native-log-1"},
        json={"client_task_id": "native-log-1", "prompt": "cat", "model": "gpt-image-2"},
    )

    assert response.status_code == 200
    assert events[0] == "commit"
    assert events[1][0:4] == ("log", "已入队", "queued", True)
    assert events[1][4]["task_id"] == response.json()["task_id"]


def test_native_edit_task_logs_queued_after_quota_commit(
    image_queue_client,
    monkeypatch,
) -> None:
    events = []
    quota = _RecordingQuota(events)
    _patch_native_task_logging(monkeypatch, events, quota)
    monkeypatch.setattr(image_tasks_module, "reserve_quota", lambda *args, **kwargs: quota)

    response = image_queue_client.post(
        "/api/image-tasks/edits",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "native-edit-log-1"},
        json={
            "client_task_id": "native-edit-log-1",
            "prompt": "make it warmer",
            "model": "gpt-image-2",
            "images": [{"b64_json": _tiny_png_base64(), "filename": "source.png", "mime_type": "image/png"}],
        },
    )

    assert response.status_code == 200
    assert events[0] == "commit"
    assert events[1][0:4] == ("log", "已入队", "queued", True)
    assert events[1][4]["task_id"] == response.json()["task_id"]


def test_native_generation_task_logs_idempotency_conflict_failure(
    image_queue_client,
    monkeypatch,
) -> None:
    events = []
    _patch_native_task_logging(monkeypatch, events)
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "native-conflict-1"}

    first = image_queue_client.post(
        "/api/image-tasks/generations",
        headers=headers,
        json={"client_task_id": "native-conflict-a", "prompt": "cat", "model": "gpt-image-2"},
    )
    second = image_queue_client.post(
        "/api/image-tasks/generations",
        headers=headers,
        json={"client_task_id": "native-conflict-b", "prompt": "dog", "model": "gpt-image-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    failures = [event for event in events if event[0:3] == ("log", "调用失败", "failed")]
    assert failures
    assert failures[-1][5]["error"] == "idempotency_conflict"


def test_generation_route_quota_dedupes_same_client_task_with_new_header(
    image_queue_client,
    api_image_task_service,
    monkeypatch,
    tmp_path,
) -> None:
    quota_service = QuotaService(
        tmp_path / "quota_usage.json",
        settings_provider=lambda: {"enabled": True, "image_daily_limit": 5},
        today_provider=lambda: "2026-08-27",
    )

    def reserve(identity, endpoint, model, *, image_request=False, idempotency_key="", idempotency_aliases=()):
        return quota_service.reserve(
            identity,
            "image",
            idempotency_key=idempotency_key,
            idempotency_aliases=idempotency_aliases,
        )

    monkeypatch.setattr("api.image_tasks.reserve_quota", reserve)

    first = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "header-key-1"},
        json={"client_task_id": "same-client-task", "prompt": "cat", "model": "gpt-image-2"},
    )
    second = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "header-key-2"},
        json={"client_task_id": "same-client-task", "prompt": "cat", "model": "gpt-image-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    assert len(api_image_task_service.repository.list_tasks("owner-1")) == 1
    persisted = json.loads(quota_service.path.read_text(encoding="utf-8"))
    assert persisted["usage"]["owner-1"]["image"] == 1


def test_generation_route_returns_conflict_for_reused_key_with_different_request(
    image_queue_client,
) -> None:
    headers = {
        "Authorization": "Bearer test",
        "Idempotency-Key": "request-conflict",
    }
    first = image_queue_client.post(
        "/api/image-tasks/generations",
        headers=headers,
        json={"client_task_id": "client-first", "prompt": "cat", "model": "gpt-image-2"},
    )
    second = image_queue_client.post(
        "/api/image-tasks/generations",
        headers=headers,
        json={"client_task_id": "client-second", "prompt": "dog", "model": "gpt-image-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "idempotency_conflict"


def test_generation_submission_rejects_disk_pressure_before_enqueue(
    image_queue_client,
    api_image_task_service,
    monkeypatch,
) -> None:
    blocked = SimpleNamespace(
        sample=lambda: object(),
        allow_new_submission=lambda snapshot: ResourceDecision(False, "resource_disk", 0),
    )
    monkeypatch.setattr(api_image_task_service.worker, "resource_controller", blocked, raising=False)

    response = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test"},
        json={"client_task_id": "disk-full", "prompt": "cat", "model": "gpt-image-2"},
    )

    assert response.status_code == 507
    assert response.json()["detail"]["error"] == "image_queue_storage_full"
    assert api_image_task_service.repository.list_tasks("owner-1") == []


def test_openai_image_error_maps_idempotency_conflict_to_http_409() -> None:
    response = _image_error_response(IdempotencyConflict(
        "idempotency key was already used with a different request"
    ))

    assert response.status_code == 409


def test_unsupported_public_image_model_maps_to_http_400() -> None:
    try:
        require_public_image_model("dall-e-3")
    except ValueError as exc:
        response = _image_error_response(exc)
    else:
        raise AssertionError("unsupported model was accepted")

    assert response.status_code == 400
    assert response.body and b"unsupported_model" in response.body


@pytest.mark.anyio
async def test_openai_image_queue_unavailable_maps_to_http_503() -> None:
    call = LoggedCall(
        {"role": "api", "name": "review"},
        "/v1/images/generations",
        "gpt-image-2",
        "review",
        image_request=True,
    )

    async def unavailable() -> None:
        raise ImageQueueUnavailableError("postgres is unavailable")

    response = await call.run(
        lambda body: {"ok": True},
        {"model": "gpt-image-2"},
        async_before=unavailable,
    )

    payload = json.loads(response.body)
    assert response.status_code == 503
    assert payload["error"]["code"] == "image_queue_unavailable"


def test_get_and_cancel_routes_are_idempotent_and_canceled_task_cannot_be_acked(image_queue_client) -> None:
    created = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test"},
        json={"client_task_id": "client-1", "prompt": "cat", "model": "gpt-image-2"},
    ).json()
    task_id = created["task_id"]

    fetched = image_queue_client.get(
        f"/api/image-tasks/{task_id}",
        headers={"Authorization": "Bearer test"},
    )
    first_cancel = image_queue_client.post(
        f"/api/image-tasks/{task_id}/cancel",
        headers={"Authorization": "Bearer test"},
    )
    second_cancel = image_queue_client.post(
        f"/api/image-tasks/{task_id}/cancel",
        headers={"Authorization": "Bearer test"},
    )
    acknowledged = image_queue_client.post(
        f"/api/image-tasks/{task_id}/ack",
        headers={"Authorization": "Bearer test"},
    )

    assert fetched.status_code == 200
    assert first_cancel.json()["status"] == second_cancel.json()["status"] == "canceled"
    assert acknowledged.status_code == 409
    assert acknowledged.json()["detail"]["error"] == "task_state_conflict"


def test_ack_route_rejects_success_task_after_gallery_artifact_is_deleted(
    image_queue_client,
    api_image_task_service,
) -> None:
    created = image_queue_client.post(
        "/api/image-tasks/generations",
        headers={"Authorization": "Bearer test"},
        json={"client_task_id": "ack-after-delete", "prompt": "cat", "model": "gpt-image-2"},
    ).json()
    task_id = created["task_id"]
    repository = api_image_task_service.repository
    claim = repository.claim_next_job(
        "worker-1",
        [ImageAccountCandidate(
            account_id=UUID("10000000-0000-0000-0000-000000000001"),
            access_token="token",
        )],
        1,
    )
    assert claim is not None
    payload = _tiny_png_base64()
    artifact = api_image_task_service.artifact_service.persist_final(
        claim.job.task_id,
        claim.job.id,
        base64.b64decode(payload),
        "https://api.example",
    )
    repository.complete_job(
        claim,
        artifact,
        {"url": artifact.public_url, "width": artifact.width, "height": artifact.height},
    )

    assert api_image_task_service.delete_public_final_artifact(artifact.relative_path) is True

    acknowledged = image_queue_client.post(
        f"/api/image-tasks/{task_id}/ack",
        headers={"Authorization": "Bearer test"},
    )

    assert acknowledged.status_code == 409
    assert acknowledged.json()["detail"]["error"] == "task_state_conflict"


def test_application_lifespan_starts_and_stops_durable_queue(monkeypatch) -> None:
    order = []

    class JoinedThread:
        def join(self, timeout=None):
            return None

    monkeypatch.setattr(app_module, "_configure_threadpool", lambda: None)
    monkeypatch.setattr(app_module.account_service, "cleanup_auto_remove_accounts", lambda: None)
    monkeypatch.setattr(app_module, "start_limited_account_watcher", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_image_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_log_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module.backup_service, "start", lambda: order.append("backup_start"))
    monkeypatch.setattr(app_module.backup_service, "stop", lambda: order.append("backup_stop"))
    monkeypatch.setattr(app_module.register_service, "shutdown", lambda timeout=None: order.append("register_shutdown"))
    monkeypatch.setattr(app_module.register_service, "stop", lambda: order.append("register_stop"))
    monkeypatch.setattr(app_module.config, "cleanup_old_images", lambda: None)
    monkeypatch.setattr(app_module, "cleanup_old_logs", lambda: None)
    monkeypatch.setattr(app_module.dashboard_metrics_service, "flush", lambda: None)
    monkeypatch.setattr(
        app_module,
        "image_task_service",
        SimpleNamespace(
            start=lambda: order.append("queue_start"),
            stop=lambda timeout=None: order.append("queue_stop"),
        ),
        raising=False,
    )

    with TestClient(app_module.create_app()):
        assert order[:2] == ["queue_start", "backup_start"]

    assert "queue_stop" in order
    assert "register_shutdown" in order
    assert "register_stop" not in order


def test_application_lifespan_keeps_text_api_available_when_queue_database_is_unavailable(monkeypatch) -> None:
    from services.image_queue.database import ImageQueueUnavailableError

    class JoinedThread:
        def join(self, timeout=None):
            return None

    monkeypatch.setattr(app_module, "_configure_threadpool", lambda: None)
    monkeypatch.setattr(app_module.account_service, "cleanup_auto_remove_accounts", lambda: None)
    monkeypatch.setattr(app_module, "start_limited_account_watcher", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_image_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_log_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module.backup_service, "start", lambda: None)
    monkeypatch.setattr(app_module.backup_service, "stop", lambda: None)
    monkeypatch.setattr(app_module.config, "cleanup_old_images", lambda: None)
    monkeypatch.setattr(app_module, "cleanup_old_logs", lambda: None)
    monkeypatch.setattr(app_module.dashboard_metrics_service, "flush", lambda: None)

    def unavailable():
        raise ImageQueueUnavailableError("postgres is unavailable")

    monkeypatch.setattr(
        app_module,
        "image_task_service",
        SimpleNamespace(start=unavailable, stop=lambda timeout=None: None, repository=None, worker=None),
        raising=False,
    )

    with TestClient(app_module.create_app()) as client:
        response = client.get("/definitely-not-an-api-route")

    assert response.status_code == 404


def test_application_lifespan_reconnects_image_queue_after_database_recovers(monkeypatch) -> None:
    from services.image_queue.database import ImageQueueUnavailableError

    attempts = 0
    reconnected = Event()
    configured = []

    class JoinedThread:
        def join(self, timeout=None):
            return None

    repository = SimpleNamespace(
        queue_snapshot=lambda: {},
        logical_backup=lambda: {},
        restore_logical_backup=lambda payload: {},
        protected_artifact_paths=lambda: set(),
    )
    worker = SimpleNamespace(resource_controller=object(), submit_registration=lambda callback: None)

    def start_queue():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ImageQueueUnavailableError("postgres is unavailable")
        reconnected.set()

    queue = SimpleNamespace(
        start=start_queue,
        stop=lambda timeout=None: None,
        repository=repository,
        artifact_service=SimpleNamespace(root=None),
        worker=worker,
    )
    monkeypatch.setenv("IMAGE_QUEUE_STARTUP_RETRY_SECONDS", "0.05")
    monkeypatch.setattr(app_module, "_configure_threadpool", lambda: None)
    monkeypatch.setattr(app_module.account_service, "cleanup_auto_remove_accounts", lambda: None)
    monkeypatch.setattr(app_module, "start_limited_account_watcher", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_image_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_log_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module.backup_service, "start", lambda: None)
    monkeypatch.setattr(app_module.backup_service, "stop", lambda: None)
    monkeypatch.setattr(
        app_module.backup_service,
        "set_image_queue_provider",
        lambda value: configured.append("backup"),
    )
    monkeypatch.setattr(app_module.config, "cleanup_old_images", lambda: None)
    monkeypatch.setattr(app_module, "cleanup_old_logs", lambda: None)
    monkeypatch.setattr(app_module.dashboard_metrics_service, "flush", lambda: None)
    monkeypatch.setattr(app_module, "image_task_service", queue, raising=False)

    with TestClient(app_module.create_app()):
        assert reconnected.wait(2)

    assert attempts >= 2
    assert "backup" in configured


def test_application_lifespan_starts_resource_linked_threadpool_governor(monkeypatch) -> None:
    order: list[str] = []
    captured: dict[str, object] = {}

    class JoinedThread:
        def join(self, timeout=None):
            order.append("governor_stop")
            return None

    class FakeGovernor:
        def start(self):
            order.append("governor_start")
            return None

        def stop(self, timeout=None):
            return JoinedThread().join(timeout=timeout)

    class FakeController:
        def sample(self):
            return SimpleNamespace()

        def recommend_thread_tokens(self, snapshot, *, ceiling, current_tokens):
            return 40

    monkeypatch.setattr(app_module, "_configure_threadpool", lambda: None)
    monkeypatch.setattr(app_module, "_build_threadpool_governor", lambda **kwargs: captured.update(kwargs) or FakeGovernor())
    monkeypatch.setattr(app_module.account_service, "cleanup_auto_remove_accounts", lambda: None)
    monkeypatch.setattr(app_module, "start_limited_account_watcher", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_image_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module, "start_log_cleanup_scheduler", lambda event: JoinedThread())
    monkeypatch.setattr(app_module.backup_service, "start", lambda: None)
    monkeypatch.setattr(app_module.backup_service, "stop", lambda: None)
    monkeypatch.setattr(app_module.config, "cleanup_old_images", lambda: None)
    monkeypatch.setattr(app_module, "cleanup_old_logs", lambda: None)
    monkeypatch.setattr(app_module.dashboard_metrics_service, "flush", lambda: None)
    monkeypatch.setattr(
        app_module,
        "image_task_service",
        SimpleNamespace(
            start=lambda: None,
            stop=lambda timeout=None: None,
            repository=SimpleNamespace(
                queue_snapshot=lambda: {},
                logical_backup=lambda: {},
                restore_logical_backup=lambda payload: {},
                protected_artifact_paths=lambda: set(),
            ),
            artifact_service=SimpleNamespace(root=None),
            worker=SimpleNamespace(resource_controller=FakeController(), submit_registration=lambda callback: None),
        ),
        raising=False,
    )

    with TestClient(app_module.create_app()):
        assert order[0] == "governor_start"

    assert captured["ceiling"] == 80
    assert "resource_controller" in captured
    assert "limiter" in captured
    assert "governor_stop" in order


def test_image_queue_unavailable_returns_503(monkeypatch) -> None:
    from fastapi import FastAPI
    from services.image_queue.database import ImageQueueUnavailableError
    from api.errors import install_exception_handlers
    from api import image_tasks as image_tasks_module

    class UnavailableQueue:
        def list_tasks(self, identity, ids, limit=100, offset=0):
            raise ImageQueueUnavailableError("image queue PostgreSQL is unavailable")

    monkeypatch.setattr(image_tasks_module, "image_task_service", UnavailableQueue())
    monkeypatch.setattr(image_tasks_module, "require_identity", lambda authorization: {"id": "owner-1"})
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(image_tasks_module.create_router())

    response = TestClient(app).get("/api/image-tasks", headers={"Authorization": "Bearer test"})

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "image_queue_unavailable"


def test_external_image_routes_inject_durable_context(monkeypatch) -> None:
    prepared = []

    async def prepare(body, payload, **kwargs):
        prepared.append((body, payload, kwargs))
        return {}

    class FakeCall:
        def __init__(self, *args, **kwargs):
            return None

        def attach_trace_metadata(self, payload):
            return None

        def _trace_image_perf(self):
            return False

        async def run(self, handler, payload, **kwargs):
            async_before = kwargs.get("async_before")
            if async_before is not None:
                await async_before()
            return handler(payload)

    async def allow(call, text):
        return None

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module.durable_image, "prepare", prepare)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1", "role": "user"})
    monkeypatch.setattr(ai_module.openai_v1_image_generations, "handle", lambda payload: payload["_image_task_context"])
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/images/generations",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "request-1",
            "X-NewAPI-Request-Id": "trace-1",
        },
        json={"model": "gpt-image-2", "prompt": "cat"},
    )

    assert response.status_code == 200
    assert response.json()["identity"]["id"] == "owner-1"
    assert response.json()["idempotency_key"] == "request-1"
    assert response.json()["trace_headers"] == {"x-newapi-request-id": "trace-1"}
    assert prepared and prepared[0][2]["mode"] == "generation"


def test_external_image_context_uses_client_task_id_as_quota_alias(monkeypatch) -> None:
    captured = {}

    async def prepare(body, payload, **kwargs):
        return {}

    class FakeCall:
        def __init__(self, *args, **kwargs):
            return None

        def attach_trace_metadata(self, payload):
            return None

        def _trace_image_perf(self):
            return False

        async def run(self, handler, payload, **kwargs):
            captured.update(payload["_image_task_context"])
            return {"ok": True}

    async def allow(call, text):
        return None

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module.durable_image, "prepare", prepare)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1", "role": "user"})
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "header-key-1"},
        json={
            "model": "gpt-image-2",
            "prompt": "cat",
            "client_task_id": "same-client-task",
        },
    )

    assert response.status_code == 200
    assert captured["idempotency_key"] == "header-key-1"
    assert captured["quota_idempotency_key"] == "header-key-1"
    assert captured["quota_idempotency_aliases"] == ["same-client-task"]


def test_text_chat_does_not_receive_image_queue_context(monkeypatch) -> None:
    captured = {}

    class FakeCall:
        def __init__(self, *args, **kwargs):
            return None

        def attach_trace_metadata(self, payload):
            return None

        def _trace_image_perf(self):
            return False

        async def run(self, handler, payload, **kwargs):
            captured.update(payload)
            return {"ok": True}

    async def allow(call, text):
        return None

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1", "role": "user"})
    monkeypatch.setattr(ai_module, "is_image_chat_request", lambda payload: False)
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={"model": "gpt-5", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert "_image_task_context" not in captured


def test_responses_image_route_logs_trace_request_headers(monkeypatch) -> None:
    captured = {}

    class FakeCall:
        def __init__(self, *args, **kwargs):
            self.trace_metadata = {}
            self.call_id = "call-responses-1"

        def attach_trace_metadata(self, payload):
            captured["trace_metadata_after_attach"] = dict(self.trace_metadata)

        def _trace_image_perf(self):
            return True

        async def run(self, handler, payload, **kwargs):
            captured["trace_metadata_at_run"] = dict(self.trace_metadata)
            return {"ok": True}

    async def allow(call, text):
        return None

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/responses",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "request-1",
            "X-NewAPI-Request-Id": "trace-1",
        },
        json={
            "model": "gpt-image-2",
            "input": "draw a cat",
            "tools": [{"type": "image_generation"}],
        },
    )

    assert response.status_code == 200
    assert captured["trace_metadata_after_attach"]["request_headers"] == {
        "x_newapi_request_id": "trace-1",
    }
    assert captured["trace_metadata_at_run"]["request_headers"] == {
        "x_newapi_request_id": "trace-1",
    }


def test_image_protocol_rejects_request_without_replayable_idempotency_key(monkeypatch) -> None:
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer test"},
        json={"model": "gpt-image-2", "prompt": "cat"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "idempotency_key_required"


def test_image_protocol_accepts_client_task_id_as_idempotency_key(monkeypatch) -> None:
    captured = {}

    class FakeCall:
        def __init__(self, *args, **kwargs):
            return None

        def attach_trace_metadata(self, payload):
            return None

        def _trace_image_perf(self):
            return False

        async def run(self, handler, payload, **kwargs):
            captured.update(payload)
            return {"ok": True}

    async def allow(call, text):
        return None

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/images/generations",
        headers={"Authorization": "Bearer test"},
        json={"model": "gpt-image-2", "prompt": "cat", "client_task_id": "client-retry-1"},
    )

    assert response.status_code == 200
    assert captured["_image_task_context"]["idempotency_key"] == "client-retry-1"


def test_editable_file_task_rejects_request_without_replayable_idempotency_key(
    monkeypatch,
) -> None:
    captured = {}

    class FakeQuota:
        def cancel(self):
            captured["quota_cancelled"] = True

    async def allow(call, text):
        return None

    def submit_ppt(identity, **kwargs):
        captured.update(kwargs)
        return {"id": "task-1", "status": "queued"}

    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "reserve_quota", lambda *args, **kwargs: FakeQuota())
    monkeypatch.setattr(ai_module, "_commit_editable_quota_or_raise", lambda *args: None)
    monkeypatch.setattr(ai_module, "resolve_api_base_url", lambda request: "https://api.example")
    monkeypatch.setattr(ai_module.editable_file_task_service, "submit_ppt", submit_ppt)
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/ppt/generations",
        headers={"Authorization": "Bearer test"},
        json={"prompt": "make a deck"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "idempotency_key_required"
    assert "id" not in captured


def test_editable_file_task_accepts_idempotency_header_as_task_id(monkeypatch) -> None:
    captured = {}

    class FakeQuota:
        def cancel(self):
            return None

    async def allow(call, text):
        return None

    def submit_ppt(identity, **kwargs):
        captured.update(kwargs)
        return {"id": kwargs["client_task_id"], "status": "queued"}

    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "reserve_quota", lambda *args, **kwargs: FakeQuota())
    monkeypatch.setattr(ai_module, "_commit_editable_quota_or_raise", lambda *args: None)
    monkeypatch.setattr(ai_module, "resolve_api_base_url", lambda request: "https://api.example")
    monkeypatch.setattr(ai_module.editable_file_task_service, "submit_ppt", submit_ppt)
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/ppt/generations",
        headers={
            "Authorization": "Bearer test",
            "Idempotency-Key": "header-task-1",
        },
        json={"prompt": "make a deck"},
    )

    assert response.status_code == 200
    assert captured["client_task_id"] == "header-task-1"


def test_editable_file_task_logs_queued_submission_after_quota_commit(monkeypatch) -> None:
    events = []

    class FakeQuota:
        committed = False

        def commit(self):
            self.committed = True
            events.append("commit")

        def cancel(self):
            events.append("cancel")

    quota = FakeQuota()

    class FakeCall:
        def __init__(self, *args, **kwargs):
            return None

        def log(self, suffix, result=None, status="success", **kwargs):
            events.append(("log", suffix, status, bool(quota.committed), result))

    async def allow(call, text):
        return None

    def submit_ppt(identity, **kwargs):
        return {"id": kwargs["client_task_id"], "task_id": kwargs["client_task_id"], "status": "queued"}

    monkeypatch.setattr(ai_module, "LoggedCall", FakeCall)
    monkeypatch.setattr(ai_module, "require_identity", lambda authorization: {"id": "owner-1"})
    monkeypatch.setattr(ai_module, "filter_or_log", allow)
    monkeypatch.setattr(ai_module, "reserve_quota", lambda *args, **kwargs: quota)
    monkeypatch.setattr(ai_module, "resolve_api_base_url", lambda request: "https://api.example")
    monkeypatch.setattr(ai_module.editable_file_task_service, "submit_ppt", submit_ppt)
    app = __import__("fastapi").FastAPI()
    app.include_router(ai_module.create_router())

    response = TestClient(app).post(
        "/v1/ppt/generations",
        headers={"Authorization": "Bearer test", "Idempotency-Key": "deck-task-1"},
        json={"prompt": "make a deck"},
    )

    assert response.status_code == 200
    assert events[0] == "commit"
    assert events[1][0:4] == ("log", "已入队", "queued", True)
    assert events[1][4]["task_id"] == "deck-task-1"
