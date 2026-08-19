"""Un esito che arriva tardi appartiene alla persona, non al collegamento.

Quando un'operazione dura piu' della finestra di attesa — un'installazione
che apre una richiesta di conferma sul computer e resta li' finche' qualcuno
non risponde — la risposta immediata dice «sta lavorando» e l'esito arriva
dopo. Consegnarlo al solo collegamento che aveva chiesto vorrebbe dire
perderlo proprio nei casi in cui la richiesta e' durata tanto da far chiudere
o riaprire la pagina: al ritorno il token e' un altro.

Run: `python3 -m pytest tests/runtime/http/test_consegna_esito_al_proprietario.py -v`
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "runtime"))

import active_sessions as _as  # noqa: E402


@pytest.fixture()
def db_isolato(tmp_path, monkeypatch):
    """Registro sessioni su file temporaneo: mai quello di esercizio."""
    monkeypatch.setattr(_as, "DB_PATH", tmp_path / "sessioni.sqlite",
                        raising=False)
    if hasattr(_as, "_CONN"):
        monkeypatch.setattr(_as, "_CONN", None, raising=False)
    _as.init_db()
    return tmp_path


def _sessione(utente: str, canale: str) -> str:
    esito = _as.register_session(utente, canale, device_label="prova")
    assert not esito.get("conflict"), esito
    return str(esito["device_token"])


def test_l_esito_raggiunge_tutti_i_collegamenti_della_persona(db_isolato):
    utente = f"u-{uuid.uuid4().hex[:8]}"
    uno = _sessione(utente, "http")
    due = _sessione(utente, "telegram")

    code = {}
    for token in (uno, due):
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        _as.subscribe(token, q)
        code[token] = q

    raggiunte = _as.publish_to_user(utente, "operation_done",
                                    {"message": "installato", "ok": True})
    assert raggiunte == 2, "non tutti i collegamenti hanno ricevuto l'esito"
    for token, q in code.items():
        ev = q.get_nowait()
        assert ev["kind"] == "operation_done"
        assert ev["message"] == "installato"


def test_l_esito_di_una_persona_non_va_a_un_altra(db_isolato):
    """Il contrario del difetto sarebbe peggio del difetto."""
    mio = f"u-{uuid.uuid4().hex[:8]}"
    altrui = f"u-{uuid.uuid4().hex[:8]}"
    token_altrui = _sessione(altrui, "http")
    _sessione(mio, "http")

    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _as.subscribe(token_altrui, q)

    _as.publish_to_user(mio, "operation_done", {"message": "roba mia"})
    assert q.empty(), "l'esito e' finito a un'altra persona"


def test_nessuno_in_ascolto_non_e_un_errore(db_isolato):
    utente = f"u-{uuid.uuid4().hex[:8]}"
    _sessione(utente, "http")
    assert _as.publish_to_user(utente, "operation_done", {"message": "x"}) == 0


def test_senza_proprietario_non_si_consegna_niente(db_isolato):
    assert _as.publish_to_user("", "operation_done", {"message": "x"}) == 0
