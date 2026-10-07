"""Frozen focused profile and full retained-turn interpretation; no actions."""
from __future__ import annotations
import math
from .oracle import CertificationError, validate_record, matrix_digest, evaluate_case
from .observations import build_observation, sum_model_calls, _terminal, _timestamp

def frozen_requests(case: dict, cycle: int) -> list[str]:
    """Resolve exact requests frozen for one independent subject/cycle.

    Generic CaseSpecs still have one initial request. Focused F5 scenarios
    freeze every turn for both subjects in the matrix, before registration;
    later captures cannot choose a different request or add a diagnostic turn.
    """
    validate_record("CaseSpec", case)
    sequences = case.get("cycle_requests")
    if (type(cycle) is not int or cycle < 1 or not isinstance(sequences, dict)
            or str(cycle) not in sequences):
        raise CertificationError("focused_cycle_requests_missing")
    if "1" not in sequences or sequences["1"][0] != case["request"]:
        raise CertificationError("focused_request_changed")
    return list(sequences[str(cycle)])


def focused_observation(case: dict, cycle: int, captures: list[dict], *,
                        probes: list[dict], effects: list[str], approval_count: int,
                        started_at: float, finished_at: float,
                        seen_turns: set[str]) -> dict:
    """Feed measured F5 turns and native postconditions to the existing oracle.

    The scenario measures the whole action interval (including waits/restart),
    records actual approval use/effects, and evaluates its native probes. None
    of these inputs is defaulted from the expected CaseSpec. This method neither
    executes an administrative action nor decides that a focused cycle passed.
    ``seen_turns`` is the observation registry across all cases and both cycles,
    separate from capture_turn's registry of acquired HTTP turns.
    """
    validate_record("CaseSpec", case)
    if type(cycle) is not int or cycle < 1 or not isinstance(captures, list) or not 1 <= len(captures) <= 64:
        raise CertificationError("invalid_focused_observation_scope")
    if (any(type(value) not in (float, int) or not math.isfinite(value)
            for value in (started_at, finished_at)) or not 0 < started_at <= finished_at):
        raise CertificationError("focused_interval_invalid")
    if type(approval_count) is not int or approval_count < 0:
        raise CertificationError("actual_approval_usage_missing")
    if (not isinstance(effects, list)
            or any(not isinstance(value, str) or not value for value in effects)
            or len(set(effects)) != len(effects)):
        raise CertificationError("actual_effect_observation_invalid")
    if not isinstance(probes, list):
        raise CertificationError("native_probes_missing")
    names = []
    for item in probes:
        validate_record("ProbeResult", item)
        names.append(item["name"])
    if len(set(names)) != len(names) or set(names) != set(case["postcondition_probes"]):
        raise CertificationError("native_probe_set_mismatch")
    requests = frozen_requests(case, cycle)
    ids, records, raw_responses = set(), [], []
    previous_end = started_at
    for captured in captures:
        raw, record = captured["http"], captured["native"]
        turn_id = record["turn_id"]
        if (not isinstance(turn_id, str) or not turn_id or turn_id != raw.get("turn_id")
                or turn_id in ids or turn_id in seen_turns):
            raise CertificationError("focused_turn_missing_or_reused")
        if (record["user_query"] != captured["request"]
                or captured["locale"] != case["locale"]):
            raise CertificationError("focused_native_request_mismatch")
        start, end = record["ts_start"], record["ts_end"]
        if (any(type(value) not in (float, int) or not math.isfinite(value) for value in (start, end))
                or not previous_end <= start <= end <= finished_at):
            raise CertificationError("focused_turn_interval_mismatch")
        if (sum_model_calls([raw]) is None or sum_model_calls([record]) is None
                or raw["model_calls"] < record["model_calls"]):
            raise CertificationError("actual_model_usage_missing")
        if (not isinstance(record.get("steps"), list)
                or any(not isinstance(step, dict) for step in record["steps"])):
            raise CertificationError("native_steps_missing")
        ids.add(turn_id)
        previous_end = end
        records.append(record)
        raw_responses.append(raw)
    if [capture["request"] for capture in captures] != requests:
        raise CertificationError("focused_request_changed")
    usage = sum_model_calls(raw_responses)
    if usage is None:
        raise CertificationError("actual_model_usage_missing")
    # The retained native steps, rather than the last HTTP summary alone, are
    # the complete observed plan. HTTP usage may include response rendering.
    raw = {**raw_responses[-1], "steps_summary": [], "model_calls": usage}
    observation = build_observation(
        case, cycle, raw,
        turn_records=records,
        probe_outcomes={item["name"]: (item["passed"], item["detail"]) for item in probes},
        effects=effects, approval_count=approval_count,
        terminal=_terminal({**records[-1], **raw_responses[-1]}),
    )
    # Do not let the generic collector infer no_action or shorten the interval
    # to the final turn: all administrative/wait time belongs to this case.
    observation.update(effects=effects, started_at=_timestamp(started_at),
                       finished_at=_timestamp(finished_at),
                       duration_ms=math.ceil((finished_at - started_at) * 1000))
    seen_turns.update(ids)
    return observation



CASE_IDS = (
    "f5-birth-reuse", "f5-preexercise-exclusion", "f5-promotion",
    "f5-generation-caches", "f5-stale-feedback", "f5-quarantine",
    "f5-queued-generation", "f5-restart-resume", "f5-unrelated-availability",
)

