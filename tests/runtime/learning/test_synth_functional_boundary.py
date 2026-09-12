from __future__ import annotations

import inspect
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import executor_birth_functional as functional
import synth_request
import synt
from generated_executor_contract import GeneratedContractError, validate_generated_manifest_text
from synt_multistage import validate_stage3
from tests.runtime.learning.test_synth_candidate_admission import _candidate_run


def cases():
    return _candidate_run().stages[2].output["tests"]


def test_multistage_checks_private_staging_before_any_birth(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(synth_request, "require_synth_birth_service", lambda: None)
    monkeypatch.setattr(synth_request, "SYNTHESIZED_EXECUTORS_DIR", tmp_path)
    def validate(root):
        assert root != tmp_path / "find_files"
        assert (root / "manifest.toml").is_file()
        events.append(("validate", root))
        return None
    def submit(data):
        assert events[0] == ("validate", data.candidate_root)
        events.append(("birth", data.candidate_root))
        return SimpleNamespace(publication=object(), error_code=None)
    monkeypatch.setattr(synth_request, "_validate_birth_tests", validate)
    monkeypatch.setattr(synth_request, "submit_synth_multistage", submit)
    synth_request._install_synthesized(_candidate_run(), "trova file", "trova file")
    assert [item[0] for item in events] == ["validate", "birth"]
    assert not events[0][1].parent.exists()


def test_functional_rejection_never_submits_or_removes_authoring(tmp_path, monkeypatch):
    existing = tmp_path / "find_files"
    existing.mkdir()
    sentinel = existing / "manifest.toml"
    sentinel.write_bytes(b"previous-authoring\n")
    monkeypatch.setattr(synth_request, "require_synth_birth_service", lambda: None)
    monkeypatch.setattr(synth_request, "SYNTHESIZED_EXECUTORS_DIR", tmp_path)
    roots = []
    monkeypatch.setattr(synth_request, "_validate_birth_tests",
                        lambda root: roots.append(root) or "test_environment_unavailable")
    def forbidden(*_args):
        raise AssertionError("Birth called before functional success")
    monkeypatch.setattr(synth_request, "submit_synth_multistage", forbidden)
    with pytest.raises(RuntimeError, match="test_environment_unavailable"):
        synth_request._install_synthesized(_candidate_run(), "trova file", "trova file")
    assert sentinel.read_bytes() == b"previous-authoring\n"
    assert not roots[0].parent.exists()
    # The turn handler must not attempt to undo a durable admission by deleting
    # its authoring projection after _install_synthesized returns.
    assert "shutil.rmtree" not in inspect.getsource(synth_request.handle_synth_request)


@pytest.mark.parametrize("field", ["setup", "teardown", "env", "reference"])
@pytest.mark.parametrize("value", ["", "unsafe shell"])
def test_stage_renderer_and_common_gate_reject_unsafe_fields(tmp_path, monkeypatch, field, value):
    run = _candidate_run()
    run.stages[2].output["tests"][0][field] = value
    assert validate_stage3(run.stages[2].output) is not None
    monkeypatch.setattr(synth_request, "require_synth_birth_service", lambda: None)
    monkeypatch.setattr(synth_request, "SYNTHESIZED_EXECUTORS_DIR", tmp_path)
    with pytest.raises(ValueError, match="fields_invalid"):
        synth_request._install_synthesized(run, "trova file", "trova file")
    from tests.runtime.skills.test_generated_executor_contract import _manifest
    text = _manifest() + '\n[[tests]]\nname="case"\ninput={}\nexpect={ok=true}\n'
    text += f'{field} = {json.dumps(value)}\n'
    with pytest.raises(GeneratedContractError, match="declarative"):
        validate_generated_manifest_text(text, expected_lifecycle="synthesized")


def test_single_stage_generator_uses_same_data_only_adapter(tmp_path, monkeypatch):
    seen = []
    generated = cases()
    result = SimpleNamespace(
        latency_ms=1, in_tokens=1, out_tokens=1,
        tool_calls=[SimpleNamespace(name="propose_birth_tests", arguments={"tests": generated})],
    )
    router = SimpleNamespace(chat_with_tools=lambda *_a, **_k: result)
    instance = object.__new__(synt.Synt)
    instance.router = router
    proposal = SimpleNamespace(name="find_files", description="test", purpose="test",
                               output_summary="ok", python_code="pass\n", proposal_dir=tmp_path)
    report = functional.SynthTestReport(tuple({**case, "passed": True} for case in generated))
    monkeypatch.setattr(synt, "validate_synth_tests", lambda data: seen.append(data) or report)
    actual = instance._run_birth_tests(SimpleNamespace(), proposal)
    assert actual["all_passed"] is True
    assert actual["passed_count"] == 3
    assert seen[0].source_bytes == proposal.python_code.encode()
    assert seen[0].cases() == generated
    assert not hasattr(synt.Synt, "_exec_birth_test")


@pytest.mark.parametrize("report", [functional.SynthTestReport(), functional.SynthTestReport(error_code="test_environment_unavailable")])
def test_multistage_never_accepts_an_empty_or_unavailable_report(tmp_path, monkeypatch, report):
    root = tmp_path / "find_files"
    root.mkdir()
    (root / "find_files.py").write_text("pass\n")
    test_text = '\n'.join(
        '[[tests]]\nname="case_' + str(i) + '"\ninput={}\nexpect={ok=true}' for i in range(3)
    )
    (root / "manifest.toml").write_text('[code]\nfiles=["find_files.py"]\n' + test_text)
    monkeypatch.setattr(synth_request, "validate_synth_tests", lambda data: report)
    assert synth_request._validate_birth_tests(root) is not None


@pytest.mark.parametrize("lang", ["it", "en"])
def test_effective_prose_prompts_have_no_shell_examples(monkeypatch, lang):
    import prompt_loader
    import yaml
    monkeypatch.setenv("METNOS_SYNT_FORMAT", "prose")
    text = prompt_loader.get("synt_tests", lang, name="change_texts", args_required=[],
                             args_properties_summary={}, capabilities=[], revertible=False,
                             reverse_pattern=None, user_request="change case")
    assert "mkdir -p" not in text and "rm -rf" not in text
    assert '"setup":' not in text and '"teardown":' not in text
    raw = yaml.safe_load((Path(prompt_loader.__file__).parent / "prompts" / lang / "synt_tests.yaml").read_text())
    for item in raw["examples"]:
        functional.validate_functional_cases(item["output"]["tests"])


@pytest.mark.parametrize("lang", ["it", "en"])
@pytest.mark.parametrize("style", ["yaml_raw", "json_raw"])
def test_structured_prompt_content_is_declarative(lang, style):
    # Directly test the structured renderer. The public loader has a separate
    # pre-existing duplicate-lang failure, recorded as C18, not fixed here.
    import prompt_loader
    import yaml
    path = Path(prompt_loader.__file__).parent / "prompts" / lang / "synt_tests.yaml"
    text = prompt_loader._render_synt_yaml(path, lang, style, name="change_texts")
    value = json.loads(text) if style == "json_raw" else yaml.safe_load(text)
    assert "mkdir -p" not in text and "rm -rf" not in text
    for example in value["examples"]:
        functional.validate_functional_cases(example["output"]["tests"])
