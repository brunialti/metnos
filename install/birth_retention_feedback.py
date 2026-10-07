"""Native feedback closure, preserving the runtime's actual learning inputs.

The last physical lines feed counters/LWW, while canonical rejections feed
unbounded change-intent discovery and observation. Neither is an expired log.
The shared JSONL owner compacts whole turn groups. The enclosing inventory
still resolves turn, contract and other external references before collection.
"""
from __future__ import annotations

from datetime import timedelta

from executor_birth_feedback import FeedbackError, failure_job_id
from executor_birth_retention import NodeState, RetentionError, RootKind
from install.birth_retention_jsonl import JournalEntry, _JsonlOwner, _time
from install.birth_retention_maintenance import OwnerObject
from install.birth_retention_sqlite import _iso
from turn_feedback import FEEDBACK_LOOKBACK, VALID_ACTIONS


def _completed_effect(effect):
    kind = effect.get("type")
    if kind == "fastpath_deleted":
        return type(effect.get("rows")) is int and effect["rows"] > 0
    if kind == "praxis_feedback":
        return effect.get("ok") is True
    if kind == "feedback_demote":
        return effect.get("action") in {
            "demoted", "skip_protected", "skip_handcrafted", "skip_already_deprecated",
        }
    if kind == "tutor_feedback":
        return effect.get("status") in {"applied", "already_applied", "not_applicable"}
    if kind in {"tutor_association_promoted", "tutor_negative_recorded"}:
        return effect.get("status") == "applied"
    return False


def _compensating_retry(record):
    """Native retry verdict: no corresponding TurnLog is written."""
    return (record["action"] == "ok"
            and record["by"] == "strato3_retry_success"
            and record["turn_id"].startswith("strato3-retry-")
            and bool(record.get("approved_pipeline"))
            and record.get("signature") == ",".join(record["approved_pipeline"]))


class _FeedbackOwner(_JsonlOwner):
    name = "turn_feedback"
    node_type = "feedback"

    def __init__(self, *, reviews, **kwargs):
        super().__init__(**kwargs)
        if reviews.name != "birth_failure_reviews":
            raise RetentionError("retention_owner_invalid", "feedback review owner")
        self.reviews = reviews

    def _record_key(self, record):
        if (type(record.get("turn_id")) is not str or not record["turn_id"]
                or record.get("action") not in VALID_ACTIONS
                or type(record.get("by")) is not str):
            raise ValueError("feedback identity")
        _time(record.get("ts"))
        for key in ("canonical", "user_query"):
            if record.get(key) is not None and type(record[key]) is not str:
                raise ValueError("feedback query")
        for key in ("approved_pipeline", "rejected_pipeline"):
            if key in record and (type(record[key]) is not list
                    or any(type(tool) is not str or not tool for tool in record[key])):
                raise ValueError("feedback pipeline")
        if "effects" in record and (type(record["effects"]) is not list
                or any(type(effect) is not dict for effect in record["effects"])):
            raise ValueError("feedback effects")
        if (record.get("tutor_feedback_status") is not None
                and type(record["tutor_feedback_status"]) is not str):
            raise ValueError("feedback status")
        for effect in record.get("effects", ()):
            for key in ("type", "status", "action", "receipt_id", "failure_job_id"):
                if effect.get(key) is not None and type(effect[key]) is not str:
                    raise ValueError("feedback effect field")
        return record["turn_id"]

    @staticmethod
    def _quarantine(effect, reviews, roots, references):
        try:
            expected = failure_job_id(effect.get("receipt_id"))
        except (FeedbackError, TypeError, ValueError):
            roots.add(RootKind.OPEN_AUDIT)
            return
        job, status = effect.get("failure_job_id"), effect.get("status")
        if type(effect.get("quarantine_applied")) is not bool:
            roots.add(RootKind.OPEN_AUDIT)
        if status == "stale_feedback":
            if job is not None or effect.get("quarantine_applied") is not False:
                roots.add(RootKind.OPEN_AUDIT)
            return
        if job != expected or status not in {"quarantined", "enqueue_failed"}:
            roots.add(RootKind.OPEN_AUDIT)
            return
        if status == "enqueue_failed":
            roots.add(RootKind.OPEN_FEEDBACK)
        row = reviews.get(job)
        if row is not None:
            if row.values["execution_receipt_id"] != effect["receipt_id"]:
                raise RetentionError("retention_inventory_incomplete", "feedback review binding")
            roots.add(RootKind.OPEN_FEEDBACK)
            references[row.identity] = None

    def _entries(self, custody, info, lines, records):
        # Match readlines()[-lookback:] exactly, including native blank lines.
        # Removing anything from this tail could bring an older error back
        # into the runtime window and change its next decision.
        recent = {key for key, _ in lines[-FEEDBACK_LOOKBACK:] if key is not None}
        reviews = {row.values["job_id"]: row for row in self.reviews.scan()}
        result = []
        for turn_id, (items, version) in self._groups(custody, info, lines, records).items():
            roots, references = set(), {}
            if turn_id in recent:
                roots.add(RootKind.OPEN_FEEDBACK)
            for record in items:
                if record["action"] == "error" and record.get("canonical"):
                    roots.add(RootKind.OPEN_REVISION)
                if record.get("warning") or record.get("tutor_feedback_status") not in {
                    None, "applied", "already_applied", "not_applicable",
                }:
                    roots.add(RootKind.OPEN_AUDIT)
                # The native successful retry appends a compensating verdict
                # without effects or a TurnLog; it is not an unfinished turn.
                reset = _compensating_retry(record)
                if "effects" not in record and not reset:
                    roots.add(RootKind.OPEN_AUDIT)
                for effect in record.get("effects", ()):
                    if effect.get("type") == "feedback_quarantine":
                        self._quarantine(effect, reviews, roots, references)
                    elif not _completed_effect(effect):
                        roots.add(RootKind.OPEN_AUDIT)
            times = tuple(_time(record["ts"]) for record in items)
            result.append(JournalEntry(OwnerObject(
                self.identity(turn_id), version, NodeState.OPEN if roots else NodeState.CLOSED,
                _iso(min(times)), None if roots else _iso(max(times) + timedelta(days=90)),
                references=tuple(references), roots=tuple(sorted(roots, key=lambda root: root.value)),
            ), items))
        return tuple(result)