# Owner-closed minimum obligations. A submitted matrix may add checks, never
# substitute an easier probe for one of the nine operational requirements.
REQUIRED_PROBES = {
    "f5-birth-reuse": {"birth_reused"},
    "f5-preexercise-exclusion": {"preexercise_nonselection"},
    "f5-promotion": {"promotion_observed"},
    "f5-generation-caches": {"generation_replaced", "independent_dispatches"},
    "f5-stale-feedback": {"stale_feedback_observed"},
    "f5-quarantine": {"quarantine_observed"},
    "f5-queued-generation": {"queued_generation_refused"},
    "f5-restart-resume": {"restarted_attempt", "service_restarted"},
    "f5-unrelated-availability": {"admission_unchanged", "independent_dispatches"},
}


def audit_completed(manifest, case, cycle, completed, seen_turns):
    """Recompute retained comparisons and verdict; never trust a passed flag."""
    from .postconditions import CHECKS, probe

    if (set(completed) != {"manifest", "case", "cycle", "inputs", "observation", "result"}
            or completed["manifest"] != manifest or completed["case"] != case
            or type(completed["cycle"]) is not int or completed["cycle"] != cycle):
        raise CertificationError("focused_completed_binding")
    names = set(case["postcondition_probes"])
    if not REQUIRED_PROBES[case["case_id"]] <= names <= CHECKS.keys():
        raise CertificationError("focused_obligation_missing")
    inputs = completed["inputs"]
    if (type(inputs) is not dict or set(inputs) != {"captures", "probes", "native_evidence",
            "effects", "approval_count", "started_at", "finished_at"}
            or type(inputs["native_evidence"]) is not dict
            or set(inputs["native_evidence"]) != names):
        raise CertificationError("focused_completed_inputs")
    recomputed = []
    for name in case["postcondition_probes"]:
        observations = inputs["native_evidence"][name]
        if not isinstance(observations, list) or not observations:
            raise CertificationError("native_probe_inputs_missing")
        recomputed.append(probe(name, *observations))
    if inputs["probes"] != recomputed:
        raise CertificationError("focused_probe_changed")
    # Queue subjects must be genuinely eligible for durable execution. A pure
    # function used by the other cases need not pretend to be a long workload.
    # Both mappings are frozen with the profile; historical profiles keep their
    # original binding when no separate queue mapping was declared.
    subjects = (manifest.get("queued_subjects", manifest["cycle_subjects"])
                if case["case_id"] == "f5-queued-generation" else manifest["cycle_subjects"])
    subject = (manifest["availability_subject"] if case["case_id"] == "f5-unrelated-availability"
               else subjects[str(cycle)])
    for name in REQUIRED_PROBES[case["case_id"]] - {"restarted_attempt", "service_restarted"}:
        if inputs["native_evidence"][name][-1] != subject:
            raise CertificationError("focused_subject_changed")
    if "service_restarted" in names:
        action, = inputs["native_evidence"]["service_restarted"]
        if not inputs["started_at"] <= action["started_at"] < action["finished_at"] <= inputs["finished_at"]:
            raise CertificationError("focused_action_interval")
    observation, result = _evaluate(manifest, case, cycle, inputs, seen_turns)
    if completed["observation"] != observation or completed["result"] != result:
        raise CertificationError("focused_verdict_changed")
    if result["verdict"] != "pass":
        raise CertificationError("focused_obligation_failed")
    return result

def _matrix(manifest, cases):
    validate_record("CertificationManifest", manifest)
    if not isinstance(cases, list) or len(cases) != len(CASE_IDS):
        raise CertificationError("focused_case_coverage")
    for case in cases:
        validate_record("CaseSpec", case)
    if {case["case_id"] for case in cases} != set(CASE_IDS):
        raise CertificationError("focused_case_coverage")
    if manifest["cycles"] != [1, 2]:
        raise CertificationError("focused_two_cycles_required")
    if "queued_subjects" in manifest:
        queued = manifest["queued_subjects"]
        if (queued["1"] == queued["2"]
                or manifest.get("availability_subject") in queued.values()):
            raise CertificationError("focused_independent_queue_subjects_required")
    for case in cases:
        if set(case.get("cycle_requests", {})) != {str(cycle) for cycle in manifest["cycles"]}:
            raise CertificationError("focused_cycle_requests_missing")
        for cycle in manifest["cycles"]:
            frozen_requests(case, cycle)
    if {case["locale"] for case in cases} != set(manifest["locales"]):
        raise CertificationError("focused_locale_coverage")
    if matrix_digest(cases) != manifest["case_matrix_sha256"]:
        raise CertificationError("focused_matrix_changed")


def _evaluate(manifest, case, cycle, inputs, seen_turns):
    # The evidence is retained with the independent probe results. The scenario
    # owns authentic collection and the action-specific interpretation; these
    # checks do not turn an unchanged snapshot into proof of an action.
    evidence = inputs["native_evidence"]
    if (not isinstance(evidence, dict) or set(evidence) != set(case["postcondition_probes"])
            or any(not isinstance(value, list) or not value for value in evidence.values())):
        raise CertificationError("native_probe_inputs_missing")
    observation = focused_observation(
        case, cycle, inputs["captures"], probes=inputs["probes"],
        effects=inputs["effects"], approval_count=inputs["approval_count"],
        started_at=inputs["started_at"], finished_at=inputs["finished_at"],
        seen_turns=seen_turns,
    )
    return observation, evaluate_case(manifest, case, cycle, observation)

