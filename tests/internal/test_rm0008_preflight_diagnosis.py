"""The private diagnosis must preserve denial and never authorize a launch."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def diagnostic():
    path = Path(__file__).resolve().parents[2] / "internal/tools/diagnose_rm0008_preflight.py"
    spec = importlib.util.spec_from_file_location("boot_diagnosis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("denied", [False, True])
def test_only_fixed_check_and_original_denial(diagnostic, monkeypatch, denied):
    calls, messages = [], []

    class PreflightError(RuntimeError):
        detail = "precise reason"

    def run(command):
        calls.append(command)
        if denied:
            raise PreflightError("private payload not to emit")

    module = {
        "require_linux_before_io_v1": lambda: None,
        "parse_cli_v1": lambda args: args,
        "_run_operational_command_v1": run,
        "_public_failure_v1": lambda error: ("birth_ownership_preflight_invalid", 21),
        "PreflightError": PreflightError,
    }
    monkeypatch.setattr(diagnostic, "emit", messages.append)
    assert diagnostic.diagnose(module) == (21 if denied else 0)
    assert calls == [["check", "--entry-id", "service-http"]]
    assert not any("private payload" in message for message in messages)
    assert ("DETAIL precise reason" in messages) == denied


def test_nonroot_refused_before_installed_code_is_read(diagnostic, monkeypatch):
    monkeypatch.setattr(diagnostic.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(diagnostic, "installed_preflight", lambda: pytest.fail("read"))
    assert diagnostic.main() == 78


def test_changed_installed_preflight_is_not_executed(diagnostic, monkeypatch, tmp_path):
    candidate = tmp_path / "preflight.py"
    candidate.write_text("raise AssertionError('must not run')")
    monkeypatch.setattr(diagnostic, "PREFLIGHT", candidate)
    monkeypatch.setattr(diagnostic.runpy, "run_path", lambda *a, **k: pytest.fail("execute"))
    with pytest.raises(RuntimeError, match="identity differs"):
        diagnostic.installed_preflight()
