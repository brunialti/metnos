from __future__ import annotations

import threading
import json
import contextlib
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
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


def _sealed_producers(tmp_path: Path, *, shared_key=None):
    """The producer stores as the prepared set hands them over."""
    from executor_birth_keystore import load_birth_keystore
    from executor_birth_producer_table_v1 import producer_store_name_v1

    stores = {}
    for index, cap in enumerate(_producer_capabilities_for_bootstrap()):
        key = shared_key or Ed25519PrivateKey.generate()
        directory = tmp_path / f"producer-{index}"
        _store(directory, key)
        name = producer_store_name_v1(cap.producer_id, cap.operation)
        stores[name] = load_birth_keystore(directory)
    return type("Sealed", (), {"producers": stores})()


def test_registry_is_closed_and_capability_specific(tmp_path: Path) -> None:
    sealed = _sealed_producers(tmp_path)
    authorities, registry = bootstrap._sealed_authorities(sealed)
    assert set(authorities) == set(_producer_capabilities_for_bootstrap())
    assert set(registry.entries) == {cap.producer_id for cap in authorities}
    # A store the prepared set does not contain is a missing capability, not a
    # default: the registry is closed by the catalogue, not by a document.
    partial = type("Sealed", (), {"producers": dict(list(sealed.producers.items())[:-1])})()
    with pytest.raises(bootstrap.BirthBootstrapError, match="registry_incomplete"):
        bootstrap._sealed_authorities(partial)


def test_reused_key_cannot_forge_a_second_capability(tmp_path: Path) -> None:
    shared = Ed25519PrivateKey.generate()
    sealed = _sealed_producers(tmp_path, shared_key=shared)
    with pytest.raises(bootstrap.BirthBootstrapError, match="capability_key_reused"):
        bootstrap._sealed_authorities(sealed)


def test_required_context_preserves_chain_inspection_failure(monkeypatch) -> None:
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root

    def fail_inspection():
        raise ownership_chain.OwnershipChainError(
            "birth_ownership_recovery_required", "productive store",
        )

    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", fail_inspection,
    )
    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1",
        lambda: pytest.fail("loaded context after failed chain inspection"),
    )
    with pytest.raises(bootstrap.BirthBootstrapError) as failure:
        bootstrap._required_context_runtime_for_bootstrap_v1()
    assert failure.value.code == "birth_ownership_recovery_required"
    assert failure.value.detail == "productive store"


def test_closed_bootstrap_refuses_the_historical_context_without_a_head(
    monkeypatch,
) -> None:
    import executor_birth_authority_gate as authority_gate
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root

    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: object(),
    )
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1",
        lambda: pytest.fail("loaded a required context without a head"),
    )

    with pytest.raises(
        bootstrap.BirthBootstrapError,
        match="birth_context_transition_required",
    ):
        bootstrap._required_context_runtime_for_bootstrap_v1()
    monkeypatch.setattr(
        prepared_root, "load_sealed_authorities_v1",
        lambda: pytest.fail("closed bootstrap fell back to historical authorities"),
    )
    with pytest.raises(
        bootstrap.BirthBootstrapError,
        match="birth_context_transition_required",
    ):
        bootstrap._build_sealed(now=lambda: datetime.now(timezone.utc))


def test_open_bootstrap_retains_the_initial_context_before_transition(
    monkeypatch,
) -> None:
    import executor_birth_authority_gate as authority_gate
    import executor_birth_ownership_chain as ownership_chain

    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: object(),
    )
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: False)

    assert bootstrap._required_context_runtime_for_bootstrap_v1() is None


def test_required_context_preserves_context_load_failure(monkeypatch) -> None:
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root

    state = ownership_chain.VerifiedOwnershipChain("cutover", ())
    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: state,
    )

    def fail_load():
        raise ownership_chain.OwnershipChainError(
            "birth_ownership_authority_missing", "metnos",
        )

    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1", fail_load,
    )
    with pytest.raises(bootstrap.BirthBootstrapError) as failure:
        bootstrap._required_context_runtime_for_bootstrap_v1()
    assert failure.value.code == "birth_ownership_authority_missing"
    assert failure.value.detail == "metnos"


