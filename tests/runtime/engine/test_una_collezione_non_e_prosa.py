"""«Mostrami tutte le fatture» vuole un elenco, non un paragrafo (10/9/2026).

Turno reale sul portale di un pedaggio: il piano arrivava sulla pagina giusta,
la leggeva, e chiudeva con `describe_entries` — cioe' con una sintesi. Roberto:
«la risposta a "mostrare tutte le fatture" non puo' essere [prosa]... nel caso
mi aspettavo una lista».

Il confine di §2.2 esisteva gia': una collezione da mostrare passa per
`extract_entries` (record tipizzati da testo non strutturato) e solo dopo si
presenta. Mancava il riconoscimento della richiesta. Il lessico lega il verbo
all'articolo — «mostrami le» — e il quantificatore in mezzo, «mostrami TUTTE
le», spezzava la forma. La regola qui provata compone due concetti gia'
registrati, verbo di richiesta e quantificatore, senza aggiungere una parola:
chi chiede l'insieme chiede i record.

Roberto, mentre la correzione veniva scritta: «elenco in caso di dati
multipli». Il quantificatore non e' l'unico modo di dire «molti»: l'italiano
lo segna sul determinante, l'inglese sul nome. Un secondo concetto generale —
il determinante plurale — copre entrambe le forme, e resta fuori dal dominio
come gli interrogativi accanto a cui e' registrato.

Il rovescio conta quanto il dritto: una richiesta al singolare, o su un valore
scalare, non deve produrre un'estrazione.
"""
from __future__ import annotations

import pytest

from engine import dispatch
from engine.types import Framework, Intent, StepSpec


def _piano() -> Framework:
    return Framework(steps=[
        StepSpec(tool="open_sites", args={"urls": ["https://x.test"]}),
        StepSpec(tool="login_sites", args={"session_ids": ["s1"]}),
        StepSpec(tool="act_sites", args={"action": "le fatture",
                                         "session_ids": ["s1"]}),
        StepSpec(tool="read_sites", args={"session_ids": ["s1"]}),
        StepSpec(tool="describe_entries", args={"context": "le fatture"}),
        StepSpec(tool="final_answer", args={}),
    ])


def _tools(framework: Framework) -> list[str]:
    return [s.tool for s in framework.steps]


def _riscrivi(query: str) -> Framework:
    return dispatch._ensure_site_session_precursor(
        _piano(), Intent(verb="read", object="sites"), query, None)


@pytest.mark.parametrize("query", [
    "apri il sito x.test, fai login e mostrami tutte le fatture",
    "mostrami tutte le fatture",
    "mostrami le fatture",
    "voglio vedere le fatture",
    "fammi vedere le fatture",
    "dammi le fatture del 2026",
    "dammi ogni fattura del sito",
    "open x.test and show me all my invoices",
    "open x.test and show me the invoices",
    "i want to see the invoices",
])
def test_una_collezione_da_mostrare_passa_per_i_record(query) -> None:
    tools = _tools(_riscrivi(query))
    assert "extract_entries" in tools, tools
    assert tools.index("extract_entries") < tools.index("describe_entries")


def test_la_presentazione_riceve_i_record_e_non_li_riassume() -> None:
    framework = _riscrivi("mostrami tutte le fatture")
    passo = next(s for s in framework.steps if s.tool == "describe_entries")
    # `compact` su entries e' il presentatore deterministico: una riga per
    # record, tutti i campi non vuoti. Non e' una sintesi.
    assert passo.args.get("data_kind") == "entries"
    assert passo.args.get("style") == "compact"
    estrazione = next(s for s in framework.steps if s.tool == "extract_entries")
    assert passo.args.get("from_step") == framework.steps.index(estrazione) + 1


@pytest.mark.parametrize("query", [
    "apri il sito x.test e mostrami la fattura di giugno",
    "apri il sito x.test e mostrami il saldo",
    "open x.test and show me the invoice",
])
def test_una_richiesta_che_non_e_una_collezione_non_estrae(query) -> None:
    assert "extract_entries" not in _tools(_riscrivi(query))
