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
    return _between(launcher_text(), "exec /usr/bin/python3.12 -I -B -c '", "\n' \"$@\"")


# --- the closed argument set -------------------------------------------------

@pytest.fixture
def launcher(tmp_path):
    """The installed launcher, with its interpreter replaced by an echo."""
    start = launcher_text().split("exec /usr/bin/python3.12", 1)[0]
    text = start + 'echo "ACCEPTED $*"\nexit 0\n'
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
    """A fake release with a real interpreter and a real module to import.

    The interpreter is real on purpose: an echoing stub cannot tell whether
    `-m` would have found anything, and the first version of this launcher was
    unrunnable for exactly that reason.
    """
    release = tmp_path / "release"
    (release / "deployment").mkdir(parents=True)
    (release / "install").mkdir(parents=True)
    (release / "runtime").mkdir(parents=True)
    (release / "install/__init__.py").write_text("")
    (release / "install/f5_authority.py").write_text(
        "import sys\n"
        "def main(argv=None):\n"
        "    import runtime_marker\n"
        "    payload = sys.stdin.read()\n"
        "    print('ARGV', *sys.argv[1:])\n"
        "    print('STDIN', payload.strip() or '<empty>')\n"
        "    print('RUNTIME', runtime_marker.NAME)\n"
        "    return 0\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n")
    (release / "runtime/runtime_marker.py").write_text("NAME = 'release-runtime'\n")
    interpreter = tmp_path / "env-python"
    interpreter.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
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


def run_bootstrap(verifier, argv, tmp_path, stdin=""):
    """Run the real bootstrap, with a real interpreter, exactly as installed."""
    script = bootstrap_text().replace(
        '"/usr/libexec/metnos/executor-birth-v1/preflight.py"', f'"{verifier}"')
    return subprocess.run([sys.executable, "-I", "-c", script, *argv],
                          input=stdin, capture_output=True, text=True, timeout=60)


def run_launcher(installer_launcher, verifier, argv, stdin=""):
    """Run the whole launcher, shell guard included, end to end."""
    return subprocess.run(["/bin/sh", str(installer_launcher), *argv],
                          input=stdin, capture_output=True, text=True, timeout=60)


@native
def test_the_launcher_really_imports_the_release_it_was_told_about(
    installation, tmp_path,
):
    """A real interpreter, so an unimportable module cannot look like success."""
    _release, _interpreter, verifier = installation
    result = run_bootstrap(verifier, ["certify", "derive"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ARGV certify derive" in result.stdout
    # The release's own runtime is importable too, not only its install package.
    assert "RUNTIME release-runtime" in result.stdout


@native
def test_the_evidence_document_survives_the_whole_launcher(installation, tmp_path):
    """The bootstrap must not eat standard input: the document arrives on it."""
    _release, _interpreter, verifier = installation
    document = json.dumps({"kind": "start_cycle"})
    result = run_bootstrap(verifier, ["evidence"], tmp_path, stdin=document)
    assert result.returncode == 0, result.stderr
    assert f"STDIN {document}" in result.stdout


@native
def test_the_launcher_writes_no_bytecode_anywhere(installation, tmp_path):
    """A first call must not make every later call fail.

    Loading the verifier writes bytecode beside it, and importing the release
    writes it inside the signed tree. Both are extra entries that the exact
    tree check refuses, so without -B the launcher works once and then breaks
    itself. `-I` implies `-E`, so PYTHONDONTWRITEBYTECODE cannot prevent it.
    Measured against the real thing: this happened on release 72 on 20/9/2026.
    """
    release, _interpreter, verifier = installation
    launcher = tmp_path / "metnos-f5-authority"
    # Only the interpreter path is substituted. The flags must come from the
    # installer, otherwise this measures the harness instead of the launcher.
    launcher.write_text(launcher_text().replace(
        "/usr/libexec/metnos/executor-birth-v1/preflight.py", str(verifier),
    ).replace("/usr/bin/python3.12", sys.executable))
    launcher.chmod(0o755)
    result = run_launcher(launcher, verifier, ["provision-key"])
    assert result.returncode == 0, result.stderr
    written = sorted(
        str(path) for root in (release, verifier.parent)
        for path in Path(root).rglob("*")
        if path.name == "__pycache__" or path.suffix == ".pyc"
    )
    assert written == [], written


@native
def test_the_document_survives_the_shell_guard_as_well(installation, tmp_path):
    """End to end: through the argument guard and into the tool."""
    _release, _interpreter, verifier = installation
    launcher = tmp_path / "metnos-f5-authority"
    launcher.write_text(launcher_text().replace(
        "/usr/libexec/metnos/executor-birth-v1/preflight.py", str(verifier)
    ).replace("/usr/bin/python3.12", sys.executable))
    launcher.chmod(0o755)
    document = json.dumps({"kind": "start_cycle"})
    result = run_launcher(launcher, verifier, ["evidence"], stdin=document)
    assert result.returncode == 0, result.stderr
    assert f"STDIN {document}" in result.stdout
    refused = run_launcher(launcher, verifier, ["evidence", "extra"], stdin=document)
    assert refused.returncode == 2


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
    # Prose may name what the code avoids; the assertion is about the code.
    bootstrap = "\n".join(line for line in bootstrap_text().splitlines()
                          if not line.lstrip().startswith("#"))
    for forbidden in ("releases-v1", "required-head", "selected-release",
                      "systemctl", "chain-v1", "sorted", "max(", "PYTHONPATH"):
        assert forbidden not in bootstrap, forbidden
    assert bootstrap.count("_load_installed_preflight_materials_v1") == 1
    # Isolation is kept and the paths are explicit, which is the only way both
    # can be true at once.
    assert "-I" in bootstrap and "sys.path[:0]" in bootstrap
