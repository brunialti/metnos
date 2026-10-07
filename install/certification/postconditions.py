"""Independent comparisons of the native F5 collector's retained observations.

These are postconditions for the existing certification oracle, not successful
F5 cases. The scenario must separately prove that its action actually occurred,
retain the authenticated collector outputs, and measure HTTP/budget/effect
evidence. An unchanged snapshot alone does not prove a feedback submission,
review, restart, or generation change while an attempt was waiting.
"""
from __future__ import annotations

import hashlib
import json

from .oracle import CertificationError


def _require(condition, reason):
    if not condition:
        raise CertificationError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _identity(value):
    _require(isinstance(value, str) and bool(value), "native_identity_missing")
    return value


def _rows(rows, key):
    _require(isinstance(rows, list) and len(rows) <= 1024, "native_rows_invalid")
    indexed = {_identity(row[key]): row for row in rows}
    _require(len(indexed) == len(rows), "native_identity_duplicate")
    return indexed


def _subject(snapshot, contract):
    _require(isinstance(contract, str) and bool(contract), "contract_scope_missing")
    subject = snapshot["subjects"][contract]
    for key in ("name", "generation_id", "candidate_id", "admission_receipt_hash",
                "manifest_hash", "code_digest", "lifecycle", "approved_lifecycle"):
        _identity(subject[key])
    _require(isinstance(subject["payload_hashes"], dict) and subject["payload_hashes"],
             "admission_payloads_missing")
    for key, value in subject["payload_hashes"].items():
        _identity(key)
        _identity(value)
    _require(type(subject["loaded"]) is bool and type(subject["selectable"]) is bool,
             "native_visibility_missing")
    if subject["loaded"]:
        _require(subject["loaded_lifecycle"] == subject["lifecycle"],
                 "loaded_lifecycle_mismatch")
    _require(not subject["selectable"] or subject["loaded"], "selectable_without_loader")
    epochs = [row for row in snapshot["epochs"] if row["contract_id"] == contract]
    _rows(epochs, "generation_id")
    current = [row for row in epochs if row["state"] == "current"]
    _require(len(current) == 1 and current[0]["generation_id"] == subject["generation_id"]
             and current[0]["lifecycle"] == subject["lifecycle"], "current_epoch_mismatch")
    return subject, current[0]


def admission_unchanged(before, after, contract):
    """Exact authenticated admission/payload continuity, irrespective of calls."""
    old, _ = _subject(before, contract)
    new, _ = _subject(after, contract)
    _require(old == new, "authenticated_admission_changed")


def preexercise_nonselection(snapshot, contract):
    """Visibility observation; the attempted ordinary/durable actions are separate."""
    subject, epoch = _subject(snapshot, contract)
    _require(subject["approved_lifecycle"] == "preexercise" and not subject["selectable"],
             "preexercise_is_selectable")
    for key in ("total_calls", "successful_calls", "failed_calls"):
        _require(type(epoch[key]) is int and epoch[key] == 0, "preexercise_invoked")


def generation_replaced(before, after, contract):
    """Both observations must authenticate distinct admissions of one contract."""
    old, _ = _subject(before, contract)
    new, _ = _subject(after, contract)
    _require(old["name"] == new["name"], "contract_name_changed")
    for key in ("generation_id", "candidate_id", "admission_receipt_hash"):
        _require(old[key] != new[key], "generation_transition_missing")
    historical = [row for row in after["epochs"] if row["contract_id"] == contract
                  and row["generation_id"] == old["generation_id"]]
    _require(len(historical) == 1 and historical[0]["state"] != "current",
             "predecessor_epoch_not_retained")


def current_epoch_unchanged(before, after, contract):
    """Check all current counters, state and timestamps, not only call totals."""
    admission_unchanged(before, after, contract)
    _, old = _subject(before, contract)
    _, new = _subject(after, contract)
    _require(old == new, "current_epoch_mutated")
    generation = new["generation_id"]
    pending = lambda snapshot: _rows([
        row for row in snapshot["failure_reviews"] if row["generation_id"] == generation
    ], "job_id")
    _require(pending(before) == pending(after), "current_failure_reviews_mutated")


