"""Unit del backend Google Photos (`backends/images/google_photos.py`).

Deterministici, NIENTE rete: `run_with_retry` e `_ensure_fresh_token` mockati
(spec Google Photos §3.7). Copre: risoluzione album per nome (+ create se
manca), vettorialita' per-file, shape §2.8 su errore, nota IRREVERSIBILE nel
message, find→entries, albums-flag, album inesistente onesto, download→_undo,
troncamento §2.7, propagazione needs_inputs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

from backends.images import google_photos as gp


@pytest.fixture(autouse=True)
def _token_ok(monkeypatch):
    # Token sempre fresco: isola la logica dal refresh OAuth (rete).
    monkeypatch.setattr(gp, "_ensure_fresh_token", lambda: True)


def _install_runner(monkeypatch, handler):
    """Sostituisce `run_with_retry` con `handler(argv) -> (data, err)`."""
    monkeypatch.setattr(gp, "run_with_retry", lambda argv, **kw: handler(argv))


# ── upload: due fasi (bytes → batchCreate a chunk di 50) ────────────────────

def _ok_batch(argv):
    """Risposta batch-create ok per TUTTI gli item passati in --items."""
    items = json.loads(argv[argv.index("--items") + 1])
    return ({"results": [{"ok": True, "media_item_id": f"M{i}",
                          "filename": it["fileName"], "status_message": ""}
                         for i, it in enumerate(items)]}, None)


def test_upload_resolves_existing_album(monkeypatch):
    calls = []

    def handler(argv):
        calls.append(argv)
        if argv[:2] == ["photos", "album-list"]:
            return ([{"id": "ALB1", "title": "Vacanze", "items_count": 2, "url": ""}], None)
        if argv[:2] == ["photos", "upload-bytes"]:
            return ({"uploadToken": "T1", "fileName": "a.jpg"}, None)
        if argv[:2] == ["photos", "batch-create"]:
            return _ok_batch(argv)
        raise AssertionError(f"argv inatteso: {argv}")

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/x/a.jpg"], "album": "Vacanze"})
    assert out["ok"] is True and out["ok_count"] == 1
    bc = [c for c in calls if c[:2] == ["photos", "batch-create"]][0]
    assert "ALB1" in bc                               # album-id risolto nel batch
    assert not any(c[:2] == ["photos", "album-create"] for c in calls)  # non ri-crea
    # nota IRREVERSIBILE appesa (§3.6)
    assert "annullabile" in out["message"] or "undoable" in out["message"]


def test_upload_creates_missing_album(monkeypatch):
    def handler(argv):
        if argv[:2] == ["photos", "album-list"]:
            return ([], None)                          # nessun album
        if argv[:2] == ["photos", "album-create"]:
            return ({"id": "NEW", "title": argv[2]}, None)
        if argv[:2] == ["photos", "upload-bytes"]:
            return ({"uploadToken": "T", "fileName": "a.jpg"}, None)
        if argv[:2] == ["photos", "batch-create"]:
            assert "NEW" in argv                       # batch nell'album creato
            return _ok_batch(argv)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/x/a.jpg"], "album": "Nuovo"})
    assert out["ok"] and out["results"][0]["album"] == "Nuovo"


def test_upload_chunks_batchcreate_at_50(monkeypatch):
    """Spec §3.2/§3.3: N upload-bytes per file, batchCreate a chunk di 50."""
    counts = {"bytes": 0, "batch_sizes": []}

    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            counts["bytes"] += 1
            return ({"uploadToken": f"T{counts['bytes']}",
                     "fileName": f"f{counts['bytes']}.jpg"}, None)
        if argv[:2] == ["photos", "batch-create"]:
            items = json.loads(argv[argv.index("--items") + 1])
            counts["batch_sizes"].append(len(items))
            return _ok_batch(argv)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    paths = [f"/x/f{i}.jpg" for i in range(120)]
    out = gp.upload({"paths": paths, "max_total": 200})
    assert out["ok"] and out["ok_count"] == 120
    assert counts["bytes"] == 120
    assert counts["batch_sizes"] == [50, 50, 20]       # chunking al contratto API


# ── upload: onesta' §2.8 + troncamento §2.7 ──────────────────────────────────

def test_upload_all_fail_honest_shape(monkeypatch):
    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            return (None, {"ok": False, "error_class": "server_error", "error": "boom"})
        raise AssertionError(argv)                     # nessun batch se 0 staged

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/x/a.jpg", "/x/b.jpg"]})
    assert out["ok"] is False
    assert out["ok_count"] == 0 and out["fail_count"] == 2
    assert isinstance(out["results"], list)            # §2.6 sempre lista
    assert out["error_class"] == "server_error" and out["error"]
    assert out["revertible"] is False


def test_upload_partial_bytes_fail_ok_count_honest(monkeypatch):
    seq = iter([({"uploadToken": "T1", "fileName": "a"}, None),
                (None, {"ok": False, "error_class": "server_error", "error": "x"})])

    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            return next(seq)
        if argv[:2] == ["photos", "batch-create"]:
            items = json.loads(argv[argv.index("--items") + 1])
            assert len(items) == 1                     # solo il file staged
            return _ok_batch(argv)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/a.jpg", "/b.jpg"]})
    assert out["ok_count"] == 1 and out["fail_count"] == 1
    assert out["ok"] is False                          # un fallimento → ok False


def test_upload_batch_item_fail_honest(monkeypatch):
    """batchCreate 200 ma un item senza mediaItem → quel file e' FALLITO (§2.8)."""
    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            n = argv[2][-5]                            # /a.jpg → 'a'
            return ({"uploadToken": f"T{n}", "fileName": f"{n}.jpg"}, None)
        if argv[:2] == ["photos", "batch-create"]:
            return ({"results": [
                {"ok": True, "media_item_id": "M1", "filename": "a.jpg",
                 "status_message": ""},
                {"ok": False, "media_item_id": "", "filename": "b.jpg",
                 "status_message": "quota exceeded"},
            ]}, None)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/a.jpg", "/b.jpg"]})
    assert out["ok_count"] == 1 and out["fail_count"] == 1
    assert "quota exceeded" in (out["failed"][0].get("error") or "")


