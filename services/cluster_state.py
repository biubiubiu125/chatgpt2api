from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from services.cluster_settings import load_cluster_settings


def _clean(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return _clean(value)


def _heartbeat_age_seconds(row: Mapping[str, Any], now: datetime) -> float:
    raw = row.get("heartbeat_age_seconds")
    if raw not in (None, ""):
        return round(max(0.0, _float(raw)), 3)
    heartbeat = row.get("heartbeat_at")
    if isinstance(heartbeat, datetime):
        return round(max(0.0, (now - heartbeat.astimezone(timezone.utc)).total_seconds()), 3)
    try:
        parsed = datetime.fromisoformat(_clean(heartbeat).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round(max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds()), 3)


def build_worker_items(
    workers: Iterable[Mapping[str, Any]],
    *,
    joins: Iterable[Mapping[str, Any]] = (),
    now: datetime | None = None,
    online_after_seconds: int = 180,
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    join_rows = {
        _clean(row.get("worker_id")): dict(row)
        for row in joins
        if _clean(row.get("worker_id"))
    }
    items: list[dict[str, Any]] = []
    heartbeat_rows = {
        _clean(row.get("worker_id")): dict(row)
        for row in workers
        if _clean(row.get("worker_id"))
    }
    worker_ids = set(join_rows) | set(heartbeat_rows)
    for worker_id in worker_ids:
        row = heartbeat_rows.get(worker_id, {})
        join_row = join_rows.get(worker_id, {})
        snapshot = dict(row.get("resource_snapshot") or {})
        effective_concurrency = _int(row.get("effective_concurrency"), _int(snapshot.get("effective_concurrency")))
        current_concurrency = _int(snapshot.get("current_concurrency"), _int(row.get("current_concurrency")))
        remaining_capacity = _int(
            snapshot.get("remaining_capacity"),
            max(0, effective_concurrency - current_concurrency),
        )
        heartbeat_age = _heartbeat_age_seconds(row, current)
        has_heartbeat = row.get("heartbeat_at") not in (None, "") or row.get("heartbeat_age_seconds") not in (None, "")
        items.append({
            "worker_id": worker_id,
            "join_status": _clean(row.get("join_status") or join_row.get("status")),
            "online": bool(has_heartbeat and heartbeat_age <= max(1, int(online_after_seconds))),
            "heartbeat_at": _iso(row.get("heartbeat_at")),
            "heartbeat_age_seconds": heartbeat_age,
            "node_role": _clean(snapshot.get("node_role")),
            "run_api": _bool(snapshot.get("run_api")),
            "run_worker": _bool(snapshot.get("run_worker"), True),
            "wireguard_ip": _clean(
                snapshot.get("wireguard_ip") or join_row.get("wireguard_ip")
            ),
            "image_base_url": _clean(snapshot.get("image_base_url")),
            "current_concurrency": current_concurrency,
            "effective_concurrency": effective_concurrency,
            "remaining_capacity": remaining_capacity,
            "available_account_count": _int(snapshot.get("available_account_count")),
            "available_quota": _int(snapshot.get("available_quota")),
            "pause_reason": _clean(row.get("pause_reason") or snapshot.get("pause_reason")),
            "recent_error": _clean(snapshot.get("recent_error")),
            "upstream_error_rate": _float(snapshot.get("upstream_error_rate")),
            "delivery_status": _clean(snapshot.get("delivery_status") or "unknown"),
            "delivery_checked_at": _clean(snapshot.get("delivery_checked_at")),
            "delivery_url": _clean(snapshot.get("delivery_url")),
            "delivery_error": _clean(snapshot.get("delivery_error")),
            "delivery_failures": _int(snapshot.get("delivery_failures")),
            "resource_snapshot": snapshot,
        })
    return sorted(items, key=lambda item: (not item["online"], item["worker_id"]))


def build_cluster_runtime_health(
    node: Mapping[str, Any],
    workers: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    node_role = _clean(node.get("node_role")).lower().replace("_", "-")
    run_api = _bool(node.get("run_api"), node_role != "worker")
    required = bool(node_role == "api-main" and run_api)
    worker_items = [dict(item) for item in workers]
    if not required:
        return {
            "required": False,
            "healthy": True,
            "status": "ok",
            "online_workers": 0,
            "capable_workers": 0,
            "delivery_unhealthy_workers": [],
            "delivery_unknown_workers": [],
        }
    online_workers = [
        item
        for item in worker_items
        if _bool(item.get("online")) and _bool(item.get("run_worker"), True)
    ]
    capable_workers = [
        item
        for item in online_workers
        if _int(item.get("effective_concurrency")) > 0
    ]
    delivery_unhealthy_workers = [
        _clean(item.get("worker_id"))
        for item in online_workers
        if _clean(item.get("delivery_status")).lower() == "unhealthy"
    ]
    delivery_unknown_workers = [
        _clean(item.get("worker_id"))
        for item in online_workers
        if _clean(item.get("delivery_status")).lower() in {"", "unknown"}
    ]
    delivery_unhealthy_workers = [item for item in delivery_unhealthy_workers if item]
    delivery_unknown_workers = [item for item in delivery_unknown_workers if item]
    healthy = bool(capable_workers) and not delivery_unhealthy_workers and not delivery_unknown_workers
    return {
        "required": True,
        "healthy": healthy,
        "status": "ok" if healthy else "degraded",
        "online_workers": len(online_workers),
        "capable_workers": len(capable_workers),
        "delivery_unhealthy_workers": delivery_unhealthy_workers,
        "delivery_unknown_workers": delivery_unknown_workers,
    }


def current_node_payload() -> dict[str, Any]:
    settings = load_cluster_settings()
    return {
        "node_role": settings.node_role,
        "run_api": settings.run_api,
        "run_worker": settings.run_worker,
        "worker_id": settings.worker_id,
        "wireguard_ip": settings.wireguard_ip,
        "image_base_url": settings.image_base_url,
        "cluster_id": settings.cluster_id,
    }


def build_cluster_snapshot(
    *,
    node: Mapping[str, Any] | None = None,
    queue: Mapping[str, Any] | None = None,
    accounts: Mapping[str, Any] | None = None,
    register: Mapping[str, Any] | None = None,
    joins: Iterable[Mapping[str, Any]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    queue_payload = dict(queue or {})
    workers = build_worker_items(queue_payload.get("workers") or [], joins=joins, now=now)
    queue_payload["workers"] = workers
    node_payload = dict(node or current_node_payload())
    runtime_health = build_cluster_runtime_health(node_payload, workers)
    return {
        "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "node": node_payload,
        "queue": queue_payload,
        "workers": workers,
        "runtime_health": runtime_health,
        "accounts": dict(accounts or {}),
        "register": dict(register or {}),
    }
