"""Conformance, fail-closed deletion, undo, and sandbox gates for delete_files."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.delete_files import delete_files  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402
from reverse_patterns import (  # noqa: E402
    _restore_blob_backup,
    _restore_trashed_files,
    build_remote_reverse_calls,
)
from backends.files import local  # noqa: E402
import sandbox  # noqa: E402


MANIFEST = ROOT / "executors" / "delete_files" / "manifest.toml"


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(ROOT / "executors", value, False,
                           is_synthesized=False)
    return value


def test_manifest_declares_destructive_contract_and_remote_undo() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["revertible"] is True
    assert manifest["reverse_pattern"] == [
        "restore_blob_backup", "restore_trashed_files"]
    assert manifest["placement"] == {
        "scope": "any", "device_ok": True,
        "min_sandbox": "appcontainer",
    }
    assert manifest["platforms"] == ["linux", "windows"]
    capabilities = {item["name"]: item for item in manifest["capabilities"]}
    assert set(capabilities) == {"fs:write", "provider:access"}
    assert capabilities["provider:access"]["when"] == {
        "arg": "client", "values": ["google_workspace"],
    }
    properties = manifest["args"]["properties"]
    assert properties["paths"]["uniqueItems"] is True
    assert properties["file_ids"]["uniqueItems"] is True
    assert "uniqueItems" not in properties["entries"]
    assert "schema_inline" in manifest["output"]


@pytest.mark.parametrize("root", [None, [], "paths"])
def test_dispatcher_rejects_non_object_root(root) -> None:
    result = delete_files.invoke(root)

    assert result["ok"] is False
    assert result["error_class"] == "invalid_args"
    assert result["error_code"] == "ERR_ARG_INVALID"
    assert result["results"] == []


def test_paths_type_failure_keeps_stable_terminal_envelope() -> None:
    result = delete_files.invoke({"paths": "/tmp/not-a-list"})

    assert result == {
        "ok": False,
        "ok_count": 0,
        "fail_count": 0,
        "results": [],
        "failed": [],
        "error_class": "invalid_args",
        "error_code": "ERR_ARG_INVALID",
        "error": result["error"],
    }


def test_invalid_path_item_does_not_echo_record() -> None:
    marker = "must-not-leak-from-delete-input"

    result = delete_files.invoke({"paths": [{"secret": marker}]})

    assert result["ok"] is False
    assert result["failed"][0]["error_code"] == "ERR_ARG_INVALID"
    assert marker not in str(result)


def test_complete_item_failure_has_top_level_error_contract(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    result = delete_files.invoke({"paths": [str(missing)]})

    assert result["ok"] is False
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
    assert result["results"] == []
    assert result["error_class"] == "not_found"
    assert result["error_code"] == "ERR_PATH_NOT_FOUND"
    assert result["error"] == result["failed"][0]["error"]


def test_mixed_delete_is_explicitly_partial(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    missing = tmp_path / "missing.txt"
    source.write_text("source", encoding="utf-8")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "mixed-delete")

    result = delete_files.invoke({"paths": [str(source), str(missing)]})

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == 1
    assert result["fail_count"] == 1
    assert len(result["results"]) == 1
    assert len(result["failed"]) == 1
    assert not source.exists()


def test_symlink_is_rejected_without_touching_link_or_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    link = tmp_path / "link.txt"
    target.write_text("target", encoding="utf-8")
    link.symlink_to(target.name)

    result = delete_files.invoke({"paths": [str(link)]})

    assert result["ok"] is False
    assert result["failed"][0]["actual"] == "symlink"
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "target"


def test_backup_failure_never_unlinks_source(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")

    def fail_backup(*_args, **_kwargs):
        raise OSError("simulated backup failure")

    monkeypatch.setattr(local, "_backup_delete_blob", fail_backup)
    result = delete_files.invoke({"paths": [str(source)]})

    assert result["ok"] is False
    assert result["failed"][0]["error_code"] == "ERR_OP_FAILED"
    assert source.read_text(encoding="utf-8") == "source"


def test_change_after_backup_refuses_delete(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("before", encoding="utf-8")
    real_backup = local._backup_delete_blob

    def changing_backup(path, blob_dir):
        outcome = real_backup(path, blob_dir)
        path.write_text("changed-after-backup", encoding="utf-8")
        return outcome

    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "revision-test")
    monkeypatch.setattr(local, "_backup_delete_blob", changing_backup)
    result = delete_files.invoke({"paths": [str(source)]})

    assert result["ok"] is False
    assert "changed after backup" in result["failed"][0]["error"]
    assert source.read_text(encoding="utf-8") == "changed-after-backup"


def test_delete_and_undo_restore_bytes_mode_and_mtime(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes(b"bit-perfect")
    source.chmod(0o640)
    expected_mtime = 1_700_000_000_123_456_789
    expected_atime = 1_699_999_000_123_456_789
    os.utime(source, ns=(expected_atime, expected_mtime))
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "undo-roundtrip")

    result = delete_files.invoke({"paths": [str(source)]})
    restored = _restore_blob_backup({}, result)

    assert result["ok"] is True
    assert result["_undo"] == {
        "reverse_pattern": "restore_blob_backup",
        "scope": {"client": "local"},
    }
    assert not result["failed"]
    row = result["results"][0]
    assert row["blob_sha256"] == hashlib.sha256(b"bit-perfect").hexdigest()
    assert row["restore_mode"] == "create"
    assert restored["ok"] is True
    restored_stat = source.stat()
    assert stat.S_IMODE(restored_stat.st_mode) == 0o640
    assert restored_stat.st_mtime_ns == expected_mtime
    assert source.read_bytes() == b"bit-perfect"


def test_drive_trash_undo_uses_only_recorded_exact_ids(monkeypatch) -> None:
    calls = []

    def fake_restore(args):
        calls.append(dict(args))
        return {
            "ok": True, "ok_count": len(args["ids"]), "fail_count": 0,
            "results": [{"id": fid, "status": "restored"}
                        for fid in args["ids"]],
            "failed": [],
        }

    from backends.files import google_workspace
    monkeypatch.setattr(google_workspace, "restore_trashed", fake_restore)
    forward = {"_undo": {
        "reverse_pattern": "restore_trashed_files",
        "ids": ["drive-id-000001", "drive-id-000002"],
        "scope": {"client": "google_workspace"},
    }}

    restored = _restore_trashed_files({}, forward)

    assert restored["ok"] is True
    assert calls == [{"ids": ["drive-id-000001", "drive-id-000002"]}]


def test_drive_trash_undo_rejects_missing_provider_scope(monkeypatch) -> None:
    from backends.files import google_workspace
    monkeypatch.setattr(
        google_workspace, "restore_trashed",
        lambda _args: pytest.fail("provider must not be called"),
    )

    restored = _restore_trashed_files({}, {"_undo": {
        "reverse_pattern": "restore_trashed_files",
        "ids": ["drive-id-000001"],
        "scope": {"client": "local"},
    }})

    assert restored["ok"] is False
    assert restored["fail_count"] == 1


def test_undo_never_overwrites_new_occupant(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("deleted", encoding="utf-8")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "occupied-test")
    result = delete_files.invoke({"paths": [str(source)]})
    source.write_text("new occupant", encoding="utf-8")

    restored = _restore_blob_backup({}, result)

    assert restored["ok"] is False
    assert "occupied" in restored["failed"][0]["error"]
    assert source.read_text(encoding="utf-8") == "new occupant"


def test_undo_rejects_corrupt_blob(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("deleted", encoding="utf-8")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "integrity-test")
    result = delete_files.invoke({"paths": [str(source)]})
    Path(result["results"][0]["blob_path"]).write_bytes(b"corrupt")

    restored = _restore_blob_backup({}, result)

    assert restored["ok"] is False
    assert restored["failed"][0]["error"] == "blob integrity mismatch"
    assert not source.exists()


def test_undo_history_bind_is_exact_and_manifest_derived(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    executor = SimpleNamespace(reverse_pattern="restore_blob_backup")

    selected = sandbox.undo_history_extras(executor, turn_id="turn-safe_1")

    assert selected == [tmp_path / "history" / "turn-safe_1" / "blob"]
    assert selected[0].is_dir()
    assert sandbox.undo_history_extras(
        SimpleNamespace(reverse_pattern="delete_created_paths"),
        turn_id="turn-safe_1") == []
    assert sandbox.undo_history_extras(executor, turn_id="../escape") == []
    assert not (tmp_path / "escape").exists()


def test_remote_undo_uses_device_local_blob_copy() -> None:
    result = {
        "results": [{
            "path": "/tmp/source.txt",
            "blob_path": "/device/history/blob/abc.bin",
            "blob_sha256": "abc",
            "restore_mode": "create",
        }],
    }

    reverse = build_remote_reverse_calls(
        ["restore_blob_backup"], {}, result)

    assert reverse["unsupported"] == []
    assert reverse["calls"] == [{
        "executor": "move_files",
        "args": {
            "entries": [{
                "src": "/device/history/blob/abc.bin",
                "dst": "/tmp/source.txt",
            }],
            "dst_template": "{dst}",
            "parents": True,
            "copy": True,
            "client": "local",
        },
    }]


def test_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "elimina questi file temporanei",
        "delete these regular files",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "delete_files" in names, (query, names)


def test_runtime_deduplicates_manifest_declared_targets(
        tmp_path: Path, monkeypatch) -> None:
    import agent_runtime

    source = tmp_path / "single-target.txt"
    source.write_text("one", encoding="utf-8")
    monkeypatch.setenv("METNOS_SANDBOX", "0")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setattr(agent_runtime, "_undo_pending", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_runtime, "_undo_done", lambda *_a, **_k: None)
    executor = _catalog().executors["delete_files"]

    result = agent_runtime.invoke_executor(
        executor,
        {"paths": [str(source), str(source)]},
        timeout_s=15,
        turn_id="deduplicate-target",
        actor="host",
        channel="test",
    )

    assert result["ok"] is True, result
    assert result["ok_count"] == 1
    assert result["fail_count"] == 0
    assert len(result["results"]) == 1
    assert not source.exists()


def test_real_bubblewrap_delete_and_undo_roundtrip(
        tmp_path: Path, monkeypatch) -> None:
    import agent_runtime

    if not sandbox.bwrap_available():
        pytest.skip("bubblewrap unavailable")

    history = tmp_path / "managed-history"
    source = tmp_path / "sandbox-source.txt"
    source.write_bytes(b"sandbox delete")
    source.chmod(0o640)
    expected_mtime = 1_700_000_100_000_000_000
    os.utime(source, ns=(expected_mtime, expected_mtime))
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(history))
    monkeypatch.setattr(agent_runtime, "_undo_pending", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_runtime, "_undo_done", lambda *_a, **_k: None)
    executor = _catalog().executors["delete_files"]

    result = agent_runtime.invoke_executor(
        executor,
        {"paths": [str(source)]},
        timeout_s=15,
        turn_id="bubblewrap-delete",
        actor="host",
        channel="test",
    )
    if (not result.get("ok") and "bwrap:" in str(result.get("error"))
            and "Operation not permitted" in str(result.get("error"))):
        pytest.skip("kernel temporarily denied bubblewrap namespace creation")

    assert result["ok"] is True, result
    assert not source.exists()
    assert Path(result["results"][0]["blob_path"]).is_file()
    restored = _restore_blob_backup({}, result)
    assert restored["ok"] is True
    assert source.read_bytes() == b"sandbox delete"
    assert stat.S_IMODE(source.stat().st_mode) == 0o640
    assert source.stat().st_mtime_ns == expected_mtime