def test_upload_truncation(monkeypatch):
    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            return ({"uploadToken": "T", "fileName": "x"}, None)
        if argv[:2] == ["photos", "batch-create"]:
            return _ok_batch(argv)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": ["/a", "/b", "/c"], "max_total": 2})
    assert out["ok_count"] == 2
    assert out["truncated"] is True and out["available_total"] == 3
    assert out["cap_field"] == "max_total" and out["cap_value"] == 2


# ── find ─────────────────────────────────────────────────────────────────────

def test_find_maps_entries(monkeypatch):
    def handler(argv):
        assert argv[:2] == ["photos", "search"]
        return ({"items": [{"id": "P1", "filename": "p.jpg", "mime": "image/jpeg",
                            "created_at": "2026-01-01T00:00:00Z",
                            "width": 10, "height": 20}],
                 "nextPageToken": ""}, None)

    _install_runner(monkeypatch, handler)
    out = gp.find({"year": 2026})
    assert out["ok"] and out["used"] == 1
    assert out["entries"][0]["id"] == "P1"


def test_find_albums_flag_lists_albums(monkeypatch):
    def handler(argv):
        assert argv[:2] == ["photos", "album-list"]
        return ([{"id": "A", "title": "T", "items_count": 5, "url": "u"}], None)

    _install_runner(monkeypatch, handler)
    out = gp.find({"albums": True})
    assert out["ok"] and out["entries"][0]["title"] == "T"
    assert out.get("albums_app_created_only") is True
    # Perimetro DICHIARATO (§2.8, turn e2b0e529): la nota app-created-only
    # viaggia nel result `message` e il render @table la appende sempre.
    assert isinstance(out.get("message"), str) and out["message"].strip()


def test_find_album_not_found_empty_honest(monkeypatch):
    def handler(argv):
        if argv[:2] == ["photos", "album-list"]:
            return ([{"id": "A", "title": "Altro", "items_count": 1, "url": ""}], None)
        raise AssertionError(argv)                     # niente search se album non risolve

    _install_runner(monkeypatch, handler)
    out = gp.find({"album": "Inesistente"})
    assert out["ok"] is True and out["entries"] == [] and out["used"] == 0


def test_upload_expands_directory_to_images(monkeypatch, tmp_path):
    """§2.4: «carica le foto della cartella X» arriva con la DIR in paths —
    l'executor la espande ai file immagine contenuti (visto live take-5)."""
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"x")
    (tmp_path / "note.txt").write_bytes(b"x")     # non-immagine: ignorato
    uploaded = []

    def handler(argv):
        if argv[:2] == ["photos", "upload-bytes"]:
            uploaded.append(argv[2])
            return ({"uploadToken": f"T{len(uploaded)}",
                     "fileName": Path(argv[2]).name}, None)
        if argv[:2] == ["photos", "batch-create"]:
            return _ok_batch(argv)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.upload({"paths": [str(tmp_path)], "album": ""})
    assert out["ok"] and out["ok_count"] == 2
    assert sorted(Path(p).name for p in uploaded) == ["a.jpg", "b.png"]


def test_upload_empty_dir_honest_not_found(monkeypatch, tmp_path):
    _install_runner(monkeypatch, lambda argv: (_ for _ in ()).throw(AssertionError(argv)))
    out = gp.upload({"paths": [str(tmp_path)]})   # dir senza immagini
    assert out["ok"] is False and out["error_class"] == "not_found"
    assert out["fail_count"] == 1 and out["ok_count"] == 0


def test_find_pagination_constant_page_size(monkeypatch):
    """Col pageToken l'API esige GLI STESSI parametri (HTTP 400 visto live):
    pageSize costante su ogni pagina, cap applicato client-side."""
    sizes, tokens = [], []

    def handler(argv):
        assert argv[:2] == ["photos", "search"]
        sizes.append(argv[argv.index("--max") + 1])
        tok = argv[argv.index("--page-token") + 1] if "--page-token" in argv else ""
        tokens.append(tok)
        page = [{"id": f"P{len(tokens)}-{i}", "filename": "x.jpg"} for i in range(3)]
        next_tok = "T2" if len(tokens) == 1 else ""
        return ({"items": page, "nextPageToken": next_tok}, None)

    _install_runner(monkeypatch, handler)
    out = gp.find({"max_results": 5})
    assert sizes == ["100", "100"]          # MAI cambiare pageSize fra pagine
    assert tokens == ["", "T2"]
    assert out["used"] == 5                 # cap client-side (6 raccolte → 5)
    assert out["truncated"] is True


