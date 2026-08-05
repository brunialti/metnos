from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[3]
for candidate in (ROOT, ROOT / "runtime"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from executors.change_files_format import change_files_format as change_format
from reverse_patterns import _delete_created_paths, _restore_blob_backup


def _fake_conversion(monkeypatch, payload: bytes = b"converted") -> None:
    monkeypatch.setattr(change_format, "_binary_missing", lambda _cmd: None)

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(payload)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(change_format.subprocess, "run", fake_run)


def test_new_conversion_is_removed_by_catalog_reverse(monkeypatch, tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"source")
    _fake_conversion(monkeypatch)

    result = change_format.invoke({"paths": [str(source)], "to_format": "jpg"})

    row = result["results"][0]
    assert row["created"] is True
    assert row["path"] == str(tmp_path / "photo.jpg")
    reversed_result = _delete_created_paths({}, result)
    assert reversed_result["ok"] is True
    assert not Path(row["path"]).exists()


def test_overwrite_conversion_restores_previous_bytes(monkeypatch, tmp_path):
    source = tmp_path / "photo.png"
    destination = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"previous")
    history = tmp_path / "history"
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(history))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-format")
    _fake_conversion(monkeypatch, b"replacement")

    result = change_format.invoke({
        "paths": [str(source)], "to_format": "jpg", "overwrite": True,
    })

    row = result["results"][0]
    assert row["created"] is False
    assert Path(row["prev_blob_path"]).read_bytes() == b"previous"
    assert destination.read_bytes() == b"replacement"
    reversed_result = _restore_blob_backup({}, result)
    assert reversed_result["ok"] is True
    assert destination.read_bytes() == b"previous"


def test_failed_overwrite_rolls_back_immediately(monkeypatch, tmp_path):
    source = tmp_path / "photo.png"
    destination = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"previous")
    monkeypatch.setenv("METNOS_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("METNOS_TURN_ID", "turn-format-failed")
    monkeypatch.setattr(change_format, "_binary_missing", lambda _cmd: None)

    def failed_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"partial-corruption")
        return SimpleNamespace(returncode=1, stdout="", stderr="failure")

    monkeypatch.setattr(change_format.subprocess, "run", failed_run)

    result = change_format.invoke({
        "paths": [str(source)], "to_format": "jpg", "overwrite": True,
    })

    assert result["results"][0]["ok"] is False
    assert destination.read_bytes() == b"previous"
