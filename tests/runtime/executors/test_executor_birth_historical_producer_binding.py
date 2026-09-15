"""Real receipt signatures; policy/source custody is a separate owner test."""
from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_receipts as receipts
import executor_birth_producer_store as store
from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_prepared_root import (
    HistoricalContextVerifiersV1, HistoricalProducerDeclarationsV1,
)
from executor_birth_prepared_set import HistoricalProducerVerifiersV1, HistoricalPublicSetV1
from manifest_inventory import ContractId, ManifestOrigin


def digest(number):
    return "sha256:" + str(number) * 64


def _binding(*, producer_changes=None, admission_changes=None, producer_transform=None):
    """Only the context/source owner is a seam; bytes and signatures are real."""
    admission_key = Ed25519PrivateKey.generate()
    producer_key = Ed25519PrivateKey.generate()
    contract = ContractId(ManifestOrigin.USER, "sample/manifest.toml")
    request = receipts.producer_request_id_v1(
        issuer_id="writer", operation="create", contract_id=contract.value,
        objective_hash=digest(1), candidate_source_id=digest(2),
    )
    producer_fields = dict(
        issuer_id="writer", executor_origin=ExecutorOrigin.HUMAN,
        revision_authorship=RevisionAuthor.MODEL, objective_hash=digest(1),
        candidate_source_id=digest(2), issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce=hashlib.sha256(request.encode()).hexdigest()[:32],
        key_id="producer-old", private_key=producer_key,
    )
    producer_fields.update(producer_changes or {})
    producer_bytes = receipts.issue_producer_receipt(**producer_fields)
    producer, _ = receipts._parse_producer(producer_bytes, temporal_now=None)
    if producer_transform is not None:
        producer_bytes = producer_transform(producer_bytes)
    producer_hash = store.producer_receipt_hash(producer_bytes)
    admission_fields = dict(
        policy_version="birth-policy/v1", contract_id=contract,
        generation_id=digest(3), candidate_id=digest(4), semantic_core_id=digest(5),
        admission_context_id=digest(6), birth_request_id=request,
        authoring_journal_hash=digest(7), predecessor_id=None,
        producer_receipt_hash=producer_hash, revision_class=receipts.RevisionClass.FIRST_BIRTH,
        check_results={"manifest": receipts.AdmissionCheck(
            "1", receipts.AdmittedCheckStatus.PASSED, digest(8),
        )},
        semantic_review_hash=None, approval_hash=None,
        approved_lifecycle=receipts.ApprovedLifecycle.ACTIVE,
        kind=receipts.AdmissionKind.ADMISSION, issued_at="2026-08-25T12:00:10Z",
        key_id="admission-old", private_key=admission_key,
    )
    admission_fields.update(admission_changes or {})
    admission_bytes = receipts.issue_admission_receipt(**admission_fields)
    public = HistoricalPublicSetV1(
        "a" * 64, "b" * 64, "c" * 32, "fixture-provisioner",
        SimpleNamespace(pin=SimpleNamespace(admission_context_id=digest(6))),
        {}, {"admission-old": admission_key.public_key()},
        {"writer:create": HistoricalProducerVerifiersV1(
            "fixture-store", {"producer-old": producer_key.public_key()},
        )},
    )
    declarations = HistoricalProducerDeclarationsV1(
        HistoricalContextVerifiersV1(digest(7), digest(8), public),
        digest(9), "fixture-source.py", digest(1),
        {"writer:create": "model"}, {"user": "human"},
    )
    row = store.ProducerReceiptRowV1(
        row_id=2, receipt_id=producer.receipt_id, receipt_hash=producer_hash,
        encoded=producer_bytes, issuer_id=producer.issuer_id,
        objective_hash=producer.objective_hash, candidate_source_id=producer.candidate_source_id,
        executor_origin=producer.executor_origin.value,
        revision_authorship=producer.revision_authorship.value,
        expires_at=producer.expires_at, state="committed",
        registered_at="2099-01-01T00:00:00Z", request_id=request,
        claimed_at="2099-01-01T00:00:00Z", finalized_at="2099-01-01T00:00:01Z",
        result_binding="unsigned fixture, not proof of terminal success",
    )
    issuance = store.ProducerIssuanceRowV1(
        4, request, "writer", "writer:create", contract.value,
        producer.objective_hash, producer.candidate_source_id, producer.receipt_id,
        producer_bytes,
    )
    return dict(admission_encoded=admission_bytes, receipt_row=row,
                issuance_row=issuance, declarations=declarations)


