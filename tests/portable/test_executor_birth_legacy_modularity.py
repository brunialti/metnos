"""Structural limits and single-authority checks for RM-0008 legacy adoption."""
from __future__ import annotations

import ast
from pathlib import Path

import executor_birth_host_chain_policy as host_chain
import executor_birth_legacy_state_journal as legacy_journal
import executor_birth_legacy_state_policy as legacy_policy
import executor_birth_legacy_state_request as legacy_request
from install import executor_birth_append_journal_posix as append_journal
from install import executor_birth_legacy_state_adoption as adoption
from install import executor_birth_legacy_state_effect_posix as effect
from install import executor_birth_legacy_state_inspection as inspection
from install import executor_birth_legacy_state_journal_posix as journal_posix
from install import executor_birth_posix_directory as directory


_MODULES = (
    host_chain, legacy_journal, legacy_policy, legacy_request,
    append_journal, adoption, effect, inspection, journal_posix, directory,
)


def test_impacted_modules_and_functions_remain_bounded() -> None:
    for module in _MODULES:
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        spans = [
            node.end_lineno - node.lineno + 1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert len(source.splitlines()) <= 400, module.__name__
        assert max(spans, default=0) <= 40, module.__name__


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
    source = Path(inspection.__file__).read_text(encoding="utf-8")
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
