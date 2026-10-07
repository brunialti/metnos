"""Native acceptance/consumer markers retain work and survive signed cleanup."""
from datetime import datetime, timezone
import json
import os
import signal
import sys
from types import SimpleNamespace

import pytest

import proposal_actions as actions
import telos_synth_consumer as consumer
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_telos_markers import _TelosMarkerOwner
from test_birth_retention_artifacts import RUN, collection


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX markers")
FUTURE = "2035-01-01T00:00:00Z"


@pytest.fixture
def native(tmp_path, monkeypatch):
    previous_umask = os.umask(0o022)
    root = tmp_path / "proposal_accepts"
    pending, processed = root / "synt_pending", root / "synt_processed"
    monkeypatch.setattr(consumer, "SYNT_PENDING_DIR", pending)
    monkeypatch.setattr(consumer, "SYNT_PROCESSED_DIR", processed)
    monkeypatch.setattr(consumer, "SYNT_AUDIT_LOG", tmp_path / "audit.jsonl")
    monkeypatch.setitem(sys.modules, "synth_request", SimpleNamespace(
        handle_synth_request=lambda *a, **k: pytest.fail("invalid marker must not start synthesis")))
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2035, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("install.birth_retention_telos_markers.datetime", Future)
    monkeypatch.setattr("test_birth_retention_artifacts.OBSERVED", FUTURE)
    owner = _TelosMarkerOwner(root=root, require_exclusion=lambda: None, owner=None)
    def write(sig="abc123", category="synt_pending", **changes):
        payload = dict(sig=sig, prop_id="1577836800.000000", ts=1577836800,
                       expected_name="compute_example", intent="")
        payload.update(changes)
        assert actions._write_marker(root / category, sig, payload)
        return root / category / f"{sig}.json"
    try:
        yield SimpleNamespace(root=root, owner=owner, write=write, pending=pending, processed=processed)
    finally:
        os.umask(previous_umask)


def selected(native, tmp_path, holds=frozenset()):
    owners = {native.owner.name: native.owner}
    return plan(native.owner.inventory(), observed_owners=frozenset(owners),
                required_owners=frozenset(owners), observed_roots=frozenset(RootKind),
                holds=holds, graph_path=tmp_path / f"graph-{len(tuple(tmp_path.glob('graph-*.sqlite')))}.sqlite", run_id=RUN, observed_at=FUTURE)


def test_native_invalid_input_is_collectible_without_starting_synthesis(native, tmp_path):
    native.write()
    assert selected(native, tmp_path) == ()
    result = consumer.run_once(max_jobs=10)
    assert result["processed"] == result["failed"] == 1
    assert consumer._count_today_processed() == 2
    objs = native.owner.inventory()
    assert len(objs) == 2 and all(obj.state is NodeState.CLOSED for obj in objs)
    owners = {native.owner.name: native.owner}
    chosen = selected(native, tmp_path)
    root, public, factory = collection(tmp_path, chosen, owners)
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["completed"] == 2
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert native.owner.inventory() == () and native.processed.is_dir()
    assert consumer.run_once(max_jobs=10)["processed"] == 0


def test_current_daily_cap_and_retention_window_preserved(native):
    native.write()
    consumer.run_once(max_jobs=10)
    obj = native.owner.inventory()[0]
    assert obj.eligible_after > datetime.now(timezone.utc).isoformat()
    # No clock shift in the planner: files produced today are never candidates.
    owners = frozenset({native.owner.name})
    assert plan(native.owner.inventory(), observed_owners=owners, required_owners=owners,
                observed_roots=frozenset(RootKind), holds=frozenset(),
                graph_path=native.root.parent / "today.sqlite", run_id=RUN,
                observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) == ()
    assert consumer._count_today_processed() == 2


@pytest.mark.parametrize("category", ["synt_pending", "change_pending", "pipeline_pending"])
def test_pending_requests_are_not_closed_by_age(native, category, tmp_path):
    native.write(category=category, prop_id="intr:abc123")
    obj, = native.owner.inventory()
    assert obj.state is NodeState.OPEN and not selected(native, tmp_path)
    with pytest.raises(RetentionError, match="retention_owner_state_invalid"):
        native.owner.delete(obj.identity, obj.version)


