from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest


_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _ROOT / "executors" / "organize_files" / "organize_files.py"
_SPEC = importlib.util.spec_from_file_location("organize_files_executor", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
organize = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(organize)


@pytest.fixture(autouse=True)
def _runtime_context(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "organize-files-test")
    monkeypatch.setenv("METNOS_ACTOR", "host")
    monkeypatch.setenv("METNOS_OWNER_USER_ID", "owner-1")
    monkeypatch.setenv("METNOS_DEVICE_ID", "server-1")
    monkeypatch.setenv("METNOS_CHANNEL", "http")


def _scope(source: Path, destination: Path, compare: Path | None = None) -> dict:
    return {
        "source_paths": [str(source)],
        "compare_paths": [str(compare)] if compare is not None else [],
        "destination_roots": [str(destination)],
    }


def _move_policy(source: Path, destination: Path, *, compare: Path | None = None,
                 deduplicate: bool = False, template: str = "{name}") -> dict:
    operations = []
    if deduplicate:
        operations.append({
            "type": "deduplicate",
            "match": "sha256",
            "keep": "outside_sources_or_first",
        })
    operations.append({
        "type": "move",
        "destination_root": str(destination),
        "path_template": template,
        "on_missing": "fail",
        "on_conflict": "fail",
    })
    return {
        "mode": "preview",
        **_scope(source, destination, compare),
        "operations": operations,
    }


def _apply(preview: dict) -> dict:
    old = os.environ.get("METNOS_FROZEN_PLAN_AUTHORIZATION")
    os.environ["METNOS_FROZEN_PLAN_AUTHORIZATION"] = preview["confirmation_token"]
    try:
        return organize.invoke({
            "mode": "apply",
            "confirmation_token": preview["confirmation_token"],
            "source_paths": preview["source_paths"],
            "compare_paths": preview["compare_paths"],
            "destination_roots": preview["destination_roots"],
        })
    finally:
        if old is None:
            os.environ.pop("METNOS_FROZEN_PLAN_AUTHORIZATION", None)
        else:
            os.environ["METNOS_FROZEN_PLAN_AUTHORIZATION"] = old


def _apply_args(preview: dict) -> dict:
    return {
        "mode": "apply",
        "confirmation_token": preview["confirmation_token"],
        "source_paths": preview["source_paths"],
        "compare_paths": preview["compare_paths"],
        "destination_roots": preview["destination_roots"],
    }


def _killed_executor(payload: dict, *, fault: str,
                     operation: str = "invoke") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "METNOS_TESTING": "1",
        "METNOS_ORGANIZE_FAULT": fault,
        "METNOS_EXECUTOR_OPERATION": operation,
    })
    if operation == "invoke":
        env["METNOS_FROZEN_PLAN_AUTHORIZATION"] = str(
            payload["confirmation_token"])
    return subprocess.run(
        [sys.executable, str(_MODULE_PATH)],
        input=json.dumps(payload), text=True, capture_output=True,
        env=env, check=False, timeout=20)


def test_preview_apply_reverse_is_lossless_for_move_and_deduplicate(
        tmp_path: Path) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "archive"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    (destination / "2024").mkdir()
    reference = compare / "kept.bin"
    reference.write_bytes(b"same-content")
    duplicate = source / "duplicate.bin"
    shutil.copyfile(reference, duplicate)
    unique = source / "20240102_unique.txt"
    unique.write_bytes(b"unique-content")
    before = {
        duplicate: duplicate.read_bytes(),
        unique: unique.read_bytes(),
        reference: reference.read_bytes(),
    }

    preview = organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True,
        template="{capture_date.year}/{name}",
    ))

    assert preview["ok"] is True
    assert preview["_undo"] == {"outcome": "no_effect"}
    assert preview["duplicate_count"] == 1
    assert preview["move_count"] == 1
    assert duplicate.exists() and unique.exists()

    applied = _apply(preview)

    moved = destination / "2024" / unique.name
    assert applied["ok"] is True
    assert applied["_undo"]["outcome"] == "reversible"
    assert not duplicate.exists() and not unique.exists()
    assert moved.read_bytes() == before[unique]
    assert reference.read_bytes() == before[reference]

    reversed_result = organize.reverse({}, applied)

    assert reversed_result["ok"] is True
    assert duplicate.read_bytes() == before[duplicate]
    assert unique.read_bytes() == before[unique]
    assert reference.read_bytes() == before[reference]
    assert not moved.exists()


