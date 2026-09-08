"""Immutable role and capability matrices for boundary classification."""
from __future__ import annotations

from dataclasses import dataclass

from contract_boundary_api_policy import BOUNDARY_API_OWNERS_V1
from contract_boundary_policy_types import (
    ContractBoundaryPolicyError,
    canonical_names_v1,
    closed_names_v1,
    require_text_v1,
)
from contract_boundary_syntax_policy import FLOW_CAPABILITIES


BOUNDARY_CAPABILITY_VOCABULARY_V1 = tuple(sorted(
    frozenset(FLOW_CAPABILITIES) | {
        capability
        for owner in BOUNDARY_API_OWNERS_V1
        for _name, capabilities in owner.apis
        for capability in capabilities
    },
    key=lambda value: value.encode("utf-8"),
))


@dataclass(frozen=True, slots=True)
class BoundaryRolePolicyV1:
    valid_roles: tuple[str, ...]
    direct_manifest_roles: tuple[str, ...]
    direct_manifest_paths: tuple[str, ...]
    birth_owner_paths: tuple[str, ...]
    birth_owner_forbidden: tuple[str, ...]
    operational_birth_forbidden: tuple[str, ...]
    bootstrap_capabilities: tuple[str, ...]
    bootstrap_roles: tuple[str, ...]
    live_mutations: tuple[str, ...]
    live_mutation_roles: tuple[str, ...]
    documentation_exemptions: tuple[str, ...]
    exception_justification: tuple[tuple[str, tuple[str, ...]], ...]
    coordinator_capabilities: tuple[str, ...]
    boundary_entry_keys: tuple[str, ...]
    birth_closed_legacy_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_role_policy_v1(self)


def _validate_justifications_v1(
    rows: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    if type(rows) is not tuple or not rows:
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:exception_justification"
        )
    kinds: list[str] = []
    for row in rows:
        if type(row) is not tuple or len(row) != 2:
            raise ContractBoundaryPolicyError(
                "boundary_policy_invalid:exception_justification"
            )
        kind, capabilities = row
        kinds.append(require_text_v1(kind, field="exception_justification_kind"))
        canonical_names_v1(capabilities, field="exception_justification_capabilities")
    closed_names_v1(tuple(kinds), field="exception_justification_kinds")


def _validate_role_policy_v1(value: BoundaryRolePolicyV1) -> None:
    canonical_fields = (
        "valid_roles", "direct_manifest_roles", "direct_manifest_paths",
        "birth_owner_paths", "birth_owner_forbidden",
        "operational_birth_forbidden", "bootstrap_capabilities",
        "bootstrap_roles", "live_mutations", "live_mutation_roles",
        "documentation_exemptions", "coordinator_capabilities",
        "boundary_entry_keys", "birth_closed_legacy_capabilities",
    )
    for field in canonical_fields:
        canonical_names_v1(getattr(value, field), field=field)
    _validate_justifications_v1(value.exception_justification)
    known = frozenset(BOUNDARY_CAPABILITY_VOCABULARY_V1)
    capability_fields = (
        "birth_owner_forbidden", "operational_birth_forbidden",
        "bootstrap_capabilities", "live_mutations", "documentation_exemptions",
        "coordinator_capabilities", "birth_closed_legacy_capabilities",
    )
    for field in capability_fields:
        if not frozenset(getattr(value, field)).issubset(known):
            raise ContractBoundaryPolicyError(
                f"boundary_policy_invalid:{field}_unknown"
            )
    for _kind, capabilities in value.exception_justification:
        if not frozenset(capabilities).issubset(known):
            raise ContractBoundaryPolicyError(
                "boundary_policy_invalid:exception_justification_unknown"
            )
    valid = frozenset(value.valid_roles)
    for field in ("direct_manifest_roles", "bootstrap_roles", "live_mutation_roles"):
        if not frozenset(getattr(value, field)).issubset(valid):
            raise ContractBoundaryPolicyError(f"boundary_policy_invalid:{field}")


BOUNDARY_ROLE_POLICY_V1 = BoundaryRolePolicyV1(
    valid_roles=(
        "administrative_tool", "birth_owner", "documentation", "live_reader",
        "migration_boundary", "offline_authoring", "operational_producer",
        "store_owner",
    ),
    direct_manifest_roles=(
        "migration_boundary", "offline_authoring", "store_owner",
    ),
    direct_manifest_paths=("runtime/executor_birth_authoring.py",),
    birth_owner_paths=(
        "runtime/executor_birth.py", "runtime/executor_birth_intent.py",
        "runtime/executor_birth_operational.py",
    ),
    birth_owner_forbidden=(
        "legacy_bootstrap", "publish_bootstrap", "publish_localization",
        "retire", "rollback", "sign",
    ),
    operational_birth_forbidden=("publish_technical", "reactivate", "sign"),
    bootstrap_capabilities=("legacy_bootstrap", "publish_bootstrap"),
    bootstrap_roles=("migration_boundary", "store_owner"),
    live_mutations=(
        "birth", "publish_localization", "publish_technical", "reactivate",
        "retire", "rollback",
    ),
    live_mutation_roles=(
        "administrative_tool", "birth_owner", "migration_boundary",
        "operational_producer", "store_owner",
    ),
    documentation_exemptions=("authoring_read", "authoring_verify"),
    exception_justification=(
        ("localization_only", ("publish_localization",)),
        ("retirement_only", ("retire",)),
        ("offline_nonproductive_authoring", ("sign",)),
    ),
    coordinator_capabilities=("store_write",),
    boundary_entry_keys=(
        "capabilities", "destination", "path", "phase", "role", "scope",
    ),
    birth_closed_legacy_capabilities=(
        "publish_localization", "publish_technical", "reactivate", "retire",
        "rollback", "sign",
    ),
)


__all__ = [
    "BOUNDARY_CAPABILITY_VOCABULARY_V1", "BOUNDARY_ROLE_POLICY_V1",
    "BoundaryRolePolicyV1",
]
