from __future__ import annotations

import threading
import json
import contextlib
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_bootstrap as bootstrap
import executor_birth_operational as operational
from executor_birth_intent import _producer_capabilities_for_bootstrap
from executor_birth_keystore import birth_key_id, raw_public_key
from executor_birth_intent import BirthIntent
from manifest_inventory import (
    ContractId, ManifestInventory, ManifestOrigin, ManifestRef, ManifestStatus,
)


def _store(root: Path, key: Ed25519PrivateKey) -> None:
    root.mkdir(mode=0o700); (root / "private").mkdir(mode=0o700); (root / "public").mkdir(mode=0o700)
    public = raw_public_key(key.public_key()); key_id = birth_key_id(public)
    for path, payload in (
        (root / "birth-keystore.lock", b"0"),
        (root / "public" / f"{key_id}.pub", public),
        (root / "private" / f"{key_id}.key", key.private_bytes_raw()),
    ):
        path.write_bytes(payload); path.chmod(0o600)
    config = {"active_key_id": key_id, "config_revision": 1,
              "keys": [{"key_id": key_id, "public_file": f"public/{key_id}.pub", "status": "active"}],
              "private_file": f"private/{key_id}.key", "schema_version": 1}
    (root / "keystore.json").write_bytes(json.dumps(config, sort_keys=True, separators=(",", ":")).encode())
    (root / "keystore.json").chmod(0o600)


def _producer_config(tmp_path: Path):
    result = {}
    for index, cap in enumerate(_producer_capabilities_for_bootstrap()):
        key = Ed25519PrivateKey.generate()
        name = f"producer-{index}"
        _store(tmp_path / name, key)
        # Provenance is no longer declared here: the author comes from the
        # closed table and the kind from where the manifest lives.
        result[f"{cap.producer_id}:{cap.operation}"] = {
            "issuer_id": cap.producer_id, "keystore": name,
        }
    return {"producers": result}


def test_registry_is_closed_and_capability_specific(tmp_path: Path) -> None:
    value = _producer_config(tmp_path)
    authorities, registry = bootstrap._load_authorities(value, tmp_path, forbidden_public_keys=())
    assert set(authorities) == set(_producer_capabilities_for_bootstrap())
    assert set(registry.entries) == {cap.producer_id for cap in authorities}
    forged = dict(value["producers"])
    forged["forged:publish"] = next(iter(forged.values()))
    with pytest.raises(bootstrap.BirthBootstrapError, match="registry_incomplete"):
        bootstrap._load_authorities({"producers": forged}, tmp_path, forbidden_public_keys=())


def test_admission_and_producer_keys_are_not_created_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing-store"
    from executor_birth_keystore import BirthKeyStoreError, load_birth_keystore
    with pytest.raises(BirthKeyStoreError, match="birth_keystore_unavailable"):
        load_birth_keystore(missing)
    assert not missing.exists()


def test_state_database_is_private_and_symlink_state_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / "state"
    db = bootstrap._secure_state_db(state)
    assert db.exists()
    if __import__("os").name != "nt":
        assert state.stat().st_mode & 0o077 == 0
        assert db.stat().st_mode & 0o077 == 0
    link = tmp_path / "state-link"
    try:
        link.symlink_to(state, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(bootstrap.BirthBootstrapError, match="state_permissions"):
        bootstrap._secure_state_db(link)


def test_manifest_ref_targets_authoring_inventory_not_candidate_staging(monkeypatch, tmp_path: Path) -> None:
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    authoring = tmp_path / "authoring" / "demo"
    staging = tmp_path / "stage"
    ref = ManifestRef(contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
                      authoring.parent, authoring / "manifest.toml", "demo/manifest.toml", (authoring,))
    import manifest_inventory
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: ManifestInventory((ref,), ()))
    resolved = bootstrap._manifest_ref(BirthIntent(staging, contract_id, "create"))
    assert resolved is ref
    assert resolved.manifest_dir != staging


def test_reused_key_cannot_forge_a_second_capability(tmp_path: Path) -> None:
    value = _producer_config(tmp_path)
    entries = value["producers"]
    names = list(entries)
    entries[names[1]]["keystore"] = entries[names[0]]["keystore"]
    with pytest.raises(Exception, match="reuses_author_identity|capability_key_reused"):
        bootstrap._load_authorities(value, tmp_path, forbidden_public_keys=())


