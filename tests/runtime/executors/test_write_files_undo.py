from __future__ import annotations

from pathlib import Path
import sys

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from backends.files import local
from reverse_patterns import _delete_created_paths, _restore_blob_backup


def test_write_new_file_can_be_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-write-new")
    path = tmp_path / "new.txt"

    result = local.write({"path": str(path), "content": "new"})

    assert result["ok"] is True
    assert result["results"][0]["created"] is True
    reversed_result = _delete_created_paths({}, result)
    assert reversed_result["ok"] is True
    assert not path.exists()


def test_write_overwrite_can_restore_previous_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-write-overwrite")
    path = tmp_path / "existing.txt"
    path.write_text("before", encoding="utf-8")

    result = local.write({"path": str(path), "content": "after"})

    row = result["results"][0]
    assert row["created"] is False
    assert Path(row["prev_blob_path"]).read_text(encoding="utf-8") == "before"
    reversed_result = _restore_blob_backup({}, result)
    assert reversed_result["ok"] is True
    assert path.read_text(encoding="utf-8") == "before"


def test_write_append_can_restore_previous_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-write-append")
    path = tmp_path / "existing.txt"
    path.write_text("before", encoding="utf-8")

    result = local.write({
        "path": str(path), "content": "+after", "mode": "append",
    })

    assert path.read_text(encoding="utf-8") == "before+after"
    reversed_result = _restore_blob_backup({}, result)
    assert reversed_result["ok"] is True
    assert path.read_text(encoding="utf-8") == "before"
