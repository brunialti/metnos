"""Real same-root journal lifecycle; authenticated coordinator proof is a port.

The proof fixtures are nominal coordinator records, not end-to-end signed
deployments. Preparation, publication, secure filesystem and recovery are real.
"""
from dataclasses import replace
import hashlib
import os

import pytest

from executor_birth_distribution_manifest import _verified_distribution_for_test
from executor_birth_ownership_coordinator import (
    SuccessorClaimV1, _install_transaction_id_v1, _successor_claim_id_v1,
)
from executor_birth_ownership_preflight import _sealed_build_identity_for_test
from executor_birth_prepared_set import load_authority_set_v1
from install import birth_authority_provisioner as provisioner
from tests.portable.rm0008_2b import support
from tests.portable.test_birth_authority_provisioning_v2 import D, _claim, _transition_inputs
from tests.portable.test_executor_birth_ownership_coordinator_v2 import record_v2

pytestmark = pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)


def _digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _load_set(prepared):
    layout = provisioner._open_installer_layout_v1()
    with layout.birth_session as session:
        with session.global_lock(exclusive=False, create=False):
            return load_authority_set_v1(session, prepared.target_set_id)


def _completed(claim, distribution, previous, prepared):
    template = record_v2(6)
    install = {
        **template.install_transaction_value(),
        **{key: getattr(claim, key) for key in (
            "request_id", "source_id", "closed_build_id", "release_sequence", "previous_head_id",
        )},
        "successor_claim_id": claim.claim_id,
    }
    return replace(
        template, **{key: value for key, value in install.items() if key != "schema_version"},
        install_transaction_id=_install_transaction_id_v1(install),
        distribution_payload_hash=_digest(distribution.encoded),
        distribution_signature_hash=_digest(distribution.signature),
        provisioning_transaction_id=prepared.transaction_id,
        previous_set_id=previous.set_id,
        target_set_id=prepared.target_set_id,
        target_admission_context_id=prepared.target_admission_context_id,
        target_context_epoch=prepared.target_context_epoch,
        target_context_material_sha256=prepared.target_context_material_sha256,
        target_set_json_sha256=prepared.target_set_json_sha256,
    )


def _successor(distribution, completed):
    sequence = distribution.release_sequence + 1
    encoded = f"distribution-{sequence}".encode()
    identity = _sealed_build_identity_for_test(_digest(encoded), D("d"), "closed-v1")
    successor = replace(
        _verified_distribution_for_test(
            identity, previous_closed_build_id=distribution.identity.closed_build_id,
            release_sequence=sequence, encoded=encoded, signature=b"t" * 64,
        ), installation_root=distribution.installation_root, files=distribution.files,
    )
    fields = dict(schema_version=1, previous_head_id=completed.head_id,
                  release_sequence=sequence, request_id=_digest(b"request" + encoded),
                  source_id=D("3"), closed_build_id=identity.closed_build_id)
    claim = SuccessorClaimV1(
        claim_id=_successor_claim_id_v1(fields),
        **{key: value for key, value in fields.items() if key != "schema_version"},
    )
    return claim, successor


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_ino, path.stat().st_mode, path.stat().st_uid, path.stat().st_gid,
            hashlib.sha256(path.read_bytes()).digest() if path.is_file() else None,
        )
        for path in (root, *sorted(root.rglob("*")))
    }


@pytest.fixture
def published(tmp_path, monkeypatch):
    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    claim = _claim()
    prepared = provisioner._prepare_transition_authority_set_v2(claim, distribution, previous)
    provisioner._publish_prepared_authority_set_v2(prepared)
    completed = _completed(claim, distribution, previous, prepared)
    successor_claim, successor = _successor(distribution, completed)
    return base / "birth", _load_set(prepared), completed, successor_claim, successor


