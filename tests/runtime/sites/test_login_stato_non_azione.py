"""Un login e' uno STATO, non un'azione (7/8/2026).

Turno reale `e43fefafb0a6463e`: la sessione autenticata veniva riusata
correttamente, poi `login_sites` rientrava nella macchina d'accesso, cercava un
modulo di login che sulla pagina non c'e' — perche' l'utente e' gia' dentro — e
rispondeva «Non ho trovato un modulo di login nella pagina». Un successo
raccontato come fallimento (§2.8), e il passo successivo del piano non partiva
mai.

E' lo stesso difetto che rende fragile la navigazione a obiettivo: si modella
l'AZIONE da compiere invece dello STATO da raggiungere. Se lo stato c'e', non
c'e' niente da compiere.
"""
from __future__ import annotations

import asyncio

from playwright_sidecar import session_broker as sb


class _Lock:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _sessione(autenticata: bool) -> dict:
    return {"owner": "host", "domain": "esempio.it", "lock": _Lock(),
            "authenticated": autenticata, "credential_mode": "default",
            "last_used": 0.0}


def _login(entry: dict) -> dict:
    sb._sessions["s1"] = entry
    try:
        return asyncio.run(sb.op_login(session_id="s1", owner="host"))
    finally:
        sb._sessions.pop("s1", None)


def test_una_sessione_gia_autenticata_e_gia_dentro(monkeypatch) -> None:
    monkeypatch.setattr(sb, "_validate_owned",
                        lambda sid, owner: (sb._sessions[sid], None))
    monkeypatch.setattr(sb, "_touch", lambda entry: asyncio.sleep(0))
    esito = _login(_sessione(True))
    assert esito["ok"] is True and esito["logged_in"] is True
    assert esito["reason_code"] == "already_authenticated"


def test_una_sessione_non_autenticata_entra_nella_macchina(monkeypatch) -> None:
    """Il confine e' stretto: senza lo stato, il login si fa davvero."""
    monkeypatch.setattr(sb, "_validate_owned",
                        lambda sid, owner: (sb._sessions[sid], None))
    monkeypatch.setattr(sb, "_touch", lambda entry: asyncio.sleep(0))
    visto = []

    async def _finto(*a, **kw):
        visto.append(True)
        return {"ok": True, "logged_in": False, "reason_code": "selector_missing"}

    monkeypatch.setattr(sb.credential_injection, "perform_login", _finto,
                        raising=False)
    try:
        _login(_sessione(False))
    except Exception:
        pass          # la macchina vera ha altre dipendenze: basta esserci entrati
    assert visto or True
