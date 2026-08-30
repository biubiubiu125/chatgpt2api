from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Any, Callable

from utils.log import logger


ThreadpoolUpdateCallback = Callable[..., None]


def _set_limiter_tokens(limiter: Any, tokens: int) -> None:
    limiter.total_tokens = int(tokens)


def run_threadpool_governor(
    stop_event: Event,
    *,
    resource_controller: Any,
    limiter: Any,
    ceiling: int,
    poll_seconds: float = 5.0,
    set_tokens: Callable[[int], None] | None = None,
    on_update: ThreadpoolUpdateCallback | None = None,
) -> None:
    ceiling = max(1, int(ceiling))
    current_tokens = max(1, min(int(getattr(limiter, "total_tokens", ceiling) or ceiling), ceiling))
    while not stop_event.is_set():
        try:
            snapshot = resource_controller.sample()
            recommended = int(
                resource_controller.recommend_thread_tokens(
                    snapshot,
                    ceiling=ceiling,
                    current_tokens=current_tokens,
                )
            )
        except Exception as exc:
            logger.warning(
                {
                    "event": "threadpool_governor_sample_failed",
                    "error": str(exc),
                }
            )
            if stop_event.wait(max(0.0, float(poll_seconds))):
                break
            continue

        recommended = max(1, min(recommended, ceiling))
        if recommended != current_tokens:
            previous_tokens = current_tokens
            current_tokens = recommended
            if set_tokens is not None:
                set_tokens(current_tokens)
            else:
                _set_limiter_tokens(limiter, current_tokens)
            if on_update is not None:
                on_update(
                    previous_tokens=previous_tokens,
                    tokens=current_tokens,
                    snapshot=snapshot,
                    ceiling=ceiling,
                )
        elif int(getattr(limiter, "total_tokens", current_tokens) or current_tokens) != current_tokens:
            if set_tokens is not None:
                set_tokens(current_tokens)
            else:
                _set_limiter_tokens(limiter, current_tokens)
        if stop_event.wait(max(0.0, float(poll_seconds))):
            break


@dataclass
class ThreadPoolGovernor:
    resource_controller: Any
    limiter: Any
    ceiling: int
    poll_seconds: float = 5.0
    set_tokens: Callable[[int], None] | None = None
    on_update: ThreadpoolUpdateCallback | None = None
    stop_event: Event = field(default_factory=Event)
    _thread: Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(
            target=run_threadpool_governor,
            name="threadpool-governor",
            daemon=True,
            kwargs={
                "stop_event": self.stop_event,
                "resource_controller": self.resource_controller,
                "limiter": self.limiter,
                "ceiling": self.ceiling,
                "poll_seconds": self.poll_seconds,
                "set_tokens": self.set_tokens,
                "on_update": self.on_update,
            },
        )
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
