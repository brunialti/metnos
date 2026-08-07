"""A clause with no source of its own reads from the source already open.

Real turn bf9c9abfaae148f4: «go to booking.com, sign in with my account and
tell me the upcoming bookings». The last clause was read as the CALENDAR; the
engine then inserted a calendar reader between the site read and the
extraction, that reader returned nothing, and the chain died on empty entries
with every step reporting ok — a false success (§2.8).

Three cheaper levers were measured and discarded before this one:
the rule written into the intent prompt changed 0 of 13 control queries; the
anaphora scaffold made the whole extraction worse; and no deterministic test
separates the cases, since "bookings" and "appointments" both resolve to no
object at all in the lexicon. What is left is one yes/no question a model
answers well and code cannot answer at all — asked only where it applies.

This file pins WHERE it applies. The measured effect on the 13-query control
bench is 1 change out of 13: the target case, and nothing else.
"""
from __future__ import annotations

import pytest

from intent_extractor import _bind_reads_to_open_source as lega


def _mai(*_a, **_kw):
    raise AssertionError("the probe was asked where it does not apply")


def _risponde(parola):
    return lambda *_a, **_kw: parola


_APRE_UN_SITO = [{"verb": "open", "object": "sites"},
                 {"verb": "login", "object": "sites"}]


def test_la_lettura_senza_fonte_segue_il_sito_aperto() -> None:
    azioni = [*_APRE_UN_SITO, {"verb": "read", "object": "events"}]
    esito = lega("apri il sito, entra e dimmi le prossime prenotazioni",
                 azioni, _risponde("SITE"))
    assert esito[-1] == {"verb": "read", "object": "sites"}


def test_una_fonte_diversa_resta_diversa() -> None:
    """The model's other answer must be honoured: not every trailing clause
    belongs to the site, and «what's on my calendar» is not a site read."""
    azioni = [*_APRE_UN_SITO, {"verb": "read", "object": "events"}]
    esito = lega("apri il sito, entra e dimmi che impegni ho domani",
                 azioni, _risponde("ELSEWHERE"))
    assert esito[-1]["object"] == "events"


def test_senza_un_sito_aperto_non_si_chiede_niente() -> None:
    azioni = [{"verb": "read", "object": "messages"},
              {"verb": "read", "object": "events"}]
    assert lega("leggi le mail e dimmi che impegni ho domani", azioni, _mai) == azioni


def test_una_clausola_gia_sul_sito_non_si_chiede() -> None:
    azioni = [*_APRE_UN_SITO, {"verb": "read", "object": "sites"}]
    assert lega("apri, entra e leggi la pagina", azioni, _mai) == azioni


@pytest.mark.parametrize("verbo", ["create", "send", "delete", "write"])
def test_una_clausola_che_AGISCE_non_si_chiede(verbo) -> None:
    """The question is about where to READ from. A clause that acts elsewhere
    (sending a mail, creating an event) names its own destination."""
    azioni = [*_APRE_UN_SITO, {"verb": verbo, "object": "messages"}]
    assert lega("apri, entra e mandami il risultato", azioni, _mai) == azioni


def test_una_sola_clausola_non_ha_niente_da_ereditare() -> None:
    azioni = [{"verb": "read", "object": "events"}]
    assert lega("dimmi le prossime prenotazioni", azioni, _mai) == azioni


def test_un_guasto_della_sonda_lascia_le_cose_come_stanno() -> None:
    """The probe is an improvement, never a dependency: if it fails, the turn
    proceeds exactly as before."""
    def _rotta(*_a, **_kw):
        raise RuntimeError("llm down")

    azioni = [*_APRE_UN_SITO, {"verb": "read", "object": "events"}]
    assert lega("apri, entra e dimmi le prenotazioni", azioni, _rotta) == azioni
