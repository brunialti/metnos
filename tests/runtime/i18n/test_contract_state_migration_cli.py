from __future__ import annotations

import contextlib
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import contract_store
import manifest_inventory
import sign
from admin.i18n_migrate_manifests import (
    activate_prepared_contract_store,
    migrate_contract_language_states,
)
from i18n_materializer import decode_language_state
from i18n_registry import LocalizationRegistry

from test_i18n_materializer import _versioned_fixture


def _contract(root: Path, name: str, *, state: bytes) -> SimpleNamespace:
    from manifest_inventory import ManifestStatus

    directory = root / name
    directory.mkdir(parents=True)
    manifest_path = directory / "manifest.toml"
    manifest_path.write_text(
        f'name="{name}"\n[description]\nen="Read"\nit="Leggi"\n'
        '[args]\ntype="object"\n[args.properties.path]\ntype="string"\n'
        '[args.properties.path.description]\nen="Path"\nit="Percorso"\n',
        encoding="utf-8",
    )
    (directory / "manifest.lang_state.json").write_bytes(state)
    return SimpleNamespace(
        contract_id=f"explicit:{name}/manifest.toml",
        manifest_path=manifest_path,
        manifest_dir=directory,
        manifest_relative=f"{name}/manifest.toml",
        status=ManifestStatus.ADMITTED,
    )


def _inventory(monkeypatch, refs) -> None:
    import manifest_inventory

    value = manifest_inventory.ManifestInventory(
        manifests=tuple(refs), problems=(),
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_authoring_manifests", lambda: value,
    )


def test_contract_state_migration_is_canonical_and_resumable(
    tmp_path: Path, monkeypatch,
) -> None:
    legacy = json.dumps({
        "description": {
            "en": {
                "version_hash": "sha256:" + "0" * 64,
                "source_lang": None,
                "source_hash": None,
            },
        },
    }).encode()
    ref = _contract(tmp_path, "reader", state=legacy)
    _inventory(monkeypatch, (ref,))

    preview = migrate_contract_language_states(dry_run=True)
    assert preview["changed"] == 1
    assert (ref.manifest_dir / "manifest.lang_state.json").read_bytes() == legacy

    written = migrate_contract_language_states()
    repeated = migrate_contract_language_states()
    state_bytes = (ref.manifest_dir / "manifest.lang_state.json").read_bytes()
    manifest = tomllib.loads(ref.manifest_path.read_text(encoding="utf-8"))
    state = decode_language_state(state_bytes, manifest=manifest)
    assert written["changed"] == 1
    assert repeated["changed"] == 0
    assert set(state["selectors"]) == {
        "description", "args.properties.path.description",
    }


def test_contract_state_migration_preflights_before_any_write(
    tmp_path: Path, monkeypatch,
) -> None:
    first = _contract(tmp_path, "first", state=b"{}")
    broken = _contract(tmp_path, "broken", state=b"not-json")
    _inventory(monkeypatch, (first, broken))
    original = (first.manifest_dir / "manifest.lang_state.json").read_bytes()

    with pytest.raises(Exception):
        migrate_contract_language_states()

    assert (first.manifest_dir / "manifest.lang_state.json").read_bytes() == original


def _prepared_activation_fixture(tmp_path: Path, monkeypatch):
    (
        paths,
        ref,
        private_key,
        trusted,
        shadow,
        publication,
        _snapshot_provider,
    ) = _versioned_fixture(tmp_path)
    state_root = tmp_path / "state"
    source = manifest_inventory.ManifestSource(
        manifest_inventory.ManifestOrigin.EXPLICIT,
        paths.manifest_roots[0],
        min_depth=1,
        max_depth=1,
        allowed_code_roots=paths.manifest_roots,
    )
    authoring_inventory = manifest_inventory.inventory_authoring_manifests
    monkeypatch.setattr(config, "PATH_USER_STATE", state_root)
    monkeypatch.setattr(
        manifest_inventory, "default_manifest_sources", lambda: (source,),
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda: authoring_inventory((source,)),
    )
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: list(trusted))
    canonical_shadow = (
        state_root / "contract-publications-shadow" / "attempt" / "v1"
    )
    canonical_shadow.parent.mkdir(parents=True)
    shadow.rename(canonical_shadow)
    report = {
        "schema": "metnos.contract-store-cutover/1",
        "shadow_root": str(canonical_shadow),
        "contracts": 1,
        "repeated": 0,
        "catalog": {
            str(ref.contract_id): publication.current_generation_id,
        },
    }
    return ref, private_key, state_root, report


def test_activation_rejects_resigned_authoring_drift_before_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    ref, private_key, state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    activated: list[object] = []
    monkeypatch.setattr(
        contract_store,
        "activate_store",
        lambda *_args, **_kwargs: activated.append(object()),
    )
    ref.manifest_path.write_text(
        ref.manifest_path.read_text(encoding="utf-8")
        + "\n# signed authoring revision after shadow preparation\n",
        encoding="utf-8",
    )
    (ref.manifest_dir / "manifest.toml.sig").write_bytes(
        sign.sign_manifest_bytes(
            ref.manifest_path.read_bytes(), private_key=private_key,
        )
    )

    with pytest.raises(
        contract_store.ContractStoreError,
        match="activation_authoring_stale",
    ):
        activate_prepared_contract_store(
            report, quiescence_guard=lambda: True,
        )

    assert activated == []
    assert not (state_root / "contract-publications.ACTIVE").exists()


