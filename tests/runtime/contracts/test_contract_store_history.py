"""Exact historical acquisition without current policy or publication rights."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

import contract_store as store
from manifest_inventory import ContractId, ManifestOrigin


CONTRACT = ContractId(ManifestOrigin.BUILTIN, "example/manifest.toml")
CONTEXT = "sha256:" + "1" * 64


@pytest.fixture
def history(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(store._C, "PATH_USER_STATE", state)
    root = state / store.STORE_RELATIVE
    contract = root / store.contract_storage_key(CONTRACT)
    contract.mkdir(mode=0o700, parents=True)
    binding = store.encode_binding(CONTRACT)
    (contract / store.BINDING_FILE).write_bytes(binding)
    payloads = {
        "manifest.toml": b'name="historical"\n',
        "manifest.toml.sig": b"x" * 64,
        "manifest.lang_state.json": b'{"old":"schema"}',
    }
    identifier = store.generation_id(payloads)
    generation = contract / "generations" / store.generation_directory_name(identifier)
    generation.mkdir(mode=0o700, parents=True)
    for name, payload in payloads.items():
        (generation / name).write_bytes(payload)
    receipts = (
        store._birth_receipt_path(contract, identifier),
        store._birth_receipt_path_v2(contract, identifier, CONTEXT),
    )
    for index, path in enumerate(receipts):
        path.parent.mkdir(mode=0o700, parents=True)
        path.write_bytes(f"unverified-receipt-{index}".encode())
    # An unrelated pointer, no authoring source and no journal are intentional.
    (contract / "current").write_bytes(b"sha256:" + b"f" * 64 + b"\n")
    # mkdir(parents=True) uses the process umask for intermediate directories;
    # reproduce the product's private creation modes, not the developer umask.
    for path in state.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    return contract, generation, identifier, receipts, payloads


@pytest.mark.parametrize("context,index", [(None, 0), (CONTEXT, 1)])
def test_exact_history_returns_raw_immutable_bytes_without_live_authority(history, monkeypatch, context, index):
    contract, _, identifier, receipts, payloads = history

    def forbidden(*args, **kwargs):
        pytest.fail("historical acquisition entered a current or mutating owner")

    for name in ("_existing_contract_directory", "read_binding", "_generation_payloads",
                 "current_contract", "_load_revision", "catalog_admission_lock", "_writer_lock",
                 "_authenticate_payloads", "_verify_payloads", "_read_regular_file"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(store._C, "ensure_dirs", forbidden)
    result = store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=context)
    assert result.contract_id == CONTRACT
    assert result.generation_id == identifier
    assert result.admission_context_id == context
    assert result.binding_bytes == (contract / store.BINDING_FILE).read_bytes()
    assert result.receipt_bytes == receipts[index].read_bytes()
    assert result.manifest_bytes == payloads["manifest.toml"]
    assert result.signature_bytes == payloads["manifest.toml.sig"]
    assert result.language_state_bytes == payloads["manifest.lang_state.json"]
    assert not (contract / "writer.lock").exists()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.receipt_bytes = b"replacement"


@pytest.mark.parametrize("context,index", [(None, 0), (CONTEXT, 1)])
def test_missing_selected_receipt_never_falls_back_to_other_layout(history, context, index):
    _, _, identifier, receipts, _ = history
    receipts[index].unlink()
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=context)
    assert receipts[1 - index].exists()


@pytest.mark.parametrize("context", [False, "", "sha256:" + "f" * 63, "../outside"])
def test_invalid_context_is_refused_before_filesystem_access(history, monkeypatch, context):
    monkeypatch.setattr(store, "_history_directory_identities_v1", lambda *_: pytest.fail("filesystem read"))
    with pytest.raises(store.ContractStoreError, match="admission_context_id_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, history[2], admission_context_id=context)


@pytest.mark.parametrize("target", ["binding", "receipt", *store.GENERATION_FILES])
def test_oversized_file_is_refused_without_repair(history, target):
    contract, generation, identifier, receipts, _ = history
    path = (contract / store.BINDING_FILE if target == "binding" else
            receipts[1] if target == "receipt" else generation / target)
    limit = 65536 if target == "binding" else 64 if target.endswith(".sig") else 1024 * 1024
    with path.open("wb") as stream:
        stream.truncate(limit + 1)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)
    assert path.stat().st_size == limit + 1


@pytest.mark.parametrize("target", ["receipt", *store.GENERATION_FILES])
def test_linked_file_is_refused(history, target):
    _, generation, identifier, receipts, _ = history
    path = receipts[1] if target == "receipt" else generation / target
    saved = path.with_name(path.name + ".saved")
    path.rename(saved)
    path.symlink_to(saved)
    # Keep the generation's expected shape, so the file check is reached.
    if target != "receipt":
        moved = generation.parent / saved.name
        saved.rename(moved)
        path.unlink()
        path.symlink_to(moved)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


def test_hardlinked_receipt_is_refused(history):
    contract, _, identifier, receipts, _ = history
    os.link(receipts[1], contract / "receipt-alias")
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


def test_wrong_binding_is_not_inferred_from_directory_name(history):
    contract, _, identifier, _, _ = history
    other = ContractId(ManifestOrigin.BUILTIN, "different/manifest.toml")
    (contract / store.BINDING_FILE).write_bytes(store.encode_binding(other))
    with pytest.raises(store.ContractStoreError, match="binding_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


@pytest.mark.parametrize("payload", [b"[" * 2000, b'{"contract_id":"\\ud800","schema_version":1}', b"not JSON"],
                         ids=["deep-nesting", "surrogate", "invalid-json"])
def test_malformed_binding_has_a_declared_error(history, payload):
    contract, _, identifier, _, _ = history
    (contract / store.BINDING_FILE).write_bytes(payload)
    with pytest.raises(store.ContractStoreError, match="binding_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


@pytest.mark.parametrize("change", ["extra", "missing", "changed-bytes", "short-signature", "empty-receipt"])
def test_incomplete_or_changed_durable_payload_is_refused(history, change):
    _, generation, identifier, receipts, _ = history
    if change == "extra":
        (generation / "unexpected").write_bytes(b"x")
    elif change == "missing":
        (generation / "manifest.toml").unlink()
    elif change == "changed-bytes":
        (generation / "manifest.toml").write_bytes(b"different")
    elif change == "short-signature":
        (generation / "manifest.toml.sig").write_bytes(b"x" * 63)
    else:
        receipts[1].write_bytes(b"")
    expected = {"extra": "generation_structure", "missing": "generation_structure",
                "changed-bytes": "generation_digest_mismatch", "short-signature": "birth_history_file_invalid",
                "empty-receipt": "birth_receipt_invalid"}[change]
    with pytest.raises(store.ContractStoreError, match=expected):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


@pytest.mark.parametrize("target", ["binding", "receipt"])
def test_changed_reread_is_refused(history, monkeypatch, target):
    contract, _, identifier, receipts, _ = history
    selected = contract / store.BINDING_FILE if target == "binding" else receipts[1]
    original = store._read_history_file_v1
    changed = False

    def read(path, **kwargs):
        nonlocal changed
        value = original(path, **kwargs)
        if path == selected and not changed:
            path.write_bytes(value + b" ")
            changed = True
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    with pytest.raises(store.ContractStoreError, match="birth_history_reread_mismatch"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


def test_unrelated_generation_creation_does_not_change_exact_item_identity(history, monkeypatch):
    _, generation, identifier, _, _ = history
    original = store._read_history_file_v1
    added = False

    def read(path, **kwargs):
        nonlocal added
        value = original(path, **kwargs)
        if not added:
            (generation.parent / ("e" * 64)).mkdir(mode=0o700)
            added = True
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    assert store.read_historical_birth_evidence_v1(
        CONTRACT, identifier, admission_context_id=CONTEXT,
    ).generation_id == identifier


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO substitution boundary")
def test_fifo_substitution_before_open_never_blocks(history, monkeypatch):
    _, _, identifier, receipts, _ = history
    target = receipts[1]
    original = os.open
    attempted = False

    def substitute(path, flags, *args, **kwargs):
        nonlocal attempted
        if Path(path) == target:
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
            target.unlink()
            os.mkfifo(target, mode=0o600)
            attempted = True
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(store.os, "open", substitute)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)
    assert attempted


@pytest.mark.parametrize("target", ["contract", "generations", "generation", "receipt-parent"])
def test_linked_directory_is_refused(history, target):
    contract, generation, identifier, receipts, _ = history
    path = {"contract": contract, "generations": generation.parent,
            "generation": generation, "receipt-parent": receipts[1].parent}[target]
    saved = path.with_name(path.name + ".saved")
    path.rename(saved)
    path.symlink_to(saved, target_is_directory=True)
    with pytest.raises(store.ContractStoreError, match="birth_history_directory_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; native ACL proof remains separate")
@pytest.mark.parametrize("target", ["contract", "receipt"])
def test_writable_by_others_is_refused_without_repair(history, target):
    contract, _, identifier, receipts, _ = history
    path = contract if target == "contract" else receipts[1]
    mode = 0o770 if target == "contract" else 0o660
    path.chmod(mode)
    with pytest.raises(store.ContractStoreError, match="birth_history_(directory|file)_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)
    assert path.stat().st_mode & 0o777 == mode


def test_directory_replaced_with_identical_bytes_is_refused(history, monkeypatch):
    import shutil

    _, generation, identifier, _, _ = history
    original = store._read_history_file_v1
    changed = False

    def read(path, **kwargs):
        nonlocal changed
        value = original(path, **kwargs)
        if path == generation / "manifest.lang_state.json" and not changed:
            saved = generation.with_name(generation.name + ".saved")
            generation.rename(saved)
            shutil.copytree(saved, generation)
            changed = True
        return value

    monkeypatch.setattr(store, "_read_history_file_v1", read)
    with pytest.raises(store.ContractStoreError, match="birth_history_source_changed"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)


def test_regular_file_substitution_between_stat_and_open_is_refused(history, monkeypatch):
    _, _, identifier, receipts, _ = history
    target = receipts[1]
    original = os.open

    def substitute(path, flags, *args, **kwargs):
        if Path(path) == target:
            saved = target.with_name(target.name + ".saved")
            target.rename(saved)
            target.write_bytes(saved.read_bytes())
            target.chmod(0o600)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(store.os, "open", substitute)
    with pytest.raises(store.ContractStoreError, match="birth_history_file_invalid"):
        store.read_historical_birth_evidence_v1(CONTRACT, identifier, admission_context_id=CONTEXT)
