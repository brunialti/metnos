"""R1 and G6 acceptance cells owned by the platform-independent activity."""
from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import os
import shutil
import subprocess
import sys
import textwrap
import weakref
from enum import Enum
from pathlib import Path

import pytest

from .certification_v1 import (
    ACTIVITIES,
    CertificationError,
    FINAL_EVIDENCE_FIELDS,
    INVENTORY_PATH,
    MANIFEST_PATH,
    REPO_ROOT,
    SUITE_ID,
    WORKFLOW_PATH,
    canonical_json_bytes,
    collect_a_node_ids,
    digest_file,
    _test_python_files,
    select_cells,
    validate_clean_tracked_worktree,
    validate_collection,
    validate_final_evidence,
    validate_manifest,
    validate_no_skip_xfail,
    validate_production_inventory,
    validate_productive_mutation_graph,
    validate_snapshot_aggregate,
    validate_workflow_structure,
)
from ._support import (
    exact_role_catalog,
    lock_role_binding,
    open_session,
    role_binding,
    tree_snapshot,
)
from .generate_production_inventory_v1 import build_production_inventory_v1
from .required_cells_v1 import EXPECTED_ACTIVITY_COUNTS_V1, REQUIRED_CELLS_V1


_BIRTH_ROLE_PATTERN_NAMES_V1 = (
    "birth_root",
    "global_lock",
    "transaction_root",
    "transaction_header",
    "transaction_header_pending",
    "transaction_prepared",
    "transaction_checkpoints",
    "transaction_checkpoint",
    "transaction_checkpoint_pending",
    "transaction_author_store",
    "transaction_authority_set",
    "final_author_store",
    "authority_sets",
    "final_authority_set",
    "final_prepared",
    "set_document",
    "admission_store",
    "producers_container",
    "producer_store",
    "approval_container",
    "approval_authority",
    "semantic_container",
    "semantic_authority",
    "semantic_public_container",
    "semantic_public_key",
    "semantic_evidence_container",
    "semantic_evidence_record",
    "context_container",
    "context_material",
    "keystore_config",
    "keystore_lock",
    "keystore_private_container",
    "keystore_private_key",
    "keystore_public_container",
    "keystore_public_key",
    "operator_input",
    "operator_approval",
    "operator_semantic",
    "operator_semantic_public",
    "operator_semantic_public_key",
    "payload_pending",
)


def _assert_common_creation_role_resolution_v1(source: str) -> None:
    tree = ast.parse(textwrap.dedent(source).strip())
    functions = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    if len(functions) != 1:
        raise AssertionError("creation source must contain exactly one function")
    function = functions[0]
    if any(isinstance(node, ast.Lambda) for node in ast.walk(function)):
        raise AssertionError("creation authority cannot be hidden in a lambda")
    parents = {
        id(child): parent
        for parent in ast.walk(function)
        for child in ast.iter_child_nodes(parent)
    }
    resolutions = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None)
        == "_resolve_effective_role_binding_v1"
    ]
    if len(resolutions) != 1:
        raise AssertionError("creation must use the one effective role resolver")
    resolution = resolutions[0]
    reservations = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None)
        == "_reserve_exact_role_binding_v1"
    ]
    if len(reservations) != 1:
        raise AssertionError("creation must use the one exact-binding reservation")
    reservation = reservations[0]
    reservation_with = next(
        (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.With)
            and any(item.context_expr is reservation for item in node.items)
        ),
        None,
    )
    if reservation_with is None or reservation_with not in function.body:
        raise AssertionError("reservation must be an unconditional function-body scope")
    binding_assignments = [
        statement
        for statement in function.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and isinstance(statement.value, ast.Call)
        and getattr(statement.value.func, "id", None) == "_BirthRoleBindingV1"
    ]
    if len(binding_assignments) != 1:
        raise AssertionError("creation must construct one complete requested binding")
    binding_assignment = binding_assignments[0]
    binding_name = binding_assignment.targets[0].id
    binding_inputs = tuple(ast.walk(binding_assignment.value))
    if not (
        len(reservation.args) == 1
        and isinstance(reservation.args[0], ast.Name)
        and reservation.args[0].id == binding_name
        and not reservation.keywords
        and any(isinstance(node, ast.Name) and node.id == "components" for node in binding_inputs)
        and any(isinstance(node, ast.Name) and node.id == "role" for node in binding_inputs)
        and any(
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_ObjectKind"
            for node in binding_inputs
        )
    ):
        raise AssertionError("reservation must receive the complete requested binding")
    resolution_statement = parents.get(id(resolution))
    if not (
        isinstance(resolution_statement, ast.Assign)
        and resolution_statement.value is resolution
        and len(resolution_statement.targets) == 1
        and isinstance(resolution_statement.targets[0], ast.Name)
        and resolution_statement in reservation_with.body
    ):
        raise AssertionError("effective binding must be captured directly inside reservation")
    effective_name = resolution_statement.targets[0].id
    if not (
        len(resolution.args) == 1
        and isinstance(resolution.args[0], ast.Name)
        and resolution.args[0].id == "components"
        and not resolution.keywords
    ):
        raise AssertionError("effective resolver must receive the canonical components")
    if reservation.lineno >= resolution.lineno:
        raise AssertionError("effective binding must be resolved inside its reservation")
    consumed_bindings = [
        node
        for node in ast.walk(reservation_with)
        if isinstance(node, ast.Attribute)
        and node.attr == "binding"
        and isinstance(node.value, ast.Name)
        and node.value.id == effective_name
        and node.lineno > resolution.lineno
    ]
    if not consumed_bindings:
        raise AssertionError("creation must consume the resolved binding")
    if any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id == "role"
        and node.lineno > resolution.lineno
        for node in ast.walk(reservation_with)
    ):
        raise AssertionError("raw requested role is used after authoritative resolution")
    allowed_pre_reservation_calls = {
        "_BirthRoleBindingV1",
        "_ObjectKind",
        "_relative_components",
        "_require_exclusive_global_lock",
        "_require_exclusive_global_lock_v1",
        "_require_open",
        "_validate_components_v1",
        "tuple",
    }
    for statement in function.body:
        if statement is reservation_with:
            break
        for call in (
            node for node in ast.walk(statement) if isinstance(node, ast.Call)
        ):
            call_name = (
                call.func.attr
                if isinstance(call.func, ast.Attribute)
                else call.func.id
                if isinstance(call.func, ast.Name)
                else ""
            )
            if call_name not in allowed_pre_reservation_calls:
                raise AssertionError(
                    "creation performs a call before exact-binding reservation"
                )
    boundaries = [
        node
        for node in ast.walk(function)
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "_directory_chain"
        )
        or (
            isinstance(node, (ast.If, ast.IfExp))
            and any(
                isinstance(candidate, ast.Attribute)
                and isinstance(candidate.value, ast.Name)
                and candidate.value.id == "os"
                and candidate.attr == "name"
                for candidate in ast.walk(node.test)
            )
        )
    ]
    if not boundaries or any(resolution.lineno >= boundary.lineno for boundary in boundaries):
        raise AssertionError(
            "creation resolves its reserved binding after traversal or platform dispatch"
        )
    current = parents.get(id(resolution))
    while current is not None and current is not reservation_with:
        if isinstance(current, (ast.If, ast.IfExp, ast.Match)):
            raise AssertionError("creation role resolution is platform-conditional")
        current = parents.get(id(current))
    if current is not reservation_with:
        raise AssertionError("creation role resolution escaped reservation scope")


