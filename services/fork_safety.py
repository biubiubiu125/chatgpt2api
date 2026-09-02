from __future__ import annotations

from typing import Any, Iterator

from utils.log import logger


def _service_singletons() -> Iterator[tuple[str, Any]]:
    """Yield the process-wide singletons a forked child inherits.

    Imported lazily and one at a time so a child can still reset everything it
    can reach even if one module is unavailable in its configuration.
    """
    for module_name, attribute in (
        ("services.config", "config"),
        ("services.account_service", "account_service"),
        ("services.proxy_service", "proxy_settings"),
        ("services.log_service", "log_service"),
        ("services.dashboard_metrics_service", "dashboard_metrics_service"),
        ("services.realtime_monitor_service", "realtime_monitor_service"),
        ("services.image_storage_service", "image_storage_service"),
    ):
        try:
            module = __import__(module_name, fromlist=[attribute])
        except Exception as exc:
            logger.warning({
                "event": "fork_state_reset_import_failed",
                "module": module_name,
                "error": str(exc),
            })
            continue
        singleton = getattr(module, attribute, None)
        if singleton is not None:
            yield f"{module_name}.{attribute}", singleton


def reset_inherited_process_state() -> list[str]:
    """Re-isolate inherited singletons at the top of a forked child.

    A ``fork`` hands the child a copy of every lock, in-flight counter and
    connection pool the parent held at fork time. Locks can arrive already held
    (instant deadlock), counters describe slots only the parent can release
    (the child waits forever), and pooled sockets are shared with the parent
    (interleaved protocol traffic). Each singleton knows how to rebuild its own
    state, so this just drives them and reports which ones ran.
    """
    reset_names: list[str] = []
    for name, singleton in _service_singletons():
        reset = getattr(singleton, "reset_after_fork", None)
        if not callable(reset):
            continue
        try:
            reset()
        except Exception as exc:
            logger.warning({
                "event": "fork_state_reset_failed",
                "target": name,
                "error": str(exc),
            })
            continue
        reset_names.append(name)
    return reset_names
