"""Native progress must keep the undo boundary until recovery completes."""
import os

import pytest

import protected_undo
from executor_birth_retention import NodeState, RootKind
from test_birth_retention_protected_undo import files, secured
from test_birth_retention_undo import native, objects


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX undo journal")


def test_progress_keeps_the_same_latest_boundary_as_the_native_reader(native):
    native.add("retry", "reversible")
    native.add("later-no-effect")
    native.clock.value += 1
    native.log.append_undone("retry", {"ok_count": 1, "fail_count": 1})

    assert [row["op_id"] for row in native.log.latest_turn_done(actor="host")] == ["retry"]
    observed = objects(native)
    assert observed["retry"].roots == (RootKind.OPEN_AUDIT,)
    assert observed["later-no-effect"].state is NodeState.CLOSED


def test_success_after_progress_allows_native_secret_discard(secured):
    handle, path = secured.create()
    orphan, _ = secured.create()
    secured.attach(handle, partial=True)
    assert files(secured)[orphan].roots == (RootKind.IN_PROGRESS_JOB,)

    secured.undo.clock.value += 1
    secured.undo.log.append_undone("first", {"ok_count": 1, "fail_count": 0})
    assert protected_undo.discard(handle, owner="host", namespace="test.cookie")
    assert not path.exists()
    assert files(secured)[orphan].state is NodeState.CLOSED
