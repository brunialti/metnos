"""test_align_framework_objects — guard §7.9 ri-allineamento oggetto-fratello.

Bug live 17/6/2026 (flusso issue github): clausola intent {find,issues} ma il
proposer Metis compone `find_pulls_github` (object=pulls) benche' il prefilter
ranki `find_issues_github` #1 — l'LLM sbaglia il fratello stesso-verbo. Il
guard deterministico `_align_framework_objects` corregge il tool quando
l'oggetto scelto e' ASSENTE dagli oggetti che l'intent ha decomposto per quel
verbo E un oggetto-intent mappa a un tool reale del catalog.

Determinismo §7.9: niente LLM, solo parse_name + lookup catalog.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.dispatch import _align_framework_objects  # noqa: E402

_CATALOG = [{"name": n} for n in (
    "find_issues_github", "find_pulls_github", "find_entries",
    "write_entries", "send_messages_github", "filter_entries")]


def _fw(*tools):
    return NS(steps=[NS(tool=t) for t in tools])


def _tools(fw):
    return [s.tool for s in fw.steps]


def test_swaps_wrong_object_sibling_same_qualifier():
    """find_pulls_github (object=pulls) -> find_issues_github quando l'intent
    chiede {find,issues}: stesso qualifier github preservato."""
    fw = _fw("find_pulls_github", "find_entries", "filter_entries")
    intent = NS(actions=[{"verb": "find", "object": "issues"},
                         {"verb": "write", "object": "entries"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["find_issues_github", "find_entries", "filter_entries"]


def test_swaps_to_no_qualifier_tool():
    """find_pulls_github -> find_entries (no qualifier) se l'oggetto-intent del
    verbo find e' entries."""
    fw = _fw("find_pulls_github", "filter_entries")
    intent = NS(actions=[{"verb": "find", "object": "entries"},
                         {"verb": "send", "object": "messages"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["find_entries", "filter_entries"]


def test_noop_when_object_already_matches():
    fw = _fw("find_issues_github")
    intent = NS(actions=[{"verb": "find", "object": "issues"},
                         {"verb": "write", "object": "entries"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["find_issues_github"]


def test_noop_when_verb_not_in_intent():
    """Step helper (filter_entries) il cui verbo non e' fra le azioni intent:
    intoccato."""
    fw = _fw("filter_entries")
    intent = NS(actions=[{"verb": "find", "object": "issues"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["filter_entries"]


def test_noop_when_chosen_object_is_valid_intent_object():
    """Intent decompone SIA issues SIA pulls per find: il pulls scelto e'
    legittimo → nessuno swap (no falso-positivo su intent ambiguo)."""
    fw = _fw("find_pulls_github")
    intent = NS(actions=[{"verb": "find", "object": "issues"},
                         {"verb": "find", "object": "pulls"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["find_pulls_github"]


def test_noop_without_actions():
    fw = _fw("find_pulls_github")
    _align_framework_objects(fw, NS(actions=[]), _CATALOG)
    assert _tools(fw) == ["find_pulls_github"]


def test_noop_when_target_tool_absent_from_catalog():
    """L'oggetto-intent non ha un tool nel catalog → nessuno swap (no tool
    inventato)."""
    fw = _fw("find_pulls_github")
    intent = NS(actions=[{"verb": "find", "object": "contacts"}])
    _align_framework_objects(fw, intent, _CATALOG)
    assert _tools(fw) == ["find_pulls_github"]
