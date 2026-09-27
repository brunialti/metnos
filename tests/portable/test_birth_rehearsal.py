"""Rehearsal never becomes productive qualification or survives its binding."""
import base64
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_activation_mode as mode
import executor_birth_lifecycle as lifecycle
import executor_birth_rehearsal as rehearsal
import executor_birth_rehearsal_isolation as isolation
import install.birth_certification_issuer as issuer
from executor_birth_canonical import encode_canonical_ascii_v1 as encode
from executor_birth_certification_authority import CertificationPublicKeyV1
from executor_birth_keystore import birth_key_id
from install.birth_certification_evidence import EvidenceFrontierV1, ProfileBindingsV1


DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
NOW = (10000, 20000 * 10**9)


@pytest.fixture
def signed():
    key = Ed25519PrivateKey.generate()
    public = CertificationPublicKeyV1(birth_key_id(key.public_key()), key.public_key(), "active")
    payload = {name: DIGEST for name in rehearsal._BINDINGS}
    payload.update(schema_version=1, purpose="f5_rehearsal_v1", policy_id=rehearsal.REHEARSAL_POLICY_V1,
                   key_id=public.key_id, issued_at=NOW[0], expires_at=NOW[0] + 3600,
                   issued_boot_ns=NOW[1], expires_boot_ns=NOW[1] + 3600 * 10**9)

    def sign(**changes):
        value = {**payload, **changes}
        return encode({**value, "signature": base64.b64encode(
            key.sign(rehearsal.REHEARSAL_DOMAIN_V1 + encode(value))).decode("ascii")})

    return public, sign


def decode(signed, *, document=None, **changes):
    public, sign = signed
    arguments = dict(authority=public, installation_id=DIGEST, head_id=DIGEST,
                     closed_build_id=DIGEST, migration_id=DIGEST, isolation_id=DIGEST, now=NOW)
    return rehearsal._decode_rehearsal_v1(document or sign(), **{**arguments, **changes})


def test_rehearsal_cannot_be_accepted_as_productive_activation(signed):
    result = decode(signed)
    assert isinstance(result, rehearsal.F5Rehearsal)
    assert not isinstance(result, lifecycle.F5Activation)
    with pytest.raises(lifecycle.LifecycleError, match="f5_activation_invalid"):
        lifecycle._decode_f5_activation_v1(signed[1](), authority=signed[0],
                                           installation_id=DIGEST, head_id=DIGEST, closed_build_id=DIGEST)


@pytest.mark.parametrize("field", ["installation_id", "head_id", "closed_build_id", "migration_id", "isolation_id"])
def test_a_copied_or_stale_permit_refuses_every_changed_binding(signed, field):
    with pytest.raises(lifecycle.LifecycleError, match="f5_rehearsal_invalid"):
        decode(signed, **{field: OTHER})


@pytest.mark.parametrize("now", [
    (NOW[0] + 3600, NOW[1]), (NOW[0], NOW[1] + 3600 * 10**9),
    (NOW[0] - 1, NOW[1]), (NOW[0], NOW[1] - 1),
])
def test_either_clock_expires_or_refuses_rollback_independently(signed, now):
    with pytest.raises(lifecycle.LifecycleError):
        decode(signed, now=now)


@pytest.mark.parametrize("changes", [
    {"expires_at": NOW[0] + 3601}, {"expires_boot_ns": NOW[1] + 3601 * 10**9},
    {"issued_at": True}, {"purpose": "f5_activation_v1"}, {"profile_id": "lab"},
    {"extra": True}, {"policy_id": "other"}, {"schema_version": True},
])
def test_even_signed_documents_must_satisfy_the_closed_purpose_and_lifetime(signed, changes):
    with pytest.raises(lifecycle.LifecycleError):
        decode(signed, document=signed[1](**changes))


def test_revocation_and_altered_signature_refuse(signed):
    with pytest.raises(lifecycle.LifecycleError):
        decode(signed, authority=replace(signed[0], status="revoked"))
    changed = signed[1]().replace(DIGEST.encode(), OTHER.encode())
    with pytest.raises(lifecycle.LifecycleError):
        decode(signed, document=changed, installation_id=OTHER, head_id=OTHER,
               closed_build_id=OTHER, migration_id=OTHER, isolation_id=OTHER)


def test_absent_permit_uses_productive_certification_and_present_expired_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearsal, "ACTIVATION_DIRECTORY", tmp_path)
    monkeypatch.setattr(mode, "require_f5_certificate", lambda: "productive")
    assert rehearsal.require_f5_lifecycle_authorization() == "productive"
    (tmp_path / rehearsal.REHEARSAL_BASENAME_V1).write_bytes(b"expired")
    monkeypatch.setattr(rehearsal, "load_f5_rehearsal", lambda: (_ for _ in ()).throw(
        lifecycle.LifecycleError("f5_rehearsal_invalid")))
    with pytest.raises(lifecycle.LifecycleError, match="f5_rehearsal_invalid"):
        rehearsal.require_f5_lifecycle_authorization()


