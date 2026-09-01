"""Focused proof that the first installed catalog crosses the sealed Birth gate."""
from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import support


pytestmark = [
    pytest.mark.skipif(
        os.name == "nt",
        reason="the managed phase-3 cutover is a Linux/systemd operation",
    ),
    pytest.mark.skipif(
        not Path("/var/lib/metnos").is_dir(),
        reason="requires the installed fixed Birth product root",
    ),
]


def test_initial_catalog_is_birth_published_and_replay_verifiable(
    tmp_path: Path, monkeypatch,
) -> None:
    import executor_birth_bootstrap as bootstrap
    import config
    import manifest_inventory
    import sign
    from admin.i18n_migrate_manifests import activate_prepared_contract_store
    from contract_bootstrap import ProductionStoreMode
    from contract_store import current_manifest, read_current_birth_receipt
    from contract_store import production_store_mode
    from executor_birth_prepared_root import load_sealed_authorities_v1
    from executor_birth_receipts import verify_admission_receipt
    from manifest_inventory import (
        ContractId,
        ManifestInventory,
        ManifestOrigin,
    )

    historic_author = Ed25519PrivateKey.generate()
    base = support.make_config(
        tmp_path / "installation",
        author=historic_author,
        operator=True,
    )
    monkeypatch.setattr(sign, "KEYS_DIR", base / "keys")
    support.provision(monkeypatch, base)
    ref, _historic_key, _historic_ring = support.create_contract_source(
        tmp_path / "contract",
    )
    ref = replace(
        ref,
        contract_id=ContractId(ManifestOrigin.USER, ref.manifest_relative),
        origin=ManifestOrigin.USER,
    )
    monkeypatch.setattr(config, "PATH_SYNTH_EXECUTORS", ref.source_root)
    inventory = ManifestInventory((ref,), ())
    monkeypatch.setattr(
        manifest_inventory, "inventory_authoring_manifests", lambda: inventory,
    )
    report = bootstrap.prepare_initial_installer_catalog_v1(
        prove_quiescent=lambda: True,
    )

    sealed = load_sealed_authorities_v1()
    trusted = tuple(sorted(sealed.author.verifier_keys.items()))
    generation_id = report["catalog"][ref.contract_id.value]
    installed = current_manifest(
        ref,
        trusted_publics=trusted,
        store_root=Path(report["shadow_root"]),
    )
    encoded = read_current_birth_receipt(
        ref,
        generation_id,
        trusted_publics=trusted,
        store_root=Path(report["shadow_root"]),
    )
    receipt = verify_admission_receipt(
        encoded,
        verifier_keys=sealed.admission.verifier_keys,
    )

    assert installed.generation_id == generation_id
    assert receipt.generation_id == generation_id
    assert report["birth_receipts"][ref.contract_id.value] == (
        "sha256:" + hashlib.sha256(encoded).hexdigest()
    )
    assert receipt.birth_request_id == bootstrap._initial_request_id_v1(
        ref, bootstrap._initial_candidate_payloads_v1(ref),
    )
    assert bootstrap.verify_initial_installer_report_v1(
        report,
        prove_quiescent=lambda: True,
    ) == {"contracts": 1, "receipts": 1}
    activate_prepared_contract_store(
        report,
        quiescence_guard=lambda: True,
    )
    assert production_store_mode() is ProductionStoreMode.ACTIVE
    assert bootstrap.verify_initial_installer_store_v1(
        prove_quiescent=lambda: True,
    ) == {"contracts": 1, "receipts": 1}
