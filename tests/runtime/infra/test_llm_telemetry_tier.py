from __future__ import annotations

import pytest


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


def test_durable_attempt_context_is_bounded_content_free_and_fail_soft():
    import llm_telemetry

    sink = llm_telemetry.BoundedUsageSink(max_records=1)
    result = type("Result", (), {
        "in_tokens": 11, "out_tokens": 7, "latency_ms": 3,
    })()
    with llm_telemetry.attempt_context(
        workload_id="wrk-test",
        stage_id="stg-test",
        unit_key="unit-test",
        attempt_id="att-test",
        sink=sink,
    ):
        llm_telemetry.record(
            provider="fixture", model="private-model",
            system="never persist this", user="or this", result=result,
        )
        llm_telemetry.record(
            provider="fixture", model="private-model", result=result,
        )

    summary = sink.summary()
    assert summary["dropped"] == 1
    assert summary["usage_missing"] is True
    assert summary["cost_unknown"] is True
    assert (summary["input_tokens"], summary["output_tokens"]) == (11, 7)
    record = summary["records"][0]
    assert record["attempt_id"] == "att-test"
    assert record["model_digest"].startswith("sha256:")
    assert "system" not in record and "user" not in record

    with llm_telemetry.attempt_context(
        workload_id="wrk-test",
        stage_id="stg-test",
        unit_key="unit-test",
        attempt_id="att-test",
        sink=lambda _record: (_ for _ in ()).throw(RuntimeError("sink failed")),
    ):
        llm_telemetry.record(provider="fixture", result=result)


def test_failed_router_call_cannot_look_like_complete_or_zero_usage(monkeypatch):
    import llm_router
    import llm_telemetry

    class Provider:
        name = "llamacpp"
        model = "physical-model"

        def chat(self, _system, _user, **_kwargs):
            raise TimeoutError("provider stopped after accepting the call")

    monkeypatch.setattr(
        llm_router, "make_provider_from_spec", lambda _spec: Provider())
    sink = llm_telemetry.BoundedUsageSink()
    with llm_telemetry.attempt_context(
        workload_id="wrk-test",
        stage_id="stg-test",
        unit_key="unit-test",
        attempt_id="att-test",
        sink=sink,
    ):
        with pytest.raises(TimeoutError):
            llm_router.LLMRouter(tiers_override={
                "fast": {"provider": "llamacpp", "model": "physical-model"},
                "wise": {"provider": "llamacpp", "model": "physical-model"},
            }).provider("wise").chat("system", "user")

    summary = sink.summary()
    assert summary["records"] == []
    assert summary["dropped"] == 1
    assert summary["usage_missing"] is True
    assert summary["zero_calls_verified"] is False


def test_transport_usage_is_content_free_and_bound_to_parent_attempt():
    import llm_telemetry

    child = llm_telemetry.BoundedTransportUsageSink()
    result = type("Result", (), {
        "text": "private output",
        "in_tokens": 13,
        "out_tokens": 5,
        "latency_ms": 7,
        "cost_micros": 0,
    })()
    with llm_telemetry.transport_usage_context(child):
        llm_telemetry.record(
            provider="llamacpp",
            model="private-model",
            system="private prompt",
            user="private input",
            result=result,
            kind="vision",
            tier="vlm:default",
        )

    wire = child.export()
    assert "private prompt" not in str(wire)
    assert "private input" not in str(wire)
    assert "private output" not in str(wire)

    parent = llm_telemetry.BoundedUsageSink()
    parent.ingest_transport(
        wire,
        workload_id="wrk-test",
        stage_id="stg-test",
        unit_key="unit-test",
        attempt_id="att-test",
    )
    summary = parent.summary()
    assert summary["usage_missing"] is False
    assert (summary["input_tokens"], summary["output_tokens"]) == (13, 5)
    assert summary["records"][0]["attempt_id"] == "att-test"


def test_durable_usage_labels_cannot_carry_arbitrary_content():
    import llm_telemetry

    child = llm_telemetry.BoundedTransportUsageSink()
    result = type("Result", (), {
        "in_tokens": 1, "out_tokens": 1, "latency_ms": 1,
    })()
    with llm_telemetry.transport_usage_context(child):
        llm_telemetry.record(
            provider="name@example.test private",
            model="private-model",
            tier="private tier text",
            kind="private/kind",
            result=result,
        )

    wire = child.export()
    assert "example.test" not in str(wire)
    assert wire["records"][0]["provider"] == "unknown"
    assert wire["records"][0]["tier"] == "unknown"
    assert wire["records"][0]["kind"] == "unknown"
