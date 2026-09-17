"""Model startup belongs to the host and preserves the admitted binding."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from durable_workloads.execution import DurableExecutionBridge
from durable_workloads.models import ExecutionContext
from durable_workloads.resource_readiness import (
    ModelResourceChanged, ModelResourceUnavailable, ensure_model_resource,
)
from durable_workloads.schema import MAX_SNAPSHOT_JSON_BYTES, digest_json
from durable_workloads.storage import DurableWorkloadStore
from durable_workloads.worker import WorkerRunStatus
from test_execution_bridge import (
    _Resolver, _admit, _contract, _map_observation, _pipeline, _schemas,
    _worker, source_resolution,
)


@pytest.fixture
def model_fixture(monkeypatch):
    import vlm_client
    import virt

    binding = {
        "family": "vlm", "role": "default", "provider": "llamacpp",
        "model": "fixture-vision", "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        "timeout_s": 60, "max_edge": 1024, "max_tokens": 512,
        "max_input_tokens": 5308416, "max_calls_per_attempt": 32,
        "usage_kind": "vision", "usage_tier": "vlm:default",
    }
    contract = SimpleNamespace(
        model_kind="vision", model_max_output_tokens=512, model_max_calls=32,
        model_binding_digest=digest_json("durable-executor-model-binding", binding,
                                         max_bytes=MAX_SNAPSHOT_JSON_BYTES),
    )
    executor = SimpleNamespace(
        capabilities=[{"name": "llm:local", "hint": ["bounded_vision"],
                       "when": {"arg": "phase", "values": ["analyze"]}}],
        args_schema={"type": "object", "properties": {"phase": {"type": "string"}}},
    )
    context = ExecutionContext("owner", "workload", "revision", "stage", "unit", "attempt",
                               "normal", (("vlm", 1),), "2099-01-01T00:00:00Z")
    calls = []
    monkeypatch.setattr(vlm_client, "reload_configuration", lambda: None)
    monkeypatch.setattr(vlm_client, "model_binding_facts", lambda **_kwargs: dict(binding))
    monkeypatch.setattr(virt, "ensure_vlm_up", lambda role, **kwargs: calls.append((role, kwargs)) or True)
    return executor, contract, context, binding, calls


def test_only_the_effective_local_vision_capability_can_start_a_model(model_fixture):
    executor, contract, context, _binding, calls = model_fixture
    ensure_model_resource(executor, {"phase": "discover"}, contract, context, None, deadline_at=10)
    ensure_model_resource(executor, {"phase": "analyze"}, contract, context, "remote-device", deadline_at=10)
    assert not calls
    ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)
    assert calls == [("default", {"deadline_at": 10})]


@pytest.mark.parametrize("field,value", [
    ("model", "other-model"), ("endpoint", "http://127.0.0.1:9000/v1/chat/completions"),
    ("max_edge", 2048), ("timeout_s", 120),
])
def test_binding_change_cannot_launch_another_model(model_fixture, field, value):
    executor, contract, context, binding, calls = model_fixture
    binding[field] = value
    with pytest.raises(ModelResourceChanged):
        ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)
    assert not calls


def test_binding_is_rechecked_after_startup(model_fixture, monkeypatch):
    import virt

    executor, contract, context, binding, _calls = model_fixture

    def start(*_args, **_kwargs):
        binding["model"] = "changed-during-start"
        return True

    monkeypatch.setattr(virt, "ensure_vlm_up", start)
    with pytest.raises(ModelResourceChanged):
        ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)


def test_unclaimed_model_resource_fails_before_start(model_fixture):
    from dataclasses import replace

    executor, contract, context, _binding, calls = model_fixture
    with pytest.raises(ModelResourceChanged):
        ensure_model_resource(executor, {"phase": "analyze"}, contract,
                              replace(context, resource_claims=()), None, deadline_at=10)
    assert not calls


def test_startup_failure_is_an_explicit_unavailable_dependency(model_fixture, monkeypatch):
    import virt

    executor, contract, context, _binding, _calls = model_fixture
    monkeypatch.setattr(virt, "ensure_vlm_up", lambda *_args, **_kwargs: False)
    with pytest.raises(ModelResourceUnavailable):
        ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)


def test_lre_uses_the_resource_contract_without_provider_or_model_logic(model_fixture, monkeypatch):
    """A different model lifecycle plugs in without adding a branch to LRE."""
    from dataclasses import replace
    from virt.resources import ModelResource

    executor, contract, context, _binding, vision_calls = model_fixture
    facts = {"family": "text", "model": "fixture-text", "endpoint": "fixture-service"}
    contract.model_kind = "text"
    contract.model_binding_digest = digest_json(
        "durable-executor-model-binding", facts, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
    context = replace(context, resource_claims=(("llm", 1),))
    calls = []

    def resolve(kind, **limits):
        assert kind == "text"
        assert limits == {"max_output_tokens": 512, "max_calls": 32}
        return ModelResource("llm", lambda: dict(facts),
                             lambda binding, **kwargs: calls.append((binding, kwargs)) or True)

    monkeypatch.setattr("virt.resources.resolve_model_resource", resolve)
    ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)
    assert calls == [(facts, {"deadline_at": 10})]
    assert not vision_calls
    with pytest.raises(ModelResourceChanged):
        ensure_model_resource(executor, {"phase": "analyze"}, contract,
                              replace(context, resource_claims=(("vlm", 1),)), None, deadline_at=10)
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint", ["https://example.invalid/v1/chat/completions",
                                    "http://user:password@127.0.0.1:8081/v1/chat/completions"])
def test_virt_does_not_launch_for_an_unmanaged_endpoint(model_fixture, endpoint):
    executor, contract, context, binding, calls = model_fixture
    binding["endpoint"] = endpoint
    contract.model_binding_digest = digest_json(
        "durable-executor-model-binding", binding, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
    with pytest.raises(ModelResourceUnavailable):
        ensure_model_resource(executor, {"phase": "analyze"}, contract, context, None, deadline_at=10)
    assert not calls


@pytest.mark.parametrize("failed", [False, True])
def test_bridge_prepares_after_recording_fence_and_never_invokes_on_failure(tmp_path, failed):
    resolver = _Resolver()
    resolver.map = _contract(
        "executor", "read_files_ocr", "metnos.test-map/1", inputs=("paths",),
        input_types=(("paths", "array"),), model=True, model_name="fixture-vision",
        model_kind="vision", model_tier="vlm:default",
    )
    candidate = _pipeline(with_reduce=False)
    candidate["stages"][1]["resources"]["vlm"] = 1
    candidate["stages"][1]["invalidation_keys"] += ["model_binding.digest", "prompt.digest"]
    calls = []
    with DurableWorkloadStore.open(tmp_path / "state.sqlite3") as store:
        _admit(store, resolver, candidate=candidate)

        def prepare(executor, args, contract, context, device_id, *, deadline_at):
            row = store._connection.execute(
                "SELECT state,executor_snapshot_json FROM attempts WHERE id=?", (context.attempt_id,),
            ).fetchone()
            assert row["state"] == "running"
            assert json.loads(row["executor_snapshot_json"])["mode"] == "verified"
            assert dict(context.resource_claims)["vlm"] == 1
            assert time.monotonic() < deadline_at
            calls.append("ready")
            if failed:
                raise ModelResourceUnavailable("fixture startup failed")

        def invoke(*_args):
            from llm_telemetry import BoundedTransportUsageSink, TRANSPORT_USAGE_KEY
            calls.append("invoke")
            result = _map_observation()
            sink = BoundedTransportUsageSink()
            result[TRANSPORT_USAGE_KEY] = sink.export()
            return result

        bridge = DurableExecutionBridge(
            store, runners=resolver, output_schemas=_schemas(),
            source_resolver=lambda item, _context: source_resolution(item, "/authorized/source.png"),
            executor_loader=lambda _name: SimpleNamespace(name="read_files_ocr"),
            executor_invoker=invoke, resource_readiness=prepare,
        )
        outcome = bridge.run_once(_worker(store, resolver))
        assert calls == (["ready"] if failed else ["ready", "invoke"])
        assert outcome.status is (WorkerRunStatus.FAILED if failed else WorkerRunStatus.COMMITTED)
        row = store._connection.execute("SELECT metrics_json,structured_error_json FROM attempts").fetchone()
        metrics = json.loads(row["metrics_json"])
        assert metrics["llm_usage"]["records"] == []
        assert metrics["llm_usage"]["zero_calls_verified"]
        assert not metrics["llm_usage"]["usage_missing"]
        if failed:
            assert json.loads(row["structured_error_json"])["code"] == "execution.model_resource_unavailable"
            assert not store._connection.execute("SELECT 1 FROM results").fetchall()
