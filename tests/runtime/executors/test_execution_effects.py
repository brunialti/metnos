from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from execution_effects import resolve_execution_effect, validate_effects
from executor_metadata import execution_policy
from engine.executor import Executor
from engine.types import Framework, StepSpec
from pipeline_effects import committed_mutations, pipeline_effect_counts


def _contract():
    return {"args": {"type": "object", "properties": {
        "phase": {"type": "string", "enum": ["inspect", "commit"]}}},
        "execution": {"effect": "reversible", "parallelism_class": 0,
                      "effects": [{"argument": "phase", "equals": "inspect",
                                   "effect": "read_only"}]}}


def _executor(manifest=None):
    manifest = manifest or _contract()
    return SimpleNamespace(
        name="get_probe", args_schema=manifest["args"],
        execution_policy_declared=True,
        execution_policy=execution_policy(manifest))


@pytest.mark.parametrize("args,expected", [
    ({"phase": "inspect"}, "read_only"),
    ({"phase": "commit"}, "reversible"),
    ({}, "reversible"), ({"phase": "INSPECT"}, "reversible")])
def test_effect_uses_exact_final_args(args, expected):
    assert resolve_execution_effect(_executor(), args) == expected


@pytest.mark.parametrize("change", [
    lambda manifest: manifest["execution"].update(effect="read_only"),
    lambda manifest: manifest["execution"].update(parallelism_class=1),
    lambda manifest: manifest["execution"].update(effects=[]),
    lambda manifest: manifest["execution"]["effects"].append(
        deepcopy(manifest["execution"]["effects"][0])),
    lambda manifest: manifest["execution"]["effects"][0].update(equals="unknown"),
    lambda manifest: manifest["execution"]["effects"][0].update(effect="mutating"),
])
def test_invalid_contract_cannot_grant_read_only(change):
    manifest = _contract()
    change(manifest)
    assert validate_effects(manifest)
    assert resolve_execution_effect(_executor(manifest), {"phase": "inspect"}) == "unknown"


@pytest.mark.parametrize("phase,mutating", [("inspect", False), ("commit", True)])
def test_engine_persists_signed_effect_not_executor_claim(phase, mutating):
    engine = Executor(catalog=[_executor()], invoke_executor=lambda _tool, _args: {
        "ok": True, "results": [{"id": 1}], "execution_effect": "read_only"})
    run = engine.run(Framework(steps=[StepSpec(
        tool="get_probe", args={"phase": phase})]))
    persisted = asdict(run.steps[0])
    assert persisted["execution_effect"] == (
        "reversible" if mutating else "read_only")
    assert pipeline_effect_counts([persisted])["mutating_attempted"] is mutating
    assert bool(committed_mutations([persisted])) is mutating
