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
