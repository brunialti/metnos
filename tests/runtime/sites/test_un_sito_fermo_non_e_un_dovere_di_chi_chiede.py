"""Un sito fermo non e' una cosa che l'utente possa fare (10/9/2026).

Turno reale `ab0ebb39`. Il portale era in manutenzione — ogni clic su «accedi»
riportava alla pagina di manutenzione — e Metnos ha risposto:

    «Non posso risolvere: Login non riuscito. Per procedere: Richiede azione
     fisica (es. condivisione posizione) o capability esterna non installata.»

Falso due volte. Non serve nessuna azione fisica, e non manca nessuna capacita':
il sito e' fermo. §2.8 — mai dichiarare un esito che non corrisponde alla
realta'.

La causa era di forma: `login_sites` marcava OGNI fallimento come
`needs_user_action`, una classe che significa «tocca alla persona». Le
credenziali, il CAPTCHA, il secondo fattore sono davvero suoi; un sito
irraggiungibile, fermo o a soglia no. Fuori dall'elenco chiuso di cio' che una
persona risolve — causa ignota compresa — il guasto e' operativo, e per quelli
la catena ha gia' il rimedio giusto: riprova piu' tardi.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from engine.terminator import SimpleTerminator
from engine.types import OPERATIONAL_ERROR_CLASSES, Intent, RunResult, StepRun


def _login_sites():
    percorso = (Path(__file__).resolve().parents[3]
                / "executors/login_sites/login_sites.py")
    spec = importlib.util.spec_from_file_location("_login_sites_onesta",
                                                  percorso)
    modulo = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("reason", [
    "credentials_missing", "captcha_required", "two_factor_required",
    "two_factor_push_required", "origin_unverified",
])
def test_cio_che_la_persona_risolve_resta_suo(reason) -> None:
    assert reason in _login_sites()._REASONS_THE_PERSON_RESOLVES


@pytest.mark.parametrize("reason", [
    "login_entry_stalled", "login_timeout", "selector_missing",
    "page_unavailable", "rate_limited", "http_forbidden", "login_failed",
])
def test_uno_stato_del_sito_non_lo_e(reason) -> None:
    """Causa ignota compresa: `needs_user_action` e' un'affermazione forte e
    si dichiara solo quando la si conosce."""
    assert reason not in _login_sites()._REASONS_THE_PERSON_RESOLVES


def test_il_sito_fermo_ha_un_messaggio_suo() -> None:
    modulo = _login_sites()
    testo = modulo._reason_message("login_entry_stalled")
    assert testo and not testo.startswith("<missing:"), testo


def test_la_risposta_dice_riprova_non_agisci_tu() -> None:
    from messages import get as _msg

    fallito = RunResult(
        steps=[StepRun(step_idx=2, tool="login_sites", args={},
                       result={"ok": False,
                               "error_class": "service_unavailable",
                               "error": _msg(
                                   "MSG_SITES_RC_LOGIN_ENTRY_STALLED")},
                       ok=False, latency_ms=1)],
        final_kind="error", ok_count=0)
    risposta = SimpleTerminator().explain(
        query="apri il sito e mostrami tutte le fatture",
        intent=Intent(verb="read", object="sites"),
        failed_run=fallito, error_class="out_of_scope")
    assert "service_unavailable" in OPERATIONAL_ERROR_CLASSES
    assert _msg("MSG_TERM_OUT_OF_SCOPE_ACTION") not in risposta.final_text
    assert _msg("MSG_CHAT_FB_RETRY") in risposta.final_text
    # E la causa detta e' quella vera, non un generico «operazione fallita».
    assert _msg("MSG_SITES_RC_LOGIN_ENTRY_STALLED") in risposta.final_text
