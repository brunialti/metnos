from __future__ import annotations

import sys
import subprocess
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executor_standard import STANDARD_ID, validate_for_lifecycle  # noqa: E402
from sign import verify_executor  # noqa: E402


BUILTIN_NAMES = {
    "admin", "classify_entries", "compare_entries", "create_tasks",
    "delete_entries", "delete_preferences", "delete_tasks", "describe_entries",
    "describe_images", "extract_entries", "find_entries", "get_preferences",
    "list_skills", "list_tasks", "read_tasks", "read_tasks_history",
    "set_preferences", "set_skills", "set_tasks", "write_entries",
}

GITHUB_BUILTIN_REQUIRED = {
    "change_pulls_github": {"repo", "number"},
    "create_issues_github": {"repo", "title"},
    "create_tasks_github": {"repo", "workflow", "ref"},
    "delete_issues_github": {"repo", "number"},
    "delete_messages_github": {"repo", "comment_id"},
    "find_files_github": {"repo"},
    "find_issues_github": {"repo"},
    "find_pulls_github": {"repo"},
    "list_dirs_github": {"repo"},
    "read_files_github": {"repo", "paths"},
    "read_issues_github": {"repo", "number"},
    "read_pulls_github": {"repo", "number"},
    "read_tasks_github": {"repo"},
    "send_messages_github": {"repo", "target", "body"},
    "set_issues_github": {"repo", "number"},
    "set_pulls_github": {"repo", "number"},
}


def _contract_dirs() -> list[Path]:
    return sorted((RUNTIME / "builtin_executor_contracts").glob("*"))


def test_all_planner_visible_builtins_have_valid_signed_contracts() -> None:
    directories = [path for path in _contract_dirs() if path.is_dir()]
    assert {path.name for path in directories} == BUILTIN_NAMES
    for directory in directories:
        manifest = tomllib.loads(
            (directory / "manifest.toml").read_text(encoding="utf-8"))
        assert manifest["name"] == directory.name
        assert manifest["executor_standard"] == STANDARD_ID
        assert validate_for_lifecycle(manifest) == [], directory.name
        ok, detail = verify_executor(directory)
        assert ok, (directory.name, detail)


def test_loader_admits_builtins_only_from_their_signed_contracts() -> None:
    from loader import invalidate_catalog_cache, load_catalog

    invalidate_catalog_cache()
    catalog = load_catalog(verify=True, include_synth=False)
    for name in BUILTIN_NAMES:
        executor = catalog.get(name)
        assert executor is not None, name
        assert executor.source == "builtin"
        assert executor.transport == "in-process"
        assert executor.standard_state == "declared"
        assert executor.executor_standard == STANDARD_ID
        assert executor.signed_by
        assert executor.output_schema
        assert executor.capabilities
        assert executor.tests
        assert executor.manifest_path.parent.name == name


@pytest.mark.parametrize("name", [
    "create_tasks", "delete_tasks", "read_tasks", "set_tasks",
    "set_skills", "find_entries", "write_entries",
    "delete_entries", "compare_entries", "describe_images",
    "describe_entries", "classify_entries", "extract_entries",
])
def test_builtin_domains_reject_missing_required_input(name: str) -> None:
    import agent_runtime

    out = agent_runtime._invoke_builtin_handler(
        name, {}, actor="contract-test", channel="cli", turn_id="contract-test")
    assert out["ok"] is False, (name, out)
    assert out.get("error") or out.get("summary"), (name, out)
    assert out.get("error_class"), (name, out)
    assert str(out.get("error_code") or "").startswith("ERR_"), (name, out)


def test_builtin_boundary_rejects_unknown_handler_with_standard_failure() -> None:
    import agent_runtime

    out = agent_runtime._invoke_builtin_handler(
        "not_a_builtin", {}, actor="contract-test", channel="cli",
        turn_id="contract-test")
    assert out == {
        "ok": False,
        "error": "unknown builtin: not_a_builtin",
        "error_class": "not_found",
        "error_code": "ERR_NOT_FOUND",
    }


def test_builtin_boundary_marks_mixed_outcomes_partial() -> None:
    import agent_runtime

    out = agent_runtime._normalize_builtin_result(
        "describe_images",
        {"ok": True, "entries": [{"path": "a"}], "ok_count": 1,
         "fail_count": 1},
    )
    assert out["partial"] is True


