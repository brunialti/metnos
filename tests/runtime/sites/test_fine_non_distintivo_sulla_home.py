"""The home does not attest a goal that distinguishes nothing (2026-08-07).

Real turn `31cd5a052c474dc6`: "go to booking.com, sign in with my account and
tell me the upcoming bookings". The pilot stopped on the home page, declared
the goal reached and answered with the page slogan — "roberto, where are you
going next?". A question instead of the data.

Two properties were missing, both general:

1. The guard stopping the HOME from attesting a goal only applied to a goal of
   ONE token. "the upcoming bookings" has two — the thing plus its facet — and
   two tokens is the normal case, not the exception. The criterion is not how
   many tokens there are: it is whether one of them still DISTINGUISHES once
   the site name (which is everywhere) and the state facets (which say how to
   filter, not what to look for) are removed.
2. The action verb survived among the goal tokens. "go" made a goal look
   distinctive when it was not, and matched "Skip to main content" — the first
   link of every accessible page in the world.

The third property came from the real page: a state facet stops the pilot only
when a control leads to it, or when the page declares a different one.
"""
from __future__ import annotations

import pytest

from playwright_sidecar import action_resolver as ar

_HOME = "https://www.booking.com/index.it.html?aid=1&auth_success=1"
_SEZIONE = "https://secure.booking.com/mytrips.it.html"

# A home page shows a digest of the whole site: that is why something
# resembling the goal is always found there.
_TESTO_HOME = """Vai al contenuto principale
Soggiorni
Voli
roberto, quale sara' la tua prossima meta?
Viaggio per lavoro
Il tuo prossimo viaggio
Rimini - 5 nov - 7 nov
Hotel Alibi"""

_TESTO_SEZIONE = """Prenotazioni e viaggi
Rimini - Hotel Alibi
5 nov - 7 nov Confermata"""


@pytest.mark.parametrize("fine", [
    "prenotazioni",                     # one token, already covered before
    "prossime prenotazioni",            # two: the thing and its facet
    "vai alle prossime prenotazioni",   # three, with the verb in front
])
def test_la_home_non_attesta_un_fine_che_non_distingue(fine) -> None:
    assert not ar.page_satisfies_goal(fine, _TESTO_HOME, scope_text=_HOME)


def test_il_conteggio_dei_token_non_e_il_criterio() -> None:
    """The same request phrased two ways cannot have two outcomes."""
    corto = ar.page_satisfies_goal("prenotazioni", _TESTO_HOME, scope_text=_HOME)
    lungo = ar.page_satisfies_goal("le prossime prenotazioni", _TESTO_HOME,
                                   scope_text=_HOME)
    assert corto == lungo is False


def test_un_fine_distintivo_resta_attestabile() -> None:
    """Guard against a false positive: the rule does not switch the proof off.

    "invoices" is neither the site name nor a state facet: where it appears,
    the page attests the goal — even on a home page.
    """
    testo = "Benvenuto\nMovimenti e fatture\n2026"
    assert ar.page_satisfies_goal("fatture 2026", testo,
                                  scope_text="https://banca.test/index.html")


def test_fuori_dalla_home_il_fine_si_attesta_normalmente() -> None:
    assert ar.page_satisfies_goal("le mie prenotazioni", _TESTO_SEZIONE,
                                  scope_text=_SEZIONE)


def test_il_verbo_d_azione_non_e_un_token_del_fine() -> None:
    assert ar.goal_tokens("vai alle mie prenotazioni") == ("booking",)
    assert "vai" not in ar.goal_tokens("vai ai documenti archiviati")


def test_il_verbo_non_aggancia_il_link_di_salto_al_contenuto() -> None:
    """The concrete defect the verb-as-token produced."""
    salto = {"id": "s1", "tag": "a", "role": "link",
             "name": "Vai al contenuto principale",
             "href": "https://www.booking.com/#main",
             "visible": True, "in_viewport": True, "topmost": True}
    esito = ar.choose_goal_candidate("vai alle prossime prenotazioni", [salto])
    assert not esito.get("ok")


# The real bookings page, taken from the journal of turn `a397afecb73d4b2a`:
# the tabs on offer are "Passati" and "Cancellati", while the UPCOMING ones are
# the default section — unnamed, only dates.
_EVIDENZA_PRENOTAZIONI = ["Prenotazioni e viaggi", "Rimini",
                          "5 nov - 7 nov1 prenotazione", "Palermo",
                          "14 gen - 14 gen1 prenotazione"]
_CONTROLLI_PRENOTAZIONI = [{"name": "Trova una prenotazione"},
                           {"name": "Passati"}, {"name": "Cancellati"}]


def test_una_faccetta_senza_controllo_non_blocca_l_arrivo() -> None:
    """The site expresses "upcoming" through dates, not through the word.

    Demanding it in words made arrival impossible to prove: the pilot reached
    the right page, failed to notice, and carried on to "Find a booking" until
    the step budget ran out (real turn, 2026-08-07).
    """
    assert ar.page_satisfies_goal(
        "prossime prenotazioni", _EVIDENZA_PRENOTAZIONI,
        scope_text=_SEZIONE,
        facets_offered=ar.offered_facet_tokens(_CONTROLLI_PRENOTAZIONI))


@pytest.mark.parametrize("fine", ["prenotazioni passate",
                                  "prenotazioni cancellate"])
def test_una_faccetta_offerta_come_controllo_ferma_l_arrivo(fine) -> None:
    """If the tab exists and has not been pressed, you have not arrived."""
    assert not ar.page_satisfies_goal(
        fine, _EVIDENZA_PRENOTAZIONI, scope_text=_SEZIONE,
        facets_offered=ar.offered_facet_tokens(_CONTROLLI_PRENOTAZIONI))


def test_la_scheda_selezionata_attesta_la_faccetta() -> None:
    """The other half of the same rule: once pressed, you have arrived.

    The active tab enters the evidence (browser-owned state), and there the
    facet is finally attested.
    """
    schede = [{"name": "Recenti"}, {"name": "Archiviate"}]
    offerte = ar.offered_facet_tokens(schede)
    assert not ar.page_satisfies_goal(
        "documenti archiviati", ["Contratto 2025.pdf"],
        scope_text="https://x.test/documenti", facets_offered=offerte)
    assert ar.page_satisfies_goal(
        "documenti archiviati", ["Archiviate", "Contratto 2025.pdf"],
        scope_text="https://x.test/documenti", facets_offered=offerte)
