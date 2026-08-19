"""Un ramo approvato che dura minuti non deve lasciare l'utente senza risposta.

Trovato dal vivo il 19/8/2026 (turno a243532766b14676). Premuto «per tutti gli
utenti», il ramo apre sul computer una finestra di conferma di Windows e resta
li' finche' una persona non risponde: sette minuti. La risposta HTTP restava
bloccata per tutto quel tempo, un proxy in mezzo rinunciava, e l'utente
riceveva una pagina d'errore grezza. L'operazione, dietro, riusciva: si
perdeva solo il modo di raccontarla — e l'esito non arrivava mai.

Due proprieta', e servono entrambe:
1. l'attesa ha un tetto, e chi ha premuto riceve un messaggio comprensibile;
2. il lavoro NON viene annullato: prosegue e produce il suo effetto.

Run: `python3 -m pytest tests/runtime/engine/test_ramo_lento_non_perde_utente.py -v`
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "runtime"))

import orchestration  # noqa: E402


class _FintoExecutor:
    name = "finto"
    timeout_s = 30


@pytest.fixture()
def catalogo(monkeypatch):
    """Un catalogo con un solo executor, senza toccare quello vero."""
    class _Cat:
        executors = {"finto": _FintoExecutor()}

    import loader
    monkeypatch.setattr(loader, "load_catalog", lambda **k: _Cat())
    return _Cat


def test_un_ramo_lento_risponde_invece_di_far_scadere_la_richiesta(
        catalogo, monkeypatch):
    partito = threading.Event()
    finito = threading.Event()

    def lento(*a, **k):
        partito.set()
        # Piu' lungo del tetto: simula la finestra di conferma di Windows.
        time.sleep(1.0)
        finito.set()
        return {"ok": True, "summary": "fatto"}

    import agent_runtime
    monkeypatch.setattr(agent_runtime, "invoke_executor", lento)
    monkeypatch.setattr(orchestration, "_ATTESA_MASSIMA_RAMO_S", 0.2)

    res = orchestration._esegui_ramo("finto", {}, actor="host", channel="http")

    # (1) Si risponde subito, e si risponde qualcosa di comprensibile.
    assert res.get("pending") is True
    assert res.get("final_message_hint"), "nessun messaggio per chi ha premuto"
    assert "<missing" not in res["final_message_hint"], "chiave i18n assente"
    assert partito.is_set(), "il lavoro non e' nemmeno partito"

    # (2) E il lavoro prosegue: non e' stato annullato per far posto alla
    #     risposta. E' la meta' che conta — l'utente aspetta un effetto, non
    #     una scusa.
    assert finito.wait(timeout=5), "il lavoro e' stato interrotto"


def test_un_ramo_veloce_si_comporta_come_sempre(catalogo, monkeypatch):
    import agent_runtime
    monkeypatch.setattr(agent_runtime, "invoke_executor",
                        lambda *a, **k: {"ok": True, "summary": "svelto"})
    res = orchestration._esegui_ramo("finto", {}, actor="host", channel="http")
    assert res == {"ok": True, "summary": "svelto"}
    assert "pending" not in res


def test_un_executor_che_non_esiste_si_distingue_da_un_fallimento(catalogo):
    """Non e' l'operazione ad essere fallita: non e' nemmeno partita."""
    res = orchestration._esegui_ramo("inesistente", {}, actor="host", channel="http")
    assert res["ok"] is False
    assert res.get("orchestration_error") is True


def test_l_esito_arriva_anche_quando_arriva_tardi(catalogo, monkeypatch):
    """La meta' che mancava: chi ha premuto deve SAPERE com'e' finita.

    Riceve «sta lavorando» e poi piu' niente sarebbe peggio di un
    fallimento dichiarato: l'operazione ha prodotto un effetto sulla
    macchina e nessuno lo ha raccontato.
    """
    consegnato = {}

    def finta_publish(user_id, kind, payload):
        consegnato["utente"] = user_id
        consegnato["tipo"] = kind
        consegnato["payload"] = payload
        return 1

    import active_sessions
    monkeypatch.setattr(active_sessions, "publish_to_user", finta_publish)

    import agent_runtime
    monkeypatch.setattr(agent_runtime, "invoke_executor",
                        lambda *a, **k: (time.sleep(0.4),
                                         {"ok": True, "summary": "installato"})[1])
    monkeypatch.setattr(orchestration, "_ATTESA_MASSIMA_RAMO_S", 0.1)

    res = orchestration._esegui_ramo(
        "finto", {}, actor="host", channel="http", owner_user_id="utente-1")
    assert res.get("pending") is True

    for _ in range(50):
        if consegnato:
            break
        time.sleep(0.05)

    assert consegnato, "l'esito non e' mai stato consegnato"
    assert consegnato["utente"] == "utente-1"
    assert consegnato["tipo"] == "operation_done"
    # Lo stesso esito si legge allo stesso modo, che arrivi subito o dopo.
    assert consegnato["payload"]["message"] == "installato"
    assert consegnato["payload"]["ok"] is True


def test_anche_un_fallimento_tardivo_viene_raccontato(catalogo, monkeypatch):
    consegnato = {}
    import active_sessions
    monkeypatch.setattr(active_sessions, "publish_to_user",
                        lambda u, k, p: consegnato.update(p) or 1)

    def esplode(*a, **k):
        time.sleep(0.3)
        raise RuntimeError("il gestore ha detto di no")

    import agent_runtime
    monkeypatch.setattr(agent_runtime, "invoke_executor", esplode)
    monkeypatch.setattr(orchestration, "_ATTESA_MASSIMA_RAMO_S", 0.1)

    orchestration._esegui_ramo(
        "finto", {}, actor="host", channel="http", owner_user_id="utente-1")
    for _ in range(50):
        if consegnato:
            break
        time.sleep(0.05)
    assert consegnato, "un fallimento tardivo e' sparito nel nulla"
    assert consegnato["ok"] is False
    assert "il gestore ha detto di no" in consegnato["message"]


def test_senza_proprietario_non_si_consegna_a_caso(catalogo, monkeypatch):
    """Meglio non consegnare che consegnare all'utente sbagliato."""
    chiamate = []
    import active_sessions
    monkeypatch.setattr(active_sessions, "publish_to_user",
                        lambda u, k, p: chiamate.append(u) or 1)
    import agent_runtime
    monkeypatch.setattr(agent_runtime, "invoke_executor",
                        lambda *a, **k: (time.sleep(0.3), {"ok": True})[1])
    monkeypatch.setattr(orchestration, "_ATTESA_MASSIMA_RAMO_S", 0.1)

    orchestration._esegui_ramo("finto", {}, actor="host", channel="http",
                               owner_user_id="")
    time.sleep(0.6)
    assert chiamate == []
