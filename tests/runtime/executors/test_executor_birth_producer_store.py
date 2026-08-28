from __future__ import annotations

import json
import multiprocessing
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, Thread

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_identity import ExecutorOrigin, RevisionAuthor
from executor_birth_producer_store import (
    ProducerReceiptBinding,
    claim_producer_receipt,
    consume_producer_receipt,
    finalize_producer_receipt,
    get_or_issue_and_claim_producer_receipt,
    get_or_issue_producer_receipt,
    recover_producer_receipt_claim,
    register_producer_receipt,
)
from executor_birth_receipts import (
    IssuerKey,
    IssuerRegistry,
    ReceiptError,
    issue_producer_receipt,
    verify_producer_receipt,
)


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
REQUEST = "sha256:" + "3" * 64
ISSUED = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
CAPABILITY = "synt_multistage:create_or_replay"
CONTRACT = "executor:test/example"


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


def _issue_once(db, registry, encoded, *, request=REQUEST, capability=CAPABILITY,
                contract=CONTRACT, objective=D1, source=D2, callback=None):
    return get_or_issue_producer_receipt(
        request_id=request, issuer_id="synt", capability_id=capability,
        contract_id=contract, objective_hash=objective,
        candidate_source_id=source, registry=registry, now=ISSUED,
        db_path=db, issue=callback or (lambda: encoded),
    )


def _issue_and_claim_once(
    db, registry, encoded, binding, *, now=ISSUED, lease_seconds=300,
    callback=None,
):
    return get_or_issue_and_claim_producer_receipt(
        request_id=REQUEST, issuer_id="synt", capability_id=CAPABILITY,
        contract_id=CONTRACT, binding=binding, registry=registry, now=now,
        db_path=db, lease_seconds=lease_seconds,
        issue=callback or (lambda: encoded),
    )


def test_issuance_restarts_with_identical_bytes_without_reinvoking_issuer(tmp_path):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    assert _issue_once(db, registry, encoded) == encoded

    def forbidden():
        raise AssertionError("issuer callback must not run on replay")

    assert _issue_once(db, registry, b"not-used", callback=forbidden) == encoded
    with sqlite3.connect(db) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 5
        row = check.execute(
            "SELECT capability_id,contract_id,encoded FROM birth_producer_issuance"
        ).fetchone()
    assert row[:2] == (CAPABILITY, CONTRACT)
    assert bytes(row[2]) == encoded


