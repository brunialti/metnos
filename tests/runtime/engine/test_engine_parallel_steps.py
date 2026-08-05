from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from engine.executor import Executor
from engine.types import Framework, StepSpec


def _catalog_executor(name: str, *, level: int = 1):
    return SimpleNamespace(
        name=name,
        args_schema={"type": "object", "properties": {}},
        execution_policy_declared=True,
        standard_state="declared",
        transport="local-subprocess",
        execution_policy={
            "effect": "read_only",
            "parallelism_class": level,
            "resource_class": "network_io",
            "concurrency_key": "none",
            "equivalence_gate": "verified",
        },
    )


def test_parallel_wave_overlaps_but_commits_in_framework_order(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [
        _catalog_executor("read_messages", level=2),
        _catalog_executor("read_events", level=1),
    ]
    pool = ThreadPoolExecutor(max_workers=2)
    lock = threading.Lock()
    release = threading.Event()
    both_entered = threading.Event()
    active = 0
    maximum = 0
    submitted = []
    invoked = []

    def work(tool, args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_entered.set()
        release.wait(timeout=2)
        # Finish the second framework step first: commit must still be stable.
        if tool == "read_messages":
            time.sleep(0.02)
        with lock:
            active -= 1
        return {"ok": True, "entries": [{"source": tool, **args}]}

    def submit(tool, args):
        submitted.append((tool, dict(args)))
        future = pool.submit(work, tool, dict(args))
        if len(submitted) == 2:
            assert both_entered.wait(timeout=1)
            release.set()
        return future

    framework = Framework(steps=[
        StepSpec("read_messages", {
            "account": "all", "time_window": "last-60d"}),
        StepSpec("read_events", {"time_window": "last-60d"}),
    ], final_message="done")
    run = Executor(
        invoke_executor=lambda tool, args: invoked.append(tool) or {
            "ok": True, "entries": []},
        submit_executor=submit,
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(framework, query="ultimi 60 giorni")
    pool.shutdown()

    assert maximum == 2
    assert invoked == []
    assert [tool for tool, _args in submitted] == [
        "read_messages", "read_events"]
    assert [step.tool for step in run.steps] == [
        "read_messages", "read_events"]
    assert [step.result["entries"][0]["source"] for step in run.steps] == [
        "read_messages", "read_events"]
    assert not run.aborted_reason


def test_parallel_wave_discards_uncommitted_peer_after_first_error(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    pool = ThreadPoolExecutor(max_workers=2)
    executed = []

    def submit(tool, _args):
        def work():
            executed.append(tool)
            return ({"ok": False, "error": "boom"}
                    if tool == "read_a" else {"ok": True, "entries": [1]})
        return pool.submit(work)

    run = Executor(
        invoke_executor=lambda _tool, _args: {"ok": True},
        submit_executor=submit,
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(Framework(steps=[StepSpec("read_a"), StepSpec("read_b")]))
    pool.shutdown()

    assert "read_a" in executed
    assert set(executed) <= {"read_a", "read_b"}
    assert [step.tool for step in run.steps] == ["read_a"]
    assert run.aborted_reason == "step_1_error"


def test_parallel_feature_off_preserves_serial_path(monkeypatch):
    monkeypatch.delenv("METNOS_ENGINE_PARALLEL_STEPS", raising=False)
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    invoked = []
    submitted = []
    run = Executor(
        invoke_executor=lambda tool, _args: invoked.append(tool) or {
            "ok": True, "entries": [{"tool": tool}]},
        submit_executor=lambda tool, _args: submitted.append(tool),
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(Framework(steps=[StepSpec("read_a"), StepSpec("read_b")]))

    assert submitted == []
    assert invoked == ["read_a", "read_b"]
    assert [step.tool for step in run.steps] == invoked


def test_dependency_and_class_zero_are_hard_serial_barriers(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [
        _catalog_executor("read_a"),
        _catalog_executor("read_zero", level=0),
        _catalog_executor("read_b"),
        _catalog_executor("read_c"),
    ]
    invoked = []
    submitted = []

    def invoke(tool, _args):
        invoked.append(tool)
        return {"ok": True, "entries": [{"tool": tool}]}

    run = Executor(
        invoke_executor=invoke,
        submit_executor=lambda tool, _args: submitted.append(tool),
        can_parallelize=lambda tool: tool != "read_zero",
        catalog=catalog,
    ).run(Framework(steps=[
        StepSpec("read_a"),
        StepSpec("read_zero"),
        StepSpec("read_b"),
        StepSpec("read_c", {"from_step": 3}),
    ]))

    assert submitted == []
    assert invoked == ["read_a", "read_zero", "read_b", "read_c"]
    assert [step.tool for step in run.steps] == invoked


def test_preflight_form_degrades_entire_wave_to_serial(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    invoked = []
    submitted = []

    import args_resolver
    monkeypatch.setattr(
        args_resolver, "scope_form_request",
        lambda tool, *_args, **_kwargs: {"decision": "needs_inputs"}
        if tool == "read_b" else None)

    run = Executor(
        invoke_executor=lambda tool, _args: invoked.append(tool) or {
            "ok": True, "entries": []},
        submit_executor=lambda tool, _args: submitted.append(tool),
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(Framework(steps=[StepSpec("read_a"), StepSpec("read_b")]))

    assert submitted == []
    assert invoked == ["read_a"]
    assert run.steps[-1].tool == "read_b"
    assert run.steps[-1].result["decision"] == "needs_inputs"


def test_scheduler_denial_degrades_to_serial(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    invoked = []
    submitted = []
    run = Executor(
        invoke_executor=lambda tool, _args: invoked.append(tool) or {
            "ok": True, "entries": []},
        submit_executor=lambda tool, _args: submitted.append(tool),
        can_parallelize=lambda tool: tool == "read_a",
        catalog=catalog,
    ).run(Framework(steps=[StepSpec("read_a"), StepSpec("read_b")]))

    assert submitted == []
    assert invoked == ["read_a", "read_b"]
    assert not run.aborted_reason


def test_parallel_future_exception_is_normalized_and_stops_commit(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    pool = ThreadPoolExecutor(max_workers=2)

    def submit(tool, _args):
        def work():
            if tool == "read_a":
                raise RuntimeError("network down")
            return {"ok": True, "entries": [1]}
        return pool.submit(work)

    run = Executor(
        invoke_executor=lambda _tool, _args: {"ok": True},
        submit_executor=submit,
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(Framework(steps=[StepSpec("read_a"), StepSpec("read_b")]))
    pool.shutdown()

    assert len(run.steps) == 1
    assert run.steps[0].result["error_class"] == "exception"
    assert "network down" in run.steps[0].result["error"]
    assert run.aborted_reason == "step_1_error"


def test_parallel_prepare_drift_fails_closed(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    pool = ThreadPoolExecutor(max_workers=2)
    executor = Executor(
        invoke_executor=lambda _tool, _args: {"ok": True},
        submit_executor=lambda tool, args: pool.submit(
            lambda: {"ok": True, "entries": [{"tool": tool, **args}]}),
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    )
    original = executor._prepare_static_read_args

    def drift(step, **kwargs):
        args = original(step, **kwargs)
        if step.tool == "read_b":
            args["unexpected"] = True
        return args

    monkeypatch.setattr(executor, "_prepare_static_read_args", drift)
    run = executor.run(Framework(steps=[
        StepSpec("read_a"), StepSpec("read_b")]))
    pool.shutdown()

    assert [step.tool for step in run.steps] == ["read_a", "read_b"]
    assert run.steps[-1].result["error_class"] == "parallel_prepare_mismatch"
    assert run.aborted_reason == "step_2_error"


def test_runtime_or_step_placeholders_are_serial_barriers(monkeypatch):
    monkeypatch.setenv("METNOS_ENGINE_PARALLEL_STEPS", "1")
    catalog = [_catalog_executor("read_a"), _catalog_executor("read_b")]
    invoked = []
    submitted = []
    run = Executor(
        invoke_executor=lambda tool, _args: invoked.append(tool) or {
            "ok": True, "entries": []},
        submit_executor=lambda tool, _args: submitted.append(tool),
        can_parallelize=lambda _tool: True,
        catalog=catalog,
    ).run(Framework(steps=[
        StepSpec("read_a", {"owner": "${RUNTIME:actor}"}),
        StepSpec("read_b", {"query": "${step1.summary}"}),
    ]), runtime_ctx={"actor": "Roberto"})

    assert submitted == []
    assert invoked == ["read_a", "read_b"]
    assert not run.aborted_reason
