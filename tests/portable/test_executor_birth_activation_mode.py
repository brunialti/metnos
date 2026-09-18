"""Which store owns lifecycle state, and when a certificate is required."""
from __future__ import annotations

import hashlib
import sys

import pytest

import executor_birth_activation_mode as mode
import executor_birth_authority_files as files
from executor_birth_lifecycle import F5Certification, LifecycleError


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


INSTALLATION = digest("installation")
MIGRATION = digest("migration")


def certificate(*, migration_id=MIGRATION, installation_id=INSTALLATION):
    return F5Certification(
        digest("certificate"), installation_id, digest("qualification"),
        digest("head"), digest("build"), migration_id,
        mode.__dict__.get("ACTIVATION_POLICY", "rm0008-f5/1"), "key", "signature",
    )


@pytest.fixture
def root(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("the managed marker custody is Linux-only")
    directory = tmp_path / "certification-v1"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    monkeypatch.setattr(mode, "ACTIVATION_DIRECTORY", directory)
    monkeypatch.setattr(mode, "_root_owned_chain", lambda location: None)
    monkeypatch.setattr(mode, "_directory_metadata",
                        lambda location, **kw: files._directory_metadata(location, root_owned=False))
    monkeypatch.setattr(mode, "_read_regular",
                        lambda location, **kw: files._read_regular(location, **{**kw, "root_owned": False}))
    monkeypatch.setattr(mode, "load_f5_activation",
                        lambda: pytest.fail("the certificate was read without a marker"))
    return directory


def install_marker(directory, *, migration_id=MIGRATION, installation_id=INSTALLATION):
    path = directory / mode.MIGRATION_BASENAME_V1
    path.write_bytes(mode.encode_migration_marker_v1(
        installation_id=installation_id, migration_id=migration_id,
        completed_at="2026-09-16T12:00:00Z",
    ))
    path.chmod(0o644)
    return path


def accept(monkeypatch, value):
    monkeypatch.setattr(mode, "load_f5_activation",
                        lambda: type("A", (), {"certificate": value})())


def refuse(monkeypatch, error):
    def raise_it():
        raise error
    monkeypatch.setattr(mode, "load_f5_activation", raise_it)


def test_an_unmigrated_installation_never_opens_the_certificate(root):
    state = mode.read_birth_activation_state()
    assert state.owner is mode.BirthStateOwner.LEGACY
    assert (state.migration_id, state.certificate, state.certificate_refusal) == (None, None, None)


def test_the_legacy_owner_refuses_an_operation_that_needs_qualification(root):
    with pytest.raises(LifecycleError) as raised:
        mode.require_f5_certificate()
    assert raised.value.code == "f5_epoch_migration_required"


def test_a_migrated_installation_binds_its_exact_certificate(root, monkeypatch):
    install_marker(root)
    expected = certificate()
    accept(monkeypatch, expected)
    state = mode.read_birth_activation_state()
    assert state.owner is mode.BirthStateOwner.EPOCH
    assert state.migration_id == MIGRATION
    assert state.certificate is expected
    assert mode.require_f5_certificate() is expected


@pytest.mark.parametrize("failure", [
    LifecycleError("f5_activation_invalid", "document"),
    files.OwnershipAuthorityError("birth_certification_authority_revoked"),
    FileNotFoundError("active.json"),
], ids=["invalid", "revoked", "absent"])
def test_a_missing_certificate_never_reselects_the_retired_legacy_state(
    root, monkeypatch, failure,
):
    install_marker(root)
    refuse(monkeypatch, failure)
    state = mode.read_birth_activation_state()
    assert state.owner is mode.BirthStateOwner.EPOCH
    assert state.certificate is None and state.certificate_refusal
    with pytest.raises(LifecycleError) as raised:
        mode.require_f5_certificate()
    assert raised.value.code == "f5_activation_invalid"


@pytest.mark.parametrize("field", ["migration_id", "installation_id"])
def test_a_certificate_for_another_migration_or_installation_is_refused(
    root, monkeypatch, field,
):
    install_marker(root)
    accept(monkeypatch, certificate(**{field: digest("other " + field)}))
    state = mode.read_birth_activation_state()
    assert state.owner is mode.BirthStateOwner.EPOCH
    assert state.certificate_refusal == "f5_activation_migration_mismatch"


@pytest.mark.parametrize("damage", ["truncated", "spacing", "purpose", "digest", "extra"])
def test_a_damaged_marker_refuses_the_selection_instead_of_guessing(
    root, monkeypatch, damage,
):
    import json
    from executor_birth_canonical import encode_canonical_ascii_v1 as encode

    raw = mode.encode_migration_marker_v1(
        installation_id=INSTALLATION, migration_id=MIGRATION,
        completed_at="2026-09-16T12:00:00Z")
    value = json.loads(raw)
    if damage == "truncated":
        raw = raw[:-4]
    elif damage == "spacing":
        raw += b"\n"
    elif damage == "purpose":
        raw = encode({**value, "purpose": "f5_activation_v1"})
    elif damage == "digest":
        raw = encode({**value, "migration_id": "not-a-digest"})
    else:
        raw = encode({**value, "unexpected": 1})
    path = root / mode.MIGRATION_BASENAME_V1
    path.write_bytes(raw)
    path.chmod(0o644)
    with pytest.raises(LifecycleError) as raised:
        mode.read_birth_activation_state()
    assert raised.value.code == "f5_migration_marker_invalid"


def test_something_other_than_the_marker_at_its_name_is_a_fault(root):
    (root / mode.MIGRATION_BASENAME_V1).mkdir()
    with pytest.raises(LifecycleError) as raised:
        mode.read_birth_activation_state()
    assert raised.value.code == "f5_migration_marker_invalid"


def test_a_loose_marker_mode_is_refused(root):
    install_marker(root).chmod(0o666)
    with pytest.raises(LifecycleError) as raised:
        mode.read_birth_activation_state()
    assert raised.value.code == "f5_migration_marker_invalid"


@pytest.mark.parametrize("field,value", [
    ("installation_id", "not-a-digest"), ("migration_id", None),
    ("completed_at", "2026-09-16 12:00:00"),
])
def test_the_marker_encoder_refuses_an_invented_identity(field, value):
    payload = dict(installation_id=INSTALLATION, migration_id=MIGRATION,
                   completed_at="2026-09-16T12:00:00Z")
    with pytest.raises(LifecycleError):
        mode.encode_migration_marker_v1(**{**payload, field: value})
