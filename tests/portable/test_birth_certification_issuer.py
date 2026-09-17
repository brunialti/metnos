"""What the certificate binds, what it refuses, and who verifies it."""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_activation_mode as mode
import executor_birth_authority_files as files
import executor_birth_lifecycle as lifecycle
import install.birth_certification_issuer as issuer
from executor_birth_canonical import encode_canonical_ascii_v1 as encode
from executor_birth_certification_authority import CertificationPublicKeyV1
from executor_birth_keystore import birth_key_id


native = pytest.mark.skipif(not sys.platform.startswith("linux"),
                            reason="the managed certification custody is Linux-only")


def digest(label):
    import hashlib
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


QUALIFICATION = digest("qualification")
MIGRATION = digest("migration")
INSTALLATION = digest("installation")
HEAD = digest("head")
BUILD = digest("build")


# --- the frozen payload ------------------------------------------------------

def test_the_payload_is_exactly_the_frozen_schema():
    payload = issuer.build_certificate_v1(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
        key_id="key-1")
    assert set(payload) == {
        "schema_version", "purpose", "installation_id", "qualification_id",
        "head_id", "closed_build_id", "migration_id", "policy_id", "key_id"}
    assert payload["purpose"] == "f5_activation_v1"
    assert payload["policy_id"] == lifecycle.ACTIVATION_POLICY
    assert "signature" not in payload


def _bindings(**overrides):
    return dict(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
        key_id="key-1", **overrides)


@pytest.mark.parametrize("field", [
    "qualification_id", "migration_id", "installation_id", "head_id",
    "closed_build_id"])
@pytest.mark.parametrize("value", ["not-a-digest", "", None, 1])
def test_an_invented_identity_never_becomes_a_payload(field, value):
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer.build_certificate_v1(**{**_bindings(), field: value})
    assert raised.value.code == "certification_binding_invalid"
    assert raised.value.detail == field


@pytest.mark.parametrize("value", ["", None])
def test_a_certificate_without_a_signing_identity_is_refused(value):
    """The key id is a name, not a digest; only its absence is an error."""
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer.build_certificate_v1(**{**_bindings(), "key_id": value})
    assert raised.value.detail == "key_id"


def test_the_payload_carries_no_threshold_number():
    """The counts are evidence, not claims a reader could be asked to trust."""
    payload = issuer.build_certificate_v1(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
        key_id="key-1")
    text = json.dumps(payload)
    for word in ("admission", "producer", "cycle", "count"):
        assert word not in text


# --- order: the migration comes first ---------------------------------------

def test_an_unmigrated_installation_cannot_be_certified(monkeypatch):
    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    monkeypatch.setattr(mode, "read_birth_activation_state",
                        lambda: mode.BirthActivationState(
                            mode.BirthStateOwner.LEGACY, None, None, None))
    monkeypatch.setattr(issuer, "observe_history_v1",
                        lambda **_k: pytest.fail("history read before the migration"))
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer.issue_certificate_v1(apply=True)
    assert raised.value.code == "certification_before_migration"


def _migrated(monkeypatch, tmp_path):
    import install.birth_lifecycle_migration as cutover

    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    monkeypatch.setattr(mode, "read_birth_activation_state",
                        lambda: mode.BirthActivationState(
                            mode.BirthStateOwner.EPOCH, MIGRATION, None, None))
    monkeypatch.setattr(cutover, "read_handoff_v1",
                        lambda: {"environment": {"METNOS_USER_STATE": str(tmp_path)}})


def test_an_unverifiable_migration_cannot_be_certified(monkeypatch, tmp_path):
    import executor_birth_lifecycle_migration as migration

    _migrated(monkeypatch, tmp_path)

    def refuse(*_a, **_k):
        raise migration.LifecycleMigrationError(
            "migration_record_incomplete", "pending_disposition=1")

    monkeypatch.setattr(migration, "verify_migration_v1", refuse)
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer.issue_certificate_v1(apply=True)
    assert raised.value.code == "migration_record_incomplete"


