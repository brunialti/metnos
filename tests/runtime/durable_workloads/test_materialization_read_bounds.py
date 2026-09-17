"""Stage readiness must seek unfinished states, not rescan completed history."""

from __future__ import annotations

import pytest

from durable_workloads.models import UnitState
from durable_workloads.storage import DurableWorkloadStore
from helpers import inventory, map_stage, plan, source
from test_hierarchical_reduction import _admit_and_commit_map


def _parent_fixture(store, count):
    candidate = plan(with_map=True)
    candidate["inventory"]["max_sources"] = count
    candidate["budgets"]["max_units"] = 2 * count + 1
    candidate["stages"][1]["cardinality"]["max_units"] = count
    candidate["stages"].append({
        **map_stage(), "key": "second", "depends_on": ["map"],
        "cardinality": {"mode": "per_source", "max_units": count},
    })
    draft = store.create_draft("owner", "probe", redacted_request={})
    revision = store.admit_revision(
        "owner", draft.workload_id, candidate,
        inventory([source(i) for i in range(count)]), expected_version=draft.version,
    )
    store._connection.execute("UPDATE units SET state='skipped'")
    return revision


@pytest.mark.parametrize("state", list(UnitState))
def test_parent_readiness_preserves_every_closed_unit_state(state, monkeypatch):
    monkeypatch.setattr(
        DurableWorkloadStore, "_materialize_ready_units_batch",
        lambda *_args, **_kwargs: (0, 1),
    )
    with DurableWorkloadStore.open(":memory:") as store:
        revision = _parent_fixture(store, 1)
        store._connection.execute("UPDATE units SET state=?", (state.value,))
        ready = state in {
            UnitState.COMMITTED, UnitState.FAILED_PERMANENT,
            UnitState.CANCELLED, UnitState.SKIPPED,
        }
        assert (store._next_materialization_stage(
            store._connection, "owner", revision.revision_id,
        ) is not None) is ready
        assert store.materialize_all_ready_units() == int(ready)


@pytest.mark.parametrize("state", list(UnitState))
def test_reduction_waiting_preserves_every_closed_unit_state(state, monkeypatch):
    with DurableWorkloadStore.open(":memory:") as store:
        workload_id = _admit_and_commit_map(store, 2)
        assert store.materialize_ready_units("owner-a", workload_id) == 1
        revision_id = store.get_workload("owner-a", workload_id).active_revision_id
        store._connection.execute(
            "UPDATE units SET state=? WHERE reduction_level=0", (state.value,),
        )
        monkeypatch.setattr(
            DurableWorkloadStore, "_materialize_ready_units_batch",
            lambda *_args, **_kwargs: (0, 1),
        )
        ready = state in {
            UnitState.COMMITTED, UnitState.FAILED_PERMANENT,
            UnitState.NEEDS_ATTENTION, UnitState.CANCELLED, UnitState.SKIPPED,
        }
        assert (store._next_materialization_stage(
            store._connection, "owner-a", revision_id,
        ) is not None) is ready
        assert store.materialize_all_ready_units() == int(ready)


@pytest.mark.parametrize("operation", ["next_stage", "all_workloads"])
def test_readiness_query_cost_does_not_grow_with_terminal_parent_history(
    operation, monkeypatch,
):
    monkeypatch.setattr(
        DurableWorkloadStore, "_materialize_ready_units_batch",
        lambda *_args, **_kwargs: (0, 1),
    )
    costs = []
    for count in (100, 2000):
        with DurableWorkloadStore.open(":memory:") as store:
            revision = _parent_fixture(store, count)
            instructions = 0

            def measure():
                nonlocal instructions
                instructions += 100
                return 0

            store._connection.set_progress_handler(measure, 100)
            try:
                for _ in range(10):
                    if operation == "next_stage":
                        assert store._next_materialization_stage(
                            store._connection, "owner", revision.revision_id,
                        )["stage_key"] == "second"
                    else:
                        assert store.materialize_all_ready_units() == 1
            finally:
                store._connection.set_progress_handler(None, 0)
            costs.append(instructions)
    # A broad margin for planner bookkeeping, not a machine/time threshold.
    # The former terminal-history scan grew twentyfold for this fixture.
    assert costs[1] <= costs[0] * 2, costs
