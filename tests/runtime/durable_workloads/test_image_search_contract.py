"""Compile the shipped image-search contract with a simulated catalog signer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from durable_workloads.direct_invocation import (
    DirectInvocationUnsupported,
    build_direct_candidate,
    direct_runtime_registration,
    is_intrinsically_long,
)
from executor_prerequisites import normalize_prerequisites
from engine.types import Framework, StepSpec
from lre_submission import submit_automatic_lre
from executor_metadata import execution_policy, intelligence_kind, transport_kind


@pytest.fixture(params=["find_images_indices", "find_persons_indices"])
def image_search_executor(request):
    root = Path(__file__).resolve().parents[3]
    manifest_path = root / "executors" / request.param / "manifest.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    # This isolates contract compilation from publication. A real signed
    # catalog and the normal executor boundary are exercised by release E2E.
    return SimpleNamespace(
        name=manifest["name"],
        version=manifest["version"],
        signed_by="fixture-authority",
        digest=manifest["code"]["digest"],
        lifecycle=manifest.get("lifecycle", "active"),
        dormant=False,
        transport=transport_kind(manifest),
        intelligence=intelligence_kind(manifest),
        timeout_s=manifest["timeout_s"],
        args_schema=manifest["args"],
        capabilities=manifest["capabilities"],
        placement=manifest["placement"],
        execution_policy_declared=isinstance(manifest.get("execution"), dict),
        execution_policy=execution_policy(manifest),
        prerequisites=normalize_prerequisites(manifest.get("prerequisites"), owner=manifest["name"]),
    )


@pytest.mark.parametrize("arguments", [
    {"name": "Roberto"},
    {"name": "guest", "time_window": "all"},
    {"reference_images": ["/fixture/reference.jpg"], "time_window": "2025"},
])
def test_existing_index_search_is_interactive_with_a_signed_build_prerequisite(
    image_search_executor, arguments,
):
    executor = image_search_executor
    assert not is_intrinsically_long(executor)
    assert executor.timeout_s == 300
    assert direct_runtime_registration([executor]) is None
    assert submit_automatic_lre(
        Framework(steps=[StepSpec(executor.name, arguments)]), catalog=[executor],
        owner_user_id="fixture-owner", turn_id="fixture-turn") is None
    assert executor.execution_policy["effect"] == "read_only"
    assert executor.placement == {"scope": "server", "device_ok": False}
    assert executor.execution_policy["parallelism_class"] == 0
    assert executor.prerequisites[0].on_error == "index_missing"
    assert executor.prerequisites[0].executor == "create_images_indices"


def test_default_window_stays_unbounded_at_the_common_boundary(
    image_search_executor, monkeypatch,
):
    import temporal_resolution

    def reject_model_call(*_args, **_kwargs):
        pytest.fail("The declared unbounded default must not invoke a model")

    monkeypatch.setattr(temporal_resolution, "_interpret", reject_model_call)
    executor = image_search_executor
    field = executor.args_schema["properties"]["time_window"]
    assert field["format"] == "time-window"
    assert field["default"] == "all"
    arguments = {"name": "Roberto", "time_window": field["default"]}
    assert temporal_resolution.resolve_temporal_args(
        executor.name, arguments, "", executor.args_schema,
    ) == arguments


def test_indexed_search_still_requires_an_explicit_effect(image_search_executor):
    executor = image_search_executor
    executor.execution_policy_declared = False

    assert direct_runtime_registration([executor]) is None
    with pytest.raises(DirectInvocationUnsupported, match="not admissible"):
        build_direct_candidate(
            executor, {"name": "Roberto"}, None,
        )
