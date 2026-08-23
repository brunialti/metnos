"""Provider-neutral receipts for reversible set membership mutations."""
from __future__ import annotations


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
