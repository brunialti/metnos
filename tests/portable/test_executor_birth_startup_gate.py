from __future__ import annotations

import copy
import os
import pickle
import sys
from pathlib import Path

import pytest

import executor_birth_startup_gate as gate


LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="flock is Linux-only here",
)


def make_gate(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    path = root / "startup-v1.lock"
    path.touch(mode=0o600)
    return path


@LINUX_ONLY
def test_exclusive_gate_emits_one_live_nontransferable_session(tmp_path):
    import fcntl

    path = make_gate(tmp_path)
    with gate._exclusive_startup_gate_for_test_v1(path) as session:
        gate._require_exclusive_startup_gate_session_for_test_v1(
            session, path,
        )
        competing = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_SH | fcntl.LOCK_NB)
        finally:
            os.close(competing)
        for operation in (
            copy.copy, copy.deepcopy,
            lambda value: pickle.loads(pickle.dumps(value)),
        ):
            with pytest.raises(TypeError):
                operation(session)
        with pytest.raises(gate.StartupGateError):
            gate._require_exclusive_startup_gate_session_v1(session)

    with pytest.raises(gate.StartupGateError):
        gate._require_exclusive_startup_gate_session_for_test_v1(
            session, path,
        )


@LINUX_ONLY
@pytest.mark.parametrize(
    "mutation",
    (
        lambda path: path.chmod(0o644),
        lambda path: path.parent.chmod(0o755),
    ),
)
def test_exclusive_gate_rejects_wrong_metadata(tmp_path, mutation):
    path = make_gate(tmp_path)
    mutation(path)

    with pytest.raises(gate.StartupGateError):
        with gate._exclusive_startup_gate_for_test_v1(path):
            pytest.fail("invalid metadata must not emit a session")


@LINUX_ONLY
def test_exclusive_gate_releases_for_the_next_holder(tmp_path):
    path = make_gate(tmp_path)
    with gate._exclusive_startup_gate_for_test_v1(path):
        pass
    with gate._exclusive_startup_gate_for_test_v1(path) as repeated:
        gate._require_exclusive_startup_gate_session_for_test_v1(
            repeated, path,
        )
