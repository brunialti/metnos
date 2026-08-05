"""Regression guards for the tier-ownership boundary.

These consumers may choose a logical tier and operation limits, but must not
grow a second configuration channel for generation policy.  Such a channel
would make a Models-page change appear accepted while silently being ignored
by part of the runtime.
"""
from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]


def test_core_consumers_leave_generation_policy_to_their_tier():
    consumers = (
        "runtime/synth_request.py",
        "runtime/manifest_normalize.py",
        "runtime/skill_description_llm.py",
        "runtime/synt_stage6_verify.py",
        "runtime/intent_extractor.py",
    )
    policy_assignments = ("temperature=", "think=", "reasoning_budget=")
    for relative_path in consumers:
        source = (_ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(item in source for item in policy_assignments), relative_path


def test_legacy_environment_policy_channels_are_retired():
    synth = (_ROOT / "runtime/synth_request.py").read_text(encoding="utf-8")
    normalize = (_ROOT / "runtime/manifest_normalize.py").read_text(encoding="utf-8")
    install_unit = (
        _ROOT / "install/units/metnos-http.service.tmpl"
    ).read_text(encoding="utf-8")
    release_gate = (
        _ROOT / "tests/tools/release_gate.py"
    ).read_text(encoding="utf-8")
    assert "METNOS_SYNT_BUDGET" not in synth
    assert "METNOS_NORMALIZE_THINK" not in normalize
    assert "METNOS_REASONING_BUDGET" not in install_unit
    assert "METNOS_REASONING_BUDGET" not in release_gate
    assert "METNOS_PROPOSER_MAX_TOKENS_THINK" not in (
        _ROOT / "runtime/engine/proposer.py"
    ).read_text(encoding="utf-8")


def test_no_production_consumer_passes_tier_owned_policy_keywords():
    """Guard the whole production tree, not a hand-picked caller sample."""

    allowed_gateways = {
        "runtime/llm_helpers.py",
        "runtime/llm_provider.py",
        "runtime/llm_router.py",
    }
    forbidden = {"temperature", "think", "reasoning_budget"}
    violations: list[str] = []
    for base in (_ROOT / "runtime", _ROOT / "executors"):
        for path in base.rglob("*.py"):
            relative = path.relative_to(_ROOT).as_posix()
            if relative in allowed_gateways or "testing" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                found = sorted(
                    keyword.arg for keyword in node.keywords
                    if keyword.arg in forbidden
                )
                if found:
                    violations.append(
                        f"{relative}:{node.lineno}: {', '.join(found)}")
    assert violations == []


def test_every_production_gateway_call_selects_a_tier_explicitly():
    """The helper default is an API fallback, never a workload decision."""

    violations: list[str] = []
    for base in (_ROOT / "runtime", _ROOT / "executors"):
        for path in base.rglob("*.py"):
            relative = path.relative_to(_ROOT).as_posix()
            if relative == "runtime/llm_helpers.py" or "testing" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "call_llm":
                    continue
                if not any(keyword.arg == "tier" for keyword in node.keywords):
                    violations.append(f"{relative}:{node.lineno}")
    assert violations == []


def test_shipped_llm_diagnostics_obey_the_same_policy_boundary():
    """Benchmarks and simulators must remain executable under strict tiers."""

    diagnostics = (
        "tests/benchmarks/grammar_args_ab.py",
        "tests/benchmarks/routing_subset_bench.py",
        "tests/benchmarks/compound_dryrun.py",
        "tests/simulator/cmp_graph_vs_proposer.py",
        "tests/e2e/scenarios/test_budget_pipeline_real.py",
    )
    forbidden = {"temperature", "think", "reasoning_budget"}
    violations: list[str] = []
    for relative in diagnostics:
        path = _ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            found = sorted(
                keyword.arg for keyword in node.keywords
                if keyword.arg in forbidden
            )
            if found:
                violations.append(
                    f"{relative}:{node.lineno}: {', '.join(found)}")
    assert violations == []
