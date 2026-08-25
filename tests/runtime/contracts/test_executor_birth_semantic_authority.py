from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from executor_birth_semantic_authority import (
    EVIDENCE_DOMAIN, PreprovisionedSemanticAuthority, derive_review_risk_facts,
    load_semantic_authority,
)
from executor_birth_semantic_review import (
    IndependentEvidenceKind, ReviewPolicyV1, SemanticReviewError,
    SemanticReviewRequest,
)


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def request(code=b"print('ok')"):
    return SemanticReviewRequest(D1, D2, "model:generator", b"name='x'", b"{}", {"main.py": code})


def policy():
    return ReviewPolicyV1(
        {kind: frozenset({"v1"}) for kind in IndependentEvidenceKind},
        {kind: frozenset({"oracle"}) for kind in IndependentEvidenceKind},
    )


def provision(root, private, **changes):
    evidence = {
        "evidence_id": D3, "evidence_version": "v1", "kind": "deterministic_oracle",
        "owner_id": "oracle", "candidate_id": D1, "admission_context_id": D2,
        "status": "passed", "evidence_hash": D4,
    }
    evidence.update(changes)
    record = {"schema_version": 1, "key_id": "oracle-key", "evidence": evidence,
              "signature": base64.b64encode(private.sign(EVIDENCE_DOMAIN + canonical(evidence))).decode()}
    root.mkdir(mode=0o700)
    path = root / "proof.json"
    path.write_bytes(canonical(record)); path.chmod(0o600)


def test_authority_authenticates_aligned_independent_evidence(tmp_path):
    private = Ed25519PrivateKey.generate(); store = tmp_path / "evidence"
    provision(store, private)
    authority = PreprovisionedSemanticAuthority(policy(), store,
                                                 {"oracle-key": private.public_key()})
    loaded_policy, facts, evidence = authority.inputs_for(request())
    assert loaded_policy is authority.policy
    assert evidence[0].evidence_id == D3
    assert facts == derive_review_risk_facts(request())


@pytest.mark.parametrize("mutation", ["signature", "owner", "context"])
def test_forged_or_obsolete_evidence_fails_closed(tmp_path, mutation):
    private = Ed25519PrivateKey.generate(); store = tmp_path / "evidence"
    changes = {"owner_id": "candidate"} if mutation == "owner" else (
        {"admission_context_id": D4} if mutation == "context" else {})
    provision(store, private, **changes)
    if mutation == "signature":
        path = store / "proof.json"; value = json.loads(path.read_bytes())
        value["signature"] = base64.b64encode(b"x" * 64).decode(); path.write_bytes(canonical(value)); path.chmod(0o600)
    authority = PreprovisionedSemanticAuthority(policy(), store,
                                                 {"oracle-key": private.public_key()})
    if mutation == "context":
        with pytest.raises(SemanticReviewError, match="evidence_obsolete"):
            authority.inputs_for(request())
    else:
        with pytest.raises(SemanticReviewError, match="evidence_forged"):
            authority.inputs_for(request())


def test_risk_facts_are_core_derived_and_danger_increases_risk():
    safe = derive_review_risk_facts(request())
    risky = derive_review_risk_facts(request(b"import socket\nimport subprocess\neval('x')"))
    assert risky.risk_score > safe.risk_score


def test_missing_evidence_store_is_review_unavailable(tmp_path):
    key = Ed25519PrivateKey.generate().public_key()
    authority = PreprovisionedSemanticAuthority(policy(), tmp_path / "missing", {"k": key})
    with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
        authority.inputs_for(request())


@pytest.mark.skipif(not hasattr(__import__("os"), "O_NOFOLLOW"), reason="requires nofollow")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "oversize", "duplicate"])
def test_evidence_file_shape_and_encoding_fail_closed(tmp_path, kind):
    private = Ed25519PrivateKey.generate(); store = tmp_path / "evidence"
    provision(store, private)
    path = store / "proof.json"
    if kind == "symlink":
        target = tmp_path / "target"; path.rename(target); path.symlink_to(target)
    elif kind == "hardlink":
        (tmp_path / "alias").hardlink_to(path)
    elif kind == "oversize":
        path.write_bytes(b"x" * (64 * 1024 + 1))
    else:
        path.write_bytes(b'{"schema_version":1,"schema_version":1}')
    authority = PreprovisionedSemanticAuthority(policy(), store,
                                                 {"oracle-key": private.public_key()})
    with pytest.raises(SemanticReviewError, match="evidence_forged"):
        authority.inputs_for(request())


def test_authority_config_requires_exact_verifier_state_and_rejects_revoked(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    key = tmp_path / "semantic.pub"; key.write_bytes(public); key.chmod(0o600)
    (tmp_path / "evidence").mkdir(mode=0o700)
    base = {
        "evidence_dir": "evidence",
        "versions": {kind.value: ["v1"] for kind in IndependentEvidenceKind},
        "owners": {kind.value: ["oracle"] for kind in IndependentEvidenceKind},
    }
    with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
        load_semantic_authority({**base, "verifiers": {"k": "semantic.pub"}}, tmp_path)
    with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
        load_semantic_authority({**base, "verifiers": {
            "k": {"path": "semantic.pub", "status": "revoked"}
        }}, tmp_path)


@pytest.mark.skipif(not hasattr(__import__("os"), "O_NOFOLLOW"), reason="requires nofollow")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "unknown"])
def test_verifier_key_is_regular_unique_and_config_is_closed(tmp_path, kind):
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "semantic.pub"; key.write_bytes(private.public_key().public_bytes_raw()); key.chmod(0o600)
    (tmp_path / "evidence").mkdir(mode=0o700)
    if kind == "symlink":
        target = tmp_path / "target.pub"; key.rename(target); key.symlink_to(target)
    elif kind == "hardlink":
        (tmp_path / "alias.pub").hardlink_to(key)
    spec = {
        "evidence_dir": "evidence",
        "verifiers": {"k": {"path": "semantic.pub", "status": "active"}},
        "versions": {item.value: ["v1"] for item in IndependentEvidenceKind},
        "owners": {item.value: ["oracle"] for item in IndependentEvidenceKind},
    }
    if kind == "unknown":
        spec["verifiers"]["k"]["extra"] = True
    with pytest.raises(SemanticReviewError, match="semantic_review_unavailable"):
        load_semantic_authority(spec, tmp_path)


def test_evidence_mutation_during_descriptor_read_fails_closed(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate(); store = tmp_path / "evidence"
    provision(store, private)
    original = os.read
    changed = False

    def racing_read(fd, size):
        nonlocal changed
        raw = original(fd, size)
        if raw and not changed:
            changed = True
            with (store / "proof.json").open("ab") as stream:
                stream.write(b" ")
        return raw

    monkeypatch.setattr("executor_birth_semantic_authority.os.read", racing_read)
    authority = PreprovisionedSemanticAuthority(policy(), store,
                                                 {"oracle-key": private.public_key()})
    with pytest.raises(SemanticReviewError, match="evidence_forged"):
        authority.inputs_for(request())
