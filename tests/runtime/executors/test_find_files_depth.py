from __future__ import annotations

import sys
from pathlib import Path


RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

from backends.files import local  # noqa: E402


def test_find_prunes_tree_at_max_depth(tmp_path):
    (tmp_path / "shallow.pdf").write_bytes(b"one")
    (tmp_path / "d1").mkdir()
    (tmp_path / "d1" / "deep.pdf").write_bytes(b"two")
    (tmp_path / "d1" / "d2").mkdir()
    (tmp_path / "d1" / "d2" / "must_not_be_visited.pdf").write_bytes(b"three")

    result = local.find({
        "base_path": str(tmp_path),
        "patterns": ["*.pdf"],
        "recursive": True,
        "max_depth": 2,
        "max_results": 100,
    })

    assert result["ok"] is True
    assert {entry["name"] for entry in result["entries"]} == {
        "shallow.pdf", "deep.pdf",
    }
    # d1, shallow.pdf, d1/d2 e d1/deep.pdf. Il file a profondita' 3 non
    # viene nemmeno enumerato: con rglob il contatore valeva 5.
    assert result["metadata"]["visited"] == 4


def test_find_max_depth_zero_returns_no_children(tmp_path):
    (tmp_path / "child.txt").write_text("x", encoding="utf-8")

    result = local.find({
        "base_path": str(tmp_path),
        "patterns": ["*"],
        "recursive": True,
        "max_depth": 0,
    })

    assert result["ok"] is True
    assert result["entries"] == []
    assert result["metadata"]["visited"] == 0
