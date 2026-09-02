"""Contracts for the immutable Birth-closed policy and its projections."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

import contract_boundary_birth_authority_policy as authority_policy
import contract_boundary_birth_exception_policy as exception_policy
import contract_boundary_birth_policy as birth_policy
import contract_boundary_guard as guard
import contract_boundary_policy as facade
import contract_boundary_policy_types as policy_types
import contract_boundary_projection as projection
import contract_boundary_role_policy as role_policy
import executor_birth_admin_preflight as standalone
import executor_birth_distribution_manifest as distribution_manifest


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_NAMES = (
    "BIRTH_CLOSED_SEALED_MODULES", "BIRTH_CLOSED_OWNER",
    "BIRTH_CLOSED_COORDINATOR_STORE_OWNERS",
    "BIRTH_CLOSED_LEGACY_CAPABILITIES", "BIRTH_CLOSED_EXCEPTIONS",
    "BIRTH_CLOSED_EXCEPTION_SCOPES", "BIRTH_CLOSED_EXCEPTION_CAPABILITIES",
    "VALID_ROLES", "LIVE_MUTATIONS", "DIRECT_MANIFEST_ALLOWED_ROLES",
    "DIRECT_MANIFEST_ALLOWED_PATHS", "BIRTH_OWNER_ALLOWED_PATHS",
    "BIRTH_OWNER_FORBIDDEN_CAPABILITIES",
    "OPERATIONAL_BIRTH_FORBIDDEN_CAPABILITIES", "BOOTSTRAP_CAPABILITIES",
    "BOOTSTRAP_ALLOWED_ROLES", "LIVE_MUTATION_ALLOWED_ROLES",
    "DOCUMENTATION_CAPABILITY_EXEMPTIONS",
    "BIRTH_CLOSED_EXCEPTION_JUSTIFICATIONS",
    "BIRTH_CLOSED_COORDINATOR_REQUIRED_CAPABILITIES",
)
POLICY_MODULE_PATHS = (
    "runtime/contract_boundary_api_policy.py",
    "runtime/contract_boundary_birth_authority_policy.py",
    "runtime/contract_boundary_birth_exception_policy.py",
    "runtime/contract_boundary_birth_policy.py",
    "runtime/contract_boundary_policy.py",
    "runtime/contract_boundary_policy_types.py",
    "runtime/contract_boundary_role_policy.py",
    "runtime/contract_boundary_syntax_policy.py",
)


def _assert_type_order_exact(left: object, right: object) -> None:
    assert type(left) is type(right)
    if type(left) is dict:
        assert tuple(left) == tuple(right)  # type: ignore[arg-type]
        for key in left:  # type: ignore[union-attr]
            _assert_type_order_exact(left[key], right[key])  # type: ignore[index]
    elif type(left) is tuple:
        assert len(left) == len(right)  # type: ignore[arg-type]
        for one, two in zip(left, right, strict=True):  # type: ignore[arg-type]
            _assert_type_order_exact(one, two)
    else:
        assert left == right


@pytest.mark.parametrize("name", PUBLIC_NAMES)
def test_facade_guard_and_standalone_preserve_public_type_value_order(
    name: str,
) -> None:
    expected = getattr(facade, name)
    _assert_type_order_exact(expected, getattr(guard, name))
    _assert_type_order_exact(expected, getattr(standalone, name))


def test_standalone_preserves_private_public_aliases_and_order() -> None:
    assert standalone.BIRTH_CLOSED_OWNER is standalone._BIRTH_CLOSED_OWNER
    assert standalone.BIRTH_CLOSED_SEALED_MODULES is (
        standalone._BIRTH_CLOSED_SEALED_MODULES
    )
    assert standalone.BIRTH_CLOSED_EXCEPTIONS == (
        standalone._BIRTH_CLOSED_EXCEPTIONS
    )
    assert standalone.BIRTH_CLOSED_EXCEPTIONS is not (
        standalone._BIRTH_CLOSED_EXCEPTIONS
    )
    assert standalone.VALID_ROLES == standalone._BOUNDARY_ROLES
    assert standalone.VALID_ROLES is not standalone._BOUNDARY_ROLES
    assert standalone._BOUNDARY_ENTRY_KEYS == facade.BOUNDARY_ENTRY_KEYS
    assert tuple(standalone.BIRTH_CLOSED_EXCEPTION_SCOPES) == tuple(
        grant.scope for grant in exception_policy.BIRTH_EXCEPTION_GRANTS_V1
    )
    assert standalone._BIRTH_CLOSED_EXCEPTION_SCOPES == tuple(sorted(
        standalone.BIRTH_CLOSED_EXCEPTION_SCOPES.items(),
    ))
    assert len(exception_policy.BIRTH_EXCEPTION_GRANTS_V1) == 16
    authority_scopes = {
        facade.BIRTH_CLOSED_OWNER, *facade.BIRTH_CLOSED_COORDINATOR_STORE_OWNERS,
    }
    assert authority_scopes.isdisjoint(facade.BIRTH_CLOSED_EXCEPTION_SCOPES)


def test_all_policy_owners_are_required_release_runtime_files() -> None:
    assert tuple(standalone._REQUIRED_MANIFEST_PATHS.items()) == tuple(
        distribution_manifest._REQUIRED_PATH_ROLES.items()
    )
    for path in POLICY_MODULE_PATHS:
        assert standalone._REQUIRED_MANIFEST_PATHS[path] == "runtime_code"
        assert distribution_manifest._REQUIRED_PATH_ROLES[path] == "runtime_code"


def test_inventory_materializer_is_pure_and_identical_in_all_consumers() -> None:
    first = birth_policy.birth_closed_inventory_value_v1()
    second = birth_policy.birth_closed_inventory_value_v1()
    assert first == second and first is not second
    first["exceptions"].append({"scope": "mutant", "exception": "mutant"})
    assert birth_policy.birth_closed_inventory_value_v1() == second
    assert facade.birth_closed_inventory_value_v1() == second
    assert guard.birth_closed_inventory_value_v1() == second
    assert standalone.birth_closed_inventory_value_v1() == second


def test_authoring_records_are_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        birth_policy.BIRTH_CLOSED_POLICY_V1.schema = "mutant"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        role_policy.BOUNDARY_ROLE_POLICY_V1.valid_roles = ()  # type: ignore[misc]


def _valid_inventory_v1() -> dict[str, object]:
    entries = []
    rows = [(facade.BIRTH_CLOSED_OWNER, "birth_owner", ("birth",), None)]
    rows.extend(
        (scope, "store_owner", ("store_write",), None)
        for scope in sorted(facade.BIRTH_CLOSED_COORDINATOR_STORE_OWNERS)
    )
    rows.extend(
        (
            scope, "offline_authoring",
            tuple(sorted(facade.BIRTH_CLOSED_EXCEPTION_CAPABILITIES[scope])),
            exception,
        )
        for scope, exception in facade.BIRTH_CLOSED_EXCEPTION_SCOPES.items()
    )
    for key, role, capabilities, exception in rows:
        path, scope = key.split(":", 1)
        entry = {
            "path": path, "scope": scope, "role": role,
            "capabilities": list(capabilities), "destination": "test",
            "phase": "M4",
        }
        if exception is not None:
            entry["closed_exception"] = exception
        entries.append(entry)
    return {
        "schema": facade.SCHEMA,
        "source_census": standalone._BIRTH_CLOSED_SOURCE_REVIEW_SHA256,
        "scan_roots": list(facade.SCAN_ROOTS), "entries": entries,
        "birth_closed": birth_policy.birth_closed_inventory_value_v1(),
    }


def test_private_checker_accepts_owner_value_in_isolated_python(
    tmp_path: Path,
) -> None:
    encoded = standalone._canonical_json(_valid_inventory_v1())
    assert standalone._validate_boundary_inventory_v1(encoded)["birth_closed"] == (
        birth_policy.birth_closed_inventory_value_v1()
    )
    inventory = tmp_path / "boundary.json"
    inventory.write_bytes(encoded)
    preflight = ROOT / "runtime" / "executor_birth_admin_preflight.py"
    code = (
        "import runpy;"
        f"d=runpy.run_path({str(preflight)!r});"
        f"v=d['_validate_boundary_inventory_v1'](open({str(inventory)!r},'rb').read());"
        "assert v['birth_closed']==d['birth_closed_inventory_value_v1']();"
        "assert d['BIRTH_CLOSED_OWNER'] is d['_BIRTH_CLOSED_OWNER'];"
        "assert d['BIRTH_CLOSED_SEALED_MODULES'] is d['_BIRTH_CLOSED_SEALED_MODULES']"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_projection_ignores_mutable_birth_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = projection.render_generated_region_v1()
    monkeypatch.setattr(facade, "BIRTH_CLOSED_OWNER", "mutable-probe")
    monkeypatch.setitem(
        facade.BIRTH_CLOSED_EXCEPTION_SCOPES, "mutable:probe", "retirement_only",
    )
    assert projection.render_generated_region_v1() == expected


@pytest.mark.parametrize("value", ("bad\0text", "\0"))
def test_policy_text_rejects_nul(value: str) -> None:
    with pytest.raises(policy_types.ContractBoundaryPolicyError):
        policy_types.require_text_v1(value, field="probe")
    with pytest.raises(policy_types.ContractBoundaryPolicyError):
        policy_types.closed_names_v1((value,), field="probe")


def test_owner_path_must_be_sealed() -> None:
    with pytest.raises(
        policy_types.ContractBoundaryPolicyError, match="birth_owner_not_sealed",
    ):
        authority_policy.BirthAuthorityPolicyV1(
            "runtime/owner.py:birth", ("runtime/sealed.py",), ("scope:a",),
        )


@pytest.mark.parametrize("scope", ("missing-separator", "runtime/owner.py:"))
def test_authority_scope_keys_must_be_complete(scope: str) -> None:
    with pytest.raises(policy_types.ContractBoundaryPolicyError):
        authority_policy.BirthAuthorityPolicyV1(
            "runtime/owner.py:birth", ("runtime/owner.py",), (scope,),
        )


def _mutated_policy(**changes: object) -> birth_policy.BirthClosedPolicyV1:
    return replace(birth_policy.BIRTH_CLOSED_POLICY_V1, **changes)


def test_owner_coordinator_and_exception_scopes_must_be_disjoint() -> None:
    owner = "runtime/owner.py:birth"
    overlapping = authority_policy.BirthAuthorityPolicyV1(
        owner, ("runtime/owner.py",), (owner,),
    )
    with pytest.raises(policy_types.ContractBoundaryPolicyError, match="overlap"):
        _mutated_policy(authority=overlapping)
    grant_scope = birth_policy.BIRTH_CLOSED_POLICY_V1.exception_grants[0].scope
    coordinator_overlap = authority_policy.BirthAuthorityPolicyV1(
        owner, ("runtime/owner.py",), (grant_scope,),
    )
    with pytest.raises(policy_types.ContractBoundaryPolicyError, match="overlap"):
        _mutated_policy(authority=coordinator_overlap)


def test_exception_grants_reject_duplicates_unknown_class_and_extra_legacy() -> None:
    grants = birth_policy.BIRTH_CLOSED_POLICY_V1.exception_grants
    with pytest.raises(policy_types.ContractBoundaryPolicyError, match="duplicate"):
        _mutated_policy(exception_grants=(grants[0], grants[0]))
    unknown = replace(grants[0], exception="unknown")
    with pytest.raises(
        policy_types.ContractBoundaryPolicyError, match="class_reference",
    ):
        _mutated_policy(exception_grants=(unknown,))
    overbroad = replace(grants[0], capabilities=("retire", "sign"))
    with pytest.raises(
        policy_types.ContractBoundaryPolicyError, match="justification",
    ):
        _mutated_policy(exception_grants=(overbroad,))
    unknown = replace(grants[0], capabilities=("sign", "unknown_capability"))
    with pytest.raises(
        policy_types.ContractBoundaryPolicyError, match="capability_unknown",
    ):
        _mutated_policy(exception_grants=(unknown, *grants[1:]))


def test_every_exception_class_must_have_a_grant() -> None:
    grants = tuple(
        grant for grant in birth_policy.BIRTH_CLOSED_POLICY_V1.exception_grants
        if grant.exception != "localization_only"
    )
    with pytest.raises(
        policy_types.ContractBoundaryPolicyError, match="class_coverage",
    ):
        _mutated_policy(exception_grants=grants)


def test_role_matrices_reject_noncanonical_capability_order() -> None:
    with pytest.raises(policy_types.ContractBoundaryPolicyError, match="order"):
        replace(
            role_policy.BOUNDARY_ROLE_POLICY_V1,
            bootstrap_capabilities=("z", "a"),
        )


@pytest.mark.parametrize(
    "field",
    (
        "birth_owner_forbidden", "operational_birth_forbidden",
        "bootstrap_capabilities", "live_mutations", "documentation_exemptions",
        "coordinator_capabilities", "birth_closed_legacy_capabilities",
    ),
)
def test_role_capability_matrices_reject_unknown_names(field: str) -> None:
    with pytest.raises(policy_types.ContractBoundaryPolicyError, match="unknown"):
        replace(role_policy.BOUNDARY_ROLE_POLICY_V1, **{field: ("unknown",)})


def _function_node(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


@pytest.mark.parametrize(
    ("relative", "functions"),
    (
        ("runtime/contract_boundary_guard.py", (
            "check", "birth_closed_findings", "render_birth_closed_inventory",
        )),
        ("runtime/executor_birth_admin_preflight.py", (
            "check", "birth_closed_findings", "_validate_boundary_inventory_v1",
        )),
        ("runtime/executor_birth_distribution_manifest.py", (
            "_canonical_inventory",
        )),
    ),
)
def test_consumers_have_no_inline_policy_set_or_justification_literal(
    relative: str, functions: tuple[str, ...],
) -> None:
    path = ROOT / relative
    for name in functions:
        node = _function_node(path, name)
        sets = [item for item in ast.walk(node) if isinstance(item, ast.Set)]
        string_sets = [
            item for item in sets
            if any(isinstance(child, ast.Constant) and type(child.value) is str
                   for child in item.elts)
        ]
        if name == "_validate_boundary_inventory_v1":
            string_sets = [
                item for item in string_sets
                if {child.value for child in item.elts if isinstance(child, ast.Constant)}
                not in ({"schema", "source_census", "scan_roots", "entries", "birth_closed"},
                        {"scope", "exception"}, {"closed_exception"})
            ]
        assert not string_sets
        constants = {
            item.value for item in ast.walk(node)
            if isinstance(item, ast.Constant) and type(item.value) is str
        }
        assert "offline_nonproductive_authoring" not in constants
        assert "runtime/executor_birth_authoring.py" not in constants


def test_policy_owner_modules_are_pure_and_within_size_contract() -> None:
    names = (
        "contract_boundary_policy_types.py", "contract_boundary_role_policy.py",
        "contract_boundary_birth_authority_policy.py",
        "contract_boundary_birth_exception_policy.py",
        "contract_boundary_birth_policy.py",
    )
    for name in names:
        source = (ROOT / "runtime" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert len(source.splitlines()) <= 400
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 40
        imported = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or "" for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not imported & {"os", "pathlib", "subprocess", "systemd"}
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not calls & {"open", "eval", "exec"}
