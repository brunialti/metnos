"""Closed-contract tests for tier virtualization and fast levels."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest


EXPECTED_TIERS = (
    "fast", "middle", "wise", "creative", "frontier",
)
EXPECTED_FAST_LEVELS = ("micro", "procedural", "fidelity")


class _RecordingProvider:
    name = "llamacpp"
    model = "recording"

    def __init__(self):
        self.calls: list[dict] = []

    def chat(self, _system, _user, **kwargs):
        self.calls.append(dict(kwargs))
        return object()


def _configured_tiers() -> dict:
    return {
        "fast": {
            "provider": "stub", "model": "fast-model",
            "level": {
                "fidelity": {"temperature": 0.17},
            },
        },
        "middle": {
            "provider": "stub", "model": "middle-model",
        },
        "wise": {
            "provider": "stub", "model": "wise-model",
            "temperature": 0.91, "think": True, "reasoning_budget": 777,
        },
        "frontier": {"provider": "stub", "model": "frontier-model"},
    }


def test_tier_vocabulary_and_workload_registry_are_closed_and_complete():
    from llm_router import FAST_LEVEL_ORDER, TIER_ORDER
    from llm_workloads import WORKLOADS, contracts_by_tier, tier_for

    assert TIER_ORDER == EXPECTED_TIERS
    assert FAST_LEVEL_ORDER == EXPECTED_FAST_LEVELS
    assert set(contracts_by_tier()) == set(EXPECTED_TIERS)
    assert WORKLOADS
    assert all(contract.tier in TIER_ORDER for contract in WORKLOADS.values())
    with pytest.raises(ValueError, match="unknown LLM workload"):
        tier_for("unregistered.workload")


def test_fast_levels_middle_and_creative_keep_distinct_policies(monkeypatch):
    import llm_router

    made: list[tuple[dict, _RecordingProvider]] = []

    def _make(spec):
        provider = _RecordingProvider()
        made.append((dict(spec), provider))
        return provider

    monkeypatch.setattr(llm_router, "make_provider_from_spec", _make)
    router = llm_router.LLMRouter(tiers_override=_configured_tiers())

    assert router.is_aliased("creative")
    assert router.tiers["creative"]["model"] == "wise-model"
    assert not ({"temperature", "think", "reasoning_budget"}
                & set(router.tiers["creative"]))

    router.provider("fast", level="micro").chat("s", "u")
    router.provider("fast", level="fidelity").chat("s", "u")
    router.provider("middle").chat("s", "u")
    router.provider("creative").chat("s", "u")

    assert made[0][0] == {"provider": "stub", "model": "fast-model"}
    assert made[1][0] == {"provider": "stub", "model": "fast-model"}
    assert made[2][0] == {"provider": "stub", "model": "middle-model"}
    assert made[3][0] == {"provider": "stub", "model": "wise-model"}
    assert made[0][1].calls == [{"temperature": 0.0, "think": False}]
    assert made[1][1].calls == [{"temperature": 0.17, "think": False}]
    assert made[2][1].calls == [{"temperature": 0.0, "think": False}]
    assert made[3][1].calls == [{"temperature": 0.35, "think": False}]


@pytest.mark.parametrize("frontier", [None, {"provider": "none"}])
def test_unconfigured_frontier_never_degrades_to_a_local_tier(frontier):
    import llm_router

    tiers = {
        "fast": {"provider": "stub", "model": "fast-model"},
        "wise": {"provider": "stub", "model": "wise-model"},
    }
    if frontier is not None:
        tiers["frontier"] = frontier
    router = llm_router.LLMRouter(tiers_override=tiers)

    assert "frontier" not in router.tiers
    with pytest.raises(llm_router.TierConfigError, match="non configurato"):
        router.provider("frontier")


def test_managed_installer_emits_all_roles_without_call_site_policy():
    from install import llm_manager
    from llm_router import DEFAULT_TIERS

    plan = llm_manager.Plan(
        backend="cpu", model_key="test", model_label="Test",
        endpoint="http://127.0.0.1:9999",
    )
    parsed = tomllib.loads(
        llm_manager._render_tiers_toml(plan, Path("model.gguf")))

    assert tuple(parsed) == EXPECTED_TIERS
    assert tuple(parsed)[:-1] == llm_manager.LOCAL_TIER_NAMES
    for tier in llm_manager.LOCAL_TIER_NAMES:
        assert parsed[tier]["provider"] == "llamacpp"
        assert not ({"temperature", "think", "reasoning_budget"}
                    & set(parsed[tier]))
    assert parsed["frontier"]["model"] == DEFAULT_TIERS["frontier"]["model"]


def test_synt_prompt_metadata_uses_the_same_workload_contracts():
    from llm_workloads import tier_for

    root = Path(__file__).resolve().parents[3] / "runtime" / "prompts"
    expected = {
        "synt_naming": tier_for("synt.procedural"),
        "synt_signature": tier_for("synt.procedural"),
        "synt_tests": tier_for("synt.procedural"),
        "synt_description": tier_for("synt.description"),
        "synt_code": tier_for("synt.multistage"),
        "synt_generate": tier_for("synt.generate"),
        "synt_birth_tests": tier_for("synt.birth_tests"),
    }
    for lang in ("it", "en"):
        for role, wanted_tier in expected.items():
            source = (root / lang / f"{role}.j2").read_text(encoding="utf-8")
            match = re.search(r"^tier:\s*(\S+)", source, re.MULTILINE)
            assert match is not None, f"{lang}/{role}.j2"
            assert match.group(1) == wanted_tier, f"{lang}/{role}.j2"