def test_stale_inventory_refuses_apply_without_effect(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    original = source / "a.txt"
    original.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))
    (source / "late.txt").write_bytes(b"late")

    applied = _apply(preview)

    assert applied["ok"] is False
    assert applied["error_code"] == "ERR_ORGANIZE_STALE_PLAN"
    assert original.exists()
    assert not (destination / original.name).exists()


def test_failed_batch_rolls_back_all_prior_moves(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    first = source / "a.txt"
    second = source / "b.txt"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    preview = organize.invoke(_move_policy(source, destination))
    original_move = organize._move_noreplace
    calls = 0

    def fail_second(src, dst, action):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-move failure")
        return original_move(src, dst, action)

    monkeypatch.setattr(organize, "_move_noreplace", fail_second)
    applied = _apply(preview)

    assert applied["ok"] is False
    assert applied["_undo"] == {"outcome": "no_effect"}
    assert first.read_bytes() == b"a"
    assert second.read_bytes() == b"b"
    assert list(destination.iterdir()) == []


def test_preview_is_worker_count_invariant(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    for index in range(24):
        (source / f"20240102_{index:02d}.txt").write_bytes(
            f"payload-{index % 5}".encode())
    args = _move_policy(
        source, destination, deduplicate=True,
        template="{capture_date.year}/{name}",
    )
    monkeypatch.setattr(organize.time, "time", lambda: 1_700_000_000)

    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "1")
    serial = organize.invoke(args)
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "8")
    parallel = organize.invoke(args)

    assert serial == parallel


