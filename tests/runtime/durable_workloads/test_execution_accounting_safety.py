"""Distinguish proven pre-dispatch refusals from missing child usage receipts."""

import json
from types import SimpleNamespace

import pytest

from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import WorkerRunStatus
from helpers import source_resolution
from test_execution_bridge import (
    _Resolver, _admit, _contract, _map_observation, _pipeline, _schemas, _worker,
)


@pytest.mark.parametrize("failure", ["loader", "contract", "transport", "observation"])
def test_dispatch_boundary_keeps_accounting_honest(tmp_path, failure):
    resolver = _Resolver(reduce_model=False)
    resolver.map = _contract(
        "executor", "read_files_ocr", "metnos.test-map/1",
        inputs=("paths",), input_types=(("paths", "array"),),
        model=True, model_name="private-model", model_tier="vlm:default",
        model_kind="vision",
    )
    candidate = _pipeline(with_reduce=False)
    candidate["stages"][1]["resources"]["vlm"] = 1
    candidate["stages"][1]["invalidation_keys"].extend((
        "model_binding.digest", "prompt.digest",
    ))
    dispatched = []

    def load(_name):
        if failure == "loader":
            raise RuntimeError("private loader detail")
        return SimpleNamespace(name="changed" if failure == "contract" else "read_files_ocr")

    def invoke(*_args):
        dispatched.append(True)
        if failure == "transport":
            raise TimeoutError("private transport detail")
        if failure == "observation":
            return {"ok": False, "error_class": "capability_unavailable"}
        return _map_observation()

    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload_id, revision_id = _admit(store, resolver, candidate=candidate)
        bridge = DurableExecutionBridge(
            store, runners=resolver, output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item, "/authorized/image.png"),
            executor_loader=load, executor_invoker=invoke,
        )
        outcome = bridge.run_once(_worker(store, resolver))
        assert outcome.status is WorkerRunStatus.FAILED
        row = store._connection.execute(
            "SELECT metrics_json, structured_error_json FROM attempts WHERE owner_user_id='owner-f7'",
        ).fetchone()
        usage = json.loads(row["metrics_json"])["llm_usage"]
        error = json.loads(row["structured_error_json"])
        revision_usage = store._connection.execute(
            "SELECT usage_unknown FROM revision_usage WHERE revision_id=?", (revision_id,),
        ).fetchone()[0]
        summary = store.execution_summary("owner-f7", workload_id)
        assert summary["last_committed_at"] is None
        if failure in {"loader", "contract"}:
            assert dispatched == []
            assert usage["zero_calls_verified"] is True
            assert usage["usage_missing"] is False
            assert revision_usage == 0
            assert error["code"] == {
                "loader": "execution.executor_unavailable",
                "contract": "execution.loaded_executor_changed",
            }[failure]
        else:
            assert dispatched == [True]
            assert usage["zero_calls_verified"] is False
            assert usage["usage_missing"] is True
            assert revision_usage == 1
            assert error["code"] == "execution.usage_accounting_incomplete"
            assert error["message_key"] == "ERR_DURABLE_USAGE_ACCOUNTING_INCOMPLETE"
            assert error["details_redacted"]["cause_code"] == {
                "transport": "execution.timeout", "observation": "execution.runner_failed",
            }[failure]
            assert summary["blocking_reason"] == "budget_accounting_incomplete"
        assert "private" not in row["structured_error_json"]
        assert usage["input_tokens"] == usage["output_tokens"] == 0


def test_summary_progress_is_a_committed_result_not_a_control_update(tmp_path):
    resolver = _Resolver(reduce_model=False)
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        workload_id, _revision = _admit(store, resolver, candidate=_pipeline(with_reduce=False))
        assert store.execution_summary("owner-f7", workload_id)["last_committed_at"] is None
        bridge = DurableExecutionBridge(
            store, runners=resolver, output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item, "/authorized/image.png"),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=lambda *_args: _map_observation(),
        )
        assert bridge.run_once(_worker(store, resolver)).status is WorkerRunStatus.COMMITTED
        summary = store.execution_summary("owner-f7", workload_id)
        expected = store._connection.execute(
            "SELECT MAX(committed_at) FROM results",
        ).fetchone()[0]
        assert summary["last_committed_at"] == expected
        assert summary["blocking_reason"] is None
