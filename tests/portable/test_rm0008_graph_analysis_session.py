"""Unit proofs for the RM-0008 test-only AST session index."""
from __future__ import annotations

import ast

import pytest

from tests.portable.rm0008_2a_acceptance import certification_v1
from tests.portable.rm0008_2a_acceptance.test_manifest_acceptance import (
    _r1_graph_mutant_sources,
)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_owner_index_classifies_one_walk_without_crossing_module_scopes() -> None:
    tree = ast.parse(
        "import os\n"
        "VALUE = 'module'\n"
        "def outer():\n"
        "    import sys\n"
        "    alias = VALUE\n"
        "    def nested():\n"
        "        return alias\n"
        "    return nested\n"
    )

    nodes, assignments, imports, parents = certification_v1._owner_node_index_v1(
        tree
    )

    assert [node.names[0].name for node in imports] == ["os"]
    assert [node.targets[0].id for node in assignments] == ["VALUE"]
    assert not any(isinstance(node, ast.FunctionDef) for node in nodes)
    assert all(node in parents for node in nodes)


def test_function_owner_index_keeps_nested_executable_graph_and_parents() -> None:
    tree = ast.parse(
        "def outer():\n"
        "    import sys\n"
        "    alias = 'value'\n"
        "    def nested():\n"
        "        return alias\n"
        "    return nested\n"
    )
    outer = _function(tree, "outer")

    nodes, assignments, imports, parents = certification_v1._owner_node_index_v1(
        outer
    )

    nested = next(
        node
        for node in nodes
        if isinstance(node, ast.FunctionDef) and node.name == "nested"
    )
    returned = next(node for node in nodes if isinstance(node, ast.Return))
    assert [node.names[0].name for node in imports] == ["sys"]
    assert [node.targets[0].id for node in assignments] == ["alias"]
    assert parents[nested] is outer
    assert isinstance(parents[returned], ast.FunctionDef)


def test_module_import_index_includes_class_and_nested_function_scopes() -> None:
    tree = ast.parse(
        "import top\n"
        "class Container:\n"
        "    from install import birth_authority_provisioner\n"
        "    def method(self):\n"
        "        import nested\n"
    )

    imports = certification_v1._module_import_index_v1(tree)

    assert [
        node.names[0].name
        for node in imports
    ] == ["top", "birth_authority_provisioner", "nested"]


def test_cached_import_index_does_not_hide_class_body_provisioner_escape() -> None:
    mutant = _r1_graph_mutant_sources()
    mutant["runtime/escape.py"] = (
        "class Escape:\n"
        "    from install import birth_authority_provisioner\n"
    )

    with pytest.raises(
        certification_v1.CertificationError,
        match="runtime reaches the installer provisioner",
    ):
        certification_v1.validate_productive_mutation_graph(
            _source_mutant=mutant
        )
