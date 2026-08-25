from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import Barrier, Lock, Thread

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_producer_store import (
    ProducerReceiptBinding,
    consume_producer_receipt,
    register_producer_receipt,
)
from executor_birth_receipts import (
    IssuerKey,
    IssuerRegistry,
    ReceiptError,
    issue_producer_receipt,
)


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
REQUEST = "sha256:" + "3" * 64
ISSUED = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _fixture():
    key = Ed25519PrivateKey.generate()
    registry = IssuerRegistry({"synt": (IssuerKey(
        "synt-1", key.public_key(), frozenset({ExecutorOrigin.SYNTHESIZED}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})
    encoded = issue_producer_receipt(
        issuer_id="synt", executor_origin=ExecutorOrigin.SYNTHESIZED,
        revision_authorship=RevisionAuthor.MODEL, objective_hash=D1,
        candidate_source_id=D2, issued_at="2026-08-25T12:00:00Z",
        expires_at="2026-08-25T13:00:00Z",
        nonce="0123456789abcdef0123456789abcdef", key_id="synt-1",
        private_key=key,
    )
    binding = ProducerReceiptBinding(
        D1, D2, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL,
    )
    return key, registry, encoded, binding


def test_registered_receipt_is_consumed_exactly_once(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    result = consume_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED, db_path=db,
    )
    assert result.objective_hash == D1
    with pytest.raises(ReceiptError, match="producer_receipt_replay"):
        consume_producer_receipt(
            encoded, registry=registry, binding=binding, request_id=REQUEST,
            now=ISSUED, db_path=db,
        )


def test_tamper_wrong_registry_and_unregistered_receipt_fail_closed(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    value = json.loads(encoded)
    value["objective_hash"] = "sha256:" + "9" * 64
    tampered = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ReceiptError):
        consume_producer_receipt(
            tampered, registry=registry, binding=binding, request_id=REQUEST,
            now=ISSUED, db_path=db,
        )
    other = Ed25519PrivateKey.generate()
    wrong = IssuerRegistry({"synt": (IssuerKey(
        "synt-1", other.public_key(), frozenset({ExecutorOrigin.SYNTHESIZED}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})
    with pytest.raises(ReceiptError, match="signature"):
        consume_producer_receipt(
            encoded, registry=wrong, binding=binding, request_id=REQUEST,
            now=ISSUED, db_path=db,
        )
    _, registry2, encoded2, binding2 = _fixture()
    with pytest.raises(ReceiptError, match="not_registered"):
        consume_producer_receipt(
            encoded2, registry=registry2, binding=binding2, request_id=REQUEST,
            now=ISSUED, db_path=tmp_path / "other.sqlite",
        )


@pytest.mark.parametrize(
    "binding",
    [
        ProducerReceiptBinding("sha256:" + "8" * 64, D2, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL),
        ProducerReceiptBinding(D1, "sha256:" + "8" * 64, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.MODEL),
        ProducerReceiptBinding(D1, D2, ExecutorOrigin.HUMAN, RevisionAuthor.MODEL),
        ProducerReceiptBinding(D1, D2, ExecutorOrigin.SYNTHESIZED, RevisionAuthor.HUMAN),
    ],
)
def test_every_snapshot_binding_is_enforced(tmp_path, binding):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    with pytest.raises(ReceiptError, match="producer_receipt_binding_invalid"):
        consume_producer_receipt(
            encoded, registry=registry, binding=binding, request_id=REQUEST,
            now=ISSUED, db_path=db,
        )


def test_expiry_is_checked_again_at_consumption(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    with pytest.raises(ReceiptError, match="producer_receipt_expired"):
        consume_producer_receipt(
            encoded, registry=registry, binding=binding, request_id=REQUEST,
            now=datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc), db_path=db,
        )


def test_concurrent_consumption_has_one_cas_winner(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    barrier = Barrier(2)
    lock = Lock()
    outcomes = []

    def consume():
        barrier.wait()
        try:
            value = consume_producer_receipt(
                encoded, registry=registry, binding=binding, request_id=REQUEST,
                now=ISSUED, db_path=db,
            )
        except ReceiptError as exc:
            value = exc.code
        with lock:
            outcomes.append(value)

    threads = [Thread(target=consume), Thread(target=consume)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert sum(not isinstance(item, str) for item in outcomes) == 1
    assert outcomes.count("producer_receipt_replay") == 1


def test_manifest_cannot_declare_issuer_authority(tmp_path):
    # The native API has no manifest parameter; only the registry authenticates.
    key, _, encoded, binding = _fixture()
    denied = IssuerRegistry({"synt": (IssuerKey(
        "synt-1", key.public_key(), frozenset({ExecutorOrigin.HUMAN}),
        frozenset({RevisionAuthor.MODEL}),
    ),)})
    with pytest.raises(ReceiptError, match="origin_authorship_mismatch"):
        register_producer_receipt(
            encoded, registry=denied, now=ISSUED,
            db_path=tmp_path / "producer.sqlite",
        )
