"""test_align_framework_action_pairs — la copertura si verifica sulla COPPIA.

La guardia (6/8/2026) ripara il piano quando l'intent chiede una coppia
produttrice (verbo, oggetto) che nessun passo esegue, mentre un passo produce
l'oggetto giusto col verbo sbagliato.

REGRESSIONE che questi test bloccano: la prima stesura aggiornava un insieme
`covered` che in quella funzione non esisteva. Il `NameError` finiva nel
`except Exception -> noop` della guardia, quindi era INVISIBILE: il piano usciva
mutato a meta' (tool e args gia' riscritti) e le coppie successive non venivano
mai guardate. Misurato: 47 piani su 2424 del corpus reale. Un test con DUE
coppie scoperte lo cattura; con una sola no, perche' il risultato visibile
sarebbe comunque corretto (§2.8: mai un esito che non corrisponde alla realta').
"""
from __future__ import annotations

import logging

from engine.dispatch import _align_framework_action_pairs as _align

_CATALOG = [{"name": n} for n in (
    "get_location", "find_places", "get_places",
    "read_persons", "get_persons", "list_dirs", "write_files",
    "describe_entries", "filter_entries")]


class _Step:
    def __init__(self, tool, args=None):
        self.tool = tool
        self.args = dict(args or {})


class _Fw:
    def __init__(self, *tools):
        self.steps = [_Step(t) for t in tools]
        self.final_message = ""


class _Intent:
    def __init__(self, actions):
        self.actions = actions
        self.verb = actions[0]["verb"] if actions else ""
        self.object = actions[0]["object"] if actions else ""


def _tools(fw):
    return [s.tool for s in fw.steps]


def test_uncovered_pair_is_repaired():
    """{get, places} chiesto, ma il piano ha find_places: verbo sbagliato
    sull'oggetto giusto -> get_places."""
    fw = _Fw("get_location", "find_places", "final_answer")
    intent = _Intent([{"verb": "get", "object": "places"}])
    out = _align(fw, intent, "dove mi trovo", _CATALOG)
    assert _tools(out) == ["get_location", "get_places", "final_answer"]


def test_two_uncovered_pairs_are_both_repaired():
    """REGRESSIONE `covered`: con DUE coppie scoperte la guardia deve ripararle
    entrambe. Con il NameError ne riparava una e usciva in silenzio."""
    fw = _Fw("find_places", "get_persons", "final_answer")
    intent = _Intent([{"verb": "get", "object": "places"},
                      {"verb": "read", "object": "persons"}])
    out = _align(fw, intent, "chi sono e dove sono", _CATALOG)
    assert _tools(out) == ["get_places", "read_persons", "final_answer"]


def test_no_exception_is_swallowed(caplog):
    """La guardia non deve mai finire nel proprio `except`: un errore li' dentro
    lascia il piano mutato a meta' senza che nessuno lo sappia."""
    fw = _Fw("find_places", "get_persons", "final_answer")
    intent = _Intent([{"verb": "get", "object": "places"},
                      {"verb": "read", "object": "persons"}])
    with caplog.at_level(logging.WARNING):
        _align(fw, intent, "chi sono e dove sono", _CATALOG)
    assert not [r for r in caplog.records if "noop" in r.getMessage()]


def test_consumer_clause_does_not_rewrite_a_producer():
    """Confine dichiarato: una clausola CONSUMATRICE non si copre riscrivendo
    un produttore, o si distrugge il flusso dei dati."""
    fw = _Fw("list_dirs", "write_files", "final_answer")
    intent = _Intent([{"verb": "list", "object": "files"},
                      {"verb": "write", "object": "files"}])
    out = _align(fw, intent, "elenca i file e mettili in un foglio", _CATALOG)
    assert _tools(out) == ["list_dirs", "write_files", "final_answer"]


# ── Il contratto del nuovo strumento decide se la riscrittura e' lecita ────
#
# Turno «dov'e' il Duomo di Milano» (16/8/2026): l'intent leggeva {get,
# places} e la guardia trasformava il CORRETTO find_places in get_places, che
# accetta solo coordinate. Il nome nella richiesta non aveva dove andare e il
# turno moriva su «Manca un argomento obbligatorio», invece di cercare per
# nome. Una riscrittura che lascia il passo ineseguibile non e' un
# allineamento: l'oracolo e' quello del Validator, cosi' la guardia non puo'
# produrre un passo che il Validator poi rifiuterebbe.

_CATALOG_TIPIZZATO = [
    {"name": "find_places",
     "args_schema": {"type": "object", "required": ["queries"],
                     "properties": {"queries": {"type": "array"},
                                    "near": {"type": "object"}}}},
    {"name": "get_places",
     "args_schema": {"type": "object",
                     "requires_one_of": [["coords", "entries", "from_step"]],
                     "properties": {"coords": {"type": "array"},
                                    "entries": {"type": "array"},
                                    "from_step": {"type": "integer"}}}},
    {"name": "get_location", "args_schema": {"type": "object"}},
]


def test_rewrite_refused_when_the_new_contract_stays_unsatisfied():
    """find_places da solo con una query testuale: get_places non ha da dove
    prendere le coordinate, quindi il passo NON si riscrive."""
    fw = _Fw("find_places", "final_answer")
    fw.steps[0].args = {"queries": ["Duomo di Milano"]}
    intent = _Intent([{"verb": "get", "object": "places"}])
    out = _align(fw, intent, "dov'e' il Duomo di Milano", _CATALOG_TIPIZZATO)
    assert _tools(out) == ["find_places", "final_answer"]
    assert out.steps[0].args == {"queries": ["Duomo di Milano"]}


def test_rewrite_kept_when_an_upstream_producer_satisfies_it():
    """Caso storico «dimmi la via piu' vicina»: con get_location a monte il
    contratto di get_places e' soddisfatto da from_step e la riscrittura resta."""
    fw = _Fw("get_location", "find_places", "final_answer")
    fw.steps[1].args = {"queries": ["via"]}
    intent = _Intent([{"verb": "get", "object": "places"}])
    out = _align(fw, intent, "dimmi la via piu' vicina", _CATALOG_TIPIZZATO)
    assert _tools(out) == ["get_location", "get_places", "final_answer"]
    assert out.steps[1].args.get("from_step") == 1
