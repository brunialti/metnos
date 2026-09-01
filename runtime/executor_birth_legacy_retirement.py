#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic retirement plan for the legacy entry points, and its proof.

Group 7 must neutralise every `legacy_binding` so the service identity cannot
recreate it, and must prove that no legacy entry is already in flight before it
does. This module owns the DECISION and the PROOF; it does not own the
mutation. Nothing here masks a unit, deletes a script or stops a process.

The separation is what makes the step auditable. A plan is a value: it can be
framed, bound into the completion capability, compared before and after, and
replayed without side effects. Once planning and executing live in the same
function, the only way to check the decision is to perform it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence


LEGACY_RETIREMENT_DOMAIN_V1 = b"metnos.executor-birth.legacy-retirement/v1\0"

# Closed table: every admitted (kind, scope) maps to exactly one action, and an
# unknown pair is refused rather than defaulted. A default here would silently
# retire a new kind of entry point the wrong way.
_RETIREMENT_ACTIONS_V1: Mapping[tuple[str, str], str] = {
    ("user_unit", "user"): "mask_user_unit",
    ("system_unit", "system"): "mask_system_unit",
    ("script", "repository"): "revoke_repository_entrypoint",
    ("python_module", "repository"): "revoke_repository_entrypoint",
}
_PRESERVE_REPLACED_SYSTEM_UNIT_V1 = "preserve_replaced_system_unit"

_IN_FLIGHT_STATES_V1 = frozenset({
    "activating", "active", "deactivating", "reloading", "running",
})


class LegacyRetirementError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> LegacyRetirementError:
    return LegacyRetirementError(code, detail)


@dataclass(frozen=True, slots=True)
class LegacyRetirementStepV1:
    """One legacy entry point and the single action that neutralises it."""

    legacy_id: str
    entry_id: str
    kind: str
    scope: str
    locator: str
    action: str


@dataclass(frozen=True, slots=True)
class CatalogRetirementPlanV1:
    """One plan and the census derived from the same decoded catalog."""

    catalog_id: str
    steps: tuple[LegacyRetirementStepV1, ...]
    dominant_unit_count: int
    legacy_binding_count: int
    cross_scope_match_count: int
    same_destination_overlap_count: int


def _require_text_v1(value: object, field: str) -> str:
    if (
        type(value) is not str or not value or value != value.strip()
        or "\x00" in value or "\n" in value or len(value.encode("utf-8")) > 4096
    ):
        raise _invalid("legacy_retirement_binding_invalid", field)
    return value


def _plan_retirement_v1(
    bindings: Sequence[Mapping[str, object]],
    dominant_units: frozenset[str],
) -> tuple[LegacyRetirementStepV1, ...]:
    """Decide one action per binding, in an order that does not depend on input.

    The order is the byte order of `legacy_id`, not the order the catalog
    happened to list: a plan whose digest changes with the caller's iteration
    order cannot be bound into a capability.
    """
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes)):
        raise _invalid("legacy_retirement_bindings_invalid", "shape")
    steps: list[LegacyRetirementStepV1] = []
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise _invalid("legacy_retirement_bindings_invalid", "entry")
        legacy_id = _require_text_v1(binding.get("legacy_id"), "legacy_id")
        if legacy_id in seen:
            raise _invalid("legacy_retirement_duplicate", legacy_id)
        seen.add(legacy_id)
        kind = _require_text_v1(binding.get("kind"), "kind")
        scope = _require_text_v1(binding.get("scope"), "scope")
        action = _RETIREMENT_ACTIONS_V1.get((kind, scope))
        if action is None:
            raise _invalid("legacy_retirement_unknown_kind", f"{kind}/{scope}")
        locator = _require_text_v1(binding.get("locator"), "locator")
        if kind == "system_unit" and locator in dominant_units:
            action = _PRESERVE_REPLACED_SYSTEM_UNIT_V1
        if binding.get("disposition") != "retire_in_group7":
            raise _invalid("legacy_retirement_disposition", legacy_id)
        steps.append(LegacyRetirementStepV1(
            legacy_id,
            _require_text_v1(binding.get("entry_id"), "entry_id"),
            kind, scope,
            locator,
            action,
        ))
    if not steps:
        raise _invalid("legacy_retirement_empty")
    ordered = tuple(sorted(
        steps, key=lambda step: step.legacy_id.encode("utf-8"),
    ))
    if sum(
        step.action == _PRESERVE_REPLACED_SYSTEM_UNIT_V1 for step in ordered
    ) > 1:
        raise _invalid("legacy_retirement_overlap", "multiple destinations")
    return ordered


