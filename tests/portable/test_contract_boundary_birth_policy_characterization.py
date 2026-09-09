"""Behavioral baseline for the residual Birth-closed policy extraction."""
from __future__ import annotations

import hashlib
import json

import pytest

import contract_boundary_guard as guard
import executor_birth_admin_preflight as standalone


POLICY_NAMES = (
    "BIRTH_CLOSED_SEALED_MODULES", "BIRTH_CLOSED_OWNER",
    "BIRTH_CLOSED_COORDINATOR_STORE_OWNERS",
    "BIRTH_CLOSED_LEGACY_CAPABILITIES", "BIRTH_CLOSED_EXCEPTIONS",
    "BIRTH_CLOSED_EXCEPTION_SCOPES", "BIRTH_CLOSED_EXCEPTION_CAPABILITIES",
    "VALID_ROLES", "LIVE_MUTATIONS",
)
POLICY_SHA256 = "b25b2c5da9b4895705e770ff5169e7142e8d6b95dceda3acb362cd87450507d1"
INVENTORY_SHA256 = "85586f340e8ac34941858dc11f64ea1aa9b998017eb1ebd81b1e351783117020"
FINDINGS_SHA256 = "c808ae0c09b7b608d8a6b1bbe1902139d2da08e40ac76868e36919650c7d7d64"

DIRECT_MANIFEST_ROLES = frozenset({
    "migration_boundary", "offline_authoring", "store_owner",
})
BIRTH_OWNER_PATHS = frozenset({
    "runtime/executor_birth.py", "runtime/executor_birth_intent.py",
    "runtime/executor_birth_operational.py",
})
BIRTH_OWNER_FORBIDDEN = frozenset({
    "legacy_bootstrap", "publish_bootstrap", "publish_localization",
    "retire", "rollback", "sign",
})
OPERATIONAL_BIRTH_FORBIDDEN = frozenset({
    "publish_technical", "reactivate", "sign",
})
BOOTSTRAP_CAPABILITIES = frozenset({"legacy_bootstrap", "publish_bootstrap"})
BOOTSTRAP_ROLES = frozenset({"migration_boundary", "store_owner"})
LIVE_MUTATION_ROLES = frozenset({
    "administrative_tool", "birth_owner", "migration_boundary",
    "operational_producer", "store_owner",
})
DOCUMENTATION_EXEMPTIONS = frozenset({"authoring_read", "authoring_verify"})
EXCEPTION_JUSTIFICATION = {
    "localization_only": frozenset({"publish_localization"}),
    "retirement_only": frozenset({"retire"}),
    "offline_nonproductive_authoring": frozenset({"sign"}),
}


def _tagged(value: object) -> object:
    if type(value) is dict:
        return ["dict", [[key, _tagged(item)] for key, item in value.items()]]
    if type(value) is tuple:
        return ["tuple", [_tagged(item) for item in value]]
    if type(value) is frozenset:
        return ["frozenset", sorted((_tagged(item) for item in value), key=repr)]
    return [type(value).__name__, value]


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _fact(
    *, path: str = "runtime/probe.py", role: str,
    capabilities: tuple[str, ...], direct: bool = False,
):
    fact = guard.ScopeFacts(
        path, "probe", 1, tuple(sorted(capabilities)), (), direct, False,
    )
    inventory = {
        "schema": guard.SCHEMA,
        "scan_roots": list(guard.SCAN_ROOTS),
        "entries": [{
            "path": path, "scope": "probe", "role": role,
            "capabilities": list(fact.capabilities),
            "destination": "characterization", "phase": "M4",
        }],
    }
    return fact, inventory


def _codes(engine, fact, inventory) -> set[str]:
    return {item.code for item in engine.check((fact,), inventory)}


def test_nine_policy_families_preserve_type_value_and_order() -> None:
    snapshot = []
    for name in POLICY_NAMES:
        imported = getattr(guard, name)
        embedded = getattr(standalone, name)
        assert _tagged(imported) == _tagged(embedded)
        snapshot.append([name, _tagged(imported)])
    assert _sha256(snapshot) == POLICY_SHA256
    assert standalone.BIRTH_CLOSED_SEALED_MODULES is standalone._BIRTH_CLOSED_SEALED_MODULES
    assert standalone.BIRTH_CLOSED_OWNER is standalone._BIRTH_CLOSED_OWNER
    assert type(standalone._BIRTH_CLOSED_COORDINATOR_STORE_OWNERS) is tuple
    assert type(standalone._BIRTH_CLOSED_EXCEPTION_SCOPES) is tuple


