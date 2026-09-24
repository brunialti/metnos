"""Derive the F5 qualification from evidence, or refuse and say why.

Nothing here reads a file, opens a store or signs anything. It takes one
authenticated historical reconciliation and one administrative evidence
frontier and answers a single question: does this installation meet the
approved entry threshold? A caller cannot improve the answer by supplying a
number, because no number is accepted — only the two observations are.

The threshold is the approved one and is not restated as a preference: at
least five genuine technical admissions, at least two authenticated producers,
two complete consecutive focused cycles, and no open defect in the scope the
census declared. Every refusal names itself, so a missing certificate always
has a reason an operator can act on.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence


EVIDENCE_SCOPE_DOMAIN_V1 = b"metnos.executor-birth.f5-evidence-scope/v1\0"
QUALIFICATION_DOMAIN_V1 = b"metnos.executor-birth.f5-qualification/v1\0"
MINIMUM_TECHNICAL_ADMISSIONS_V1 = 5
MINIMUM_AUTHENTICATED_PRODUCERS_V1 = 2
REQUIRED_CONSECUTIVE_CYCLES_V1 = 2


class QualificationRefused(RuntimeError):
    """A closed diagnostic: why this installation is not certifiable yet."""

    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class QualificationV1:
    """What the evidence proves, bound to the evidence that proved it."""

    qualification_id: str
    required_head_id: str
    evidence_head: str
    evidence_scope_id: str
    profile_id: str
    cycle_ids: tuple[str, ...]
    admission_receipts: tuple[str, ...]
    authenticated_producers: tuple[str, ...]

    @property
    def technical_admissions(self) -> int:
        return len(self.admission_receipts)


def evidence_scope_id_v1(issues: Sequence[object]) -> str:
    """Name exactly the gaps this history still has.

    The census records which gaps were declared and reviewed. Recomputing the
    same identity from the history observed now is what stops a certificate
    from being issued over evidence that grew a new hole since: an undeclared
    gap changes this digest, and the census no longer matches.
    """
    payload = json.dumps(sorted(
        [str(item.source), str(item.identity), str(item.code)] for item in issues
    ), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(EVIDENCE_SCOPE_DOMAIN_V1 + payload).hexdigest()


def derive_qualification_v1(reconciliation: object, frontier: object) -> QualificationV1:
    """Answer the threshold question from the two observations, or refuse."""
    required_head = getattr(reconciliation, "required_head_id", None)
    acts = getattr(reconciliation, "technical_acts", None)
    issuers = getattr(reconciliation, "technical_issuers", None)
    issues = getattr(reconciliation, "issues", None)
    if (not isinstance(required_head, str) or not required_head
            or acts is None or issuers is None or issues is None):
        raise QualificationRefused("qualification_input_invalid", "reconciliation")
    for field in ("census_scope", "open_findings", "profile",
                  "consecutive_successes", "pending_cycle", "head",
                  "profile_bindings"):
        if not hasattr(frontier, field):
            raise QualificationRefused("qualification_input_invalid", "frontier")

    if frontier.census_scope is None:
        raise QualificationRefused("census_absent")
    observed_scope = evidence_scope_id_v1(issues)
    if frontier.census_scope != observed_scope:
        # The declared scope and the history no longer describe the same set of
        # gaps. Certifying here would sign a review of something else.
        raise QualificationRefused("undisclosed_evidence_gap", observed_scope)
    if frontier.open_findings:
        raise QualificationRefused("open_defect", ",".join(frontier.open_findings[:8]))
    if frontier.pending_cycle is not None:
        raise QualificationRefused("cycle_interrupted")
    if frontier.profile is None:
        raise QualificationRefused("profile_absent")
    if frontier.profile_bindings is None:
        raise QualificationRefused("profile_bindings_absent")
    if frontier.profile_bindings.head_id != required_head:
        raise QualificationRefused("profile_head_mismatch")
    cycles = tuple(frontier.consecutive_successes)
    if len(cycles) < REQUIRED_CONSECUTIVE_CYCLES_V1:
        raise QualificationRefused("consecutive_cycles_insufficient", str(len(cycles)))
    if len(acts) < MINIMUM_TECHNICAL_ADMISSIONS_V1:
        raise QualificationRefused("technical_admissions_insufficient", str(len(acts)))
    producers = tuple(issuers)
    if len(producers) < MINIMUM_AUTHENTICATED_PRODUCERS_V1:
        raise QualificationRefused(
            "authenticated_producers_insufficient", str(len(producers)))

    receipts = tuple(sorted({act.encoded_hash for act in acts}))
    if len(receipts) != len(acts):
        # Deduplication happens in the reconciler; two acts sharing an encoded
        # receipt here would mean the same admission counted twice.
        raise QualificationRefused("duplicate_admission", str(len(acts) - len(receipts)))
    payload = json.dumps({
        "admission_receipts": list(receipts),
        "authenticated_producers": list(producers),
        "cycle_ids": list(cycles),
        "evidence_head": frontier.head,
        "evidence_scope_id": observed_scope,
        "profile_id": frontier.profile,
        "required_head_id": required_head,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return QualificationV1(
        "sha256:" + hashlib.sha256(QUALIFICATION_DOMAIN_V1 + payload).hexdigest(),
        required_head, str(frontier.head), observed_scope, str(frontier.profile),
        cycles, receipts, producers,
    )


__all__ = [
    "EVIDENCE_SCOPE_DOMAIN_V1", "MINIMUM_AUTHENTICATED_PRODUCERS_V1",
    "MINIMUM_TECHNICAL_ADMISSIONS_V1", "QUALIFICATION_DOMAIN_V1",
    "QualificationRefused", "QualificationV1", "REQUIRED_CONSECUTIVE_CYCLES_V1",
    "derive_qualification_v1", "evidence_scope_id_v1",
]
