"""Structural limits and single-authority checks for RM-0008 legacy adoption."""
from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# Structural checks read source; they must not execute platform-specific imports.
_MODULE_PATHS = (
    "runtime/executor_birth_host_chain_policy.py",
    "runtime/executor_birth_legacy_state_journal.py",
    "runtime/executor_birth_legacy_state_policy.py",
    "runtime/executor_birth_legacy_state_request.py",
    "install/executor_birth_append_journal_posix.py",
    "install/executor_birth_legacy_state_adoption.py",
    "install/executor_birth_legacy_state_effect_posix.py",
    "install/executor_birth_legacy_state_inspection.py",
    "install/executor_birth_legacy_state_journal_posix.py",
    "install/executor_birth_posix_directory.py",
)


def test_impacted_modules_and_functions_remain_bounded() -> None:
    for relative in _MODULE_PATHS:
        source = (_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        spans = [
            node.end_lineno - node.lineno + 1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert len(source.splitlines()) <= 400, relative
        assert max(spans, default=0) <= 40, relative


def test_contract_store_no_longer_has_ownership_mutation_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "runtime" / "contract_store.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"chown", "fchown", "lchown"}


def test_terminal_inspection_has_no_filesystem_mutation_primitive() -> None:
    source = (_ROOT / "install/executor_birth_legacy_state_inspection.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {
        "mkdir", "chmod", "fchmod", "chown", "fchown", "link",
        "unlink", "rename", "replace", "remove", "write",
    }
