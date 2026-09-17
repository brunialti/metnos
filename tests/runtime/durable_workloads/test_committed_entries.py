"""Historical results are explicit verified inputs, never rewritten executions."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.internal_runners import committed_entries
from durable_workloads.storage import DurableStoreError, DurableWorkloadStore
from durable_workloads.schema import SchemaValidationError
from durable_workloads.worker import WorkerRunStatus
from helpers import source_resolution
from test_execution_bridge import _Resolver, _admit, _map_observation, _pipeline, _schemas, _worker
from test_internal_runners import _context


def _committed(store):
    resolver = _Resolver(reduce_model=False)
    workload, revision = _admit(store, resolver, candidate=_pipeline(with_reduce=False))
    bridge = DurableExecutionBridge(
        store, runners=resolver, output_schemas=_schemas(),
        source_resolver=lambda item, _context: source_resolution(item, "/authorized/input"),
        executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
        executor_invoker=lambda *_args: _map_observation(),
    )
    assert bridge.run_once(_worker(store, resolver)).status is WorkerRunStatus.COMMITTED
    row = store._connection.execute("SELECT id, digest, schema_version FROM results").fetchone()
    return workload, revision, {"result_id": row["id"], "digest": row["digest"], "schema_version": row["schema_version"]}


def test_reading_verified_history_preserves_entries_and_all_rows(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload, revision, ref = _committed(store)
        before = list(store._connection.iterdump())
        context = replace(_context(workload, revision), owner_user_id="owner-f7")
        reader = committed_entries(store)
        expected = {"entries": _map_observation()["entries"]}
        assert reader({"references": [ref]}, context) == expected
        assert reader({"references": [ref]}, context) == expected
        assert list(store._connection.iterdump()) == before


@pytest.mark.parametrize("defect", ["owner", "digest", "schema", "duplicate", "missing", "extra", "uncommitted"])
def test_changed_or_unowned_history_cannot_be_imported(tmp_path, defect):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _workload, _revision, ref = _committed(store)
        owner = "owner-f7"
        refs = [dict(ref)]
        if defect == "owner":
            owner = "another-owner"
        elif defect == "digest":
            refs[0]["digest"] = "sha256:" + "0" * 64
        elif defect == "schema":
            refs[0]["schema_version"] = "metnos.another/1"
        elif defect == "duplicate":
            refs.append(ref)
        elif defect == "missing":
            refs[0]["result_id"] = "res_missing"
        elif defect == "extra":
            refs[0]["path"] = "/private"
        else:
            store._connection.execute("UPDATE units SET state='pending' WHERE committed_result_id IS NOT NULL")
        with pytest.raises((DurableStoreError, SchemaValidationError)):
            store.read_committed_entries(owner, refs)


def test_payload_digest_is_rechecked_even_if_storage_returns_corrupt_bytes(tmp_path, monkeypatch):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _workload, _revision, ref = _committed(store)
        corrupt = {**ref, "payload_json": json.dumps({"entries": []})}
        with monkeypatch.context() as patch:
            patch.setattr(store, "_connection", SimpleNamespace(execute=lambda *_args:
                SimpleNamespace(fetchone=lambda: corrupt)))
            with pytest.raises(SchemaValidationError):
                store.read_committed_entries("owner-f7", [ref])


def test_reference_count_is_bounded_before_reading_database(tmp_path):
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        for refs in (None, [], [{}] * 1025):
            with pytest.raises(DurableStoreError):
                store.read_committed_entries("owner-f7", refs)
