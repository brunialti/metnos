"""NLU is an optional feature, but its LLM request must stay virtualized."""
from __future__ import annotations

from types import SimpleNamespace


def test_nlu_uses_the_fast_procedural_router_request(monkeypatch):
    import nlu

    observed: dict[str, object] = {}

    class Provider:
        def chat(self, system, user, **kwargs):
            observed.update(system=system, user=user, **kwargs)
            return SimpleNamespace(text=(
                '{"ordering":{"mode":"none","key":"","desc":false},'
                '"time_window":"","recurrence":{"every":"","at":""},'
                '"count_intent":false,"visualize_intent":false}'
            ))

    def get_llm(tier, *, level=None):
        observed["tier"] = tier
        observed["level"] = level
        return Provider()

    monkeypatch.setattr(nlu, "get_llm", get_llm)
    assert nlu._extract("mostra le mail") == {
        "ordering": {"mode": "none", "key": "", "desc": False},
        "time_window": "", "recurrence": {"every": "", "at": ""},
        "count_intent": False, "visualize_intent": False,
    }
    assert observed["tier"] == "fast"
    assert observed["level"] == "procedural"
    assert observed["max_tokens"] == 200
    assert "grammar" in observed
    assert not ({"temperature", "think", "reasoning_budget"} & observed.keys())
