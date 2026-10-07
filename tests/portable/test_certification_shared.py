"""Inert adversarial oracle fixtures. These never count as native F5 cycles."""
from copy import deepcopy
import json

import pytest

from install.certification import focused
from install.certification.oracle import CertificationError, matrix_digest
from install.certification.postconditions import probe
from certification_fixtures import frozen, inputs, CONTRACT, native, replacement, resumed


def make_cases(*, queued_subjects=None):
    manifest, cases = frozen.__wrapped__()
    manifest.update(platform="linux", cycle_subjects={
        "1": "user:inert_first/manifest.toml", "2": "user:inert_second/manifest.toml"},
        availability_subject="builtin:inert_clock/manifest.toml")
    if queued_subjects is not None:
        manifest["queued_subjects"] = queued_subjects
    for case in cases:
        case["postcondition_probes"] = sorted(focused.REQUIRED_PROBES[case["case_id"]])
    manifest["case_matrix_sha256"] = matrix_digest(cases)
    completed = []
    for cycle in (1, 2):
        observations = []
        for index, case in enumerate(cases):
            contract = (manifest["availability_subject"] if index == 8
                        else manifest.get("queued_subjects", manifest["cycle_subjects"])[str(cycle)]
                        if index == 6 else manifest["cycle_subjects"][str(cycle)])
            before = json.loads(json.dumps(native.__wrapped__()).replace(CONTRACT, contract))
            # Reuse the independently written old fixture; replacement expects
            # its original key, then we remap to this cycle's independent subject.
            after = json.loads(json.dumps(replacement(native.__wrapped__())).replace(CONTRACT, contract))
            after["turn_id"] = "inert-new-turn"
            after["invocations"][0].update(receipt_id="new-receipt", generation_id="new-generation",
                                          candidate_id="new-candidate")
            preexercise = deepcopy(before)
            preexercise["subjects"][contract].update(lifecycle="synthesized", loaded_lifecycle="synthesized",
                                                      approved_lifecycle="preexercise", selectable=False)
            preexercise["epochs"][0].update(lifecycle="synthesized", total_calls=0, successful_calls=0)
            preexercise["invocations"] = []
            waiting, finished = resumed.__wrapped__()
            waiting["workload"]["id"] = finished["workload"]["id"] = f"inert-job-{cycle}"
            start = 1000 * cycle + 10 * index
            restart = dict(status="passed", kill_returncode=0, restart_returncode=0,
                started_at=start + 1, finished_at=start + 2, unit_name="metnos-durable-worker.service",
                before=dict(ControlGroup="/system.slice/metnos-durable-worker.service",
                            InvocationID=f"old-{cycle}", MainPID="111"),
                after=dict(ControlGroup="/system.slice/metnos-durable-worker.service",
                           InvocationID=f"new-{cycle}", MainPID="222", ActiveState="active", SubState="running"))
            if index == 0:
                result = dict(request_id="inert-native-birth", error_code=None,
                    report=dict(outcome="preexercise", error_code=None, candidate_id="old-candidate"),
                    publication=dict(contract_id=contract, current_generation_id="old-generation"))
                evidence = dict(birth_reused=[preexercise, preexercise, result, contract])
            elif index == 1:
                evidence = dict(preexercise_nonselection=[preexercise, contract])
            elif index == 2:
                evidence = dict(promotion_observed=[preexercise, after, contract])
            elif index == 3:
                evidence = dict(generation_replaced=[before, after, contract],
                                independent_dispatches=[before, after, contract])
            elif index == 4:
                feedback = dict(status="stale_feedback", receipt_id="receipt-1",
                                failure_job_id=None, quarantine_applied=False)
                evidence = dict(stale_feedback_observed=[before, after, after, feedback, contract])
            elif index == 5:
                before["invocations"][0]["output"]["ok"] = False
                after["subjects"][contract].update(lifecycle="quarantined", loaded_lifecycle="quarantined",
                                                    approved_lifecycle="quarantined", selectable=False)
                after["epochs"][-1]["lifecycle"] = "quarantined"
                after["failure_reviews"] = [dict(job_id="review", generation_id="old-generation",
                    candidate_id="old-candidate", execution_receipt_id="receipt-1", error_code="actual_failure")]
                evidence = dict(quarantine_observed=[before, after, after, contract])
            elif index == 6:
                refused = deepcopy(waiting)
                refused["attempts"][0].update(state="failed", ended_at="finish",
                    structured_error_json=json.dumps(dict(schema_version="metnos.durable-error/1",
                        error_class="capability_unavailable", code="execution.quarantined", retry="manual")))
                action = dict(wait_site="scheduler", entered_at=start + 1, transition_at=start + 2,
                    released_at=start + 3, selected_generation="old-generation",
                    released_after_generation="new-generation", attempt_id="attempt-1")
                evidence = dict(queued_generation_refused=[before, after, waiting, refused, action, contract])
            elif index == 7:
                evidence = dict(restarted_attempt=[waiting, finished, "unit"], service_restarted=[restart])
            else:
                again = deepcopy(before)
                again.update(turn_id="other-turn")
                again["invocations"][0]["receipt_id"] = "other-receipt"
                evidence = dict(admission_unchanged=[before, again, contract],
                                independent_dispatches=[before, again, contract])
            data = inputs(case, f"INERT-{cycle}-{index}")
            data.update(native_evidence=evidence, probes=[probe(name, *evidence[name])
                        for name in case["postcondition_probes"]], started_at=start, finished_at=start + 4)
            data["captures"][0]["native"].update(ts_start=start + 2, ts_end=start + 3)
            observation, result = focused._evaluate(manifest, case, cycle, data, set())
            assert result["verdict"] == "pass", result
            observations.append(dict(manifest=manifest, case=case, cycle=cycle, inputs=data,
                                     observation=observation, result=result))
        completed.append(observations)
    return manifest, cases, completed


