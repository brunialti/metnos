"""Regressione (§2.8): il gate github headless non deve essere un drop silenzioso.

Bug: `_open_gate_dialog` apriva un dialog via get_inputs con channel=None (task
schedulato) -> get_inputs SOLO registra (nessun pusher headless) -> la card non
raggiungeva l'owner ("dialog_failed"). Fix: oltre a registrare il dialog, il
gate notifica l'owner via il path provato (_notify_owner -> send_messages host).
"""
import sys
from pathlib import Path

_RT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_RT))
import jobs.github_watcher as W  # noqa: E402


def test_headless_gate_notifies_owner(monkeypatch):
    calls = []

    def fake_invoke(name, args):
        calls.append((name, args))
        return {"ok": True}

    monkeypatch.setattr(W, "_invoke", fake_invoke)
    ok = W._open_gate_dialog(
        repo="brunialti/metnos", kind="issue", number=7,
        title="Bug X", body="dettaglio del problema",
        top_matches=[{"ref": "issue#3", "similarity": 0.82}],
        classification_hint="support", snooze_options_s=[3600, 14400, 86400],
    )
    invoked = [c[0] for c in calls]
    assert "get_inputs" in invoked          # dialog ancora registrato
    assert "send_messages" in invoked       # owner notificato (no silent drop)
    assert ok is True
    # la notifica all'owner usa il contratto canonico to_user=host
    notify = next(a for n, a in calls if n == "send_messages")
    assert notify["messages"][0]["to_user"] == "host"
    body = notify["messages"][0]["body"]
    assert "#7" in body and "Decisione richiesta" in body