def test_entries_enrich_policy_but_cannot_escape_source_scope(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    source.mkdir()
    destination.mkdir()
    (destination / "Alpha-Team").mkdir()
    outside.mkdir()
    inside_file = source / "report.txt"
    inside_file.write_bytes(b"report")
    outside_file = outside / "private.txt"
    outside_file.write_bytes(b"private")
    args = _move_policy(source, destination, template="{project|slug}/{name}")
    args["entries"] = [{"path": str(inside_file), "project": "Alpha Team"}]

    preview = organize.invoke(args)

    assert preview["ok"] is True
    assert preview["results"][0]["destination"].endswith(
        "/Alpha-Team/report.txt")

    args["entries"] = [{"path": str(outside_file), "project": "Secret"}]
    refused = organize.invoke(args)
    assert refused["ok"] is False
    assert refused["error_code"] == "ERR_ORGANIZE_POLICY"


def test_unsafe_or_overdeep_condition_is_rejected_before_scan(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    for condition in [
        {"field": "name", "operator": "regex", "value": "(a+)+$"},
        {"not": {"not": {"not": {"not": {"not": {"not": {"not": {
            "not": {"not": {
                "field": "name", "operator": "equals", "value": "a",
            }},
        }}}}}}}},
    ]:
        args = _move_policy(source, destination)
        args["select"] = condition
        result = organize.invoke(args)
        assert result["ok"] is False
        assert result["error_code"] == "ERR_ORGANIZE_POLICY"


def test_destination_inside_source_and_symlink_source_are_rejected(
        tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    nested = source / "archive"
    nested.mkdir()
    args = _move_policy(source, nested)
    assert organize.invoke(args)["error_code"] == "ERR_ORGANIZE_POLICY"

    destination = tmp_path / "destination"
    destination.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    args = _move_policy(source, destination)
    args["source_paths"] = [str(alias)]
    refused = organize.invoke(args)
    assert refused["ok"] is False
    assert refused["error_class"] == "unsafe_target"


def test_apply_is_bound_to_actor_and_exact_scope(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    other = tmp_path / "other"
    source.mkdir()
    destination.mkdir()
    other.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))

    wrong_scope = dict(preview)
    wrong_scope["destination_roots"] = [str(other)]
    refused_scope = _apply(wrong_scope)
    assert refused_scope["ok"] is False
    assert item.exists()

    monkeypatch.setenv("METNOS_ACTOR", "guest:other")
    refused_actor = _apply(preview)
    assert refused_actor["ok"] is False
    assert refused_actor["error_class"] == "permission_denied"
    assert item.exists()


def test_runtime_metadata_is_accepted_exactly_and_ignored(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    metadata = {
        "_actor": "runtime-actor",
        "_lang": "it",
        "_channel": "telegram",
        "_turn_id": "runtime-turn",
    }

    refused_preview = organize.invoke({
        **_move_policy(source, destination),
        **metadata,
        "_runtime_private": "not-allowed",
    })
    assert refused_preview["ok"] is False
    assert refused_preview["error_code"] == "ERR_ORGANIZE_POLICY"

    preview = organize.invoke({**_move_policy(source, destination), **metadata})
    assert preview["ok"] is True
    apply_args = {**_apply_args(preview), **metadata}
    monkeypatch.setenv(
        "METNOS_FROZEN_PLAN_AUTHORIZATION", preview["confirmation_token"])

    refused_apply = organize.invoke({
        **apply_args,
        "_runtime_private": "not-allowed",
    })
    assert refused_apply["ok"] is False
    assert refused_apply["error_code"] == "ERR_ORGANIZE_POLICY"
    assert item.exists()

    applied = organize.invoke(apply_args)
    assert applied["ok"] is True
    assert not item.exists()
    reversed_result = organize.reverse({}, applied)
    assert reversed_result["ok"] is True
    assert item.read_bytes() == b"a"


def test_reverse_refuses_changed_destination_without_partial_effect(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"original")
    preview = organize.invoke(_move_policy(source, destination))
    applied = _apply(preview)
    moved = destination / item.name
    moved.write_bytes(b"changed-after-apply")

    reversed_result = organize.reverse({}, applied)

    assert reversed_result["ok"] is False
    assert reversed_result["ok_count"] == 0
    assert not item.exists()
    assert moved.read_bytes() == b"changed-after-apply"


def test_receipt_is_private_and_content_verified(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "a.txt").write_bytes(b"a")
    applied = _apply(organize.invoke(_move_policy(source, destination)))
    receipt = Path(applied["receipt_path"])

    assert receipt.stat().st_mode & 0o777 == 0o600
    value = json.loads(receipt.read_text(encoding="ascii"))
    value["actions"][0]["destination"] = str(tmp_path / "elsewhere")
    receipt.write_text(json.dumps(value), encoding="ascii")

    reversed_result = organize.reverse({}, applied)
    assert reversed_result["ok"] is False
    assert reversed_result["error_code"] == "ERR_ORGANIZE_RECEIPT_INVALID"


def test_copied_preview_token_cannot_authorize_apply(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))

    refused = organize.invoke({
        "mode": "apply",
        "confirmation_token": preview["confirmation_token"],
        "source_paths": preview["source_paths"],
        "compare_paths": preview["compare_paths"],
        "destination_roots": preview["destination_roots"],
    })

    assert refused["ok"] is False
    assert refused["error_code"] == "ERR_ORGANIZE_CONFIRMATION_REQUIRED"
    assert item.exists()


def test_repeated_reverse_reports_zero_new_effects(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "a.txt").write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))
    applied = _apply(preview)

    first = organize.reverse({}, applied)
    second = organize.reverse({}, applied)

    assert first["ok"] is True and first["ok_count"] == 1
    assert second["ok"] is True and second["ok_count"] == 0
    assert second["results"] == []

    receipt = Path(applied["receipt_path"])
    terminal = receipt.read_bytes()
    replay = _apply(preview)
    assert replay["ok"] is False
    assert replay["_undo"] == {"outcome": "no_effect"}
    assert receipt.read_bytes() == terminal


