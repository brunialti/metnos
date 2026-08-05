from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from http_routes_admin import _executors_rows  # noqa: E402
from http_render import render_template  # noqa: E402
from loader import Executor  # noqa: E402


def _executor(**overrides) -> Executor:
    values = {
        "name": "read_files",
        "version": "1.0.0",
        "description": "read",
        "affinity": [],
        "args_schema": {},
        "capabilities": [],
        "tests": [],
        "code_path": Path("read_files.py"),
        "manifest_path": Path("manifest.toml"),
        "signed_by": "author",
    }
    values.update(overrides)
    return Executor(**values)


def test_admin_rows_use_catalog_metadata_instead_of_path_heuristics() -> None:
    executor = _executor(
        membership="builtin",
        source="builtin",
        transport="local-or-remote",
        executor_standard="metnos.executor/1.0",
        standard_state="declared",
    )

    row = _executors_rows([executor])[0]

    assert row["membership"] == "builtin"
    assert row["source"] == "builtin"
    assert row["transport"] == "local-or-remote"
    assert row["executor_standard"] == "metnos.executor/1.0"
    assert row["standard_state"] == "declared"
    assert row["execution_policy"]["parallelism_class"] == 0


def test_executor_catalog_default_execution_policy_is_serial() -> None:
    executor = _executor()

    assert executor.execution_policy == {
        "effect": "unknown",
        "parallelism_class": 0,
        "resource_class": "default",
        "concurrency_key": "none",
        "equivalence_gate": "unverified",
    }
    assert executor.execution_policy_declared is False


def test_admin_rows_keep_safe_defaults_for_old_test_doubles() -> None:
    class OldExecutor:
        name = "read_files"
        version = "1"
        lifecycle = "active"
        capabilities = []
        revertible = False
        deprecated_at = None
        superseded_by = None

    row = _executors_rows([OldExecutor()])[0]

    assert row["membership"] == "builtin"
    assert row["source"] == "handcrafted"
    assert row["transport"] == "local-subprocess"
    assert row["standard_state"] == "legacy"
    assert row["execution_policy"]["parallelism_class"] == 0


def test_executor_admin_template_exposes_membership_separately() -> None:
    row = _executors_rows([_executor(
        name="find_issues_github",
        membership="builtin",
        source="handcrafted",
    )])[0]

    html = render_template("executors.html", rows=[row], rejected=[])

    assert "appartenenza" in html
    assert "find_issues_github" in html
    assert "builtin" in html
    assert "handcrafted" in html
