from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

import agent_runtime  # noqa: E402
from invocation_scope import check_invocation_scope  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from test_runner import check_hints  # noqa: E402


def _executor() -> SimpleNamespace:
    return SimpleNamespace(
        capabilities=[{
            "name": "fs:write",
            "hint": ["/tmp/metnos-scope/**"],
            "path_args": ["dst_dir"],
            "parent_path_args": ["paths"],
        }],
        args_schema={"properties": {
            "dst_dir": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        }},
    )


def test_annotated_paths_are_checked_against_signed_scope() -> None:
    executor = _executor()

    assert check_invocation_scope(executor, {
        "dst_dir": "/tmp/metnos-scope/out",
        "paths": ["/tmp/metnos-scope/in/a.md"],
    }, actor="scope-test") is None
    assert "outside allowed scope" in check_invocation_scope(executor, {
        "dst_dir": "/etc/metnos-scope-out",
        "paths": ["/tmp/metnos-scope/in/a.md"],
    }, actor="scope-test")


def test_parent_path_annotation_covers_derived_sibling_output() -> None:
    denial = check_invocation_scope(_executor(), {
        "paths": ["/etc/source.md"],
    }, actor="scope-test")

    assert denial is not None
    assert "/etc" in denial


def test_unannotated_executor_preserves_legacy_semantics() -> None:
    executor = SimpleNamespace(
        capabilities=[{"name": "fs:write", "hint": ["/tmp/**"]}],
        args_schema={"properties": {"dst_dir": {"type": "string"}}},
    )

    assert check_invocation_scope(
        executor, {"dst_dir": "/etc/legacy"}, actor="scope-test") is None


def test_birth_scope_checker_does_not_treat_entry_columns_as_authority() -> None:
    capabilities = [{"name": "fs:write", "hint": ["/tmp/**"]}]

    assert check_hints({
        "path": "/tmp/report.xlsx",
        "entries": [{"path": "/observed/source.jpg", "src": "/data"}],
    }, capabilities, actor="scope-test") is None
    assert "outside allowed scope" in check_hints(
        {"path": "/etc/report.xlsx"}, capabilities, actor="scope-test")


def test_live_chokepoint_denies_change_format_before_subprocess(
        monkeypatch) -> None:
    catalog = Catalog()
    _load_dir_into_catalog(ROOT / "executors", catalog, False,
                           is_synthesized=False)
    executor = catalog.executors["change_files_format"]
    monkeypatch.setattr(
        agent_runtime.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("subprocess must not run")),
    )

    result = agent_runtime._invoke_executor_impl(
        executor,
        {"paths": ["/tmp/input.md"], "to_format": "html",
         "dst_dir": "/etc/metnos-scope-out"},
        actor="scope-test", channel="test",
    )

    assert result["ok"] is False
    assert result["error_class"] == "permission_denied"
    assert result["error_code"] == "ERR_PERMISSION_DENIED"
