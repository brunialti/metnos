from __future__ import annotations


class _RecordingProvider:
    mode = "local"

    def __init__(self, name: str = "llamacpp"):
        self.name = name
        self.model = "recording"
        self.calls: list[dict] = []

    def chat(self, _system, _user, **kwargs):
        self.calls.append(dict(kwargs))
        return object()

    def chat_with_tools(self, _system, _user, _tools, history=None, **kwargs):
        self.calls.append({"history": history, **kwargs})
        return object()


def _tiers(*, provider: str = "llamacpp") -> dict:
    return {
        "fast": {
            "provider": provider,
            "model": "fast-model",
            "temperature": 0.35,
            "think": True,
            "reasoning_budget": 192,
        },
        "wise": {
            "provider": provider,
            "model": "wise-model",
        },
    }


def test_provider_boundary_applies_tier_policy_to_direct_chat(monkeypatch):
    import llm_router

    raw = _RecordingProvider()
    monkeypatch.setattr(llm_router, "make_provider_from_spec", lambda _spec: raw)

    router = llm_router.LLMRouter(tiers_override=_tiers())
    provider = router.provider("fast")
    provider.chat("system", "user", max_tokens=40)
    provider.chat_with_tools("system", "user", [], history=[{"role": "user"}])

    assert provider.model == "recording"  # public provider attributes remain available
    assert raw.calls == [
        {
            "max_tokens": 40,
            "temperature": 0.35,
            "think": True,
            "reasoning_budget": 192,
        },
        {
            "history": [{"role": "user"}],
            "temperature": 0.35,
            "think": True,
            "reasoning_budget": 192,
        },
    ]


def test_operation_cannot_override_tier_policy(monkeypatch):
    import llm_router
    import pytest

    raw = _RecordingProvider()
    monkeypatch.setattr(llm_router, "make_provider_from_spec", lambda _spec: raw)

    with pytest.raises(llm_router.TierConfigError, match="cannot be overridden"):
        llm_router.LLMRouter(tiers_override=_tiers()).provider("fast").chat(
            "system", "user", temperature=0.0, think=False,
            reasoning_budget=8)

    assert raw.calls == []


def test_reasoning_budget_is_not_sent_to_non_llamacpp_provider(monkeypatch):
    import llm_router

    raw = _RecordingProvider(name="anthropic")
    monkeypatch.setattr(llm_router, "make_provider_from_spec", lambda _spec: raw)

    llm_router.LLMRouter(
        tiers_override=_tiers(provider="anthropic")).provider("fast").chat(
            "system", "user")

    assert raw.calls == [{"temperature": 0.35, "think": True}]


def test_router_resolves_the_default_config_path_when_constructed(
        tmp_path, monkeypatch):
    """A file created by the Models UI takes effect without an import-time path."""

    import llm_router

    path = tmp_path / "llm_tiers.toml"
    path.write_text(
        """
[fast]
provider = "stub"
model = "configured-fast"

[wise]
provider = "stub"
model = "configured-wise"
""".strip(),
        encoding="utf-8",
    )
    seen: list[dict] = []

    def _make(spec):
        seen.append(dict(spec))
        return _RecordingProvider(name="stub")

    monkeypatch.setattr(llm_router, "_default_config_path", lambda: path)
    monkeypatch.setattr(llm_router, "make_provider_from_spec", _make)

    llm_router.LLMRouter().provider("fast")

    assert seen == [{"provider": "stub", "model": "configured-fast"}]