def test_required_context_preserves_prepared_root_failure(monkeypatch) -> None:
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root

    state = ownership_chain.VerifiedOwnershipChain("cutover", ())
    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: state,
    )

    def fail_load():
        raise prepared_root.PreparedRootError("birth_context_selection_changed")

    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1", fail_load,
    )
    with pytest.raises(bootstrap.BirthBootstrapError) as failure:
        bootstrap._required_context_runtime_for_bootstrap_v1()
    assert failure.value.code == "birth_context_selection_changed"
    assert failure.value.detail == ""


def test_manifest_ref_targets_authoring_inventory_not_candidate_staging(monkeypatch, tmp_path: Path) -> None:
    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    authoring = tmp_path / "authoring" / "demo"
    staging = tmp_path / "stage"
    ref = ManifestRef(contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
                      authoring.parent, authoring / "manifest.toml", "demo/manifest.toml", (authoring,))
    import manifest_inventory
    monkeypatch.setattr(manifest_inventory, "inventory_authoring_manifests",
                        lambda: ManifestInventory((ref,), ()))
    monkeypatch.setattr(
        manifest_inventory,
        "resolve_manifest_layout",
        lambda: manifest_inventory.ManifestLayout.AUTHORING,
    )
    resolved = bootstrap._manifest_ref(BirthIntent(staging, contract_id, "create"))
    assert resolved is ref
    assert resolved.manifest_dir != staging


def _empty_live_inventory(monkeypatch, tmp_path):
    import manifest_inventory as inventory
    monkeypatch.setattr(inventory, "resolve_manifest_layout", lambda: inventory.ManifestLayout.STORE_ONLY)
    monkeypatch.setattr(inventory, "inventory_store_manifests", lambda: ManifestInventory((), ()))
    monkeypatch.setattr(inventory._C, "PATH_USER_STATE", tmp_path / "state")
    monkeypatch.setattr(inventory, "inventory_authoring_manifests",
                        lambda: pytest.fail("live creation must not scan authoring"))
    return inventory


def test_first_core_destination_is_structural_and_does_not_create_an_admission(monkeypatch, tmp_path):
    inventory = _empty_live_inventory(monkeypatch, tmp_path)
    contract = ContractId(ManifestOrigin.CORE, "set_processes/manifest.toml")
    ref = bootstrap._manifest_ref(BirthIntent(tmp_path / "untrusted", contract, "first admission"))
    assert ref.manifest_path == tmp_path / "state/contract-authoring/v1/core/set_processes/manifest.toml"
    assert not ref.manifest_path.exists()
    assert ref.allowed_code_roots == (ref.source_root,)
    assert ref.manifest_hash is None and ref.name is None
    assert inventory.inventory_store_manifests().manifests == ()


@pytest.mark.parametrize("origin,relative", [
    (ManifestOrigin.EXPLICIT, "demo/manifest.toml"),
    (ManifestOrigin.RETIRED, "demo/manifest.toml"),
    (ManifestOrigin.CORE, "wrong/depth/manifest.toml"),
])
def test_first_destination_rejects_unmapped_retired_or_invalid_topology(monkeypatch, tmp_path, origin, relative):
    _empty_live_inventory(monkeypatch, tmp_path)
    with pytest.raises(bootstrap.BirthBootstrapError, match="birth_authoring_target_unavailable"):
        bootstrap._manifest_ref(BirthIntent(tmp_path / "stage", ContractId(origin, relative), "create"))


def test_existing_live_reference_is_not_replaced_by_a_prospective_destination(monkeypatch, tmp_path):
    inventory = _empty_live_inventory(monkeypatch, tmp_path)
    contract = ContractId(ManifestOrigin.CORE, "demo/manifest.toml")
    ref = inventory.prospective_manifest_ref(contract)
    monkeypatch.setattr(inventory, "inventory_store_manifests", lambda: ManifestInventory((ref,), ()))
    monkeypatch.setattr(inventory, "prospective_manifest_ref", lambda _: pytest.fail("existing reference replaced"))
    assert bootstrap._manifest_ref(BirthIntent(tmp_path / "stage", contract, "edit")) is ref


def test_first_destination_never_hides_a_corrupt_inventory(monkeypatch, tmp_path):
    inventory = _empty_live_inventory(monkeypatch, tmp_path)
    monkeypatch.setattr(inventory, "inventory_store_manifests", lambda: ManifestInventory((), (object(),)))
    with pytest.raises(bootstrap.BirthBootstrapError, match="birth_authoring_inventory_invalid"):
        bootstrap._manifest_ref(BirthIntent(tmp_path / "stage", ContractId(ManifestOrigin.CORE, "demo/manifest.toml"), "create"))


