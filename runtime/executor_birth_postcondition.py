"""Read-only verifier for an Executor Birth publication postcondition.

This boundary neither repairs nor publishes.  It authenticates the selected
RM-0007 revision and the durable AdmissionReceipt, then treats a surviving
authoring journal only as additional crash-recovery evidence.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from contract_store import (
    ContractRetirement, PublicationResult, TrustedPublic, VerifiedManifest,
    _birth_receipt_path, _existing_contract_directory, current_contract,
)
from executor_birth_authoring import authoring_paths, load_prepared_journal
from executor_birth_receipts import (
    AdmittedCheckStatus, verify_admission_receipt,
)

if TYPE_CHECKING:
    from executor_birth_operational import BirthRequest


class BirthPostconditionError(ValueError):
    """Stable fail-closed error from observational terminal verification."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def verify_birth_postcondition(
    request: "BirthRequest",
    expected: PublicationResult | None,
    admission_receipt: bytes | None,
    *,
    trusted_publics: Iterable[TrustedPublic],
    admission_verifier_keys: Mapping[str, Ed25519PublicKey],
    store_root: Path | str | None = None,
) -> tuple[PublicationResult, bytes]:
    """Authenticate one already-published Birth result without mutation.

    ``expected`` and ``admission_receipt`` are optional replay hints.  When
    supplied they must match the independently read durable state exactly.
    """
    # Structural validation avoids a dependency cycle when the sealed
    # operational core selects this verifier during its own assembly.
    if not all(hasattr(request, field) for field in (
        "request_id", "manifest_ref",
    )):
        raise BirthPostconditionError("birth_postcondition_invalid", "request")
    if expected is not None and not isinstance(expected, PublicationResult):
        raise BirthPostconditionError("birth_postcondition_invalid", "expected")
    if admission_receipt is not None and not isinstance(admission_receipt, bytes):
        raise BirthPostconditionError("birth_postcondition_invalid", "receipt")

    try:
        revision = current_contract(
            request.manifest_ref,
            trusted_publics=tuple(trusted_publics),
            store_root=store_root,
        )
    except Exception as exc:
        raise BirthPostconditionError("birth_postcondition_store_invalid", str(exc)) from exc
    if isinstance(revision, ContractRetirement):
        raise BirthPostconditionError("birth_postcondition_retired", revision.retirement_id)
    if not isinstance(revision, VerifiedManifest) or revision.generation_id is None:
        raise BirthPostconditionError("birth_postcondition_generation_invalid")

    generation = revision.generation_id
    try:
        contract_dir = _existing_contract_directory(
            request.manifest_ref.contract_id, store_root=store_root,
        )
        receipt_path = _birth_receipt_path(contract_dir, generation)
        stored = receipt_path.read_bytes()
    except Exception as exc:
        raise BirthPostconditionError("birth_postcondition_receipt_missing", str(exc)) from exc
    if admission_receipt is not None and stored != admission_receipt:
        raise BirthPostconditionError("birth_postcondition_receipt_conflict")
    try:
        receipt = verify_admission_receipt(
            stored, verifier_keys=admission_verifier_keys,
        )
    except Exception as exc:
        raise BirthPostconditionError("birth_postcondition_receipt_invalid", str(exc)) from exc

    if receipt.contract_id != request.manifest_ref.contract_id.value:
        raise BirthPostconditionError("birth_postcondition_binding_invalid", "contract_id")
    if receipt.generation_id != generation:
        raise BirthPostconditionError("birth_postcondition_binding_invalid", "generation_id")
    if receipt.birth_request_id != request.request_id:
        raise BirthPostconditionError("birth_postcondition_binding_invalid", "request_id")
    journal_check = receipt.check_results.get("authoring_install_journal_v1")
    if (
        journal_check is None
        or journal_check.rule_version != "1"
        or journal_check.status is not AdmittedCheckStatus.PASSED
        or journal_check.evidence_hash != receipt.authoring_journal_hash
    ):
        raise BirthPostconditionError("birth_postcondition_binding_invalid", "journal_hash")

    publication = PublicationResult(
        request.manifest_ref.contract_id,
        receipt.predecessor_id,
        generation,
        "commit_birth_snapshot",
        expected.repeated if expected is not None else True,
    )
    if (
        publication.contract_id != request.manifest_ref.contract_id
        or publication.previous_generation_id != receipt.predecessor_id
        or publication.current_generation_id != generation
        or publication.operation != "commit_birth_snapshot"
    ):
        raise BirthPostconditionError("birth_postcondition_binding_invalid", "publication")
    if expected is not None and publication != expected:
        raise BirthPostconditionError("birth_postcondition_publication_conflict")

    try:
        pending = load_prepared_journal(authoring_paths(
            request.manifest_ref.manifest_dir,
            request.manifest_ref.contract_id.value,
        ))
    except Exception as exc:
        raise BirthPostconditionError("birth_postcondition_journal_invalid", str(exc)) from exc
    if pending is not None:
        journal_bindings = {
            "request_id": request.request_id,
            "contract_id": request.manifest_ref.contract_id.value,
            "new_generation_id": generation,
            "predecessor_generation_id": receipt.predecessor_id,
            "candidate_id": receipt.candidate_id,
            "semantic_core_id": receipt.semantic_core_id,
            "admission_context_id": receipt.admission_context_id,
        }
        for field, wanted in journal_bindings.items():
            if getattr(pending, field) != wanted:
                raise BirthPostconditionError("birth_postcondition_journal_conflict", field)
        if pending.journal_hash != receipt.authoring_journal_hash:
            raise BirthPostconditionError("birth_postcondition_journal_conflict", "journal_hash")

    # This is the exact wire shape consumed by the sealed operational replay
    # boundary.  Receipt details stay internal to this verifier.
    return publication, stored


__all__ = [
    "BirthPostconditionError", "verify_birth_postcondition",
]
