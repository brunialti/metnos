from __future__ import annotations

import sys
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

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
    "set_preferences", "set_skills", "set_tasks", "start_lre",
    "write_entries",
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
    return sorted(
        path
        for path in (RUNTIME / "builtin_executor_contracts").glob("*")
        if not path.name.startswith(".")
    )


def test_all_planner_visible_builtins_have_valid_signed_contracts(
        signed_builtin_contracts) -> None:
    assert {path.name for path in _contract_dirs() if path.is_dir()} == BUILTIN_NAMES
    directories = sorted(path for path in signed_builtin_contracts.iterdir()
                         if path.is_dir())
    assert {path.name for path in directories} == BUILTIN_NAMES
    for directory in directories:
        manifest = tomllib.loads(
            (directory / "manifest.toml").read_text(encoding="utf-8"))
        assert manifest["name"] == directory.name
        assert manifest["executor_standard"] == STANDARD_ID
        assert validate_for_lifecycle(manifest) == [], directory.name
        ok, detail = verify_executor(directory)
        assert ok, (directory.name, detail)


def test_admin_contract_describes_unprivileged_commands_without_granting_them():
    import scripts.generate_builtin_executor_contracts as generator

    it, en, capabilities, _ = generator._META["admin"]
    assert "anche senza privilegi" in it and "unprivileged commands" in en
    assert "vaglio" in it and "safety gate" in en
    assert {name for name, _ in capabilities} == {"system:admin"}
    for description in generator._description("admin", it, en):
        positions = [description.index(part) for part in ("SCOPO:", "PATTERN:", "NON:", "OUT:")]
        assert positions == sorted(positions)
        assert len(description.split("OUT:", 1)[0]) <= 240


def test_loader_admits_builtins_only_from_their_signed_contracts(
        signed_builtin_contracts) -> None:
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


def test_private_contracts_keep_signature_and_code_checks(
        signed_builtin_contracts, tmp_path: Path) -> None:
    import shutil
    from loader import _load_builtin_contract

    forged = tmp_path / "admin"
    shutil.copytree(signed_builtin_contracts / "admin", forged)
    (forged / "manifest.toml.sig").write_bytes(b"\0" * 64)
    ok, _detail = verify_executor(forged)
    assert not ok

    changed = tmp_path / "admin.py"
    changed.write_bytes((RUNTIME / "system" / "admin.py").read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="differs from the admitted implementation"):
        _load_builtin_contract("admin", changed)


def test_builtin_handler_source_registry_is_total_and_literal() -> None:
    import agent_runtime

    assert set(agent_runtime._BUILTIN_TOOL_MODULE_FILES) == set(
        agent_runtime._BUILTIN_TOOL_HANDLERS
    )
    for name, module_file in agent_runtime._BUILTIN_TOOL_MODULE_FILES.items():
        handler = agent_runtime._BUILTIN_TOOL_HANDLERS[name]
        assert Path(module_file).stem == handler.__module__.rsplit(".", 1)[-1]
        assert agent_runtime._builtin_tool_module_path(name) == RUNTIME / module_file
        assert (RUNTIME / module_file).is_file()


def test_builtin_contract_rejects_a_runtime_module_that_differs_from_admitted_bytes(
    tmp_path: Path,
) -> None:
    from loader import _load_builtin_contract

    changed = tmp_path / "compare_entries.py"
    changed.write_bytes((RUNTIME / "compare_entries.py").read_bytes() + b"\n# changed\n")

    with pytest.raises(ValueError, match="differs from the admitted implementation"):
        _load_builtin_contract("compare_entries", changed)


