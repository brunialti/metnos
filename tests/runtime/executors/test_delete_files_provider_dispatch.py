from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
for candidate in (ROOT, ROOT / "runtime"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from executors.delete_files import delete_files
from backends.files import google_workspace


def test_google_file_ids_dispatch_to_drive_delete(monkeypatch):
    calls = []
    backend = SimpleNamespace(delete=lambda args: (
        calls.append(dict(args)) or {
            "ok": True,
            "n_deleted": 1,
            "results": [{"ok": True, "id": "drive-1", "status": "trashed"}],
        }))
    monkeypatch.setattr(delete_files, "_backend", lambda client: (
        backend if client == "google_workspace" else None))

    result = delete_files.invoke({
        "client": "google_workspace", "file_ids": ["drive-1"],
    })

    assert result["ok"] is True
    assert calls == [{
        "client": "google_workspace", "file_ids": ["drive-1"],
    }]


def test_drive_trash_records_jit_restore_metadata(monkeypatch):
    monkeypatch.setattr(
        google_workspace, "_ids_from_args_or_locator",
        lambda _args, **_kwargs: (["drive-id-000001"], [], None),
    )
    calls = []

    def fake_run(argv, *, executor, args_base, result_kind="entries"):
        calls.append((argv, executor, args_base, result_kind))
        return {"status": "trashed"}, None

    monkeypatch.setattr(google_workspace, "_run_drive", fake_run)

    result = google_workspace.delete({
        "client": "google_workspace", "file_ids": ["drive-id-000001"],
    })

    assert result["ok"] is True
    assert result["_undo"] == {
        "reverse_pattern": "restore_trashed_files",
        "ids": ["drive-id-000001"],
        "scope": {"client": "google_workspace"},
    }
    assert calls[0][0] == ["drive", "delete", "drive-id-000001"]


def test_drive_permanent_delete_never_claims_undo(monkeypatch):
    monkeypatch.setattr(
        google_workspace, "_ids_from_args_or_locator",
        lambda _args, **_kwargs: (["drive-id-000001"], [], None),
    )
    monkeypatch.setattr(
        google_workspace, "_run_drive",
        lambda *_args, **_kwargs: ({"status": "deleted"}, None),
    )

    result = google_workspace.delete({
        "client": "google_workspace", "file_ids": ["drive-id-000001"],
        "permanent": True,
    })

    assert result["ok"] is True
    assert "_undo" not in result


def test_restore_trashed_calls_restore_subcommand_and_deduplicates(monkeypatch):
    calls = []

    def fake_run(argv, *, executor, args_base, result_kind="entries"):
        calls.append((argv, executor, args_base, result_kind))
        return {"status": "restored"}, None

    monkeypatch.setattr(google_workspace, "_run_drive", fake_run)

    result = google_workspace.restore_trashed({
        "ids": ["drive-id-000001", "drive-id-000001", "drive-id-000002"],
    })

    assert result["ok"] is True
    assert result["ok_count"] == 2
    assert [call[0] for call in calls] == [
        ["drive", "restore", "drive-id-000001"],
        ["drive", "restore", "drive-id-000002"],
    ]