def dispatches(snapshot, contract, *, successful):
    """Require actual, distinct, exact-generation invocations in this observation."""
    subject, _ = _subject(snapshot, contract)
    _identity(snapshot["turn_id"])
    invocations = [row for row in snapshot["invocations"] if row["contract_id"] == contract]
    _require(bool(invocations), "subject_dispatch_missing")
    _rows(invocations, "receipt_id")
    for row in invocations:
        _require(all(row[key] == subject[key] for key in ("name", "candidate_id", "generation_id")),
                 "dispatch_generation_mismatch")
        _require(row["output"]["ok"] is successful, "dispatch_outcome_mismatch")
    return invocations


def independent_dispatches(before, after, contract):
    old = dispatches(before, contract, successful=True)
    new = dispatches(after, contract, successful=True)
    _require(before["turn_id"] != after["turn_id"], "turn_reused")
    _require(not {row["receipt_id"] for row in old} & {row["receipt_id"] for row in new},
             "dispatch_reused")


def quarantine_observed(failure, quarantined, repeated, contract):
    """Signed lifecycle, old failed dispatch, durable review and idempotent replay."""
    failed = dispatches(failure, contract, successful=False)
    generation_replaced(failure, quarantined, contract)
    subject, _ = _subject(quarantined, contract)
    _require(subject["lifecycle"] == "quarantined"
             and subject["approved_lifecycle"] == "quarantined"
             and not subject["selectable"], "signed_quarantine_missing")
    receipt_ids = {row["receipt_id"] for row in failed}
    pending = [row for row in quarantined["failure_reviews"]
               if row["execution_receipt_id"] in receipt_ids]
    _require(len(pending) == 1, "failure_review_not_unique")
    request = pending[0]
    old_subject, _ = _subject(failure, contract)
    _require(all(request[key] == old_subject[key] for key in ("generation_id", "candidate_id")),
             "failure_review_generation_mismatch")
    _identity(request["error_code"])
    current_epoch_unchanged(quarantined, repeated, contract)
    _require(_rows(quarantined["failure_reviews"], "job_id")
             == _rows(repeated["failure_reviews"], "job_id"), "failure_review_replay_changed")


def checkpoint_preserved(before, after):
    """Keep the same job, frozen revision, existing units, attempts and results.

    Later unit discovery is allowed; substituting a new job/revision is not.
    Terminal attempts/results must remain byte-identical. Running attempts may
    finish, but their identity, fence, worker and already recorded execution
    snapshot cannot be rewritten after a restart.
    """
    old, new = before["workload"], after["workload"]
    for key in ("owner_user_id", "id", "active_revision_id", "request_digest", "created_at"):
        _require(_identity(old[key]) == _identity(new[key]), "workload_identity_changed")
    _require(before["revision"] == after["revision"], "workload_revision_changed")
    _require(type(old["version"]) is int and type(new["version"]) is int
             and new["version"] >= old["version"], "workload_version_regressed")
    old_units, new_units = [_rows(item["units"], "id") for item in (before, after)]
    _require(old_units and old_units.keys() <= new_units.keys(), "checkpoint_units_lost")
    for unit_id, old_unit in old_units.items():
        new_unit = new_units[unit_id]
        for key in ("revision_id", "stage_id", "unit_key", "created_at"):
            _require(_identity(old_unit[key]) == _identity(new_unit[key]), "checkpoint_unit_replaced")
        for key in ("fence", "attempt_count"):
            _require(type(old_unit[key]) is int and type(new_unit[key]) is int
                     and new_unit[key] >= old_unit[key], "checkpoint_counter_regressed")
        if old_unit["state"] == "committed":
            _require(old_unit == new_unit, "committed_checkpoint_changed")
    old_attempts, new_attempts = [_rows(item["attempts"], "id") for item in (before, after)]
    _require(old_attempts.keys() <= new_attempts.keys(), "checkpoint_attempt_lost")
    for attempt_id, old_attempt in old_attempts.items():
        new_attempt = new_attempts[attempt_id]
        if old_attempt["ended_at"] is not None:
            _require(old_attempt == new_attempt, "terminal_attempt_changed")
        else:
            for key in ("unit_id", "number", "fence", "worker_id", "started_at"):
                _require(old_attempt[key] == new_attempt[key], "running_attempt_replaced")
            for key in ("invocation_id", "executor_snapshot_json", "model_snapshot_json"):
                if old_attempt[key] is not None:
                    _require(old_attempt[key] == new_attempt[key], "running_attempt_binding_changed")
    old_results, new_results = [_rows(item["results"], "id") for item in (before, after)]
    _require(old_results.keys() <= new_results.keys(), "checkpoint_result_lost")
    for result_id, result in old_results.items():
        _require(result == new_results[result_id], "committed_result_changed")


