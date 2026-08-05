from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path


class _Provider:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    def chat(self, system, prompt, **kwargs):
        self._calls.append({"system": system, "prompt": prompt, **kwargs})
        return SimpleNamespace(text="ok")


class _Router:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    def provider(self, tier, **kwargs):
        self._calls.append({"tier": tier, **kwargs})
        return _Provider(self._calls)


def _router_module(calls: list[dict]):
    return SimpleNamespace(LLMRouter=lambda: _Router(calls))


def test_telos_uses_its_tier_without_hidden_policy(monkeypatch):
    import telos_introspect

    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "llm_router", _router_module(calls))

    assert telos_introspect._llm_invoke_tier("prompt", grammar="rule") == "ok"
    assert calls == [
        {"tier": "creative"},
        {"system": "", "prompt": "prompt", "max_tokens": 2048, "grammar": "rule"},
    ]


def test_alignment_uses_the_middle_tier_without_hidden_policy(monkeypatch):
    import alignment_engine

    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "llm_router", _router_module(calls))

    assert alignment_engine._default_llm_invoke("prompt") == "ok"
    assert calls[0]["tier"] == "middle"
    assert calls[0]["tier"].level is None
    assert calls[1] == {
        "system": "", "prompt": "prompt", "max_tokens": 2048,
    }


def test_background_consumers_do_not_restore_a_physical_provider_bypass():
    root = Path(__file__).resolve().parents[3]
    expected = {
        "runtime/telos_introspect.py": 'provider(_BACKGROUND_TIER)',
        "runtime/alignment_engine.py": 'provider(_BACKGROUND_TIER)',
        "runtime/admin/manifest_refactor.py": 'provider(tier_for("manifest.refactor"))',
    }
    for relative_path, router_call in expected.items():
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "LlamaCppProvider" not in source
        assert "from llm_router import LLMRouter" in source
        assert router_call in source