def _productive_catalog_cases_v1() -> tuple[tuple[tuple[str, ...], bool, str], ...]:
    transaction_id = "1" * 32
    transaction = (f".birth-provisioning-v1.txn.{transaction_id}",)
    set_id = "2" * 64
    producer = "p-" + "3" * 64
    key_id = "birth-ed25519-v1-sha256-" + "4" * 64
    sequence = "00000000000000000000"
    maximum_sequence = "00000000000000008191"
    final_set = ("authority-sets", set_id)
    staged_set = transaction + ("authority-set",)
    cases: list[tuple[tuple[str, ...], bool, str]] = [
        ((), True, "birth_integrity_only"),
        (("provisioning-v1.lock",), False, "birth_integrity_only"),
        (transaction, True, "birth_integrity_only"),
        (transaction + ("transaction-v1.json",), False, "birth_integrity_only"),
        (
            transaction + (f".transaction-v1.pending.{transaction_id}",),
            False,
            "birth_integrity_only",
        ),
        (transaction + ("prepared-v1.json",), False, "birth_integrity_only"),
        (transaction + ("checkpoints-v1",), True, "birth_integrity_only"),
        (
            transaction + ("checkpoints-v1", f"{sequence}.json"),
            False,
            "birth_integrity_only",
        ),
        (
            transaction + ("checkpoints-v1", f"{maximum_sequence}.json"),
            False,
            "birth_integrity_only",
        ),
        (
            transaction
            + (
                "checkpoints-v1",
                f".checkpoint-pending-{sequence}-{transaction_id}",
            ),
            False,
            "birth_integrity_only",
        ),
        (transaction + ("author-root-v1",), True, "birth_confidential"),
        (staged_set, True, "birth_integrity_only"),
        (("author-root-v1",), True, "birth_confidential"),
        (("authority-sets",), True, "birth_integrity_only"),
        (final_set, True, "birth_integrity_only"),
        (("prepared-v1.json",), False, "birth_integrity_only"),
        (("operator-input-v1",), True, "birth_integrity_only"),
        (
            ("operator-input-v1", "approval-authority.json"),
            False,
            "birth_integrity_only",
        ),
        (
            ("operator-input-v1", "semantic-authority.json"),
            False,
            "birth_integrity_only",
        ),
        (
            ("operator-input-v1", "semantic-public"),
            True,
            "birth_integrity_only",
        ),
        (
            ("operator-input-v1", "semantic-public", "review-key.pub"),
            False,
            "birth_integrity_only",
        ),
    ]
    for authority_root in (final_set, staged_set):
        cases.extend(
            (
                (authority_root + ("set.json",), False, "birth_integrity_only"),
                (authority_root + ("admission",), True, "birth_confidential"),
                (authority_root + ("producers",), True, "birth_integrity_only"),
                (
                    authority_root + ("producers", producer),
                    True,
                    "birth_confidential",
                ),
                (authority_root + ("approval",), True, "birth_integrity_only"),
                (
                    authority_root + ("approval", "authority.json"),
                    False,
                    "birth_integrity_only",
                ),
                (authority_root + ("semantic",), True, "birth_integrity_only"),
                (
                    authority_root + ("semantic", "authority.json"),
                    False,
                    "birth_integrity_only",
                ),
                (
                    authority_root + ("semantic", "public"),
                    True,
                    "birth_integrity_only",
                ),
                (
                    authority_root + ("semantic", "public", "review-key.pub"),
                    False,
                    "birth_integrity_only",
                ),
                (
                    authority_root + ("semantic", "evidence"),
                    True,
                    "birth_integrity_only",
                ),
                (
                    authority_root
                    + ("semantic", "evidence", "review-evidence.json"),
                    False,
                    "birth_integrity_only",
                ),
                (authority_root + ("context",), True, "birth_integrity_only"),
                (
                    authority_root + ("context", "material-v1.json"),
                    False,
                    "birth_integrity_only",
                ),
            )
        )
    keystore_anchors = (
        ("author-root-v1",),
        transaction + ("author-root-v1",),
        final_set + ("admission",),
        staged_set + ("admission",),
        final_set + ("producers", producer),
        staged_set + ("producers", producer),
    )
    for anchor in keystore_anchors:
        cases.extend(
            (
                (anchor + ("keystore.json",), False, "birth_confidential"),
                (anchor + ("birth-keystore.lock",), False, "birth_confidential"),
                (anchor + ("private",), True, "birth_confidential"),
                (anchor + ("private", f"{key_id}.key"), False, "birth_confidential"),
                (anchor + ("public",), True, "birth_integrity_only"),
                (anchor + ("public", f"{key_id}.pub"), False, "birth_integrity_only"),
            )
        )
    pending = f".payload-pending-{sequence}-{transaction_id}"
    classified_transaction_directories = tuple(
        (components, role_name)
        for components, directory, role_name in cases
        if directory
        and len(components) > len(transaction)
        and components[: len(transaction)] == transaction
    )
    cases.extend(
        (components + (pending,), False, role_name)
        for components, role_name in classified_transaction_directories
    )
    return tuple(cases)


def _productive_catalog_case_patterns_v1() -> tuple[str, ...]:
    initial = (
        "birth_root",
        "global_lock",
        "transaction_root",
        "transaction_header",
        "transaction_header_pending",
        "transaction_prepared",
        "transaction_checkpoints",
        "transaction_checkpoint",
        "transaction_checkpoint",
        "transaction_checkpoint_pending",
        "transaction_author_store",
        "transaction_authority_set",
        "final_author_store",
        "authority_sets",
        "final_authority_set",
        "final_prepared",
        "operator_input",
        "operator_approval",
        "operator_semantic",
        "operator_semantic_public",
        "operator_semantic_public_key",
    )
    authority = (
        "set_document",
        "admission_store",
        "producers_container",
        "producer_store",
        "approval_container",
        "approval_authority",
        "semantic_container",
        "semantic_authority",
        "semantic_public_container",
        "semantic_public_key",
        "semantic_evidence_container",
        "semantic_evidence_record",
        "context_container",
        "context_material",
    )
    keystore = (
        "keystore_config",
        "keystore_lock",
        "keystore_private_container",
        "keystore_private_key",
        "keystore_public_container",
        "keystore_public_key",
    )
    base_patterns = (
        *initial,
        *(name for _ in range(2) for name in authority),
        *(name for _ in range(6) for name in keystore),
    )
    pending_count = sum(
        1
        for components, _directory, _role in _productive_catalog_cases_v1()
        if components and components[-1].startswith(".payload-pending-")
    )
    return (*base_patterns, *("payload_pending" for _ in range(pending_count)))


def _assert_productive_catalog_matcher_v1(secure_fs) -> None:
    pattern_type = secure_fs._BirthRolePatternV1
    assert issubclass(pattern_type, str) and issubclass(pattern_type, Enum)
    assert tuple(item.name for item in pattern_type) == _BIRTH_ROLE_PATTERN_NAMES_V1
    assert tuple(item.value for item in pattern_type) == _BIRTH_ROLE_PATTERN_NAMES_V1
    catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(pattern_type),
        exact_bindings=(),
        generation=0,
    )
    assert [field.name for field in dataclasses.fields(catalog)] == [
        "schema_version",
        "patterns",
        "exact_bindings",
        "generation",
    ]
    assert not hasattr(catalog, "__dict__")
    assert tuple(inspect.signature(type(catalog)._resolve_binding_v1).parameters) == (
        "self",
        "components",
    )
    resolver = catalog._resolve_binding_v1
    cases = _productive_catalog_cases_v1()
    case_patterns = _productive_catalog_case_patterns_v1()
    transaction_id = "a" * 32
    transaction = (f".birth-provisioning-v1.txn.{transaction_id}",)
    final_set = ("authority-sets", "b" * 64)
    staged_set = transaction + ("authority-set",)
    producer_prefix = final_set + ("producers",)
    private_prefix = ("author-root-v1", "private")
    assert len(cases) == len(case_patterns)
    assert set(case_patterns) == set(_BIRTH_ROLE_PATTERN_NAMES_V1)
    representatives: dict[str, tuple[str, ...]] = {}
    for pattern_name, (components, directory, role_name) in zip(
        case_patterns, cases, strict=True
    ):
        representatives.setdefault(pattern_name, components)
        binding = resolver(components)
        assert binding.components == components
        assert binding.kind == secure_fs._ObjectKind(
            "directory" if directory else "regular_file"
        )
        assert binding.role == secure_fs._BirthObjectRole(role_name)
    boundary_cases = (
        (
            final_set + ("semantic", "public", "A_B.pub"),
            "birth_integrity_only",
        ),
        (
            final_set + ("semantic", "evidence", "A_B.json"),
            "birth_integrity_only",
        ),
        (
            final_set + ("semantic", "public", "x" * 124 + ".pub"),
            "birth_integrity_only",
        ),
        (
            final_set + ("semantic", "evidence", "x" * 123 + ".json"),
            "birth_integrity_only",
        ),
    )
    for components, role_name in boundary_cases:
        binding = resolver(components)
        assert binding.kind == secure_fs._ObjectKind("regular_file")
        assert binding.role == secure_fs._BirthObjectRole(role_name)

    concordant_components, concordant_directory, concordant_role = cases[0]
    concordant_binding = secure_fs._BirthRoleBindingV1(
        components=concordant_components,
        kind=secure_fs._ObjectKind(
            "directory" if concordant_directory else "regular_file"
        ),
        role=secure_fs._BirthObjectRole(concordant_role),
    )
    concordant_catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(pattern_type),
        exact_bindings=(concordant_binding,),
        generation=0,
    )
    assert concordant_catalog._resolve_binding_v1(
        concordant_components
    ) == concordant_binding
    for pattern_name, components in representatives.items():
        reduced_patterns = tuple(
            item for item in pattern_type if item.name != pattern_name
        )
        try:
            reduced = secure_fs._BirthRoleCatalogV1(
                schema_version=1,
                patterns=reduced_patterns,
                exact_bindings=(),
                generation=0,
            )
        except secure_fs.BirthSecureFSError:
            continue
        with pytest.raises(secure_fs.BirthSecureFSError):
            reduced._resolve_binding_v1(components)

    invalid_catalog_paths = (
        ("unknown",),
        (f".birth-provisioning-v1.txn.{transaction_id.upper()}",),
        (f".birth-provisioning-v1.txn.{'a' * 31}",),
        (f".birth-provisioning-v1.txn.{'a' * 33}",),
        (f".birth-provisioning-v1.txn.{'a' * 31}g",),
        transaction + (f".transaction-v1.pending.{'9' * 32}",),
        ("authority-sets", "b" * 63),
        ("authority-sets", "b" * 65),
        ("authority-sets", "B" * 64),
        ("authority-sets", "b" * 63 + "g"),
        producer_prefix + ("p-" + "c" * 63,),
        producer_prefix + ("p-" + "c" * 65,),
        producer_prefix + ("p-" + "C" * 64,),
        producer_prefix + ("p-" + "c" * 63 + "g",),
        transaction + ("checkpoints-v1", "8191.json"),
        transaction + ("checkpoints-v1", "0" * 19 + ".json"),
        transaction + ("checkpoints-v1", "0" * 21 + ".json"),
        transaction + ("checkpoints-v1", "0" * 19 + "a.json"),
        transaction + ("checkpoints-v1", "00000000000000008192.json"),
        transaction
        + (
            "checkpoints-v1",
            ".checkpoint-pending-00000000000000000000-" + "9" * 32,
        ),
        final_set + ("semantic", "public", ".pub"),
        final_set + ("semantic", "public", "x" * 125 + ".pub"),
        final_set + ("semantic", "public", "bad!.pub"),
        final_set + ("semantic", "public", "é.pub"),
        final_set + ("semantic", "evidence", ".json"),
        final_set + ("semantic", "evidence", "x" * 124 + ".json"),
        final_set + ("semantic", "evidence", "bad!.json"),
        final_set + ("semantic", "evidence", "é.json"),
        ("review.pub",),
        ("review-evidence.json",),
        private_prefix
        + ("birth-ed25519-v1-sha256-" + "d" * 63 + ".key",),
        private_prefix
        + ("birth-ed25519-v1-sha256-" + "d" * 65 + ".key",),
        private_prefix
        + ("birth-ed25519-v1-sha256-" + "D" * 64 + ".key",),
        private_prefix
        + ("birth-ed25519-v1-sha256-" + "d" * 63 + "g.key",),
        transaction
        + (
            "transaction-v1.json",
            ".payload-pending-00000000000000000000-" + transaction_id,
        ),
        transaction
        + (
            "authority-set",
            "approval",
            ".payload-pending-00000000000000000000-" + "9" * 32,
        ),
        staged_set
        + (
            "approval",
            ".payload-pending-00000000000000008192-" + transaction_id,
        ),
    )
    for components in invalid_catalog_paths:
        with pytest.raises(secure_fs.BirthSecureFSError) as caught:
            resolver(components)
        assert caught.value.code == "birth_provisioning_recovery_ambiguous"

    invalid_components = (
        (".",),
        ("..",),
        ("bad/name",),
        ("bad\\name",),
        ("e\u0301.pub",),
    )
    for components in invalid_components:
        with pytest.raises(secure_fs.BirthSecureFSError) as caught:
            resolver(components)
        assert caught.value.code == "birth_provisioning_io_unavailable"

    root_binding = secure_fs._BirthRoleBindingV1(
        components=(),
        kind=secure_fs._ObjectKind("directory"),
        role=secure_fs._BirthObjectRole("birth_integrity_only"),
    )
    ascii_1024 = ("a" * 255,) * 3 + ("b" * 254, "c")
    multibyte_1024 = ("é" * 127,) * 4 + ("é" * 2,)
    ascii_1025 = ("a" * 255,) * 4 + ("b",)
    multibyte_1025 = ("é" * 127,) * 4 + ("é" * 2 + "a",)
    assert len("/".join(ascii_1024).encode("utf-8")) == 1024
    assert len("/".join(multibyte_1024).encode("utf-8")) == 1024
    assert len("/".join(ascii_1025).encode("utf-8")) == 1025
    assert len("/".join(multibyte_1025).encode("utf-8")) == 1025
    for components in (ascii_1024, multibyte_1024):
        boundary_binding = secure_fs._BirthRoleBindingV1(
            components=components,
            kind=secure_fs._ObjectKind("regular_file"),
            role=secure_fs._BirthObjectRole("birth_confidential"),
        )
        boundary_catalog = secure_fs._BirthRoleCatalogV1(
            schema_version=1,
            patterns=(),
            exact_bindings=(root_binding, boundary_binding),
            generation=0,
        )
        assert boundary_catalog._resolve_binding_v1(components) == boundary_binding
    for components in (ascii_1025, multibyte_1025):
        try:
            boundary_binding = secure_fs._BirthRoleBindingV1(
                components=components,
                kind=secure_fs._ObjectKind("regular_file"),
                role=secure_fs._BirthObjectRole("birth_confidential"),
            )
            boundary_catalog = secure_fs._BirthRoleCatalogV1(
                schema_version=1,
                patterns=(),
                exact_bindings=(root_binding, boundary_binding),
                generation=0,
            )
        except secure_fs.BirthSecureFSError as caught:
            assert caught.code == "birth_provisioning_io_unavailable"
        else:
            with pytest.raises(secure_fs.BirthSecureFSError) as caught:
                boundary_catalog._resolve_binding_v1(components)
            assert caught.value.code == "birth_provisioning_io_unavailable"

    conflicting_root = secure_fs._BirthRoleBindingV1(
        components=(),
        kind=secure_fs._ObjectKind("directory"),
        role=secure_fs._BirthObjectRole("birth_confidential"),
    )
    for exact_bindings, expected_code in (
        ((root_binding, root_binding), "birth_provisioning_recovery_ambiguous"),
        ((conflicting_root,), "birth_provisioning_acl_unsafe"),
        (
            (root_binding, conflicting_root),
            "birth_provisioning_acl_unsafe",
        ),
    ):
        try:
            invalid_catalog = secure_fs._BirthRoleCatalogV1(
                schema_version=1,
                patterns=tuple(pattern_type),
                exact_bindings=exact_bindings,
                generation=0,
            )
        except secure_fs.BirthSecureFSError as caught:
            assert caught.code == expected_code
            continue
        with pytest.raises(secure_fs.BirthSecureFSError) as caught:
            invalid_catalog._resolve_binding_v1(())
        assert caught.value.code == expected_code
    conflicting_file = secure_fs._BirthRoleBindingV1(
        components=("same",),
        kind=secure_fs._ObjectKind("regular_file"),
        role=secure_fs._BirthObjectRole("birth_confidential"),
    )
    conflicting_directory = secure_fs._BirthRoleBindingV1(
        components=("same",),
        kind=secure_fs._ObjectKind("directory"),
        role=secure_fs._BirthObjectRole("birth_confidential"),
    )
    try:
        ambiguous_components = secure_fs._BirthRoleCatalogV1(
            schema_version=1,
            patterns=(),
            exact_bindings=(root_binding, conflicting_file, conflicting_directory),
            generation=0,
        )
    except secure_fs.BirthSecureFSError as caught:
        assert caught.code == "birth_provisioning_acl_unsafe"
    else:
        with pytest.raises(secure_fs.BirthSecureFSError) as caught:
            ambiguous_components._resolve_binding_v1(("same",))
        assert caught.value.code == "birth_provisioning_acl_unsafe"


