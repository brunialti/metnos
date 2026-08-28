from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_ownership_preflight as preflight
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_maintenance_units import MAINTENANCE_TARGETS_V1
from executor_birth_ownership_cutover import (
    PURPOSE,
    OwnershipCutoverKey,
    OwnershipCutoverRegistry,
    install_ownership_cutover_certificate,
    issue_ownership_cutover_certificate,
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _authority():
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    raw = public.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    key_id = "birth-ed25519-v1-sha256-" + hashlib.sha256(raw).hexdigest()
    return private, key_id, OwnershipCutoverRegistry({
        key_id: OwnershipCutoverKey(key_id, public, frozenset({PURPOSE})),
    })


def _proof() -> CurrentReceiptProof:
    identities = (("executor:alpha", D("1")),)
    return CurrentReceiptProof(identities, {identities[0]: D("2")})


def _maintenance(
    *, active_state: str = "inactive", pid: int = 0,
    load_state: str = "loaded",
) -> bytes:
    units = tuple({
        "scope": scope, "unit": unit,
        "load_state": load_state if index == 0 else "loaded",
        "active_state": active_state if index == 0 else "inactive",
        "main_pid": pid if index == 0 else 0,
    } for index, (scope, unit) in enumerate(MAINTENANCE_TARGETS_V1))
    return preflight.canonical_maintenance_proof(
        source="inactive_http_and_inactive_sidecar",
        units=units,
    )


def test_maintenance_proof_rejects_a_well_formed_subset():
    value = json.loads(_maintenance())
    with pytest.raises(preflight.OwnershipPreflightError, match="maintenance_invalid"):
        preflight.canonical_maintenance_proof(
            source=value["source"], units=value["units"][:-1],
        )


def test_maintenance_proof_rejects_a_required_unit_not_found():
    with pytest.raises(preflight.OwnershipPreflightError, match="not_quiescent"):
        _maintenance(load_state="not-found")


def _installed(tmp_path: Path):
    private, key_id, registry = _authority()
    proof = _proof()
    maintenance = _maintenance()
    encoded, signature = issue_ownership_cutover_certificate(
        proof=proof, previous_cutover_id=None, request_id=D("3"),
        signing_key_id=key_id,
        maintenance_evidence_hash=preflight.maintenance_evidence_hash(maintenance),
        boundary_inventory_hash=D("4"), boundary_guard_version="closed-v1",
        closed_build_id=D("5"), private_key=private,
    )
    install_ownership_cutover_certificate(
        tmp_path, encoded, signature, registry=registry, expected_proof=proof,
    )
    build = preflight._sealed_build_identity_for_test(D("5"), D("4"), "closed-v1")
    return registry, proof, maintenance, build


def test_maintenance_proof_is_canonical_domain_separated_and_quiescent():
    encoded = _maintenance()
    assert encoded == json.dumps(
        json.loads(encoded), ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("ascii")
    assert preflight.maintenance_evidence_hash(encoded).startswith("sha256:")
    with pytest.raises(preflight.OwnershipPreflightError, match="not_quiescent"):
        _maintenance(active_state="active", pid=41)


@pytest.mark.parametrize("mutation", ["order", "duplicate", "extra", "source"])
def test_maintenance_proof_rejects_noncanonical_or_open_documents(mutation):
    value = json.loads(_maintenance())
    if mutation == "order":
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=False).encode("ascii")
    elif mutation == "duplicate":
        encoded = _maintenance().replace(b'{"active_state"', b'{"scope":"system","active_state"')
    elif mutation == "extra":
        value["extra"] = True
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    else:
        value["source"] = "caller_selected"
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(preflight.OwnershipPreflightError):
        preflight.maintenance_evidence_hash(encoded)


def test_startup_preflight_binds_build_inventory_guard_catalog_and_maintenance(
    tmp_path, monkeypatch,
):
    registry, proof, maintenance, build = _installed(tmp_path)
    monkeypatch.setattr(preflight, "DEFAULT_CERTIFICATE_DIRECTORY", tmp_path)
    monkeypatch.setattr(preflight, "verify_root_owned_certificate_directory", lambda _path: None)
    result = preflight.preflight_closed_build(
        tmp_path, registry=registry, authenticated_build=build,
        expected_current=proof,
    )
    assert result.closed_build_id == D("5")
    assert result.current_count == 1


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("closed_build_id", "build_mismatch"),
        ("boundary_inventory_hash", "inventory_mismatch"),
        ("boundary_guard_version", "guard_mismatch"),
    ],
)
def test_startup_preflight_fails_closed_on_build_binding(
    tmp_path, monkeypatch, field, code,
):
    registry, proof, maintenance, build = _installed(tmp_path)
    monkeypatch.setattr(preflight, "DEFAULT_CERTIFICATE_DIRECTORY", tmp_path)
    monkeypatch.setattr(preflight, "verify_root_owned_certificate_directory", lambda _path: None)
    values = {
        "closed_build_id": build.closed_build_id,
        "boundary_inventory_hash": build.boundary_inventory_hash,
        "boundary_guard_version": build.boundary_guard_version,
    }
    values[field] = "other-version" if field == "boundary_guard_version" else D("9")
    changed = preflight._sealed_build_identity_for_test(**values)
    with pytest.raises(preflight.OwnershipPreflightError, match=code):
        preflight.preflight_closed_build(
            tmp_path, registry=registry, authenticated_build=changed,
            expected_current=proof,
        )


