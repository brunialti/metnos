"""W2 v1 (ADR 0187) — preferenze utente esplicite.

Storage users.db (`user_prefs`, vocabolario CHIUSO), placeholder
`${RUNTIME:pref_<chiave>}` nel resolver executor, prefs nel profilo persona.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

os.environ.setdefault("METNOS_ENGINE", "v3")


@pytest.fixture()
def users_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("METNOS_USERS_DB", str(tmp_path / "users.db"))
    import users
    # il modulo può cachare il path: forza re-resolve
    if hasattr(users, "_DB_PATH_CACHE"):
        monkeypatch.setattr(users, "_DB_PATH_CACHE", None, raising=False)
    users.init_db()
    u = users.create_user(name="mario", role="guest")
    return users, (u["id"] if isinstance(u, dict) else u)


def test_set_get_list_delete(users_isolated):
    users, uid = users_isolated
    assert users.set_pref(uid, "tone", "formale")["ok"]
    assert users.set_pref(uid, "reply_length", "breve")["ok"]
    assert users.get_pref(uid, "tone") == "formale"
    assert users.list_prefs(uid) == {"tone": "formale",
                                     "reply_length": "breve"}
    assert users.delete_pref(uid, "tone") is True
    assert users.get_pref(uid, "tone") is None


def test_closed_vocabulary(users_isolated):
    users, uid = users_isolated
    assert users.set_pref(uid, "tone", "urlante")["ok"] is False
    assert users.set_pref(uid, "colore", "blu")["ok"] is False
    assert users.get_pref(uid, "tone", "neutro") == "neutro"  # default


def test_unknown_user_honest(users_isolated):
    users, _ = users_isolated
    assert users.set_pref("nessuno", "tone", "formale")["ok"] is False
    assert users.list_prefs("nessuno") == {}


def test_runtime_placeholder_pref(users_isolated, monkeypatch):
    users, uid = users_isolated
    users.set_pref(uid, "units", "imperial")
    import devices
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda a: uid)
    from engine.executor import _resolve_runtime_placeholders
    out = _resolve_runtime_placeholders(
        {"u": "${RUNTIME:pref_units}", "manca": "${RUNTIME:pref_tone}"},
        {"actor": "mario", "lang": "it", "channel": "http"})
    assert out["u"] == "imperial"
    # pref assente → placeholder INTATTO (§2.8, mai vuoto silenzioso)
    assert out["manca"] == "${RUNTIME:pref_tone}"