@pytest.mark.parametrize("slug", ["installer-only-entry"], ids=["installer-only-entry"])
def test_r1_installer_entry(
    slug: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = importlib.import_module("install.birth_authority_provisioning")
    secure_fs = importlib.import_module("executor_birth_secure_fs")
    entry = getattr(module, "open_birth_provisioning_layout_v1")
    assert inspect.isfunction(entry)
    assert entry.__module__ == "install.birth_authority_provisioning"
    assert tuple(inspect.signature(entry).parameters) == ()
    layout_type = getattr(module, "ProvisioningLayoutV1")
    assert dataclasses.is_dataclass(layout_type)
    assert [field.name for field in dataclasses.fields(layout_type)] == [
        "birth_session",
        "operator_input",
        "service_identity",
    ]
    real_root_resolver = module._resolve_birth_root_v1
    real_operator_resolver = module._resolve_operator_input_v1
    actual_root = tmp_path / "config" / "birth"
    actual_root.mkdir(parents=True, mode=0o755)
    actual_root.chmod(0o755)
    actual_identity = secure_fs._PlatformIdentity(
        posix_uid=os.geteuid(), windows_service_sid=None
    )
    handles, canonical_root = real_root_resolver(
        actual_root, actual_identity
    )
    try:
        assert handles and len(handles) == len(set(handles))
        assert os.path.normcase(canonical_root) == os.path.normcase(
            os.path.abspath(os.fspath(actual_root))
        )
        observed = os.fstat(handles[-1])
        expected = actual_root.stat(follow_symlinks=False)
        assert (observed.st_dev, observed.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        )
    finally:
        for handle in reversed(handles):
            os.close(handle)

    operator_calls = []
    operator_sentinel = object()

    class ReadSessionProbe:
        _identity = actual_identity

        def open_directory(self, components, *, role):
            operator_calls.append((components, role))
            return operator_sentinel

    assert real_operator_resolver(
        ReadSessionProbe(), ("operator-input-v1",), actual_identity
    ) is operator_sentinel
    assert operator_calls == [
        (
            ("operator-input-v1",),
            secure_fs._BirthObjectRole("birth_integrity_only"),
        )
    ]
    calls: list[tuple[object, ...]] = []
    path_user_config = Path("/rm0008-installer-sentinel")
    identity = secure_fs._PlatformIdentity(
        posix_uid=12345,
        windows_service_sid=None,
    )
    session = object()
    operator_input = object()
    adopted_descriptors = []

    def resolve_config():
        calls.append(("config",))
        return path_user_config

    def resolve_identity():
        calls.append(("identity",))
        return identity

    def resolve_root(root, received_identity):
        calls.append(("root", root, received_identity))
        return (101, 202), "/canonical/rm0008/birth"

    def adopt(descriptor):
        adopted_descriptors.append(descriptor)
        return session

    def resolve_operator(received_session, components, received_identity):
        calls.append(
            ("operator", received_session, components, received_identity)
        )
        return operator_input

    monkeypatch.setattr(module, "_resolve_path_user_config_v1", resolve_config)
    monkeypatch.setattr(
        module, "_resolve_birth_service_identity_v1", resolve_identity
    )
    monkeypatch.setattr(module, "_resolve_birth_root_v1", resolve_root)
    monkeypatch.setattr(
        module, "_resolve_operator_input_v1", resolve_operator
    )
    monkeypatch.setattr(secure_fs, "_adopt_authenticated_root", adopt)
    layout = entry()
    assert type(layout) is layout_type
    assert not hasattr(layout, "__dict__")
    assert layout.birth_session is session
    assert layout.operator_input is operator_input
    assert layout.service_identity is identity
    for field in dataclasses.fields(layout_type):
        with pytest.raises((AttributeError, TypeError)):
            setattr(layout, field.name, object())
    assert calls == [
        ("config",),
        ("identity",),
        ("root", path_user_config / "birth", identity),
        ("operator", session, ("operator-input-v1",), identity),
    ]
    assert len(adopted_descriptors) == 1
    descriptor = adopted_descriptors[0]
    assert descriptor.handles == (101, 202)
    assert descriptor.root_path == "/canonical/rm0008/birth"
    assert descriptor.identity is identity
    catalog = descriptor.role_catalog
    assert catalog.patterns == tuple(secure_fs._BirthRolePatternV1)
    assert catalog.exact_bindings == ()
    assert catalog.schema_version == 1 and catalog.generation == 0
    assert not any(
        hasattr(operator_input, name)
        for name in (
            "create_file_exclusive",
            "create_directory_exclusive",
            "rename_no_replace",
            "dispose_transaction_object",
            "_extend_role_catalog_v1",
        )
    )


@pytest.mark.parametrize(
    "slug",
    ["descriptor-immutable-single-consumption"],
    ids=["descriptor-immutable-single-consumption"],
)
def test_r1_descriptor_immutable_single_consumption(
    slug: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secure_fs = importlib.import_module("executor_birth_secure_fs")
    _assert_productive_catalog_matcher_v1(secure_fs)
    descriptor_type = getattr(secure_fs, "_AuthenticatedRootDescriptor")
    identity = secure_fs._PlatformIdentity(posix_uid=os.geteuid(), windows_service_sid=None)
    binding = secure_fs._BirthRoleBindingV1(
        components=(),
        kind=secure_fs._ObjectKind("directory"),
        role=secure_fs._BirthObjectRole("birth_integrity_only"),
    )
    catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=(),
        exact_bindings=(binding,),
        generation=0,
    )
    fixture_kind_root = tmp_path / "exact-fixture-kind"
    fixture_kind_root.mkdir(mode=0o755)
    fixture_kind_root.chmod(0o755)
    ordinary_target = fixture_kind_root / "ordinary-target"
    ordinary_target.write_bytes(b"target")
    ordinary_target.chmod(0o600)
    disguised = fixture_kind_root / "declared-file"
    disguised.symlink_to(ordinary_target.name)
    disguised_binding = role_binding(
        secure_fs,
        (disguised.name,),
        directory=False,
        role=secure_fs._BirthObjectRole("birth_confidential"),
    )
    with pytest.raises(AssertionError):
        exact_role_catalog(
            secure_fs,
            (disguised_binding,),
            root=fixture_kind_root,
        )
    disguised.unlink()
    os.mkfifo(disguised, mode=0o600)
    with pytest.raises(AssertionError):
        exact_role_catalog(
            secure_fs,
            (disguised_binding,),
            root=fixture_kind_root,
        )
    sentinel_handle = 987_654_321
    descriptor = descriptor_type(
        handles=(sentinel_handle,),
        root_path="/rm0008-a-only-sentinel",
        identity=identity,
        role_catalog=catalog,
    )
    assert [field.name for field in dataclasses.fields(descriptor)] == [
        "handles",
        "root_path",
        "identity",
        "role_catalog",
    ]
    assert not hasattr(descriptor, "__dict__")
    assert not hasattr(descriptor, "_adopted")
    assert not hasattr(secure_fs, "_DESCRIPTOR_TOKEN")
    assert weakref.ref(descriptor)() is descriptor
    for field, replacement in (
        ("handles", (sentinel_handle + 1,)),
        ("root_path", "/changed"),
        ("identity", secure_fs._PlatformIdentity(posix_uid=0, windows_service_sid=None)),
        (
            "role_catalog",
            secure_fs._BirthRoleCatalogV1(1, (), (), 0),
        ),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(descriptor, field, replacement)
    for target, field, replacement in (
        (binding, "components", ("changed",)),
        (catalog, "generation", 1),
    ):
        with pytest.raises((AttributeError, TypeError)):
            setattr(target, field, replacement)

    sessions: list[tuple[object, object, object, object, object]] = []
    close_calls: list[object] = []

    def fake_session(
        token: object,
        handles: object,
        root_path: object,
        *,
        identity: object,
        role_catalog: object,
    ) -> object:
        sessions.append((token, handles, root_path, identity, role_catalog))
        return object()

    monkeypatch.setattr(secure_fs, "_SecureRootSession", fake_session)
    monkeypatch.setattr(os, "close", lambda handle: close_calls.append(handle))
    first = secure_fs._adopt_authenticated_root(descriptor)
    assert first is not None
    assert sessions[0] == (
        secure_fs._SESSION_TOKEN,
        descriptor.handles,
        descriptor.root_path,
        descriptor.identity,
        descriptor.role_catalog,
    )
    assert sessions[0][1] is descriptor.handles
    assert sessions[0][3] is descriptor.identity
    assert sessions[0][4] is descriptor.role_catalog
    assert close_calls == []
    assert descriptor.handles == (sentinel_handle,)
    with pytest.raises(secure_fs.BirthSecureFSError) as error:
        secure_fs._adopt_authenticated_root(descriptor)
    assert error.value.code == "birth_provisioning_io_unavailable"
    equal_by_value = descriptor_type(
        handles=(sentinel_handle,),
        root_path="/rm0008-a-only-sentinel",
        identity=identity,
        role_catalog=catalog,
    )
    assert equal_by_value is not descriptor
    assert equal_by_value != descriptor
    assert secure_fs._adopt_authenticated_root(equal_by_value) is not None
    assert sessions[1] == (
        secure_fs._SESSION_TOKEN,
        equal_by_value.handles,
        equal_by_value.root_path,
        equal_by_value.identity,
        equal_by_value.role_catalog,
    )
    assert sessions[1][1] is equal_by_value.handles
    assert sessions[1][3] is equal_by_value.identity
    assert sessions[1][4] is equal_by_value.role_catalog
    with pytest.raises(secure_fs.BirthSecureFSError):
        secure_fs._adopt_authenticated_root(equal_by_value)
    assert len(sessions) == 2
    assert close_calls == []

    monkeypatch.undo()
    root = tmp_path / "catalog-authority"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    lock = root / "provisioning-v1.lock"
    lock.write_bytes(b"0")
    lock.chmod(0o644)
    payload = root / "catalog-confidential.bin"
    payload.write_bytes(b"catalog-authority")
    payload.chmod(0o644)
    role_bindings = (
        lock_role_binding(secure_fs),
        role_binding(
            secure_fs,
            (payload.name,),
            directory=False,
            role=secure_fs._BirthObjectRole("birth_confidential"),
        ),
    )
    before = (payload.stat().st_dev, payload.stat().st_ino, payload.stat().st_mode)
    with open_session(root, role_bindings=role_bindings) as active:
        with active.global_lock(exclusive=False, create=False):
            with pytest.raises(secure_fs.BirthSecureFSError) as mismatch:
                active.read_file(
                    (payload.name,),
                    maximum=64,
                    role=secure_fs._BirthObjectRole("birth_integrity_only"),
                )
    assert mismatch.value.code == "birth_provisioning_acl_unsafe"
    assert payload.read_bytes() == b"catalog-authority"
    assert (payload.stat().st_dev, payload.stat().st_ino, payload.stat().st_mode) == before

    productive_root = tmp_path / "productive-catalog-authority"
    productive_root.mkdir(mode=0o755)
    productive_root.chmod(0o755)
    productive_operator = productive_root / "operator-input-v1"
    productive_operator.mkdir(mode=0o755)
    productive_operator.chmod(0o755)
    productive_approval = productive_operator / "approval-authority.json"
    productive_approval.write_bytes(b"productive-catalog")
    productive_approval.chmod(0o644)
    productive_catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(secure_fs._BirthRolePatternV1),
        exact_bindings=(),
        generation=0,
    )
    productive_components = ("operator-input-v1", "approval-authority.json")
    productive_role = secure_fs._BirthObjectRole("birth_integrity_only")
    with open_session(
        productive_root,
        role_catalog=productive_catalog,
    ) as productive_session:
        with productive_session.global_lock(exclusive=True, create=True):
            productive_lock_resolution = (
                productive_session._resolve_effective_role_binding_v1(
                    ("provisioning-v1.lock",)
                )
            )
            assert productive_lock_resolution.origin is secure_fs._BirthRoleBindingOriginV1.CATALOG
            productive_resolution = (
                productive_session._resolve_effective_role_binding_v1(
                    productive_components
                )
            )
            assert productive_resolution.origin is secure_fs._BirthRoleBindingOriginV1.CATALOG
            assert productive_session.read_file(
                productive_components,
                maximum=64,
                role=productive_role,
            ) == b"productive-catalog"
            productive_io_primitives = {
                "fstat": os.fstat,
                "listdir": os.listdir,
                "lstat": os.lstat,
                "open": os.open,
                "readlink": os.readlink,
                "scandir": os.scandir,
                "stat": os.stat,
            }

            def reject_productive_io(*arguments, **keywords):
                raise AssertionError("catalog/role conflict reached filesystem I/O")

            for primitive_name in productive_io_primitives:
                monkeypatch.setattr(os, primitive_name, reject_productive_io)
            with pytest.raises(secure_fs.BirthSecureFSError) as productive_mismatch:
                productive_session.read_file(
                    productive_components,
                    maximum=64,
                    role=secure_fs._BirthObjectRole("birth_confidential"),
                )
            assert productive_mismatch.value.code == "birth_provisioning_acl_unsafe"
            for primitive_name, primitive in productive_io_primitives.items():
                monkeypatch.setattr(os, primitive_name, primitive)

    origin_type = secure_fs._BirthRoleBindingOriginV1
    assert tuple((item.name, item.value) for item in origin_type) == (
        ("CATALOG", "catalog"),
        ("OVERLAY_RESERVED", "overlay_reserved"),
        ("OVERLAY_COMMITTED", "overlay_committed"),
    )
    resolved_type = secure_fs._ResolvedBirthRoleBindingV1
    assert dataclasses.is_dataclass(resolved_type)
    assert [field.name for field in dataclasses.fields(resolved_type)] == [
        "binding",
        "origin",
    ]
    resolver_method = secure_fs._SecureRootSession._resolve_effective_role_binding_v1
    assert tuple(inspect.signature(resolver_method).parameters) == (
        "self",
        "components",
    )
    valid_creation_shape = """
def create_file_exclusive(self, components, payload, role):
    requested = _BirthRoleBindingV1(components, _ObjectKind("regular_file"), role)
    with self._reserve_exact_role_binding_v1(requested):
        effective = self._resolve_effective_role_binding_v1(components)
        with self._directory_chain(components[:-1]):
            if os.name == "nt":
                return self._create_windows(effective.binding)
            return self._create_posix(effective.binding)
"""
    _assert_common_creation_role_resolution_v1(valid_creation_shape)
    late_resolution = valid_creation_shape.replace(
        "        effective = self._resolve_effective_role_binding_v1(components)\n"
        "        with self._directory_chain(components[:-1]):\n",
        "        with self._directory_chain(components[:-1]):\n"
        "            effective = self._resolve_effective_role_binding_v1(components)\n",
    )
    platform_only_resolution = """
def create_file_exclusive(self, components, payload, role):
    requested = _BirthRoleBindingV1(components, _ObjectKind("regular_file"), role)
    with self._reserve_exact_role_binding_v1(requested):
        if os.name == "nt":
            effective = self._resolve_effective_role_binding_v1(components)
        with self._directory_chain(components[:-1]):
            return self._create(effective.binding)
"""
    ignored_resolution = valid_creation_shape.replace(
        "        effective = self._resolve_effective_role_binding_v1(components)",
        "        self._resolve_effective_role_binding_v1(components)",
    )
    dead_lambda_resolution = valid_creation_shape.replace(
        "        effective = self._resolve_effective_role_binding_v1(components)",
        "        deferred = lambda: self._resolve_effective_role_binding_v1(components)\n"
        "        effective = deferred()",
    )
    resolution_before_reservation = valid_creation_shape.replace(
        "    with self._reserve_exact_role_binding_v1(requested):\n"
        "        effective = self._resolve_effective_role_binding_v1(components)\n",
        "    effective = self._resolve_effective_role_binding_v1(components)\n"
        "    with self._reserve_exact_role_binding_v1(requested):\n",
    )
    io_before_reservation = valid_creation_shape.replace(
        "    with self._reserve_exact_role_binding_v1(requested):",
        "    os.stat(components[-1])\n"
        "    with self._reserve_exact_role_binding_v1(requested):",
    )
    raw_role_after_resolution = valid_creation_shape.replace(
        "return self._create_windows(effective.binding)",
        "return self._create_windows(role)",
    ).replace(
        "return self._create_posix(effective.binding)",
        "return self._create_posix(role)",
    )
    raw_role_reservation = valid_creation_shape.replace(
        "self._reserve_exact_role_binding_v1(requested)",
        "self._reserve_exact_role_binding_v1(role)",
    )
    for mutant in (
        late_resolution,
        platform_only_resolution,
        ignored_resolution,
        dead_lambda_resolution,
        resolution_before_reservation,
        io_before_reservation,
        raw_role_after_resolution,
        raw_role_reservation,
    ):
        with pytest.raises(AssertionError):
            _assert_common_creation_role_resolution_v1(mutant)
    for method_name in (
        "create_file_exclusive",
        "create_directory_exclusive",
    ):
        _assert_common_creation_role_resolution_v1(
            inspect.getsource(getattr(secure_fs._SecureRootSession, method_name))
        )
    overlay_root = tmp_path / "catalog-overlay"
    overlay_root.mkdir(mode=0o755)
    overlay_root.chmod(0o755)
    container = overlay_root / "container"
    container.mkdir(mode=0o700)
    container.chmod(0o700)
    container_binding = secure_fs._BirthRoleBindingV1(
        components=("container",),
        kind=secure_fs._ObjectKind("directory"),
        role=secure_fs._BirthObjectRole("birth_confidential"),
    )
    target_components = ("container", "fresh.bin")
    target_role = secure_fs._BirthObjectRole("birth_confidential")
    target_binding = secure_fs._BirthRoleBindingV1(
        components=target_components,
        kind=secure_fs._ObjectKind("regular_file"),
        role=target_role,
    )
    resolved_probe = resolved_type(
        binding=target_binding,
        origin=origin_type.OVERLAY_RESERVED,
    )
    assert not hasattr(resolved_probe, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        resolved_probe.origin = origin_type.OVERLAY_COMMITTED
    directory_components = ("container", "fresh-directory")
    directory_binding = secure_fs._BirthRoleBindingV1(
        components=directory_components,
        kind=secure_fs._ObjectKind("directory"),
        role=target_role,
    )
    late_components = ("container", "late-failure.bin")
    late_binding = secure_fs._BirthRoleBindingV1(
        components=late_components,
        kind=secure_fs._ObjectKind("regular_file"),
        role=target_role,
    )
    lock_binding = lock_role_binding(secure_fs)
    real_io_primitives = {
        "chmod": os.chmod,
        "fstat": os.fstat,
        "fsync": os.fsync,
        "listdir": os.listdir,
        "lstat": os.lstat,
        "mkdir": os.mkdir,
        "open": os.open,
        "readlink": os.readlink,
        "scandir": os.scandir,
        "stat": os.stat,
        "write": os.write,
    }
    pre_io_seen: list[tuple[tuple[str, ...], object]] = []
    force_first_io_failure = False
    forbid_any_io = False
    creation_attempt_active = False
    first_syscall_pending = False
    active_overlay = None
    attempt_binding = target_binding
    late_failure_path = None
    resolver_probe_active = False

    def logical_inventory(root: Path) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                # The payload digest is the last column of the snapshot; size
                # and the three timestamps are deliberately left out, because
                # this comparison is about identity and permissions.
                row[11],
            )
            for row in tree_snapshot(root)
        )

    def observe_first_io() -> None:
        nonlocal force_first_io_failure, first_syscall_pending
        nonlocal resolver_probe_active
        if resolver_probe_active:
            raise AssertionError("effective role resolver performed filesystem I/O")
        if forbid_any_io:
            raise AssertionError("catalog rejection reached filesystem I/O")
        if creation_attempt_active and first_syscall_pending:
            first_syscall_pending = False
            assert active_overlay is not None
            resolver_probe_active = True
            try:
                reserved = active_overlay._resolve_effective_role_binding_v1(
                    attempt_binding.components
                )
            finally:
                resolver_probe_active = False
            assert reserved.binding == attempt_binding
            assert reserved.origin is origin_type.OVERLAY_RESERVED
            pre_io_seen.append((attempt_binding.components, reserved.origin))
            if force_first_io_failure:
                force_first_io_failure = False
                raise OSError(5, "forced first-I/O failure")

    def checked_io_primitive(name: str):
        real_primitive = real_io_primitives[name]

        def checked(*arguments, **keywords):
            nonlocal late_failure_path
            observe_first_io()
            if (
                name == "fsync"
                and late_failure_path is not None
                and late_failure_path.exists()
            ):
                late_failure_path = None
                raise OSError(5, "forced post-create durability failure")
            return real_primitive(*arguments, **keywords)

        return checked

    for primitive_name in real_io_primitives:
        monkeypatch.setattr(
            os,
            primitive_name,
            checked_io_primitive(primitive_name),
        )
    with open_session(
        overlay_root, role_bindings=(container_binding,)
    ) as active:
        active_overlay = active
        attempt_binding = lock_binding
        creation_attempt_active = True
        first_syscall_pending = True
        with active.global_lock(exclusive=True, create=True):
            creation_attempt_active = False
            committed_lock = active._resolve_effective_role_binding_v1(
                lock_binding.components
            )
            assert committed_lock.binding == lock_binding
            assert committed_lock.origin is origin_type.OVERLAY_COMMITTED

            before_failed_create = logical_inventory(overlay_root)
            attempt_binding = target_binding
            creation_attempt_active = True
            first_syscall_pending = True
            force_first_io_failure = True
            with pytest.raises(secure_fs.BirthSecureFSError) as failed_create:
                active.create_file_exclusive(
                    target_components,
                    b"first-attempt",
                    role=target_role,
                )
            creation_attempt_active = False
            assert failed_create.value.code == "birth_provisioning_io_unavailable"
            with pytest.raises(secure_fs.BirthSecureFSError) as rolled_back:
                active._resolve_effective_role_binding_v1(target_components)
            assert rolled_back.value.code == "birth_provisioning_recovery_ambiguous"
            assert logical_inventory(overlay_root) == before_failed_create

            creation_attempt_active = True
            first_syscall_pending = True
            active.create_file_exclusive(
                target_components,
                b"committed",
                role=target_role,
            )
            creation_attempt_active = False
            committed = active._resolve_effective_role_binding_v1(target_components)
            assert committed.binding == target_binding
            assert committed.origin is origin_type.OVERLAY_COMMITTED
            forbid_any_io = True
            with pytest.raises(secure_fs.BirthSecureFSError) as wrong_role:
                active.read_file(
                    target_components,
                    maximum=32,
                    role=secure_fs._BirthObjectRole("birth_integrity_only"),
                )
            assert wrong_role.value.code == "birth_provisioning_acl_unsafe"
            forbid_any_io = False
            assert active.read_file(
                target_components, maximum=32, role=target_role
            ) == b"committed"

            committed_path = overlay_root.joinpath(*target_components)
            saved_path = committed_path.with_name("fresh.original")
            committed_path.rename(saved_path)
            committed_path.write_bytes(b"substitute")
            committed_path.chmod(0o600)
            substitute = committed_path.stat(follow_symlinks=False)
            real_read = os.read

            def reject_substitute_read(handle, maximum):
                observed = os.fstat(handle)
                if (observed.st_dev, observed.st_ino) == (
                    substitute.st_dev,
                    substitute.st_ino,
                ):
                    raise AssertionError("substituted identity was read")
                return real_read(handle, maximum)

            monkeypatch.setattr(os, "read", reject_substitute_read)
            try:
                with pytest.raises(secure_fs.BirthSecureFSError) as substitution:
                    active.read_file(
                        target_components,
                        maximum=32,
                        role=target_role,
                    )
                assert substitution.value.code == "birth_provisioning_recovery_ambiguous"
            finally:
                monkeypatch.setattr(os, "read", real_read)
                committed_path.unlink()
                saved_path.rename(committed_path)

            before_late_failure = logical_inventory(overlay_root)
            attempt_binding = late_binding
            creation_attempt_active = True
            first_syscall_pending = True
            late_failure_path = overlay_root.joinpath(*late_components)
            with pytest.raises(secure_fs.BirthSecureFSError) as late_failure:
                active.create_file_exclusive(
                    late_components,
                    b"must-be-rolled-back",
                    role=target_role,
                )
            creation_attempt_active = False
            late_failure_path = None
            assert late_failure.value.code == "birth_provisioning_io_unavailable"
            with pytest.raises(secure_fs.BirthSecureFSError) as late_rolled_back:
                active._resolve_effective_role_binding_v1(late_components)
            assert late_rolled_back.value.code == "birth_provisioning_recovery_ambiguous"
            assert logical_inventory(overlay_root) == before_late_failure

            attempt_binding = directory_binding
            creation_attempt_active = True
            first_syscall_pending = True
            active.create_directory_exclusive(
                directory_components,
                role=target_role,
            )
            creation_attempt_active = False
            committed_directory = active._resolve_effective_role_binding_v1(
                directory_components
            )
            assert committed_directory.binding == directory_binding
            assert committed_directory.origin is origin_type.OVERLAY_COMMITTED
    assert pre_io_seen == [
        (lock_binding.components, origin_type.OVERLAY_RESERVED),
        (target_binding.components, origin_type.OVERLAY_RESERVED),
        (target_binding.components, origin_type.OVERLAY_RESERVED),
        (late_binding.components, origin_type.OVERLAY_RESERVED),
        (directory_binding.components, origin_type.OVERLAY_RESERVED),
    ]

    with open_session(
        overlay_root, role_bindings=(container_binding,)
    ) as root_only:
        active_overlay = root_only
        with root_only.global_lock(exclusive=False, create=False):
            with pytest.raises(secure_fs.BirthSecureFSError) as absent_binding:
                root_only._resolve_effective_role_binding_v1(target_components)
            assert absent_binding.value.code == "birth_provisioning_recovery_ambiguous"
            forbid_any_io = True
            with pytest.raises(secure_fs.BirthSecureFSError) as preexisting_rejected:
                root_only.read_file(
                    target_components, maximum=32, role=target_role
                )
            assert preexisting_rejected.value.code == "birth_provisioning_recovery_ambiguous"
            forbid_any_io = False

    with open_session(
        overlay_root,
        role_bindings=(container_binding, target_binding),
    ) as declared:
        active_overlay = declared
        with declared.global_lock(exclusive=False, create=False):
            catalog_resolution = declared._resolve_effective_role_binding_v1(
                target_components
            )
            assert catalog_resolution.binding == target_binding
            assert catalog_resolution.origin is origin_type.CATALOG
            assert declared.read_file(
                target_components, maximum=32, role=target_role
            ) == b"committed"

    same_root = tmp_path / "catalog-overlay-concordant"
    same_root.mkdir(mode=0o755)
    same_root.chmod(0o755)
    same_container = same_root / "container"
    same_container.mkdir(mode=0o700)
    same_container.chmod(0o700)
    same_catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=(),
        exact_bindings=(binding, container_binding, target_binding),
        generation=0,
    )
    with open_session(same_root, role_catalog=same_catalog) as concordant:
        active_overlay = concordant
        with concordant.global_lock(exclusive=True, create=True):
            attempt_binding = target_binding
            creation_attempt_active = True
            first_syscall_pending = True
            concordant.create_file_exclusive(
                target_components,
                b"same-authority",
                role=target_role,
            )
            creation_attempt_active = False
            same_resolution = concordant._resolve_effective_role_binding_v1(
                target_components
            )
            assert same_resolution.binding == target_binding
            assert same_resolution.origin is origin_type.OVERLAY_COMMITTED

    conflict_root = tmp_path / "catalog-overlay-conflict"
    conflict_root.mkdir(mode=0o755)
    conflict_root.chmod(0o755)
    conflict_container = conflict_root / "container"
    conflict_container.mkdir(mode=0o700)
    conflict_container.chmod(0o700)
    conflicting_binding = secure_fs._BirthRoleBindingV1(
        components=target_components,
        kind=secure_fs._ObjectKind("regular_file"),
        role=secure_fs._BirthObjectRole("birth_integrity_only"),
    )
    conflict_catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=(),
        exact_bindings=(binding, container_binding, conflicting_binding),
        generation=0,
    )
    with open_session(conflict_root, role_catalog=conflict_catalog) as conflict:
        active_overlay = conflict
        with conflict.global_lock(exclusive=True, create=True):
            conflict_before = logical_inventory(conflict_root)
            forbid_any_io = True
            with pytest.raises(secure_fs.BirthSecureFSError) as overlay_conflict:
                conflict.create_file_exclusive(
                    target_components,
                    b"must-not-exist",
                    role=target_role,
                )
            forbid_any_io = False
            assert overlay_conflict.value.code == "birth_provisioning_acl_unsafe"
            conflict_resolution = conflict._resolve_effective_role_binding_v1(
                target_components
            )
            assert conflict_resolution.binding == conflicting_binding
            assert conflict_resolution.origin is origin_type.CATALOG
            assert logical_inventory(conflict_root) == conflict_before