@pytest.mark.parametrize("already_current", [False, True])
def test_builtin_generator_sign_mode_resumes_one_closed_birth_candidate(
    tmp_path: Path, monkeypatch, capsys, already_current: bool,
) -> None:
    import executor_birth_intent
    import scripts.generate_builtin_executor_contracts as generator

    module = tmp_path / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "unchanged").write_text("kept", encoding="utf-8")
    spec = {
        "function": {
            "name": "compare_entries",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {
                        "type": "string",
                        "paired_device_identity": "name",
                        "paired_device_identity_mode": "token",
                    },
                },
            },
        },
    }
    observed = []

    def submit(intent):
        files = sorted(path.name for path in intent.candidate_source_root.iterdir())
        manifest = tomllib.loads(
            (intent.candidate_source_root / "manifest.toml").read_text("utf-8")
        )
        assert files == [
            "implementation.py.src", "manifest.lang_state.json", "manifest.toml",
        ]
        assert manifest["code"]["files"] == ["implementation.py.src"]
        assert manifest["args"]["properties"]["device"] == {
            "type": "string",
            "paired_device_identity": "name",
            "paired_device_identity_mode": "token",
            "description": {
                "it": "Argomento device.",
                "en": "Argument device.",
            },
        }
        assert (intent.candidate_source_root / "implementation.py.src").read_bytes() == b"VALUE = 1\n"
        assert intent.reason == (
            "regenerate shipped builtin executor contract; batch attempt 2"
        )
        observed.append(intent.contract_id.value)
        return SimpleNamespace(error_code=None, publication=object())

    monkeypatch.setattr(generator, "OUT", output)
    monkeypatch.setattr(generator, "_all_specs", lambda: {
        "compare_entries": (spec, module),
        "describe_entries": (spec, module),
    })
    monkeypatch.setattr(generator, "_META", {
        name: generator._META[name]
        for name in ("compare_entries", "describe_entries")
    })
    monkeypatch.setattr(executor_birth_intent, "require_birth_intent_adapter", lambda: None)
    monkeypatch.setattr(executor_birth_intent, "submit_builtin_generation_birth", submit)
    monkeypatch.setattr(
        generator, "_birth_candidate_is_current",
        lambda _root, _contract_id: already_current,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_builtin_executor_contracts.py", "--sign",
            "--birth-attempt", "2", "--only", "compare_entries",
        ],
    )

    generator.main()

    assert observed == (
        [] if already_current else ["builtin:compare_entries/manifest.toml"]
    )
    assert sorted(path.name for path in output.iterdir()) == ["unchanged"]
    expected = (
        "published 0 immutable contract generations; 1 already current"
        if already_current
        else "published 1 immutable contract generations; 0 already current"
    )
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("name", [
    "create_tasks", "delete_tasks", "read_tasks", "set_tasks",
    "set_skills", "find_entries", "write_entries",
    "delete_entries", "compare_entries", "describe_images",
    "describe_entries", "classify_entries", "extract_entries",
    "start_lre",
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
    assert out["error_code"] == "ERR_ARGV_STRUCTURE"


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


@pytest.mark.parametrize("existing", [None, "2.3.4"])
def test_builtin_generator_preserves_source_version(tmp_path, monkeypatch, existing):
    import scripts.generate_builtin_executor_contracts as generator

    output = tmp_path / "contracts"
    source = output / "compare_entries" / "manifest.toml"
    if existing is not None:
        source.parent.mkdir(parents=True)
        source.write_text(f'version = "{existing}"\n')
    module = tmp_path / "implementation.py"
    module.write_bytes(b"VALUE = 1\n")
    spec = {"function": {"name": "compare_entries", "parameters": {"type": "object"}}}
    monkeypatch.setattr(generator, "OUT", output)
    monkeypatch.setattr(generator, "_all_specs", lambda: {"compare_entries": (spec, module)})
    monkeypatch.setattr(generator, "_META", {"compare_entries": generator._META["compare_entries"]})
    monkeypatch.setattr(sys, "argv", ["generate"])
    generator.main()
    manifest = tomllib.loads(source.read_text())
    assert manifest["version"] == (existing or generator.INITIAL_BUILTIN_VERSION)