def plan_retirement_v1(
    bindings: Sequence[Mapping[str, object]],
) -> tuple[LegacyRetirementStepV1, ...]:
    """Plan an isolated set with no dominant destination information."""
    return _plan_retirement_v1(bindings, frozenset())


def plan_catalog_retirement_v1(catalog: object) -> CatalogRetirementPlanV1:
    """Derive both sides of the overlap decision from one decoded catalog."""
    from executor_birth_service_catalog import DecodedServiceCatalogV1

    if type(catalog) is not DecodedServiceCatalogV1:
        raise _invalid("legacy_retirement_catalog_invalid", "type")
    dominant_units = frozenset(
        str(entry.unit_name)
        for entry in catalog.entries if entry.unit_name is not None
    )
    bindings = tuple({
        "legacy_id": binding.legacy_id,
        "entry_id": binding.entry_id,
        "kind": binding.kind,
        "scope": binding.scope,
        "locator": binding.locator,
        "disposition": binding.disposition,
    } for binding in catalog.legacy_bindings)
    steps = _plan_retirement_v1(bindings, dominant_units)
    cross_scope_matches = sum(
        step.kind in {"user_unit", "system_unit"}
        and step.locator in dominant_units
        for step in steps
    )
    same_destination_overlaps = sum(
        step.action == _PRESERVE_REPLACED_SYSTEM_UNIT_V1 for step in steps
    )
    census = (
        len(dominant_units), len(steps),
        cross_scope_matches, same_destination_overlaps,
    )
    return CatalogRetirementPlanV1(
        catalog.catalog_id, steps, *census,
    )


def plan_digest_v1(steps: Sequence[LegacyRetirementStepV1]) -> str:
    """Frame the plan so no field can slide into its neighbour."""
    digest = hashlib.sha256(LEGACY_RETIREMENT_DOMAIN_V1)
    digest.update(len(steps).to_bytes(8, "big"))
    for step in steps:
        if type(step) is not LegacyRetirementStepV1:
            raise _invalid("legacy_retirement_plan_invalid", "step")
        for field in (
            step.legacy_id, step.entry_id, step.kind,
            step.scope, step.locator, step.action,
        ):
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def require_no_legacy_in_flight_v1(
    steps: Sequence[LegacyRetirementStepV1],
    observed_states: Mapping[tuple[str, str], str],
) -> None:
    """Refuse while any legacy entry point is still running.

    Retiring an entry that is in flight would leave a live process owned by an
    identity that no longer exists, which is worse than not retiring it: the
    old world would keep running with no way to address it.

    A locator the observer says nothing about is NOT treated as idle. Silence
    is not evidence of absence, and this is the one place where assuming it
    would hide exactly the case that matters.
    """
    if not isinstance(observed_states, Mapping):
        raise _invalid("legacy_retirement_observation_invalid", "shape")
    in_flight: list[str] = []
    unobserved: list[str] = []
    for step in steps:
        identity = (step.scope, step.locator)
        state = observed_states.get(identity)
        if state is None:
            unobserved.append(f"{step.scope}/{step.locator}")
            continue
        if _require_text_v1(state, "state") in _IN_FLIGHT_STATES_V1:
            in_flight.append(step.locator)
    if unobserved:
        raise _invalid("legacy_retirement_unobserved", unobserved[0])
    if in_flight:
        raise _invalid("legacy_retirement_in_flight", in_flight[0])


__all__ = [
    "LEGACY_RETIREMENT_DOMAIN_V1",
    "LegacyRetirementError",
    "CatalogRetirementPlanV1",
    "LegacyRetirementStepV1",
    "plan_catalog_retirement_v1",
    "plan_digest_v1",
    "plan_retirement_v1",
    "require_no_legacy_in_flight_v1",
]