@pytest.mark.parametrize("status", ["success", "candidate", "noop", "failed"])
def test_processed_status_never_substitutes_native_birth_closure(native, tmp_path, status):
    src = native.write(intent="a real request")
    consumer._move_processed(str(src), "abc123", status, {"ok": status != "failed"})
    assert len(native.owner.inventory()) == 2 and not selected(native, tmp_path)


def test_inconsistent_invalid_status_stays_open(native, tmp_path):
    src = native.write(intent="a real request")
    consumer._move_processed(str(src), "abc123", "invalid", {"error": "missing expected_name or intent"})
    assert not selected(native, tmp_path)  # Closed sidecar is retained by its open marker.


def test_pending_duplicate_or_hold_preserves_physical_pair(native, tmp_path):
    native.write()
    consumer.run_once(max_jobs=10)
    obj = native.owner.inventory()[0]
    assert not selected(native, tmp_path, frozenset({obj.identity.key.node_id}))
    native.write(category="change_pending")
    assert not selected(native, tmp_path)


@pytest.mark.parametrize("alteration", ["unknown", "link", "hardlink", "mode", "bad_binding", "duplicate_key"])
def test_unknown_or_unsafe_marker_blocks_inventory(native, alteration):
    path = native.write()
    if alteration == "unknown":
        (native.root / "other").mkdir()
    elif alteration == "link":
        path.unlink()
        path.symlink_to("/dev/null")
    elif alteration == "hardlink":
        os.link(path, native.root.parent / "alias")
    elif alteration == "mode":
        path.chmod(0o666)
    elif alteration == "bad_binding":
        record = json.loads(path.read_text())
        record["sig"] = "different"
        path.write_text(json.dumps(record))
    else:
        path.write_text('{"sig":"abc123","sig":"abc123"}')
    with pytest.raises(RetentionError):
        native.owner.inventory()


def test_changed_file_rejected_and_absent_delete_idempotent(native):
    native.write()
    consumer.run_once(max_jobs=10)
    obj = native.owner.inventory()[0]
    path = native.root / obj.identity.local_id
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        native.owner.delete(obj.identity, obj.version)
    path.unlink()
    native.owner.delete(obj.identity, obj.version)
    assert native.owner.version(obj.identity) is None


def test_process_death_after_unlink_resumes_original_intent(native, tmp_path):
    native.write()
    consumer.run_once(max_jobs=10)
    chosen = selected(native, tmp_path)
    owners = {native.owner.name: native.owner}
    root, public, factory = collection(tmp_path, chosen, owners)
    before = (root / "active.json").read_bytes()
    pid = os.fork()
    if pid == 0:
        real = os.unlink
        def interrupted(path, *args, **kwargs):
            real(path, *args, **kwargs)
            os.kill(os.getpid(), signal.SIGKILL)
        os.unlink = interrupted
        factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)
        os._exit(9)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    assert (root / "active.json").read_bytes() == before
    assert factory().resume(owners, public_keys=public, max_objects=10, max_seconds=20)["remaining"] == 0
    factory().finish(owners, public_keys=public, verify_recovery=lambda: None)
    assert not list(native.processed.iterdir())


def test_native_acceptance_keeps_markers_and_all_physical_proposal_copies(native, tmp_path):
    from install.birth_retention_telos import _TelosDecisions, _TelosProposalOwner
    root = tmp_path / "telos"
    root.mkdir()
    paths = tuple(root / f"proposals-{n}.jsonl" for n in range(3))
    for path in paths:
        path.write_text(json.dumps({"ts": 1577836800, "executor_target": "compute_example"}) + "\n")
    decisions_path = root / "decisions.jsonl"
    decisions_path.write_text(json.dumps({"ts": 1577836801, "prop_id": "1577836800.000000",
                                          "action": "accept", "by": "admin"}) + "\n")
    decisions = _TelosDecisions(path=decisions_path, require_exclusion=lambda: None, owner=None)
    owner = _TelosProposalOwner(paths=paths, decisions=decisions, markers=native.owner)
    native.write()
    consumer.run_once(max_jobs=10)
    objects = owner.inventory()
    assert len(objects) == 6
    assert set(obj.identity for obj in objects) >= {ref for obj in objects for ref in obj.references}
    owners = frozenset(owner.owners)
    assert plan(objects, observed_owners=owners, required_owners=owners, observed_roots=frozenset(RootKind),
                holds=frozenset(), graph_path=tmp_path / "joined.sqlite", run_id=RUN, observed_at=FUTURE) == ()
    assert all(obj.references for obj in objects if obj.identity.owner != decisions.name)
