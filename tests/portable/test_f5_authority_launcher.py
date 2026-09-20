"""The launcher's two jobs: refuse anything else, and ask the verifier.

The text under test is extracted from the installer itself, so what is asserted
is what will actually be installed rather than a copy that can drift.
"""
from __future__ import annotations

import json
import os
import shlex
import stat
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
    # Flags are part of the subject, not delimiters imposed by the harness.
    command = launcher_text().split("exec /usr/bin/python3.12 ", 1)[1]
    return _between(command, " -c '\n", "\n' \"$@\"")


def launcher_environment() -> dict[str, str]:
    """Keep pytest's filesystem isolation without inheriting its remedies.

    The repository-wide plugin exports METNOS_WORKSPACE for every test.
    Inheriting it would repair a launcher missing its own workspace override.
    Python cache settings must not supply or redirect bytecode suppression
    either. The fake release below needs no inherited Metnos configuration.
    """
    return {name: value for name, value in os.environ.items()
            if not name.startswith(("METNOS_", "PYTHON"))}


def tree_snapshot(root: Path) -> dict:
    """Observe entries, bytes and metadata; reading may change atime only."""
    result = {}
    for path in (root, *sorted(root.rglob("*"))):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            content = path.read_bytes()
        elif stat.S_ISLNK(info.st_mode):
            content = os.readlink(path)
        else:
            content = None
        result[str(path.relative_to(root))] = (
            info.st_mode, info.st_uid, info.st_gid, info.st_ino,
            info.st_mtime_ns, content,
        )
    return result


def materialize_launcher(verifier, tmp_path, *, text=None):
    """Substitute installation locations only, never flags or environment."""
    scratch = tmp_path / "admin-workspace"
    scratch.mkdir(exist_ok=True)
    launcher = tmp_path / "metnos-f5-authority"
    launcher.write_text((launcher_text() if text is None else text).replace(
        "/usr/libexec/metnos/executor-birth-v1/preflight.py", str(verifier),
    ).replace("/usr/bin/python3.12", shlex.quote(sys.executable)
    ).replace("/var/lib/metnos-admin/f5-workspace-v1", str(scratch)))
    launcher.chmod(0o755)
    return launcher, scratch


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
        # The real release derives its workspace from the installation root and
        # creates it on import, which is how the launcher once wrote into a
        # signed tree. Reproduce that one line, or the census test is vacuous.
        "import os\n"
        "from pathlib import Path\n"
        "workspace = Path(os.environ.get('METNOS_WORKSPACE')\n"
        "     or Path(__file__).resolve().parents[1] / 'workspace'\n"
        "     )\n"
        "for name in ('.scheduler', '.mnestoma'):\n"
        "    (workspace / name).mkdir(parents=True, exist_ok=True)\n"
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
    interpreter.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
    interpreter.chmod(0o755)
    (release / "deployment/executor-birth-service-catalog-v1.json").write_text(
        json.dumps({"entries": [
            {"entry_id": "service-http", "target_executable": str(interpreter)},
            {"entry_id": "service-durable-worker",
             "target_executable": "/never/used"},
        ]}))
    verifier = tmp_path / "verifier" / "preflight.py"
    verifier.parent.mkdir()
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
    """Exercise bootstrap behavior through the actual shell and its flags."""
    launcher, _scratch = materialize_launcher(verifier, tmp_path)
    return run_launcher(launcher, argv, stdin=stdin)


def run_launcher(installer_launcher, argv, stdin="", *, env=None):
    """Run the whole launcher, shell guard included, end to end."""
    return subprocess.run(["/bin/sh", str(installer_launcher), *argv],
                          input=stdin, capture_output=True, text=True, timeout=60,
                          env=launcher_environment() if env is None else env)


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
def test_the_launcher_leaves_both_trees_untouched(installation, tmp_path):
    """A first call must not make every later call fail.

    Loading the verifier writes bytecode beside it, and importing the release
    writes it inside the signed tree. Both are extra entries that the exact
    tree check refuses, so without -B the launcher works once and then breaks
    itself. `-I` implies `-E`, so PYTHONDONTWRITEBYTECODE cannot prevent it.
    Measured against the real thing: this happened on release 72 on 20/9/2026.
    """
    release, _interpreter, verifier = installation
    launcher, scratch = materialize_launcher(verifier, tmp_path)
    before_release = tree_snapshot(release)
    before_verifier = tree_snapshot(verifier.parent)
    # Both trees remain writable: a missing protection must cause an
    # observable write, not be masked by filesystem permissions.
    for argv in (["provision-key"], ["certify", "derive"]):
        result = run_launcher(launcher, argv)
        assert result.returncode == 0, result.stderr
        assert tree_snapshot(release) == before_release
        assert tree_snapshot(verifier.parent) == before_verifier
    assert (scratch / ".scheduler").is_dir()
    assert (scratch / ".mnestoma").is_dir()


@native
@pytest.mark.parametrize("remedy", ["verifier_bytecode", "release_bytecode", "workspace"])
def test_each_missing_remedy_is_detected(installation, tmp_path, remedy):
    """Negative controls: each mutation runs successfully, then changes a tree."""
    release, _interpreter, verifier = installation
    replacements = {
        "verifier_bytecode": (
            "exec /usr/bin/python3.12 -I -B -c",
            "exec /usr/bin/python3.12 -I -c",
        ),
        "release_bytecode": (
            '[interpreter, "-I", "-B", "-c", stage, *sys.argv[1:]]',
            '[interpreter, "-I", "-c", stage, *sys.argv[1:]]',
        ),
        "workspace": (
            'os.environ["METNOS_WORKSPACE"] = "/var/lib/metnos-admin/f5-workspace-v1"\n',
            "",
        ),
    }
    original = launcher_text()
    old, new = replacements[remedy]
    assert original.count(old) == 1
    mutated = original.replace(old, new, 1)
    launcher, _scratch = materialize_launcher(verifier, tmp_path, text=mutated)
    before_release = tree_snapshot(release)
    before_verifier = tree_snapshot(verifier.parent)
    result = run_launcher(launcher, ["provision-key"])
    assert result.returncode == 0, result.stderr
    if remedy == "verifier_bytecode":
        assert list(verifier.parent.rglob("*.pyc"))
        assert tree_snapshot(verifier.parent) != before_verifier
        assert tree_snapshot(release) == before_release
    else:
        assert tree_snapshot(release) != before_release
        assert tree_snapshot(verifier.parent) == before_verifier
        if remedy == "release_bytecode":
            assert list(release.rglob("*.pyc"))
        else:
            assert (release / "workspace/.scheduler").is_dir()
            assert (release / "workspace/.mnestoma").is_dir()


@native
def test_the_document_survives_the_shell_guard_as_well(installation, tmp_path):
    """End to end: through the argument guard and into the tool."""
    _release, _interpreter, verifier = installation
    launcher, _scratch = materialize_launcher(verifier, tmp_path)
    document = json.dumps({"kind": "start_cycle"})
    result = run_launcher(launcher, ["evidence"], stdin=document)
    assert result.returncode == 0, result.stderr
    assert f"STDIN {document}" in result.stdout
    refused = run_launcher(launcher, ["evidence", "extra"], stdin=document)
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
