"""Regression: executor helpers remain visible in relocatable installs."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import sandbox  # noqa: E402


def _executor(**changes):
    values = {
        "name": "example",
        "source": "handcrafted",
        "membership": "builtin",
        "code_dependencies": (),
        "capabilities": [],
        "code_path": Path(__file__),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_runtime_is_bound_read_only_outside_system_roots(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "relocated-metnos" / "runtime"
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "__file__", str(runtime_dir / "sandbox.py"))

    code = tmp_path / "relocated-metnos" / "executors" / "get_now" / "get_now.py"
    code.parent.mkdir(parents=True)
    code.touch()

    args = sandbox._build_bwrap_args(code, capabilities=[])

    index = args.index(str(runtime_dir))
    assert args[index - 1] == "--ro-bind"
    assert args[index + 1] == str(runtime_dir)


def test_system_python_user_site_is_bound_read_only():
    import site

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])
    user_site = Path(site.getusersitepackages())
    if sys.prefix == sys.base_prefix and user_site.exists():
        index = args.index(str(user_site))
        assert args[index - 1] == "--ro-bind"
        assert args[index + 1] == str(user_site)


def _set_python_layout(monkeypatch, prefix, base_prefix):
    executable = prefix / "bin" / "python"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    stdlib = base_prefix / "lib" / "python3.12"
    platstdlib = prefix / "lib" / "python3.12"
    stdlib.mkdir(parents=True, exist_ok=True)
    platstdlib.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sandbox.sys, "prefix", str(prefix))
    monkeypatch.setattr(sandbox.sys, "exec_prefix", str(prefix))
    monkeypatch.setattr(sandbox.sys, "base_prefix", str(base_prefix))
    monkeypatch.setattr(sandbox.sys, "base_exec_prefix", str(base_prefix))
    monkeypatch.setattr(sandbox.sys, "executable", str(executable))
    paths = {"stdlib": str(stdlib), "platstdlib": str(platstdlib)}
    monkeypatch.setattr(
        sandbox.sysconfig, "get_path", lambda name: paths[name],
    )


def test_external_non_venv_python_binds_only_its_exact_prefix(
        tmp_path, monkeypatch):
    prefix = tmp_path / "opt" / "hostedtoolcache" / "Python" / "3.12" / "x64"
    _set_python_layout(monkeypatch, prefix, prefix)

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])

    exact = str(prefix.resolve())
    assert ["--ro-bind", exact, exact] in [
        args[index:index + 3] for index in range(len(args) - 2)
    ]
    assert str(prefix.parent) not in args


def test_venv_with_external_base_binds_both_exact_prefixes(
        tmp_path, monkeypatch):
    prefix = tmp_path / "venv"
    base_prefix = tmp_path / "python-base" / "3.12"
    _set_python_layout(monkeypatch, prefix, base_prefix)

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])

    for root in (prefix.resolve(), base_prefix.resolve()):
        exact = str(root)
        assert ["--ro-bind", exact, exact] in [
            args[index:index + 3] for index in range(len(args) - 2)
        ]


def test_symlinked_python_prefix_preserves_the_command_path(
        tmp_path, monkeypatch):
    real_prefix = tmp_path / "real-python"
    alias_prefix = tmp_path / "python-link"
    real_prefix.mkdir()
    alias_prefix.symlink_to(real_prefix, target_is_directory=True)
    _set_python_layout(monkeypatch, alias_prefix, alias_prefix)

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])

    assert [
        "--ro-bind", str(real_prefix.resolve()), str(alias_prefix.absolute()),
    ] in [args[index:index + 3] for index in range(len(args) - 2)]


def test_broad_interpreter_prefix_fails_closed(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "prefix", "/opt")
    monkeypatch.setattr(sandbox.sys, "exec_prefix", "/opt")
    monkeypatch.setattr(sandbox.sys, "base_prefix", "/opt")
    monkeypatch.setattr(sandbox.sys, "base_exec_prefix", "/opt")

    with pytest.raises(
        sandbox.SandboxUnavailableError, match="too broad to sandbox",
    ):
        sandbox._build_bwrap_args(Path(__file__), capabilities=[])


def test_interpreter_dependency_root_survives_later_home_redirection(
        tmp_path, monkeypatch):
    original_site = tmp_path / "original-home" / "site-packages"
    original_site.mkdir(parents=True)
    monkeypatch.setattr(sys, "path", [str(original_site), *sys.path])
    monkeypatch.setenv("HOME", str(tmp_path / "sandbox-home"))

    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])

    resolved = str(original_site.resolve())
    index = args.index(resolved)
    assert args[index - 1] == "--ro-bind"
    assert args[index + 1] == resolved


@pytest.mark.parametrize(
    "executor",
    (
        _executor(),
        _executor(source="synthesized"),
        _executor(source="imported", membership="third-party"),
        _executor(code_dependencies=("parent",)),
    ),
)
def test_executor_code_has_no_naked_fallback(
        executor, monkeypatch):
    monkeypatch.setenv("METNOS_SANDBOX", "0")
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: False)

    with pytest.raises(sandbox.SandboxUnavailableError):
        sandbox.wrap_command(executor, ["python3", "executor.py"])


def test_only_the_pinned_builtin_undo_broker_may_use_the_naked_broker_path(
        monkeypatch):
    monkeypatch.setenv("METNOS_SANDBOX", "0")
    monkeypatch.setattr(sandbox, "bwrap_available", lambda: False)
    trusted = _executor(
        name="undo_last_turn",
        capabilities=[{"name": "system:undo"}],
        digest=sandbox._TRUSTED_UNDO_BROKER_DIGEST_V1,
        code_files=("undo_last_turn.py",),
    )
    hostile = _executor(
        name="undo_last_turn",
        capabilities=[{"name": "system:undo"}],
        digest="sha256:" + "0" * 64,
        code_files=("undo_last_turn.py",),
    )

    assert sandbox.wrap_command(trusted, ["python3", "undo.py"]) == [
        "python3", "undo.py",
    ]
    with pytest.raises(sandbox.SandboxUnavailableError):
        sandbox.wrap_command(hostile, ["python3", "other.py"])


def test_default_mounts_do_not_expose_birth_authorities_or_user_store(
        tmp_path, monkeypatch):
    import config
    import site

    monkeypatch.setattr(
        site, "getusersitepackages", lambda: str(tmp_path / "missing-site"),
    )
    monkeypatch.setattr(sandbox, "python_package_roots", lambda: ())
    monkeypatch.setattr(config, "DB_I18N", tmp_path / "missing-i18n.sqlite")
    monkeypatch.setattr(
        config, "DB_DETECTION", tmp_path / "missing-detection.sqlite",
    )
    args = sandbox._build_bwrap_args(Path(__file__), capabilities=[])
    mounted = {
        args[index + 1]
        for index, token in enumerate(args[:-1])
        if token in {"--bind", "--ro-bind"}
    }

    assert "/var/lib/metnos/executor-birth" not in mounted
    assert "/opt" not in mounted
    assert not any(path.startswith("/home/") for path in mounted)


def test_sealed_files_hide_and_lock_their_sibling_namespace(tmp_path):
    import admitted_module_v1 as admitted

    assert admitted._PROJECTED_TRUST_ROOT_V1 == sandbox._PROJECTED_TRUST_ROOT_V1
    public = tmp_path / "config" / "birth" / "author-root-v1" / "public"
    public.mkdir(parents=True)
    selected = public / "birth-key.pub"
    unrelated = public / "unrelated.pub"
    selected.write_bytes(b"s" * 32)
    unrelated.write_bytes(b"u" * 32)

    args = sandbox._build_bwrap_args(
        Path(__file__), capabilities=[], sealed_ro_files=[selected],
    )

    sealed = sandbox._PROJECTED_TRUST_ROOT_V1
    tmpfs = ["--tmpfs", str(sealed)]
    selected_bind = [
        "--ro-bind", str(selected), str(sealed / selected.name),
    ]
    remount = ["--remount-ro", str(sealed)]
    tmpfs_at = next(
        index for index in range(len(args))
        if args[index:index + len(tmpfs)] == tmpfs
    )
    bind_at = next(
        index for index in range(len(args))
        if args[index:index + len(selected_bind)] == selected_bind
    )
    remount_at = next(
        index for index in range(len(args))
        if args[index:index + len(remount)] == remount
    )
    assert tmpfs_at < bind_at < remount_at
    assert str(unrelated) not in args
    assert "--disable-userns" in args
    assert "--assert-userns-disabled" in args


def test_sealed_file_basename_collisions_fail_closed(tmp_path):
    first = tmp_path / "first" / "author_pub.bin"
    second = tmp_path / "second" / "author_pub.bin"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"a" * 32)
    second.write_bytes(b"b" * 32)

    with pytest.raises(
        sandbox.SandboxUnavailableError, match="names collide",
    ):
        sandbox._build_bwrap_args(
            Path(__file__), capabilities=[],
            sealed_ro_files=[first, second],
        )


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not sandbox.bwrap_available(),
    reason="requires the Linux Bubblewrap executor sandbox",
)
@pytest.mark.parametrize("with_key", [False, True])
def test_projected_trust_namespace_resists_child_filesystem_changes(
        tmp_path, monkeypatch, with_key):
    probe = tmp_path / "probe.py"
    probe.write_text(
        """import ctypes, errno, os
