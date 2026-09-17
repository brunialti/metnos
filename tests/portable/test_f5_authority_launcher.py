"""The launcher's two jobs: refuse anything else, and ask the verifier.

The text under test is extracted from the installer itself, so what is asserted
is what will actually be installed rather than a copy that can drift.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


native = pytest.mark.skipif(not sys.platform.startswith("linux"),
                            reason="the managed launcher is Linux-only")
INSTALLER = Path(__file__).resolve().parents[2] / "internal/tools/install_f5_authority.sh"
ACCEPTED = ["provision-key", "evidence", "migrate plan", "migrate apply",
            "certify derive", "certify issue"]


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def launcher_text() -> str:
    return _between(INSTALLER.read_text(), "<<'LAUNCHER_EOF'\n", "\nLAUNCHER_EOF")


def bootstrap_text() -> str:
    return _between(launcher_text(), "<<'BOOTSTRAP'\n", "\nBOOTSTRAP")


# --- the closed argument set -------------------------------------------------

@pytest.fixture
def launcher(tmp_path):
    """The installed launcher, with its interpreter replaced by an echo."""
    text = launcher_text().replace(
        "exec /usr/bin/python3.12 -I - \"$@\"", 'echo "ACCEPTED $*"; cat >/dev/null; exit 0')
    path = tmp_path / "metnos-f5-authority"
    path.write_text(text)
    path.chmod(0o755)
    return path


@native
@pytest.mark.parametrize("form", ACCEPTED)
def test_every_accepted_form_reaches_the_tool(launcher, form):
    result = subprocess.run(["/bin/sh", str(launcher), *form.split()],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip() == f"ACCEPTED {form}"


@native
@pytest.mark.parametrize("argv", [
    [], ["migrate"], ["certify"], ["provision-key", "extra"],
    ["migrate", "plan", "--force"], ["certify", "sign"], ["evidence", "census"],
    ["MIGRATE", "plan"], ["--help"], ["migrate", "APPLY"],
])
def test_anything_else_is_refused_before_the_tool_runs(launcher, argv):
    result = subprocess.run(["/bin/sh", str(launcher), *argv],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "ACCEPTED" not in result.stdout


def test_the_sudoers_rule_names_exactly_the_accepted_forms():
    text = INSTALLER.read_text()
    rule = _between(text, "NOPASSWD: ", "\nSUDOERS_EOF")
    granted = sorted(item.strip().split("$LAUNCHER ", 1)[1] for item in rule.split(","))
    assert granted == sorted(ACCEPTED)


# --- the release comes from the verifier, and from nothing else --------------

@pytest.fixture
def installation(tmp_path):
    """A fake verifier naming a fake release that names its interpreter."""
    release = tmp_path / "release"
    (release / "deployment").mkdir(parents=True)
    interpreter = tmp_path / "env-python"
    interpreter.write_text(
        "#!/bin/sh\necho \"RAN $* in $(pwd) path=$PYTHONPATH\"\n")
    interpreter.chmod(0o755)
    (release / "deployment/executor-birth-service-catalog-v1.json").write_text(
        json.dumps({"entries": [
            {"entry_id": "service-http", "target_executable": str(interpreter)},
            {"entry_id": "service-durable-worker",
             "target_executable": "/never/used"},
        ]}))
    verifier = tmp_path / "preflight.py"
    verifier.write_text(f'''
from types import SimpleNamespace

def _authenticate_fixed_ownership_snapshot_v1():
    return SimpleNamespace(observed=True)

def _load_installed_preflight_materials_v1(snapshot, *, review_sources):
    assert review_sources is False, "the launcher must not request a source review"
    assert snapshot.observed, "the launcher must pass the verifier's own snapshot"
    return SimpleNamespace(build=SimpleNamespace(facts=SimpleNamespace(
        installation_root="{release}"))), object()
''')
    return release, interpreter, verifier


def run_bootstrap(verifier, argv, tmp_path):
    script = bootstrap_text().replace(
        '"/usr/libexec/metnos/executor-birth-v1/preflight.py"', f'"{verifier}"')
    path = tmp_path / "bootstrap.py"
    path.write_text(script)
    return subprocess.run([sys.executable, "-I", str(path), *argv],
                          capture_output=True, text=True, timeout=60)


@native
def test_the_launcher_runs_the_release_the_verifier_named(installation, tmp_path):
    release, _interpreter, verifier = installation
    result = run_bootstrap(verifier, ["certify", "derive"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "-m install.f5_authority certify derive" in result.stdout
    assert f"in {release}" in result.stdout
    assert f"path={release}:{release}/runtime" in result.stdout


@native
def test_a_release_naming_no_single_interpreter_is_refused(installation, tmp_path):
    release, _interpreter, verifier = installation
    catalog = release / "deployment/executor-birth-service-catalog-v1.json"
    catalog.write_text(json.dumps({"entries": [
        {"entry_id": "service-http", "target_executable": "/one"},
        {"entry_id": "service-http", "target_executable": "/another"},
    ]}))
    result = run_bootstrap(verifier, ["certify", "derive"], tmp_path)
    assert result.returncode != 0
    assert "no single interpreter" in result.stderr


@native
def test_a_verifier_that_refuses_stops_the_launcher(installation, tmp_path):
    release, _interpreter, verifier = installation
    verifier.write_text(
        "def _authenticate_fixed_ownership_snapshot_v1():\n"
        "    raise RuntimeError('effective identity')\n")
    result = run_bootstrap(verifier, ["certify", "derive"], tmp_path)
    assert result.returncode != 0
    assert "effective identity" in result.stderr


@native
def test_the_launcher_keeps_no_pointer_and_reimplements_no_chain():
    """The whole point of the shape: it asks, and it asks only the verifier."""
    bootstrap = bootstrap_text()
    for forbidden in ("releases-v1", "required-head", "selected-release",
                      "systemctl", "chain-v1", "sorted", "max("):
        assert forbidden not in bootstrap, forbidden
    assert bootstrap.count("_load_installed_preflight_materials_v1") == 1
