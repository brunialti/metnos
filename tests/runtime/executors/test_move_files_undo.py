from __future__ import annotations

from pathlib import Path
import sys

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from backends.files import local
from reverse_patterns import apply_patterns


_REVERSE = ["swap_src_dst", "restore_blob_backup", "delete_created_dirs"]


def test_overwrite_move_restores_source_and_previous_destination(
        monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-move-overwrite")
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source-bytes", encoding="utf-8")
    destination.write_text("destination-bytes", encoding="utf-8")

    result = local.move({
        "entries": [{"path": str(source)}],
        "dst_template": str(destination),
        "overwrite": True,
    })

    assert result["ok"] is True
    row = result["results"][0]
    assert Path(row["prev_blob_path"]).read_text(encoding="utf-8") == (
        "destination-bytes")
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "source-bytes"

    reversed_result = apply_patterns(_REVERSE, {}, result)

    assert reversed_result["ok"] is True
    assert source.read_text(encoding="utf-8") == "source-bytes"
    assert destination.read_text(encoding="utf-8") == "destination-bytes"


def test_overwrite_move_refuses_unbacked_directory_destination(
        monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    source = tmp_path / "source.txt"
    destination = tmp_path / "occupied"
    source.write_text("source", encoding="utf-8")
    destination.mkdir()
    (destination / "keep.txt").write_text("keep", encoding="utf-8")

    result = local.move({
        "entries": [{"path": str(source)}],
        "dst_template": str(destination),
        "overwrite": True,
    })

    assert result["ok"] is False
    assert result["failed"][0]["error_code"] == "ERR_UNDO_BACKUP_FAILED"
    assert source.read_text(encoding="utf-8") == "source"
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep"
