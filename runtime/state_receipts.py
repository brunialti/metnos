"""Provider-neutral receipts for reversible state transitions."""
from __future__ import annotations

import copy


def membership_delta(before, after) -> dict[str, list[str]]:
    """Return a canonical receipt for a set transition.

    Values are opaque strings.  The helper neither knows nor infers their
    domain (labels, grants, tags, roles, ...).
    """
    before_set = {value for value in before if isinstance(value, str)}
    after_set = {value for value in after if isinstance(value, str)}
    return {
        "members_before": sorted(before_set),
        "members_after": sorted(after_set),
        "members_added": sorted(after_set - before_set),
        "members_removed": sorted(before_set - after_set),
    }


def inverse_membership_delta(receipt: dict) -> tuple[list[str], list[str]]:
    """Return ``(add, remove)`` needed to invert a canonical receipt."""
    if not isinstance(receipt, dict):
        raise ValueError("membership receipt must be an object")
    added = receipt.get("members_added")
    removed = receipt.get("members_removed")
    if (not isinstance(added, list) or not isinstance(removed, list)
            or any(not isinstance(value, str) for value in added + removed)):
        raise ValueError("membership receipt has invalid deltas")
    return sorted(set(removed)), sorted(set(added))


def state_transition(before, after) -> dict:
    """Capture exact JSON-compatible states without domain knowledge."""
    return {
        "state_before": copy.deepcopy(before),
        "state_after": copy.deepcopy(after),
    }


def state_to_restore(receipt: dict, current):
    """Return the prior state only when current still equals recorded after."""
    if not isinstance(receipt, dict):
        raise ValueError("state receipt must be an object")
    if "state_before" not in receipt or "state_after" not in receipt:
        raise ValueError("state receipt is incomplete")
    if current != receipt["state_after"]:
        raise ValueError("current state differs from receipt")
    return copy.deepcopy(receipt["state_before"])