def _replace_public(binding, **changes):
    declarations = binding["declarations"]
    binding["declarations"] = replace(declarations, context=replace(
        declarations.context, public_set=replace(declarations.context.public_set, **changes),
    ))


def test_historical_producer_join_is_pure_immutable_and_uses_signed_act_time(monkeypatch):
    binding = _binding()

    def forbidden(*args, **kwargs):
        pytest.fail("historical binding attempted operational access")

    for name in ("_open", "_migrate", "_verify_claimed", "claim_producer_receipt"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(store.sqlite3, "connect", forbidden)
    result = store.verify_historical_producer_binding_v1(**binding)
    assert result.namespace == "writer:create"
    assert result.producer.expires_at == "2026-08-25T13:00:00Z"
    assert result.admission.issued_at == "2026-08-25T12:00:10Z"
    with pytest.raises(FrozenInstanceError):
        result.namespace = "writer:update"


@pytest.mark.parametrize("state", ("available", "in_progress", "rejected"))
def test_historical_binding_is_not_a_terminal_success_claim(state):
    binding = _binding()
    binding["receipt_row"] = replace(binding["receipt_row"], state=state,
        finalized_at=None, result_binding=None, rejection_code="unsigned status")
    result = store.verify_historical_producer_binding_v1(**binding)
    assert result.namespace == "writer:create"
    assert not hasattr(result, "qualified")


@pytest.mark.parametrize("field", (
    "receipt_id", "receipt_hash", "issuer_id", "objective_hash", "candidate_source_id",
    "executor_origin", "revision_authorship", "expires_at", "request_id",
))
def test_historical_binding_rejects_forged_receipt_columns(field):
    binding = _binding()
    binding["receipt_row"] = replace(binding["receipt_row"], **{field: "forged"})
    with pytest.raises(receipts.ReceiptError, match="history_stored_binding"):
        store.verify_historical_producer_binding_v1(**binding)


@pytest.mark.parametrize("field", (
    "receipt_id", "issuer_id", "objective_hash", "candidate_source_id",
    "request_id", "contract_id", "capability_id",
))
def test_historical_binding_rejects_forged_issuance_columns(field):
    binding = _binding()
    binding["issuance_row"] = replace(binding["issuance_row"], **{field: "forged"})
    with pytest.raises(receipts.ReceiptError, match="history_stored_binding"):
        store.verify_historical_producer_binding_v1(**binding)


@pytest.mark.parametrize("changes, error", (
    ({"executor_origin": ExecutorOrigin.CORE}, "origin_authorship_mismatch"),
    ({"revision_authorship": RevisionAuthor.HUMAN}, "origin_authorship_mismatch"),
    ({"nonce": "0" * 32}, "history_nonce_binding"),
    ({"candidate_source_id": digest(9)}, "history_capability_binding"),
    ({"objective_hash": digest(9)}, "history_capability_binding"),
    ({"issuer_id": "other"}, "history_capability_binding"),
))
def test_historical_binding_does_not_trust_signed_producer_claims_alone(changes, error):
    with pytest.raises(receipts.ReceiptError, match=error):
        store.verify_historical_producer_binding_v1(**_binding(producer_changes=changes))


@pytest.mark.parametrize("act, accepted", (
    ("2026-08-25T11:59:30Z", True),
    ("2026-08-25T11:59:29Z", False),
    ("2026-08-25T12:59:59Z", True),
    ("2026-08-25T13:00:00Z", False),
))
def test_historical_binding_preserves_existing_time_boundaries(act, accepted):
    binding = _binding(admission_changes={"issued_at": act})
    if accepted:
        assert store.verify_historical_producer_binding_v1(**binding)
    else:
        with pytest.raises(receipts.ReceiptError):
            store.verify_historical_producer_binding_v1(**binding)


@pytest.mark.parametrize("changes", (
    {"admission_context_id": digest(9)},
    {"kind": receipts.AdmissionKind.REATTESTATION},
    {"producer_receipt_hash": digest(9)},
    {"birth_request_id": digest(9)},
    {"contract_id": ContractId(ManifestOrigin.USER, "other/manifest.toml")},
))
def test_historical_binding_rejects_inconsistent_signed_admission(changes):
    with pytest.raises(receipts.ReceiptError):
        store.verify_historical_producer_binding_v1(**_binding(admission_changes=changes))


@pytest.mark.parametrize("case", ("admission-key", "producer-key", "missing-key", "missing-origin", "missing-author", "wrong-operation", "different-bytes"))
def test_historical_binding_rejects_inconsistent_public_scope_or_bytes(case):
    binding = _binding()
    if case == "admission-key":
        _replace_public(binding, admission_verifier_keys={
            "admission-old": Ed25519PrivateKey.generate().public_key(),
        })
    elif case in {"producer-key", "missing-key", "wrong-operation"}:
        keys = binding["declarations"].context.public_set.producers["writer:create"]
        if case != "wrong-operation":
            keys = replace(keys, verifier_keys={} if case == "missing-key" else {
                "producer-old": Ed25519PrivateKey.generate().public_key(),
            })
        _replace_public(binding, producers={
            "writer:update" if case == "wrong-operation" else "writer:create": keys,
        })
    elif case == "different-bytes":
        binding["issuance_row"] = replace(binding["issuance_row"], encoded=b"other")
    else:
        field = "authors" if case == "missing-author" else "executor_origins"
        binding["declarations"] = replace(binding["declarations"], **{field: {}})
    with pytest.raises(receipts.ReceiptError):
        store.verify_historical_producer_binding_v1(**binding)


def test_historical_binding_keeps_old_key_after_rotation():
    binding = _binding()
    public = binding["declarations"].context.public_set
    producer_keys = public.producers["writer:create"]
    _replace_public(binding,
        admission_verifier_keys={**public.admission_verifier_keys,
            "admission-new": Ed25519PrivateKey.generate().public_key()},
        producers={"writer:create": replace(producer_keys, verifier_keys={
            **producer_keys.verifier_keys, "producer-new": Ed25519PrivateKey.generate().public_key(),
        })},
    )
    assert store.verify_historical_producer_binding_v1(**binding).producer.authentication.key_id == "producer-old"


@pytest.mark.parametrize("field, value", (
    ("admission_encoded", b"x" * (1024 * 1024 + 1)),
    ("admission_encoded", "not bytes"),
    ("receipt_row", b"x" * (store._HISTORY_ROW_BYTES + 1)),
    ("issuance_row", b"x" * (store._HISTORY_ROW_BYTES + 1)),
))
def test_historical_binding_bounds_bytes_before_parsing(field, value, monkeypatch):
    binding = _binding()
    if field == "admission_encoded":
        binding[field] = value
    else:
        binding[field] = replace(binding[field], encoded=value)
    monkeypatch.setattr(receipts, "verify_admission_receipt", lambda *_args, **_kw: pytest.fail("parsed oversized input"))
    with pytest.raises(receipts.ReceiptError, match="history_binding_bytes"):
        store.verify_historical_producer_binding_v1(**binding)


@pytest.mark.parametrize("payload", (
    b"[" * 2000 + b"]" * 2000,
    b'{"value":"\\ud800"}',
    b'{"value":' + b"1" * 5000 + b"}",
))
@pytest.mark.parametrize("side", ("admission", "producer"))
def test_historical_binding_normalizes_malformed_wire_decoding(payload, side):
    binding = _binding(producer_transform=(lambda _old: payload) if side == "producer" else None)
    if side == "admission":
        binding["admission_encoded"] = payload
    with pytest.raises(receipts.ReceiptError):
        store.verify_historical_producer_binding_v1(**binding)


@pytest.mark.parametrize("values", (
    ("writer", "create", "user:sample/manifest.toml", digest(1), digest(2)),
    ("é漢字", "modifica", "user:città/manifest.toml", digest(1), digest(2)),
    ("ab", "c", "", "", ""),
    ("a", "bc", "", "", ""),
))
def test_request_v1_codec_matches_original_factory_byte_framing(values):
    import executor_birth_bootstrap as bootstrap

    encoded = receipts.producer_request_id_v1(**dict(zip(
        ("issuer_id", "operation", "contract_id", "objective_hash", "candidate_source_id"), values,
    )))
    assert encoded == bootstrap._hash(b"metnos.executor-birth.request/v1\0", *values)
