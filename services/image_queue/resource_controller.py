from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import time
from threading import RLock
from typing import Any, Callable

import psutil

from services.image_failure import image_failure
from services.image_queue.settings import ImageQueueSettings
from services.image_queue.types import ResourceDecision, ResourceSnapshot


class ImageQueueStorageFullError(RuntimeError):
    code = "image_queue_storage_full"

    def __init__(self, message: str = "image artifact storage is under disk pressure") -> None:
        super().__init__(message)
        self.failure = image_failure(self.code, raw_detail=message).with_public_detail(message)


class ImageQueueResourcePressureError(RuntimeError):
    code = "image_queue_resource_pressure"

    def __init__(self, reason: str = "resource_pressure") -> None:
        message = f"image queue is temporarily unavailable due to {reason}"
        super().__init__(message)
        self.reason = reason
        self.failure = image_failure(self.code, raw_detail=message).with_public_detail(message)


class ResourceController:
    ESTIMATED_GENERATION_MEMORY_BYTES = 384 * 1024**2
    FILE_HANDLE_GUARD = 8192
    UPSTREAM_ERROR_WINDOW = 40
    UPSTREAM_ERROR_RATE_THRESHOLD = 0.45
    UPSTREAM_ERROR_MIN_SAMPLES = 8

    def __init__(
        self,
        settings: ImageQueueSettings,
        *,
        database: Any | None = None,
        cgroup_root: Path | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.database = database
        self.cgroup_root = cgroup_root or Path("/sys/fs/cgroup")
        self._monotonic = monotonic
        self._cpu_paused = False
        self._last_swap_sample: tuple[int, int, float] | None = None
        self._last_cpu_sample: tuple[int, float] | None = None
        self._adaptive_limit = 1
        self._recovering = False
        self._lock = RLock()
        self._upstream_outcomes: deque[bool] = deque(maxlen=self.UPSTREAM_ERROR_WINDOW)

    @staticmethod
    def _read_cgroup_number(path: Path) -> int | None:
        try:
            value = path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None
        if not value or value == "max":
            return None
        try:
            return max(0, int(value))
        except ValueError:
            return None

    def _read_first_cgroup_number(self, *relative_paths: str) -> int | None:
        for relative_path in relative_paths:
            value = self._read_cgroup_number(self.cgroup_root / relative_path)
            if value is not None:
                return value
        return None

    def _memory_sample(self, memory: Any) -> tuple[int, int]:
        physical_limit = int(memory.total)
        physical_available = int(memory.available)
        cgroup_limit = self._read_first_cgroup_number(
            "memory.max",
            "memory.limit_in_bytes",
            "memory/memory.limit_in_bytes",
        )
        cgroup_current = self._read_first_cgroup_number(
            "memory.current",
            "memory.usage_in_bytes",
            "memory/memory.usage_in_bytes",
        )
        if (
            cgroup_limit is None
            or cgroup_current is None
            or cgroup_limit <= 0
            or cgroup_limit >= physical_limit
        ):
            return physical_available, physical_limit
        return min(physical_available, max(0, cgroup_limit - cgroup_current)), cgroup_limit

    def cpu_limit_cores(self) -> float | None:
        try:
            raw = (self.cgroup_root / "cpu.max").read_text(encoding="ascii").strip().split()
            if len(raw) != 2 or raw[0] == "max":
                return None
            quota = int(raw[0])
            period = int(raw[1])
            return float(quota) / float(period) if quota > 0 and period > 0 else None
        except (OSError, UnicodeError, ValueError, ZeroDivisionError):
            pass
        quota = self._read_first_cgroup_number(
            "cpu.cfs_quota_us",
            "cpu/cpu.cfs_quota_us",
            "cpuacct/cpu.cfs_quota_us",
            "cpu,cpuacct/cpu.cfs_quota_us",
        )
        period = self._read_first_cgroup_number(
            "cpu.cfs_period_us",
            "cpu/cpu.cfs_period_us",
            "cpuacct/cpu.cfs_period_us",
            "cpu,cpuacct/cpu.cfs_period_us",
        )
        if quota is None or period is None or quota <= 0 or period <= 0:
            return None
        return float(quota) / float(period)

    def _cpu_usage_microseconds(self) -> int | None:
        try:
            values = {
                key: int(value)
                for key, value in (
                    line.split(None, 1)
                    for line in (self.cgroup_root / "cpu.stat").read_text(encoding="ascii").splitlines()
                    if len(line.split(None, 1)) == 2
                )
            }
            return max(0, int(values["usage_usec"]))
        except (OSError, UnicodeError, ValueError, KeyError):
            pass
        usage_ns = self._read_first_cgroup_number(
            "cpuacct.usage",
            "cpu/cpuacct.usage",
            "cpuacct/cpuacct.usage",
            "cpu,cpuacct/cpuacct.usage",
        )
        if usage_ns is not None:
            return int(usage_ns // 1000)
        usage_ticks = self._read_cpuacct_stat_ticks()
        if usage_ticks is None:
            return None
        try:
            clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        except (AttributeError, OSError, ValueError):
            clock_ticks = 100
        if clock_ticks <= 0:
            return None
        return int(usage_ticks * 1_000_000 / clock_ticks)

    def _read_cpuacct_stat_ticks(self) -> int | None:
        for relative_path in (
            "cpuacct.stat",
            "cpu/cpuacct.stat",
            "cpuacct/cpuacct.stat",
            "cpu,cpuacct/cpuacct.stat",
        ):
            try:
                lines = (self.cgroup_root / relative_path).read_text(encoding="ascii").splitlines()
            except (OSError, UnicodeError):
                continue
            values: dict[str, int] = {}
            for line in lines:
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    values[parts[0]] = int(parts[1])
                except ValueError:
                    continue
            usage = values.get("user", 0) + values.get("system", 0)
            if usage > 0:
                return usage
        return None

    def _container_cpu_percent(self, fallback: float) -> float:
        limit_cores = self.cpu_limit_cores()
        if limit_cores is None:
            return float(fallback)
        usage_usec = self._cpu_usage_microseconds()
        if usage_usec is None:
            return float(fallback)
        now = self._monotonic()
        previous = self._last_cpu_sample
        self._last_cpu_sample = (usage_usec, now)
        if previous is None or now <= previous[1] or usage_usec < previous[0]:
            return float(fallback)
        used_seconds = float(usage_usec - previous[0]) / 1_000_000.0
        elapsed = now - previous[1]
        return max(0.0, min(100.0, used_seconds / elapsed / limit_cores * 100.0))

    @staticmethod
    def _file_handle_count(process: Any) -> int:
        for name in ("num_handles", "num_fds"):
            method = getattr(process, name, None)
            if callable(method):
                try:
                    return int(method())
                except (OSError, TypeError, ValueError):
                    continue
        return 0

    def _swap_rates(self, swap: Any) -> tuple[int, int]:
        now = self._monotonic()
        current = (int(getattr(swap, "sin", 0)), int(getattr(swap, "sout", 0)), now)
        previous = self._last_swap_sample
        self._last_swap_sample = current
        if previous is None or now <= previous[2]:
            return 0, 0
        elapsed = now - previous[2]
        return (
            int(max(0, current[0] - previous[0]) / elapsed),
            int(max(0, current[1] - previous[1]) / elapsed),
        )

    def _database_pool_percent(self) -> float:
        if self.database is None:
            return 0.0
        try:
            return float(self.database.pool_usage_percent())
        except Exception:
            return 100.0

    def note_upstream_outcome(
        self,
        *,
        success: bool,
        status_code: int | None = None,
        error_code: str = "",
    ) -> None:
        """Record a recent upstream generation/download outcome for adaptive pause."""
        with self._lock:
            if success:
                self._upstream_outcomes.append(True)
                return
            code = str(error_code or "").strip().lower()
            try:
                numeric_status = int(status_code) if status_code is not None else None
            except (TypeError, ValueError):
                numeric_status = None
            transient = (
                numeric_status in {408, 429, 500, 502, 503, 504}
                or code in {
                    "upstream_timeout",
                    "upstream_5xx",
                    "upstream_rate_limited",
                    "rate_limited",
                    "image_stream_interrupted",
                    "image_poll_timeout",
                    "image_stream_timeout",
                    "network_error",
                    "upstream_connection_failed",
                    "upstream_connection_timeout",
                    "upstream_unavailable",
                }
                or (numeric_status is not None and numeric_status >= 500)
            )
            # Only count transient upstream pressure; permanent input/policy errors
            # should not freeze the whole generation pipeline.
            if transient:
                self._upstream_outcomes.append(False)

    def upstream_error_rate(self) -> float:
        with self._lock:
            if len(self._upstream_outcomes) < self.UPSTREAM_ERROR_MIN_SAMPLES:
                return 0.0
            failures = sum(1 for item in self._upstream_outcomes if not item)
            return failures / float(len(self._upstream_outcomes))

    def sample(self) -> ResourceSnapshot:
        with self._lock:
            self.settings.artifact_root.mkdir(parents=True, exist_ok=True)
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            process = psutil.Process()
            disk = shutil.disk_usage(self.settings.artifact_root.resolve())
            available_memory, memory_limit = self._memory_sample(memory)
            swap_in_rate, swap_out_rate = self._swap_rates(swap)
            return ResourceSnapshot(
                cpu_percent=self._container_cpu_percent(float(psutil.cpu_percent(interval=None))),
                available_memory_bytes=available_memory,
                memory_limit_bytes=memory_limit,
                swap_in_bytes_per_second=swap_in_rate,
                swap_out_bytes_per_second=swap_out_rate,
                thread_count=int(process.num_threads()),
                file_handle_count=self._file_handle_count(process),
                database_pool_percent=self._database_pool_percent(),
                disk_free_bytes=int(disk.free),
                disk_free_percent=(float(disk.free) / float(disk.total) * 100.0) if disk.total else 0.0,
                sampled_at=datetime.now(timezone.utc),
            )

    def _disk_pressure(self, snapshot: ResourceSnapshot) -> bool:
        return snapshot.disk_free_bytes < 5 * 1024**3 or snapshot.disk_free_percent < 5.0

    @staticmethod
    def _memory_used_percent(snapshot: ResourceSnapshot) -> float:
        if snapshot.memory_limit_bytes <= 0:
            return 0.0
        used = max(0, snapshot.memory_limit_bytes - snapshot.available_memory_bytes)
        return max(0.0, min(100.0, float(used) / float(snapshot.memory_limit_bytes) * 100.0))

    def _memory_pressure(self, snapshot: ResourceSnapshot) -> bool:
        return snapshot.memory_limit_bytes > 0 and snapshot.available_memory_bytes < max(
            512 * 1024**2,
            int(snapshot.memory_limit_bytes * 0.05),
        )

    @staticmethod
    def _swap_pressure(snapshot: ResourceSnapshot) -> bool:
        return snapshot.swap_in_bytes_per_second > 0 or snapshot.swap_out_bytes_per_second > 0

    def _generation_memory_pause(self, snapshot: ResourceSnapshot) -> bool:
        return (
            self._memory_pressure(snapshot)
            or self._memory_used_percent(snapshot) >= self.settings.memory_pause_percent
        )

    def _submission_memory_pressure(self, snapshot: ResourceSnapshot) -> bool:
        return (
            self._memory_pressure(snapshot)
            or self._memory_used_percent(snapshot) >= self.settings.memory_reject_percent
        )

    def allow_recovery(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        """Recovery/saving may continue under CPU pressure; only hard resource walls block it."""
        with self._lock:
            if self._disk_pressure(snapshot):
                return ResourceDecision(False, "resource_disk", 0)
            if self._memory_pressure(snapshot):
                return ResourceDecision(False, "resource_memory", 0)
            if snapshot.database_pool_percent >= 95.0:
                return ResourceDecision(False, "resource_database_pool", 0)
            if snapshot.file_handle_count >= self.FILE_HANDLE_GUARD:
                return ResourceDecision(False, "resource_file_handles", 0)
            if snapshot.thread_count >= self.settings.absolute_guard:
                return ResourceDecision(False, "resource_threads", 0)
            return ResourceDecision(True, "", max(1, self.settings.absolute_guard))

    def allow_new_generation(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        with self._lock:
            return self._allow_new_generation_locked(snapshot)

    def _allow_new_generation_locked(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        if snapshot.cpu_percent >= self.settings.cpu_pause_percent:
            self._cpu_paused = True
        elif self._cpu_paused and snapshot.cpu_percent < self.settings.cpu_resume_percent:
            self._cpu_paused = False
        if self._cpu_paused:
            return self._pressure("resource_cpu")
        if self._swap_pressure(snapshot):
            return self._pressure("resource_swap")
        if snapshot.database_pool_percent >= 85.0:
            return self._pressure("resource_database_pool")
        if self._disk_pressure(snapshot):
            return self._pressure("resource_disk")
        if self._generation_memory_pause(snapshot):
            return self._pressure("resource_memory")
        if snapshot.file_handle_count >= self.FILE_HANDLE_GUARD:
            return self._pressure("resource_file_handles")
        error_rate = self.upstream_error_rate()
        if error_rate >= self.UPSTREAM_ERROR_RATE_THRESHOLD:
            return self._pressure("resource_upstream_errors")
        remaining_threads = max(0, self.settings.absolute_guard - snapshot.thread_count)
        if remaining_threads <= 0:
            return self._pressure("resource_threads")
        reserve = max(512 * 1024**2, int(snapshot.memory_limit_bytes * 0.05))
        usable_memory = max(0, snapshot.available_memory_bytes - reserve)
        memory_slots = max(1, usable_memory // self.ESTIMATED_GENERATION_MEMORY_BYTES)
        safe_limit = max(1, min(self.settings.absolute_guard, remaining_threads, memory_slots))
        if (
            snapshot.cpu_percent >= self.settings.cpu_throttle_percent
            or self._memory_used_percent(snapshot) >= self.settings.memory_throttle_percent
        ):
            return ResourceDecision(
                True,
                "",
                max(1, min(safe_limit, self._adaptive_limit // 2)),
            )
        if self._recovering:
            self._adaptive_limit = min(safe_limit, self._adaptive_limit + 1)
            self._recovering = False
        effective = min(safe_limit, self._adaptive_limit)
        self._adaptive_limit = min(safe_limit, self._adaptive_limit + 1)
        return ResourceDecision(True, "", max(1, effective))

    def allow_new_registration(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        with self._lock:
            if snapshot.cpu_percent >= self.settings.cpu_pause_percent:
                self._cpu_paused = True
            elif self._cpu_paused and snapshot.cpu_percent < self.settings.cpu_resume_percent:
                self._cpu_paused = False
            if self._cpu_paused or snapshot.cpu_percent >= self.settings.cpu_throttle_percent:
                return ResourceDecision(False, "resource_cpu", 0)
            if self._swap_pressure(snapshot):
                return ResourceDecision(False, "resource_swap", 0)
            if snapshot.database_pool_percent >= 85.0:
                return ResourceDecision(False, "resource_database_pool", 0)
            if self._disk_pressure(snapshot):
                return ResourceDecision(False, "resource_disk", 0)
            if (
                self._memory_pressure(snapshot)
                or self._memory_used_percent(snapshot) >= self.settings.memory_throttle_percent
            ):
                return ResourceDecision(False, "resource_memory", 0)
            if snapshot.file_handle_count >= self.FILE_HANDLE_GUARD:
                return ResourceDecision(False, "resource_file_handles", 0)
            if snapshot.thread_count >= self.settings.absolute_guard:
                return ResourceDecision(False, "resource_threads", 0)
            if self.upstream_error_rate() >= self.UPSTREAM_ERROR_RATE_THRESHOLD:
                return ResourceDecision(False, "resource_upstream_errors", 0)
            return ResourceDecision(True, "", 1)

    def _pressure(self, reason: str) -> ResourceDecision:
        self._adaptive_limit = max(1, self._adaptive_limit // 2)
        self._recovering = True
        return ResourceDecision(False, reason, 0)

    def recommend_thread_tokens(
        self,
        snapshot: ResourceSnapshot,
        *,
        ceiling: int,
        current_tokens: int,
    ) -> int:
        with self._lock:
            if snapshot.cpu_percent >= self.settings.cpu_pause_percent:
                self._cpu_paused = True
            elif self._cpu_paused and snapshot.cpu_percent < self.settings.cpu_resume_percent:
                self._cpu_paused = False

            ceiling = max(1, int(ceiling))
            current_tokens = max(1, int(current_tokens))
            thread_headroom = int(self.settings.absolute_guard) - int(snapshot.thread_count)
            safe_cap = max(
                1,
                min(
                    ceiling,
                    self.settings.absolute_guard,
                    max(1, current_tokens + thread_headroom),
                ),
            )
            if (
                self._cpu_paused
                or self._swap_pressure(snapshot)
                or self._disk_pressure(snapshot)
                or self._memory_pressure(snapshot)
                or snapshot.database_pool_percent >= 95.0
                or snapshot.file_handle_count >= self.FILE_HANDLE_GUARD
                or self.upstream_error_rate() >= self.UPSTREAM_ERROR_RATE_THRESHOLD
                or self._memory_used_percent(snapshot) >= self.settings.memory_pause_percent
            ):
                return max(1, min(safe_cap, max(1, current_tokens // 2)))
            if (
                snapshot.cpu_percent >= self.settings.cpu_throttle_percent
                or self._memory_used_percent(snapshot) >= self.settings.memory_throttle_percent
                or snapshot.database_pool_percent >= 85.0
            ):
                return max(1, min(safe_cap, max(1, current_tokens - max(1, current_tokens // 4))))
            if current_tokens < safe_cap:
                return min(safe_cap, current_tokens + 1)
            return safe_cap

    def allow_new_submission(self, snapshot: ResourceSnapshot) -> ResourceDecision:
        if snapshot.disk_free_bytes < 5 * 1024**3 or snapshot.disk_free_percent < 5.0:
            return ResourceDecision(False, "resource_disk", 0)
        return ResourceDecision(True, "", 1)
