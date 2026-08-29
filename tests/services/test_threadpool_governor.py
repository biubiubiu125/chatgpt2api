from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Thread
from types import SimpleNamespace

from services.image_queue.resource_controller import ResourceController
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import ResourceSnapshot
from services.threadpool_governor import run_threadpool_governor


def snapshot(**changes: object) -> ResourceSnapshot:
    base = ResourceSnapshot(
        cpu_percent=20.0,
        available_memory_bytes=8 * 1024**3,
        memory_limit_bytes=16 * 1024**3,
        swap_in_bytes_per_second=0,
        swap_out_bytes_per_second=0,
        thread_count=20,
        file_handle_count=20,
        database_pool_percent=10.0,
        disk_free_bytes=20 * 1024**3,
        disk_free_percent=20.0,
        sampled_at=datetime.now(timezone.utc),
    )
    return replace(base, **changes)


def test_run_threadpool_governor_updates_limiter_and_monitor() -> None:
    stop_event = Event()
    limiter = SimpleNamespace(total_tokens=80)
    updates: list[tuple[int, int]] = []
    samples = iter(
        [
            snapshot(cpu_percent=96, available_memory_bytes=256 * 1024**2, memory_limit_bytes=16 * 1024**3),
        ]
    )
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))

    def sample() -> ResourceSnapshot:
        return next(samples)

    def recommend(sampled: ResourceSnapshot, *, ceiling: int, current_tokens: int) -> int:
        assert ceiling == 80
        assert current_tokens == 80
        return 40

    def on_update(*, previous_tokens: int, tokens: int, snapshot: ResourceSnapshot, ceiling: int) -> None:
        updates.append((previous_tokens, tokens))
        stop_event.set()

    controller.sample = sample  # type: ignore[assignment]
    controller.recommend_thread_tokens = recommend  # type: ignore[assignment]

    thread = Thread(
        target=run_threadpool_governor,
        kwargs={
            "stop_event": stop_event,
            "resource_controller": controller,
            "limiter": limiter,
            "ceiling": 80,
            "poll_seconds": 0,
            "on_update": on_update,
        },
        daemon=True,
    )
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert limiter.total_tokens == 40
    assert updates == [(80, 40)]