def test_bootstrap_is_once_and_concurrent(monkeypatch, tmp_path: Path) -> None:
    sentinel = object()
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    monkeypatch.setattr(bootstrap, "_BOOT_STATE", "cold")
    monkeypatch.setattr(bootstrap, "_BOOT_ERROR", None)
    calls = []
    barrier = threading.Barrier(3)

    def build(_paths, *, now):
        calls.append(now)
        return sentinel

    monkeypatch.setattr(bootstrap, "_build", build)
    monkeypatch.setattr(bootstrap, "_install_birth_runtime_bundle",
                        lambda bundle: setattr(operational, "_RUNTIME_BUNDLE", bundle))
    results = []

    def run():
        barrier.wait()
        results.append(bootstrap.bootstrap_birth_runtime(
            bootstrap.BirthBootstrapPaths(tmp_path / "config", tmp_path / "state")))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert results == [sentinel, sentinel]
    assert len(calls) == 1


def test_failed_bootstrap_is_sticky_and_fail_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    monkeypatch.setattr(bootstrap, "_BOOT_STATE", "cold")
    monkeypatch.setattr(bootstrap, "_BOOT_ERROR", None)
    calls = 0

    def fail(_paths, *, now):
        nonlocal calls
        calls += 1
        raise bootstrap.BirthBootstrapError("missing")

    monkeypatch.setattr(bootstrap, "_build", fail)
    paths = bootstrap.BirthBootstrapPaths(tmp_path / "missing", tmp_path / "state")
    with pytest.raises(bootstrap.BirthBootstrapError, match="missing"):
        bootstrap.bootstrap_birth_runtime(paths)
    with pytest.raises(bootstrap.BirthBootstrapError, match="birth_bootstrap_failed"):
        bootstrap.bootstrap_birth_runtime(paths)
    assert calls == 1


def test_restart_reuses_already_installed_complete_bundle(monkeypatch, tmp_path: Path) -> None:
    sentinel = object()
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", sentinel)
    monkeypatch.setattr(bootstrap, "_BOOT_STATE", "cold")
    monkeypatch.setattr(
        bootstrap, "_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rebuilt")),
    )
    assert bootstrap.bootstrap_birth_runtime(
        bootstrap.BirthBootstrapPaths(tmp_path / "config", tmp_path / "state")
    ) is sentinel


def test_build_end_to_end_from_explicit_material_and_dedicated_keystores(monkeypatch, tmp_path: Path) -> None:
    admission_root = tmp_path / "admission"
    _store(admission_root, Ed25519PrivateKey.generate())
    producers = _producer_config(tmp_path)
    component_names = (
        "standard", "linter", "vocabulary", "authority_registry",
        "sandbox_registry", "property_catalog", "runner", "review_policy",
        "template_allowlist", "primitive_allowlist", "dependency_allowlist",
    )
    context = {name: {"version": "v1", "files": [], "configuration": {"name": name}}
               for name in component_names}
    semantic_key = Ed25519PrivateKey.generate()
    (tmp_path / "semantic.pub").write_bytes(semantic_key.public_key().public_bytes_raw())
    (tmp_path / "semantic.pub").chmod(0o600)
    (tmp_path / "semantic-evidence").mkdir(mode=0o700)
    evidence_kinds = ("deterministic_oracle", "human_case", "metamorphic_relation")
    semantic_review = {
        "evidence_dir": "semantic-evidence", "verifiers": {
            "semantic-v1": {"path": "semantic.pub", "status": "active"}
        },
        "versions": {kind: ["v1"] for kind in evidence_kinds},
        "owners": {kind: [f"owner:{kind}"] for kind in evidence_kinds},
    }
    approver_key = Ed25519PrivateKey.generate()
    import base64
    approval_registry = {
        "schema_version": 1, "revision": 1,
        "keys": {"approver-v1": base64.b64encode(
            approver_key.public_key().public_bytes_raw()).decode()},
        "actors": {"operator": {"key_ids": ["approver-v1"],
                                  "scopes": ["synthesized"]}},
    }
    (tmp_path / "approval-authority.json").write_bytes(
        json.dumps(approval_registry, sort_keys=True, separators=(",", ":")).encode()
    )
    (tmp_path / "approval-authority.json").chmod(0o600)
    value = {
        "schema_version": 1, "policy_version": "birth-policy-v1",
        "receipt_ttl_seconds": 3600, "admission": {"keystore": "admission"},
        "approval": {"db_path": "approval.sqlite",
                     "authority_registry": "approval-authority.json"},
        "producers": producers["producers"], "context": context,
        "semantic_review": semantic_review,
    }
    config = tmp_path / "bootstrap.json"
    config.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    import manifest_inventory, sign
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: ManifestInventory((), ()))
    monkeypatch.setattr(sign, "list_trusted_publics", lambda: [])
    bundle = bootstrap._build(
        bootstrap.BirthBootstrapPaths(config, tmp_path / "state"),
        now=lambda: datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    )
    assert set(bundle.producer_factories) == set(_producer_capabilities_for_bootstrap())
    assert bundle.core.producer_db.exists()
    assert bundle.core.admission_key_id in bundle.core.admission_verifier_keys
    assert bundle.core.context_epoch_resolver().startswith("sha256:")


