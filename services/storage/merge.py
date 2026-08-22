from __future__ import annotations

from copy import deepcopy
from typing import Any


_MISSING = object()


def item_identity(item: dict[str, Any], identity_key: str) -> str:
    return str(item.get(identity_key) or "").strip()


def merge_item_lists(
    current_items: list[dict[str, Any]],
    incoming_items: list[dict[str, Any]],
    previous_items: list[dict[str, Any]],
    *,
    identity_key: str,
) -> list[dict[str, Any]]:
    current_by_id = {
        key: deepcopy(item)
        for item in current_items
        if isinstance(item, dict) and (key := item_identity(item, identity_key))
    }
    incoming_by_id = {
        key: deepcopy(item)
        for item in incoming_items
        if isinstance(item, dict) and (key := item_identity(item, identity_key))
    }
    previous_by_id = {
        key: deepcopy(item)
        for item in previous_items
        if isinstance(item, dict) and (key := item_identity(item, identity_key))
    }

    ordered_ids: list[str] = []
    for collection in (current_items, incoming_items):
        for item in collection:
            if not isinstance(item, dict):
                continue
            key = item_identity(item, identity_key)
            if key and key not in ordered_ids:
                ordered_ids.append(key)

    merged: list[dict[str, Any]] = []
    for key in ordered_ids:
        current = current_by_id.get(key)
        incoming = incoming_by_id.get(key)
        previous = previous_by_id.get(key)

        if incoming is None:
            if previous is not None:
                continue
            if current is not None:
                merged.append(current)
            continue

        if previous is not None and current is None:
            continue

        if previous is None or current is None:
            next_item = deepcopy(incoming)
            next_item[identity_key] = key
            merged.append(next_item)
            continue

        next_item = deepcopy(current)
        for field in set(previous) | set(incoming):
            previous_value = previous.get(field, _MISSING)
            incoming_value = incoming.get(field, _MISSING)
            if incoming_value == previous_value:
                continue
            if incoming_value is _MISSING:
                next_item.pop(field, None)
            else:
                next_item[field] = deepcopy(incoming_value)
        next_item[identity_key] = key
        merged.append(next_item)

    return merged