# ── download ─────────────────────────────────────────────────────────────────

def test_download_maps_results_and_undo(monkeypatch, tmp_path):
    def handler(argv):
        assert argv[:2] == ["photos", "download"]
        return ({"path": str(tmp_path / "a.jpg"), "filename": "a.jpg", "bytes": 100}, None)

    _install_runner(monkeypatch, handler)
    out = gp.download({"ids": ["M1"], "dst_dir": str(tmp_path)})
    assert out["ok"] and out["ok_count"] == 1
    assert out["results"][0]["local_path"].endswith("a.jpg")
    assert out["_undo"]["reverse_pattern"] == "delete_created_paths"
    assert out["_undo"]["paths"] == [str(tmp_path / "a.jpg")]


def test_download_from_entries_piping(monkeypatch, tmp_path):
    def handler(argv):
        return ({"path": str(tmp_path / f"{argv[2]}.jpg"), "filename": "x", "bytes": 1}, None)

    _install_runner(monkeypatch, handler)
    out = gp.download({"entries": [{"id": "E1"}, {"id": "E2"}], "dst_dir": str(tmp_path)})
    assert out["ok_count"] == 2


# ── picker (P3, D8): create→dialog, resume not-ready→dialog, ready→download ──

def test_picker_create_returns_dialog_with_link(monkeypatch):
    def handler(argv):
        assert argv[:2] == ["photos", "picker-create"]
        return ({"session_id": "S1", "picker_uri": "https://photos.google.com/pick/S1",
                 "media_items_set": False}, None)

    _install_runner(monkeypatch, handler)
    out = gp.picker({"picker": True})
    assert out["decision"] == "needs_inputs"
    ni = out["needs_inputs"]
    assert "https://photos.google.com/pick/S1" in ni["dialog"][0]["prompt"]
    oc = ni["on_complete"]
    assert oc["type"] == "resume_executor_with_values"
    assert oc["executor"] == "get_images_google_photos"
    assert oc["args_base"]["picker_session_id"] == "S1"
    assert out.get("final_message_hint")          # il link arriva in chat


def test_picker_resume_not_ready_reasks(monkeypatch):
    def handler(argv):
        assert argv[:2] == ["photos", "picker-get"]
        return ({"session_id": "S1", "picker_uri": "https://p/S1",
                 "media_items_set": False}, None)

    _install_runner(monkeypatch, handler)
    out = gp.picker({"picker": True, "picker_session_id": "S1"})
    assert out["decision"] == "needs_inputs"      # onesto: non ancora pronta
    assert "https://p/S1" in out["needs_inputs"]["dialog"][0]["prompt"]


def test_picker_resume_ready_downloads(monkeypatch, tmp_path):
    calls = []

    def handler(argv):
        calls.append(argv[1])
        if argv[:2] == ["photos", "picker-get"]:
            return ({"session_id": "S1", "media_items_set": True}, None)
        if argv[:2] == ["photos", "picker-download"]:
            assert "S1" in argv
            return ({"results": [
                {"ok": True, "id": "I1", "filename": "a.jpg",
                 "path": str(tmp_path / "a.jpg"), "bytes": 10},
                {"ok": False, "id": "I2", "filename": "b.jpg",
                 "error": "HTTP 500"},
            ]}, None)
        raise AssertionError(argv)

    _install_runner(monkeypatch, handler)
    out = gp.picker({"picker": True, "picker_session_id": "S1",
                     "dst_dir": str(tmp_path)})
    assert calls == ["picker-get", "picker-download"]
    assert out["ok_count"] == 1 and out["fail_count"] == 1
    assert out["ok"] is False                     # un fallito → onesto
    assert out["_undo"]["reverse_pattern"] == "delete_created_paths"


def test_picker_needs_inputs_oauth_propagates(monkeypatch):
    monkeypatch.setattr(gp, "_ensure_fresh_token", lambda: False)
    out = gp.picker({"picker": True})
    assert out.get("decision") == "needs_inputs"  # setup OAuth, non picker


# ── errori d'arg (senza rete) + needs_inputs ─────────────────────────────────

def test_upload_missing_paths_invalid_args():
    out = gp.upload({"album": "x"})
    assert out["ok"] is False and out["error_class"] == "invalid_args"


def test_download_missing_ids_invalid_args():
    out = gp.download({})
    assert out["ok"] is False and out["error_class"] == "invalid_args"


def test_upload_needs_inputs_propagates(monkeypatch):
    monkeypatch.setattr(gp, "_ensure_fresh_token", lambda: False)
    out = gp.upload({"paths": ["/x/a.jpg"]})
    assert out.get("decision") == "needs_inputs"
