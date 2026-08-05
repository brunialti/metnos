from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_unified_runner_delegates_and_persists_result(tmp_path, monkeypatch):
    import build_runner

    corpus = tmp_path / "photos"
    corpus.mkdir()
    monkeypatch.setattr(build_runner, "_PROGRESS_DIR", tmp_path / "progress")
    monkeypatch.setattr(build_runner, "_COMPLETE_DIR", tmp_path / "complete")
    monkeypatch.setattr(build_runner, "_INDEX_BASE", tmp_path / "index")
    monkeypatch.setitem(sys.modules, "create_images_indices", SimpleNamespace(
        invoke=lambda args: {
            "ok": True, "n_entries_total": 3, "ok_count": 3,
            "fail_count": 0, "index_path": str(tmp_path / "index"),
        }))

    rc = build_runner.main([
        "--base-path", str(corpus), "--idx", "unified", "--actor", "alice",
    ])

    assert rc == 0
    records = list((tmp_path / "progress").glob("*.json"))
    assert len(records) == 1
    progress = json.loads(records[0].read_text())
    assert progress["state"] == "done"
    assert progress["n_entries"] == 3
    marker = json.loads(next((tmp_path / "complete").glob("*.json")).read_text())
    assert marker["ok"] is True
    assert marker["actor"] == "alice"


def test_unified_runner_reports_builder_failure(tmp_path, monkeypatch):
    import build_runner

    corpus = tmp_path / "photos"
    corpus.mkdir()
    monkeypatch.setattr(build_runner, "_PROGRESS_DIR", tmp_path / "progress")
    monkeypatch.setattr(build_runner, "_COMPLETE_DIR", tmp_path / "complete")
    monkeypatch.setattr(build_runner, "_INDEX_BASE", tmp_path / "index")
    monkeypatch.setitem(sys.modules, "create_images_indices", SimpleNamespace(
        invoke=lambda args: {"ok": False, "error": "backend unavailable"}))

    assert build_runner.main([
        "--base-path", str(corpus), "--idx", "unified",
    ]) == 4
    progress = json.loads(next((tmp_path / "progress").glob("*.json")).read_text())
    assert progress["state"] == "error"
    assert progress["error"] == "backend unavailable"
