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
        {"name": "drive:permissions", "hint": ["drive:share"]},
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