def test_admin_contract_remains_fail_closed_without_command() -> None:
    from system import admin

    out = admin.invoke(intent="", command_proposed="")
    assert out["ok"] is False
    assert out["decision"] == "reject"
    assert out["approval_required"] is False
    assert out["error_class"] == "invalid_args"
    assert out["error_code"] == "ERR_ARG_INVALID"


def test_task_history_empty_filter_is_a_bounded_valid_read() -> None:
    import agent_runtime

    out = agent_runtime._invoke_builtin_handler(
        "read_tasks_history", {}, actor="contract-test", channel="cli",
        turn_id="contract-test")
    assert out["ok"] is True
    assert isinstance(out.get("history"), list)
    assert out["used"] == len(out["history"])
    assert out["cap_value"] == 200
    assert isinstance(out["truncated"], bool)


def test_installed_github_builtins_are_handcrafted_standard_signed_contracts() -> None:
    from config import PATH_USER_DATA
    from loader import invalidate_catalog_cache, load_catalog

    root = PATH_USER_DATA / "executors" / "skills" / "github"
    manifests = sorted(root.glob("*/manifest.toml"))
    if not manifests:
        pytest.skip("github builtin skill is not installed")
    assert {path.parent.name for path in manifests} == set(GITHUB_BUILTIN_REQUIRED)
    invalidate_catalog_cache()
    catalog = load_catalog(verify=True, include_synth=True)
    for path in manifests:
        manifest = tomllib.loads(path.read_text(encoding="utf-8"))
        assert manifest.get("origin") == "handcrafted"
        assert "provenance" not in manifest
        assert "import" not in str(manifest.get("author", "")).lower()
        assert manifest.get("executor_standard") == STANDARD_ID
        assert validate_for_lifecycle(manifest) == [], path.parent.name
        assert verify_executor(path.parent)[0], path.parent.name
        loaded = catalog.get(path.parent.name)
        assert loaded is not None
        assert loaded.membership == "builtin"
        assert loaded.source == "handcrafted"
        assert loaded.is_imported is False
        capabilities = manifest.get("capabilities") or []
        assert {cap.get("name") for cap in capabilities} == {"provider:access"}
        assert capabilities[0].get("hint") == ["github"]
        assert set(manifest["args"].get("required") or []) == \
            GITHUB_BUILTIN_REQUIRED[path.parent.name]
        assert "error_code?: str" in manifest["output"]["schema_inline"]
        tests = manifest.get("tests") or []
        assert {test["name"] for test in tests} == {
            "rejects_unknown_args_offline", "happy_path_offline",
            "auth_missing_offline_needs_inputs",
            "rejects_missing_required_offline",
        }
        for test in tests:
            if test["name"] in {
                    "happy_path_offline", "auth_missing_offline_needs_inputs"}:
                assert str((test.get("env") or {}).get(
                    "METNOS_SUBPROCESS_FAKE", "")).startswith("skill_test_fakes.")
            assert not any(str(key).startswith("_force_") for key in test["input"])
        code = (path.parent / manifest["code"]["files"][0]).read_text(
            encoding="utf-8")
        assert "executor importato" not in code
        assert "executor builtin GitHub handcrafted" in code
        assert "_validate_skill_args" in code
        assert "_error_code_for_class" in code


def test_installed_github_registry_normalizes_builtin_authority() -> None:
    from config import PATH_USER_DATA
    from skill_registry import get_skill_info
    from skills_catalog import skill_tier

    root = PATH_USER_DATA / "executors" / "skills" / "github"
    if not any(root.glob("*/manifest.toml")):
        pytest.skip("github builtin skill is not installed")
    info = get_skill_info("github")
    assert info is not None
    assert info.is_builtin is True
    assert info.is_first_party is True
    assert info.is_imported is False
    # La posizione resta osservabile senza contaminare l'identità.
    assert info.is_builtin_repo is False
    assert info.trust == "metnos-official"
    # Release tier e product membership sono assi distinti (ADR 0170/0195).
    assert skill_tier("github") == "first_party"


@pytest.mark.parametrize("name", sorted(GITHUB_BUILTIN_REQUIRED))
def test_installed_github_builtin_birth_suite_is_hermetic(name: str) -> None:
    from config import PATH_USER_DATA

    manifest = (PATH_USER_DATA / "executors" / "skills" / "github" / name
                / "manifest.toml")
    if not manifest.is_file():
        pytest.skip("github builtin skill is not installed")
    proc = subprocess.run(
        [sys.executable, str(RUNTIME / "test_runner.py"), str(manifest)],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4/4 passati" in proc.stdout
