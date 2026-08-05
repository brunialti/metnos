"""Collision policy for title-derived local spreadsheet artifacts."""
from __future__ import annotations

import sys
from pathlib import Path


EXECUTOR_DIR = (Path(__file__).resolve().parents[3] / "executors" /
                "create_files_spreadsheet")
if str(EXECUTOR_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTOR_DIR))

import create_files_spreadsheet as spreadsheet  # noqa: E402
from backends.files import local  # noqa: E402


def _use_output_dir(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(local, "_spreadsheet_out_dir", lambda: path)


def test_implicit_title_collision_uses_short_turn_id(tmp_path, monkeypatch):
    _use_output_dir(monkeypatch, tmp_path)
    original = tmp_path / "Immagini duplicate.xlsx"
    original.write_bytes(b"original")

    result = spreadsheet.invoke({
        "title": "Immagini duplicate",
        "values": [["path"], ["/images/a.jpg"]],
        "_turn_id": "843113fe02e543d2",
    })

    expected = tmp_path / "Immagini duplicate_843113fe.xlsx"
    assert result["ok"] is True
    assert result["path"] == str(expected)
    assert expected.is_file()
    assert original.read_bytes() == b"original"


def test_same_turn_collision_adds_bounded_ordinal(tmp_path, monkeypatch):
    _use_output_dir(monkeypatch, tmp_path)
    (tmp_path / "report.xlsx").write_bytes(b"original")
    (tmp_path / "report_843113fe.xlsx").write_bytes(b"previous turn output")

    result = spreadsheet.invoke({
        "title": "report",
        "values": [["value"], [1]],
        "_turn_id": "843113fe02e543d2",
    })

    assert result["ok"] is True
    assert result["path"] == str(tmp_path / "report_843113fe_2.xlsx")


def test_remote_turn_environment_is_valid_collision_source(
        tmp_path, monkeypatch):
    _use_output_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("METNOS_TURN_ID", "abc12345remote")
    (tmp_path / "report.xlsx").write_bytes(b"original")

    result = spreadsheet.invoke({
        "title": "report",
        "values": [["value"], [1]],
    })

    assert result["ok"] is True
    assert result["path"] == str(tmp_path / "report_abc12345.xlsx")


def test_explicit_path_collision_remains_fail_closed(tmp_path, monkeypatch):
    _use_output_dir(monkeypatch, tmp_path)
    explicit = tmp_path / "chosen.xlsx"
    explicit.write_bytes(b"original")

    result = spreadsheet.invoke({
        "path": str(explicit),
        "values": [["value"], [1]],
        "_turn_id": "843113fe02e543d2",
    })

    assert result["ok"] is False
    assert result["error_code"] == "ERR_DST_EXISTS"
    assert explicit.read_bytes() == b"original"
    assert not (tmp_path / "chosen_843113fe.xlsx").exists()
