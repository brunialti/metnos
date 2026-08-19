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
