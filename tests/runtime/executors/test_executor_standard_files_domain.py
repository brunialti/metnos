"""Domain gate for file metadata, writes, and provider ACL sharing."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.get_files import get_files  # noqa: E402
from executors.share_files import share_files  # noqa: E402
from executors.write_files import write_files  # noqa: E402


def _manifest(name: str) -> dict:
    path = ROOT / "executors" / name / "manifest.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_files_domain_declares_narrow_authority() -> None:
    get_manifest = _manifest("get_files")
    write_manifest = _manifest("write_files")
    share_manifest = _manifest("share_files")

    assert get_manifest["executor_standard"] == "metnos.executor/1.0"
    assert get_manifest["capabilities"] == [
        {"name": "fs:read", "hint": ["~/**", "/tmp/**"]},
        {"name": "network:http", "hint": ["nominatim.openstreetmap.org"]},
    ]
    assert write_manifest["capabilities"][1]["when"] == {
        "arg": "client", "values": ["google_workspace"]}
    assert share_manifest["capabilities"] == [
        {"name": "drive:permissions", "hint": ["drive:share", "drive:unshare"]},
        {
            "name": "provider:access",
            "hint": ["google-workspace"],
            "when": {"arg": "client", "values": ["google_workspace"]},
        },
    ]


def test_get_files_reads_known_local_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("abc", encoding="utf-8")
    result = get_files.invoke({"entries": [{"path": str(source)}],
                               "fields": ["size"]})

    assert result["ok"] is True
    assert result["ok_count"] == 1
    assert result["entries"][0]["size_bytes"] == 3


def test_write_files_writes_vector_result_to_tmp(tmp_path: Path) -> None:
    target = tmp_path / "written.txt"
    result = write_files.invoke({"path": str(target), "content": "standard"})

    assert result["ok"] is True
    assert result["ok_count"] == 1
    assert result["results"][0]["path"] == str(target)
    assert target.read_text(encoding="utf-8") == "standard"


def test_share_files_calls_only_selected_provider(monkeypatch) -> None:
    calls = []

    class Backend:
        @staticmethod
        def share(args):
            calls.append(dict(args))
            return {"ok": True, "n_shared": 1,
                    "results": [{"id": "file-1"}], "failed": []}

    monkeypatch.setitem(share_files._HANDLERS, "google_workspace", Backend)
    result = share_files.invoke({
        "file_ids": ["file-1"], "email": "guest@example.com",
        "client": "google_workspace",
    })

    assert result["ok"] is True
    assert calls == [{
        "file_ids": ["file-1"], "email": "guest@example.com",
        "client": "google_workspace",
    }]


def test_share_files_reverse_uses_exact_permission_receipt(monkeypatch) -> None:
    calls = []

    def fake_revoke(args):
        calls.append(args)
        return {"ok": True, "ok_count": 1, "fail_count": 0,
                "results": [{"status": "revoked"}], "failed": []}

    monkeypatch.setattr(
        share_files.google_workspace, "revoke_permissions", fake_revoke)
    result = share_files.reverse({}, {
        "results": [{
            "id": "drive-file-000001", "permission_id": "perm-001",
        }],
        "_undo": {
            "reverse_pattern": "module.reverse",
            "permissions": [{
                "file_id": "drive-file-000001",
                "permission_id": "perm-001",
            }],
        },
    })

    assert result["ok"] is True
    assert calls == [{"permissions": [{
        "file_id": "drive-file-000001", "permission_id": "perm-001",
    }]}]


def test_google_share_emits_exact_undo_receipt(monkeypatch) -> None:
    calls = []

    def fake_run(argv, *, executor, args_base, result_kind):
        calls.append((argv, executor, args_base, result_kind))
        return {"permissionId": "permission-001"}, None

    monkeypatch.setattr(share_files.google_workspace, "_run_drive", fake_run)
    result = share_files.google_workspace.share({
        "file_id": "drive-file-000001",
        "email": "guest@example.com",
        "role": "reader",
    })

    assert result["ok"] is True
    assert result["results"][0]["file_id"] == "drive-file-000001"
    assert result["_undo"] == {
        "reverse_pattern": "module.reverse",
        "permissions": [{
            "file_id": "drive-file-000001",
            "permission_id": "permission-001",
        }],
        "scope": {"client": "google_workspace"},
    }
    assert calls[0][0] == [
        "drive", "share", "drive-file-000001", "--role", "reader",
        "--type", "user", "--email", "guest@example.com",
    ]


def test_google_share_reverse_revokes_only_recorded_permissions(
        monkeypatch) -> None:
    calls = []

    def fake_run(argv, *, executor, args_base, result_kind):
        calls.append((argv, executor, args_base, result_kind))
        return {"status": "revoked"}, None

    monkeypatch.setattr(share_files.google_workspace, "_run_drive", fake_run)
    result = share_files.google_workspace.revoke_permissions({
        "permissions": [
            {"file_id": "drive-file-000001",
             "permission_id": "permission-001"},
            {"file_id": "drive-file-000002",
             "permission_id": "permission-002"},
        ],
    })

    assert result["ok"] is True
    assert result["ok_count"] == 2
    assert [call[0] for call in calls] == [
        ["drive", "unshare", "drive-file-000001", "permission-001"],
        ["drive", "unshare", "drive-file-000002", "permission-002"],
    ]


def test_share_files_round_trip_through_undo_last_turn(
        tmp_path, monkeypatch) -> None:
    calls = []

    def fake_revoke(args):
        calls.append(args)
        return {
            "ok": True, "ok_count": 1, "fail_count": 0,
            "results": [{
                "ok": True, "file_id": "drive-file-000001",
                "permission_id": "permission-001", "status": "revoked",
            }],
            "failed": [],
        }

    monkeypatch.setattr(
        share_files.google_workspace, "revoke_permissions", fake_revoke)

    from undo import UndoLog
    log_path = tmp_path / "undo.jsonl"
    log = UndoLog(log_path)
    log.append_pending(
        "op-share", "turn-share", "share_files",
        {"file_id": "drive-file-000001", "email": "guest@example.com"},
        plan={}, actor="host",
    )
    log.append_done("op-share", {
        "ok": True,
        "results": [{
            "id": "drive-file-000001", "file_id": "drive-file-000001",
            "permission_id": "permission-001",
        }],
        "_undo": {
            "reverse_pattern": "module.reverse",
            "permissions": [{
                "file_id": "drive-file-000001",
                "permission_id": "permission-001",
            }],
        },
    })

    sys.path.insert(0, str(ROOT / "executors" / "undo_last_turn"))
    try:
        import undo_last_turn
        result = undo_last_turn.invoke({
            "log_path": str(log_path), "_actor": "host",
        })
    finally:
        sys.path.pop(0)

    assert result["ok"] is True, result
    assert result["undone_count"] == 1
    assert calls == [{"permissions": [{
        "file_id": "drive-file-000001", "permission_id": "permission-001",
    }]}]