def test_coordinator_rereads_authorization_before_publication(signed, tmp_path, monkeypatch):
    coordinator = lifecycle.LifecycleCoordinator(
        decode(signed), db_path=tmp_path / "epochs.sqlite",
        publish_and_reread=lambda *_: pytest.fail("published after expiry"),
        verify_admission=lambda *_: pytest.fail("verified after expiry"))
    monkeypatch.setattr(rehearsal, "load_f5_rehearsal", lambda: (_ for _ in ()).throw(
        lifecycle.LifecycleError("f5_rehearsal_invalid")))
    with pytest.raises(lifecycle.LifecycleError, match="f5_rehearsal_invalid"):
        coordinator.revise(None, expected_version=1, target=None, name="test", source="test", occurred_at="now")


@pytest.fixture
def issuance(monkeypatch, signed):
    import install.birth_certification_evidence as evidence
    import executor_birth_certification_authority as authority
    frontier = EvidenceFrontierV1(DIGEST, 2, DIGEST, (), DIGEST, (), None,
                                 ProfileBindingsV1(DIGEST, DIGEST, DIGEST, DIGEST, DIGEST))
    state = SimpleNamespace(frontier=frontier)

    @contextmanager
    def administrative():
        yield state

    monkeypatch.setattr(issuer, "_require_root_v1", lambda: None)
    monkeypatch.setattr(isolation, "observe_local_rehearsal_v1", lambda: SimpleNamespace(observation_id=DIGEST))
    monkeypatch.setattr(issuer, "_completed_migration_id", lambda: DIGEST)
    monkeypatch.setattr(issuer, "_installation_frontier_v1", lambda: (DIGEST, DIGEST, DIGEST))
    monkeypatch.setattr(evidence, "administrative_evidence_v1", administrative)
    monkeypatch.setattr(authority, "load_certification_public_key_v1", lambda: signed[0])
    monkeypatch.setattr(rehearsal, "_clock_v1", lambda: NOW)
    monkeypatch.setattr(issuer, "_sign_certificate_v1", lambda *_: pytest.fail("unexpected signing"))
    monkeypatch.setattr(issuer, "_install_rehearsal_v1", lambda *_: pytest.fail("unexpected installation"))
    return state


def test_rehearsal_plan_needs_no_successful_cycles_and_never_signs(issuance):
    plan = issuer.issue_rehearsal_v1(apply=False)
    assert plan["purpose"] == "f5_rehearsal_v1" and plan["permit_id"] is None
    assert plan["profile_id"] == DIGEST and plan["expires_at"] == NOW[0] + 3600
    assert "qualification_id" not in plan


@pytest.mark.parametrize("change", [
    {"profile": None}, {"profile_bindings": None}, {"open_findings": (DIGEST,)},
    {"profile_bindings": ProfileBindingsV1(OTHER, DIGEST, DIGEST, DIGEST, DIGEST)},
    {"profile_bindings": ProfileBindingsV1(DIGEST, OTHER, DIGEST, DIGEST, DIGEST)},
])
def test_issuer_refuses_absent_mismatched_profile_or_open_defects(issuance, change):
    issuance.frontier = replace(issuance.frontier, **change)
    with pytest.raises(issuer.CertificationIssueError):
        issuer.issue_rehearsal_v1(apply=True)


def test_issuer_checks_real_isolation_before_other_owners(issuance, monkeypatch):
    monkeypatch.setattr(isolation, "observe_local_rehearsal_v1", lambda: (_ for _ in ()).throw(
        isolation.RehearsalIsolationError("host user namespace")))
    monkeypatch.setattr(issuer, "_completed_migration_id", lambda: pytest.fail("checked migration on host"))
    with pytest.raises(isolation.RehearsalIsolationError):
        issuer.issue_rehearsal_v1(apply=True)


def test_issuer_detects_frontier_change_before_signing(issuance, monkeypatch):
    values = iter([(DIGEST, DIGEST, DIGEST), (DIGEST, OTHER, DIGEST)])
    monkeypatch.setattr(issuer, "_installation_frontier_v1", lambda: next(values))
    with pytest.raises(issuer.CertificationIssueError, match="rehearsal_frontier_changed"):
        issuer.issue_rehearsal_v1(apply=True)


