"""Type- and order-preserving comparisons for boundary policy tests."""
from __future__ import annotations

import re


def freeze_policy(value: object) -> object:
    """Retain nested types and meaningful order, including regex flags."""
    if type(value) is dict:
        return (type(value), tuple(
            (freeze_policy(key), freeze_policy(item))
            for key, item in value.items()
        ))
    if type(value) in (tuple, list):
        return (type(value), tuple(freeze_policy(item) for item in value))
    if type(value) in (set, frozenset):
        return (type(value), frozenset(freeze_policy(item) for item in value))
    if isinstance(value, re.Pattern):
        return (type(value), type(value.pattern), value.pattern, value.flags)
    return (type(value), value)
