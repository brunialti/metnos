"""Inert native shapes retained from the independent transport/postcondition fixtures.
No live data or activation evidence is constructed here.
"""
from copy import deepcopy
import json
import pytest
from install.certification.focused import CASE_IDS
from install.certification.oracle import matrix_digest

CONTRACT = "user:compute_values/manifest.toml"


@pytest.fixture
def frozen():
    cases = [dict(
        schema_version="metnos.certification-case/1", case_id=case_id,
        logical_flow_id="inert-transport-fixture", locale="it", request="È una prova inerte 🙂",
        cycle_requests={"1": ["È una prova inerte 🙂"], "2": ["È una prova inerte 🙂"]},
        fixture="inert-transport-only", expected_route="engine",
        allowed_plans=[["fixture_tool", "final_answer"]], forbidden_tools=[],
        expected_placement="server", required_approval=False, expected_terminal="completed",
        required_effects=[], forbidden_effects=[], postcondition_probes=["fixture_probe"],
        response_requirements=[], budgets=dict(max_approvals=0, deadline_s=10, max_model_calls=1),
    ) for case_id in CASE_IDS]
    manifest = dict(
        schema_version="metnos.certification-manifest/1", certification_id="INERT-TRANSPORT-ONLY",
        oracle_version="fixture", git_commit="a" * 40,
        **dict.fromkeys(("suite_sha256", "corpus_sha256", "catalog_sha256", "llm_config_sha256",
                         "surface_sha256"), "b" * 64),
        case_matrix_sha256=matrix_digest(cases), platform="inert-fixture", fixture="inert-fixture",
        locales=["it"], cycles=[1, 2], created_at="2026-10-03T12:00:00Z",
    )
    return manifest, cases


def inputs(case, turn, *, passed=True):
    return dict(
        captures=[dict(request=case["request"], locale="it", deadline_s=10,
                       http=dict(turn_id=turn, model_calls=1, final_kind="answer", final_message="🙂"),
                       native=dict(turn_id=turn, user_query=case["request"], ts_start=102., ts_end=103.,
                                   model_calls=1, steps=[dict(chosen_tool="fixture_tool")]))],
        probes=[dict(name="fixture_probe", passed=passed, detail="inert transport fixture")],
        native_evidence={"fixture_probe": [dict(fixture_only=True, independently_observed=passed)]},
        effects=[], approval_count=0, started_at=100., finished_at=104.,
    )


@pytest.fixture
def native():
    subject = dict(name="compute_values", generation_id="old-generation",
                   candidate_id="old-candidate", admission_receipt_hash="old-admission",
                   lifecycle="active", approved_lifecycle="active", loaded_lifecycle="active",
                   manifest_hash="manifest", code_digest="code", payload_hashes={"manifest": "payload"},
                   loaded=True, selectable=True)
    epoch = dict(contract_id=CONTRACT, generation_id="old-generation", state="current",
                 lifecycle="active", total_calls=1, successful_calls=1, failed_calls=0,
                 negative_feedback=0, positive_feedback=0, state_version=1, last_used_at="first")
    invocation = dict(contract_id=CONTRACT, name="compute_values", generation_id="old-generation",
                      candidate_id="old-candidate", receipt_id="receipt-1", output={"ok": True})
    return dict(subjects={CONTRACT: subject}, epochs=[epoch], failure_reviews=[],
                turn_id="turn-1", invocations=[invocation])


def replacement(native):
    new = deepcopy(native)
    new["subjects"][CONTRACT].update(generation_id="new-generation", candidate_id="new-candidate",
                                      admission_receipt_hash="new-admission")
    new["epochs"][0]["state"] = "historical"
    new["epochs"].append({**native["epochs"][0], "generation_id": "new-generation"})
    return new


@pytest.fixture
def resumed():
    before = dict(workload=dict(owner_user_id="owner", id="same-job", active_revision_id="same-revision",
                     request_digest="request", created_at="start", state="running", version=3),
                  revision=dict(id="same-revision", plan_digest="plan", plan_json='{"stages":[]}'),
                  units=[dict(id="unit", revision_id="same-revision", stage_id="stage", unit_key="key",
                              created_at="start", state="running", fence=1, attempt_count=1,
                              active_attempt_id="attempt-1", committed_result_id=None)],
                  attempts=[dict(id="attempt-1", unit_id="unit", number=1, fence=1, worker_id="worker-1",
                                 started_at="start", ended_at=None, state="running", invocation_id=None,
                                 executor_snapshot_json=None, model_snapshot_json=None)], results=[])
    after = deepcopy(before)
    after["workload"].update(state="completed", version=5)
    after["units"][0].update(state="committed", active_attempt_id=None, fence=2, attempt_count=2,
                             committed_result_id="result")
    after["attempts"][0].update(state="timed_out", ended_at="lease-expiry",
        structured_error_json=json.dumps(dict(schema_version="metnos.durable-error/1",
            error_class="lease_lost", code="lease.expired_during_execution", retry="automatic",
            details_redacted=dict(execution_started=True))))
    after["attempts"].append(dict(after["attempts"][0], id="attempt-2", number=2, fence=2,
                                   worker_id="worker-2", state="succeeded", ended_at="finish",
                                   structured_error_json=None))
    after["results"] = [dict(id="result", attempt_id="attempt-2", unit_id="unit",
                             revision_id="same-revision", fence=2)]
    return before, after

