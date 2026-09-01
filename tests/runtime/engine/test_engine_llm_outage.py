"""Planner dependency failures are bounded and accurately reported."""
from __future__ import annotations


def test_fast_llm_timeout_stops_once_and_reports_timeout(monkeypatch):
    import agent_runtime
    import intent_extractor
    import llm_router

    calls = []
    updates = []

    class OfflineProvider:
        def chat(self, _system, _user, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("local model did not answer")

    class Progress:
        def update_free(self, message):
            updates.append(message)

    monkeypatch.setattr(
        llm_router.LLMRouter, "provider",
        lambda _self, _tier: OfflineProvider(),
    )
    monkeypatch.setattr(
        intent_extractor, "extract_intent",
        lambda _query, llm_call: llm_call("system", "user") or None,
    )

    result = agent_runtime._run_engine(
        "controlla le email", [], turn_id="llm-down", actor="host",
        channel="http", owner_user_id="owner", progress=Progress(),
    )

    assert result["final_kind"] == "error"
    assert result["error_class"] == "provider_timeout"
    assert result["match_source"] == "llm_timeout"
    assert "tempo previsto" in result["final_text"]
    assert "occupato" in result["final_text"]
    assert "non disponibile" not in result["final_text"]
    assert updates == [result["final_text"]]
    assert len(calls) == 1
    assert (
        calls[0]["request_timeout_s"]
        == agent_runtime.ENGINE_FAST_LLM_TIMEOUT_S
    )


def test_fast_llm_connection_failure_reports_unavailable(monkeypatch):
    import agent_runtime
    import intent_extractor
    import llm_router

    class OfflineProvider:
        def chat(self, _system, _user, **_kwargs):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(
        llm_router.LLMRouter, "provider",
        lambda _self, _tier: OfflineProvider(),
    )
    monkeypatch.setattr(
        intent_extractor, "extract_intent",
        lambda _query, llm_call: llm_call("system", "user") or None,
    )

    result = agent_runtime._run_engine(
        "controlla le email", [], turn_id="llm-offline", actor="host",
        channel="http", owner_user_id="owner",
    )

    assert result["final_kind"] == "error"
    assert result["error_class"] == "provider_unavailable"
    assert result["match_source"] == "llm_unavailable"
    assert "Modello linguistico non disponibile" in result["final_text"]
    assert "servizio LLM" in result["final_text"]


def test_wrapped_timeout_has_priority_over_generic_provider_error():
    import agent_runtime

    class ProviderError(RuntimeError):
        pass

    try:
        try:
            raise TimeoutError("request deadline exhausted")
        except TimeoutError as exc:
            raise ProviderError("provider request failed") from exc
    except ProviderError as exc:
        assert agent_runtime._llm_dependency_failure_kind(exc) == "provider_timeout"