def test_actual_host_cannot_issue_a_lab_permit():
    if isolation.sys.platform.startswith("linux"):
        if isolation._read(isolation.Path("/proc/self/uid_map"), 4096).split()[1] != b"0":
            pytest.skip("this runner is already in a user namespace")
        with pytest.raises(isolation.RehearsalIsolationError):
            isolation.observe_local_rehearsal_v1()


@pytest.fixture
def installed_reader(monkeypatch, signed):
    import executor_birth_certification_authority as authority
    import executor_birth_ownership_authorities as ownership
    import executor_birth_ownership_chain as chain

    window = chain.VerifiedOwnershipWindowV1(
        (SimpleNamespace(head_id=DIGEST),), (),
        SimpleNamespace(identity=SimpleNamespace(closed_build_id=DIGEST)), ())
    monkeypatch.setattr(rehearsal, "observe_local_rehearsal_v1", lambda: SimpleNamespace(observation_id=DIGEST))
    monkeypatch.setattr(authority, "load_certification_public_key_v1", lambda: signed[0])
    monkeypatch.setattr(ownership, "load_ownership_public_registries_v1", lambda: object())
    monkeypatch.setattr(rehearsal, "_installation_id_v1", lambda _: DIGEST)
    marker = mode.encode_migration_marker_v1(installation_id=DIGEST, migration_id=DIGEST,
                                             completed_at="2026-09-26T00:00:00Z")
    monkeypatch.setattr(mode, "_marker_bytes", lambda: marker)
    monkeypatch.setattr(chain, "inspect_required_ownership_v1", lambda: window)
    monkeypatch.setattr(rehearsal, "_root_owned_chain", lambda *_: None)
    monkeypatch.setattr(rehearsal, "_directory_metadata", lambda *_, **__: None)
    monkeypatch.setattr(rehearsal, "_read_regular", lambda *_, **__: signed[1]())
    monkeypatch.setattr(rehearsal, "_clock_v1", lambda: NOW)


def test_fixed_reader_authenticates_the_current_permit(installed_reader):
    assert rehearsal.load_f5_rehearsal().permit.profile_id == DIGEST


@pytest.mark.parametrize("change", ["isolation", "head", "key", "migration", "document", "expiry"])
def test_fixed_reader_refuses_movement_during_observation(installed_reader, signed, monkeypatch, change):
    import executor_birth_certification_authority as authority
    import executor_birth_ownership_chain as chain

    if change == "isolation":
        values = iter([SimpleNamespace(observation_id=DIGEST), SimpleNamespace(observation_id=OTHER)])
        monkeypatch.setattr(rehearsal, "observe_local_rehearsal_v1", lambda: next(values))
    elif change == "head":
        before = chain.inspect_required_ownership_v1()
        values = iter([before, replace(before, heads=(SimpleNamespace(head_id=OTHER),))])
        monkeypatch.setattr(chain, "inspect_required_ownership_v1", lambda: next(values))
    elif change == "key":
        values = iter([signed[0], replace(signed[0], status="revoked")])
        monkeypatch.setattr(authority, "load_certification_public_key_v1", lambda: next(values))
    elif change == "migration":
        values = iter([mode._marker_bytes(), None])
        monkeypatch.setattr(mode, "_marker_bytes", lambda: next(values))
    elif change == "document":
        values = iter([signed[1](), signed[1](profile_id=OTHER)])
        monkeypatch.setattr(rehearsal, "_read_regular", lambda *_, **__: next(values))
    else:
        monkeypatch.setattr(rehearsal, "_clock_v1", lambda: (NOW[0], NOW[1] + 3600 * 10**9))
    with pytest.raises(lifecycle.LifecycleError, match="f5_rehearsal_invalid"):
        rehearsal.load_f5_rehearsal()


def test_productive_certificate_requirement_does_not_consult_rehearsal(monkeypatch):
    monkeypatch.setattr(mode, "read_birth_activation_state", lambda: mode.BirthActivationState(
        mode.BirthStateOwner.EPOCH, DIGEST, None, "uncertified"))
    monkeypatch.setattr(rehearsal, "load_f5_rehearsal", lambda: pytest.fail("production accepted rehearsal"))
    with pytest.raises(lifecycle.LifecycleError, match="f5_activation_invalid"):
        mode.require_f5_certificate()


def test_issue_uses_same_payload_as_runtime_without_inventing_qualification(issuance, signed, monkeypatch):
    def sign(payload):
        assert "qualification_id" not in payload
        return signed[1](**payload)

    monkeypatch.setattr(issuer, "_sign_certificate_v1", sign)
    monkeypatch.setattr(issuer, "_install_rehearsal_v1", lambda raw: decode(signed, document=raw).permit.permit_id)
    report = issuer.issue_rehearsal_v1(apply=True)
    assert report["permit_id"] == decode(signed).permit.permit_id
