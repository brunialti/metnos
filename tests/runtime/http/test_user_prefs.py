"""W2 v1 (ADR 0187) — preferenze utente esplicite.

Storage users.db (`user_prefs`, vocabolario CHIUSO), placeholder
`${RUNTIME:pref_<chiave>}` nel resolver executor, prefs nel profilo persona.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

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


def test_runtime_context_overwrites_internal_values_from_plan():
    from engine.executor import _resolve_runtime_placeholders

    out = _resolve_runtime_placeholders(
        {"_actor": "foreign", "_lang": "xx", "_channel": "foreign"},
        {"actor": "alice", "lang": "it", "channel": "http"},
    )

    assert out["_actor"] == "alice"
    assert out["_lang"] == "it"
    assert out["_channel"] == "http"


# --- Preferenze dalla chat (builtin get/set/delete_preferences) -------------

@pytest.fixture()
def prefs_tools(users_isolated, monkeypatch):
    """I tre builtin legati all'utente isolato della fixture."""
    users, uid = users_isolated
    import devices
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda a: uid)
    import user_preferences as up
    return up, users, uid


def test_get_preferences_lists_value_and_origin(prefs_tools):
    up, users, uid = prefs_tools
    users.set_pref(uid, "reply_length", "breve", source="chat")
    out = up.handle_get_preferences({}, actor="mario")
    assert out["ok"] is True
    assert [e["key"] for e in out["entries"]] == ["reply_length"]
    entry = out["entries"][0]
    assert entry["value"] == "breve"
    assert entry["origin"]                    # origine leggibile, non il codice
    assert entry["applied"] is True
    # `available` dice cosa si puo' impostare: un elenco vuoto non e' un vicolo
    # cieco (§2.11).
    assert {a["key"] for a in out["available"]} == set(users.PREF_KEYS)


def test_get_preferences_empty_is_honest(prefs_tools):
    up, _users, _uid = prefs_tools
    out = up.handle_get_preferences({}, actor="mario")
    assert out["ok"] is True and out["entries"] == []
    assert out["note"]                        # dice che valgono i predefiniti


def test_set_preferences_marks_chat_origin(prefs_tools):
    up, users, uid = prefs_tools
    out = up.handle_set_preferences(
        {"key": "reply_length", "value": "breve"}, actor="mario")
    assert out["ok"] is True and out["ok_count"] == 1
    assert out["results"] == [{"key": "reply_length", "value": "breve",
                               "applied": True}]
    assert users.list_prefs_detailed(uid)["reply_length"]["source"] == "chat"


def test_set_preferences_declares_when_it_has_no_effect(prefs_tools):
    """§2.8: una preferenza senza consumatore non si spaccia per applicata."""
    up, _users, _uid = prefs_tools
    out = up.handle_set_preferences({"key": "tone", "value": "formale"},
                                    actor="mario")
    assert out["ok"] is True
    assert out["results"][0]["applied"] is False
    assert out["results"][0]["note"]


def test_set_preferences_rejects_value_naming_the_alternatives(prefs_tools):
    up, _users, _uid = prefs_tools
    out = up.handle_set_preferences({"key": "tone", "value": "urlante"},
                                    actor="mario")
    assert out["ok"] is False and out["error_class"] == "invalid_args"
    # Un rifiuto senza alternative non e' una risposta.
    for allowed in _users_allowed("tone"):
        assert allowed in out["error"]


def test_set_preferences_rejects_unknown_key(prefs_tools):
    up, _users, _uid = prefs_tools
    out = up.handle_set_preferences({"key": "colore", "value": "blu"},
                                    actor="mario")
    assert out["ok"] is False and out["error_class"] == "invalid_args"


def test_delete_preferences_restores_default(prefs_tools):
    up, users, uid = prefs_tools
    users.set_pref(uid, "tone", "formale")
    out = up.handle_delete_preferences({"keys": ["tone"]}, actor="mario")
    assert out["ok"] is True and out["ok_count"] == 1
    assert out["results"] == [{"key": "tone", "removed": True,
                               "previous": "formale"}]
    assert users.get_pref(uid, "tone") is None


def test_delete_preferences_all(prefs_tools):
    up, users, uid = prefs_tools
    users.set_pref(uid, "tone", "formale")
    users.set_pref(uid, "reply_length", "breve")
    out = up.handle_delete_preferences({"all": True}, actor="mario")
    assert out["ok_count"] == 2 and users.list_prefs(uid) == {}


def test_delete_preferences_without_target_asks(prefs_tools):
    """Senza chiave e senza `all` non si indovina: si chiede (§2.8)."""
    up, users, uid = prefs_tools
    users.set_pref(uid, "tone", "formale")
    out = up.handle_delete_preferences({}, actor="mario")
    assert out["ok"] is False and out["error_class"] == "invalid_args"
    assert "tone" in out["error"]
    assert users.get_pref(uid, "tone") == "formale"   # nulla e' stato toccato


def test_preferences_are_scoped_to_the_actor(users_isolated, monkeypatch):
    """Un attore non vede ne' tocca le preferenze di un altro."""
    users, uid = users_isolated
    other = users.create_user(name="lucia", role="guest")
    other_id = other["id"] if isinstance(other, dict) else other
    users.set_pref(other_id, "tone", "formale")
    import devices
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda a: uid)
    import user_preferences as up
    assert up.handle_get_preferences({}, actor="mario")["entries"] == []
    up.handle_delete_preferences({"all": True}, actor="mario")
    assert users.get_pref(other_id, "tone") == "formale"


def test_manifest_key_enum_covers_the_registry():
    """Anti-regressione: il contratto FIRMATO deve elencare tutte le chiavi.

    L'enum nel manifest e' un'istantanea di `users.PREF_KEYS`. Se il registro
    cambia (es. una tecnica stealth nuova) senza rigenerare il contratto, il
    planner non potrebbe piu' nominare quella preferenza: qui fallisce forte
    invece di sotto-dichiarare in silenzio.
    Rigenerazione: python3 scripts/generate_builtin_executor_contracts.py --sign
    """
    import tomllib
    import users
    path = (_RUNTIME / "builtin_executor_contracts" / "set_preferences"
            / "manifest.toml")
    manifest = tomllib.loads(path.read_text(encoding="utf-8"))
    enum = manifest["args"]["properties"]["key"]["enum"]
    assert list(enum) == list(users.PREF_KEYS)
    # Il valore ammesso e' dichiarato in ENTRAMBE le lingue: un planner in EN
    # deve vedere lo stesso insieme chiuso di uno in IT.
    value_desc = manifest["args"]["properties"]["value"]["description"]
    for lang in ("it", "en"):
        assert "reply_length: breve|normale|dettagliata" in value_desc[lang]


def _users_allowed(key: str) -> tuple:
    import users
    return users.allowed_pref_values(key)
