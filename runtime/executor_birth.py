"""Observational RM-0008 Birth boundary.

F1 only acquires an owned candidate and computes identities.  It deliberately
does not sign, publish, activate, consume receipts or change lifecycle state.
The operational ``birth_executor`` API is introduced only after the later
observation and commit phases satisfy their exit gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from executor_birth_identity import (
    AdmissionContextV1,
    CandidateIdentities,
    CandidateIdentityInput,
    ExecutorOrigin,
    RevisionAuthor,
    compute_candidate_identities,
)
from executor_birth_snapshot import CandidateSnapshot, acquire_candidate_snapshot
from manifest_inventory import ContractId


@dataclass(frozen=True, slots=True)
class ObservedCandidate:
    """Owned F1 observation; closing it destroys the private byte copy."""

    contract_id: ContractId
    snapshot: CandidateSnapshot
    identities: CandidateIdentities
    executor_origin: ExecutorOrigin
    revision_authorship: RevisionAuthor
    objective_hash: str

    def close(self) -> None:
        self.snapshot.close()

    def __enter__(self) -> "ObservedCandidate":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def observe_candidate(
    source_root: Path | str,
    *,
    contract_id: ContractId,
    executor_origin: ExecutorOrigin,
    revision_authorship: RevisionAuthor,
    objective_hash: str,
    admission_context: AdmissionContextV1,
    private_parent: Path | str | None = None,
) -> ObservedCandidate:
    """Acquire and identify one candidate without operational side effects."""
    snapshot = acquire_candidate_snapshot(
        source_root, private_parent=private_parent,
    )
    try:
        identity_input = CandidateIdentityInput(
            contract_id=contract_id,
            manifest_bytes=snapshot.manifest_bytes,
            language_state_bytes=snapshot.language_state_bytes,
            code_files=snapshot.code_files,
            executor_origin=executor_origin,
            revision_authorship=revision_authorship,
            objective_hash=objective_hash,
        )
        identities = compute_candidate_identities(identity_input, admission_context)
    except Exception:
        snapshot.close()
        raise
    return ObservedCandidate(
        contract_id=contract_id,
        snapshot=snapshot,
        identities=identities,
        executor_origin=executor_origin,
        revision_authorship=revision_authorship,
        objective_hash=objective_hash,
    )
