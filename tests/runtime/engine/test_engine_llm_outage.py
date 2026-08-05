"""Planner dependency outages are bounded and visible to the user."""
from __future__ import annotations

def test_fast_llm_timeout_stops_once_and_reports_progress(monkeypatch):
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
    assert result["error_class"] == "provider_unavailable"
    assert result["match_source"] == "llm_unavailable"
    assert "Modello linguistico non disponibile" in result["final_text"]
    assert "servizio LLM" in result["final_text"]
    assert updates == [result["final_text"]]
    assert len(calls) == 1
    assert calls[0]["request_timeout_s"] == agent_runtime.ENGINE_FAST_LLM_TIMEOUT_S
