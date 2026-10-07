"""Stopped Birth stores, retaining native parent/child records as one object.

The inventory assembler authenticates historical evidence and resolves external
references before planning. These owners bind every physical row to the CAS and
remove a bundle in one SQLite transaction; they grant no collection authority.
"""
from __future__ import annotations

import json
from pathlib import Path

from executor_birth_retention import RetentionError, RootKind
from install.birth_retention_sqlite import (
    _SQLiteOwner, _closed, _retained, _utc,
)


def _producer_state(row):
    if row["state"] in {"available", "in_progress"}:
        return _retained(row["registered_at"], RootKind.IN_PROGRESS_JOB)
    if row["state"] not in {"committed", "rejected"}:
        raise RetentionError("retention_owner_state_invalid", "producer state")
    if (not row["terminal_envelope"] or not row["terminal_auth"]
            or row["rejection_code"] == "legacy_terminal"):
        return _retained(row["registered_at"], RootKind.OPEN_AUDIT)
    return _closed(row["registered_at"], row["finalized_at"], row["expires_at"])


def _birth_approval_state(row):
    if row["status"] == "pending":
        return _retained(row["created_at"], RootKind.OPEN_APPROVAL)
    if row["status"] not in {"approved", "rejected", "expired"}:
        raise RetentionError("retention_owner_state_invalid", "approval state")
    if not all(row[name] for name in (
        "candidate_id", "semantic_core_id", "admission_context_id", "approval_scope",
    )) or (row["status"] != "expired" and not all(row[name] for name in (
        "decision_actor", "decision_key_id", "decision_signature",
    ))):
        return _retained(row["created_at"], RootKind.OPEN_AUDIT)
    return _closed(row["created_at"], row["decision_at"], row["expires_at"])


def _review_state(row):
    from executor_birth_failure_review import FailureReviewError, FailureReviewRequest, _canonical, _pairs

    try:
        raw = row["request_json"]
        if type(raw) is not bytes or len(raw) > 8 * 1024 * 1024:
            raise ValueError("request bytes")
        value = json.loads(raw, object_pairs_hook=_pairs)
        request = FailureReviewRequest(**value)
        if (raw != _canonical(value)
                or request.execution_receipt_id != row["execution_receipt_id"]):
            raise ValueError("request binding")
    except (TypeError, ValueError, FailureReviewError) as exc:
        raise RetentionError("retention_owner_state_invalid", "failure review") from exc
    # This queue contains only unresolved work. Its native owner removes a row
    # after processing, rather than recording a terminal state in this table.
    return _retained(row["created_at"], RootKind.IN_PROGRESS_JOB, RootKind.OPEN_FEEDBACK)


class _BirthBundleOwner(_SQLiteOwner):
    """The two native one-to-zero-or-one relations, never caller-selected SQL."""
    def __init__(self, kind, *, path: Path, require_exclusion, owner):
        import executor_birth_approval_store as approvals
        import executor_birth_producer_store as producers

        if kind == "producer_receipts":
            schema = producers._RECEIPT_SCHEMA + producers._ISSUANCE_SCHEMA
            table, key, node = "birth_producer_receipts", "receipt_id", "producer_receipt"
            self.child, self.child_key = "birth_producer_issuance", "request_id"
            state = _producer_state
        elif kind == "approvals":
            schema = approvals._SCHEMA
            table, key, node = "birth_approvals", "token", "approval"
            self.child, self.child_key = "birth_approval_consumptions", "token"
            state = _birth_approval_state
        else:
            raise RetentionError("retention_owner_invalid", "Birth store")
        super().__init__(name="birth_" + kind, path=path, schema=schema, table=table,
                         primary_key=key, node_type=node, state=state,
                         require_exclusion=require_exclusion, owner=owner)

    def _related(self, connection, values):
        children = tuple(dict(row) for row in connection.execute(
            f'SELECT * FROM "{self.child}" WHERE "{self.primary_key}"=? '
            f'ORDER BY "{self.child_key}" LIMIT 2', (values[self.primary_key],)))
        if len(children) > 1:
            raise RetentionError("retention_owner_invalid", "Birth relation cardinality")
        if self.table == "birth_producer_receipts":
            from executor_birth_receipts import _parse_producer
            from executor_birth_producer_store import producer_receipt_hash
            try:
                receipt, _ = _parse_producer(values["encoded"], temporal_now=None)
                fields = ("receipt_id", "issuer_id", "objective_hash", "candidate_source_id", "expires_at")
                if (any(values[name] != getattr(receipt, name) for name in fields)
                        or values["receipt_hash"] != producer_receipt_hash(values["encoded"])
                        or values["executor_origin"] != receipt.executor_origin.value
                        or values["revision_authorship"] != receipt.revision_authorship.value):
                    raise ValueError("producer binding")
                for child in children:
                    if any(child[name] != values[name] for name in (
                        "receipt_id", "issuer_id", "objective_hash", "candidate_source_id", "encoded",
                    )):
                        raise ValueError("issuance binding")
                    if child["contract_id"] == "__legacy_unknown_contract__":
                        # A migration gap cannot become a collectible closed object.
                        raise RetentionError("retention_inventory_incomplete", "legacy issuance contract")
            except (TypeError, ValueError) as exc:
                raise RetentionError("retention_owner_state_invalid", "producer binding") from exc
        else:
            from executor_birth_approval import ApprovalSubject, approval_subject_hash
            fields = ("candidate_id", "semantic_core_id", "admission_context_id",
                      "approval_scope", "expires_at")
            if all(values[name] for name in fields):
                try:
                    subject = ApprovalSubject(*(values[name] for name in fields))
                    if approval_subject_hash(subject) != values["subject_hash"]:
                        raise ValueError("approval subject")
                except (TypeError, ValueError) as exc:
                    raise RetentionError("retention_owner_state_invalid", "approval subject") from exc
            for child in children:
                if (values["status"] != "approved"
                        or _utc(child["consumed_at"]) < _utc(values["decision_at"])):
                    raise RetentionError("retention_owner_state_invalid", "approval consumption")
        return ((self.child, children),)

    def _delete_related(self, connection, row):
        (_table, children), = row.related
        removed = connection.execute(
            f'DELETE FROM "{self.child}" WHERE "{self.primary_key}"=?',
            (row.values[self.primary_key],))
        if removed.rowcount != len(children):
            raise RetentionError("retention_owner_changed", "Birth relation changed")


def _operational_birth_sqlite_owners(require_exclusion, account_owner):
    import config
    from executor_birth_feedback import _QUEUE_SCHEMA

    directory = config.PATH_USER_STATE / "birth"
    return (
        *(_BirthBundleOwner(kind, path=directory / (kind + ".sqlite"),
                            require_exclusion=require_exclusion, owner=account_owner)
          for kind in ("producer_receipts", "approvals")),
        _SQLiteOwner(name="birth_failure_reviews", path=directory / "failure_reviews.sqlite",
                     schema=_QUEUE_SCHEMA, table="executor_failure_review_queue",
                     primary_key="job_id", node_type="feedback", state=_review_state,
                     require_exclusion=require_exclusion, owner=account_owner),
    )
