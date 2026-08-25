from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_receipts import (
    AdmissionCheck,
    AdmissionKind,
    AdmittedCheckStatus,
    ApprovedLifecycle,
    IssuerKey,
    IssuerRegistry,
    ReceiptError,
    RevisionClass,
    issue_admission_receipt,
    issue_producer_receipt,
    verify_admission_receipt,
    verify_producer_receipt,
)
from manifest_inventory import ContractId, ManifestOrigin


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64
D5 = "sha256:" + "5" * 64
D6 = "sha256:" + "6" * 64
NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _registry(key: Ed25519PrivateKey) -> IssuerRegistry:
    return IssuerRegistry({"synt": (IssuerKey(
        "synt-2026", key.public_key(), frozenset({ExecutorOrigin.SYNTHESIZED}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})


def _producer(key: Ed25519PrivateKey, **changes) -> bytes:
    values = {
        "issuer_id": "synt", "executor_origin": ExecutorOrigin.SYNTHESIZED,
        "revision_authorship": RevisionAuthor.MODEL, "objective_hash": D1,
        "candidate_source_id": D2, "issued_at": "2026-08-25T12:00:00Z",
        "expires_at": "2026-08-25T13:00:00Z",
        "nonce": "0123456789abcdef0123456789abcdef", "key_id": "synt-2026",
        "private_key": key,
    }
    values.update(changes)
    return issue_producer_receipt(**values)


def _admission(key: Ed25519PrivateKey, **changes) -> bytes:
    values = {
        "policy_version": "birth-policy/v1",
        "contract_id": ContractId(ManifestOrigin.USER, "sample/manifest.toml"),
        "generation_id": D1, "candidate_id": D2, "semantic_core_id": D3,
        "admission_context_id": D4, "predecessor_id": None,
        "birth_request_id": D3, "authoring_journal_hash": D4,
        "producer_receipt_hash": D5, "revision_class": RevisionClass.FIRST_BIRTH,
        "check_results": {"manifest": AdmissionCheck(
            "manifest/v1", AdmittedCheckStatus.PASSED, D6,
        )},
        "semantic_review_hash": D2, "approval_hash": None,
        "approved_lifecycle": ApprovedLifecycle.ACTIVE,
        "kind": AdmissionKind.ADMISSION, "issued_at": "2026-08-25T12:00:00Z",
        "key_id": "birth-test", "private_key": key,
    }
    values.update(changes)
    return issue_admission_receipt(**values)


def _mutate(encoded: bytes, operation) -> bytes:
    value = json.loads(encoded)
    operation(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()


def test_producer_round_trip_and_key_rotation(key: Ed25519PrivateKey) -> None:
    receipt = verify_producer_receipt(_producer(key), registry=_registry(key), now=NOW)
    assert receipt.issuer_id == "synt"
    assert receipt.executor_origin is ExecutorOrigin.SYNTHESIZED
    assert receipt.revision_authorship is RevisionAuthor.MODEL
    assert receipt.receipt_id.startswith("sha256:")


@pytest.mark.parametrize("field", ["objective_hash", "candidate_source_id", "nonce", "expires_at"])
def test_producer_tamper_is_rejected(key: Ed25519PrivateKey, field: str) -> None:
    encoded = _mutate(_producer(key), lambda value: value.__setitem__(field, "bad"))
    with pytest.raises(ReceiptError):
        verify_producer_receipt(encoded, registry=_registry(key), now=NOW)


def test_producer_wrong_domain_key_and_authority_are_rejected(key: Ed25519PrivateKey) -> None:
    other = Ed25519PrivateKey.generate()
    with pytest.raises(ReceiptError, match="signature"):
        verify_producer_receipt(_producer(key), registry=_registry(other), now=NOW)
    registry = IssuerRegistry({"synt": (IssuerKey(
        "synt-2026", key.public_key(), frozenset({ExecutorOrigin.HUMAN}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})
    with pytest.raises(ReceiptError, match="origin_authorship_mismatch"):
        verify_producer_receipt(_producer(key), registry=registry, now=NOW)


def test_producer_expiry_future_and_exact_boundary(key: Ed25519PrivateKey) -> None:
    with pytest.raises(ReceiptError, match="expired"):
        verify_producer_receipt(
            _producer(key), registry=_registry(key),
            now=datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc),
        )
    future = _producer(key, issued_at="2026-08-25T12:00:31Z")
    with pytest.raises(ReceiptError, match="future"):
        verify_producer_receipt(future, registry=_registry(key), now=NOW)
    accepted = _producer(key, issued_at="2026-08-25T12:00:30Z")
    assert verify_producer_receipt(accepted, registry=_registry(key), now=NOW)


@pytest.mark.parametrize(
    ("issued", "expires"),
    [("2026-08-25T12:00:00.1Z", "2026-08-25T13:00:00Z"),
     ("2026-08-25T12:00:00+00:00", "2026-08-25T13:00:00Z"),
     ("2026-08-25T13:00:00Z", "2026-08-25T13:00:00Z")],
)
def test_producer_timestamp_form_and_order_are_strict(
    key: Ed25519PrivateKey, issued: str, expires: str,
) -> None:
    with pytest.raises(ReceiptError):
        _producer(key, issued_at=issued, expires_at=expires)


def test_producer_schema_base64_and_canonical_json_are_strict(key: Ed25519PrivateKey) -> None:
    valid = _producer(key)
    with pytest.raises(ReceiptError):
        verify_producer_receipt(valid + b"\n", registry=_registry(key), now=NOW)
    extra = _mutate(valid, lambda value: value.__setitem__("extra", True))
    with pytest.raises(ReceiptError, match="schema"):
        verify_producer_receipt(extra, registry=_registry(key), now=NOW)
    malformed = _mutate(
        valid, lambda value: value["authentication"].__setitem__("signature", "not/base64!"),
    )
    with pytest.raises(ReceiptError, match="signature"):
        verify_producer_receipt(malformed, registry=_registry(key), now=NOW)


def test_duplicate_json_key_is_rejected(key: Ed25519PrivateKey) -> None:
    encoded = _producer(key)
    duplicate = encoded.replace(b'{"authentication":', b'{"issuer_id":"synt","authentication":', 1)
    with pytest.raises(ReceiptError, match="duplicate"):
        verify_producer_receipt(duplicate, registry=_registry(key), now=NOW)


def test_registry_rejects_duplicate_rotation_key(key: Ed25519PrivateKey) -> None:
    entry = IssuerKey(
        "same", key.public_key(), frozenset({ExecutorOrigin.SYNTHESIZED}),
        frozenset({RevisionAuthor.MODEL}),
    )
    with pytest.raises(ReceiptError, match="duplicate issuer key"):
        IssuerRegistry({"synt": (entry, entry)})


def test_admission_round_trip_preserves_null_and_closed_checks(key: Ed25519PrivateKey) -> None:
    receipt = verify_admission_receipt(
        _admission(key), public_key=key.public_key(), expected_key_id="birth-test",
    )
    assert receipt.contract_id == "user:sample/manifest.toml"
    assert receipt.predecessor_id is None
    assert receipt.approval_hash is None
    assert receipt.check_results["manifest"].status is AdmittedCheckStatus.PASSED


def test_admission_keyring_verifies_history_and_rejects_unknown_or_revoked_keys(
    key: Ed25519PrivateKey,
) -> None:
    successor = Ed25519PrivateKey.generate()
    historical = _admission(key, key_id="birth-old")
    current = _admission(successor, key_id="birth-current")
    keyring = {
        "birth-old": key.public_key(),
        "birth-current": successor.public_key(),
    }
    assert verify_admission_receipt(
        historical, verifier_keys=keyring,
    ).authentication.key_id == "birth-old"
    assert verify_admission_receipt(
        current, verifier_keys=keyring,
    ).authentication.key_id == "birth-current"
    with pytest.raises(ReceiptError, match="admission key"):
        verify_admission_receipt(historical, verifier_keys={
            "birth-current": successor.public_key(),
        })
    unknown = _admission(Ed25519PrivateKey.generate(), key_id="birth-unknown")
    with pytest.raises(ReceiptError, match="admission key"):
        verify_admission_receipt(unknown, verifier_keys=keyring)


def test_admission_check_order_has_one_canonical_encoding(key: Ed25519PrivateKey) -> None:
    first = AdmissionCheck("v1", AdmittedCheckStatus.PASSED, D1)
    second = AdmissionCheck("v2", AdmittedCheckStatus.NOT_APPLICABLE, D2)
    assert _admission(key, check_results={"z": second, "a": first}) == _admission(
        key, check_results={"a": first, "z": second},
    )


@pytest.mark.parametrize("status", ["failed", "unavailable", "unknown"])
def test_failed_or_unavailable_check_is_not_representable(
    key: Ed25519PrivateKey, status: str,
) -> None:
    encoded = _mutate(
        _admission(key),
        lambda value: value["check_results"]["manifest"].__setitem__("status", status),
    )
    with pytest.raises(ReceiptError, match="check status"):
        verify_admission_receipt(
            encoded, public_key=key.public_key(), expected_key_id="birth-test",
        )


def test_admission_tamper_wrong_key_id_and_signature_are_rejected(key: Ed25519PrivateKey) -> None:
    valid = _admission(key)
    tampered = _mutate(valid, lambda value: value.__setitem__("generation_id", D6))
    with pytest.raises(ReceiptError):
        verify_admission_receipt(
            tampered, public_key=key.public_key(), expected_key_id="birth-test",
        )
    with pytest.raises(ReceiptError, match="admission key"):
        verify_admission_receipt(
            valid, public_key=key.public_key(), expected_key_id="different",
        )
    with pytest.raises(ReceiptError, match="signature"):
        verify_admission_receipt(
            valid, public_key=Ed25519PrivateKey.generate().public_key(),
            expected_key_id="birth-test",
        )


def test_admission_rejects_invalid_contract_enum_digest_and_extra_field(
    key: Ed25519PrivateKey,
) -> None:
    for operation in (
        lambda value: value.__setitem__("contract_id", "user:../manifest.toml"),
        lambda value: value.__setitem__("approved_lifecycle", "disabled"),
        lambda value: value.__setitem__("approval_hash", ""),
        lambda value: value.__setitem__("extra", None),
    ):
        with pytest.raises(ReceiptError):
            verify_admission_receipt(
                _mutate(_admission(key), operation), public_key=key.public_key(),
                expected_key_id="birth-test",
            )


def test_admission_authentication_requires_canonical_base64(key: Ed25519PrivateKey) -> None:
    encoded = _admission(key)
    value = json.loads(encoded)
    raw = base64.b64decode(value["authentication"]["signature"])
    value["authentication"]["signature"] = base64.b64encode(raw).decode() + "="
    malformed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ReceiptError, match="signature"):
        verify_admission_receipt(
            malformed, public_key=key.public_key(), expected_key_id="birth-test",
        )
