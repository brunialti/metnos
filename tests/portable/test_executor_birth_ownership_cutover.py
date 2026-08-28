"""Portable adversarial certification for the RM-0008 F4 certificate core."""
from __future__ import annotations

import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_ownership_cutover as cutover_module
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_ownership_cutover import (
    CUTOVER_ID_DOMAIN, PAYLOAD_BASENAME, PURPOSE, SIGNATURE_BASENAME,
    SIGNATURE_DOMAIN, OwnershipCutoverError, OwnershipCutoverKey,
    OwnershipCutoverRegistry, install_ownership_cutover_certificate,
    issue_ownership_cutover_certificate, read_ownership_cutover_certificate,
    verify_ownership_cutover_certificate,
)
from executor_birth_keystore import birth_key_id


D = lambda char: "sha256:" + char * 64


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


@pytest.fixture
def authority():
    private = Ed25519PrivateKey.from_private_bytes(b"o" * 32)
    key_id = birth_key_id(private.public_key())
    registry = OwnershipCutoverRegistry({
        key_id: OwnershipCutoverKey(key_id, private.public_key(), frozenset({PURPOSE})),
    })
    return private, key_id, registry


def _proof(*names: str) -> CurrentReceiptProof:
    identities = tuple(sorted((f"explicit:{name}/manifest.toml", D(name[0])) for name in names))
    return CurrentReceiptProof(identities, {
        identity: D(chr(ord("f") - index)) for index, identity in enumerate(identities)
    })


def _issue(authority, proof=None, previous=None):
    private, key_id, _registry = authority
    return issue_ownership_cutover_certificate(
        proof=proof or _proof("alpha"), previous_cutover_id=previous,
        request_id=D("1"), signing_key_id=key_id,
        maintenance_evidence_hash=D("2"), boundary_inventory_hash=D("3"),
        boundary_guard_version="metnos.contract-boundary-inventory/2+birth-closed/1",
        closed_build_id=D("4"), private_key=private,
    )


@pytest.mark.parametrize("names", [(), ("alpha",), ("charlie", "alpha", "bravo")])
def test_zero_one_many_round_trip_bind_exact_current_proof(authority, names):
    proof = _proof(*names)
    encoded, signature = _issue(authority, proof)
    certificate = verify_ownership_cutover_certificate(
        encoded, signature, registry=authority[2], expected_proof=proof,
    )
    assert certificate.as_proof() == proof
    assert certificate.current_count == len(names)
    assert encoded == _canonical(json.loads(encoded))


def test_previous_cutover_is_authenticated_and_exact(authority):
    encoded, signature = _issue(authority, previous=D("9"))
    certificate = verify_ownership_cutover_certificate(
        encoded, signature, registry=authority[2], expected_previous_cutover_id=D("9"),
    )
    assert certificate.previous_cutover_id == D("9")
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_binding_invalid"):
        verify_ownership_cutover_certificate(
            encoded, signature, registry=authority[2], expected_previous_cutover_id=None,
        )


def _resign(authority, encoded, mutate):
    value = json.loads(encoded)
    mutate(value)
    value["cutover_id"] = "sha256:" + hashlib.sha256(
        CUTOVER_ID_DOMAIN + _canonical({key: item for key, item in value.items()
                                       if key != "cutover_id"})
    ).hexdigest()
    changed = _canonical(value)
    return changed, authority[0].sign(SIGNATURE_DOMAIN + changed)


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(extra=True),
    lambda value: value.update(current_count=True),
    lambda value: value["current_receipts"].append(dict(value["current_receipts"][0])),
    lambda value: value["current_receipts"][0].update(extra="forbidden"),
])
def test_closed_schema_duplicate_and_boolean_count_fail(authority, mutation):
    encoded, _signature = _issue(authority)
    changed, signature = _resign(authority, encoded, mutation)
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_proof_invalid"):
        verify_ownership_cutover_certificate(changed, signature, registry=authority[2])


def test_noncanonical_duplicate_json_tamper_and_wrong_purpose_fail(authority):
    encoded, signature = _issue(authority)
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_proof_invalid"):
        verify_ownership_cutover_certificate(encoded + b"\n", signature, registry=authority[2])
    duplicate = encoded[:-1] + b',"schema_version":1}'
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_proof_invalid"):
        verify_ownership_cutover_certificate(duplicate, signature, registry=authority[2])
    unauthorized = OwnershipCutoverRegistry({authority[1]: OwnershipCutoverKey(
        authority[1], authority[0].public_key(), frozenset({"ownership_head_v1"}),
    )})
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_key_unauthorized"):
        verify_ownership_cutover_certificate(encoded, signature, registry=unauthorized)


def test_catalog_and_current_proof_mismatch_fail_closed(authority):
    proof = _proof("alpha", "bravo")
    encoded, signature = _issue(authority, proof)
    changed, changed_signature = _resign(
        authority, encoded, lambda value: value.update(catalog_id=D("8")),
    )
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_binding_invalid"):
        verify_ownership_cutover_certificate(changed, changed_signature, registry=authority[2])
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_binding_invalid"):
        verify_ownership_cutover_certificate(
            encoded, signature, registry=authority[2], expected_proof=_proof("alpha"),
        )