def test_atomic_issuance_and_claim_is_single_under_concurrency(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    barrier = Barrier(8)
    lock = Lock()
    calls: list[int] = []
    results: list[bytes] = []

    def issue():
        with lock:
            calls.append(1)
        return encoded

    def run():
        barrier.wait()
        value = _issue_and_claim_once(db, registry, encoded, binding, callback=issue)
        with lock:
            results.append(value)

    threads = [Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert calls == [1]
    assert results == [encoded] * 8
    with sqlite3.connect(db) as check:
        assert check.execute(
            "SELECT state,request_id FROM birth_producer_receipts",
        ).fetchone() == ("in_progress", REQUEST)


def test_atomic_factory_retry_renews_same_request_after_lease_expiry(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    _issue_and_claim_once(
        db, registry, encoded, binding, lease_seconds=1,
    )
    with sqlite3.connect(db) as check:
        first = check.execute(
            "SELECT lease_expires_at FROM birth_producer_receipts",
        ).fetchone()[0]
    assert _issue_and_claim_once(
        db, registry, encoded, binding,
        now=ISSUED + timedelta(seconds=2), lease_seconds=300,
        callback=lambda: pytest.fail("issuer callback ran on retry"),
    ) == encoded
    with sqlite3.connect(db) as check:
        second = check.execute(
            "SELECT lease_expires_at FROM birth_producer_receipts",
        ).fetchone()[0]
    assert second > first


@pytest.mark.parametrize("changed", ["capability", "contract", "objective", "source", "issuer"])
def test_issuance_request_id_rejects_every_changed_binding(tmp_path, changed):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    _issue_once(db, registry, encoded)
    values = dict(capability=CAPABILITY, contract=CONTRACT, objective=D1, source=D2)
    if changed != "issuer":
        values[changed] = ("sha256:" + "9" * 64 if changed in {"objective", "source"}
                           else "different")
    kwargs = dict(
        request_id=REQUEST, issuer_id="different" if changed == "issuer" else "synt",
        capability_id=values["capability"], contract_id=values["contract"],
        objective_hash=values["objective"], candidate_source_id=values["source"],
        registry=registry, now=ISSUED, db_path=db,
        issue=lambda: pytest.fail("issuer callback ran for conflicting binding"),
    )
    with pytest.raises(ReceiptError, match="producer_receipt_request_conflict"):
        get_or_issue_producer_receipt(**kwargs)


def test_concurrent_issuance_threads_call_issuer_once(tmp_path):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    barrier = Barrier(8); lock = Lock(); calls = []; results = []

    def issue():
        with lock:
            calls.append(1)
        return encoded

    def run():
        barrier.wait()
        value = _issue_once(db, registry, encoded, callback=issue)
        with lock:
            results.append(value)

    threads = [Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(calls) == 1
    assert results == [encoded] * 8


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_concurrent_issuance_processes_return_identical_bytes(tmp_path):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(4)
    calls = context.Value("i", 0)
    results = context.Queue()

    def run():
        def issue():
            with calls.get_lock():
                calls.value += 1
            return encoded
        barrier.wait()
        try:
            results.put((True, _issue_once(db, registry, encoded, callback=issue)))
        except Exception as exc:  # pragma: no cover - reported in parent
            results.put((False, repr(exc)))

    processes = [context.Process(target=run) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert not process.is_alive()
        assert process.exitcode == 0
    observed = [results.get(timeout=2) for _ in processes]
    assert observed == [(True, encoded)] * 4
    assert calls.value == 1


def test_failed_ledger_insert_rolls_back_receipt_registration(tmp_path):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    # Establish/migrate the schema, then force the second statement of the
    # issuance transaction to abort. The receipt row must not leak through.
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.close()
    from executor_birth_producer_store import _open
    initialized = _open(db); initialized.close()
    connection = sqlite3.connect(db)
    connection.execute("""CREATE TRIGGER abort_issuance BEFORE INSERT ON birth_producer_issuance
                         BEGIN SELECT RAISE(ABORT, 'simulated crash frontier'); END""")
    connection.commit(); connection.close()
    with pytest.raises(sqlite3.IntegrityError, match="simulated crash frontier"):
        _issue_once(db, registry, encoded)
    with sqlite3.connect(db) as check:
        assert check.execute("SELECT count(*) FROM birth_producer_receipts").fetchone()[0] == 0
        check.execute("DROP TRIGGER abort_issuance"); check.commit()
    assert _issue_once(db, registry, encoded) == encoded


def test_v4_issuance_migration_preserves_unknown_binding_fail_closed(tmp_path):
    _, registry, encoded, _ = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    with sqlite3.connect(db) as old:
        old.execute("DROP TABLE birth_producer_issuance")
        old.execute("""CREATE TABLE birth_producer_issuance (
          request_id TEXT PRIMARY KEY, issuer_id TEXT NOT NULL, operation TEXT NOT NULL,
          objective_hash TEXT NOT NULL, candidate_source_id TEXT NOT NULL,
          encoded BLOB NOT NULL UNIQUE)""")
        old.execute("INSERT INTO birth_producer_issuance VALUES (?,?,?,?,?,?)",
                    (REQUEST, "synt", CAPABILITY, D1, D2, encoded))
        old.execute("PRAGMA user_version=4")
    with pytest.raises(ReceiptError, match="producer_receipt_request_conflict"):
        _issue_once(db, registry, encoded,
                    callback=lambda: pytest.fail("legacy issuance was reopened"))
    with sqlite3.connect(db) as check:
        row = check.execute(
            "SELECT capability_id,contract_id,encoded FROM birth_producer_issuance"
        ).fetchone()
        assert check.execute("PRAGMA user_version").fetchone()[0] == 5
    assert row[:2] == (CAPABILITY, "__legacy_unknown_contract__")
    assert bytes(row[2]) == encoded


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


def test_claim_survives_reopen_and_same_request_replays_idempotently(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    first = claim_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED, db_path=db, lease_seconds=30,
    )
    replay = claim_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED + timedelta(seconds=1), db_path=db, lease_seconds=999,
    )
    assert replay == first


def test_different_request_can_never_steal_live_or_expired_claim(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    other = "sha256:" + "4" * 64
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    claim_producer_receipt(encoded, registry=registry, binding=binding,
                           request_id=REQUEST, now=ISSUED, db_path=db, lease_seconds=2)
    for instant in (ISSUED + timedelta(seconds=1), ISSUED + timedelta(seconds=2)):
        with pytest.raises(ReceiptError, match="owned_by_other_request"):
            claim_producer_receipt(encoded, registry=registry, binding=binding,
                                   request_id=other, now=instant, db_path=db)


def test_expired_lease_requires_explicit_recovery_by_owner(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    claim_producer_receipt(encoded, registry=registry, binding=binding,
                           request_id=REQUEST, now=ISSUED, db_path=db, lease_seconds=2)
    expired = ISSUED + timedelta(seconds=2)
    with pytest.raises(ReceiptError, match="explicit_recovery_required"):
        claim_producer_receipt(encoded, registry=registry, binding=binding,
                               request_id=REQUEST, now=expired, db_path=db)
    recovered = recover_producer_receipt_claim(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=expired, db_path=db, lease_seconds=5,
    )
    assert recovered.lease_expires_at == "2026-08-25T12:00:07Z"


def test_terminal_result_is_durable_idempotent_and_conflicts_fail(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    result = "sha256:" + "5" * 64
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    claim_producer_receipt(encoded, registry=registry, binding=binding,
                           request_id=REQUEST, now=ISSUED, db_path=db)
    committed = finalize_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED, db_path=db, result_binding=result,
    )
    assert committed.state == "committed" and committed.result_binding == result
    assert finalize_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED, db_path=db, result_binding=result,
    ) == committed
    assert claim_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED, db_path=db,
    ) == committed
    with pytest.raises(ReceiptError, match="producer_receipt_final_conflict"):
        finalize_producer_receipt(
            encoded, registry=registry, binding=binding, request_id=REQUEST,
            now=ISSUED, db_path=db, rejection_code="publication_failed",
        )


def test_concurrent_different_requests_have_one_permanent_owner(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    requests = [REQUEST, "sha256:" + "4" * 64]
    barrier = Barrier(2); outcomes = []; lock = Lock()
    def run(request):
        barrier.wait()
        try:
            value = claim_producer_receipt(encoded, registry=registry, binding=binding,
                                           request_id=request, now=ISSUED, db_path=db)
            value = value.request_id
        except ReceiptError as exc:
            value = exc.code
        with lock: outcomes.append(value)
    threads = [Thread(target=run, args=(request,)) for request in requests]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=5)
    assert sum(value in requests for value in outcomes) == 1
    assert outcomes.count("producer_receipt_replay") == 1


def test_concurrent_retries_of_same_request_observe_one_claim(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    barrier = Barrier(2); outcomes = []; lock = Lock()
    def run():
        barrier.wait()
        value = claim_producer_receipt(encoded, registry=registry, binding=binding,
                                       request_id=REQUEST, now=ISSUED, db_path=db)
        with lock: outcomes.append(value)
    threads = [Thread(target=run), Thread(target=run)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=5)
    assert len(outcomes) == 2 and outcomes[0] == outcomes[1]


def test_crash_before_finalization_cannot_be_mistaken_for_commit(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    claim_producer_receipt(encoded, registry=registry, binding=binding,
                           request_id=REQUEST, now=ISSUED, db_path=db, lease_seconds=2)
    # Reopening after the simulated process loss exposes in_progress, never a
    # false committed result.  Recovery is an explicit owner-only operation.
    with pytest.raises(ReceiptError, match="explicit_recovery_required"):
        finalize_producer_receipt(
            encoded, registry=registry, binding=binding, request_id=REQUEST,
            now=ISSUED + timedelta(seconds=2), db_path=db,
            result_binding="sha256:" + "5" * 64,
        )
    recover_producer_receipt_claim(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED + timedelta(seconds=2), db_path=db,
    )
    rejected = finalize_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=ISSUED + timedelta(seconds=2), db_path=db,
        rejection_code="postcondition_unknown_after_crash",
    )
    assert rejected.state == "rejected"


def test_claimed_authority_can_be_recovered_after_issue_expiry(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    register_producer_receipt(encoded, registry=registry, now=ISSUED, db_path=db)
    claim_producer_receipt(encoded, registry=registry, binding=binding,
                           request_id=REQUEST, now=ISSUED, db_path=db, lease_seconds=2)
    after_expiry = ISSUED + timedelta(hours=1)
    recovered = recover_producer_receipt_claim(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=after_expiry, db_path=db,
    )
    assert recovered.state == "in_progress"
    committed = finalize_producer_receipt(
        encoded, registry=registry, binding=binding, request_id=REQUEST,
        now=after_expiry, db_path=db, result_binding="sha256:" + "5" * 64,
    )
    assert committed.state == "committed"


def test_v1_database_migrates_available_and_consumed_without_reopening_authority(tmp_path):
    _, registry, encoded, binding = _fixture()
    db = tmp_path / "producer.sqlite"
    old = sqlite3.connect(db)
    old.executescript("""CREATE TABLE birth_producer_receipts (
      receipt_id TEXT PRIMARY KEY,receipt_hash TEXT NOT NULL,encoded BLOB NOT NULL,issuer_id TEXT NOT NULL,
      objective_hash TEXT NOT NULL,candidate_source_id TEXT NOT NULL,executor_origin TEXT NOT NULL,
      revision_authorship TEXT NOT NULL,expires_at TEXT NOT NULL,state TEXT NOT NULL,
      registered_at TEXT NOT NULL,consumed_at TEXT,request_id TEXT);
    """)
    receipt = verify_producer_receipt(encoded, registry=registry, now=ISSUED)
    from executor_birth_producer_store import producer_receipt_hash
    old.execute("INSERT INTO birth_producer_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt.receipt_id, producer_receipt_hash(encoded), encoded, receipt.issuer_id,
                 receipt.objective_hash, receipt.candidate_source_id, receipt.executor_origin.value,
                 receipt.revision_authorship.value, receipt.expires_at, "consumed",
                 "2026-08-25T12:00:00Z", "2026-08-25T12:00:01Z", REQUEST))
    old.commit(); old.close()
    replay = claim_producer_receipt(encoded, registry=registry, binding=binding,
                                    request_id=REQUEST, now=ISSUED, db_path=db)
    assert replay.state == "rejected" and replay.rejection_code == "legacy_terminal"
    with sqlite3.connect(db) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 5