def test_startup_preflight_requires_authenticated_build_authority(tmp_path, monkeypatch):
    registry, proof, maintenance, _build = _installed(tmp_path)
    monkeypatch.setattr(preflight, "DEFAULT_CERTIFICATE_DIRECTORY", tmp_path)
    monkeypatch.setattr(preflight, "verify_root_owned_certificate_directory", lambda _path: None)
    with pytest.raises(preflight.OwnershipPreflightError, match="authority_missing"):
        preflight.preflight_closed_build(
            tmp_path, registry=registry, authenticated_build=None,
            expected_current=proof,
        )


def test_startup_preflight_rejects_catalog_drift(tmp_path, monkeypatch):
    registry, proof, maintenance, build = _installed(tmp_path)
    monkeypatch.setattr(preflight, "DEFAULT_CERTIFICATE_DIRECTORY", tmp_path)
    monkeypatch.setattr(preflight, "verify_root_owned_certificate_directory", lambda _path: None)
    other_identity = (("executor:beta", D("6")),)
    other = CurrentReceiptProof(other_identity, {other_identity[0]: D("7")})
    with pytest.raises(preflight.OwnershipPreflightError, match="binding_invalid"):
        preflight.preflight_closed_build(
            tmp_path, registry=registry, authenticated_build=build,
            expected_current=other,
        )


def test_cutover_coordinator_rejects_historical_maintenance_drift(tmp_path):
    registry, proof, maintenance, _build = _installed(tmp_path)
    from executor_birth_ownership_cutover import read_ownership_cutover_certificate
    certificate = read_ownership_cutover_certificate(
        tmp_path, registry=registry, expected_proof=proof,
    )
    changed = preflight.canonical_maintenance_proof(
        source="inactive_http_and_sidecar_broker", units=json.loads(maintenance)["units"],
    )
    with pytest.raises(preflight.OwnershipPreflightError, match="maintenance_changed"):
        preflight.verify_cutover_maintenance_evidence(certificate, changed)


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="Linux metadata")
def test_linux_root_owned_check_rejects_writable_or_nonroot_ancestor(tmp_path):
    # Even if the leaf were made to look safe, /tmp is a world-writable parent.
    # Rejecting the first unsafe ancestor prevents replacement of the whole
    # certificate directory before its leaf metadata is inspected.
    tmp_path.chmod(0o755)
    (tmp_path / "ownership-cutover-v1.json").write_bytes(b"{}")
    (tmp_path / "ownership-cutover-v1.sig").write_bytes(b"x" * 64)
    for child in tmp_path.iterdir():
        child.chmod(0o644)
    with pytest.raises(preflight.OwnershipPreflightError, match="path_unsafe") as caught:
        preflight.verify_root_owned_certificate_directory(tmp_path)
    assert Path(caught.value.detail) in tmp_path.parents