def test_shared_oracle_recomputes_both_complete_inert_cycles():
    manifest, cases, completed = make_cases()
    seen = set()
    for cycle in (1, 2):
        for case, value in zip(cases, completed[cycle - 1]):
            assert focused.audit_completed(manifest, case, cycle, value, seen)["verdict"] == "pass"
    assert len(seen) == 18


def test_frozen_queue_subjects_replay_with_independent_native_evidence():
    manifest, cases, completed = make_cases(queued_subjects={
        "1": "builtin:inert_long_first/manifest.toml", "2": "builtin:inert_long_second/manifest.toml"})
    focused._matrix(manifest, cases)
    seen = set()
    for cycle in (1, 2):
        for case, value in zip(cases, completed[cycle - 1]):
            assert focused.audit_completed(manifest, case, cycle, value, seen)["verdict"] == "pass"
    assert len(seen) == 18


@pytest.mark.parametrize("damage", ["wrong", "primary", "duplicate", "availability", "incomplete", "late"])
def test_queue_subject_mapping_cannot_be_chosen_from_successful_observations(damage):
    manifest, cases, completed = make_cases(queued_subjects={
        "1": "builtin:inert_long_first/manifest.toml", "2": "builtin:inert_long_second/manifest.toml"})
    case, value = cases[6], completed[0][6]
    if damage == "late":
        value = deepcopy(value)  # authentic completed artifact retains its frozen manifest
        manifest["queued_subjects"]["1"] = "builtin:replacement/manifest.toml"
    elif damage == "duplicate":
        manifest["queued_subjects"]["2"] = manifest["queued_subjects"]["1"]
    elif damage == "availability":
        manifest["queued_subjects"]["1"] = manifest["availability_subject"]
    elif damage == "incomplete":
        manifest["queued_subjects"].pop("2")
    else:
        manifest["queued_subjects"]["1"] = (manifest["cycle_subjects"]["1"]
                                               if damage == "primary" else "builtin:wrong/manifest.toml")
    with pytest.raises(CertificationError):
        focused._matrix(manifest, cases)
        focused.audit_completed(manifest, case, 1, value, set())


@pytest.mark.parametrize("damage", ["flag", "input", "result", "action", "subject", "turn", "interval"])
def test_shared_refuses_retained_forgery_and_missing_action(damage):
    manifest, cases, completed = make_cases()
    case, value = cases[7], completed[0][7]
    seen = set()
    if damage == "flag":
        value["inputs"]["probes"][0]["passed"] = False
    elif damage == "input":
        value["inputs"]["native_evidence"]["restarted_attempt"][1]["results"].clear()
    elif damage == "result":
        value["result"]["model_calls"] = 0
    elif damage == "action":
        value["inputs"]["native_evidence"].pop("service_restarted")
    elif damage == "subject":
        case, value = cases[2], completed[0][2]
        manifest["cycle_subjects"]["1"] = "unrelated"
    elif damage == "turn":
        seen.add(value["inputs"]["captures"][0]["native"]["turn_id"])
    else:
        value["inputs"]["started_at"] += 3
    with pytest.raises(CertificationError):
        focused.audit_completed(manifest, case, 1, value, seen)


@pytest.mark.parametrize("name,index,position,field,value", [
    ("birth_reused", 0, 2, "publication", None),
    ("stale_feedback_observed", 4, 3, "failure_job_id", "wrong-job"),
    ("queued_generation_refused", 6, 4, "transition_at", 0),
    ("service_restarted", 7, 0, "restart_returncode", 1),
])
def test_shared_action_failure_never_passes(name, index, position, field, value):
    _, _, completed = make_cases()
    arguments = completed[0][index]["inputs"]["native_evidence"][name]
    arguments[position][field] = value
    assert probe(name, *arguments)["passed"] is False


def test_shared_promotion_requires_no_preexercise_dispatch_and_one_active_dispatch():
    _, _, completed = make_cases()
    arguments = completed[0][2]["inputs"]["native_evidence"]["promotion_observed"]
    assert probe("promotion_observed", *arguments)["passed"]
    arguments[1]["invocations"].clear()
    assert not probe("promotion_observed", *arguments)["passed"]