def test_delete_undo_restores_mode_times_and_xattrs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "destination"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    reference = compare / "kept.bin"
    duplicate = source / "duplicate.bin"
    reference.write_bytes(b"same")
    duplicate.write_bytes(b"same")
    duplicate.chmod(0o640)
    os.utime(duplicate, ns=(1_700_000_000_000_000_000,
                            1_700_000_001_000_000_000))
    try:
        os.setxattr(duplicate, "user.metnos-test", b"preserve")
    except OSError as exc:
        pytest.skip(f"xattrs unavailable: {exc}")
    before = duplicate.stat()
    applied = _apply(organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True)))

    reversed_result = organize.reverse({}, applied)

    after = duplicate.stat()
    assert reversed_result["ok"] is True
    assert duplicate.read_bytes() == b"same"
    assert after.st_mode & 0o777 == before.st_mode & 0o777
    assert after.st_mtime_ns == before.st_mtime_ns
    assert os.getxattr(duplicate, "user.metnos-test") == b"preserve"


def test_hardlinked_duplicate_is_refused_before_delete(tmp_path: Path) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "destination"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    reference = compare / "kept.bin"
    reference.write_bytes(b"same")
    duplicate = source / "duplicate.bin"
    os.link(reference, duplicate)
    preview = organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True))

    assert preview["ok"] is False
    assert preview["apply_ready"] is False
    assert preview["error_code"] == "ERR_ORGANIZE_PLAN_BLOCKED"
    assert duplicate.exists() and reference.exists()
    assert duplicate.stat().st_ino == reference.stat().st_ino


def test_missing_destination_parent_is_blocked_in_preview(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")

    preview = organize.invoke(_move_policy(
        source, destination, template="missing/{name}"))

    assert preview["ok"] is False
    assert preview["apply_ready"] is False
    assert preview["error_code"] == "ERR_ORGANIZE_PLAN_BLOCKED"
    assert item.exists()
    assert not (destination / "missing").exists()


def test_replaced_destination_root_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    replacement = tmp_path / "replacement"
    source.mkdir()
    destination.mkdir()
    replacement.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))
    old_destination = tmp_path / "destination-old"
    destination.rename(old_destination)
    replacement.rename(destination)

    applied = _apply(preview)

    assert applied["ok"] is False
    assert applied["error_code"] == "ERR_ORGANIZE_STALE_PLAN"
    assert item.exists()
    assert list(destination.iterdir()) == []


def test_symlink_parent_swap_cannot_escape_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    source.mkdir()
    destination.mkdir()
    outside.mkdir()
    category = destination / "category"
    category.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(
        source, destination, template="category/{name}"))
    category.rmdir()
    category.symlink_to(outside, target_is_directory=True)

    applied = _apply(preview)

    assert applied["ok"] is False
    assert item.exists()
    assert list(outside.iterdir()) == []


