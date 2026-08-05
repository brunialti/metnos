from __future__ import annotations

import sys
from types import SimpleNamespace


def test_admin_default_llm_bridge_uses_middle_tier_without_hidden_policy(
        monkeypatch):
    from system import admin

    calls: list[dict] = []

    class _Router:
        def chat(self, system, prompt, **kwargs):
            calls.append({"system": system, "prompt": prompt, **kwargs})
            return SimpleNamespace(text='{"kind":"unknown"}')

    monkeypatch.setitem(
        sys.modules, "llm_router", SimpleNamespace(LLMRouter=_Router))

    assert admin._default_llm_call("translate this") == '{"kind":"unknown"}'
    assert calls == [{
        "system": "",
        "prompt": "translate this",
        "tier": "middle",
        "max_tokens": 400,
    }]
