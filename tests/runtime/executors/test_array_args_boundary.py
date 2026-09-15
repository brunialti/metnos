"""Schema-driven singleton arrays at the resolved invocation boundary."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from executor_helpers import normalize_array_args


SCHEMA = {"properties": {"urls": {"type": "array"}}}
URL = "https://example.org/article?a=1,2"


@pytest.mark.parametrize("value", [URL, "", "one,two", "one\ntwo"])
def test_scalar_string_is_preserved_whole_without_mutating_input(value):
    args = {"urls": value, "unknown": "keep"}
    out = normalize_array_args(args, SCHEMA)
    assert out == {"urls": [value], "unknown": "keep"}
    assert args["urls"] == value
    assert normalize_array_args(out, SCHEMA) is out


@pytest.mark.parametrize("value", [[URL], [], None, 1, True, {"url": URL}])
def test_existing_lists_and_non_string_values_remain_unchanged(value):
    args = {"urls": value}
    assert normalize_array_args(args, SCHEMA) is args


@pytest.mark.parametrize("declaration", [
    {"type": ["string", "array"]},
    {"anyOf": [{"type": "string"}, {"type": "array"}]},
    {"type": "string"}, {}, None,
])
def test_unions_and_untyped_properties_preserve_scalar_semantics(declaration):
    args = {"urls": URL}
    assert normalize_array_args(args, {"properties": {"urls": declaration}}) is args


@pytest.mark.parametrize("schema", [None, {}, {"properties": []}, "invalid"])
def test_absent_or_invalid_schema_is_noop(schema):
    args = {"urls": URL}
    assert normalize_array_args(args, schema) is args


@pytest.fixture
def invocation_boundary(monkeypatch):
    """Observe resolved arguments at the real source-context safety gate.

    The synthetic missing context stops execution before journal/subprocess
    work.  No sandbox, signature or authorization check is disabled.
    """
    from agent_runtime import _invoke_executor_impl
    import from_step_projection

    seen = []

    def source_gate(args, _schema, **_kwargs):
        seen.append(args)
        return ["fixture_context"]

    monkeypatch.setattr(from_step_projection, "required_source_context_fields", source_gate)
    consumer = SimpleNamespace(name="fixture_read", args_schema=SCHEMA)

    def invoke(args):
        return _invoke_executor_impl(consumer, args, actor="authenticated")

    return consumer, seen, invoke


def test_direct_invocation_normalizes_before_controls_without_expanding_authority(invocation_boundary):
    _, seen, invoke = invocation_boundary
    result = invoke({"urls": URL, "_actor": "untrusted"})
    assert result["error_class"] == "missing_source_context"
    assert seen == [{"urls": [URL], "_actor": "authenticated"}]


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("reference", ["${step1.entries.0.url}", "${step1.entries.*.url}"])
def test_piping_and_resume_normalize_resolved_values_once(invocation_boundary, resume, reference):
    from engine.executor import Executor
    from engine.types import Framework, StepRun, StepSpec

    consumer, seen, invoke = invocation_boundary
    producer_result = {"ok": True, "entries": [{"url": URL}]}
    producer_calls = []

    def dispatch(tool, args):
        if tool == "fixture_find":
            producer_calls.append(tool)
            return producer_result
        return invoke(args)

    seed = [StepRun(step_idx=1, tool="fixture_find", args={},
                    result=producer_result, ok=True, latency_ms=0)] if resume else []
    steps = [] if resume else [StepSpec(tool="fixture_find")]
    consumer_args = {"urls": reference}
    steps.append(StepSpec(tool=consumer.name, args=consumer_args))
    executor = Executor(invoke_executor=dispatch, catalog=[consumer], seed_steps=seed)
    executor.run(Framework(steps=steps), query="read selected item")

    assert len(seen) == 1
    assert seen[0]["urls"] == [URL]
    assert producer_calls == ([] if resume else ["fixture_find"])
    assert consumer_args == {"urls": reference}
