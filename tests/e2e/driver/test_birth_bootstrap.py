from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import executor_birth_bootstrap as bootstrap
from executor_birth_intent import _producer_capabilities_for_bootstrap
from executor_birth_keystore import load_birth_keystore, raw_public_key
from manifest_inventory import ManifestInventory

from .birth_bootstrap import provision_e2e_birth_bootstrap


def test_provisioned_birth_bootstrap_builds_with_separated_authorities(
        monkeypatch, tmp_path: Path) -> None:
    user_config = tmp_path / "config"
    user_config.mkdir(mode=0o700)
    config_path = provision_e2e_birth_bootstrap(user_config)
    value = json.loads(config_path.read_text())

    stores = [load_birth_keystore(config_path.parent / value["admission"]["keystore"])]
    stores.extend(load_birth_keystore(config_path.parent / item["keystore"])
                  for item in value["producers"].values())
    public_keys = [raw_public_key(store.active_private_key.public_key()) for store in stores]
    assert len(stores) == 1 + len(_producer_capabilities_for_bootstrap())
    assert len(set(public_keys)) == len(public_keys)

    import manifest_inventory
    import sign
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: ManifestInventory((), ()))
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: [])
    bundle = bootstrap._build(
        bootstrap.BirthBootstrapPaths(config_path, tmp_path / "state" / "birth"),
        now=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    )
    assert set(bundle.producer_factories) == set(_producer_capabilities_for_bootstrap())
    assert bundle.core.producer_db.exists()
    assert (config_path.parent / "approval-store" / "approvals.sqlite").exists()
