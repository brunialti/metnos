"""Bug zip-line (5/7/2026) — il resume di un dialog è un TURNO COMPLETO.

`process_completion_callback` ritorna `CompletionResult` (testo + attachments
+ meta del turno): prima ritornava solo str e un resume che ri-eseguiva un
turno intero (disambiguazione foto) BUTTAVA gallery/badge/meta — 74 foto rese
come testo nudo, status line vuota.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _fake_turnlog(**kw):
    class _L:
        final_message = kw.get("final_message", "trovate 74 foto")
        attachments = kw.get("attachments", [])
        turn_id = kw.get("turn_id", "t123abc")
        ts_start = kw.get("ts_start", 100.0)
        ts_end = kw.get("ts_end", 107.2)
        target_device = kw.get("target_device", None)
    return _L()


def test_completion_from_turnlog_carries_everything():
    from orchestration import _completion_from_turnlog
    atts = [{"kind": "image", "path": "/x/a.jpg", "basename": "a.jpg"}]
    cr = _completion_from_turnlog(_fake_turnlog(attachments=atts))
    assert cr.text == "trovate 74 foto"
    assert cr.attachments == atts
    assert cr.turn_id == "t123abc"
    assert cr.total_ms == 7200
    assert cr.target_device == ""
    # forma del payload final dei turni normali (bug dce8a3bc):
    assert cr.gallery_url == "/agent/gallery/t123abc"
    assert cr.n_total_matches == 1


def test_completion_path_badges_and_no_gallery_for_files():
    from orchestration import _completion_from_turnlog

    class _Step:
        def __init__(self, tool, ok=True):
            self.chosen_tool = tool
            self.error = None
            self.result = {"ok": ok}
    lg = _fake_turnlog(attachments=[{"kind": "file", "path": "/x.xlsx"}])
    lg.steps = [_Step("find_images_indices"), _Step("describe_entries")]
    cr = _completion_from_turnlog(lg)
    assert cr.path == [{"tool": "find_images_indices", "ok": True},
                       {"tool": "describe_entries", "ok": True}]
    # solo file → niente gallery (bug 7d2f734f: «gallery 1 foto» vuota)
    assert cr.gallery_url == "" and cr.n_total_matches == 0


def test_completion_from_turnlog_device_tag():
    from orchestration import _completion_from_turnlog
    cr = _completion_from_turnlog(_fake_turnlog(target_device="PC-ROBERTO"))
    assert cr.target_device == "PC-ROBERTO"


def test_wrapper_normalizes_legacy_str(monkeypatch):
    import orchestration as orch
    monkeypatch.setattr(orch, "_dispatch_completion",
                        lambda *a, **k: "solo testo")
    cr = orch.process_completion_callback("s1", "d1")
    assert isinstance(cr, orch.CompletionResult)
    assert cr.text == "solo testo"
    assert cr.attachments == [] and cr.turn_id == ""


def test_wrapper_passes_through_structured(monkeypatch):
    import orchestration as orch
    rich = orch.CompletionResult(text="ok", attachments=[{"kind": "image"}],
                                 turn_id="t9", total_ms=1500)
    monkeypatch.setattr(orch, "_dispatch_completion", lambda *a, **k: rich)
    cr = orch.process_completion_callback("s1", "d1")
    assert cr is rich


def test_rerun_disambiguated_returns_structured(monkeypatch):
    """Il dispatch della disambiguazione (il caso zip-line) ritorna il
    CompletionResult del NUOVO turno, non solo il testo."""
    import orchestration as orch

    class _AR:
        @staticmethod
        def run_turn(query, **kw):
            return _fake_turnlog(
                final_message="74 foto",
                attachments=[{"kind": "image", "path": "/x.jpg"}],
                turn_id="newturn1")
    monkeypatch.setitem(sys.modules, "agent_runtime", _AR)
    out = orch._process_rerun_query_disambiguated(
        {"type": "rerun_query_disambiguated",
         "query": "trova foto della zip line"},
        {"object": "images"}, actor="host", channel="http")
    assert isinstance(out, orch.CompletionResult)
    assert out.turn_id == "newturn1"
    assert out.attachments and out.text == "74 foto"


def test_daemon_sends_resume_media_chunked(monkeypatch):
    """Telegram: il resume con foto manda sendMediaGroup a chunk di 10
    (stessa forma dei turni normali); i file restano fuori dall'album."""
    import types
    from channels.daemon import ChannelDaemon
    import orchestration as orch

    sent = []

    class _Chan:
        name = "telegram"
        def send_media_group(self, *, chat_id, attachments, turn_id,
                             caption_first):
            sent.append((chat_id, len(attachments), caption_first))
            return {"ok": True}

    d = ChannelDaemon.__new__(ChannelDaemon)   # no __init__ (niente rete)
    d.channel = _Chan()
    atts = ([{"kind": "image", "path": f"/x/{i}.jpg"} for i in range(12)]
            + [{"kind": "file", "path": "/x/doc.xlsx"}])
    cr = orch.CompletionResult(text="ok", attachments=atts, turn_id="t1")
    d._send_resume_media("chat9", cr)
    assert sent == [("chat9", 10, "Foto 1-10 di 12 per la tua query"),
                    ("chat9", 2, "Foto 11-12 di 12 per la tua query")]


def test_daemon_resume_media_noop_non_telegram():
    from channels.daemon import ChannelDaemon
    import orchestration as orch

    class _Chan:
        name = "http"
        def send_media_group(self, **kw):
            raise AssertionError("non deve inviare fuori da telegram")

    d = ChannelDaemon.__new__(ChannelDaemon)
    d.channel = _Chan()
    cr = orch.CompletionResult(text="ok",
                               attachments=[{"kind": "image", "path": "/a.jpg"}])
    d._send_resume_media("c", cr)   # nessuna eccezione, nessun invio
