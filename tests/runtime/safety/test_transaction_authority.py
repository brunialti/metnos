from types import SimpleNamespace

from frozen_plan_consent import validate_frozen_plan
from sandbox import filesystem_write_extras, undo_history_extras, wrap_command


def _executor(capabilities, properties=None):
    return SimpleNamespace(
        code_path=__import__("pathlib").Path("/tmp/fixture.py"),
        capabilities=capabilities,
        args_schema={"properties": properties or {}},
        reverse_pattern="module.reverse")


def _frozen_manifest(write_capability):
    return {
        "revertible": True, "reverse_pattern": "module.reverse",
        "placement": {"scope": "server"},
        "undo": {"outcome": "per_execution"},
        "args": {"properties": {
            "mode": {"type": "string", "enum": ["preview", "apply"]},
            "paths": {"type": "array", "items": {"type": "string"}},
            "token": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                      "runtime_resolved": True},
        }},
        "execution": {
            "effect": "reversible", "parallelism_class": 0,
            "effects": [{"argument": "mode", "equals": "preview",
                         "effect": "read_only"}],
            "frozen_plan": {
                "argument": "mode", "preview_value": "preview",
                "apply_value": "apply", "token_argument": "token",
                "token_result": "token", "carry_arguments": ["paths"],
                "artifact_suffix": ".plan.json",
                "journal_suffix": ".receipt.json",
                "recovery": "same_token_write_ahead_v1",
            },
        },
        "capabilities": [write_capability,
                         {"name": "metnos:history", "hint": ["turn"]}],
    }


def test_frozen_plan_rejects_unconditional_mutating_capability():
    unsafe = _frozen_manifest({"name": "fs:write", "hint": ["arg:paths"]})
    assert any("conditioned on apply" in finding
               for finding in validate_frozen_plan(unsafe))
    safe = _frozen_manifest({
        "name": "fs:write", "hint": ["arg:paths"],
        "when": {"arg": "mode", "values": ["apply"]}})
    assert validate_frozen_plan(safe) == []
    safe["placement"]["scope"] = "any"
    assert any("placement.scope" in finding
               for finding in validate_frozen_plan(safe))


def test_frozen_plan_uses_mutation_axis_not_criticality_as_proxy():
    noncritical_mutation = _frozen_manifest({
        "name": "channel:out", "hint": ["arg:paths"]})
    assert any("conditioned on apply" in finding
               for finding in validate_frozen_plan(noncritical_mutation))
    critical_read = _frozen_manifest({
        "name": "auth.password_storage", "hint": ["arg:paths"]})
    assert validate_frozen_plan(critical_read) == []


def test_history_authority_requires_exact_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path))
    executor = _executor([{"name": "metnos:history", "hint": ["turn"]}])
    assert undo_history_extras(executor, turn_id="turn-1") == [
        tmp_path / "turn-1" / "blob"]
    assert undo_history_extras(executor, turn_id=None) == []
    assert undo_history_extras(executor, turn_id="") == []
    assert undo_history_extras(executor, turn_id="..") == []


def test_static_and_dynamic_write_mount_exist_only_on_apply(
        tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    code = tmp_path / "fixture.py"
    code.write_text("", encoding="utf-8")
    capabilities = [{
        "name": "fs:write", "hint": [str(root), "arg:roots"],
        "when": {"arg": "mode", "values": ["apply"]}}]
    executor = _executor(capabilities, {
        "roots": {"type": "array", "items": {"type": "string"}},
        "mode": {"type": "string", "enum": ["preview", "apply"]}})
    executor.code_path = code
    monkeypatch.setattr("sandbox.sandbox_disabled", lambda: False)
    monkeypatch.setattr("sandbox.bwrap_available", lambda: True)
    for mode in ("preview", "apply"):
        args = {"mode": mode, "roots": [str(root)]}
        dynamic = filesystem_write_extras(executor, args)
        command = wrap_command(
            executor, ["python3", str(code)], extra_rw=dynamic,
            invocation_args=args)
        binds = [command[index + 1] for index, value in enumerate(command[:-1])
                 if value == "--bind"]
        assert (str(root) in binds) == (mode == "apply")
