"""Negative cases for the closed RM-0008 acceptance evolution."""
from __future__ import annotations

import ast
import hashlib

import pytest

from tests.portable.rm0008_2a_acceptance import certification_v1 as certification


_CERTIFICATION = (
    "tests/portable/rm0008_2a_acceptance/certification_v1.py"
)
_NEGATIVE_CASES = (
    "tests/portable/rm0008_2a_acceptance/test_manifest_acceptance.py"
)
_ANCHOR = "tests/portable/test_rm0008_acceptance_evolution.py"
_EXPECTED_PRODUCTIVE_GRAPH_MUTANTS = (
    "reader_gains_a_mutation",
    "third_construction_site",
    "phase_calls_a_mutation",
    "phase_calls_the_layout",
    "unreviewed_transition_entry",
    "transition_calls_the_layout",
    "transition_calls_a_mutation",
    "public_provisioner_door",
    "runtime_reaches_provisioner",
    "absent_provisioner_entry",
    "top_level_alias_escape",
    "in_function_alias_escape",
    "second_session_factory",
    "orphan_installer_helper",
    "direct_legacy_session",
    "bound_method_escape",
    "attribute_container_escape",
    "list_container_escape",
    "token_alias_escape",
    "dropped_role_catalog",
    "copied_role_catalog",
    "filesystem_public_wrapper",
    "catalog_constructor_escape",
    "catalog_extension_escape",
    "empty_productive_catalog",
    "wrong_catalog_schema",
    "wrong_catalog_generation",
    "reordered_productive_catalog",
    "public_catalog_alias",
    "catalog_constructor_alias",
    "pattern_enum_alias",
    "shadowed_tuple",
    "initial_exact_binding",
    "nested_function_escape",
    "nested_class_escape",
    "dunder_getattribute_escape",
    "vars_type_escape",
    "object_getattribute_escape",
    "concatenated_getattr_escape",
    "dict_get_escape",
    "attrgetter_escape",
    "methodcaller_escape",
    "partial_getattr_escape",
    "joined_name_escape",
    "eval_escape",
    "formatted_name_escape",
    "runtime_name_escape",
    "module_dynamic_escape",
    "module_dunder_escape",
    "imported_module_dynamic_escape",
    "module_return_escape",
    "module_vars_escape",
    "module_dict_escape",
    "builtin_import_escape",
    "sys_modules_escape",
    "omitted_descriptor_catalog",
    "copied_descriptor_catalog",
)
_EXPECTED_PRODUCTIVE_GRAPH_TEST_AST_SHA256 = (
    "9c0c5b703e46883b0a60673eaa5bcd4cd755416e517cea0c12a33c041606267a"
)


def _productive_graph_test(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    test_function = next(
        (
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "test_r1_productive_graph_no_mutating_capability"
        ),
        None,
    )
    assert test_function is not None
    return test_function


def _productive_graph_mutant_loop(test_function: ast.FunctionDef) -> ast.For:
    matching_loops = [
        node for node in ast.walk(test_function)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "mutant"
        and isinstance(node.iter, ast.Tuple)
    ]
    assert len(matching_loops) == 1
    return matching_loops[0]


def _assert_closed_productive_graph_mutants(source: str) -> None:
    test_function = _productive_graph_test(source)
    normalized = ast.dump(
        test_function,
        annotate_fields=True,
        include_attributes=False,
    ).encode("utf-8")
    assert hashlib.sha256(normalized).hexdigest() == (
        _EXPECTED_PRODUCTIVE_GRAPH_TEST_AST_SHA256
    )
    loop = _productive_graph_mutant_loop(test_function)
    names = tuple(
        node.id for node in loop.iter.elts if isinstance(node, ast.Name)
    )
    assert len(names) == len(loop.iter.elts)
    assert names == _EXPECTED_PRODUCTIVE_GRAPH_MUTANTS

    guarded_calls = [
        node for node in ast.walk(loop)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and isinstance(item.context_expr.func.value, ast.Name)
            and item.context_expr.func.value.id == "pytest"
            and item.context_expr.func.attr == "raises"
            and len(item.context_expr.args) == 1
            and isinstance(item.context_expr.args[0], ast.Name)
            and item.context_expr.args[0].id == "CertificationError"
            for item in node.items
        )
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "validate_productive_mutation_graph"
            and any(
                keyword.arg == "_source_mutant"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "mutant"
                for keyword in child.keywords
            )
            for child in ast.walk(node)
        )
    ]
    assert len(guarded_calls) == 1