def _recovery_fixture(monkeypatch, tmp_path: Path, *, current: str | None):
    import contract_store
    import executor_birth_authoring as authoring
    import executor_birth_receipts as receipts
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    canonical = tmp_path / "authoring" / "demo"
    ref = ManifestRef(contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
                      canonical.parent, canonical / "manifest.toml", "demo/manifest.toml", (canonical,))
    pending = SimpleNamespace(
        contract_id=contract_id.value, new_generation_id="sha256:" + "3" * 64,
        predecessor_generation_id="sha256:" + "2" * 64,
        request_id="sha256:" + "1" * 64, journal_hash="sha256:" + "4" * 64,
        candidate_id="sha256:" + "5" * 64, semantic_core_id="sha256:" + "6" * 64,
        admission_context_id="sha256:" + "7" * 64, new_tree_id="sha256:" + "8" * 64,
    )
    receipt = SimpleNamespace(
        contract_id=pending.contract_id, generation_id=pending.new_generation_id,
        birth_request_id=pending.request_id, authoring_journal_hash=pending.journal_hash,
        predecessor_id=pending.predecessor_generation_id, candidate_id=pending.candidate_id,
        semantic_core_id=pending.semantic_core_id,
        admission_context_id=pending.admission_context_id,
    )
    control = SimpleNamespace(canonical=canonical, lock=tmp_path / "lock")
    import manifest_inventory
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: ManifestInventory((ref,), ()))
    monkeypatch.setattr(authoring, "authoring_paths", lambda *_args: control)
    monkeypatch.setattr(authoring, "load_prepared_journal", lambda _control: pending)
    monkeypatch.setattr(authoring, "authoring_token", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(contract_store, "catalog_admission_lock", lambda **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(contract_store, "_writer_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    receipt_path = tmp_path / "receipt"; receipt_path.write_bytes(b"receipt")
    monkeypatch.setattr(contract_store, "_birth_receipt_path", lambda *_args: receipt_path)
    monkeypatch.setattr(contract_store, "_publication_base_locked",
                        lambda *_args, **_kwargs: (tmp_path, object(), current, {}))
    monkeypatch.setattr(receipts, "verify_admission_receipt", lambda *_args, **_kwargs: receipt)
    calls = []
    monkeypatch.setattr(authoring, "observe_tree", lambda _path: {"manifest.toml": b"x"})
    monkeypatch.setattr(authoring, "authoring_tree_id", lambda _tree: pending.new_tree_id)
    monkeypatch.setattr(authoring, "advance_version", lambda *_args: calls.append("advance"))
    monkeypatch.setattr(authoring, "cleanup_transaction", lambda *_args: calls.append("cleanup"))
    monkeypatch.setattr(authoring, "rollback_prepared", lambda *_args: calls.append("rollback"))
    return bootstrap._PostconditionAdapter(trusted_publics=(), verifier_keys={}), pending, calls


def test_recovery_rolls_forward_committed_pointer(monkeypatch, tmp_path: Path) -> None:
    adapter, pending, calls = _recovery_fixture(
        monkeypatch, tmp_path, current="sha256:" + "3" * 64,
    )
    adapter.recover_authoring()
    assert calls == ["advance", "cleanup"]


def test_recovery_rolls_back_predecessor_pointer(monkeypatch, tmp_path: Path) -> None:
    adapter, pending, calls = _recovery_fixture(
        monkeypatch, tmp_path, current="sha256:" + "2" * 64,
    )
    adapter.recover_authoring()
    assert calls == ["rollback"]


def test_recovery_blocks_ambiguous_pointer(monkeypatch, tmp_path: Path) -> None:
    adapter, _pending, calls = _recovery_fixture(
        monkeypatch, tmp_path, current="sha256:" + "9" * 64,
    )
    with pytest.raises(bootstrap.BirthBootstrapError, match="pointer_conflict"):
        adapter.recover_authoring()
    assert calls == []
