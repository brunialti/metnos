"""The launcher's two jobs: refuse anything else, and ask the verifier.

The text under test is extracted from the installer itself, so what is asserted
is what will actually be installed rather than a copy that can drift.
The installer is private administrative tooling: these tests belong in the
internal suite and must not be exported without their subject.
"""
from __future__ import annotations

import ast
import json
import os
import shlex
import shutil
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
    # Exercise the actual import-time filesystem initialization, including
    # permission repairs, instead of maintaining an imitation of config.
    shutil.copyfile(INSTALLER.parents[2] / "runtime/config.py",
                    release / "runtime/config.py")
    (release / "install/f5_authority.py").write_text(
        "import sys\n"
        "import config\n"
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
    """Missing flags change a tree; a missing workspace now refuses safely."""
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
    if remedy == "workspace":
        # The new path guard independently prevents this old corruption.
        # The successful-launch assertion still detects the missing override.
        assert result.returncode == 1, result.stderr
        assert "refused: unsafe F5 path METNOS_WORKSPACE" in result.stderr
        assert tree_snapshot(release) == before_release
        assert tree_snapshot(verifier.parent) == before_verifier
        return
    assert result.returncode == 0, result.stderr
    if remedy == "verifier_bytecode":
        assert list(verifier.parent.rglob("*.pyc"))
        assert tree_snapshot(verifier.parent) != before_verifier
        assert tree_snapshot(release) == before_release
    else:
        assert tree_snapshot(release) != before_release
        assert tree_snapshot(verifier.parent) == before_verifier
        assert list(release.rglob("*.pyc"))


def profile_environment(tmp_path):
    """Isolate user data without supplying workspace or bytecode remedies."""
    env = launcher_environment()
    for suffix in ("DATA", "STATE", "CONFIG", "CACHE"):
        env["METNOS_USER_" + suffix] = str(tmp_path / "profile" / suffix.lower())
    return env


def assert_path_refused(installation, tmp_path, env, name):
    release, _interpreter, verifier = installation
    launcher, _scratch = materialize_launcher(verifier, tmp_path)
    before_release = tree_snapshot(release)
    before_verifier = tree_snapshot(verifier.parent)
    result = run_launcher(launcher, ["provision-key"], env=env)
    assert result.returncode == 1, result.stderr
    assert result.stderr.strip() == "refused: unsafe F5 path " + name
    assert "ARGV" not in result.stdout
    assert tree_snapshot(release) == before_release
    assert tree_snapshot(verifier.parent) == before_verifier


@native
@pytest.mark.parametrize("suffix", ["DATA", "STATE", "CONFIG", "CACHE"])
@pytest.mark.parametrize("target_tree", ["release", "verifier"])
def test_inherited_mutable_roots_cannot_touch_either_tree(
    installation, tmp_path, suffix, target_tree,
):
    release, _interpreter, verifier = installation
    target = release / "runtime" if target_tree == "release" else verifier.parent
    marker = target / "already-signed.sig"
    marker.write_bytes(b"existing signed-file witness\n")
    marker.chmod(0o444)
    name = "METNOS_USER_" + suffix
    env = profile_environment(tmp_path)
    env[name] = str(target)
    assert_path_refused(installation, tmp_path, env, name)


@native
@pytest.mark.parametrize("relative", [".", "runtime", "../outside"])
def test_relative_roots_are_refused_before_changing_directory(
    installation, tmp_path, relative,
):
    env = profile_environment(tmp_path)
    env["METNOS_USER_CONFIG"] = relative
    assert_path_refused(installation, tmp_path, env, "METNOS_USER_CONFIG")


@native
def test_dotdot_cannot_create_a_protected_directory_on_the_way_out(
    installation, tmp_path,
):
    release, _interpreter, _verifier = installation
    env = profile_environment(tmp_path)
    env["METNOS_USER_CONFIG"] = str(release / "new/../../outside")
    assert_path_refused(installation, tmp_path, env, "METNOS_USER_CONFIG")


@native
@pytest.mark.parametrize("target_tree", ["release", "verifier"])
def test_a_linked_parent_cannot_hide_a_destination_in_a_protected_tree(
    installation, tmp_path, target_tree,
):
    release, _interpreter, verifier = installation
    target = release if target_tree == "release" else verifier.parent
    alias = tmp_path / "outside-link"
    alias.symlink_to(target, target_is_directory=True)
    env = profile_environment(tmp_path)
    env["METNOS_USER_CONFIG"] = str(alias / "new-config")
    assert_path_refused(installation, tmp_path, env, "METNOS_USER_CONFIG")


@native
def test_a_mutable_ancestor_of_a_protected_tree_is_refused(installation, tmp_path):
    env = profile_environment(tmp_path)
    env["METNOS_USER_CONFIG"] = str(tmp_path)
    assert_path_refused(installation, tmp_path, env, "METNOS_USER_CONFIG")


@native
def test_home_defaults_cannot_bypass_the_check_through_a_link(installation, tmp_path):
    release, _interpreter, _verifier = installation
    profile_home = tmp_path / "profile-home"
    profile_home.mkdir()
    (profile_home / ".config").symlink_to(release / "runtime", target_is_directory=True)
    env = profile_environment(tmp_path)
    env["HOME"] = str(profile_home)
    del env["METNOS_USER_CONFIG"]
    assert_path_refused(installation, tmp_path, env, "METNOS_USER_CONFIG")


@native
def test_valid_external_paths_are_preserved_and_still_initialized(installation, tmp_path):
    release, _interpreter, verifier = installation
    env = profile_environment(tmp_path)
    # A shared textual prefix is not containment in the release tree.
    external = tmp_path / "release-sibling"
    external.mkdir()
    record = external / "account.sig"
    record.write_bytes(b"existing administrator data\n")
    record.chmod(0o644)
    env["METNOS_USER_CONFIG"] = str(external)
    launcher, scratch = materialize_launcher(verifier, tmp_path)
    before_release = tree_snapshot(release)
    before_verifier = tree_snapshot(verifier.parent)
    for _ in range(2):
        result = run_launcher(launcher, ["certify", "derive"], env=env)
        assert result.returncode == 0, result.stderr
        assert tree_snapshot(release) == before_release
        assert tree_snapshot(verifier.parent) == before_verifier
    assert record.read_bytes() == b"existing administrator data\n"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    for suffix in ("DATA", "STATE", "CONFIG"):
        assert Path(env["METNOS_USER_" + suffix]).is_dir()
    assert (scratch / ".scheduler").is_dir()
    assert (scratch / ".mnestoma").is_dir()


@native
def test_removing_the_path_check_reproduces_the_real_permission_damage(
    installation, tmp_path,
):
    release, _interpreter, verifier = installation
    marker = release / "runtime/already-signed.sig"
    marker.write_bytes(b"signed-file witness\n")
    marker.chmod(0o444)
    check = "    require_external_path(name, Path(os.environ.get(name) or default))"
    original = launcher_text()
    assert original.count(check) == 1
    launcher, _scratch = materialize_launcher(
        verifier, tmp_path, text=original.replace(check, "    pass", 1))
    env = profile_environment(tmp_path)
    env["METNOS_USER_CONFIG"] = str(release / "runtime")
    before = tree_snapshot(release)
    result = run_launcher(launcher, ["provision-key"], env=env)
    assert result.returncode == 0, result.stderr
    after = tree_snapshot(release)
    assert set(before) == set(after)
    assert after != before
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600


@pytest.fixture(scope="module")
def real_f5_sources():
    """Copy source dependencies, without importing application code in pytest."""
    repo = INSTALLER.parents[2]
    pending = ["install.f5_authority"]
    seen, files = set(), set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        relative = Path(*name.split("."))
        candidates = [path for base in (repo, repo / "runtime")
                      for path in (base / relative.with_suffix(".py"),
                                   base / relative / "__init__.py")]
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            continue
        files.add(source)
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                pending.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                pending.append(node.module)
    return [(source, source.relative_to(repo)) for source in sorted(files)]


@native
@pytest.mark.skipif(getattr(os, "geteuid", lambda: 0)() == 0,
                    reason="the real administrative command must refuse before provisioning")
@pytest.mark.parametrize("tainted", [False, True])
def test_real_f5_imports_preserve_the_trees(
    installation, tmp_path, real_f5_sources, tainted,
):
    release, _interpreter, verifier = installation
    for source, relative in real_f5_sources:
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    marker = release / "runtime/already-signed.sig"
    marker.write_bytes(b"signed-file witness\n")
    marker.chmod(0o444)
    env = profile_environment(tmp_path)
    if tainted:
        env["METNOS_USER_CONFIG"] = str(release / "runtime")
    launcher, _scratch = materialize_launcher(verifier, tmp_path)
    before_release = tree_snapshot(release)
    before_verifier = tree_snapshot(verifier.parent)
    result = run_launcher(launcher, ["provision-key"], env=env)
    assert result.returncode == 1, result.stderr
    if tainted:
        assert result.stderr.strip() == "refused: unsafe F5 path METNOS_USER_CONFIG"
    else:
        assert json.loads(result.stderr)["error"] == "birth_certification_authority_root_required"
    assert tree_snapshot(release) == before_release
    assert tree_snapshot(verifier.parent) == before_verifier


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