def test_inventory_and_findings_outputs_are_frozen() -> None:
    rendered = guard.render_birth_closed_inventory(())
    inventory = json.loads(rendered)
    normalized = dict(inventory, source_census="sha256:" + "0" * 64)
    normalized_bytes = (
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    assert hashlib.sha256(normalized_bytes).hexdigest() == INVENTORY_SHA256
    imported = guard.birth_closed_findings((), inventory)
    embedded = standalone.birth_closed_findings((), inventory)
    first = [(item.code, item.scope, item.message) for item in imported]
    second = [(item.code, item.scope, item.message) for item in embedded]
    assert first == second and len(first) == 162
    assert _sha256(first) == FINDINGS_SHA256


@pytest.mark.parametrize("role", sorted(guard.VALID_ROLES))
def test_direct_manifest_role_matrix(role: str) -> None:
    fact, inventory = _fact(
        role=role, capabilities=("authoring_read",), direct=True,
    )
    expected = role not in DIRECT_MANIFEST_ROLES
    for engine in (guard, standalone):
        assert ("direct_manifest_dir_read_without_token" in _codes(
            engine, fact, inventory,
        )) is expected


def test_direct_manifest_dedicated_reader_path() -> None:
    fact, inventory = _fact(
        path="runtime/executor_birth_authoring.py",
        role="administrative_tool", capabilities=("authoring_read",), direct=True,
    )
    assert "direct_manifest_dir_read_without_token" not in _codes(
        guard, fact, inventory,
    )


@pytest.mark.parametrize("path", [*sorted(BIRTH_OWNER_PATHS), "runtime/probe.py"])
def test_birth_owner_path_matrix(path: str) -> None:
    fact, inventory = _fact(role="birth_owner", path=path, capabilities=("birth",))
    expected = path not in BIRTH_OWNER_PATHS
    for engine in (guard, standalone):
        assert ("birth_owner_invalid" in _codes(engine, fact, inventory)) is expected


@pytest.mark.parametrize("capability", sorted(BIRTH_OWNER_FORBIDDEN))
def test_birth_owner_forbidden_capability_matrix(capability: str) -> None:
    fact, inventory = _fact(
        role="birth_owner", path="runtime/executor_birth.py",
        capabilities=("birth", capability),
    )
    for engine in (guard, standalone):
        assert "birth_owner_invalid" in _codes(engine, fact, inventory)


@pytest.mark.parametrize("capability", sorted(OPERATIONAL_BIRTH_FORBIDDEN))
def test_operational_birth_forbidden_matrix(capability: str) -> None:
    fact, inventory = _fact(
        role="operational_producer", capabilities=("birth", capability),
    )
    for engine in (guard, standalone):
        assert "operational_birth_mixed_authority" in _codes(
            engine, fact, inventory,
        )


@pytest.mark.parametrize("capability", sorted(BOOTSTRAP_CAPABILITIES))
@pytest.mark.parametrize("role", sorted(guard.VALID_ROLES))
def test_bootstrap_role_matrix(role: str, capability: str) -> None:
    fact, inventory = _fact(role=role, capabilities=(capability,))
    expected = role not in BOOTSTRAP_ROLES
    for engine in (guard, standalone):
        assert ("legacy_bootstrap_outside_boundary" in _codes(
            engine, fact, inventory,
        )) is expected


@pytest.mark.parametrize("capability", sorted(guard.LIVE_MUTATIONS))
@pytest.mark.parametrize("role", sorted(guard.VALID_ROLES))
def test_live_mutation_role_matrix(role: str, capability: str) -> None:
    fact, inventory = _fact(role=role, capabilities=(capability,))
    expected = role not in LIVE_MUTATION_ROLES
    for engine in (guard, standalone):
        assert ("live_mutation_role_invalid" in _codes(
            engine, fact, inventory,
        )) is expected


@pytest.mark.parametrize("capability", sorted(guard.LIVE_READER_FORBIDDEN))
def test_documentation_exemption_matrix(capability: str) -> None:
    fact, inventory = _fact(role="documentation", capabilities=(capability,))
    expected = capability not in DOCUMENTATION_EXEMPTIONS
    for engine in (guard, standalone):
        assert ("documentation_mutates_boundary" in _codes(
            engine, fact, inventory,
        )) is expected


def test_exception_justification_matrix_matches_grants() -> None:
    assert set(guard.BIRTH_CLOSED_EXCEPTIONS) == set(EXCEPTION_JUSTIFICATION)
    for scope, exception in guard.BIRTH_CLOSED_EXCEPTION_SCOPES.items():
        capabilities = guard.BIRTH_CLOSED_EXCEPTION_CAPABILITIES[scope]
        assert capabilities & EXCEPTION_JUSTIFICATION[exception]
