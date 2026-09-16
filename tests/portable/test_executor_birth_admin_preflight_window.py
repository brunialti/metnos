"""Standalone current-state verification does not replay lifetime history."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import executor_birth_admin_preflight as preflight
from test_executor_birth_admin_preflight import (
    LINUX_ONLY, _authenticated_fixed_ownership_fixture, _write_control_file,
)

pytestmark = LINUX_ONLY


def _read(root, *, between=None):
    candidate = preflight._capture_current_ownership_state_core_v1(
        root, uid=os.getuid(), gid=os.getgid(), chain_stop=root,
        between_for_test=between,
    )
    return preflight._authenticate_fixed_ownership_snapshot_core_v1(
        candidate, openssl_executable=Path("/usr/bin/openssl"),
    )


@pytest.mark.parametrize("count", (1, 3, 12))
def test_current_window_access_is_bounded_without_archive_enumeration(tmp_path, monkeypatch, count):
    root, _ = _authenticated_fixed_ownership_fixture(tmp_path, release_count=count)
    captured = []
    read = preflight._capture_trusted_file_v1

    def observe(path, **kwargs):
        captured.append(path.relative_to(root).as_posix())
        return read(path, **kwargs)

    def no_inventory(*_args, **_kwargs):
        raise AssertionError("ordinary verification enumerated history")

    monkeypatch.setattr(preflight, "_capture_trusted_file_v1", observe)
    monkeypatch.setattr(preflight, "_control_names_v1", no_inventory)
    result = _read(root)
    assert result.history_start_sequence == max(1, count - 1)
    assert result.required_head.release_sequence == count
    assert len(result.heads) == len(result.transactions) == min(count, 2)
    assert len(set(captured)) <= 47
    assert not any("-private-" in path for path in captured)
    sequences = {head.release_sequence for head in result.heads}
    assert sequences == set(range(max(1, count - 1), count + 1))


@pytest.mark.parametrize("after_cas", (False, True))
def test_current_window_preserves_required_head_cas_recovery(tmp_path, after_cas):
    root, _ = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=4, final_record_sequence=4, required_after_cas=after_cas,
    )
    result = _read(root)
    assert result.required_head.release_sequence == (4 if after_cas else 3)
    assert result.transactions[-1].prefix.records[-1].sequence == 4
    assert len(result.heads) == (2 if after_cas else 3)


@pytest.mark.parametrize("terminal", (-1, 0, 1, 2, 3, 4))
def test_current_window_preserves_each_pending_successor_phase(tmp_path, terminal):
    root, _ = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=4, final_record_sequence=4,
    )
    transactions = root / "coordinator-v1/transactions-v2"
    pending = next(path for path in transactions.iterdir()
                   if json.loads((path / "record-000-v2.json").read_bytes())["release_sequence"] == 4)
    first = json.loads((pending / "record-000-v2.json").read_bytes())
    last = json.loads((pending / "record-004-v2.json").read_bytes())
    for record in pending.iterdir():
        if int(record.name[7:10]) > terminal:
            record.unlink()
    if terminal < 0:
        pending.rmdir()
    stems = [("builds-v1", first["closed_build_id"].removeprefix("sha256:")),
             ("heads-v1", f"{4:020d}-" + last["cutover_id"].removeprefix("sha256:"))]
    if terminal < 3:
        stems.append(("cutovers-v1", last["cutover_id"].removeprefix("sha256:")))
    for directory, stem in stems:
        for suffix in (".json", ".sig"):
            (root / "chain-v1" / directory / (stem + suffix)).unlink()
    result = _read(root)
    assert result.required_head.release_sequence == 3
    assert result.history_start_sequence == 2
    if terminal < 0:
        assert len(result.pending_claims) == 1
        assert len(result.transactions) == 2
    else:
        assert not result.pending_claims
        assert result.transactions[-1].prefix.records[-1].sequence == terminal


@pytest.mark.parametrize("damage", (
    "distribution_signature", "cutover_signature", "context", "attestation",
    "claim", "journal_gap", "journal_extra", "symlink", "mode",
))
def test_current_window_refuses_selected_damage(tmp_path, damage):
    root, _ = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=4, final_record_sequence=6,
    )
    transaction = next(path for path in (root / "coordinator-v1/transactions-v2").iterdir()
                       if json.loads((path / "record-000-v2.json").read_bytes())["release_sequence"] == 4)
    first = json.loads((transaction / "record-000-v2.json").read_bytes())
    last = json.loads((transaction / "record-006-v2.json").read_bytes())
    if damage == "distribution_signature":
        target = root / "chain-v1/builds-v1" / (first["closed_build_id"].removeprefix("sha256:") + ".sig")
        _write_control_file(target, b"x" * 64)
    elif damage == "cutover_signature":
        target = root / "chain-v1/cutovers-v1" / (last["cutover_id"].removeprefix("sha256:") + ".sig")
        _write_control_file(target, b"x" * 64)
    elif damage == "context":
        target = root / "chain-v1/context-transitions-v1" / (first["context_transition_id"].removeprefix("sha256:") + ".json")
        target.unlink()
    elif damage == "attestation":
        (root / "preflight-attestations-v1" / (first["request_id"] + ".json")).unlink()
    elif damage == "claim":
        target = root / "coordinator-v1/successor-claims-v1" / (first["previous_head_id"].removeprefix("sha256:") + ".json")
        target.unlink()
    elif damage == "journal_gap":
        (transaction / "record-003-v2.json").unlink()
    elif damage == "journal_extra":
        _write_control_file(transaction / "record-007-v2.json", b"{}")
    elif damage == "symlink":
        target = transaction / "record-006-v2.json"
        original = transaction / "saved.json"
        target.rename(original)
        target.symlink_to(original)
    else:
        (transaction / "record-006-v2.json").chmod(0o666)
    with pytest.raises(preflight.PreflightError):
        _read(root)


def test_current_window_does_not_hide_current_tampering_or_reaudit_old_damage(tmp_path):
    root, _ = _authenticated_fixed_ownership_fixture(tmp_path, release_count=4)
    heads = sorted((root / "chain-v1/heads-v1").glob("*.sig"))
    _write_control_file(heads[0], b"x" * 64)
    assert _read(root).required_head.release_sequence == 4
    with pytest.raises(preflight.PreflightError):
        preflight._authenticate_fixed_ownership_snapshot_for_test_v1(
            root, openssl_executable=Path("/usr/bin/openssl"),
        )
    _write_control_file(heads[-1], b"x" * 64)
    with pytest.raises(preflight.PreflightError):
        _read(root)


def test_current_window_refuses_change_during_capture(tmp_path):
    root, _ = _authenticated_fixed_ownership_fixture(tmp_path, release_count=3)
    pointer = root / "chain-v1/required-head-v1.bin"

    def change():
        _write_control_file(pointer, b"altered selector")

    with pytest.raises(preflight.PreflightError):
        _read(root, between=change)


def test_current_window_refuses_rollback_even_with_valid_old_signature(tmp_path):
    root, _ = _authenticated_fixed_ownership_fixture(
        tmp_path, release_count=4, final_record_sequence=6,
    )
    head = sorted((root / "chain-v1/heads-v1").glob("*.json"))[-2]
    encoded = head.read_bytes()
    pointer = (preflight.REQUIRED_HEAD_MAGIC_V1 + len(encoded).to_bytes(4, "big")
               + encoded + head.with_suffix(".sig").read_bytes())
    _write_control_file(root / "chain-v1/required-head-v1.bin", pointer)
    with pytest.raises(preflight.PreflightError):
        _read(root)