def restarted_attempt(before, after, unit_id):
    """One recorded running unit resumes under a new fenced worker/attempt.

    The scenario still needs its independently recorded service stop/start and
    external effect inventory: worker IDs alone do not prove a service restart.
    """
    checkpoint_preserved(before, after)
    old, new = [_rows(item["units"], "id")[unit_id] for item in (before, after)]
    _require(old["state"] == "running" and new["state"] == "committed",
             "unit_did_not_resume")
    previous = _rows(before["attempts"], "id")[old["active_attempt_id"]]
    interrupted = _rows(after["attempts"], "id")[previous["id"]]
    result = _rows(after["results"], "id")[new["committed_result_id"]]
    resumed = _rows(after["attempts"], "id")[result["attempt_id"]]
    _require(result["unit_id"] == resumed["unit_id"] == unit_id
             and result["revision_id"] == new["revision_id"]
             and result["fence"] == resumed["fence"] == new["fence"],
             "resumed_result_binding_mismatch")
    _require(previous["unit_id"] == unit_id and previous["state"] == "running"
             and previous["ended_at"] is None
             and interrupted["state"] == "timed_out"
             and interrupted["ended_at"] is not None,
             "interrupted_attempt_not_retained")
    # Lease loss before execution is abandoned; an executing attempt times out.
    # A generic failure or timeout is not proof of recovery from the lost lease.
    error = json.loads(interrupted["structured_error_json"])
    _require(isinstance(error, dict)
             and error.get("schema_version") == "metnos.durable-error/1"
             and error.get("error_class") == "lease_lost"
             and error.get("code") == "lease.expired_during_execution"
             and error.get("retry") == "automatic"
             and isinstance(error.get("details_redacted"), dict)
             and error["details_redacted"].get("execution_started") is True,
             "interrupted_lease_expiry_not_proven")
    _require(resumed["id"] not in _rows(before["attempts"], "id")
             and resumed["worker_id"] != previous["worker_id"]
             and resumed["number"] > previous["number"]
             and resumed["fence"] > previous["fence"]
             and resumed["state"] == "succeeded", "new_fenced_attempt_missing")
    _require(sum(row["unit_id"] == unit_id for row in after["results"]) == 1,
             "resumed_unit_result_duplicated")
    _require(after["workload"]["state"] == "completed", "resumed_workload_incomplete")


def birth_reused(before, after, result, contract):
    """An actual second Birth decision retains the exact admitted payload."""
    admission_unchanged(before, after, contract)
    subject, _ = _subject(before, contract)
    _identity(result["request_id"])
    publication, report = result["publication"], result["report"]
    _require(result["error_code"] is None and report["error_code"] is None
             and report["outcome"] in {"admitted", "preexercise"}
             and report["candidate_id"] == subject["candidate_id"]
             and publication["contract_id"] == contract
             and publication["current_generation_id"] == subject["generation_id"],
             "birth_reuse_not_observed")
    # PublicationResult.repeated is a convenience flag, not admission evidence.
    # The retained native result plus authenticated before/after identity is used.


