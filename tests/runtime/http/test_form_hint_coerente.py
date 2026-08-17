"""La riga sotto un modulo dice cio' che la persona ha davanti (17/8/2026).

Difetto reale, turno 989c8826. Sotto una scheda con due BOTTONI compariva
«1 campi da compilare; rispondi `annulla` per abortire»: manda a cercare una
casella che non c'e', e suggerisce di rispondere scrivendo proprio dove la
scelta a bottoni era stata introdotta per non far scrivere piu' niente.

Il criterio e' strutturale — che cosa dichiara lo schema del dialogo — non un
elenco di casi noti: un dialogo nuovo fatto di sole scelte prende la riga
giusta senza che nessuno lo aggiunga da qualche parte.

Run: `python3 -m pytest tests/runtime/http/test_form_hint_coerente.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parents[3] / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


def _coda(dialog):
    """L'ultima riga del messaggio che accompagna il modulo."""
    import orchestration as O
    reso = O._build_final_message_hint(
        {"title": "Conferma", "description": "descrizione",
         "dialog": dialog, "dialog_id": "abc"}, "form")
    return reso.splitlines()[-1]


def _scelta(kind="choice"):
    return {"var": "d", "prompt": "?", "schema": {
        "kind": kind, "choices": [{"label": "A", "value": "a"},
                                  {"label": "B", "value": "b"}]}}


def _campo(kind="text"):
    return {"var": "n", "prompt": "Nome?", "schema": {"kind": kind}}


@pytest.mark.parametrize("kind", ["choice", "yes_no"])
def test_un_modulo_di_soli_bottoni_non_parla_di_campi(kind):
    coda = _coda([_scelta(kind)])
    assert "compilare" not in coda.lower()
    assert "scegli" in coda.lower() or "choose" in coda.lower()


def test_un_modulo_con_campi_continua_a_dirlo():
    coda = _coda([_campo()])
    assert "compilare" in coda.lower() or "fill" in coda.lower()


def test_un_modulo_misto_parla_di_campi():
    """Basta un campo da riempire perche' la persona debba scrivere: la riga
    piu' esigente e' quella corretta."""
    coda = _coda([_scelta(), _campo()])
    assert "compilare" in coda.lower() or "fill" in coda.lower()


@pytest.mark.parametrize("kind", ["text", "credentials", "number", "date"])
def test_ogni_campo_da_riempire_conta(kind):
    coda = _coda([_scelta(), _campo(kind)])
    assert "compilare" in coda.lower() or "fill" in coda.lower(), kind


def test_un_dialogo_vuoto_non_promette_bottoni():
    """Senza passi non c'e' niente da scegliere: dire «scegli qui sopra»
    sarebbe indicare qualcosa che non c'e'."""
    coda = _coda([])
    assert "scegli" not in coda.lower()


def test_uno_schema_sconosciuto_e_trattato_come_da_riempire():
    """Fail-closed sulla parte che si legge: un tipo che non conosciamo
    potrebbe richiedere di scrivere, e promettere bottoni sarebbe la
    promessa sbagliata."""
    coda = _coda([{"var": "x", "prompt": "?", "schema": {"kind": "domani"}}])
    assert "compilare" in coda.lower() or "fill" in coda.lower()


def test_uno_schema_assente_e_trattato_come_da_riempire():
    coda = _coda([{"var": "x", "prompt": "?"}])
    assert "compilare" in coda.lower() or "fill" in coda.lower()
