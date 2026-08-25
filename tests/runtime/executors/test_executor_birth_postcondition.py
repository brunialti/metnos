from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_postcondition as postcondition
from contract_store import (
    ContractRetirement, PublicationResult, VerifiedManifest, contract_storage_key,
    encode_binding,
)
from executor_birth_authoring import (
    AuthoringInstallJournalV1, authoring_paths, persist_prepared_journal,
)
from executor_birth_operational import BirthRequest
from executor_birth_postcondition import (
    BirthPostconditionError, verify_birth_postcondition,
)
from executor_birth_receipts import (
    AdmissionCheck, AdmissionKind, AdmittedCheckStatus, ApprovedLifecycle,
    RevisionClass, issue_admission_receipt,
)
from manifest_inventory import ContractId, ManifestOrigin, ManifestRef, ManifestStatus


def _d(character: str) -> str:
    return "sha256:" + character * 64


REQUEST = _d("1")
GENERATION = _d("2")
PREDECESSOR = _d("3")
CANDIDATE = _d("4")
SEMANTIC = _d("5")
CONTEXT = _d("6")


def _fixture(tmp_path: Path, *, journal: bool = False):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.toml").write_bytes(b"manifest")
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    ref = ManifestRef(
        contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
        source, source / "manifest.toml", "demo/manifest.toml", (source,),
    )
    request = BirthRequest(
        REQUEST, ref, b"producer", "actor", "reason", (), "update", source,
    )
    pending = AuthoringInstallJournalV1(
        request_id=REQUEST, contract_id=contract_id.value,
        source_origin=ManifestOrigin.USER.value,
        canonical_tree_id=_d("7"), old_tree_id=_d("8"), new_tree_id=_d("9"),
        candidate_id=CANDIDATE, semantic_core_id=SEMANTIC,
        admission_context_id=CONTEXT,
        predecessor_generation_id=PREDECESSOR,
        new_generation_id=GENERATION,
        staging_basename=f".birth-stage-{REQUEST[7:]}",
        backup_basename=f".birth-backup-{REQUEST[7:]}",
    )
    journal_hash = pending.journal_hash
    if journal:
        persist_prepared_journal(authoring_paths(source, contract_id.value), pending)

    private = Ed25519PrivateKey.generate()
    receipt = issue_admission_receipt(
        policy_version="birth-v1", contract_id=contract_id,
        generation_id=GENERATION, candidate_id=CANDIDATE,
        semantic_core_id=SEMANTIC, admission_context_id=CONTEXT,
        birth_request_id=REQUEST, authoring_journal_hash=journal_hash,
        predecessor_id=PREDECESSOR, producer_receipt_hash=_d("a"),
        revision_class=RevisionClass.CODE_REVISION,
        check_results={"authoring_install_journal_v1": AdmissionCheck(
            "1", AdmittedCheckStatus.PASSED, journal_hash,
        )}, semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=ApprovedLifecycle.ACTIVE,
        kind=AdmissionKind.ADMISSION, issued_at="2026-08-25T12:00:00Z",
        key_id="birth-1", private_key=private,
    )
    store = tmp_path / "store"
    contract_dir = store / contract_storage_key(contract_id)
    (contract_dir / "admission-receipts").mkdir(parents=True)
    (contract_dir / "binding.json").write_bytes(encode_binding(contract_id))
    (contract_dir / "current").write_text(GENERATION + "\n", encoding="ascii")
    (contract_dir / "admission-receipts" / f"{GENERATION[7:]}.json").write_bytes(receipt)
    revision = VerifiedManifest(
        contract_id, GENERATION, source, (source,), b"manifest", _d("b"), {},
        b"sig", _d("c"), b"{}", {}, "manifest-key", _d("d"), _d("d"),
    )
    expected = PublicationResult(
        contract_id, PREDECESSOR, GENERATION, "commit_birth_snapshot", False,
    )
    return request, expected, receipt, private, store, revision, pending


@pytest.mark.parametrize("journal", [False, True])
def test_verifies_committed_and_crash_after_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, journal: bool,
) -> None:
    request, expected, receipt, private, store, revision, _pending = _fixture(
        tmp_path, journal=journal,
    )
    monkeypatch.setattr(postcondition, "current_contract", lambda *_a, **_k: revision)
    publication, verified_receipt = verify_birth_postcondition(
        request, expected, receipt, trusted_publics=(),
        admission_verifier_keys={"birth-1": private.public_key()}, store_root=store,
    )
    assert publication == expected
    assert verified_receipt == receipt


@pytest.mark.parametrize("failure", ["missing", "tamper", "conflict"])
def test_receipt_failures_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    request, expected, receipt, private, store, revision, _pending = _fixture(tmp_path)
    monkeypatch.setattr(postcondition, "current_contract", lambda *_a, **_k: revision)
    receipt_path = next(store.rglob("admission-receipts/*.json"))
    supplied = receipt
    if failure == "missing":
        receipt_path.unlink()
    elif failure == "tamper":
        receipt_path.write_bytes(receipt[:-1] + bytes([receipt[-1] ^ 1]))
        supplied = None
    else:
        supplied = b"different"
    with pytest.raises(BirthPostconditionError):
        verify_birth_postcondition(
            request, expected, supplied, trusted_publics=(),
            admission_verifier_keys={"birth-1": private.public_key()}, store_root=store,
        )


def test_expected_predecessor_conflict_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, expected, receipt, private, store, revision, _pending = _fixture(tmp_path)
    monkeypatch.setattr(postcondition, "current_contract", lambda *_a, **_k: revision)
    conflicting = PublicationResult(
        expected.contract_id, _d("e"), expected.current_generation_id,
        expected.operation, expected.repeated,
    )
    with pytest.raises(BirthPostconditionError, match="publication_conflict"):
        verify_birth_postcondition(
            request, conflicting, receipt, trusted_publics=(),
            admission_verifier_keys={"birth-1": private.public_key()}, store_root=store,
        )


def test_retirement_is_not_a_successful_birth_postcondition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, expected, receipt, private, store, _revision, _pending = _fixture(tmp_path)
    retirement = ContractRetirement(
        request.manifest_ref.contract_id, _d("f"), GENERATION,
        "operator", "retired", b"payload", b"sig", _d("a"), "manifest-key",
    )
    monkeypatch.setattr(postcondition, "current_contract", lambda *_a, **_k: retirement)
    with pytest.raises(BirthPostconditionError, match="retired"):
        verify_birth_postcondition(
            request, expected, receipt, trusted_publics=(),
            admission_verifier_keys={"birth-1": private.public_key()}, store_root=store,
        )


def test_surviving_journal_must_match_receipt_and_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, expected, receipt, private, store, revision, pending = _fixture(
        tmp_path, journal=True,
    )
    monkeypatch.setattr(postcondition, "current_contract", lambda *_a, **_k: revision)
    conflicting = AuthoringInstallJournalV1(
        **{**pending.as_dict(), "candidate_id": _d("e")},
    )
    persist_prepared_journal(
        authoring_paths(request.manifest_ref.manifest_dir, request.manifest_ref.contract_id.value),
        conflicting,
    )
    with pytest.raises(BirthPostconditionError, match="journal_conflict"):
        verify_birth_postcondition(
            request, expected, receipt, trusted_publics=(),
            admission_verifier_keys={"birth-1": private.public_key()}, store_root=store,
        )
