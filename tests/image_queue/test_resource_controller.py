from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from services.image_queue.resource_controller import ResourceController
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import ResourceSnapshot


def snapshot(**changes: object) -> ResourceSnapshot:
    base = ResourceSnapshot(
        cpu_percent=40.0,
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


def test_cpu_gate_requires_pause_and_resume_thresholds() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    assert controller.allow_new_generation(snapshot(cpu_percent=96)).allowed is False
    assert controller.allow_new_generation(snapshot(cpu_percent=90)).allowed is False
    assert controller.allow_new_generation(snapshot(cpu_percent=84)).allowed is True


def test_swap_io_is_pressure_not_capacity() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    decision = controller.allow_new_generation(snapshot(swap_in_bytes_per_second=8 * 1024**2))
    assert decision.allowed is False
    assert decision.reason == "resource_swap"


def test_submission_gate_only_rejects_disk_pressure() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))

    cpu_pressure = controller.allow_new_submission(snapshot(cpu_percent=99))
    disk_pressure = controller.allow_new_submission(snapshot(disk_free_bytes=1024**3))

    assert cpu_pressure.allowed is True
    assert disk_pressure.allowed is False
    assert disk_pressure.reason == "resource_disk"


def test_sample_uses_database_pool_pressure(tmp_path) -> None:
    database = SimpleNamespace(pool_usage_percent=lambda: 87.5)
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        artifact_root=tmp_path / "artifacts",
    )

    sampled = ResourceController(settings, database=database).sample()

    assert sampled.database_pool_percent == 87.5


def test_sample_respects_cgroup_v2_memory_limit(tmp_path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "memory.max").write_text(str(2 * 1024**3), encoding="ascii")
    (cgroup_root / "memory.current").write_text(str(1536 * 1024**2), encoding="ascii")
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        artifact_root=tmp_path / "artifacts",
    )

    sampled = ResourceController(settings, cgroup_root=cgroup_root).sample()

    assert sampled.memory_limit_bytes == 2 * 1024**3
    assert sampled.available_memory_bytes == 512 * 1024**2


def test_sample_converts_swap_counters_to_rates(tmp_path, monkeypatch) -> None:
    samples = iter(
        [
            SimpleNamespace(sin=100, sout=200),
            SimpleNamespace(sin=300, sout=600),
        ]
    )
    times = iter([10.0, 12.0])
    monkeypatch.setattr("services.image_queue.resource_controller.psutil.swap_memory", lambda: next(samples))
    settings = ImageQueueSettings(
        database_url="postgresql://test",
        artifact_root=tmp_path / "artifacts",
    )
    controller = ResourceController(settings, monotonic=lambda: next(times))

    first = controller.sample()
    second = controller.sample()

    assert (first.swap_in_bytes_per_second, first.swap_out_bytes_per_second) == (0, 0)
    assert (second.swap_in_bytes_per_second, second.swap_out_bytes_per_second) == (100, 200)


def test_cgroup_cpu_usage_is_normalized_to_container_quota(tmp_path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "cpu.max").write_text("100000 100000", encoding="ascii")
    cpu_stat = cgroup_root / "cpu.stat"
    cpu_stat.write_text("usage_usec 100000\n", encoding="ascii")
    times = iter([10.0, 12.0])
    controller = ResourceController(
        ImageQueueSettings(database_url="postgresql://test"),
        cgroup_root=cgroup_root,
        monotonic=lambda: next(times),
    )

    assert controller._container_cpu_percent(5.0) == 5.0
    cpu_stat.write_text("usage_usec 2000000\n", encoding="ascii")

    assert controller._container_cpu_percent(5.0) == 95.0


def test_adaptive_limit_uses_memory_budget_and_aimd() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    constrained = snapshot(
        available_memory_bytes=600 * 1024**2,
        memory_limit_bytes=1024**3,
        thread_count=10,
    )

    first = controller.allow_new_generation(constrained)
    second = controller.allow_new_generation(replace(constrained, available_memory_bytes=2 * 1024**3))
    third = controller.allow_new_generation(replace(constrained, available_memory_bytes=2 * 1024**3))
    pressured = controller.allow_new_generation(replace(constrained, swap_out_bytes_per_second=1))
    recovered = controller.allow_new_generation(replace(constrained, available_memory_bytes=2 * 1024**3))

    assert first.allowed is True and first.effective_limit == 1
    assert second.effective_limit == 1
    assert third.effective_limit == 2
    assert pressured.allowed is False
    assert recovered.allowed is True and recovered.effective_limit == 2


def test_recommend_thread_tokens_shrinks_under_pressure() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    pressured = snapshot(cpu_percent=96, available_memory_bytes=256 * 1024**2, memory_limit_bytes=16 * 1024**3)

    assert controller.recommend_thread_tokens(pressured, ceiling=80, current_tokens=80) == 40


def test_recommend_thread_tokens_recovers_by_one_when_healthy() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    healthy = snapshot(cpu_percent=20, available_memory_bytes=8 * 1024**3, memory_limit_bytes=16 * 1024**3)

    assert controller.recommend_thread_tokens(healthy, ceiling=80, current_tokens=40) == 41


def test_recommend_thread_tokens_does_not_double_count_current_threadpool_threads() -> None:
    controller = ResourceController(
        ImageQueueSettings(
            database_url="postgresql://test",
            absolute_guard=128,
            generation_concurrency_limit=128,
            generation_concurrency_hard_cap=128,
        )
    )
    healthy = snapshot(
        cpu_percent=20,
        available_memory_bytes=8 * 1024**3,
        memory_limit_bytes=16 * 1024**3,
        thread_count=100,
    )

    assert controller.recommend_thread_tokens(healthy, ceiling=80, current_tokens=80) == 80


def test_registration_pressure_check_does_not_mutate_generation_limit() -> None:
    controller = ResourceController(ImageQueueSettings(database_url="postgresql://test"))
    controller._adaptive_limit = 7

    for _ in range(3):
        decision = controller.allow_new_registration(snapshot())

    assert decision.allowed is True
    assert controller._adaptive_limit == 7


def test_sample_uses_num_fds_when_num_handles_is_unavailable(tmp_path, monkeypatch) -> None:
    process = SimpleNamespace(num_threads=lambda: 7, num_fds=lambda: 23)
    monkeypatch.setattr("services.image_queue.resource_controller.psutil.Process", lambda: process)
    controller = ResourceController(ImageQueueSettings(
        database_url="postgresql://test",
        artifact_root=tmp_path / "artifacts",
    ))

    assert controller.sample().file_handle_count == 23