def test_bootstrap_is_once_and_concurrent(monkeypatch, tmp_path: Path) -> None:
    sentinel = object()
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", None)
    monkeypatch.setattr(bootstrap, "_BOOT_STATE", "cold")
    monkeypatch.setattr(bootstrap, "_BOOT_ERROR", None)
    calls = []
    barrier = threading.Barrier(3)

    def build(*, now):
        calls.append(now)
        return sentinel

    monkeypatch.setattr(bootstrap, "_build_sealed", build)
    monkeypatch.setattr(bootstrap, "_install_birth_runtime_bundle",
                        lambda bundle: setattr(operational, "_RUNTIME_BUNDLE", bundle))
    results = []

    def run():
        barrier.wait()
        results.append(bootstrap.bootstrap_birth_runtime())

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

    def fail(*, now):
        nonlocal calls
        calls += 1
        raise bootstrap.BirthBootstrapError("missing")

    monkeypatch.setattr(bootstrap, "_build_sealed", fail)
    with pytest.raises(bootstrap.BirthBootstrapError, match="missing"):
        bootstrap.bootstrap_birth_runtime()
    with pytest.raises(bootstrap.BirthBootstrapError, match="birth_bootstrap_failed"):
        bootstrap.bootstrap_birth_runtime()
    assert calls == 1


def test_restart_reuses_already_installed_complete_bundle(monkeypatch, tmp_path: Path) -> None:
    sentinel = object()
    monkeypatch.setattr(operational, "_RUNTIME_BUNDLE", sentinel)
    monkeypatch.setattr(bootstrap, "_BOOT_STATE", "cold")
    monkeypatch.setattr(
        bootstrap, "_build_sealed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rebuilt")),
    )
    assert bootstrap.bootstrap_birth_runtime() is sentinel


@pytest.mark.parametrize("changed", (False, True))
@pytest.mark.parametrize("receipt_present", (False, True))
def test_transition_authenticates_current_without_reusing_v1_receipts(
    monkeypatch, tmp_path: Path, changed: bool, receipt_present: bool,
) -> None:
    import contract_store
    import executor_birth_prepared_root as prepared_root
    import manifest_inventory
    import skill_registry
    from contract_bootstrap import ProductionStoreMode

    contract_id = ContractId(ManifestOrigin.CORE, "sample/manifest.toml")
    ref = ManifestRef(
        contract_id, ManifestOrigin.CORE, ManifestStatus.ADMITTED,
        tmp_path, tmp_path / "sample" / "manifest.toml",
        "sample/manifest.toml", (tmp_path,),
    )
    inventory = ManifestInventory((ref,), ())
    first = "sha256:" + "1" * 64
    second = "sha256:" + ("2" if changed else "1") * 64
    observed = iter((first, second))
    monkeypatch.setattr(
        contract_store, "production_store_mode",
        lambda: ProductionStoreMode.STORE_ONLY,
    )
    monkeypatch.setattr(
        contract_store, "materialize_repository_authoring_for_transition_v1",
        lambda **_kwargs: pytest.fail(
            "administrative verification attempted authoring materialization"
        ),
    )
    monkeypatch.setattr(
        contract_store, "current_manifest",
        lambda *_args, **_kwargs: SimpleNamespace(generation_id=next(observed)),
    )
    observed_policy = []

    def owned_skill_snapshot(owner):
        observed_policy.append(("snapshot", owner))
        return lambda name: observed_policy.append((name, owner)) or True

    def store_inventory(*, skill_enabled=None):
        assert skill_enabled is not None
        assert skill_enabled("github") is True
        return inventory

    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 991)
    monkeypatch.setattr(bootstrap.os, "getegid", lambda: 0)
    monkeypatch.setattr(
        skill_registry, "_skill_enabled_snapshot_for_owner_v1",
        owned_skill_snapshot,
    )
    monkeypatch.setattr(
        manifest_inventory, "inventory_store_manifests", store_inventory,
    )
    monkeypatch.setattr(
        prepared_root, "_load_historical_transition_verifiers_v1",
        lambda: SimpleNamespace(
            prepared=object(), author_verifier_keys={},
            admission_verifier_keys={},
        ),
    )
    monkeypatch.setattr(
        prepared_root, "load_sealed_authorities_v1",
        lambda: pytest.fail("deferred receipt verification selected a runtime"),
    )
    monkeypatch.setattr(
        bootstrap, "_transition_historical_receipt_v1",
        lambda *_args, **_kwargs:
        b"historical-receipt" if receipt_present else None,
    )
    operation = lambda: bootstrap.verify_initial_installer_store_v1(
        prove_quiescent=lambda: True,
        trusted_authoring_owner=(991, 991),
        defer_v1_receipts_to_transition_v2=True,
    )
    if changed:
        with pytest.raises(
            bootstrap.BirthBootstrapError,
            match="birth_initial_catalog_changed",
        ):
            operation()
    else:
        assert operation() == {
            "contracts": 1,
            "receipts": int(receipt_present),
        }
    assert observed_policy == [
        ("snapshot", (991, 991)),
        ("github", (991, 991)),
    ]