def promotion_observed(before, after, contract):
    preexercise_nonselection(before, contract)
    generation_replaced(before, after, contract)
    subject, _ = _subject(after, contract)
    _require(subject["approved_lifecycle"] == subject["lifecycle"] == "active"
             and subject["selectable"], "promotion_not_selectable")
    dispatches(after, contract, successful=True)


def stale_feedback_observed(old, before, after, feedback, contract):
    receipts = dispatches(old, contract, successful=True)
    generation_replaced(old, before, contract)
    current_epoch_unchanged(before, after, contract)
    _require(feedback["status"] == "stale_feedback"
             and feedback["receipt_id"] in {row["receipt_id"] for row in receipts}
             and feedback["failure_job_id"] is None
             and feedback["quarantine_applied"] is False, "stale_feedback_not_observed")


def service_restarted(action):
    """Retained native service-control result, in addition to lease recovery."""
    _require(action["status"] == "passed" and action["restart_returncode"] == 0
             and action["kill_returncode"] == 0
             and 0 < action["started_at"] < action["finished_at"], "service_restart_failed")
    before, after = action["before"], action["after"]
    _require(before["ControlGroup"] == after["ControlGroup"]
             and after["ControlGroup"].endswith("/" + _identity(action["unit_name"]))
             and _identity(before["InvocationID"]) != _identity(after["InvocationID"])
             and int(before["MainPID"]) > 0 and int(after["MainPID"]) > 0
             and before["MainPID"] != after["MainPID"]
             and after["ActiveState"] == "active" and after["SubState"] == "running",
             "service_incarnation_unchanged")


def queued_generation_refused(before, after, waiting, finished, action, contract):
    """The selected old generation is refused after a recorded admission wait."""
    generation_replaced(before, after, contract)
    checkpoint_preserved(waiting, finished)
    _require(action["wait_site"] in {"resource", "scheduler"}
             and 0 < action["entered_at"] < action["transition_at"] < action["released_at"]
             and action["selected_generation"] == before["subjects"][contract]["generation_id"]
             and action["released_after_generation"] == after["subjects"][contract]["generation_id"],
             "generation_change_during_wait_missing")
    old = _rows(waiting["attempts"], "id")[action["attempt_id"]]
    new = _rows(finished["attempts"], "id")[action["attempt_id"]]
    _require(old["state"] == "running" and old["ended_at"] is None
             and new["ended_at"] is not None and old["invocation_id"] is None
             and new["invocation_id"] is None, "queued_attempt_not_refused")
    error = json.loads(new["structured_error_json"])
    _require(error["schema_version"] == "metnos.durable-error/1"
             and error["error_class"] == "capability_unavailable" and error["retry"] == "manual"
             and error["code"] in {"execution.runner_absent", "execution.dormant",
                                    "execution.retired", "execution.quarantined"}
             and not any(row["attempt_id"] == new["id"] for row in finished["results"]),
             "queued_generation_refusal_missing")


CHECKS = {
    check.__name__: check for check in (
        admission_unchanged, preexercise_nonselection, generation_replaced,
        current_epoch_unchanged, independent_dispatches, quarantine_observed,
        checkpoint_preserved, restarted_attempt,
        birth_reused, promotion_observed, stale_feedback_observed,
        service_restarted, queued_generation_refused,
    )
}


def probe(name, *observations):
    """Adapt a comparison to the existing oracle and bind its input bytes.

    Missing or malformed fields remain a failed probe; they never become an
    empty/equal default. The detail carries input digests rather than private
    user payloads. The original artifacts remain with the scenario collector.
    """
    _require(name in CHECKS, "unknown_f5_postcondition")
    try:
        digests = [hashlib.sha256(_canonical(value).encode()).hexdigest()
                   for value in observations]
        CHECKS[name](*observations)
    except (CertificationError, KeyError, TypeError, ValueError, IndexError) as error:
        return {"name": name, "passed": False,
                "detail": f"{type(error).__name__}: {error}"}
    return {"name": name, "passed": True,
            "detail": "native inputs sha256:" + ",".join(digests)}
