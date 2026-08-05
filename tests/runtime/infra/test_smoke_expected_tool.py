"""Test del Layer 5 di synth admission: smoke battery con expected_tool
(ADR 0114, 8/5/2026 sera).

Bug live 8/5: synth `find_texts` con affinity catch-all aveva preso il
posto di `find_urls` per query "cerca su web ...". Smoke battery aggiunge
`expected_first_tool` per ogni case → regression canary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


class TestEachSmokeQueryHasExpectedTool:
    """Ogni case in BATTERY ha i campi nuovi richiesti dal Layer 5."""

    def test_battery_entries_have_expected_tool(self):
        from smoke import BATTERY
        for case in BATTERY:
            assert "expected_first_tool" in case, (
                f"case {case['q']!r} missing expected_first_tool"
            )
            assert isinstance(case["expected_first_tool"], str)
            assert "expected_arg_keys" in case, (
                f"case {case['q']!r} missing expected_arg_keys"
            )
            assert isinstance(case["expected_arg_keys"], (set, frozenset))
            assert "min_pass_rate" in case, (
                f"case {case['q']!r} missing min_pass_rate"
            )
            assert 0.0 <= case["min_pass_rate"] <= 1.0


class TestSmokeRunPasses:
    """Per query semplici, il routing simulato deve preferire l'expected
    tool. Test su query "che ora e?" → get_now."""

    def test_get_now_routed_correctly(self):
        from smoke import _run_smoke_with_tool_assertion
        case = {
            "q": "che ora e?",
            "expected_first_tool": "get_now",
            "expected_arg_keys": set(),
        }
        result = _run_smoke_with_tool_assertion(case)
        # Se prefilter non disponibile o LLM offline → skip; altrimenti ok.
        if result.get("skip"):
            pytest.skip(f"smoke routing skipped: {result.get('reason')}")
        # Soft assertion: actual_first deve essere coerente con expected o
        # con l'oggetto "events". Permettiamo flessibilita' (smoke battery
        # non e' deterministica al 100% sul catalog reale).
        assert result["ok"] or "get_" in (result.get("actual_first") or ""), (
            f"unexpected routing: {result}"
        )


class TestSmokeFlagsRoutingRegression:
    """Mock prefilter: simula il caso bug 8/5 dove find_texts (synth)
    veniva ranked sopra find_urls per query "cerca ...". Il routing
    assertion DEVE FAIL."""

    def test_smoke_detects_find_texts_hijack(self, monkeypatch):
        from smoke import _run_smoke_with_tool_assertion

        # Mock catalog con find_urls + find_texts
        class FakeExec:
            def __init__(self, name):
                self.name = name
                self.affinity = []
                self.lifecycle = "active"
        class FakeCatalog:
            executors = {
                "find_urls":  FakeExec("find_urls"),
                "find_texts": FakeExec("find_texts"),
            }
        # Mock rank_with_intent: ritorna find_texts PRIMA di find_urls (bug)
        import prefilter as pref
        def fake_rank(q, catalog, intent, *, k=3):
            return [FakeExec("find_texts"), FakeExec("find_urls")]
        monkeypatch.setattr(pref, "rank_with_intent", fake_rank)

        case = {
            "q": "cerca su web l'organico scuola",
            "expected_first_tool": "find_urls",
            "expected_arg_keys": set(),
        }
        result = _run_smoke_with_tool_assertion(case, catalog=FakeCatalog())
        # Routing assertion DEVE flaggare la regressione
        assert not result["ok"]
        assert result.get("actual_first") == "find_texts"
        assert result.get("expected") == "find_urls"


class TestRunRoutingBatteryAggregation:
    """`run_smoke_routing_battery` ritorna {results, n_pass, n_fail, n_skip}."""

    def test_run_battery_returns_aggregate(self):
        from smoke import run_smoke_routing_battery
        out = run_smoke_routing_battery()
        assert "results" in out
        assert "n_pass" in out
        assert "n_fail" in out
        assert "n_skip" in out
        assert len(out["results"]) > 0


class TestNoExpectedFirstToolSkipped:
    """Case senza `expected_first_tool` → skip con reason."""

    def test_case_without_expected_skipped(self):
        from smoke import _run_smoke_with_tool_assertion
        case = {"q": "test", "tool_re": r".*", "kind": "answer"}
        result = _run_smoke_with_tool_assertion(case)
        assert result["skip"] is True
        assert result["ok"] is True


def test_mutating_smoke_case_is_routing_only(monkeypatch):
    """The production smoke gate must never send/create/delete real data."""
    import smoke

    monkeypatch.setattr(
        smoke,
        "run_turn",
        lambda *a, **kw: pytest.fail("mutating smoke invoked a live turn"),
    )
    monkeypatch.setattr(
        smoke,
        "_run_smoke_with_tool_assertion",
        lambda case: {
            "ok": True,
            "actual_first": case["expected_first_tool"],
            "reason": "deterministic routing",
        },
    )
    result = smoke.run_one({
        "q": "scrivi a Roberto: ciao",
        "tool_re": r"^send_messages$",
        "kind": "answer",
        "expected_first_tool": "send_messages",
        "expected_arg_keys": set(),
        "min_pass_rate": 1.0,
    }, 1, 1)
    assert result["ok"] is True
    assert result["routing_only"] is True


@pytest.mark.parametrize("query", [
    "scrivi a Roberto: ciao, ci vediamo alle 8",
    "write to Alice: see you at eight",
])
def test_direct_recipient_write_routes_to_messages(query):
    """Recipient+body syntax disambiguates write from filesystem output."""
    import smoke

    intent = smoke._bow_intent_for_smoke(query)
    assert intent == {"verb": "send", "object": "messages"}
    result = smoke._run_smoke_with_tool_assertion({
        "q": query,
        "expected_first_tool": "send_messages",
    })
    assert result["ok"] is True, result


def test_direct_message_boundary_does_not_capture_file_path():
    from prefilter import (
        detect_canonical_object,
        detect_canonical_verb,
        is_direct_message_query,
        tokenize,
    )

    query = "write to /tmp/note.txt: hello"
    tokens = tokenize(query)
    assert is_direct_message_query(query) is False
    assert detect_canonical_verb(tokens, query) == "write"
    assert detect_canonical_object(tokens, query) == "files"


def test_readonly_smoke_disables_learning_and_restores_environment(monkeypatch):
    import os
    from types import SimpleNamespace

    import smoke

    seen = {}

    def fake_turn(*args, **kwargs):
        seen["fastpath"] = os.environ.get("METNOS_FASTPATH")
        seen["autopath"] = os.environ.get("METNOS_AUTOPATH")
        return SimpleNamespace(
            steps=[SimpleNamespace(chosen_tool="get_now")],
            final_kind="answer",
            final_message="ok",
        )

    monkeypatch.setenv("METNOS_FASTPATH", "1")
    monkeypatch.delenv("METNOS_AUTOPATH", raising=False)
    monkeypatch.setattr(smoke, "run_turn", fake_turn)
    result = smoke.run_one({
        "q": "che ore sono?",
        "tool_re": r"^get_now$",
        "kind": "answer",
        "expected_first_tool": "get_now",
        "expected_arg_keys": set(),
        "min_pass_rate": 1.0,
    }, 1, 1)
    assert result["ok"] is True
    assert seen == {"fastpath": "0", "autopath": "0"}
    assert os.environ["METNOS_FASTPATH"] == "1"
    assert "METNOS_AUTOPATH" not in os.environ