def test_nondeferred_initial_verification_keeps_strict_context_validation(
    monkeypatch,
) -> None:
    import contract_store
    import executor_birth_prepared_root as prepared_root
    from contract_bootstrap import ProductionStoreMode
    from executor_birth_prepared_set import PreparedSetError

    monkeypatch.setattr(
        contract_store, "production_store_mode",
        lambda: ProductionStoreMode.STORE_ONLY,
    )
    monkeypatch.setattr(
        prepared_root, "_load_historical_transition_verifiers_v1",
        lambda: pytest.fail("ordinary initial verification selected history"),
    )

    def mismatched_context():
        raise PreparedSetError("birth_prepared_set_mismatch")

    monkeypatch.setattr(
        prepared_root, "load_sealed_authorities_v1", mismatched_context,
    )
    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        bootstrap.verify_initial_installer_store_v1(prove_quiescent=lambda: True)


def test_transition_allows_only_an_absent_historical_receipt(
    monkeypatch, tmp_path: Path,
) -> None:
    import contract_store

    ref = SimpleNamespace(
        contract_id=ContractId(ManifestOrigin.CORE, "sample/manifest.toml"),
    )
    generation_id = "sha256:" + "1" * 64
    monkeypatch.setattr(
        contract_store, "_existing_contract_directory",
        lambda *_args, **_kwargs: tmp_path,
    )

    assert bootstrap._transition_historical_receipt_v1(
        ref, generation_id, store_root=None, admission_verifiers={},
    ) is None

    receipt = contract_store._birth_receipt_path(tmp_path, generation_id)
    receipt.parent.mkdir()
    receipt.mkdir()
    with pytest.raises(
        bootstrap.BirthBootstrapError,
        match="birth_initial_receipt_invalid",
    ):
        bootstrap._transition_historical_receipt_v1(
            ref, generation_id, store_root=None, admission_verifiers={},
        )


