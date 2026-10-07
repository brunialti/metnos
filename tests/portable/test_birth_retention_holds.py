"""Administrative holds protect real collection, including interrupted changes."""
from __future__ import annotations

from dataclasses import replace
import os
import signal
import sys

import pytest

from executor_birth_authority_files import OwnershipAuthorityError
from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_maintenance import (
    _digest, _update_holds, plan, read_holds,
)
from test_birth_retention_maintenance import context, NOW, RUN


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native Linux administrative custody")


@pytest.fixture
def root(tmp_path):
    tmp_path.chmod(0o755)
    return tmp_path


def update(root, holds=(), expected=None, crash=None, require=lambda: None):
    return _update_holds(root, root_owned=False, holds=frozenset(holds),
                         expected_version=expected, require_exclusion=require, crash=crash)


HOLD = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64


def test_explicit_initialize_add_remove_and_stale_change(root):
    with pytest.raises(OwnershipAuthorityError):
        read_holds(root, root_owned=False)
    empty = update(root)
    assert read_holds(root, root_owned=False) == frozenset()
    assert (root / "holds.json").stat().st_mode & 0o777 == 0o600
    held = update(root, [HOLD], empty)
    assert held != empty
    with pytest.raises(RetentionError, match="retention_holds_changed"):
        update(root, [OTHER], empty)
    assert read_holds(root, root_owned=False) == frozenset({HOLD})
    # A lost acknowledgement is idempotent; initialization cannot clear holds.
    assert update(root, [HOLD], empty) == held
    with pytest.raises(RetentionError, match="retention_holds_changed"):
        update(root)
    assert update(root, [], held) == empty


@pytest.mark.parametrize("marker", ["active.json", ".active.json.tmp"])
def test_cannot_change_register_during_incomplete_collection(root, marker):
    empty = update(root)
    original = (root / "holds.json").read_bytes()
    (root / marker).symlink_to(root / "absent")
    with pytest.raises(RetentionError, match="retention_holds_active_maintenance"):
        update(root, [HOLD], empty)
    assert (root / "holds.json").read_bytes() == original


@pytest.mark.parametrize("mutation", ["missing", "unsafe", "symlink", "hardlink", "corrupt"])
def test_unknown_or_unsafe_register_cannot_be_replaced(root, mutation):
    empty = update(root)
    path = root / "holds.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "unsafe":
        path.chmod(0o666)
    elif mutation == "symlink":
        path.rename(root / "target")
        path.symlink_to(root / "target")
    elif mutation == "hardlink":
        os.link(path, root / "alias")
    else:
        path.write_bytes(b"interrupted, not empty")
    with pytest.raises((RetentionError, OwnershipAuthorityError, ValueError)):
        update(root, [HOLD], empty)
    assert not (root / "holds.next.json").exists()


def test_lost_exclusion_before_publication_blocks_collection(root):
    empty = update(root)
    count = 0

    def require():
        nonlocal count
        count += 1
        if count == 2:
            raise RetentionError("retention_exclusion_lost")

    with pytest.raises(RetentionError, match="retention_exclusion_lost"):
        update(root, [HOLD], empty, require=require)
    with pytest.raises(RetentionError, match="retention_holds_recovery_required"):
        read_holds(root, root_owned=False)
    with pytest.raises(RetentionError, match="pending change differs"):
        update(root, [OTHER], empty)
    update(root, [HOLD], empty)
    assert read_holds(root, root_owned=False) == frozenset({HOLD})


@pytest.mark.skipif(sys.platform != "linux", reason="native administrative writer and SIGKILL")
@pytest.mark.parametrize("point", ["after_holds.next.json_temp_prefix",
    "after_holds.next.json_rename", "before_holds_replace", "after_holds_replace"])
def test_forced_interruption_recovers_same_change(root, point):
    empty = update(root)
    pid = os.fork()
    if pid == 0:
        def kill(stage):
            if stage == point:
                os.kill(os.getpid(), signal.SIGKILL)
        try:
            update(root, [HOLD], empty, crash=kill)
        finally:
            os._exit(17)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    if point != "after_holds_replace":
        with pytest.raises(RetentionError, match="retention_holds_recovery_required"):
            read_holds(root, root_owned=False)
    update(root, [HOLD], empty)
    assert read_holds(root, root_owned=False) == frozenset({HOLD})
    assert sorted(p.name for p in root.iterdir()) == ["holds.json"]


def test_hold_preserves_physical_reference_group_then_explicit_release_collects(context, tmp_path):
    root, owners, objects, public, excluded, factory, begin = context
    source, target = objects
    linked = (replace(source, references=(target.identity,)), target)
    version = _digest(sorted(read_holds(root, root_owned=False)))
    version = update(root, [source.identity.key.node_id], version)

    def candidates(path):
        return plan(linked, observed_owners=frozenset(owners), required_owners=frozenset(owners),
                    observed_roots=frozenset(RootKind), holds=read_holds(root, root_owned=False),
                    graph_path=path, run_id=RUN, observed_at=NOW)

    assert not candidates(tmp_path / "held.sqlite")
    assert all(owner.count() == 0 for owner in owners.values())
    update(root, [], version)
    released = candidates(tmp_path / "released.sqlite")
    assert {obj.identity for obj in released} == {obj.identity for obj in linked}
    begin(candidates=released)
    # A hold must not change after the original signed plan has been committed.
    with pytest.raises(RetentionError, match="retention_holds_active_maintenance"):
        update(root, [source.identity.key.node_id], _digest([]))
    factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert all(owner.count() == 1 for owner in owners.values())