def test_two_successive_updates_archive_only_completed_journals_and_replay(published, monkeypatch):
    root, previous, completed, claim, distribution = published
    marker = (root / "prepared-v1.json").read_bytes()
    for index in range(2):
        monkeypatch.setattr(
            provisioner, "_provisioner_build_id_v2",
            lambda index=index: f"birth-provisioner-v2-successor-{index}",
        )
        source = root / (provisioner.TRANSACTION_PREFIX_V2 + completed.provisioning_transaction_id)
        archive = root / (provisioner.COMPLETED_TRANSACTION_PREFIX_V2 + completed.provisioning_transaction_id)
        before = _snapshot(source)
        prepared = provisioner._prepare_transition_authority_set_v2(
            claim, distribution, previous, completed_predecessor=completed,
        )
        assert not source.exists()
        assert _snapshot(archive) == before
        assert (archive / provisioner.MATERIAL_PLAN_BASENAME_V2).stat().st_mode & 0o777 == 0o600
        assert len(tuple(root.glob(provisioner.TRANSACTION_PREFIX_V2 + "*"))) == 1
        assert provisioner._prepare_transition_authority_set_v2(
            claim, distribution, previous, completed_predecessor=completed,
        ) == prepared
        provisioner._publish_prepared_authority_set_v2(prepared)
        # A fresh secure session and normal set reader tolerate inert archives.
        current = _load_set(prepared)
        assert current.set_id == prepared.target_set_id
        assert current.provisioner_build_id != previous.provisioner_build_id
        assert _snapshot(archive) == before
        completed = _completed(claim, distribution, previous, prepared)
        previous = current
        claim, distribution = _successor(distribution, completed)
    assert len(tuple(root.glob(provisioner.COMPLETED_TRANSACTION_PREFIX_V2 + "*"))) == 2
    assert (root / "prepared-v1.json").read_bytes() == marker


@pytest.mark.parametrize("field,value", (
    ("phase", 5), ("head_id", D("f")), ("closed_build_id", D("f")),
    ("provisioning_transaction_id", "f" * 32), ("target_set_id", "f" * 64),
    ("target_admission_context_id", D("f")), ("target_context_epoch", D("f")),
    ("target_context_material_sha256", "f" * 64), ("target_set_json_sha256", "f" * 64),
    ("distribution_payload_hash", D("f")), ("distribution_signature_hash", D("f")),
    ("previous_set_id", "f" * 64), ("request_id", D("f")),
))
def test_archive_requires_the_exact_completed_predecessor(published, field, value):
    root, previous, completed, claim, distribution = published
    changes = {field: value}
    if field == "phase":
        changes = dict(sequence=5, state=record_v2(5).state, preflight_attestation_hash=None)
    elif field == "head_id":
        changes["verified_chain_head_id"] = value
    if field in {"closed_build_id", "request_id"}:
        changes["install_transaction_id"] = _install_transaction_id_v1({
            **completed.install_transaction_value(), field: value,
        })
    altered = replace(completed, **changes)
    before = _snapshot(root)
    with pytest.raises(provisioner.BirthProvisioningError):
        provisioner._prepare_transition_authority_set_v2(
            claim, distribution, previous, completed_predecessor=altered,
        )
    assert _snapshot(root) == before


