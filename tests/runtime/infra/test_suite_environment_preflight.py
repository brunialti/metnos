from __future__ import annotations

import errno
from pathlib import Path

from _runtime_conftest import module as conftest


def test_full_suite_detection_does_not_block_focused_tests(tmp_path) -> None:
    root = conftest._TESTS_ROOT
    assert conftest._full_runtime_suite_requested(
        [str(root)], cwd=tmp_path)
    assert not conftest._full_runtime_suite_requested(
        [str(root / "test_executor_standard.py")], cwd=tmp_path)
    assert not conftest._full_runtime_suite_requested(
        [f"{root / 'test_executor_standard.py'}::test_conforming_manifest_passes"],
        cwd=tmp_path,
    )


def test_environment_probe_reports_read_only_state_and_socket(
        tmp_path, monkeypatch) -> None:
    writable = tmp_path / "writable"
    writable.mkdir()
    read_only = tmp_path / "readonly"
    read_only.mkdir()

    class _Stat:
        f_flag = conftest.os.ST_RDONLY

    monkeypatch.setattr(conftest.os, "statvfs", lambda _path: _Stat())
    monkeypatch.setattr(conftest.os, "access", lambda *_args: False)

    class _DeniedSocket:
        def __init__(self, *_args, **_kwargs):
            raise PermissionError(errno.EPERM, "blocked by sandbox")

    monkeypatch.setattr(conftest.socket, "socket", _DeniedSocket)
    issues = conftest._full_suite_environment_issues(
        env={
            "METNOS_USER_DATA": str(read_only),
            "METNOS_USER_STATE": str(read_only),
            "METNOS_USER_CONFIG": str(read_only),
        },
        home=writable,
    )

    assert len([issue for issue in issues if "read-only path" in issue]) == 3
    assert any("localhost sockets unavailable" in issue for issue in issues)


def test_current_capable_environment_probe_can_be_faked_green(
        tmp_path, monkeypatch) -> None:
    class _Stat:
        f_flag = 0

    class _Socket:
        def bind(self, _address):
            return None

        def close(self):
            return None

    monkeypatch.setattr(conftest.os, "statvfs", lambda _path: _Stat())
    monkeypatch.setattr(conftest.os, "access", lambda *_args: True)
    monkeypatch.setattr(conftest.socket, "socket", lambda *_args: _Socket())
    env = {
        "METNOS_USER_DATA": str(tmp_path),
        "METNOS_USER_STATE": str(tmp_path),
        "METNOS_USER_CONFIG": str(tmp_path),
    }
    assert conftest._full_suite_environment_issues(
        env=env, home=tmp_path) == []


def test_test_session_redirects_every_mutable_root(tmp_path) -> None:
    env: dict[str, str] = {}
    root = conftest._activate_test_session(
        env=env, root=tmp_path / "session")

    assert root == tmp_path / "session"
    assert Path(env["HOME"]) == root / "home"
    assert Path(env["METNOS_USER_DATA"]).is_relative_to(root)
    state = Path(env["METNOS_USER_STATE"])
    config = Path(env["METNOS_USER_CONFIG"])
    workspace = Path(env["METNOS_WORKSPACE"])
    index = Path(env["METNOS_INDEX_ROOT"])
    scheduler_db = Path(env["METNOS_SCHEDULER_V2_DB"])
    lockfile = Path(env["METNOS_HTTP_LOCKFILE"])
    for path in (state, config, workspace, index, scheduler_db, lockfile):
        assert path.is_relative_to(root)
    assert state.is_dir()


def test_seed_snapshot_excludes_private_keys_and_credentials(tmp_path) -> None:
    source_home = tmp_path / "source-home"
    source_data = source_home / ".local/share/metnos"
    source_config = source_home / ".config/metnos"
    (source_data / "executors/demo").mkdir(parents=True)
    (source_data / "executors/demo/manifest.toml").write_text("name='demo'")
    (source_config / "keys").mkdir(parents=True)
    (source_config / "keys/real_pub.bin").write_bytes(b"p" * 32)
    (source_config / "keys/real_priv.bin").write_bytes(b"s" * 32)
    (source_config / "credentials.env").write_text("TOKEN=secret")

    root = tmp_path / "session"
    env: dict[str, str] = {}
    conftest._activate_test_session(
        env=env,
        root=root,
        seed=True,
        source_home=source_home,
        source_data=source_data,
        source_config=source_config,
    )

    test_config = Path(env["METNOS_USER_CONFIG"])
    assert (Path(env["METNOS_USER_DATA"]) /
            "executors/demo/manifest.toml").is_file()
    assert (test_config / "keys/installed_real_pub.bin").is_file()
    assert not (test_config / "keys/real_priv.bin").exists()
    assert not (test_config / "credentials.env").exists()
    assert (test_config / "keys/author_priv.bin").is_file()