@pytest.mark.parametrize(
    "slug",
    ["productive-graph-no-mutating-capability"],
    ids=["productive-graph-no-mutating-capability"],
)
def test_r1_productive_graph_no_mutating_capability(slug: str) -> None:
    inventory = validate_production_inventory(enforce_filesystem=False)
    validate_productive_mutation_graph(inventory)
    baseline = _r1_graph_mutant_sources()
    validate_productive_mutation_graph(_source_mutant=baseline)

    top_level_alias_escape = dict(baseline)
    top_level_alias_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as secure_fs
def exposed(session):
    return secure_fs.dispose_transaction_object(session)
"""
    in_function_alias_escape = dict(baseline)
    in_function_alias_escape["runtime/escape.py"] = """
def exposed():
    import executor_birth_secure_fs as secure_fs
    alias = secure_fs._adopt_authenticated_root
    return alias
"""
    second_session_factory = dict(baseline)
    second_session_factory["runtime/executor_birth_secure_fs.py"] += """
def _second_factory():
    return _SecureRootSession(_SESSION_TOKEN)
"""
    orphan_installer_helper = dict(baseline)
    orphan_installer_helper["install/birth_authority_provisioning.py"] += """
def _orphan_mutator(session):
    return session.dispose_transaction_object(None)
"""
    direct_legacy_session = dict(baseline)
    direct_legacy_session["runtime/executor_birth_secure_fs.py"] = direct_legacy_session[
        "runtime/executor_birth_secure_fs.py"
    ].replace(
        "return _LegacyReadSession(_LEGACY_TOKEN, session)",
        "return session",
    )
    bound_method_escape = dict(baseline)
    bound_method_escape["runtime/escape.py"] = """
