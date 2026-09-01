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
    import executor_birth_legacy_gate as legacy_gate
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root

    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: object(),
    )
    monkeypatch.setattr(legacy_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1",
        lambda: pytest.fail("loaded a required context without a head"),
    )

    with pytest.raises(
        bootstrap.BirthBootstrapError,
        match="birth_context_transition_required",
    ):
        bootstrap._required_context_runtime_for_bootstrap_v1()


def test_open_bootstrap_retains_the_initial_context_before_transition(
    monkeypatch,
) -> None:
    import executor_birth_legacy_gate as legacy_gate
    import executor_birth_ownership_chain as ownership_chain

    monkeypatch.setattr(
        ownership_chain, "inspect_ownership_chain_state_v1", lambda: object(),
    )
    monkeypatch.setattr(legacy_gate, "closed_build_enforcement", lambda: False)

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
    resolved = bootstrap._manifest_ref(BirthIntent(staging, contract_id, "create"))
    assert resolved is ref
    assert resolved.manifest_dir != staging


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