def test_post_effect_failure_is_rolled_back_from_wal(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))
    original = organize._move_noreplace

    def fail_after_effect(src, dst, action):
        original(src, dst, action)
        raise OSError("injected after source unlink")

    monkeypatch.setattr(organize, "_move_noreplace", fail_after_effect)
    applied = _apply(preview)

    assert applied["ok"] is False
    assert applied["_undo"] == {"outcome": "no_effect"}
    assert item.read_bytes() == b"a"
    assert not (destination / "a.txt").exists()
    journals = list((tmp_path / "history").rglob("*.organize-receipt.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text())["status"] == "rolled_back"


def test_retry_reconciles_crash_after_last_effect(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))
    original_move = organize._move_noreplace
    original_rollback = organize._rollback_journal

    def crash_after_effect(src, dst, action):
        original_move(src, dst, action)
        raise SystemExit("simulated SIGKILL boundary")

    def abort_before_rollback(_journal, _path, cause):
        raise cause

    monkeypatch.setattr(organize, "_move_noreplace", crash_after_effect)
    monkeypatch.setattr(organize, "_rollback_journal", abort_before_rollback)
    with pytest.raises(SystemExit):
        _apply(preview)
    assert not item.exists() and (destination / "a.txt").exists()

    monkeypatch.setattr(organize, "_move_noreplace", original_move)
    monkeypatch.setattr(organize, "_rollback_journal", original_rollback)
    applied = _apply(preview)

    assert applied["ok"] is True
    assert applied["ok_count"] == 1
    assert organize.reverse({}, applied)["ok"] is True
    assert item.read_bytes() == b"a"


@pytest.mark.parametrize("fault", [
    "after_wal", "after_staging_before_destination",
    "after_effect_before_journal",
])
def test_sigkill_recovery_uses_durable_wal(
        tmp_path: Path, fault: str) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    preview = organize.invoke(_move_policy(source, destination))

    killed = _killed_executor(_apply_args(preview), fault=fault)

    assert killed.returncode == -signal.SIGKILL
    journal = next((tmp_path / "history").rglob(
        "*.organize-receipt.json"))
    assert json.loads(journal.read_text())["status"] in {
        "prepared", "applying"}
    recovered = _apply(preview)
    assert recovered["ok"] is True
    assert not item.exists()
    assert (destination / "a.txt").read_bytes() == b"a"
    assert organize.reverse({}, recovered)["ok"] is True


def test_sigkill_during_reverse_is_reconciled_without_second_effect(
        tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"a")
    applied = _apply(organize.invoke(_move_policy(source, destination)))

    killed = _killed_executor(
        {"plan": {}, "results": applied},
        fault="after_reverse_effect_before_journal", operation="reverse")

    assert killed.returncode == -signal.SIGKILL
    assert item.read_bytes() == b"a"
    assert not (destination / "a.txt").exists()
    retried = organize.reverse({}, applied)
    assert retried["ok"] is True
    assert retried["ok_count"] == 0


@pytest.mark.parametrize(("fault", "exists_after_kill", "retry_count"), [
    ("after_staging_before_destination", False, 1),
    ("after_reverse_effect_before_journal", True, 0),
])
def test_sigkill_during_reverse_delete_recovers_from_durable_intent(
        tmp_path: Path, fault: str, exists_after_kill: bool,
        retry_count: int) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "destination"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    duplicate = source / "duplicate.bin"
    reference = compare / "reference.bin"
    duplicate.write_bytes(b"same")
    reference.write_bytes(b"same")
    applied = _apply(organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True)))
    assert applied["ok"] is True and not duplicate.exists()

    killed = _killed_executor(
        {"plan": {}, "results": applied},
        fault=fault, operation="reverse")

    assert killed.returncode == -signal.SIGKILL
    assert duplicate.exists() is exists_after_kill
    if exists_after_kill:
        assert duplicate.read_bytes() == b"same"
    retried = organize.reverse({}, applied)
    assert retried["ok"] is True
    assert retried["ok_count"] == retry_count
    assert duplicate.read_bytes() == b"same"
    assert list(source.glob(".metnos-organize-*")) == []
    receipt = json.loads(Path(applied["receipt_path"]).read_text())
    assert receipt["status"] == "undone"


def test_executor_has_no_unbounded_external_metadata_process() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "ffprobe" not in source


def test_same_bytes_source_replacement_is_not_moved(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"same")
    preview = organize.invoke(_move_policy(source, destination))
    original_write = organize._write_journal
    swapped = False

    def swap_after_intent(path, journal):
        nonlocal swapped
        digest = original_write(path, journal)
        if (not swapped and journal.get("status") == "applying"
                and any(row.get("state") == "intent"
                        for row in journal.get("actions") or [])):
            item.unlink()
            item.write_bytes(b"same")
            swapped = True
        return digest

    monkeypatch.setattr(organize, "_write_journal", swap_after_intent)
    result = _apply(preview)

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert item.read_bytes() == b"same"
    assert not (destination / "a.txt").exists()


def test_destination_created_after_preflight_is_never_removed(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"ours")
    preview = organize.invoke(_move_policy(source, destination))
    target = destination / "a.txt"
    original_write = organize._write_journal
    injected = False

    def inject_collision(path, journal):
        nonlocal injected
        digest = original_write(path, journal)
        if (not injected and journal.get("status") == "applying"
                and any(row.get("state") == "intent"
                        for row in journal.get("actions") or [])):
            target.write_bytes(b"third-party")
            injected = True
        return digest

    monkeypatch.setattr(organize, "_write_journal", inject_collision)
    result = _apply(preview)

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert item.read_bytes() == b"ours"
    assert target.read_bytes() == b"third-party"