def test_the_issuer_rereads_the_store_the_migration_used(monkeypatch, tmp_path):
    """Root has its own state directory; the service's is the one that counts."""
    import executor_birth_lifecycle_migration as migration

    _migrated(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(migration, "verify_migration_v1",
                        lambda migration_id, *, epoch_db_path: seen.update(
                            path=epoch_db_path, identity=migration_id))
    monkeypatch.setattr(issuer, "_installation_frontier_v1",
                        lambda: (INSTALLATION, HEAD, BUILD))
    monkeypatch.setattr(issuer, "observe_history_v1",
                        lambda **_k: (_ for _ in ()).throw(
                            issuer.CertificationIssueError("stop", "after the reread")))
    with pytest.raises(issuer.CertificationIssueError):
        issuer.issue_certificate_v1(apply=False)
    assert seen["path"] == tmp_path / "birth" / "executor_epochs.sqlite"
    assert seen["identity"] == MIGRATION


@pytest.mark.parametrize("command", ["derive", "issue"])
def test_neither_command_runs_without_administrative_privilege(monkeypatch, command):
    monkeypatch.setattr(issuer, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(issuer.os, "geteuid", lambda: 1000)
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer.issue_certificate_v1(apply=command == "issue")
    assert raised.value.code == "certification_root_required"


def test_the_command_line_accepts_only_its_two_stages():
    assert issuer.main([]) == 64
    assert issuer.main(["issue", "--force"]) == 64


# --- signing, and the reader that must accept the result ---------------------

@pytest.fixture
def authority(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("managed key custody is Linux-only")
    private = Ed25519PrivateKey.generate()
    public = CertificationPublicKeyV1(birth_key_id(private.public_key()),
                                      private.public_key(), "active")
    directory = tmp_path / "certification-authority-v1"
    directory.mkdir(mode=0o755)
    monkeypatch.setattr("executor_birth_certification_authority.DEFAULT_DIRECTORY_V1",
                        directory)
    monkeypatch.setattr(issuer, "_root_owned_chain", lambda path: None)
    monkeypatch.setattr(issuer, "_read_regular",
                        lambda path, **kw: files._read_regular(
                            path, **{**kw, "root_owned": False}))
    monkeypatch.setattr("install.birth_certification_authority_provisioner._verify_pair",
                        lambda directory, **_kw: public)
    (directory / "private.bin").write_bytes(private.private_bytes_raw())
    (directory / "private.bin").chmod(0o600)
    return private, public


@native
def test_the_signature_is_the_one_the_runtime_reader_verifies(authority):
    private, public = authority
    payload = issuer.build_certificate_v1(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
        key_id=public.key_id)
    encoded = issuer._sign_certificate_v1(payload)
    accepted = lifecycle._decode_f5_activation_v1(
        encoded, authority=public, installation_id=INSTALLATION,
        head_id=HEAD, closed_build_id=BUILD).certificate
    assert accepted.qualification_id == QUALIFICATION
    assert accepted.migration_id == MIGRATION
    assert accepted.key_id == public.key_id


@native
def test_a_certificate_for_another_installation_is_refused_by_that_reader(authority):
    _private, public = authority
    encoded = issuer._sign_certificate_v1(issuer.build_certificate_v1(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=digest("another installation"), head_id=HEAD,
        closed_build_id=BUILD, key_id=public.key_id))
    with pytest.raises(lifecycle.LifecycleError):
        lifecycle._decode_f5_activation_v1(
            encoded, authority=public, installation_id=INSTALLATION,
            head_id=HEAD, closed_build_id=BUILD)


@native
def test_a_revoked_authority_never_signs(authority, monkeypatch):
    _private, public = authority
    from dataclasses import replace

    monkeypatch.setattr("install.birth_certification_authority_provisioner._verify_pair",
                        lambda directory, **_kw: replace(public, status="revoked"))
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer._sign_certificate_v1(issuer.build_certificate_v1(
            qualification_id=QUALIFICATION, migration_id=MIGRATION,
            installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
            key_id=public.key_id))
    assert raised.value.code == "certification_authority_revoked"


@native
def test_a_payload_naming_another_key_never_signs(authority):
    _private, public = authority
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer._sign_certificate_v1(issuer.build_certificate_v1(
            qualification_id=QUALIFICATION, migration_id=MIGRATION,
            installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
            key_id="somebody-elses-key"))
    assert raised.value.code == "certification_authority_mismatch"