def _baseline() -> dict[str, tuple[str, str]]:
    return {
        _CERTIFICATION: ("100644", "a" * 40),
        _NEGATIVE_CASES: ("100644", "b" * 40),
        "tests/portable/rm0008_2a_acceptance/required_cells_v1.py": (
            "100644", "c" * 40,
        ),
    }


def test_only_the_two_reviewed_acceptance_files_may_evolve() -> None:
    source = _baseline()
    current = dict(source)
    current[_CERTIFICATION] = ("100644", "d" * 40)
    current[_NEGATIVE_CASES] = ("100644", "e" * 40)
    current[_ANCHOR] = ("100644", "f" * 40)

    certification._validate_reviewed_acceptance_tree_evolution(source, current)


def test_productive_graph_negative_cases_remain_closed_and_guarded() -> None:
    source = (
        certification.REPO_ROOT / _NEGATIVE_CASES
    ).read_text(encoding="utf-8")
    _assert_closed_productive_graph_mutants(source)


def test_removing_a_productive_graph_negative_case_is_detected() -> None:
    source = (
        certification.REPO_ROOT / _NEGATIVE_CASES
    ).read_text(encoding="utf-8")
    reduced = source.replace(
        "        unreviewed_transition_entry,\n",
        "",
        1,
    )
    assert reduced != source
    with pytest.raises(AssertionError):
        _assert_closed_productive_graph_mutants(reduced)


@pytest.mark.parametrize("variant", (
    "missing", "added", "third", "only-one", "anchor-missing", "anchor-mode",
))
def test_every_other_acceptance_tree_change_is_rejected(variant: str) -> None:
    source = _baseline()
    current = dict(source)
    current[_CERTIFICATION] = ("100644", "d" * 40)
    current[_NEGATIVE_CASES] = ("100644", "e" * 40)
    current[_ANCHOR] = ("100644", "f" * 40)
    if variant == "missing":
        current.pop("tests/portable/rm0008_2a_acceptance/required_cells_v1.py")
    elif variant == "added":
        current["tests/portable/rm0008_2a_acceptance/extra.py"] = (
            "100644", "f" * 40,
        )
    elif variant == "third":
        current["tests/portable/rm0008_2a_acceptance/required_cells_v1.py"] = (
            "100644", "f" * 40,
        )
    elif variant == "only-one":
        current[_NEGATIVE_CASES] = source[_NEGATIVE_CASES]
    if variant == "anchor-missing":
        current.pop(_ANCHOR)
    elif variant == "anchor-mode":
        current[_ANCHOR] = ("100755", "f" * 40)

    with pytest.raises(
        certification.CertificationError,
        match="frozen acceptance baseline has an unreviewed evolution",
    ):
        certification._validate_reviewed_acceptance_tree_evolution(
            source, current,
        )


def test_current_acceptance_anchor_content_is_independently_bound() -> None:
    source = (certification.REPO_ROOT / _ANCHOR).read_bytes()
    certification._validate_current_exact_acceptance_blobs({_ANCHOR: source})

    with pytest.raises(
        certification.CertificationError,
        match="current acceptance anchor differs",
    ):
        certification._validate_current_exact_acceptance_blobs({
            _ANCHOR: source + b"\n",
        })
