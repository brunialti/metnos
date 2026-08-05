from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
for candidate in (ROOT, ROOT / "runtime"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from executors.write_files_doc import write_files_doc  # noqa: E402


def test_append_reverse_removes_exact_recorded_range(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_delete_range(args: dict) -> dict:
        calls.append(dict(args))
        return {
            "ok": True,
            "results": [{"ok": True, "document_id": args["document_id"]}],
        }

    monkeypatch.setattr(
        write_files_doc.google_workspace, "delete_doc_range", fake_delete_range,
    )

    result = write_files_doc.reverse({}, {
        "document_id": "doc-1",
        "results": [{
            "document_id": "doc-1",
            "inserted_at": 17,
            "characters_appended": 9,
        }],
    })

    assert result["ok"] is True
    assert result["ok_count"] == 1
    assert calls == [{"document_id": "doc-1", "start": 17, "end": 26}]


def test_append_reverse_fails_honestly_without_recorded_range(
        monkeypatch) -> None:
    monkeypatch.setattr(
        write_files_doc.google_workspace,
        "delete_doc_range",
        lambda _args: (_ for _ in ()).throw(AssertionError("must not call")),
    )

    result = write_files_doc.reverse({}, {
        "document_id": "doc-1",
        "results": [{"document_id": "doc-1", "characters_appended": 9}],
    })

    assert result["ok"] is False
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
