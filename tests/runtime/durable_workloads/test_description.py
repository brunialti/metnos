"""Closed display facts from the owner's current admitted LRE plan."""

import copy
import json

import pytest

from durable_runtime_registry import describe_plan
from durable_workloads.control import DurableWorkloadControl
from durable_workloads.image_indexing import EXECUTOR, PLAN_ID
from durable_workloads.storage import DurableWorkloadStore, WorkloadNotFoundError
from helpers import inventory, plan, source

OWNER = "owner-description"
GENERIC = {"kind": "generic", "operation": None, "target_path": None, "phase": None}
PATH = '/archive/<img src=x onerror="alert(1)">/photos'


@pytest.fixture
def store(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "description.sqlite3") as value:
        yield value


def admitted(store, *, owner=OWNER, index=True, phase="discover", number=1):
    draft = store.create_draft(owner, f"description-{number}", redacted_request={"private": "not display data"})
    candidate = plan(with_map=True)
    stage = candidate["stages"][1]
    if index:
        candidate["plan_id"] = PLAN_ID
        stage["key"] = phase
        stage["runner"]["name"] = EXECUTOR
        stage["input_bindings"]["base_path"] = {"ref": "literal", "value": PATH}
    revision = store.admit_revision(owner, draft.workload_id, candidate, inventory([source(0)]),
                                    expected_version=draft.version, usage_complete=True)
    return draft.workload_id, revision.revision_id, candidate


def read(store, wid, owner=OWNER):
    return store.descriptions_many(owner, [wid], projector=describe_plan)[wid]


def test_closed_description_in_page_and_detail_with_html_as_data(store):
    wid, _, _ = admitted(store)
    control = DurableWorkloadControl(store, cursor_secret="description", describe_plan=describe_plan)
    expected = {"kind": "image_indexing", "operation": EXECUTOR, "target_path": PATH, "phase": None}
    assert control.list_workloads(OWNER)["items"][0]["description"] == expected
    assert control.detail(OWNER, wid)["workload"]["description"] == expected
    assert "private" not in json.dumps(expected)
    assert "input_bindings" not in json.dumps(expected)


def test_owner_isolation_and_no_projector_on_foreign_job(store):
    wid, _, _ = admitted(store, owner="other-owner")
    calls = []
    with pytest.raises(WorkloadNotFoundError):
        store.descriptions_many(OWNER, [wid], projector=lambda *args: calls.append(args))
    assert calls == []
    assert store.descriptions_many(OWNER, [], projector=describe_plan) == {}
    with pytest.raises(ValueError):
        store.descriptions_many(OWNER, [wid] * 201, projector=describe_plan)


@pytest.mark.parametrize("broken", ['{"stages":[]}', 'not-json', 'null'])
def test_invalid_plan_has_no_description_or_path(store, broken):
    wid, revision, _ = admitted(store)
    # Deliberately corrupt this isolated fixture to test defensive reads.
    store._connection.execute("DROP TRIGGER revisions_admitted_immutable")
    store._connection.execute("PRAGMA ignore_check_constraints=ON")
    store._connection.execute("UPDATE revisions SET plan_json=? WHERE id=?", (broken, revision))
    assert read(store, wid) == GENERIC


def test_missing_unadmitted_and_old_revision_do_not_supply_path(store):
    draft = store.create_draft(OWNER, "no-plan", redacted_request={"base_path": PATH})
    assert read(store, draft.workload_id) == GENERIC
    wid, revision, _ = admitted(store)
    store._connection.execute("UPDATE workloads SET active_revision_id=NULL WHERE id=?", (wid,))
    assert read(store, wid) == GENERIC
    store._connection.execute("UPDATE workloads SET active_revision_id=? WHERE id=?", (revision, wid))
    store._connection.execute("DROP TRIGGER revisions_admitted_immutable")
    store._connection.execute("UPDATE revisions SET admitted_at=NULL WHERE id=?", (revision,))
    assert read(store, wid) == GENERIC


def test_generic_operation_never_guesses_title_or_path(store):
    wid, _, _ = admitted(store, index=False)
    assert read(store, wid) == {**GENERIC, "operation": "read_files_ocr"}


@pytest.mark.parametrize("phase", ["discover", "folders", "analyze", "merge", "publish"])
def test_phase_is_observed_not_inferred_from_workload_state(store, phase):
    wid, revision, _ = admitted(store, phase=phase)
    assert read(store, wid)["phase"] is None
    store._connection.execute("UPDATE units SET state='leased' WHERE revision_id=?", (revision,))
    assert read(store, wid)["phase"] == phase
    store._connection.execute("UPDATE units SET state='running' WHERE revision_id=?", (revision,))
    assert read(store, wid)["phase"] == phase
    store._connection.execute("UPDATE units SET state='committed' WHERE revision_id=?", (revision,))
    assert read(store, wid)["phase"] is None


@pytest.mark.parametrize("binding", [
    {"ref": "literal", "value": "relative/photos"},
    {"ref": "source.path"}, {"ref": "literal", "value": 123},
])
def test_only_literal_absolute_image_path_is_displayed(store, binding):
    _, _, candidate = admitted(store)
    candidate["stages"][1]["input_bindings"]["base_path"] = binding
    assert describe_plan(candidate, "discover")["target_path"] is None


def test_conflicting_image_paths_have_no_invented_target(store):
    _, _, candidate = admitted(store)
    second = copy.deepcopy(candidate["stages"][1])
    second["input_bindings"]["base_path"]["value"] = "/different/photos"
    candidate["stages"].append(second)
    assert describe_plan(candidate, "not-a-phase")["target_path"] is None
    assert describe_plan(candidate, "not-a-phase")["phase"] is None


def test_description_read_count_is_constant_per_page(store):
    for number in range(1, 5):
        admitted(store, number=number)
    statements = []
    store._connection.set_trace_callback(statements.append)
    try:
        page = DurableWorkloadControl(store, cursor_secret="description", describe_plan=describe_plan).list_workloads(OWNER)
    finally:
        store._connection.set_trace_callback(None)
    assert len(page["items"]) == 4
    assert len([sql for sql in statements if "WITH description_selected AS" in sql]) == 1
