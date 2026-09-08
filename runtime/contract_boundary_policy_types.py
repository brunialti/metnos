"""Shared immutable validation primitives for boundary policy owners."""
from __future__ import annotations


class ContractBoundaryPolicyError(ValueError):
    """One authored policy fact is outside its closed representation."""


def require_text_v1(value: object, *, field: str) -> str:
    if type(value) is not str or not value or "\0" in value:
        raise ContractBoundaryPolicyError(f"boundary_policy_invalid:{field}")
    return value


def require_scope_key_v1(value: object, *, field: str) -> str:
    text = require_text_v1(value, field=field)
    path, separator, scope = text.partition(":")
    if separator != ":" or not path or not scope or ":" in scope:
        raise ContractBoundaryPolicyError(f"boundary_policy_invalid:{field}")
    return text


def closed_names_v1(values: object, *, field: str) -> tuple[str, ...]:
    if type(values) is not tuple or not values:
        raise ContractBoundaryPolicyError(f"boundary_policy_invalid:{field}")
    if any(
        type(value) is not str or not value or "\0" in value
        for value in values
    ):
        raise ContractBoundaryPolicyError(f"boundary_policy_invalid:{field}")
    if len(set(values)) != len(values):
        raise ContractBoundaryPolicyError(f"boundary_policy_duplicate:{field}")
    return values


def canonical_names_v1(values: object, *, field: str) -> tuple[str, ...]:
    closed = closed_names_v1(values, field=field)
    if closed != tuple(sorted(closed, key=lambda value: value.encode("utf-8"))):
        raise ContractBoundaryPolicyError(f"boundary_policy_order:{field}")
    return closed


__all__ = [
    "ContractBoundaryPolicyError", "canonical_names_v1", "closed_names_v1",
    "require_scope_key_v1", "require_text_v1",
]