def test_move_basename_swap_is_restored_and_never_moved(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"approved")
    displaced = tmp_path / "approved-displaced.txt"
    preview = organize.invoke(_move_policy(source, destination))
    real_rename = organize._rename_noreplace
    swapped = False

    def swap_before_stage(source_fd, source_name, destination_fd,
                          destination_name):
        nonlocal swapped
        if (not swapped and source_name == "a.txt"
                and destination_name.startswith(".metnos-organize-stage-")):
            item.rename(displaced)
            item.write_bytes(b"third-party")
            swapped = True
        return real_rename(
            source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(organize, "_rename_noreplace", swap_before_stage)
    result = _apply(preview)

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert item.read_bytes() == b"third-party"
    assert displaced.read_bytes() == b"approved"
    assert not (destination / "a.txt").exists()
    assert list(source.glob(".metnos-organize-*")) == []


def test_delete_basename_swap_is_restored_and_never_removed(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "destination"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    duplicate = source / "duplicate.bin"
    reference = compare / "reference.bin"
    duplicate.write_bytes(b"approved")
    reference.write_bytes(b"approved")
    displaced = tmp_path / "approved-displaced.bin"
    preview = organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True))
    real_rename = organize._rename_noreplace
    swapped = False

    def swap_before_quarantine(source_fd, source_name, destination_fd,
                               destination_name):
        nonlocal swapped
        if (not swapped and source_name == "duplicate.bin"
                and destination_name.startswith(".metnos-organize-delete-")):
            duplicate.rename(displaced)
            duplicate.write_bytes(b"third-party")
            swapped = True
        return real_rename(
            source_fd, source_name, destination_fd, destination_name)

    monkeypatch.setattr(organize, "_rename_noreplace", swap_before_quarantine)
    result = _apply(preview)

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert duplicate.read_bytes() == b"third-party"
    assert displaced.read_bytes() == b"approved"
    assert list(source.glob(".metnos-organize-*")) == []


def test_hardlink_destination_created_after_preflight_is_never_removed(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"ours")
    preview = organize.invoke(_move_policy(source, destination))
    target = destination / "a.txt"
    original_write = organize._write_journal
    injected = False

    def inject_hardlink(path, journal):
        nonlocal injected
        digest = original_write(path, journal)
        if (not injected and journal.get("status") == "applying"
                and any(row.get("state") == "intent"
                        for row in journal.get("actions") or [])):
            os.link(item, target)
            injected = True
        return digest

    monkeypatch.setattr(organize, "_write_journal", inject_hardlink)
    result = _apply(preview)

    assert result["ok"] is False
    assert result["_undo"] == {"outcome": "no_effect"}
    assert item.exists() and target.exists()
    assert item.stat().st_ino == target.stat().st_ino


def test_added_hardlink_after_move_blocks_undo(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"payload")
    applied = _apply(organize.invoke(_move_policy(source, destination)))
    moved = destination / "a.txt"
    late_link = tmp_path / "late-link.txt"
    os.link(moved, late_link)

    reversed_result = organize.reverse({}, applied)

    assert reversed_result["ok"] is False
    assert reversed_result["fail_count"] == 1
    assert not item.exists()
    assert moved.read_bytes() == late_link.read_bytes() == b"payload"
    assert moved.stat().st_ino == late_link.stat().st_ino


