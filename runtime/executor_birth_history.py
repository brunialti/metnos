"""Reconcile historical Birth evidence without certifying or activating F5.

The acquisition owners supply complete bounded inventories, public historical
declarations and exact durable read-back. This module joins their facts. Its
result remains inert: independent snapshots and a stable chain are not an
all-writer frontier, and neither a count nor a Python container grants F5.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Callable

from contract_store import (
    ContractStoreError, HistoricalBirthEvidenceV1, HistoricalBirthInventoryV1,
    read_historical_birth_evidence_v1,
)
from executor_birth_producer_store import ProducerHistoryV1
from executor_birth_receipts import AdmissionKind, AdmissionReceipt, ReceiptError, RevisionClass


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceIssueV1:
    """A retained evidence gap or contradiction, never an implicit exclusion."""

    source: str
    identity: str
    code: str


@dataclass(frozen=True, slots=True)
class HistoricalActV1:
    admission: AdmissionReceipt
    encoded_hash: str
    issuer_id: str
    namespace: str
    candidate_source_id: str
    classification: str
    continuity_receipt_hash: str | None = None


@dataclass(frozen=True, slots=True)
class HistoricalBirthReconciliationV1:
    """An observed inventory with all gaps; not a certification dossier."""

    required_head_id: str | None
    physical_receipts: int
    producer_rows: int
    issuance_rows: int
    acts: tuple[HistoricalActV1, ...]
    issues: tuple[HistoricalEvidenceIssueV1, ...]

    @property
    def technical_acts(self) -> tuple[HistoricalActV1, ...]:
        return tuple(act for act in self.acts if act.classification == "technical_candidate")

    @property
    def technical_issuers(self) -> tuple[str, ...]:
        return tuple(sorted({act.issuer_id for act in self.technical_acts}))


_TECHNICAL_REVISIONS = frozenset({
    RevisionClass.FIRST_BIRTH, RevisionClass.CODE_REVISION,
    RevisionClass.AUTHORITY_REVISION, RevisionClass.CONTRACT_REVISION,
})


def reconcile_historical_birth_v1(
    inventory: HistoricalBirthInventoryV1, producer_history: ProducerHistoryV1,
    declarations: tuple, *,
    read_evidence: Callable[..., HistoricalBirthEvidenceV1] = read_historical_birth_evidence_v1,
) -> HistoricalBirthReconciliationV1:
    """Join every enumerated item; unsigned selectors never establish success.

    Missing context policy, unjoined rows, ambiguous recovery and contradictory
    identities remain explicit issues. Callers must not turn the candidate
    count into a threshold proof while ignoring those issues or frontier work.
    """
    from cryptography.exceptions import InvalidSignature
    from executor_birth_operational import verify_historical_publication_v1
    from executor_birth_prepared_root import HistoricalProducerDeclarationsV1
    from executor_birth_reattestation import (
        BirthReattestationError, verify_historical_reattestation_v2,
    )
    from executor_birth_receipts import ApprovedLifecycle, _parse_admission

    if (type(inventory) is not HistoricalBirthInventoryV1
            or type(producer_history) is not ProducerHistoryV1
            or type(declarations) is not tuple
            or any(type(item) is not HistoricalProducerDeclarationsV1 for item in declarations)
            or not callable(read_evidence)):
        raise ValueError("birth_history_reconciliation_input_invalid")
    physical_count = sum(len(contract.receipts) for contract in inventory.contracts)
    if (physical_count > 100_000 or len(producer_history.receipts) > 100_000
            or len(producer_history.issuances) > 100_000 or len(declarations) > 4096):
        raise ValueError("birth_history_reconciliation_limit")
    issues = []

    def issue(source, identity, code):
        issues.append(HistoricalEvidenceIssueV1(source, str(identity), code))

    contexts = {}
    heads = {item.context.required_head_id for item in declarations}
    if len(heads) > 1:
        raise ValueError("birth_history_reconciliation_frontier_mismatch")
    for item in declarations:
        identifier = item.context.public_set.material.pin.admission_context_id
        if identifier in contexts:
            raise ValueError("birth_history_reconciliation_context_duplicate")
        contexts[identifier] = item

    def index(rows, field, source):
        result = {}
        for row in rows:
            value = getattr(row, field)
            result.setdefault(value, []).append(row)
        for identity, values in result.items():
            if len(values) != 1:
                issue(source, identity, "duplicate_stored_identity")
        return result

    producers = index(producer_history.receipts, "receipt_hash", "producer")
    issuances = index(producer_history.issuances, "receipt_id", "issuance")
    used_producers, used_issuances = set(), set()
    acts = []
    # Exact duplicates are observed once; conflicting identity overlaps are
    # removed from the candidate set after the complete census, not first-win.
    exact = set()
    for contract in inventory.contracts:
        covered_generations = set()
        for located in contract.receipts:
            identity = f"{contract.contract_id.value}|{located.generation_id}|{located.admission_context_id}"
            try:
                admission, _ = _parse_admission(located.encoded)
                selected = contexts.get(admission.admission_context_id)
                if selected is None:
                    issue("receipt", identity, "historical_context_policy_unavailable")
                    continue
                rows = producers.get(admission.producer_receipt_hash, ())
                if len(rows) != 1:
                    issue("receipt", identity, "producer_binding_missing_or_ambiguous")
                    continue
                row = rows[0]
                issued = issuances.get(row.receipt_id, ())
                if len(issued) != 1:
                    issue("receipt", identity, "issuance_binding_missing_or_ambiguous")
                    continue
                issuance = issued[0]
                durable = read_evidence(
                    contract.contract_id, located.generation_id,
                    admission_context_id=located.admission_context_id,
                )
                if (type(durable) is not HistoricalBirthEvidenceV1
                        or durable.contract_id != contract.contract_id
                        or durable.generation_id != located.generation_id
                        or durable.admission_context_id != located.admission_context_id
                        or durable.receipt_bytes != located.encoded
                        or durable.binding_bytes != contract.binding_bytes
                        or located.generation_id not in contract.generation_ids):
                    issue("receipt", identity, "durable_inventory_reread_mismatch")
                    continue
                inputs = dict(evidence=durable, receipt_row=row, issuance_row=issuance,
                              declarations=selected)
                continuity = None
                if admission.kind is AdmissionKind.REATTESTATION:
                    joined = verify_historical_reattestation_v2(**inputs)
                    continuity = joined.continuity_receipt_hash
                    classification = "reattestation"
                else:
                    joined = verify_historical_publication_v1(**inputs)
                    # A quarantine changes one manifest field, so the revision
                    # class calls it a contract revision. It is the removal of
                    # execution authority from an admission that already
                    # happened, never a new one, and counting it would let a
                    # withdrawal raise the admission threshold.
                    classification = (
                        "quarantine" if admission.approved_lifecycle is ApprovedLifecycle.QUARANTINED
                        else "preexercise" if admission.approved_lifecycle is ApprovedLifecycle.PREEXERCISE
                        else "technical_candidate" if admission.revision_class in _TECHNICAL_REVISIONS
                        else "nontechnical_revision"
                    )
                binding = joined.producer_binding
                used_producers.add(row.row_id)
                used_issuances.add(issuance.row_id)
                covered_generations.add(located.generation_id)
                encoded_hash = "sha256:" + hashlib.sha256(located.encoded).hexdigest()
                if encoded_hash not in exact:
                    exact.add(encoded_hash)
                    acts.append(HistoricalActV1(
                        binding.admission, encoded_hash, binding.producer.issuer_id,
                        binding.namespace, binding.producer.candidate_source_id,
                        classification, continuity,
                    ))
            except (ReceiptError, ContractStoreError, BirthReattestationError,
                    InvalidSignature, UnicodeError, ValueError, TypeError,
                    RecursionError, OverflowError) as exc:
                code = getattr(exc, "code", type(exc).__name__)
                detail = getattr(exc, "detail", "")
                # Owner error details are bounded diagnostic codes, not raw
                # receipt content. Do not expose arbitrary exception strings.
                if isinstance(detail, str) and detail.startswith("history_"):
                    code = f"{code}:{detail}"
                issue("receipt", identity, code)
        for generation in set(contract.generation_ids) - covered_generations:
            issue("generation", f"{contract.contract_id.value}|{generation}", "generation_not_reconciled")
        for retirement in contract.retirement_ids:
            issue("retirement", f"{contract.contract_id.value}|{retirement}", "retirement_not_reconciled")
    for empty in inventory.unbound_empty_namespaces:
        issue("namespace", empty.storage_key, "unbound_namespace_not_reconciled")
    for row in producer_history.receipts:
        if row.row_id not in used_producers:
            issue("producer", row.row_id, "producer_without_verified_durable_admission")
    for row in producer_history.issuances:
        if row.row_id not in used_issuances:
            issue("issuance", row.row_id, "issuance_without_verified_durable_admission")

    conflicts = set()
    for key_number, key in enumerate((
        lambda act: act.admission.receipt_id,
        lambda act: act.admission.birth_request_id,
        lambda act: (act.admission.contract_id, act.admission.generation_id),
    )):
        seen = {}
        for number, act in enumerate(acts):
            if key_number == 2 and act.admission.kind is AdmissionKind.REATTESTATION:
                # Context reattestations repeat generations by definition,
                # but their receipt/request identities must still be unique.
                continue
            previous = seen.setdefault(key(act), number)
            if previous != number:
                conflicts.update((previous, number))
    by_hash = {act.encoded_hash: act for act in acts}
    invalid_continuity = set()
    dependents = {}
    for number, act in enumerate(acts):
        if number in conflicts:
            issue("receipt", act.admission.receipt_id, "conflicting_admission_identity")
            acts[number] = replace(act, classification="conflicting_identity")
        if act.continuity_receipt_hash is not None:
            dependents.setdefault(act.continuity_receipt_hash, []).append(number)
            previous = by_hash.get(act.continuity_receipt_hash)
            selected = contexts[act.admission.admission_context_id]
            if (previous is None
                    or previous.admission.admission_context_id != selected.context.previous_admission_context_id
                    or previous.admission.contract_id != act.admission.contract_id
                    or previous.admission.generation_id != act.admission.generation_id
                    or previous.admission.semantic_core_id != act.admission.semantic_core_id
                    or previous.admission.approved_lifecycle != act.admission.approved_lifecycle
                    or (previous.admission.kind is AdmissionKind.REATTESTATION
                        and previous.candidate_source_id != act.candidate_source_id)):
                issue("receipt", act.admission.receipt_id, "continuity_predecessor_not_reconciled")
                acts[number] = replace(acts[number], classification="unreconciled_continuity")
                invalid_continuity.add(number)
    pending = list(conflicts | invalid_continuity)
    invalid = set(pending)
    while pending:
        parent = pending.pop()
        for child in dependents.get(acts[parent].encoded_hash, ()):
            if child not in invalid:
                invalid.add(child)
                pending.append(child)
                issue("receipt", acts[child].admission.receipt_id, "continuity_predecessor_not_reconciled")
                acts[child] = replace(acts[child], classification="unreconciled_continuity")
    return HistoricalBirthReconciliationV1(
        next(iter(heads)) if heads else None, physical_count,
        len(producer_history.receipts), len(producer_history.issuances),
        tuple(acts), tuple(issues),
    )
