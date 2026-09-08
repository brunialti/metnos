"""Validated aggregate for the complete immutable Birth-closed policy."""
from __future__ import annotations

from dataclasses import dataclass

from contract_boundary_birth_authority_policy import (
    BIRTH_AUTHORITY_POLICY_V1,
    BirthAuthorityPolicyV1,
)
from contract_boundary_birth_exception_policy import (
    BIRTH_EXCEPTION_CLASSES_V1,
    BIRTH_EXCEPTION_GRANTS_V1,
    BirthExceptionGrantV1,
)
from contract_boundary_policy_types import (
    ContractBoundaryPolicyError, closed_names_v1, require_text_v1,
)
from contract_boundary_role_policy import (
    BOUNDARY_CAPABILITY_VOCABULARY_V1,
    BOUNDARY_ROLE_POLICY_V1,
    BoundaryRolePolicyV1,
)
from contract_boundary_syntax_policy import (
    BIRTH_CLOSED_GUARD_VERSION,
    BIRTH_CLOSED_SCHEMA,
)


@dataclass(frozen=True, slots=True)
class BirthClosedPolicyV1:
    schema: str
    guard_version: str
    authority: BirthAuthorityPolicyV1
    roles: BoundaryRolePolicyV1
    exception_classes: tuple[str, ...]
    exception_grants: tuple[BirthExceptionGrantV1, ...]

    def __post_init__(self) -> None:
        _validate_birth_closed_policy_v1(self)


def _validated_grant_scopes_v1(
    value: BirthClosedPolicyV1, justifications: dict[str, tuple[str, ...]],
) -> frozenset[str]:
    grants = value.exception_grants
    if type(grants) is not tuple or not grants:
        raise ContractBoundaryPolicyError("boundary_policy_invalid:exception_grants")
    if any(type(grant) is not BirthExceptionGrantV1 for grant in grants):
        raise ContractBoundaryPolicyError("boundary_policy_invalid:exception_grants")
    scopes = closed_names_v1(
        tuple(grant.scope for grant in grants), field="exception_grant_scopes",
    )
    legacy = frozenset(value.roles.birth_closed_legacy_capabilities)
    known = frozenset(BOUNDARY_CAPABILITY_VOCABULARY_V1)
    for grant in grants:
        if grant.exception not in justifications:
            raise ContractBoundaryPolicyError(
                "boundary_policy_invalid:exception_class_reference"
            )
        justified = frozenset(justifications[grant.exception])
        if frozenset(grant.capabilities) & legacy != justified:
            raise ContractBoundaryPolicyError(
                "boundary_policy_invalid:exception_capability_justification"
            )
        if not frozenset(grant.capabilities).issubset(known):
            raise ContractBoundaryPolicyError(
                "boundary_policy_invalid:exception_capability_unknown"
            )
    return frozenset(scopes)


def _validate_birth_closed_policy_v1(value: BirthClosedPolicyV1) -> None:
    require_text_v1(value.schema, field="birth_closed_schema")
    require_text_v1(value.guard_version, field="birth_closed_guard_version")
    if type(value.authority) is not BirthAuthorityPolicyV1:
        raise ContractBoundaryPolicyError("boundary_policy_invalid:birth_authority")
    if type(value.roles) is not BoundaryRolePolicyV1:
        raise ContractBoundaryPolicyError("boundary_policy_invalid:birth_roles")
    closed_names_v1(value.exception_classes, field="exception_classes")
    justifications = dict(value.roles.exception_justification)
    if tuple(justifications) != value.exception_classes:
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:exception_justification_order"
        )
    grant_scopes = _validated_grant_scopes_v1(value, justifications)
    granted_classes = frozenset(
        grant.exception for grant in value.exception_grants
    )
    if granted_classes != frozenset(value.exception_classes):
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:exception_class_coverage"
        )
    authority_scopes = {
        value.authority.owner, *value.authority.coordinator_store_owners,
    }
    if len(authority_scopes) != 1 + len(value.authority.coordinator_store_owners):
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:birth_authority_scope_overlap"
        )
    if authority_scopes & grant_scopes:
        raise ContractBoundaryPolicyError(
            "boundary_policy_invalid:birth_exception_scope_overlap"
        )


BIRTH_CLOSED_POLICY_V1 = BirthClosedPolicyV1(
    schema=BIRTH_CLOSED_SCHEMA,
    guard_version=BIRTH_CLOSED_GUARD_VERSION,
    authority=BIRTH_AUTHORITY_POLICY_V1,
    roles=BOUNDARY_ROLE_POLICY_V1,
    exception_classes=BIRTH_EXCEPTION_CLASSES_V1,
    exception_grants=BIRTH_EXCEPTION_GRANTS_V1,
)


def birth_closed_inventory_value_v1() -> dict[str, object]:
    """Materialize the exact canonical inventory value from frozen facts."""
    value = BIRTH_CLOSED_POLICY_V1
    return {
        "schema": value.schema,
        "guard_version": value.guard_version,
        "owner": value.authority.owner,
        "coordinator_store_owners": list(value.authority.coordinator_store_owners),
        "sealed_modules": list(value.authority.sealed_modules),
        "exceptions": [
            {"scope": grant.scope, "exception": grant.exception}
            for grant in sorted(
                value.exception_grants, key=lambda item: item.scope.encode("utf-8"),
            )
        ],
    }


__all__ = [
    "BIRTH_CLOSED_POLICY_V1", "BirthClosedPolicyV1",
    "birth_closed_inventory_value_v1",
]
