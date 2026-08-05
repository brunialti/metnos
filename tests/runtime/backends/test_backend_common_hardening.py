from __future__ import annotations

import sys
from pathlib import Path

import pytest

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")


def test_frontier_provider_only_spec_inherits_model_from_router_sot():
    from llm_router import DEFAULT_TIERS, LLMRouter

    tiers = {name: dict(spec) for name, spec in DEFAULT_TIERS.items()}
    tiers["frontier"] = {"provider": "anthropic"}
    router = LLMRouter(tiers_override=tiers)

    assert router.describe()["frontier"]["model"] == \
        DEFAULT_TIERS["frontier"]["model"]
    assert router.provider("frontier").model == \
        DEFAULT_TIERS["frontier"]["model"]


def test_direct_anthropic_provider_spec_requires_explicit_model():
    from llm_provider import make_provider_from_spec

    with pytest.raises(ValueError, match="requires an explicit model"):
        make_provider_from_spec({"provider": "anthropic"})


def test_google_transient_retries_use_bounded_exponential_backoff(
        tmp_path, monkeypatch):
    from backends import _google_api_runner as runner

    attempts = iter([
        (1, "", "rate limited"),
        (1, "", "rate limited"),
        (0, "{}", ""),
    ])
    delays = []
    monkeypatch.setattr(runner, "_skill_root", lambda: tmp_path)
    monkeypatch.setattr(runner, "_run_api", lambda *_a, **_kw: next(attempts))
    monkeypatch.setattr(runner, "_classify_error",
                        lambda _rc, _stderr: "rate_limited")
    monkeypatch.setattr(runner.time, "sleep", delays.append)

    data, error = runner.run_with_retry(
        ["calendar", "list"], executor="read_events", args_base={},
        max_retries=2, backoff_base_s=0.5,
    )

    assert data == {}
    assert error is None
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize(
    ("module_name", "result_kind"),
    [
        ("backends.contacts.google_workspace", "entries"),
        ("backends.events.google_workspace", "entries"),
        ("backends.messages.gmail_google_workspace", "results"),
    ],
)
def test_google_backends_share_one_oauth_boundary(
        module_name, result_kind, monkeypatch):
    module = __import__(module_name, fromlist=["_auth_needs_inputs"])
    calls = []

    def common(args, *, executor, result_kind):
        calls.append((args, executor, result_kind))
        return {"ok": True, "decision": "needs_inputs", result_kind: []}

    monkeypatch.setattr(module, "_common_auth_needs_inputs", common)
    out = module._auth_needs_inputs({"x": 1}, executor="test_executor")
    assert out["decision"] == "needs_inputs"
    assert calls == [({"x": 1}, "test_executor", result_kind)]

