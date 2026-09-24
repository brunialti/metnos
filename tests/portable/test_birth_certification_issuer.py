"""What the certificate binds, what it refuses, and who verifies it."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_activation_mode as mode
import executor_birth_authority_files as files
import executor_birth_lifecycle as lifecycle
import install.birth_certification_issuer as issuer
from executor_birth_account_identity import PosixAccountRecordV1, PosixAccountSnapshotV1
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

    snapshot = PosixAccountSnapshotV1(
        PosixAccountRecordV1("metnos", 995, 985, str(tmp_path / "service-home"),
                             "/usr/sbin/nologin"), (985,))
    monkeypatch.setattr(cutover, "resolve_posix_account_snapshot_v1",
                        lambda name: snapshot)
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
    assert issuer._completed_migration_id() == MIGRATION
    assert seen["path"] == tmp_path / "birth" / "executor_epochs.sqlite"
    assert seen["identity"] == MIGRATION


@pytest.fixture
def qualification_inputs(monkeypatch):
    from install.birth_certification_evidence import EvidenceFrontierV1, ProfileBindingsV1
    from install.birth_certification_qualification import evidence_scope_id_v1

    history = SimpleNamespace(
        required_head_id=HEAD, issues=(),
        technical_acts=tuple(SimpleNamespace(encoded_hash=digest(str(i))) for i in range(5)),
        technical_issuers=("builtin_contract_generator", "stack_reconcile"),
    )
    frontier = EvidenceFrontierV1(
        digest("evidence"), 12, evidence_scope_id_v1(()), (), digest("profile"),
        ("cycle-a", "cycle-b"), None,
        ProfileBindingsV1(INSTALLATION, HEAD, digest("source"),
                          digest("catalog"), digest("harness")),
    )
    monkeypatch.setattr(issuer, "observe_history_v1", lambda **_k: history)
    return history, frontier


def test_service_derivation_uses_authenticated_history_and_decodes_archives(
    monkeypatch, qualification_inputs,
):
    from install.birth_certification_qualification import derive_qualification_v1

    history, frontier = qualification_inputs
    observed = []
    monkeypatch.setattr(issuer, "observe_history_v1", lambda **kwargs: (
        observed.append(kwargs["public_sources"]) or history))
    result = issuer._derive_as_service_v1({"qualification": {
        "frontier": asdict(frontier),
        "public_sources": [["archive/source.py", base64.b64encode(b"public bytes").decode()]],
    }})
    assert result == asdict(derive_qualification_v1(history, frontier))
    assert observed == [(("archive/source.py", b"public bytes"),)]


def test_qualification_uses_the_reviewed_service_paths(monkeypatch, qualification_inputs):
    import install.birth_lifecycle_migration as cutover
    from install.birth_certification_qualification import derive_qualification_v1

    history, frontier = qualification_inputs
    handoff = {"environment": {"METNOS_USER_STATE": "/reviewed-service-state"}}
    monkeypatch.setattr(cutover, "read_handoff_v1", lambda: handoff)

    def child(operation, reviewed, *, qualification):
        assert operation == "qualify" and reviewed is handoff
        return json.loads(json.dumps(issuer._derive_as_service_v1({
            "qualification": qualification,
        })))

    monkeypatch.setattr(cutover, "_in_service_child", child)
    assert issuer._service_qualification_v1(frontier, ()) == derive_qualification_v1(history, frontier)


def test_service_refusal_never_becomes_a_qualification(monkeypatch, qualification_inputs):
    import install.birth_lifecycle_migration as cutover

    _history, frontier = qualification_inputs
    monkeypatch.setattr(cutover, "read_handoff_v1", lambda: {})

    def refuse(*_a, **_k):
        raise cutover.LifecycleCutoverError("technical_admissions_insufficient", "0")

    monkeypatch.setattr(cutover, "_in_service_child", refuse)
    with pytest.raises(issuer.CertificationIssueError) as raised:
        issuer._service_qualification_v1(frontier, ())
    assert (raised.value.code, raised.value.detail) == ("technical_admissions_insufficient", "0")


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("changed", [None, "evidence", "migration", "installation", "head"])
def test_issuance_rereads_every_authority_before_signing(
    monkeypatch, qualification_inputs, apply, changed,
):
    import install.birth_certification_evidence as evidence
    import install.birth_certification_qualification as qualification
    import executor_birth_certification_authority as authority

    history, frontier = qualification_inputs
    derived = qualification.derive_qualification_v1(history, frontier)
    if changed == "head":
        derived = replace(derived, required_head_id=digest("other-head"))
    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    migrations = iter([MIGRATION, digest("other") if changed == "migration" else MIGRATION])
    installations = iter([(INSTALLATION, HEAD, BUILD), (
        INSTALLATION, HEAD, digest("other") if changed == "installation" else BUILD)])
    monkeypatch.setattr(issuer, "_completed_migration_id", lambda: next(migrations))
    monkeypatch.setattr(issuer, "_installation_frontier_v1", lambda: next(installations))
    monkeypatch.setattr(issuer, "_service_qualification_v1", lambda *_a: derived)
    frontiers = iter([frontier, replace(frontier, head=digest("other"))
                      if changed == "evidence" else frontier])

    @contextmanager
    def ledger():
        yield SimpleNamespace(frontier=next(frontiers))

    monkeypatch.setattr(evidence, "administrative_evidence_v1", ledger)
    signed = []
    monkeypatch.setattr(authority, "load_certification_public_key_v1",
                        lambda: SimpleNamespace(key_id="dedicated-key"))
    monkeypatch.setattr(issuer, "_sign_certificate_v1",
                        lambda payload: signed.append(payload) or b"signed")
    monkeypatch.setattr(issuer, "_install_certificate_v1", lambda *_a: Path("active.json"))
    if changed:
        with pytest.raises(issuer.CertificationIssueError) as refused:
            issuer.issue_certificate_v1(apply=apply)
        assert refused.value.code == "certification_frontier_changed"
        assert signed == []
    else:
        result = issuer.issue_certificate_v1(apply=apply)
        assert result["technical_admissions"] == 5
        assert len(signed) == int(apply)
        assert result["certificate"] == ("active.json" if apply else None)


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("changed", ["installation_id", "head_id", "missing"])
def test_a_frozen_profile_cannot_move_to_another_installation_or_head(
    monkeypatch, qualification_inputs, apply, changed,
):
    import install.birth_certification_evidence as evidence

    _history, frontier = qualification_inputs
    bindings = (None if changed == "missing" else
                replace(frontier.profile_bindings, **{changed: digest("different")}))
    frontier = replace(frontier, profile_bindings=bindings)
    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    monkeypatch.setattr(issuer, "_completed_migration_id", lambda: MIGRATION)
    monkeypatch.setattr(issuer, "_installation_frontier_v1",
                        lambda: (INSTALLATION, HEAD, BUILD))

    @contextmanager
    def ledger():
        yield SimpleNamespace(frontier=frontier)

    monkeypatch.setattr(evidence, "administrative_evidence_v1", ledger)
    monkeypatch.setattr(issuer, "_service_qualification_v1",
                        lambda *_a: pytest.fail("mismatched profile reached derivation"))
    monkeypatch.setattr(issuer, "_sign_certificate_v1",
                        lambda *_a: pytest.fail("mismatched profile reached signing"))
    with pytest.raises(issuer.CertificationIssueError) as refused:
        issuer.issue_certificate_v1(apply=apply)
    assert refused.value.code == ("profile_bindings_absent" if changed == "missing"
                                  else "certification_profile_mismatch")


@pytest.mark.parametrize("command", ["derive", "issue"])
def test_neither_command_runs_without_administrative_privilege(monkeypatch, command):
    monkeypatch.setattr(issuer, "_managed_authority_platform_supported_v1", lambda: True)
    monkeypatch.setattr(issuer.os, "geteuid", lambda: 1000, raising=False)
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


# --- what the runtime reader gives back ---------------------------------------

@pytest.fixture
def published(monkeypatch, tmp_path):
    """Publish into a temporary directory, with the custody helpers stubbed."""
    from contextlib import contextmanager

    directory = tmp_path / "certification-v1"
    directory.mkdir()
    monkeypatch.setattr(issuer, "ACTIVATION_DIRECTORY_V1", directory)
    monkeypatch.setattr(issuer, "_root_owned_chain", lambda _root: None)
    monkeypatch.setattr(issuer, "_directory_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(issuer, "_sync_directory", lambda _path: None)
    monkeypatch.setattr(issuer, "_read_regular",
                        lambda path, **kw: Path(path).read_bytes())

    @contextmanager
    def lock(_root, **_kw):
        yield

    monkeypatch.setattr(issuer, "_provisioning_lock", lock)
    return directory


def _certificate(**overrides):
    payload = issuer.build_certificate_v1(
        qualification_id=QUALIFICATION, migration_id=MIGRATION,
        installation_id=INSTALLATION, head_id=HEAD, closed_build_id=BUILD,
        key_id="key-1")
    return payload, SimpleNamespace(**{**payload, **overrides})


def test_a_published_certificate_is_the_one_that_was_signed(monkeypatch, published):
    payload, accepted = _certificate()
    monkeypatch.setattr(lifecycle, "load_f5_activation",
                        lambda: SimpleNamespace(certificate=accepted))
    path = issuer._install_certificate_v1(encode(payload), payload)
    assert path == published / issuer.CERTIFICATE_BASENAME_V1
    assert json.loads(path.read_bytes()) == payload


@pytest.mark.parametrize("field", [
    "installation_id", "qualification_id", "head_id", "closed_build_id",
    "migration_id", "policy_id", "key_id",
])
def test_a_reread_that_names_another_binding_is_not_a_published_issue(
        monkeypatch, published, field):
    """A document that parses but binds something else is a failed issue."""
    payload, accepted = _certificate(**{field: digest("something else")})
    monkeypatch.setattr(lifecycle, "load_f5_activation",
                        lambda: SimpleNamespace(certificate=accepted))
    with pytest.raises(issuer.CertificationIssueError) as refused:
        issuer._install_certificate_v1(encode(payload), payload)
    assert refused.value.code == "certification_document_unsafe"
    assert refused.value.detail == field


def test_a_reader_that_refuses_the_document_is_reported_as_such(
        monkeypatch, published):
    payload, _accepted = _certificate()

    def refuse():
        raise lifecycle.LifecycleError("f5_activation_invalid", "signature")

    monkeypatch.setattr(lifecycle, "load_f5_activation", refuse)
    with pytest.raises(issuer.CertificationIssueError) as refused:
        issuer._install_certificate_v1(encode(payload), payload)
    assert refused.value.code == "certification_document_refused"