def exposed(session):
    return session.dispose_transaction_object
"""
    attribute_container_escape = dict(baseline)
    attribute_container_escape["runtime/escape.py"] = """
class Sink:
    pass
def exposed(session):
    sink = Sink()
    sink.mutator = session.dispose_transaction_object
    return sink
"""
    list_container_escape = dict(baseline)
    list_container_escape["runtime/escape.py"] = """
def exposed(session):
    return [session.dispose_transaction_object]
"""
    token_alias_escape = dict(baseline)
    token_alias_escape["runtime/executor_birth_secure_fs.py"] += """
def _token_escape():
    alias = _SESSION_TOKEN
    return alias
"""
    dropped_role_catalog = dict(baseline)
    dropped_role_catalog["runtime/executor_birth_secure_fs.py"] = (
        dropped_role_catalog["runtime/executor_birth_secure_fs.py"].replace(
            "role_catalog=descriptor.role_catalog",
            "role_catalog=None",
        )
    )
    copied_role_catalog = dict(baseline)
    copied_role_catalog["runtime/executor_birth_secure_fs.py"] = (
        copied_role_catalog["runtime/executor_birth_secure_fs.py"].replace(
            "role_catalog=descriptor.role_catalog",
            "role_catalog=tuple(descriptor.role_catalog.exact_bindings)",
        )
    )
    filesystem_public_wrapper = dict(baseline)
    filesystem_public_wrapper["runtime/executor_birth_secure_fs.py"] += """