def test_the_sealed_build_refuses_without_a_prepared_set(monkeypatch, tmp_path: Path) -> None:
    """There is no free-form path left: without a prepared set nothing is built.

    The previous end-to-end test drove `_build` on material declared by a
    configuration document.  That path is gone — it could not publish anyway,
    lacking the author key — so what has to be proven now is that its absence
    is a refusal and not a fallback.
    """
    import config as runtime_config
    from executor_birth_prepared_root import PreparedRootError

    monkeypatch.setattr(runtime_config, "PATH_USER_CONFIG", tmp_path / "absent")
    assert not hasattr(bootstrap, "_build")
    assert not hasattr(bootstrap, "_load_authorities")
    assert not hasattr(bootstrap, "_context_builder")
    with pytest.raises((PreparedRootError, bootstrap.BirthBootstrapError)):
        bootstrap._build_sealed(now=lambda: NOW)


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
    monkeypatch.setattr(manifest_inventory, "inventory_manifests",
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


def test_recovery_without_a_pending_journal_does_not_create_store_entries(
    monkeypatch, tmp_path: Path,
) -> None:
    import contract_store
    import executor_birth_authoring as authoring
    import manifest_inventory

    contract_id = ContractId(ManifestOrigin.USER, "demo/manifest.toml")
    canonical = tmp_path / "authoring" / "demo"
    ref = ManifestRef(
        contract_id, ManifestOrigin.USER, ManifestStatus.ADMITTED,
        canonical.parent, canonical / "manifest.toml", "demo/manifest.toml",
        (canonical,),
    )
    control = SimpleNamespace(canonical=canonical, lock=tmp_path / "lock")
    monkeypatch.setattr(
        manifest_inventory, "inventory_manifests",
        lambda: ManifestInventory((ref,), ()),
    )
    monkeypatch.setattr(authoring, "authoring_paths", lambda *_args: control)
    monkeypatch.setattr(authoring, "load_prepared_journal", lambda _control: None)
    monkeypatch.setattr(
        authoring, "authoring_token",
        lambda *_args, **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        contract_store, "catalog_admission_lock",
        lambda **_kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        contract_store, "_writer_lock",
        lambda *_args, **_kwargs: pytest.fail("created an empty store entry"),
    )

    adapter = bootstrap._PostconditionAdapter(
        trusted_publics=(), verifier_keys={},
    )
    adapter.recover_authoring()


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


def test_the_gate_asks_whether_anything_is_prepared(monkeypatch, tmp_path: Path) -> None:
    """An installation with no prepared set continues; it does not try to boot.

    The question is answered by looking for the marker, never by reading a
    refusal: an absent root and a failed read share one input/output code, so
    inferring the inactive state from it would hide a real fault.
    """
    import config as runtime_config

    monkeypatch.setattr(runtime_config, "PATH_USER_CONFIG", tmp_path / "config")
    monkeypatch.setattr(
        bootstrap, "bootstrap_birth_runtime",
        lambda **_kwargs: pytest.fail("booted without a prepared set"),
    )
    assert bootstrap.birth_authority_is_prepared_v1() is False
    bootstrap.require_birth_runtime_before_workers()


def test_the_gate_lets_a_failure_through_once_a_set_is_prepared(
        monkeypatch, tmp_path: Path) -> None:
    """With a set on disk every activation failure stays fatal."""
    import config as runtime_config
    from executor_birth_prepared_set import MARKER_BASENAME_V1

    root = tmp_path / "config" / bootstrap.BIRTH_STATE_BASENAME_V1
    root.mkdir(mode=0o700, parents=True)
    (root / MARKER_BASENAME_V1).write_bytes(b"{}")
    monkeypatch.setattr(runtime_config, "PATH_USER_CONFIG", tmp_path / "config")

    def refuse(**_kwargs):
        raise bootstrap.BirthBootstrapError("birth_prepared_set_mismatch")

    monkeypatch.setattr(bootstrap, "bootstrap_birth_runtime", refuse)
    assert bootstrap.birth_authority_is_prepared_v1() is True
    with pytest.raises(bootstrap.BirthBootstrapError,
                       match="birth_prepared_set_mismatch"):
        bootstrap.require_birth_runtime_before_workers()


def test_the_durable_databases_are_created_private(tmp_path: Path) -> None:
    """Receipts and approvals live under the state directory, both private."""
    import os
    import stat as stat_module

    state = bootstrap._secure_state_dir(tmp_path / "state" / "birth")
    receipts = bootstrap._secure_state_db(
        state, bootstrap.PRODUCER_RECEIPTS_BASENAME_V1,
    )
    approvals = bootstrap._secure_state_db(
        state, bootstrap.APPROVALS_BASENAME_V1,
    )
    assert receipts.parent == state and approvals.parent == state
    assert receipts != approvals
    if os.name != "nt":
        assert stat_module.S_IMODE(state.stat().st_mode) & 0o077 == 0
        for path in (receipts, approvals):
            assert stat_module.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_a_loose_state_directory_is_refused(tmp_path: Path) -> None:
    """A directory others can reach is a refusal, not something to repair."""
    import os

    if os.name == "nt":
        pytest.skip("POSIX permission bits do not carry the same meaning here")
    state = tmp_path / "state" / "birth"
    state.mkdir(mode=0o755, parents=True)
    with pytest.raises(bootstrap.BirthBootstrapError,
                       match="birth_state_permissions"):
        bootstrap._secure_state_dir(state)


def _release_selection(build: str, *, transition_request: str = "1", staged=False):
    """Use nominal selection evidence, with explicitly test-sealed builds."""
    from executor_birth_context_selection import (
        _context_selection_for_staged_reattestation_v1,
        _context_selection_from_required_chain_v1,
    )
    from executor_birth_context_transition import issue_context_transition_v1
    from executor_birth_cutover import CurrentReceiptProof
    from executor_birth_distribution_manifest import _verified_distribution_for_test
    from executor_birth_identity import admission_context_id
    from executor_birth_ownership_preflight import _sealed_build_identity_for_test
    from tests.portable.test_executor_birth_context_selection import _evidence, _prepared_with
    from tests.runtime.executors.test_executor_birth_operational import D, _context

    previous, prepared, _distribution = _evidence()
    prepared = _prepared_with(
        prepared, prepared_admission_context_id=admission_context_id(_context()),
        prepared_context_epoch=D,
    )
    build_id = "sha256:" + build * 64
    _, transition = issue_context_transition_v1(
        request_id="sha256:" + transition_request * 64,
        closed_build_id=build_id,
        previous_cutover_id=previous.previous_cutover_id,
        previous_set_id=previous.previous_set_id,
        previous_admission_context_id=previous.previous_admission_context_id,
        previous_context_epoch=previous.previous_context_epoch,
        set_id=prepared.set_id,
        prepared_admission_context_id=prepared.prepared_admission_context_id,
        prepared_context_epoch=prepared.prepared_context_epoch,
        context_material_sha256=prepared.context_material_sha256,
        set_json_sha256=prepared.set_json_sha256,
        current_inventory=CurrentReceiptProof((), {}).inventory,
    )
    distribution = _verified_distribution_for_test(
        _sealed_build_identity_for_test(build_id, "sha256:" + "d" * 64, "closed-v1"),
        previous_closed_build_id=None, release_sequence=1,
        encoded=b"distribution", signature=b"s" * 64,
    )
    mint = (_context_selection_for_staged_reattestation_v1 if staged
            else _context_selection_from_required_chain_v1)
    return mint(transition, prepared, distribution)


@pytest.fixture
def release_factory(monkeypatch, tmp_path):
    """Real factory/issuance and bootstrap wiring; no live authority or store."""
    import executor_birth_prepared_root as prepared_root
    from executor_birth_identity import admission_context_id
    from executor_birth_intent import _STACK_RECONCILE
    from executor_birth_predecessor import AdmissionContextPin
    from tests.runtime.executors.test_executor_birth_operational import D, NOW, _candidate, _context

    sealed = _sealed_producers(tmp_path)
    sealed.author = SimpleNamespace(verifier_keys={})
    authorities, registry = bootstrap._sealed_authorities(sealed)
    candidate = _candidate(tmp_path)
    contract = ContractId(ManifestOrigin.CORE, "consult_frontier/manifest.toml")
    ref = ManifestRef(
        contract, ManifestOrigin.CORE, ManifestStatus.ADMITTED,
        candidate, candidate / "manifest.toml", "consult_frontier/manifest.toml", (candidate,),
    )
    context = _context()
    pin = AdmissionContextPin(admission_context_id(context), D)
    state = SimpleNamespace(selection=None, instant=NOW)
    assembly = SimpleNamespace(
        authorities=authorities, registry=registry,
        producer_db=tmp_path / "producer.sqlite", ttl_seconds=3600,
        now=lambda: state.instant,
        context_builder=SimpleNamespace(preview=lambda _intent: (context, pin)),
        core=object(),
    )
    monkeypatch.setattr(bootstrap, "_manifest_ref", lambda _intent: ref)
    monkeypatch.setattr(
        bootstrap, "_required_context_runtime_for_bootstrap_v1",
        lambda: None if state.selection is None else SimpleNamespace(
            authorities=sealed, selection=state.selection,
        ),
    )
    monkeypatch.setattr(prepared_root, "load_sealed_authorities_v1", lambda: sealed)
    monkeypatch.setattr(bootstrap, "_prepare_sealed_birth_assembly_v1", lambda *_args, **_kw: assembly)
    monkeypatch.setattr(bootstrap, "_reattestation_factory_for_assembly_v1", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        bootstrap, "_assemble_birth_runtime_bundle",
        lambda _core, factories, _reattestation, **_kw: SimpleNamespace(producer_factories=factories),
    )

    def factory(selection, capability=_STACK_RECONCILE):
        state.selection = selection
        return bootstrap._build_sealed(now=assembly.now).producer_factories[capability]

    return SimpleNamespace(
        factory=factory, assembly=assembly, state=state, context=context, pin=pin,
        intent=BirthIntent(candidate, contract, "publish changed executors"),
    )


def test_real_release_request_factory_accepts_a_first_admission(monkeypatch, tmp_path, request):
    # Do not stub the destination lookup: that hid the production refusal.
    lookup = bootstrap._manifest_ref
    fixture = request.getfixturevalue("release_factory")
    monkeypatch.setattr(bootstrap, "_manifest_ref", lookup)
    _empty_live_inventory(monkeypatch, tmp_path)
    result = fixture.factory(_release_selection("2"))(fixture.intent)
    assert result.candidate_source_root == fixture.intent.candidate_source_root
    assert result.manifest_ref.manifest_path == tmp_path / "state/contract-authoring/v1/core/consult_frontier/manifest.toml"


@pytest.mark.parametrize("elapsed", (600, 7200))
def test_release_request_is_stable_for_the_same_build_id(release_factory, elapsed):
    fixture = release_factory
    first_selection = _release_selection("2")
    second_selection = _release_selection("2", transition_request="3")
    assert first_selection is not second_selection
    assert first_selection.distribution is not second_selection.distribution
    assert first_selection.transition_id != second_selection.transition_id
    first_factory = fixture.factory(first_selection)
    first = first_factory(fixture.intent)
    fixture.state.instant += timedelta(seconds=elapsed)
    second_factory = fixture.factory(second_selection)
    assert first_factory is not second_factory
    second = second_factory(fixture.intent)
    assert second.request_id == first.request_id
    assert second.producer_receipt == first.producer_receipt


@pytest.mark.parametrize("capability", _producer_capabilities_for_bootstrap(),
                         ids=lambda cap: f"{cap.producer_id}:{cap.operation}")
def test_only_stack_release_requests_change_between_builds(release_factory, capability):
    from executor_birth_intent import _STACK_RECONCILE
    from executor_birth_receipts import verify_producer_receipt

    fixture = release_factory
    requests = [fixture.factory(selection, capability)(fixture.intent)
                for selection in (None, _release_selection("2"), _release_selection("3"))]
    receipts = [verify_producer_receipt(
        request.producer_receipt, registry=fixture.assembly.registry, now=fixture.state.instant,
    ) for request in requests]
    legacy_objective = bootstrap._hash(
        b"metnos.executor-birth.objective/v1\0", fixture.intent.reason,
        *fixture.intent.approval_refs,
    )
    assert receipts[0].objective_hash == legacy_objective
    assert len({receipt.candidate_source_id for receipt in receipts}) == 1
    if capability is _STACK_RECONCILE:
        assert len({request.request_id for request in requests}) == 3
        assert len({receipt.objective_hash for receipt in receipts}) == 3
    else:
        assert len({request.request_id for request in requests}) == 1
        assert len({request.producer_receipt for request in requests}) == 1


@pytest.mark.parametrize("staged", (False, True))
def test_release_factory_refuses_nonrequired_selections(release_factory, staged):
    selection = _release_selection("2", staged=staged)
    if not staged:
        selection = SimpleNamespace(**{
            field: getattr(selection, field) for field in selection.__dataclass_fields__
        })
    with pytest.raises(bootstrap.BirthBootstrapError, match="birth_context_selection_invalid"):
        release_factory.factory(selection)
    assert not release_factory.assembly.producer_db.exists()


def test_release_scope_requires_capability_identity_not_matching_fields(release_factory):
    from executor_birth_intent import _STACK_RECONCILE

    fixture = release_factory
    authority = fixture.assembly.authorities[_STACK_RECONCILE]
    lookalike = SimpleNamespace(
        producer_id=_STACK_RECONCILE.producer_id, operation=_STACK_RECONCILE.operation,
        _seal=_STACK_RECONCILE._seal,
    )
    legacy = fixture.factory(None)(fixture.intent)
    fixture.assembly.authorities = {
        _STACK_RECONCILE: replace(authority, capability=lookalike),
    }
    for selection in (_release_selection("2"), _release_selection("3")):
        request = fixture.factory(selection)(fixture.intent)
        assert request.request_id == legacy.request_id
        assert request.producer_receipt == legacy.producer_receipt


def _producer_rows(db_path, request_id):
    """Read complete rows, including receipt and signed terminal bytes, in a fixture DB."""
    with sqlite3.connect(db_path) as db:
        return (
            db.execute("SELECT * FROM birth_producer_issuance WHERE request_id=?", (request_id,)).fetchone(),
            db.execute("SELECT * FROM birth_producer_receipts WHERE request_id=?", (request_id,)).fetchone(),
        )


@pytest.mark.parametrize("legacy", (True, False), ids=("historical-unscoped", "release-scoped"))
def test_new_build_can_claim_after_expired_terminal_staging_rejection(release_factory, tmp_path, legacy):
    from contract_store import ContractStoreError
    from executor_birth_producer_store import ProducerReceiptBinding, claim_producer_receipt
    from executor_birth_receipts import ReceiptError, verify_producer_receipt
    from executor_birth_shadow import _sealed_dependencies_for_test

    fixture = release_factory
    first = fixture.factory(None if legacy else _release_selection("2"))(fixture.intent)
    admission_key = Ed25519PrivateKey.generate()

    def staging_invalid(_request):
        raise ContractStoreError("staging_invalid")

    core = operational._sealed_core_for_test(
        producer_registry=fixture.assembly.registry, producer_db=fixture.assembly.producer_db,
        context_resolver=lambda _request: (fixture.context, fixture.pin),
        context_epoch_resolver=lambda: fixture.pin.context_epoch,
        predecessor_resolver=staging_invalid,
        shadow_dependencies=_sealed_dependencies_for_test(),
        admission_private_key=admission_key, admission_public_key=admission_key.public_key(),
        admission_key_id="admission", policy_version="birth-policy-v1",
        now=fixture.assembly.now,
        publisher=lambda *_args, **_kw: pytest.fail("publication started before predecessor validation"),
        publisher_options={"store_root": tmp_path / "store"},
    )
    rejected = operational._birth_executor_for_test(first, _core=core)
    assert rejected.error_code == "staging_invalid"
    old_rows = _producer_rows(fixture.assembly.producer_db, first.request_id)
    assert all(row is not None for row in old_rows)
    with sqlite3.connect(fixture.assembly.producer_db) as db:
        assert db.execute(
            "SELECT state,rejection_code,terminal_envelope IS NOT NULL,terminal_auth IS NOT NULL "
            "FROM birth_producer_receipts WHERE request_id=?", (first.request_id,),
        ).fetchone() == ("rejected", "staging_invalid", 1, 1)
    first_receipt = verify_producer_receipt(
        first.producer_receipt, registry=fixture.assembly.registry, now=fixture.state.instant,
    )
    fixture.state.instant += timedelta(hours=2)
    with pytest.raises(ReceiptError, match="producer_receipt_expired"):
        verify_producer_receipt(
            first.producer_receipt, registry=fixture.assembly.registry, now=fixture.state.instant,
        )
    second = fixture.factory(_release_selection("3"))(fixture.intent)
    assert second.request_id != first.request_id
    second_receipt = verify_producer_receipt(
        second.producer_receipt, registry=fixture.assembly.registry, now=fixture.state.instant,
    )
    assert second_receipt.candidate_source_id == first_receipt.candidate_source_id
    assert second_receipt.receipt_id != first_receipt.receipt_id
    assert second_receipt.expires_at > first_receipt.expires_at
    with sqlite3.connect(fixture.assembly.producer_db) as db:
        assert db.execute(
            "SELECT state FROM birth_producer_receipts WHERE receipt_id=?", (second_receipt.receipt_id,),
        ).fetchone() == ("available",)
    claim = claim_producer_receipt(
        second.producer_receipt, registry=fixture.assembly.registry,
        binding=ProducerReceiptBinding(
            second_receipt.objective_hash, second_receipt.candidate_source_id,
            second_receipt.executor_origin, second_receipt.revision_authorship,
        ),
        request_id=second.request_id, now=fixture.state.instant, db_path=fixture.assembly.producer_db,
    )
    assert claim.state == "in_progress"
    assert claim.request_id == second.request_id
    assert _producer_rows(fixture.assembly.producer_db, first.request_id) == old_rows
