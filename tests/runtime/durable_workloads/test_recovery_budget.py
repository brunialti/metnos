"""A continuation spends only the remainder; missing reports are never zeroed."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from durable_workloads.recovery import remaining_recovery_budgets


def _facts():
    plan = {"budgets": {"max_units": 100, "max_tokens": 1000, "max_cost_micros": 0,
        "max_bytes_read": 10000, "max_bytes_written": 10000, "max_artifacts": 20,
        "max_wall_time_s": 1000, "max_attempts_per_unit": 3, "max_concurrency": 4}}
    usage = {"input_tokens": 10, "output_tokens": 5, "cost_micros": 0, "input_bytes": 20,
        "output_bytes": 10, "artifact_count": 1, "usage_unknown": 1,
        "started_at": "2026-09-17T10:00:00Z", "clock_high_water_at": "2026-09-17T10:00:01Z"}
    attempts = [{"attempt_id": "attempt-missing", "state": "failed", "usage": None,
                 "model": {"schema_version": "metnos.durable-model-snapshot/2", "mode": "llm",
                           "cost_policy": "zero", "max_calls": 2,
                           "max_input_tokens": 100, "max_output_tokens": 50}}]
    return plan, usage, attempts


def test_projection_reserves_full_missing_contract_without_mutating_history():
    facts = _facts()
    before = deepcopy(facts)
    limits, assessment = remaining_recovery_budgets(*facts, unit_count=12,
        now=datetime(2026, 9, 17, 10, 2, 0, tzinfo=timezone.utc))
    assert facts == before
    assert limits == {"max_units": 88, "max_tokens": 685, "max_cost_micros": 0,
        "max_bytes_read": 9980, "max_bytes_written": 9990, "max_artifacts": 19,
        "max_wall_time_s": 880, "max_attempts_per_unit": 1, "max_concurrency": 4}
    assert assessment["reserved_tokens"] == 300
    assert assessment["uncertain_attempts"] == [{"attempt_id": "attempt-missing", "reserved_tokens": 300}]


@pytest.mark.parametrize("defect", ["active", "paid", "unbounded", "unexplained", "budget", "clock"])
def test_projection_refuses_unbounded_or_changed_authority(defect):
    plan, usage, attempts = _facts()
    if defect == "active":
        attempts[0]["state"] = "running"
    elif defect == "paid":
        attempts[0]["model"]["cost_policy"] = "unbounded"
    elif defect == "unbounded":
        del attempts[0]["model"]["max_calls"]
    elif defect == "unexplained":
        attempts.clear()
    elif defect == "budget":
        plan["budgets"]["max_tokens"] = 100
    else:
        usage["clock_high_water_at"] = "2026-09-17T10:03:00Z"
    with pytest.raises(ValueError):
        remaining_recovery_budgets(plan, usage, attempts, unit_count=12,
            now=datetime(2026, 9, 17, 10, 2, 0, tzinfo=timezone.utc))