def public_escape(session):
    return session.create_file_exclusive()
"""
    catalog_constructor_escape = dict(baseline)
    catalog_constructor_escape["runtime/escape.py"] = """
from executor_birth_secure_fs import _BirthRoleCatalogV1
def exposed():
    return _BirthRoleCatalogV1()
"""
    catalog_extension_escape = dict(baseline)
    catalog_extension_escape["runtime/escape.py"] = """
def exposed(session):
    return session._extend_role_catalog_v1
"""
    empty_productive_catalog = dict(baseline)
    empty_productive_catalog["install/birth_authority_provisioning.py"] = (
        empty_productive_catalog["install/birth_authority_provisioning.py"].replace(
            "patterns=tuple(secure_fs._BirthRolePatternV1)",
            "patterns=()",
        )
    )
    wrong_catalog_schema = dict(baseline)
    wrong_catalog_schema["install/birth_authority_provisioning.py"] = (
        wrong_catalog_schema["install/birth_authority_provisioning.py"].replace(
            "schema_version=1", "schema_version=2"
        )
    )
    wrong_catalog_generation = dict(baseline)
    wrong_catalog_generation["install/birth_authority_provisioning.py"] = (
        wrong_catalog_generation["install/birth_authority_provisioning.py"].replace(
            "generation=0", "generation=1"
        )
    )
    reordered_productive_catalog = dict(baseline)
    reordered_productive_catalog["install/birth_authority_provisioning.py"] = (
        reordered_productive_catalog["install/birth_authority_provisioning.py"].replace(
            "patterns=tuple(secure_fs._BirthRolePatternV1)",
            "patterns=tuple(reversed(secure_fs._BirthRolePatternV1))",
        )
    )
    public_catalog_alias = dict(baseline)
    public_catalog_alias["runtime/escape.py"] = """