def test_activation_rejects_stale_registry_owner_before_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    ref, _private_key, state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    registry = LocalizationRegistry(state_root / "i18n_registry.sqlite")
    historical_contract = "user:retired-reader/manifest.toml"
    registry.register(
        "contract:read_files:args.properties.path.description",
        "contract",
        "en",
        "de",
        "0" * 64,
        metadata={"contract_id": historical_contract},
    )
    assert registry.retire_published_contract(historical_contract) == 1
    activated: list[object] = []
    monkeypatch.setattr(
        contract_store,
        "activate_store",
        lambda *_args, **_kwargs: activated.append(object()),
    )

    with pytest.raises(
        contract_store.ContractStoreError,
        match="activation_registry_collision",
    ):
        activate_prepared_contract_store(
            report, quiescence_guard=lambda: True,
        )

    assert activated == []
    assert not (state_root / "contract-publications.ACTIVE").exists()
    rows = registry.resources("de", current_only=False)
    assert len(rows) == 1
    assert rows[0].status == "stale"
    assert rows[0].contract_id == historical_contract
    assert str(ref.contract_id) != historical_contract


def test_activation_rereads_production_before_registry_reconciliation(
    tmp_path: Path, monkeypatch,
) -> None:
    _ref, _private_key, _state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    shadow_root = Path(report["shadow_root"])
    real_current_manifest = contract_store.current_manifest
    events: list[str] = []

    def current_manifest(ref, *, trusted_publics, store_root=None):
        if store_root is None:
            events.append("production-read")
            store_root = shadow_root
        else:
            events.append("shadow-read")
        return real_current_manifest(
            ref,
            trusted_publics=trusted_publics,
            store_root=store_root,
        )

    def activate_store(
        _expected,
        *,
        shadow_root,
        trusted_publics,
        quiescence_guard,
    ):
        del shadow_root, trusted_publics
        assert quiescence_guard() is True
        events.append("activate")

    monkeypatch.setattr(contract_store, "current_manifest", current_manifest)
    monkeypatch.setattr(contract_store, "activate_store", activate_store)

    activate_prepared_contract_store(
        report, quiescence_guard=lambda: True,
    )

    assert events == ["shadow-read", "activate", "production-read"]


def test_direct_activation_holds_catalog_exclusion_through_reconciliation(
    tmp_path: Path, monkeypatch,
) -> None:
    _ref, _private_key, _state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    shadow_root = Path(report["shadow_root"])
    real_current_manifest = contract_store.current_manifest
    held = False
    events: list[str] = []

    @contextlib.contextmanager
    def catalog_lock(*_args, **_kwargs):
        nonlocal held
        assert not held
        held = True
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")
            held = False

    def current_manifest(ref, *, trusted_publics, store_root=None):
        assert held
        events.append("production-read" if store_root is None else "shadow-read")
        return real_current_manifest(
            ref,
            trusted_publics=trusted_publics,
            store_root=shadow_root if store_root is None else store_root,
        )

    def activate_store(
        _expected,
        *,
        shadow_root,
        trusted_publics,
        quiescence_guard,
    ):
        del shadow_root, trusted_publics
        assert held
        assert quiescence_guard() is True
        events.append("activate")

    monkeypatch.setattr(contract_store, "catalog_admission_lock", catalog_lock)
    monkeypatch.setattr(contract_store, "current_manifest", current_manifest)
    monkeypatch.setattr(contract_store, "activate_store", activate_store)

    activate_prepared_contract_store(
        report, quiescence_guard=lambda: True,
    )

    assert events == [
        "lock-enter",
        "shadow-read",
        "activate",
        "production-read",
        "lock-exit",
    ]


def test_activation_retry_after_swap_reconciles_without_shadow(
    tmp_path: Path, monkeypatch,
) -> None:
    import i18n_pipeline

    ref, _private_key, state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    real_reconcile = i18n_pipeline.reconcile_published_contract_registry

    def interrupted_reconcile(*_args, **_kwargs):
        raise RuntimeError("simulated post-swap registry crash")

    monkeypatch.setattr(
        i18n_pipeline,
        "reconcile_published_contract_registry",
        interrupted_reconcile,
    )
    with pytest.raises(RuntimeError, match="post-swap registry crash"):
        activate_prepared_contract_store(
            report, quiescence_guard=lambda: True,
        )

    assert contract_store.production_store_mode() is (
        contract_store.ProductionStoreMode.ACTIVE
    )
    assert not Path(report["shadow_root"]).exists()

    reconciled: list[str] = []

    def observe_reconcile(revision, **kwargs):
        reconciled.append(str(revision.contract_id))
        return real_reconcile(revision, **kwargs)

    monkeypatch.setattr(
        i18n_pipeline,
        "reconcile_published_contract_registry",
        observe_reconcile,
    )
    activate_prepared_contract_store(
        report, quiescence_guard=lambda: True,
    )

    assert reconciled == [str(ref.contract_id)]
    assert contract_store.production_store_mode() is (
        contract_store.ProductionStoreMode.ACTIVE
    )
    assert (state_root / "contract-publications" / "v1").is_dir()


def test_post_swap_retry_rejects_report_from_another_catalog(
    tmp_path: Path, monkeypatch,
) -> None:
    import i18n_pipeline

    _ref, _private_key, _state_root, report = _prepared_activation_fixture(
        tmp_path, monkeypatch,
    )
    activate_prepared_contract_store(
        report, quiescence_guard=lambda: True,
    )
    foreign = dict(report)
    foreign["catalog"] = {
        "explicit:foreign/manifest.toml": next(iter(report["catalog"].values())),
    }
    reconciled: list[object] = []
    monkeypatch.setattr(
        i18n_pipeline,
        "reconcile_published_contract_registry",
        lambda revision, **_kwargs: reconciled.append(revision),
    )

    with pytest.raises(
        contract_store.ContractStoreError,
        match="activation_catalog_mismatch",
    ):
        activate_prepared_contract_store(
            foreign, quiescence_guard=lambda: True,
        )

    assert reconciled == []
