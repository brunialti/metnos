"""The commit link crosses the real store primitive on an isolated root.

Section 10.5 asks for the real thing: the key the provisioner installed is the
key that publishes, the receipt is issued by the prepared Admission identity,
and the result is authenticated with the productive verifiers.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_commit_publisher as link
from executor_birth_commit_publisher import BirthCommitFactsV1

from . import support

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason=support.WINDOWS_BLOCKER_V1
)

DIGEST = "sha256:" + "3" * 64


def _loaded(active_id: str, private: Ed25519PrivateKey):
    return type("Loaded", (), {
        "active_key_id": active_id,
        "active_private_key": private,
        "verifier_keys": {active_id: private.public_key()},
    })()


def _facts(ref, snapshot, predecessor: str, epoch: str) -> BirthCommitFactsV1:
    from executor_birth_receipts import ApprovedLifecycle, RevisionClass

    return BirthCommitFactsV1(
        manifest_ref=ref,
        snapshot=snapshot,
        request_id="sha256:" + "8" * 64,
        policy_version="birth-policy-v1",
        contract_id=ref.contract_id,
        candidate_id=DIGEST,
        semantic_core_id=DIGEST,
        admission_context_id=DIGEST,
        expected_generation_id=predecessor,
        predecessor_id=predecessor,
        predecessor_snapshot_id=None,
        revision_facts_id=None,
        observed_context_epoch=epoch,
        producer_receipt_hash=DIGEST,
        revision_class=RevisionClass.CODE_REVISION,
        approved_lifecycle=ApprovedLifecycle.ACTIVE,
        check_results={},
        semantic_review_hash=None,
        approval_hash=None,
        issued_at="2026-08-27T12:00:00Z",
    )


def _prepared_author(monkeypatch, base: Path):
    """The author identity the provisioner staged, read back from its store."""
    return support.load_installed_author_store(monkeypatch, base)


def test_the_prepared_identities_publish_a_real_generation(
    tmp_path: Path, monkeypatch,
):
    from contract_store import publish_signed_source
    from executor_birth_receipts import verify_admission_receipt

    author = Ed25519PrivateKey.generate()
    base = support.make_config(tmp_path, author=author, operator=True)
    support.provision(monkeypatch, base)
    loaded = _prepared_author(monkeypatch, base)

    work = tmp_path / "work"
    work.mkdir()
    ref, _private, _trusted = support.create_contract_source(work)
    # The contract is signed by the very key the provisioner installed.
    support.write(
        ref.manifest_dir / "manifest.toml.sig",
        __import__("sign").sign_manifest_bytes(
            (ref.manifest_dir / "manifest.toml").read_bytes(),
            private_key=loaded.active_private_key,
        ),
        0o644,
    )
    trusted = tuple(sorted(loaded.verifier_keys.items()))
    store = work / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store,
    )
    snapshot = support.birth_candidate_snapshot(ref, work)

    admission = Ed25519PrivateKey.generate()
    epoch = "sha256:" + "5" * 64
    bundle = link._build_prepared_bundle_v1(
        author=loaded,
        admission=_loaded("admission-key", admission),
        set_id="a" * 64,
        prepared_admission_context_id=DIGEST,
        prepared_context_epoch=epoch,
        store_root=store,
    )
    outcome = bundle.publisher.commit(
        _facts(ref, snapshot, initial.current_generation_id, epoch)
    )
    publication = outcome.publication
    assert outcome.admission_receipt, "the issued receipt travels with it"

    assert publication.current_generation_id != initial.current_generation_id
    receipts = tuple(store.rglob("admission-receipts/*.json"))
    assert len(receipts) == 1
    receipt = verify_admission_receipt(
        receipts[0].read_bytes(), verifier_keys=bundle.view.admission_public_keys,
    )
    assert receipt.generation_id == publication.current_generation_id
    assert receipt.contract_id == ref.contract_id.value
    assert receipt.admission_context_id == DIGEST


def test_a_receipt_of_another_identity_is_not_authenticated(
    tmp_path: Path, monkeypatch,
):
    """The verifier of the prepared set accepts only its own Admission key."""
    from contract_store import publish_signed_source
    from executor_birth_receipts import verify_admission_receipt

    author = Ed25519PrivateKey.generate()
    base = support.make_config(tmp_path, author=author, operator=True)
    support.provision(monkeypatch, base)
    loaded = _prepared_author(monkeypatch, base)

    work = tmp_path / "work"
    work.mkdir()
    ref, _private, _trusted = support.create_contract_source(work)
    support.write(
        ref.manifest_dir / "manifest.toml.sig",
        __import__("sign").sign_manifest_bytes(
            (ref.manifest_dir / "manifest.toml").read_bytes(),
            private_key=loaded.active_private_key,
        ),
        0o644,
    )
    store = work / "store"
    initial = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=tuple(sorted(loaded.verifier_keys.items())),
        store_root=store,
    )
    snapshot = support.birth_candidate_snapshot(ref, work)
    epoch = "sha256:" + "5" * 64
    bundle = link._build_prepared_bundle_v1(
        author=loaded,
        admission=_loaded("admission-key", Ed25519PrivateKey.generate()),
        set_id="a" * 64,
        prepared_admission_context_id=DIGEST,
        prepared_context_epoch=epoch,
        store_root=store,
    )
    bundle.publisher.commit(
        _facts(ref, snapshot, initial.current_generation_id, epoch)
    )
    receipts = tuple(store.rglob("admission-receipts/*.json"))
    stranger = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(Exception):
        verify_admission_receipt(
            receipts[0].read_bytes(),
            verifier_keys={"admission-key": stranger},
        )
