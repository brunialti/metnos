"""Authenticated physical receipt values for cross-owner joins."""
import json

import pytest

from executor_birth_retention import RetentionError
from test_birth_retention_signed_store import history, store, D


def test_scan_exposes_each_authenticated_physical_copy(history, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('historical scan attempted current publication or policy')
    for name in ('_authenticate_payloads', '_load_generation', 'current_contract', 'catalog_admission_lock'):
        monkeypatch.setattr(store, name, forbidden)
    objects, admissions = history.owner.scan()
    assert objects == history.owner.inventory()
    assert set(admissions) == {o.identity for o in objects if o.identity.node_type == 'admission_receipt'}
    assert len(admissions) == 2
    for identity, receipt in admissions.items():
        assert receipt.contract_id == history.contract.value
        assert receipt.generation_id == history.generation
        assert receipt.birth_request_id == D
        assert receipt.admission_context_id == D
        assert receipt.approval_hash is None
    with pytest.raises(TypeError):
        admissions[next(iter(admissions))] = None


def test_scan_rejects_forged_receipt(history):
    raw = json.loads(history.encoded)
    raw['birth_request_id'] = 'sha256:' + 'b' * 64
    history.write(history.receipts[0], json.dumps(raw).encode())
    with pytest.raises(RetentionError):
        history.owner.scan()


def test_scan_rejects_custody_change_after_authentication(history, monkeypatch):
    original = history.owner._project
    def changed(*args):
        result = original(*args)
        history.write(history.receipts[0], history.encoded + b'\n')
        return result
    monkeypatch.setattr(history.owner, '_project', changed)
    with pytest.raises(RetentionError, match='changed'):
        history.owner.scan()


def test_native_evidence_matches_each_physical_copy(history):
    objects, admissions, evidence = history.owner.scan_with_evidence()
    assert (objects, admissions) == history.owner.scan()
    assert set(evidence) == set(admissions)
    assert len(evidence) == 2
    assert {item.admission_context_id for item in evidence.values()} == {None, D}
    for identity, item in evidence.items():
        assert item.receipt_bytes == history.encoded
        assert item.contract_id == history.contract
        assert store.verify_historical_birth_evidence_v1(item,
            admission_verifier_keys=history.owner.admission_keys,
            author_verifier_keys=history.owner.author_keys) == admissions[identity]
    with pytest.raises(TypeError):
        evidence[next(iter(evidence))] = None
