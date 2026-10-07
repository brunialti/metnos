"""Joined native LRE inventory; no claim of installation-wide F6 coverage."""
from dataclasses import replace
import os

import pytest

from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_maintenance import plan
from install.birth_retention_workloads import _WorkloadInventory
from test_birth_retention_artifacts import OBSERVED, PAYLOAD, RUN, collection, native
from test_birth_retention_artifact_auxiliary import orphan_copy, published, staging


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX artifact owners")


def inventory(native):
    return _WorkloadInventory(workloads=native.jobs, artifact_root=native.root)


def project(joined, objects, path, *, holds=frozenset()):
    # This fixture's closed universe has only LRE metadata/files. It does not
    # attest observations of any other installed owner or retention root.
    return plan(objects, observed_owners=frozenset(joined.owners),
                required_owners=frozenset(joined.owners), observed_roots=frozenset(RootKind),
                holds=holds, graph_path=path, run_id=RUN, observed_at=OBSERVED)


def test_join_preserves_all_blob_publication_and_paused_workspace_edges(native):
    pub_owner, pub_path, pub_id = published(native)
    temp_owner, temp_path, temp_id, _workspace = staging(native, user="owner-b", state="paused")
    joined = inventory(native)
    objects = joined.inventory()
    assert len({obj.identity for obj in objects}) == len(objects)
    jobs = {row.values["owner_user_id"]: row.identity for row in native.jobs.scan()}
    by_id = {obj.identity: obj for obj in objects}
    assert {ref.owner for ref in by_id[jobs["owner-a"]].references} == {
        native.blobs.name, pub_owner.name}
    assert pub_id in by_id[jobs["owner-a"]].references
    assert temp_id in by_id[jobs["owner-b"]].references
    assert by_id[temp_id].roots == (RootKind.IN_PROGRESS_JOB,)
    assert set(joined.owners) == {native.jobs.name, native.blobs.name, pub_owner.name, temp_owner.name}
    assert not project(joined, objects, native.root.parent / "protected.sqlite")
    assert pub_path.read_bytes() == temp_path.read_bytes() == PAYLOAD


def test_two_native_inventories_collect_job_then_blob_preserving_active_user_and_hold(native, tmp_path):
    closed = native.create("owner-a")
    active = native.create("owner-b", closed=False)
    _pub_owner, orphan_path, orphan_id = orphan_copy(native, user="owner-c")
    _temp_owner, stage_path, stage_id, _workspace = staging(native, user="owner-d", state="admitted")
    native.finish("owner-d", "same-local-id", clean=False)
    joined = inventory(native)
    objects = joined.inventory()
    jobs = {row.values["owner_user_id"]: row.identity for row in native.jobs.scan()}
    held = project(joined, objects, tmp_path / "held.sqlite",
                   holds=frozenset({jobs["owner-a"].key.node_id}))
    assert jobs["owner-a"] not in {obj.identity for obj in held}
    first = project(joined, objects, tmp_path / "first.sqlite")
    assert {obj.identity for obj in first} == {jobs["owner-a"], orphan_id, stage_id}
    allocated = sum(path.stat().st_blocks * 512 for path in (closed.path, orphan_path, stage_path))
    assert allocated > 0
    for number in (1, 2):
        candidates = first if number == 1 else project(joined, joined.inventory(), tmp_path / "second.sqlite")
        if number == 2:
            assert len(candidates) == 2
            assert {obj.identity.owner for obj in candidates} == {native.blobs.name, native.jobs.name}
            assert jobs["owner-d"] in {obj.identity for obj in candidates}
        run_root = tmp_path / str(number)
        run_root.mkdir()
        root, public, factory = collection(run_root, candidates, joined.owners)
        result = factory().resume(joined.owners, public_keys=public, max_objects=100, max_seconds=20)
        assert result["completed"] == len(candidates) and result["remaining"] == 0
        factory().finish(joined.owners, public_keys=public, verify_recovery=lambda: None)
        assert not (root / "active.json").exists()
        assert active.path.read_bytes() == PAYLOAD
        if number == 1:
            assert closed.path.read_bytes() == PAYLOAD
            assert native.jobs.version(jobs["owner-d"]) is not None
            # The native observer can now certify actual scratch absence,
            # enabling job collection on the subsequent signed inventory.
            native.cleanup("owner-d", "same-local-id")
    assert all(not path.exists() for path in (closed.path, orphan_path, stage_path))
    assert {row.values["owner_user_id"] for row in native.jobs.scan()} == {"owner-b"}
    assert not project(joined, joined.inventory(), tmp_path / "final.sqlite")


def test_native_job_change_between_file_projections_blocks_collection(native, monkeypatch):
    item = native.create("owner-a", closed=False)
    joined = inventory(native)
    first = joined.file_owners[0]
    original = first.inventory
    def changing():
        result = original()
        native.finish("owner-a", item.draft.workload_id)
        return result
    monkeypatch.setattr(first, "inventory", changing)
    with pytest.raises(RetentionError, match="retention_owner_changed"):
        joined.inventory()
    assert item.path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("damage", ["missing_job", "duplicate", "missing_reference", "foreign_owner"])
def test_incomplete_or_ambiguous_file_projection_blocks_before_plan(native, monkeypatch, damage):
    item = native.create("owner-a")
    joined = inventory(native)
    first = joined.file_owners[0]
    observed = first.inventory()
    job, = (obj for obj in observed if obj.identity.owner == native.jobs.name)
    blob, = (obj for obj in observed if obj.identity.owner == first.name)
    damaged = {
        "missing_job": (blob,),
        "duplicate": (job, blob, blob),
        "missing_reference": (job,),
        "foreign_owner": (job, replace(blob, identity=replace(blob.identity, owner="unobserved"))),
    }[damage]
    monkeypatch.setattr(first, "inventory", lambda: damaged)
    with pytest.raises(RetentionError, match="retention_inventory_incomplete"):
        joined.inventory()
    assert item.path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("present", [True, False])
def test_empty_or_absent_artifact_store_and_empty_native_database_are_observed(native, present):
    root = native.root if present else native.root.parent / "absent-artifacts"
    assert root.exists() is present
    joined = _WorkloadInventory(workloads=native.jobs, artifact_root=root)
    assert joined.inventory() == ()
    assert len(joined.owners) == 4