def test_committed_receipt_never_reapplies_after_external_restore(
        tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    item = source / "a.txt"
    item.write_bytes(b"payload")
    preview = organize.invoke(_move_policy(source, destination))
    applied = _apply(preview)
    moved = destination / "a.txt"
    moved.rename(item)
    receipt = Path(applied["receipt_path"])
    before = receipt.read_bytes()

    replay = _apply(preview)

    assert replay["ok"] is False
    assert replay["error_class"] == "conflict"
    assert replay["_undo"] == {"outcome": "no_effect"}
    assert item.read_bytes() == b"payload"
    assert not moved.exists()
    assert receipt.read_bytes() == before


def test_scan_refuses_directory_replaced_by_symlink(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    inner = source / "inner"
    outside = tmp_path / "outside"
    destination = tmp_path / "destination"
    inner.mkdir(parents=True)
    outside.mkdir()
    destination.mkdir()
    (inner / "inside.txt").write_bytes(b"inside")
    secret = outside / "secret.txt"
    secret.write_bytes(b"outside-secret")
    held = source / "inner-held"
    real_open = organize.os.open
    swapped = False

    def swap_directory(path, flags, *args, **kwargs):
        nonlocal swapped
        if (not swapped and path == "inner" and kwargs.get("dir_fd") is not None
                and flags & os.O_DIRECTORY):
            inner.rename(held)
            inner.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(organize.os, "open", swap_directory)
    preview = organize.invoke(_move_policy(source, destination))

    assert preview["ok"] is False
    assert preview["apply_ready"] is False
    assert preview["scan_complete"] is False
    assert str(secret) not in json.dumps(preview)


def test_hash_refuses_parent_replaced_by_symlink_after_scan(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    inner = source / "inner"
    outside = tmp_path / "outside"
    destination = tmp_path / "destination"
    inner.mkdir(parents=True)
    outside.mkdir()
    destination.mkdir()
    (inner / "item.txt").write_bytes(b"approved")
    secret = outside / "item.txt"
    secret.write_bytes(b"outside-secret")
    held = source / "inner-held"
    real_parallel = organize.parallel_map_ordered
    calls = 0

    def swap_before_hash(items, worker):
        nonlocal calls
        calls += 1
        if calls == 2:
            inner.rename(held)
            inner.symlink_to(outside, target_is_directory=True)
        return real_parallel(items, worker)

    monkeypatch.setattr(organize, "parallel_map_ordered", swap_before_hash)
    preview = organize.invoke(_move_policy(source, destination))

    assert preview["ok"] is False
    assert preview["apply_ready"] is False
    assert preview["scan_complete"] is False
    assert str(secret) not in json.dumps(preview)


def test_added_hardlink_before_delete_fails_closed(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    compare = tmp_path / "compare"
    destination = tmp_path / "destination"
    source.mkdir()
    compare.mkdir()
    destination.mkdir()
    duplicate = source / "duplicate.bin"
    reference = compare / "reference.bin"
    duplicate.write_bytes(b"same")
    reference.write_bytes(b"same")
    preview = organize.invoke(_move_policy(
        source, destination, compare=compare, deduplicate=True))
    extra = tmp_path / "late-link.bin"
    original_write = organize._write_journal
    injected = False

    def add_link(path, journal):
        nonlocal injected
        digest = original_write(path, journal)
        if (not injected and journal.get("status") == "applying"
                and any(row.get("kind") == "delete"
                        and row.get("state") == "intent"
                        for row in journal.get("actions") or [])):
            os.link(duplicate, extra)
            injected = True
        return digest

    monkeypatch.setattr(organize, "_write_journal", add_link)
    result = _apply(preview)

    assert result["ok"] is False
    assert duplicate.read_bytes() == b"same"
    assert extra.read_bytes() == b"same"


def test_move_preserves_existing_hardlink_topology(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    peer = tmp_path / "peer.bin"
    item = source / "a.bin"
    item.write_bytes(b"same")
    os.link(item, peer)
    inode = item.stat().st_ino

    applied = _apply(organize.invoke(_move_policy(source, destination)))

    moved = destination / "a.bin"
    assert applied["ok"] is True
    assert moved.stat().st_ino == peer.stat().st_ino == inode
    assert moved.stat().st_nlink == 2
    assert organize.reverse({}, applied)["ok"] is True
    assert item.stat().st_ino == peer.stat().st_ino == inode


def test_cross_filesystem_move_is_blocked_in_preview(tmp_path: Path) -> None:
    cross_root = Path("/dev/shm")
    if not cross_root.is_dir() or cross_root.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("no distinct writable filesystem available")
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_bytes(b"a")
    with tempfile.TemporaryDirectory(
            prefix="metnos-organize-", dir=cross_root) as raw_destination:
        preview = organize.invoke(_move_policy(
            source, Path(raw_destination)))
    assert preview["ok"] is False
    assert preview["apply_ready"] is False
    assert preview["error_code"] == "ERR_ORGANIZE_PLAN_BLOCKED"


def test_signed_authoring_catalog_and_real_undo_broker_roundtrip(
        tmp_path: Path) -> None:
    """Exercise the signed loader and the real undo broker in one process.

    Isolated user roots select the authoring inventory without touching the
    productive contract store.  The broker must discover ``organize_files``
    through ``load_catalog(verify=True)`` and dispatch its declared custom
    reverse; neither the catalog nor the reverse function is monkeypatched.
    """
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    data = tmp_path / "user-data"
    state = tmp_path / "user-state"
    history = tmp_path / "history"
    for directory in (source, destination, data, state, history):
        directory.mkdir(mode=0o700)
    (source / "item.txt").write_text("payload", encoding="utf-8")
    request = _move_policy(source, destination)
    undo_path = data / "undo.jsonl"
    env = os.environ.copy()
    env.update({
        "METNOS_USER_DATA": str(data),
        "METNOS_USER_STATE": str(state),
        "METNOS_HISTORY_DIR": str(history),
        "METNOS_TURN_ID": "broker-turn",
        "METNOS_ACTOR": "host",
        "METNOS_OWNER_USER_ID": "owner-1",
        "METNOS_DEVICE_ID": "server-1",
        "METNOS_CHANNEL": "http",
        "METNOS_ORGANIZE_REQUEST": json.dumps(request),
        "METNOS_ORGANIZE_UNDO_LOG": str(undo_path),
        "PYTHONPATH": str(_ROOT / "runtime"),
    })
    probe = r'''
import importlib.util
import json
import os
from pathlib import Path

from loader import load_catalog
from manifest_inventory import ManifestLayout, resolve_manifest_layout
from undo import UndoLog

assert resolve_manifest_layout() is ManifestLayout.AUTHORING
catalog = load_catalog(verify=True, include_synth=False)
executor = catalog.get("organize_files")
assert executor is not None
spec = importlib.util.spec_from_file_location("organize_broker_probe", executor.code_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
request = json.loads(os.environ["METNOS_ORGANIZE_REQUEST"])
preview = module.invoke(request)
assert preview.get("ok") is True, preview
apply_args = {
    "mode": "apply",
    "confirmation_token": preview["confirmation_token"],
    "source_paths": preview["source_paths"],
    "compare_paths": preview["compare_paths"],
    "destination_roots": preview["destination_roots"],
}
os.environ["METNOS_FROZEN_PLAN_AUTHORIZATION"] = preview["confirmation_token"]
applied = module.invoke(apply_args)
assert applied.get("ok") is True and applied.get("ok_count") == 1, applied
log_path = Path(os.environ["METNOS_ORGANIZE_UNDO_LOG"])
log = UndoLog(log_path)
log.append_pending(
    "organize-op", "broker-turn", "organize_files", apply_args,
    {"args": apply_args}, actor="host", channel="http",
    outcome_contract="per_execution",
)
assert log.append_completion(
    "organize-op", applied, outcome_contract="per_execution",
) == "reversible"
undo_path = executor.code_path.parents[1] / "undo_last_turn" / "undo_last_turn.py"
undo_spec = importlib.util.spec_from_file_location("real_undo_broker", undo_path)
assert undo_spec is not None and undo_spec.loader is not None
undo_module = importlib.util.module_from_spec(undo_spec)
undo_spec.loader.exec_module(undo_module)
undone = undo_module.invoke({"log_path": str(log_path), "_actor": "host"})
assert undone.get("ok") is True and undone.get("undone_count") == 1, undone
second = undo_module.invoke({"log_path": str(log_path), "_actor": "host"})
assert second.get("undone_count") == 0, second
print(json.dumps({"applied": applied, "undone": undone, "second": second}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe], cwd=_ROOT, env=env,
        text=True, capture_output=True, timeout=30, check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert (source / "item.txt").read_text(encoding="utf-8") == "payload"
    assert not (destination / "item.txt").exists()
    records = [json.loads(line) for line in undo_path.read_text().splitlines()]
    assert [record["type"] for record in records] == [
        "pending", "done", "undone",
    ]