@pytest.mark.parametrize("obstacle", (
    "duplicate", "other_v2", "v1", "pending", "staged", "private_mode",
    "material_bytes", "checkpoint", "published_set",
    "checkpoint_context_digest", "checkpoint_set_digest",
))
def test_ambiguous_or_incomplete_journal_is_never_archived(published, obstacle):
    root, previous, completed, claim, distribution = published
    source = root / (provisioner.TRANSACTION_PREFIX_V2 + completed.provisioning_transaction_id)
    if obstacle == "pending":
        name = provisioner._checkpoint_pending_name_v1(2, completed.provisioning_transaction_id)
        (source / provisioner.CHECKPOINTS_BASENAME_V1 / name).write_bytes(b"pending")
    elif obstacle == "staged":
        (source / provisioner.AUTHORITY_SET_BASENAME_V1).mkdir()
    elif obstacle == "private_mode":
        (source / provisioner.MATERIAL_PLAN_BASENAME_V2).chmod(0o644)
    elif obstacle == "material_bytes":
        (source / provisioner.MATERIAL_PLAN_BASENAME_V2).write_bytes(b"{}")
    elif obstacle == "checkpoint":
        checkpoint = sorted((source / provisioner.CHECKPOINTS_BASENAME_V1).glob("*.json"))[-1]
        checkpoint.write_bytes(b"{}")
    elif obstacle in {"checkpoint_context_digest", "checkpoint_set_digest"}:
        checkpoint = sorted((source / provisioner.CHECKPOINTS_BASENAME_V1).glob("*.json"))[-1]
        record = provisioner.decode_checkpoint_v1(checkpoint.read_bytes())
        field = "context_material_sha256" if obstacle == "checkpoint_context_digest" else "set_json_sha256"
        altered = replace(record, digests={**record.digests, field: "f" * 64})
        checkpoint.write_bytes(altered.encode())
    elif obstacle == "published_set":
        (root / provisioner.AUTHORITY_SETS_BASENAME_V1 / previous.set_id / "set.json").write_bytes(b"{}")
    else:
        prefix = {
            "duplicate": provisioner.COMPLETED_TRANSACTION_PREFIX_V2,
            "other_v2": provisioner.TRANSACTION_PREFIX_V2,
            "v1": provisioner.TRANSACTION_PREFIX_V1,
        }[obstacle]
        nonce = completed.provisioning_transaction_id if obstacle == "duplicate" else "f" * 32
        (root / (prefix + nonce)).mkdir()
    before = _snapshot(root)
    with pytest.raises(provisioner.BirthProvisioningError):
        provisioner._prepare_transition_authority_set_v2(
            claim, distribution, previous, completed_predecessor=completed,
        )
    assert _snapshot(root) == before


@pytest.mark.parametrize("field,value", (("closed_build_id", D("f")), ("release_sequence", 3)))
def test_candidate_mismatch_refuses_before_archiving(published, field, value):
    root, previous, completed, claim, distribution = published
    fields = {**claim.as_value(include_id=False), field: value}
    altered = replace(claim, **{field: value}, claim_id=_successor_claim_id_v1(fields))
    before = _snapshot(root)
    with pytest.raises(provisioner.BirthProvisioningError):
        provisioner._prepare_transition_authority_set_v2(
            altered, distribution, previous, completed_predecessor=completed,
        )
    assert _snapshot(root) == before


def test_preexisting_archive_is_inert_not_authority_for_replay(published, monkeypatch):
    from executor_birth_secure_fs import _SecureRootSession

    root, previous, completed, claim, distribution = published
    source = root / (provisioner.TRANSACTION_PREFIX_V2 + completed.provisioning_transaction_id)
    archive = root / (provisioner.COMPLETED_TRANSACTION_PREFIX_V2 + completed.provisioning_transaction_id)
    before = _snapshot(source)
    original = _SecureRootSession.rename_no_replace

    def crash_after_rename(session, source_components, target_components, **kwargs):
        result = original(session, source_components, target_components, **kwargs)
        if target_components == (archive.name,):
            raise RuntimeError("simulated interruption after atomic archive rename")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(_SecureRootSession, "rename_no_replace", crash_after_rename)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            provisioner._prepare_transition_authority_set_v2(
                claim, distribution, previous, completed_predecessor=completed,
            )
    assert not source.exists()
    assert _snapshot(archive) == before
    prepared = provisioner._prepare_transition_authority_set_v2(
        claim, distribution, previous, completed_predecessor=completed,
    )
    assert prepared.target_set_id != previous.set_id
    assert _snapshot(archive) == before
    # Removing the authenticated predecessor input does not make an archive
    # selectable; the active successor can replay only its own exact header.
    assert provisioner._prepare_transition_authority_set_v2(claim, distribution, previous) == prepared
    with pytest.raises(provisioner.BirthProvisioningError):
        provisioner._prepare_transition_authority_set_v2(_claim(), distribution, previous)
    assert _snapshot(archive) == before
