from __future__ import annotations


def test_router_provider_attaches_logical_tier_to_provider_telemetry(monkeypatch):
    import llm_router
    import llm_telemetry

    records: list[dict] = []
    monkeypatch.setattr(llm_telemetry, "_sinks", [records.append])

    class Provider:
        name = "llamacpp"
        model = "physical-model"

        def chat(self, system, user, **_kwargs):
            result = type("Result", (), {
                "text": "ok", "in_tokens": 1, "out_tokens": 1,
                "latency_ms": 1,
            })()
            llm_telemetry.record(
                provider=self.name, model=self.model,
                system=system, user=user, result=result)
            return result

    monkeypatch.setattr(
        llm_router, "make_provider_from_spec", lambda _spec: Provider())
    tiers = {
        "fast": {"provider": "llamacpp", "model": "physical-model"},
        "wise": {"provider": "llamacpp", "model": "physical-model"},
    }

    llm_router.LLMRouter(tiers_override=tiers).provider("fast").chat("s", "u")

    assert records[0]["tier"] == "fast"


def test_tier_context_is_scoped_and_explicit_record_value_wins(monkeypatch):
    import llm_telemetry

    records: list[dict] = []
    monkeypatch.setattr(llm_telemetry, "_sinks", [records.append])
    with llm_telemetry.tier_context("middle"):
        llm_telemetry.record(provider="stub", result="one")
        llm_telemetry.record(provider="stub", result="two", tier="frontier")
    llm_telemetry.record(provider="stub", result="three")

    assert [item["tier"] for item in records] == [
        "middle", "frontier", None,
    ]


def test_fallback_provider_inherits_tier_policy_and_telemetry(monkeypatch):
    import llm_router
    import llm_telemetry

    calls: list[dict] = []
    records: list[dict] = []
    monkeypatch.setattr(llm_telemetry, "_sinks", [records.append])

    class Provider:
        name = "llamacpp"
        model = "fallback-model"

        def chat(self, system, user, **kwargs):
            calls.append(kwargs)
            result = type("Result", (), {"text": "ok"})()
            llm_telemetry.record(
                provider=self.name, model=self.model,
                system=system, user=user, result=result)
            return result

    monkeypatch.setattr(
        llm_router, "make_provider_from_spec", lambda _spec: Provider())
    provider = llm_router.provider_from_tier_spec(
        "wise", {"provider": "llamacpp", "model": "fallback-model",
                 "think": True, "temperature": 0.15,
                 "reasoning_budget": 384})

    provider.chat("s", "u")

    assert calls == [{
        "temperature": 0.15, "think": True, "reasoning_budget": 384,
    }]
    assert records[0]["tier"] == "wise"


def test_fallback_chain_inherits_configured_tier_policy():
    import llm_router

    router = llm_router.LLMRouter(tiers_override={
        "fast": {"provider": "stub", "model": "fast-model"},
        "wise": {
            "provider": "stub", "model": "primary-model",
            "think": True, "temperature": 0.15,
            "reasoning_budget": 384,
            "fallback": [{"provider": "other", "model": "backup-model"}],
        },
    })

    assert router.fallback_chain("wise")[1] == {
        "provider": "other", "model": "backup-model",
        "think": True, "temperature": 0.15,
        "reasoning_budget": 384,
    }
