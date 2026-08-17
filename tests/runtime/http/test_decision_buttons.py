"""Bottoni si'/no nella chat web (17/8/2026).

Su Telegram una proposta che si chiude con un si' o un no ha da tempo due
bottoni: il tap manda `cap:<turn_id>:yes|no`, cioe' una decisione legata a
QUELLA proposta. Sul canale HTTP la stessa domanda arrivava come testo, e
l'utente doveva scriverla — con tutto il rischio di fraintendimento che il
controllo su «si'» / «no» deve poi assorbire.

Qui la decisione e' tipizzata e passa per la STESSA funzione che consuma la
proposta quando la risposta e' scritta (`_apply_cap_pending`): un consumo
solo, due modi di raggiungerlo.

Run: `python3 -m pytest tests/runtime/http/test_decision_buttons.py -v`
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME = _ROOT / "runtime"
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


# ── Quali domande meritano i bottoni ──────────────────────────────────
def _log(kind: str, turn_id: str = "abc123"):
    return SimpleNamespace(turn_id=turn_id,
                           expandable_caps=[{"kind": kind}] if kind else [])


@pytest.mark.parametrize("kind", [
    "admin_approval", "approval_required", "cap_expand",
])
def test_una_domanda_si_no_offre_i_bottoni(kind):
    import http_routes_agent as H
    payload = H._pending_decision_payload(_log(kind))
    assert payload is not None
    assert payload["turn_id"] == "abc123"
    assert payload["kind"] == kind
    assert payload["yes_label"] and payload["no_label"]
    assert payload["yes_label"] != payload["no_label"]


def test_un_dialogo_strutturato_non_li_offre():
    """`get_inputs_response` ha gia' il suo modulo con i bottoni: due
    comandi per la stessa domanda sarebbero un errore, non una comodita'."""
    import http_routes_agent as H
    assert H._pending_decision_payload(_log("get_inputs_response")) is None


def test_un_turno_senza_domanda_aperta_non_li_offre():
    import http_routes_agent as H
    assert H._pending_decision_payload(_log("")) is None


def test_le_scritte_vengono_dall_i18n():
    """§7.13: nessuna scritta cablata. Approvazione amministrativa e
    conferma generica hanno etichette diverse, entrambe dal dizionario."""
    import http_routes_agent as H
    from messages import get as _m
    admin = H._pending_decision_payload(_log("admin_approval"))
    generic = H._pending_decision_payload(_log("approval_required"))
    assert admin["yes_label"] == _m("MSG_BTN_APPROVE")
    assert admin["no_label"] == _m("MSG_BTN_REJECT")
    assert generic["yes_label"] == _m("MSG_BTN_YES")
    assert generic["no_label"] == _m("MSG_BTN_NO")


# ── La decisione non passa per l'interpretazione del testo ────────────
@pytest.fixture
def pending(monkeypatch):
    """Una proposta aperta, con un consumo osservabile."""
    import http_routes_agent as H
    from channels import daemon as D

    stato = {"cleared": False, "classify_calls": 0}
    proposta = {"proposal": {"kind": "approval_required",
                             "executor": "find_images_indices",
                             "args_suggested": {},
                             "cap_field": "max_results",
                             "cap_suggested": 50},
                "turn_id": "turno-aperto"}

    monkeypatch.setattr(D, "_cap_pending_load",
                        lambda sid, **kw: dict(proposta))
    monkeypatch.setattr(D, "_cap_pending_clear",
                        lambda sid: stato.update(cleared=True))

    def _spia(text):
        stato["classify_calls"] += 1
        return "other"

    monkeypatch.setattr(D, "_classify_yes_no", _spia)
    monkeypatch.setattr(H, "_msg", lambda k, **kw: k)
    return stato


def test_il_bottone_non_fa_interpretare_il_testo(pending, monkeypatch):
    """Il punto dell'esercizio: con una decisione tipizzata il
    classificatore non viene nemmeno interpellato, quindi non esiste una
    frase che possa essere letta male."""
    import http_routes_agent as H
    monkeypatch.setattr(H, "_msg", lambda k, **kw: k)

    # Testo volutamente ambiguo: se contasse, «no» vincerebbe.
    H._apply_cap_pending("s1", "no, aspetta", owner_user_id="u1",
                         decision="yes", decision_turn_id="turno-aperto")
    assert pending["classify_calls"] == 0