def test_reordered_receipts_fail_even_with_a_valid_cutover_signature(authority):
    encoded, _signature = _issue(authority, _proof("alpha", "bravo"))
    changed, signature = _resign(
        authority, encoded, lambda value: value["current_receipts"].reverse(),
    )
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_proof_invalid"):
        verify_ownership_cutover_certificate(changed, signature, registry=authority[2])


def test_no_replace_store_exact_retry_and_orphan_signature_resume(tmp_path, authority):
    os.chmod(tmp_path, 0o700)
    proof = _proof("alpha")
    encoded, signature = _issue(authority, proof)
    first = install_ownership_cutover_certificate(
        tmp_path, encoded, signature, registry=authority[2], expected_proof=proof,
    )
    second = install_ownership_cutover_certificate(
        tmp_path, encoded, signature, registry=authority[2], expected_proof=proof,
    )
    assert first == second

    orphan = tmp_path / "orphan"
    orphan.mkdir(mode=0o700)
    (orphan / SIGNATURE_BASENAME).write_bytes(signature)
    os.chmod(orphan / SIGNATURE_BASENAME, 0o644)
    resumed = install_ownership_cutover_certificate(
        orphan, encoded, signature, registry=authority[2], expected_proof=proof,
    )
    assert resumed.cutover_id == first.cutover_id


def test_temporary_write_round_trips_every_byte_value(tmp_path):
    payload = bytes(range(256))
    destination = tmp_path / "all-bytes.tmp"

    cutover_module._write_temporary(destination, payload)

    assert destination.read_bytes() == payload
    assert destination.stat().st_size == len(payload)


def test_temporary_write_requests_binary_mode(monkeypatch, tmp_path):
    """The Windows CRT must not expand an LF byte to CRLF."""
    binary_flag = 0x8000
    observed_flags: list[int] = []
    written = bytearray()

    def fake_open(_path, flags, _mode):
        observed_flags.append(flags)
        return 73

    def fake_write(_fd, payload):
        written.extend(payload)
        return len(payload)

    monkeypatch.setattr(cutover_module.os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(cutover_module.os, "open", fake_open)
    monkeypatch.setattr(cutover_module.os, "write", fake_write)
    monkeypatch.setattr(cutover_module.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(cutover_module.os, "fchmod", lambda _fd, _mode: None, raising=False)
    monkeypatch.setattr(cutover_module.os, "close", lambda _fd: None)

    signature = b"prefix\nsuffix\x00"
    cutover_module._write_temporary(tmp_path / "signature.tmp", signature)

    assert observed_flags == [
        cutover_module.os.O_WRONLY
        | cutover_module.os.O_CREAT
        | cutover_module.os.O_EXCL
        | getattr(cutover_module.os, "O_CLOEXEC", 0)
        | binary_flag
    ]
    assert bytes(written) == signature


def test_partial_pair_conflict_and_hardlink_are_never_trusted(tmp_path, authority):
    os.chmod(tmp_path, 0o700)
    proof = _proof("alpha")
    encoded, signature = _issue(authority, proof)
    (tmp_path / PAYLOAD_BASENAME).write_bytes(encoded)
    os.chmod(tmp_path / PAYLOAD_BASENAME, 0o644)
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_recovery_required"):
        read_ownership_cutover_certificate(tmp_path, registry=authority[2])

    hardlinks = tmp_path / "hardlinks"
    hardlinks.mkdir(mode=0o700)
    (hardlinks / PAYLOAD_BASENAME).write_bytes(encoded)
    (hardlinks / SIGNATURE_BASENAME).write_bytes(signature)
    os.chmod(hardlinks / PAYLOAD_BASENAME, 0o644)
    os.chmod(hardlinks / SIGNATURE_BASENAME, 0o644)
    os.link(hardlinks / PAYLOAD_BASENAME, hardlinks / "second-name")
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_recovery_required"):
        read_ownership_cutover_certificate(hardlinks, registry=authority[2])


def test_existing_different_certificate_is_a_non_overwriting_conflict(tmp_path, authority):
    os.chmod(tmp_path, 0o700)
    proof = _proof("alpha")
    first, first_signature = _issue(authority, proof)
    install_ownership_cutover_certificate(
        tmp_path, first, first_signature, registry=authority[2], expected_proof=proof,
    )
    second, second_signature = issue_ownership_cutover_certificate(
        proof=proof, previous_cutover_id=None, request_id=D("5"),
        signing_key_id=authority[1], maintenance_evidence_hash=D("2"),
        boundary_inventory_hash=D("3"), boundary_guard_version="guard/1",
        closed_build_id=D("4"), private_key=authority[0],
    )
    with pytest.raises(OwnershipCutoverError, match="birth_ownership_cutover_conflict"):
        install_ownership_cutover_certificate(
            tmp_path, second, second_signature, registry=authority[2], expected_proof=proof,
        )
    assert (tmp_path / PAYLOAD_BASENAME).read_bytes() == first
