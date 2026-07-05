"""Regressioni circoscritte per il backend Google Drive.

Il backend deve essere autosufficiente sugli ID Drive: accetta ID diretti,
`entries` risolte da `from_step`, oppure locatori strutturati (`query`,
`name`, `pattern`). Non dipende dal composer e non modifica il motore.
"""
from __future__ import annotations

import sys
from pathlib import Path


_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)


def test_google_drive_read_accepts_entries_from_find(monkeypatch):
    from backends.files import google_workspace as gw

    calls = []

    def fake_run_drive(argv, *, executor, args_base, result_kind="entries"):
        calls.append((argv, executor, args_base, result_kind))
        return {
            "id": argv[-1],
            "name": "KAKEBO SPESE 2026",
            "mimeType": "text/plain",
            "content": "contenuto del file",
        }, None

    monkeypatch.setattr(gw, "_run_drive", fake_run_drive)

    out = gw.read({
        "client": "google_workspace",
        "entries": [{"id": "drive-file-1", "name": "KAKEBO SPESE 2026"}],
    })

    assert out["ok"] is True
    assert out["used"] == 1
    assert out["entries"][0]["id"] == "drive-file-1"
    assert out["entries"][0]["content"] == "contenuto del file"
    assert calls[0][0] == ["drive", "read", "drive-file-1"]


def test_google_drive_read_resolves_query_name(monkeypatch):
    from backends.files import google_workspace as gw

    calls = []

    def fake_run_drive(argv, *, executor, args_base, result_kind="entries"):
        calls.append(argv)
        if argv[:2] == ["drive", "search"]:
            # Nomi NON esatti rispetto alla query: read resta VETTORIALE (§2.1).
            # Se un risultato avesse il NOME ESATTO della query, `find` lo
            # preferirebbe (narrowing single-target) — coperto dall'e2e compound.
            return [
                {"id": "drive-doc-1", "name": "KAKEBO SPESE 2026 - parte 1"},
                {"id": "drive-doc-2", "name": "KAKEBO SPESE 2026 - parte 2"},
            ], None
        return {
            "id": argv[-1],
            "name": argv[-1],
            "content": f"contenuto {argv[-1]}",
        }, None

    monkeypatch.setattr(gw, "_run_drive", fake_run_drive)

    out = gw.read({"client": "google_workspace", "query": "KAKEBO SPESE 2026"})

    assert out["ok"] is True
    assert out["used"] == 2
    assert [e["id"] for e in out["entries"]] == ["drive-doc-1", "drive-doc-2"]
    assert calls[0][:3] == ["drive", "search", "KAKEBO SPESE 2026"]
    assert calls[1] == ["drive", "read", "drive-doc-1"]
    assert calls[2] == ["drive", "read", "drive-doc-2"]


def test_google_drive_append_doc_accepts_entries_from_find(monkeypatch):
    from backends.files import google_workspace as gw

    calls = []

    def fake_run_drive(argv, *, executor, args_base, result_kind="entries"):
        calls.append((argv, executor, args_base, result_kind))
        return {"characters": 11, "inserted_at": 5}, None

    monkeypatch.setattr(gw, "_run_drive", fake_run_drive)

    out = gw.append_doc({
        "client": "google_workspace",
        "entries": [{"id": "drive-doc-1", "name": "KAKEBO SPESE 2026"}],
        "text": "nuova riga",
    })

    assert out["ok"] is True
    assert out["document_id"] == "drive-doc-1"
    assert out["results"][0]["id"] == "drive-doc-1"
    assert calls[0][0] == ["docs", "append", "drive-doc-1", "--text", "nuova riga"]


def test_google_drive_write_spreadsheet_query_ambiguous_asks_choice(monkeypatch):
    from backends.files import google_workspace as gw

    def fake_run_drive(argv, *, executor, args_base, result_kind="entries"):
        assert argv[:2] == ["drive", "search"]
        # Nomi NON esatti rispetto a "Budget": nessun match-esatto → resta
        # AMBIGUO (chiede la scelta). Con un nome esatto, find risolverebbe da sé.
        return [
            {"id": "sheet-1", "name": "Budget 2025",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
            {"id": "sheet-2", "name": "Budget 2026",
             "mimeType": "application/vnd.google-apps.spreadsheet"},
        ], None

    monkeypatch.setattr(gw, "_run_drive", fake_run_drive)

    out = gw.write_spreadsheet({
        "client": "google_workspace",
        "query": "Budget",
        "range": "Sheet1!A1",
        "values": [["x"]],
    })

    assert out["ok"] is True
    assert out["decision"] == "needs_inputs"
    assert out["needs_inputs"]["on_complete"]["executor"] == "write_files_spreadsheet"
    choices = out["needs_inputs"]["dialog"][0]["schema"]["choices"]
    assert [choice["value"] for choice in choices] == ["sheet-1", "sheet-2"]


def test_google_drive_read_doc_query_single_resolves(monkeypatch):
    from backends.files import google_workspace as gw

    calls = []

    def fake_run_drive(argv, *, executor, args_base, result_kind="entries"):
        calls.append(argv)
        if argv[:2] == ["drive", "search"]:
            return [{"id": "doc-1", "name": "Verbale",
                     "mimeType": "application/vnd.google-apps.document"}], None
        return {"title": "Verbale", "body": "testo"}, None

    monkeypatch.setattr(gw, "_run_drive", fake_run_drive)

    out = gw.read_doc({"client": "google_workspace", "query": "Verbale"})

    assert out["ok"] is True
    assert out["document_id"] == "doc-1"
    assert out["body_text"] == "testo"
    assert calls == [
        ["drive", "search", "Verbale", "--max", "25"],
        ["docs", "get", "doc-1"],
    ]
