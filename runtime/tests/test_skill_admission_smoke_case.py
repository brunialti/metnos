"""Test del generatore smoke case di skill_admission (_smoke_case_for_plan)
+ filtro qualifier provider in _add_smoke_cases_for (skills_cli).

Regression canary per i 4 bug pre-15/5/2026 sera dell'importer:
1. mappa queries_by_pattern incompleta -> fallback "verb object" stub.
2. expected_first_tool con suffix provider anche su query senza marker
   -> aspettativa irraggiungibile (filtro grammar ADR 0136 esclude).
3. nessuno skip per pattern non mappato -> bad test data in BATTERY_IMPORTS.
4. nessuna dedup naturale (cf. smoke_imports.add_case).

Fix: _smoke_case_for_plan ritorna `_no_smoke=True` per pattern non mappati;
_strip_unmarked_provider strippa il suffix provider quando la query non ha
marker.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))
sys.path.insert(0, str(_RUNTIME / "cli"))


class _FakePlan:
    def __init__(self, verb, obj, qualifier=None, name=None, args=None):
        self.verb = verb
        self.obj = obj
        self.qualifier = qualifier
        self.name = name or f"{verb}_{obj}"
        self.args = args or []


class TestSmokeCaseForPlan:
    """_smoke_case_for_plan: mappa pattern -> query realistica IT."""

    def test_mapped_pattern_returns_realistic_query(self):
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("send", "messages",
                      name="send_messages_google_workspace")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case
        assert case["query"] == "scrivi a Roberto: ciao, ci vediamo alle 8"
        # expected_first_tool autoritativo dalla mappa = builtin canonical.
        # NON deriva da plan.name (che ha suffix provider).
        assert case["expected_first_tool"] == "send_messages"

    def test_find_messages_maps_to_read_messages_builtin(self):
        """find_messages non esiste come builtin → mappa ritorna read_messages."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("find", "messages",
                      name="find_messages_google_workspace")
        case = _smoke_case_for_plan(p)
        assert case["expected_first_tool"] == "read_messages"

    def test_set_events_maps_to_create_events_builtin(self):
        """set_events non esiste come builtin → mappa ritorna create_events."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("set", "events",
                      name="set_events_google_workspace")
        case = _smoke_case_for_plan(p)
        assert case["expected_first_tool"] == "create_events"

    def test_unmapped_pattern_returns_no_smoke_flag(self):
        """Pattern non in queries_by_pattern -> skip auto-add."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("compress", "files", qualifier="zip",
                      name="compress_files_zip")
        case = _smoke_case_for_plan(p)
        assert case.get("_no_smoke") is True
        assert "_reason" in case

    def test_no_stub_fallback_query_emitted(self):
        """Niente case con query stub "verb object" generica EN."""
        from skill_admission import _smoke_case_for_plan
        # Verbo+obj che non sono nella mappa.
        p = _FakePlan("render", "texts", name="render_texts")
        case = _smoke_case_for_plan(p)
        # Stub "render texts" NON deve essere emesso.
        assert "query" not in case or case.get("_no_smoke")

    def test_mapped_pattern_with_qualifier(self):
        """Pattern con qualifier (es. files_xlsx) deve matchare."""
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan("read", "files", qualifier="xlsx",
                      name="read_files_xlsx")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case
        assert "foglio" in case["query"].lower()
        assert case["expected_first_tool"] == "read_files_xlsx"


class TestStripUnmarkedProvider:
    """_strip_unmarked_provider: allinea expected con filtro grammar ADR 0136."""

    def test_strips_suffix_when_query_has_no_marker(self):
        from skills_cli import _strip_unmarked_provider
        from tool_grammar import _PROVIDER_SUFFIX_MARKERS
        out = _strip_unmarked_provider(
            "send_messages_google_workspace",
            "scrivi a Roberto",
            _PROVIDER_SUFFIX_MARKERS,
        )
        assert out == "send_messages"

    def test_keeps_suffix_when_query_has_marker(self):
        from skills_cli import _strip_unmarked_provider
        from tool_grammar import _PROVIDER_SUFFIX_MARKERS
        # Marker "gmail" presente nella query.
        out = _strip_unmarked_provider(
            "send_messages_google_workspace",
            "manda via gmail a Roberto",
            _PROVIDER_SUFFIX_MARKERS,
        )
        assert out == "send_messages_google_workspace"

    def test_passthrough_canonical_tool(self):
        """Tool senza suffix provider -> identita'."""
        from skills_cli import _strip_unmarked_provider
        from tool_grammar import _PROVIDER_SUFFIX_MARKERS
        out = _strip_unmarked_provider(
            "send_messages", "scrivi a Roberto",
            _PROVIDER_SUFFIX_MARKERS,
        )
        assert out == "send_messages"

    def test_workspace_marker_word_keeps_suffix(self):
        from skills_cli import _strip_unmarked_provider
        from tool_grammar import _PROVIDER_SUFFIX_MARKERS
        out = _strip_unmarked_provider(
            "find_files_google_workspace",
            "cerca documenti nel mio google drive",
            _PROVIDER_SUFFIX_MARKERS,
        )
        assert out == "find_files_google_workspace"


class TestQueriesByPatternCoverage:
    """La mappa `queries_by_pattern` copre i pattern comuni §2.2."""

    @pytest.mark.parametrize("verb,obj", [
        ("read", "events"), ("set", "events"), ("delete", "events"),
        ("find", "messages"), ("read", "messages"), ("send", "messages"),
        ("move", "messages"), ("find", "files"), ("read", "files"),
        ("write", "files"), ("delete", "files"), ("create", "dirs"),
        ("list", "dirs"), ("read", "contacts"), ("share", "files"),
    ])
    def test_common_pattern_is_mapped(self, verb, obj):
        from skill_admission import _smoke_case_for_plan
        p = _FakePlan(verb, obj, name=f"{verb}_{obj}")
        case = _smoke_case_for_plan(p)
        assert "_no_smoke" not in case, (
            f"pattern ({verb}, {obj}) non mappato in queries_by_pattern"
        )
        assert case["query"] and len(case["query"]) > len(f"{verb} {obj}"), (
            f"query troppo corta per ({verb}, {obj}): {case['query']!r}"
        )