def test_senza_decisione_il_testo_conta_ancora(pending):
    """Scrivere la risposta continua a funzionare: i due modi sono
    complementari, non alternativi."""
    import http_routes_agent as H
    H._apply_cap_pending("s1", "si", owner_user_id="u1")
    assert pending["classify_calls"] == 1


# ── Un tap non deve mai agire al buio ─────────────────────────────────
def test_un_tap_su_una_proposta_vecchia_non_esegue(pending, monkeypatch):
    """Il bottone porta l'impronta della domanda che l'utente stava
    guardando. Se nel frattempo ne e' aperta un'altra, non decide quella."""
    import http_routes_agent as H
    monkeypatch.setattr(H, "_msg", lambda k, **kw: k)

    _q, consumed, msg = H._apply_cap_pending(
        "s1", "Si", owner_user_id="u1",
        decision="yes", decision_turn_id="una-bolla-vecchia")

    assert consumed is None, "non deve consumare la proposta corrente"
    assert msg == "MSG_CAP_PROPOSAL_EXPIRED"
    assert not pending["cleared"], "non deve toccare lo stato aperto"


def test_un_tap_senza_proposta_aperta_lo_dice(monkeypatch):
    """Scaduta, gia' risposta altrove, o ripresa su un altro device: il
    bottone non fa partire nulla e lo dichiara (§2.8)."""
    import http_routes_agent as H
    from channels import daemon as D
    monkeypatch.setattr(D, "_cap_pending_load", lambda sid, **kw: None)
    monkeypatch.setattr(H, "_msg", lambda k, **kw: k)

    _q, consumed, msg = H._apply_cap_pending(
        "s1", "Si", owner_user_id="u1",
        decision="yes", decision_turn_id="qualsiasi")
    assert consumed is None
    assert msg == "MSG_CAP_PROPOSAL_EXPIRED"


def test_senza_decisione_una_query_normale_resta_trasparente(monkeypatch):
    """Nessuna proposta aperta e nessun bottone: il turno passa oltre
    senza messaggi inventati."""
    import http_routes_agent as H
    from channels import daemon as D
    monkeypatch.setattr(D, "_cap_pending_load", lambda sid, **kw: None)

    q, consumed, msg = H._apply_cap_pending(
        "s1", "che ore sono", owner_user_id="u1")
    assert (q, consumed, msg) == ("che ore sono", None, None)


# ── Il valore arriva dal client: va validato ──────────────────────────
@pytest.mark.parametrize("valore", [
    "YES", " yes ", "true", "1", "si", "sì", "approva", "ok", "y",
    "yes; rm -rf /", "yes\n", "yes\x00", "['yes']", "0", "-1", "None",
])
def test_un_valore_fuori_dall_insieme_chiuso_non_e_una_decisione(
        valore, pending):
    """Il campo lo scrive il client, quindi va validato dal server e non
    dal client. Fuori da `yes`/`no` non e' una decisione: si ricade sul
    percorso normale, dove a decidere e' la lettura del testo — e questa
    volta il valore grezzo arriva alla funzione, senza normalizzazioni di
    comodo nel test."""
    import http_routes_agent as H
    H._apply_cap_pending("s1", "boh", owner_user_id="u1", decision=valore)
    assert pending["classify_calls"] == 1, (
        f"{valore!r} non deve valere come decisione")


@pytest.mark.parametrize("valore", ["yes", "no"])
def test_i_due_valori_ammessi_decidono(valore, pending):
    import http_routes_agent as H
    H._apply_cap_pending("s1", "boh", owner_user_id="u1", decision=valore)
    assert pending["classify_calls"] == 0


def test_un_valore_invalido_non_apre_nemmeno_il_ramo_scaduta(pending,
                                                             monkeypatch):
    """Un valore fuori insieme non deve nemmeno far scattare il controllo
    di proposta vecchia: non e' una decisione, quindi la sua impronta non
    significa niente."""
    import http_routes_agent as H
    monkeypatch.setattr(H, "_msg", lambda k, **kw: k)
    _q, _consumed, msg = H._apply_cap_pending(
        "s1", "boh", owner_user_id="u1",
        decision="true", decision_turn_id="una-bolla-vecchia")
    assert msg != "MSG_CAP_PROPOSAL_EXPIRED"
