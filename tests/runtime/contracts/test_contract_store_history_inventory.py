"""Complete raw receipt namespaces, without live-catalog or authority filters."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

import contract_store as store
from manifest_inventory import ContractId, ManifestOrigin


CONTEXT = "sha256:" + "c" * 64
GENERATION = "sha256:" + "a" * 64
RETIREMENT = "sha256:" + "b" * 64


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(store._C, "PATH_USER_STATE", state)
    root = state / store.STORE_RELATIVE
    root.mkdir(mode=0o700, parents=True)

    def make(name, *, retired=False, empty=False):
        contract_id = ContractId(ManifestOrigin.BUILTIN, name + "/manifest.toml")
        directory = root / store.contract_storage_key(contract_id)
        directory.mkdir(mode=0o700)
        (directory / store.BINDING_FILE).write_bytes(store.encode_binding(contract_id))
        generations = directory / "generations"
        generations.mkdir(mode=0o700)
        if not empty:
            generation = generations / store.generation_directory_name(GENERATION)
            generation.mkdir(mode=0o700)
            for filename in store.GENERATION_FILES:
                # Inventory shape is deliberately not authenticated content.
                (generation / filename).write_bytes(b"unverified revision")
            for context in (None, CONTEXT):
                receipt = (store._birth_receipt_path(directory, GENERATION) if context is None else
                           store._birth_receipt_path_v2(directory, GENERATION, context))
                receipt.parent.mkdir(mode=0o700, parents=True)
                # Equal bytes at two distinct physical locators must survive.
                receipt.write_bytes(b'{"outcome":"rejected"}')
            if retired:
                tombstone = generations / store.generation_directory_name(RETIREMENT)
                tombstone.mkdir(mode=0o700)
                for filename in store.RETIREMENT_FILES:
                    (tombstone / filename).write_bytes(b"unverified retirement")
                (directory / "current").write_bytes((RETIREMENT + "\n").encode())
                (directory / "writer.lock").write_bytes(b"0")
        # Deliberately leave non-retired histories without current or lock.
        for path in state.rglob("*"):
            path.chmod(0o700 if path.is_dir() else 0o600)
        return contract_id, directory

    contracts = (make("retired", retired=True), make("unreachable"), make("empty", empty=True))
    return root, contracts, make


def test_inventory_preserves_every_contract_layout_and_raw_act(inventory, monkeypatch):
    root, contracts, _ = inventory

    def forbidden(*args, **kwargs):
        pytest.fail("inventory entered a mutating, current or authenticating boundary")

    for name in ("current_contract", "_existing_contract_directory", "catalog_admission_lock",
                 "_writer_lock", "read_binding", "_load_revision", "_read_current_optional",
                 "_load_generation", "_read_regular_file", "read_historical_birth_evidence_v1"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(store._C, "ensure_dirs", forbidden)
    result = store.read_historical_birth_inventory_v1()
    assert result.source_path == root
    assert result.unbound_empty_namespaces == ()
    assert [item.contract_id for item in result.contracts] == [
        item[0] for item in sorted(contracts, key=lambda item: item[1].name)
    ]
    assert result.entry_count == len(tuple(root.rglob("*")))
    assert result.field_bytes == sum(len(item.binding_bytes) + sum(
        len(receipt.encoded) for receipt in item.receipts) for item in result.contracts)
    by_contract = {item.contract_id: item for item in result.contracts}
    for index, (contract_id, directory) in enumerate(contracts):
        item = by_contract[contract_id]
        assert item.binding_bytes == (directory / store.BINDING_FILE).read_bytes()
        assert item.generation_ids == (() if index == 2 else (GENERATION,))
        assert item.retirement_ids == ((RETIREMENT,) if index == 0 else ())
        assert tuple(receipt.admission_context_id for receipt in item.receipts) == (
            () if index == 2 else (None, CONTEXT))
        for receipt in item.receipts:
            assert receipt.generation_id == GENERATION
            assert receipt.encoded == b'{"outcome":"rejected"}'
            with pytest.raises(dataclasses.FrozenInstanceError):
                receipt.encoded = b"different"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.entry_count = 0


def test_empty_existing_root_is_explicit_but_missing_root_is_not(inventory, monkeypatch, tmp_path):
    state = tmp_path / "other-state"
    root = state / store.STORE_RELATIVE
    root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(store._C, "PATH_USER_STATE", state)
    result = store.read_historical_birth_inventory_v1()
    assert result.contracts == () and result.entry_count == result.field_bytes == 0
    root.rmdir()
    with pytest.raises(store.ContractStoreError, match="birth_history_inventory_invalid"):
        store.read_historical_birth_inventory_v1()
    assert not root.exists()


@pytest.mark.parametrize("kind", ["entries", "bytes"])
def test_exact_total_budget_succeeds_and_one_less_refuses_everything(inventory, kind):
    result = store.read_historical_birth_inventory_v1()
    field = "entry_count" if kind == "entries" else "field_bytes"
    limit = getattr(result, field)
    keyword = "max_" + kind
    assert store.read_historical_birth_inventory_v1(**{keyword: limit}) == result
    with pytest.raises(store.ContractStoreError, match="birth_history_(entry|byte)_limit"):
        store.read_historical_birth_inventory_v1(**{keyword: limit - 1})


@pytest.mark.parametrize("options", [
    {"max_entries": True}, {"max_entries": 0}, {"max_bytes": -1}, {"max_bytes": 1.5},
    {"timeout_seconds": False}, {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": "1"},
    pytest.param({"timeout_seconds": 10 ** 1000}, id="overflowing-timeout"),
])
def test_invalid_budget_is_rejected_before_filesystem(inventory, monkeypatch, options):
    monkeypatch.setattr(store, "_store_root", lambda *_: pytest.fail("filesystem selected"))
    with pytest.raises(store.ContractStoreError, match="birth_history_budget_invalid"):
        store.read_historical_birth_inventory_v1(**options)


@pytest.mark.parametrize("location", ["root", "contract", "generations", "generation", "v1", "v2", "contexts"])
def test_unknown_or_staging_namespace_entry_is_not_silently_skipped(inventory, location):
    root, contracts, _ = inventory
    directory = contracts[0][1]
    paths = {"root": root, "contract": directory, "generations": directory / "generations",
             "generation": directory / "generations" / store.generation_directory_name(GENERATION),
             "v1": directory / "admission-receipts", "v2": directory / "admission-receipts-v2",
             "contexts": directory / "admission-receipts-v2" / store.generation_directory_name(GENERATION)}
    (paths[location] / ".pending.tmp").write_bytes(b"not yet published")
    with pytest.raises(store.ContractStoreError, match="(birth_history_namespace_invalid|revision_structure)"):
        store.read_historical_birth_inventory_v1()


def test_unbound_residual_is_not_filtered_or_repaired(inventory):
    root, _, _ = inventory
    residual = root / ("f" * 64)
    residual.mkdir(mode=0o700)
    with pytest.raises(store.ContractStoreError, match="birth_history_namespace_invalid") as failure:
        store.read_historical_birth_inventory_v1()
    assert f"contract={residual.name};missing=binding.json,generations;unexpected=" == failure.value.detail
    assert tuple(residual.iterdir()) == ()


@pytest.mark.parametrize("kind", ["foreign", "missing", "deep-json"])
def test_invalid_structural_binding_prevents_partial_inventory(inventory, kind):
    _, contracts, _ = inventory
    path = contracts[0][1] / store.BINDING_FILE
    if kind == "missing":
        path.unlink()
    else:
        path.write_bytes(store.encode_binding(contracts[1][0]) if kind == "foreign" else b"[" * 2000)
    with pytest.raises(store.ContractStoreError, match="(binding_invalid|birth_history_namespace_invalid)"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.parametrize("context", [None, CONTEXT])
@pytest.mark.parametrize("generation", ["sha256:" + "f" * 64, RETIREMENT])
def test_receipt_for_absent_or_retired_generation_is_rejected(inventory, context, generation):
    directory = inventory[1][0][1]
    old = (store._birth_receipt_path(directory, GENERATION) if context is None else
           store._birth_receipt_path_v2(directory, GENERATION, context))
    new = (store._birth_receipt_path(directory, generation) if context is None else
           store._birth_receipt_path_v2(directory, generation, context))
    new.parent.mkdir(mode=0o700, exist_ok=True)
    old.rename(new)
    with pytest.raises(store.ContractStoreError, match="birth_history_generation_missing"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.parametrize("target", ["contract", "revision", "v1", "v2", "contexts", "receipt", "payload", "current", "lock"])
def test_link_at_any_traversed_level_is_refused(inventory, tmp_path, target):
    directory = inventory[1][0][1]
    v1 = store._birth_receipt_path(directory, GENERATION)
    v2 = store._birth_receipt_path_v2(directory, GENERATION, CONTEXT)
    revision = directory / "generations" / store.generation_directory_name(GENERATION)
    path = {"contract": directory, "revision": revision, "v1": v1.parent,
            "v2": v2.parent.parent, "contexts": v2.parent, "receipt": v1,
            "payload": revision / store.GENERATION_FILES[0],
            "current": directory / "current", "lock": directory / "writer.lock"}[target]
    saved = tmp_path / "saved"
    is_directory = path.is_dir()
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=is_directory)
    with pytest.raises(store.ContractStoreError, match="birth_history_(file|directory)_invalid"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.parametrize("target", ["binding", "receipt"])
def test_per_file_budget_is_enforced_before_payload_read(inventory, target):
    directory = inventory[1][0][1]
    path = (directory / store.BINDING_FILE if target == "binding" else
            store._birth_receipt_path(directory, GENERATION))
    with path.open("wb") as stream:
        stream.truncate((65536 if target == "binding" else 1024 * 1024) + 1)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_inventory_v1()


def test_empty_receipt_is_not_discarded(inventory):
    store._birth_receipt_path(inventory[1][0][1], GENERATION).write_bytes(b"")
    with pytest.raises(store.ContractStoreError, match="birth_receipt_invalid"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.parametrize("change", ["binding", "receipt", "payload", "current", "same-name-replacement"])
def test_change_after_content_read_is_detected_by_final_metadata(inventory, monkeypatch, change):
    directory = inventory[1][0][1]
    receipt = store._birth_receipt_path(directory, GENERATION)
    target = {"binding": directory / store.BINDING_FILE, "receipt": receipt,
              "payload": directory / "generations" / store.generation_directory_name(GENERATION) / store.GENERATION_FILES[0],
              "current": directory / "current", "same-name-replacement": receipt}[change]
    original = store._read_history_file_v1
    changed = False

    def read(path, **kwargs):
        nonlocal changed
        value = original(path, **kwargs)
        if path == receipt and not changed:
            old = target.read_bytes()
            if change == "same-name-replacement":
                target.unlink()
            target.write_bytes(old if change == "same-name-replacement" else old + b" ")
            target.chmod(0o600)
            changed = True
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    with pytest.raises(store.ContractStoreError, match="birth_history_source_changed"):
        store.read_historical_birth_inventory_v1()
    assert changed


@pytest.mark.parametrize("change", ["contract", "receipt", "generation"])
def test_added_member_after_initial_scan_prevents_partial_success(inventory, monkeypatch, change):
    root, contracts, make = inventory
    directory = contracts[0][1]
    receipt = store._birth_receipt_path(directory, GENERATION)
    original = store._read_history_file_v1
    changed = False

    def read(path, **kwargs):
        nonlocal changed
        value = original(path, **kwargs)
        if path == receipt and not changed:
            changed = True
            if change == "contract":
                make("added", empty=True)
            elif change == "receipt":
                new = store._birth_receipt_path(directory, "sha256:" + "f" * 64)
                new.write_bytes(value)
                new.chmod(0o600)
            else:
                (directory / "generations" / ("f" * 64)).mkdir(mode=0o700)
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    with pytest.raises(store.ContractStoreError, match="birth_history_source_changed"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.parametrize("phase", ["scan", "final"])
def test_deadline_is_enforced_during_initial_and_final_scans(inventory, monkeypatch, phase):
    root = inventory[0]
    original = store.os.scandir
    root_scans = 0
    expired = False
    monkeypatch.setattr(store.time, "monotonic", lambda: 100.0 if expired else 0.0)

    def scan(path):
        nonlocal root_scans, expired
        if Path(path) == root:
            root_scans += 1
            if root_scans == (1 if phase == "scan" else 2):
                expired = True
        return original(path)

    monkeypatch.setattr(store.os, "scandir", scan)
    with pytest.raises(store.ContractStoreError, match="birth_history_timeout"):
        store.read_historical_birth_inventory_v1(timeout_seconds=1)


def test_entry_budget_is_checked_while_iterating_not_after_sorting(inventory, monkeypatch):
    original = store.os.scandir
    seen = 0

    class LimitedScan:
        def __init__(self, path):
            self.entries = original(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.entries.close()

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal seen
            seen += 1
            assert seen <= 2, "inventory accumulated beyond its declared budget"
            return next(self.entries)

    monkeypatch.setattr(store.os, "scandir", LimitedScan)
    with pytest.raises(store.ContractStoreError, match="birth_history_entry_limit"):
        store.read_historical_birth_inventory_v1(max_entries=1)
    assert seen == 2


def test_growth_between_observation_and_read_cannot_exceed_remaining_bytes(inventory, monkeypatch):
    budget = store.read_historical_birth_inventory_v1().field_bytes
    original = store._read_history_file_v1
    remaining = budget
    materialized = 0
    attempted = False

    def growing_read(path, **kwargs):
        nonlocal remaining, materialized, attempted
        if path.name != store.BINDING_FILE and not attempted:
            attempted = True
            path.write_bytes(b"x" * (remaining + 1))
            value = original(path, **kwargs)
            materialized = len(value)
            return value
        value = original(path, **kwargs)
        remaining -= len(value)
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", growing_read)
    with pytest.raises(store.ContractStoreError, match="birth_history_(byte_limit|file_invalid)"):
        store.read_historical_birth_inventory_v1(max_bytes=budget)
    assert attempted
    assert materialized <= remaining


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; native ACL proof remains separate")
@pytest.mark.parametrize("target", ["directory", "payload"])
def test_unsafe_permissions_are_refused_without_repair(inventory, target):
    directory = inventory[1][0][1]
    path = directory if target == "directory" else (
        directory / "generations" / store.generation_directory_name(GENERATION) / store.GENERATION_FILES[0])
    mode = 0o770 if target == "directory" else 0o660
    path.chmod(mode)
    with pytest.raises(store.ContractStoreError, match="birth_history_(directory|file)_invalid"):
        store.read_historical_birth_inventory_v1()
    assert path.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("target", ["payload", "current"])
def test_hardlinked_metadata_only_file_is_refused(inventory, tmp_path, target):
    directory = inventory[1][0][1]
    path = directory / "current" if target == "current" else (
        directory / "generations" / store.generation_directory_name(GENERATION) / store.GENERATION_FILES[0])
    os.link(path, tmp_path / "hardlink")
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO boundary")
def test_nonregular_metadata_only_file_is_refused(inventory):
    path = inventory[1][0][1] / "current"
    path.unlink()
    os.mkfifo(path, mode=0o600)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_inventory_v1()


@pytest.fixture
def unbound_namespace(inventory):
    root = inventory[0]
    directory = root / ("f" * 64)
    directory.mkdir(mode=0o700)
    (directory / "generations").mkdir(mode=0o700)
    (directory / "writer.lock").write_bytes(b"\0")
    (directory / "writer.lock").chmod(0o600)
    return directory


def test_exact_empty_unbound_namespace_is_preserved_without_inferred_identity(
    inventory, unbound_namespace, monkeypatch,
):
    import manifest_inventory

    def forbidden(*args, **kwargs):
        pytest.fail("raw observation entered an operational lock/residual reader")

    monkeypatch.setattr(store, "_writer_lock", forbidden)
    monkeypatch.setattr(store, "catalog_admission_lock", forbidden)
    monkeypatch.setattr(manifest_inventory, "_is_empty_unbound_publication_residue_v1", forbidden)
    result = store.read_historical_birth_inventory_v1()
    assert len(result.contracts) == len(inventory[1])
    assert len(result.unbound_empty_namespaces) == 1
    item = result.unbound_empty_namespaces[0]
    assert item.storage_key == unbound_namespace.name
    assert item.lock_bytes == b"\0"
    assert not hasattr(item, "contract_id")
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.storage_key = "different"
    assert result.field_bytes == 1 + sum(len(contract.binding_bytes) + sum(
        len(receipt.encoded) for receipt in contract.receipts) for contract in result.contracts)
    assert result.entry_count == len(tuple(inventory[0].rglob("*")))
    assert store.read_historical_birth_inventory_v1(
        max_entries=result.entry_count, max_bytes=result.field_bytes,
    ) == result
    with pytest.raises(store.ContractStoreError, match="birth_history_byte_limit"):
        store.read_historical_birth_inventory_v1(max_bytes=result.field_bytes - 1)
    assert not (unbound_namespace / store.BINDING_FILE).exists()


@pytest.mark.parametrize("change", ["nonempty-generations", "current", "receipts", "non-null-lock", "large-lock", "missing-lock"])
def test_deviation_from_empty_unbound_shape_still_refuses_inventory(inventory, unbound_namespace, change):
    if change == "nonempty-generations":
        (unbound_namespace / "generations" / ("e" * 64)).mkdir(mode=0o700)
    elif change == "current":
        (unbound_namespace / "current").write_bytes(GENERATION.encode())
    elif change == "receipts":
        (unbound_namespace / "admission-receipts").mkdir(mode=0o700)
    elif change == "missing-lock":
        (unbound_namespace / "writer.lock").unlink()
    else:
        (unbound_namespace / "writer.lock").write_bytes(b"1" if change == "non-null-lock" else b"\0\0")
    with pytest.raises(store.ContractStoreError, match="birth_history_(namespace_invalid|unbound_invalid|file_invalid)"):
        store.read_historical_birth_inventory_v1()


@pytest.mark.skipif(os.name != "posix", reason="Exact POSIX mode observation; native ACL proof remains separate")
@pytest.mark.parametrize("target", ["root", "namespace", "generations", "lock"])
def test_empty_unbound_namespace_requires_exact_private_modes(inventory, unbound_namespace, target):
    path = {"root": inventory[0], "namespace": unbound_namespace,
            "generations": unbound_namespace / "generations", "lock": unbound_namespace / "writer.lock"}[target]
    mode = 0o640 if target == "lock" else 0o750
    path.chmod(mode)
    with pytest.raises(store.ContractStoreError, match="birth_history_unbound_invalid"):
        store.read_historical_birth_inventory_v1()
    assert path.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize("change", ["binding", "generation", "lock"])
def test_empty_unbound_namespace_change_after_read_is_not_ignored(inventory, unbound_namespace, monkeypatch, change):
    original = store._read_history_file_v1
    lock = unbound_namespace / "writer.lock"

    def read(path, **kwargs):
        value = original(path, **kwargs)
        if path == lock:
            if change == "binding":
                (unbound_namespace / store.BINDING_FILE).write_bytes(b"{}")
            elif change == "generation":
                (unbound_namespace / "generations" / ("e" * 64)).mkdir(mode=0o700)
            else:
                lock.write_bytes(b"1")
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    with pytest.raises(store.ContractStoreError, match="birth_history_source_changed"):
        store.read_historical_birth_inventory_v1()