from pathlib import Path
from admitted_module_v1 import _projected_trusted_public_keys_v1
root = Path('/.metnos-admission-v1')
assert bool(os.statvfs(root).f_flag & os.ST_RDONLY)
expected = ['selected_pub.bin'] if os.environ['WITH_KEY'] == '1' else []
assert sorted(path.name for path in root.iterdir()) == expected
if expected:
    assert (root / expected[0]).read_bytes() == b'k' * 32
assert len(_projected_trusted_public_keys_v1()) == len(expected)
for action in (
    lambda: (root / 'forged_pub.bin').write_bytes(b'f' * 32),
    lambda: root.rename('/.metnos-admission-moved-v1'),
):
    try:
        action()
    except OSError:
        pass
    else:
        raise AssertionError('sealed trust namespace was mutable')
libc = ctypes.CDLL(None, use_errno=True)
assert libc.unshare(0x10000000 | 0x00020000) == -1
assert ctypes.get_errno() in {errno.EPERM, errno.ENOSPC}
Path('/tmp/admission-probe-writable').write_text('ok')
Path(os.environ['DECLARED_RW']).joinpath('allowed').write_text('ok')
""",
        encoding="utf-8",
    )
    writable = tmp_path / "writable"
    writable.mkdir()
    selected = tmp_path / "selected_pub.bin"
    selected.write_bytes(b"k" * 32)
    selected.chmod(0o644)
    executor = _executor(code_path=probe)
    monkeypatch.setattr(sandbox, "__file__", str(_RUNTIME / "sandbox.py"))
    command = sandbox.wrap_command(
        executor,
        [sys.executable, str(probe)],
        extra_rw=[writable],
        sealed_ro_files=[selected] if with_key else [],
        force_net=True,
    )
    process = subprocess.run(
        command, capture_output=True, text=True, timeout=15,
        env={
            **os.environ,
            "DECLARED_RW": str(writable),
            "PYTHONPATH": str(_RUNTIME),
            "WITH_KEY": "1" if with_key else "0",
        },
        check=False,
    )
    if process.returncode and "bwrap:" in process.stderr and any(
        value in process.stderr
        for value in ("Operation not permitted", "Permission denied")
    ):
        pytest.skip("host denied Bubblewrap namespace creation")
    assert process.returncode == 0, process.stderr
    assert (writable / "allowed").read_text(encoding="utf-8") == "ok"