from executor_birth_secure_fs import _BirthRoleCatalogV1 as PublicRoleCatalog
"""
    catalog_constructor_alias = dict(baseline)
    catalog_constructor_alias["install/birth_authority_provisioning.py"] = (
        catalog_constructor_alias["install/birth_authority_provisioning.py"].replace(
            "catalog = secure_fs._BirthRoleCatalogV1(",
            "catalog_type = secure_fs._BirthRoleCatalogV1\n"
            "    catalog = catalog_type(",
        )
    )
    pattern_enum_alias = dict(baseline)
    pattern_enum_alias["install/birth_authority_provisioning.py"] = (
        pattern_enum_alias["install/birth_authority_provisioning.py"].replace(
            "catalog = secure_fs._BirthRoleCatalogV1(",
            "role_patterns = secure_fs._BirthRolePatternV1\n"
            "    catalog = secure_fs._BirthRoleCatalogV1(",
        ).replace(
            "patterns=tuple(secure_fs._BirthRolePatternV1)",
            "patterns=tuple(role_patterns)",
        )
    )
    shadowed_tuple = dict(baseline)
    shadowed_tuple["install/birth_authority_provisioning.py"] = (
        shadowed_tuple["install/birth_authority_provisioning.py"].replace(
            "def open_birth_provisioning_layout_v1():",
            "def open_birth_provisioning_layout_v1():\n"
            "    tuple = lambda value: ()",
        )
    )
    initial_exact_binding = dict(baseline)
    initial_exact_binding["install/birth_authority_provisioning.py"] = (
        initial_exact_binding["install/birth_authority_provisioning.py"].replace(
            "exact_bindings=()", "exact_bindings=(object(),)"
        )
    )
    nested_function_escape = dict(baseline)
    nested_function_escape["runtime/escape.py"] = """
def exposed(session):
    def later():
        return session.dispose_transaction_object(None)
    return later
"""
    nested_class_escape = dict(baseline)
    nested_class_escape["runtime/escape.py"] = """
def exposed(session):
    class Later:
        def run(self):
            return session.dispose_transaction_object(None)
    return Later
"""
    dunder_getattribute_escape = dict(baseline)
    dunder_getattribute_escape["runtime/escape.py"] = """
def exposed(session):
    return session.__getattribute__("dispose_transaction_object")
"""
    vars_type_escape = dict(baseline)
    vars_type_escape["runtime/escape.py"] = """
def exposed(session):
    return vars(type(session))["dispose_transaction_object"].__get__(session)
"""
    object_getattribute_escape = dict(baseline)
    object_getattribute_escape["runtime/escape.py"] = """
def exposed(session):
    return object.__getattribute__(session, "dispose_transaction_object")
"""
    concatenated_getattr_escape = dict(baseline)
    concatenated_getattr_escape["runtime/escape.py"] = """
def exposed(session):
    return getattr(session, "dispose_" + "transaction_object")
"""
    dict_get_escape = dict(baseline)
    dict_get_escape["runtime/escape.py"] = """
def exposed(session):
    return type(session).__dict__.get("dispose_transaction_object").__get__(session)
"""
    attrgetter_escape = dict(baseline)
    attrgetter_escape["runtime/escape.py"] = """
import operator
def exposed(session):
    return operator.attrgetter("dispose_transaction_object")(session)
"""
    methodcaller_escape = dict(baseline)
    methodcaller_escape["runtime/escape.py"] = """
import operator
def exposed(session):
    return operator.methodcaller("dispose_transaction_object", None)(session)
"""
    partial_getattr_escape = dict(baseline)
    partial_getattr_escape["runtime/escape.py"] = """
import functools
def exposed(session):
    return functools.partial(
        getattr, session, "dispose_transaction_object"
    )
"""
    joined_name_escape = dict(baseline)
    joined_name_escape["runtime/escape.py"] = """
def exposed(session):
    name = "".join(("dispose", "_transaction_object"))
    return getattr(session, name)
"""
    eval_escape = dict(baseline)
    eval_escape["runtime/escape.py"] = """
def exposed(session):
    return eval("session.dispose_transaction_object")
"""
    formatted_name_escape = dict(baseline)
    formatted_name_escape["runtime/escape.py"] = """
def exposed(session):
    name = "{}_{}".format("dispose", "transaction_object")
    return getattr(session, name)
"""
    runtime_name_escape = dict(baseline)
    runtime_name_escape["runtime/escape.py"] = """
def exposed(session, name):
    return getattr(session, name)
"""
    module_dynamic_escape = dict(baseline)
    module_dynamic_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as sf
def exposed(name):
    return getattr(sf, name)
"""
    module_dunder_escape = dict(baseline)
    module_dunder_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as sf
def exposed(name):
    return sf.__getattribute__(name)
"""
    imported_module_dynamic_escape = dict(baseline)
    imported_module_dynamic_escape["runtime/escape.py"] = """
import importlib
def exposed(name):
    return getattr(
        importlib.import_module("executor_birth_secure_fs"), name
    )
"""
    module_return_escape = dict(baseline)
    module_return_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as sf
def exposed():
    return sf
"""
    module_vars_escape = dict(baseline)
    module_vars_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as sf
def exposed(name):
    return vars(sf)[name]
"""
    module_dict_escape = dict(baseline)
    module_dict_escape["runtime/escape.py"] = """
import executor_birth_secure_fs as sf
def exposed(name):
    return sf.__dict__[name]
"""
    builtin_import_escape = dict(baseline)
    builtin_import_escape["runtime/escape.py"] = """
def exposed(name):
    return getattr(__import__("executor_birth_secure_fs"), name)
"""
    sys_modules_escape = dict(baseline)
    sys_modules_escape["runtime/escape.py"] = """
import sys
def exposed(name):
    return getattr(sys.modules["executor_birth_secure_fs"], name)
"""
    omitted_descriptor_catalog = dict(baseline)
    omitted_descriptor_catalog["install/birth_authority_provisioning.py"] = (
        omitted_descriptor_catalog["install/birth_authority_provisioning.py"].replace(
            "        role_catalog=catalog,\n", ""
        )
    )
    copied_descriptor_catalog = dict(baseline)
    copied_descriptor_catalog["install/birth_authority_provisioning.py"] = (
        copied_descriptor_catalog["install/birth_authority_provisioning.py"].replace(
            "role_catalog=catalog", "role_catalog=tuple(catalog.exact_bindings)"
        )
    )
    for mutant in (
        top_level_alias_escape,
        in_function_alias_escape,
        second_session_factory,
        orphan_installer_helper,
        direct_legacy_session,
        bound_method_escape,
        attribute_container_escape,
        list_container_escape,
        token_alias_escape,
        dropped_role_catalog,
        copied_role_catalog,
        filesystem_public_wrapper,
        catalog_constructor_escape,
        catalog_extension_escape,
        empty_productive_catalog,
        wrong_catalog_schema,
        wrong_catalog_generation,
        reordered_productive_catalog,
        public_catalog_alias,
        catalog_constructor_alias,
        pattern_enum_alias,
        shadowed_tuple,
        initial_exact_binding,
        nested_function_escape,
        nested_class_escape,
        dunder_getattribute_escape,
        vars_type_escape,
        object_getattribute_escape,
        concatenated_getattr_escape,
        dict_get_escape,
        attrgetter_escape,
        methodcaller_escape,
        partial_getattr_escape,
        joined_name_escape,
        eval_escape,
        formatted_name_escape,
        runtime_name_escape,
        module_dynamic_escape,
        module_dunder_escape,
        imported_module_dynamic_escape,
        module_return_escape,
        module_vars_escape,
        module_dict_escape,
        builtin_import_escape,
        sys_modules_escape,
        omitted_descriptor_catalog,
        copied_descriptor_catalog,
    ):
        with pytest.raises(CertificationError):
            validate_productive_mutation_graph(_source_mutant=mutant)


def _r1_graph_mutant_sources() -> dict[str, str]:
    return {
        "runtime/executor_birth_secure_fs.py": """
_SESSION_TOKEN = object()
_LEGACY_TOKEN = object()
class _AuthenticatedRootDescriptor:
    pass
class _BirthRolePatternV1:
    pass
class _BirthRoleCatalogV1:
    pass
class _BirthRoleCatalogExtensionV1:
    pass
class _SecureRootSession:
    def __init__(self, token):
        if token is not _SESSION_TOKEN:
            raise TypeError
    def create_file_exclusive(self):
        return None
    def create_directory_exclusive(self):
        return None
    def rename_no_replace(self):
        return None
    def dispose_transaction_object(self):
        return None
    def _extend_role_catalog_v1(self):
        return None
class _LegacyReadSession:
    def __init__(self, token, session):
        self.session = session
def _open_legacy_root_session():
    session = _SecureRootSession(_SESSION_TOKEN)
    return _LegacyReadSession(_LEGACY_TOKEN, session)
def _adopt_authenticated_root(descriptor):
    return _SecureRootSession(
        _SESSION_TOKEN,
        role_catalog=descriptor.role_catalog,
    )
""",
        "install/birth_authority_provisioning.py": """
import executor_birth_secure_fs as secure_fs
class ProvisioningLayoutV1:
    def __init__(self, *, birth_session, operator_input, service_identity):
        self.birth_session = birth_session
        self.operator_input = operator_input
        self.service_identity = service_identity
def _resolve_path_user_config_v1():
    return object()
def _resolve_birth_service_identity_v1():
    return object()
def _resolve_birth_root_v1(root, identity):
    return (), "birth"
def _resolve_operator_input_v1(session, components, identity):
    return object()
def open_birth_provisioning_layout_v1():
    base = _resolve_path_user_config_v1()
    identity = _resolve_birth_service_identity_v1()
    handles, root_path = _resolve_birth_root_v1(base, identity)
    catalog = secure_fs._BirthRoleCatalogV1(
        schema_version=1,
        patterns=tuple(secure_fs._BirthRolePatternV1),
        exact_bindings=(),
        generation=0,
    )
    descriptor = secure_fs._AuthenticatedRootDescriptor(
        handles=handles,
        root_path=root_path,
        identity=identity,
        role_catalog=catalog,
    )
    session = secure_fs._adopt_authenticated_root(descriptor)
    operator_input = _resolve_operator_input_v1(
        session, ("operator-input-v1",), identity
    )
    return ProvisioningLayoutV1(
        birth_session=session,
        operator_input=operator_input,
        service_identity=identity,
    )
""",
    }


@pytest.mark.parametrize("slug", ["schema-canonical"], ids=["schema-canonical"])
def test_g6_schema_canonical(slug: str) -> None:
    manifest = validate_manifest()
    assert len(manifest["cells"]) == len(REQUIRED_CELLS_V1)
    assert MANIFEST_PATH.read_bytes()[-1:] != b"\n"


@pytest.mark.parametrize(
    "slug", ["required-cell-inventory"], ids=["required-cell-inventory"]
)
def test_g6_required_cell_inventory(slug: str) -> None:
    assert len(REQUIRED_CELLS_V1) == sum(EXPECTED_ACTIVITY_COUNTS_V1.values())
    assert len(set(REQUIRED_CELLS_V1)) == len(REQUIRED_CELLS_V1)
    counts = {
        activity: sum(cell[1] == activity for cell in REQUIRED_CELLS_V1)
        for activity in ACTIVITIES
    }
    assert counts == EXPECTED_ACTIVITY_COUNTS_V1
    source_file = inspect.getsourcefile(
        importlib.import_module(".required_cells_v1", __package__)
    )
    assert source_file is not None
    source_path = Path(source_file)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "REQUIRED_CELLS_V1"
    )
    assert isinstance(assignment.value, ast.Tuple)
    assert len(assignment.value.elts) == len(REQUIRED_CELLS_V1)
    assert all(isinstance(item, ast.Tuple) and len(item.elts) == 5 for item in assignment.value.elts)
    validate_manifest()


@pytest.mark.parametrize("slug", ["collection-exact"], ids=["collection-exact"])
def test_g6_collection_exact(slug: str, tmp_path: Path) -> None:
    manifest = validate_manifest()
    assert validate_collection(manifest) == collect_a_node_ids()

    source_root = REPO_ROOT / "tests/portable/rm0008_2a_acceptance"
    shadow_root = tmp_path / "shadow-repository"
    shadow_a = shadow_root / "tests/portable/rm0008_2a_acceptance"
    shadow_a.mkdir(parents=True)
    for name in (
        "certification_v1.py",
        "required_cells_v1.py",
        "collect_node_ids_v1.py",
        "run_activity_v1.py",
        "pytest-certification.ini",
    ):
        shutil.copy2(source_root / name, shadow_a / name)
    runtime = shadow_root / "runtime"
    runtime.mkdir()
    marker = tmp_path / "shadow-imported"
    (runtime / "pytest.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('pytest')\n",
        encoding="utf-8",
    )
    (runtime / "certification_v1.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('certification')\n",
        encoding="utf-8",
    )
    plugin_root = tmp_path / "external-plugin"
    plugin_root.mkdir()
    plugin_marker = tmp_path / "plugin-loaded"
    (plugin_root / "rm0008_evil_plugin.py").write_text(
        "from pathlib import Path\n"
        "def pytest_configure(config):\n"
        f"    Path({str(plugin_marker)!r}).write_text('loaded')\n",
        encoding="utf-8",
    )
    empty_tests = shadow_root / "empty-tests"
    empty_tests.mkdir()
    environment = os.environ.copy()
    pytest_distribution_root = Path(pytest.__file__).resolve().parents[1]
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(runtime), str(plugin_root), str(pytest_distribution_root))
    )
    environment["PYTEST_ADDOPTS"] = "-p rm0008_evil_plugin"
    environment["PYTEST_PLUGINS"] = "rm0008_evil_plugin"
    collected = subprocess.run(
        [
            sys.executable,
            "-P",
            str(shadow_a / "collect_node_ids_v1.py"),
            str(empty_tests),
        ],
        cwd=shadow_root,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert collected.returncode == int(pytest.ExitCode.NO_TESTS_COLLECTED)
    assert "pytest collection failed" in collected.stderr
    assert not marker.exists()
    assert not plugin_marker.exists()


@pytest.mark.parametrize("slug", ["no-skip-xfail"], ids=["no-skip-xfail"])
def test_g6_no_skip_xfail(slug: str) -> None:
    validate_no_skip_xfail()
    valid_platform_dispatch = {
        "valid_os_alias.py": """
import os as host_os
if host_os.name == "nt":
    owner = "windows"
else:
    owner = "posix"
""",
        "valid_sys_value_alias.py": """
from sys import platform as host_platform
owner = "windows" if host_platform == "win32" else "other"
""",
    }
    validate_no_skip_xfail(_source_mutants=valid_platform_dispatch)
    valid_owned_dispatch = {
        "valid_owned_dispatch.py": """
import os
def test_common():
    assert required_common_oracle()
    if os.name == "nt":
        windows_observation = "recorded"
    else:
        posix_observation = "recorded"
""",
    }
    validate_no_skip_xfail(
        _source_mutants=valid_owned_dispatch,
        _owned_platforms={
            "valid_owned_dispatch.py": {
                "test_common": {"linux", "windows"},
            },
        },
    )
    rejected_sources = {
        "hasattr_branch.py": """
import executor_birth_secure_fs as sf
if not hasattr(sf, "_required_symbol"):
    assert True
""",
        "aliased_hasattr.py": """
import executor_birth_secure_fs as sf
probe = hasattr
if probe(sf, "_required_symbol"):
    assert True
""",
        "getattr_expression.py": """
import executor_birth_secure_fs as sf
value = sf._required_symbol() if getattr(sf, "_required_symbol", None) else None
""",
        "importlib_alias.py": """
import importlib.util as loader
if loader.find_spec("required_module"):
    assert True
""",
        "non_exact_platform.py": """
import os
if os.name:
    assert True
""",
        "imported_skip_alias.py": """
from pytest import skip as finish
finish("not exercised")
""",
        "marker_alias.py": """
import pytest
conditional = pytest.mark.xfail
@conditional
def test_hidden():
    assert True
""",
        "dynamic_marker.py": """
import pytest
marker = getattr(pytest.mark, "skip")
marker("not exercised")
""",
        "exception_selection.py": """
try:
    import required_module
except ImportError:
    required_module = None
""",
        "implicit_boolean.py": """
import executor_birth_secure_fs as sf
assert not hasattr(sf, "_required_symbol") or sf._required_symbol()
""",
        "dynamic_default.py": """
import executor_birth_secure_fs as sf
getattr(sf, "_required_symbol", lambda: None)()
""",
        "vars_membership.py": """
import executor_birth_secure_fs as sf
if "_required_symbol" in vars(sf):
    sf._required_symbol()
else:
    assert True
""",
        "dict_get.py": """
import executor_birth_secure_fs as sf
if sf.__dict__.get("_required_symbol"):
    sf._required_symbol()
""",
        "direct_vars_get.py": """
import executor_birth_secure_fs as sf
vars(sf).get("_required_symbol", lambda: None)()
""",
        "aliased_attribute_error.py": """
import executor_birth_secure_fs as sf
MissingCapability = AttributeError
try:
    sf._required_symbol()
except MissingCapability:
    pass
""",
        "_hidden_helper.py": """
import executor_birth_secure_fs as sf
if hasattr(sf, "_required_symbol"):
    sf._required_symbol()
""",
        "linux_owned_vacuous.py": """
import os
def test_linux_owned():
    if os.name == "nt":
        assert required_oracle()
""",
        "linux_owned_nested_vacuous.py": """
import os
from contextlib import nullcontext
def test_linux_owned():
    with nullcontext():
        if os.name == "nt":
            assert required_oracle()
""",
        "linux_owned_assignment_vacuous.py": """
import os
def test_linux_owned():
    if os.name == "nt":
        assert required_oracle()
    else:
        observed = "linux"
""",
        "assigned_capability.py": """
import executor_birth_secure_fs as sf
available = hasattr(sf, "_required_symbol")
if available:
    sf._required_symbol()
else:
    assert True
""",
        "helper_capability.py": """
import executor_birth_secure_fs as sf
def available():
    return hasattr(sf, "_required_symbol")
if available():
    sf._required_symbol()
else:
    assert True
""",
        "assigned_platform.py": """
import os
on_windows = os.name == "nt"
if on_windows:
    assert windows_oracle()
else:
    assert True
""",
        "helper_platform.py": """
import os
def on_windows():
    return os.name == "nt"
if on_windows():
    assert windows_oracle()
else:
    assert True
""",
        "suppressed_capability.py": """
import contextlib as contexts
from builtins import AttributeError as MissingCapability
quiet = contexts.suppress
with quiet(MissingCapability):
    import executor_birth_secure_fs as sf
    sf._required_symbol()
assert True
""",
        "tests/portable/conftest.py": """
import pytest
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.outcome = "passed"
""",
    }
    for label, source in rejected_sources.items():
        owned_platforms = (
            {label: {"test_linux_owned": {"linux"}}}
            if "owned" in label
            else None
        )
        with pytest.raises(
            CertificationError,
            match="forbidden|capability|platform|effective pytest support",
        ):
            validate_no_skip_xfail(
                _source_mutants={label: source},
                _owned_platforms=owned_platforms,
            )
    runner_path = REPO_ROOT / "tests/portable/rm0008_2a_acceptance/run_activity_v1.py"
    recorder_source = runner_path.read_text(encoding="utf-8")
    recorder_mutant = recorder_source.replace(
        '        if report.when == "call":\n',
        '        if report.when != "call":\n',
        1,
    )
    assert recorder_mutant != recorder_source
    with pytest.raises(CertificationError, match="forbidden pytest control hook"):
        validate_no_skip_xfail(
            _source_mutants={runner_path.as_posix(): recorder_mutant}
        )
    scanned = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in _test_python_files()
    }
    effective_support = {
        relative
        for relative in (
            "conftest.py",
            "tests/portable/conftest.py",
            "tests/runtime/conftest.py",
            "tests/windows_identity/conftest.py",
        )
        if (REPO_ROOT / relative).is_file()
    }
    assert effective_support <= scanned
