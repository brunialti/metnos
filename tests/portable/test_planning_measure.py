"""A0 observes the real dispatcher without acquiring any execution port.

Candidate manifests supply the actual argument/policy forms. Admission and
inline execution are controlled ports: no persistent admission or tool runs.
"""
from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import os
import subprocess
import sys
from threading import Event, Thread
import tomllib
from types import SimpleNamespace

import pytest

from engine import dispatch
from engine.types import Framework, Intent, RunResult, StepSpec
from loader import Executor
import planning_measure as measure


ROOT = Path(__file__).resolve().parents[2]
QUERY = "Trova i file duplicati nella cartella Immagini/zz_prova_metnos_a"
OWNER = "7ef0c9a07f64455b"


@pytest.fixture
def candidate_catalog():
    """Read authoring data only; this fixture does not certify signatures."""
    catalog = []
    for name in ("get_now", "find_files_hash", "list_dirs"):
        path = ROOT / "executors" / name / "manifest.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        catalog.append(Executor(
            name=name, version=data["version"],
            description=data["description"]["it"], affinity=data["affinity"],
            args_schema=data["args"], capabilities=data.get("capabilities", []),
            tests=[], code_path=None, manifest_path=path, signed_by="",
            timeout_s=data.get("timeout_s", 30),
            execution_policy=data.get("execution", {}),
            execution_policy_declared="execution" in data,
            filesystem=data.get("filesystem", {}),
            filesystem_search=data.get("filesystem_search", {}),
            planning_companions=data.get("planning", {}).get("companions", []),
        ))
    assert next(e for e in catalog if e.name == "find_files_hash").timeout_s == 600
    return catalog


def scenario(case):
    if case == "short":
        return "Che ora è?", Intent(verb="get", object="now", actions=[{"verb": "get", "object": "now"}]), Framework([
            StepSpec("get_now", {}), StepSpec("final_answer", {})])
    root = "Immagini/zz_prova_metnos_a"
    if case == "refused":
        # Real conversation placeholder: a durable job cannot read this value.
        root = "${RUNTIME:cwd}"
    return QUERY, Intent(verb="find", object="files", actions=[{"verb": "find", "object": "files"}]), Framework([
        StepSpec("find_files_hash", {"base_path": root, "max_results": 0}),
        StepSpec("final_answer", {})])


def exercise_dispatch(monkeypatch, module, catalog, case, *, fault=None):
    """Shared before/after harness; guards, routing and shape checks stay real."""
    from engine import proposer, recovery, terminator
    from durable_workloads.direct_invocation import is_intrinsically_long
    import lre_submission

    for key, value in {"METNOS_ENGINE": "v3", "METNOS_FASTPATH": "0",
                       "METNOS_AUTOPATH": "0", "METNOS_VALIDATOR": "0",
                       "METNOS_GUARD_FIRE_COUNT": "0"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module._ap, "excluded_framework_hashes", lambda _i: set())
    query, intent, framework = scenario(case)
    calls = {"proposals": [], "admission": [], "inline": [], "tools": []}

    def propose(**kwargs):
        calls["proposals"].append({
            "query": kwargs["query"], "intent": asdict(kwargs["intent"]),
            "pool": list(kwargs["pool"]),
            "excluded": sorted(kwargs["excluded_hashes"]),
        })
        return deepcopy(framework)

    def admit(plan):
        calls["admission"].append(plan.to_dict())
        if fault:
            pytest.fail("observer failure reached durable admission")
        by_name = {item.name: item for item in catalog}
        long = [(i, step, by_name[step.tool])
                for i, step in enumerate(plan.steps)
                if step.tool in by_name and is_intrinsically_long(by_name[step.tool])]
        if not long:
            return None
        _accepted, refusal = lre_submission._admissible_shape(
            plan.steps, long, by_name, fillers=bool(plan.fillers), rest_follows=True)
        return refusal or {"ok": True, "decision": "accepted",
                           "final_message_hint": "Lavoro accettato"}

    def invoke(*args, **kwargs):
        calls["tools"].append((args, kwargs))
        pytest.fail("a portable planning proof cannot invoke tools")

    class ControlledExecutor:
        def __init__(self, **kwargs):
            assert kwargs["invoke_executor"] is invoke

        def run(self, plan, **kwargs):
            calls["inline"].append(plan.to_dict())
            if fault:
                pytest.fail("observer failure reached inline execution")
            return RunResult(final_text="Risultato controllato", final_kind="answer",
                             framework_hash=module.compute_framework_hash(plan))

    monkeypatch.setattr(proposer, "get_proposer", lambda: SimpleNamespace(propose=propose))
    monkeypatch.setattr(recovery, "get_recovery", lambda: None)
    monkeypatch.setattr(terminator, "get_terminator", lambda: None)
    monkeypatch.setattr(module, "Executor", ControlledExecutor)
    result = module.run_turn(
        query=query, intent=intent, catalog=catalog, invoke_executor_cb=invoke,
        admit_long_work_cb=admit, runtime_ctx={"channel": "http", "user_query_raw": query})
    outcome = asdict(result)
    outcome.pop("elapsed_ms")
    return outcome, calls


@pytest.mark.parametrize("case", ["long", "short", "refused"])
@pytest.mark.parametrize("fault", ["import", "copy", "redaction", "lock"])
def test_observer_fault_is_inert_when_measurement_is_off(
        monkeypatch, candidate_catalog, case, fault):
    baseline = exercise_dispatch(monkeypatch, dispatch, candidate_catalog, case)
    with monkeypatch.context() as broken:
        def fail(*_args, **_kwargs):
            raise OSError("observer sentinel must remain untouched")
        if fault == "import":
            original = builtins.__import__
            def guarded_import(name, *args, **kwargs):
                if name == "planning_measure":
                    fail()
                return original(name, *args, **kwargs)
            broken.setattr(builtins, "__import__", guarded_import)
        elif fault == "copy":
            broken.setattr(measure, "deepcopy", fail)
        elif fault == "redaction":
            broken.setattr(measure, "_redact", fail)
        else:
            broken.setattr(measure, "_LOCK", SimpleNamespace(acquire=fail))
        observed = exercise_dispatch(broken, dispatch, candidate_catalog, case)
    assert observed == baseline
    outcome, calls = observed
    assert len(calls["proposals"]) == 1 and calls["tools"] == []
    assert outcome["match_source"] == ("engine" if case == "short" else "lre")
    if case == "refused":
        assert outcome["durable_admission"]["error_code"] == "ERR_LRE_PLAN_LONG_NEEDS_VALUE"
    assert bool(calls["inline"]) is (case == "short")


@pytest.fixture
def engine_boundary(monkeypatch, candidate_catalog):
    """Replace model/intent extraction only, then enter the real dispatcher."""
    import agent_runtime
    seen = {}
    def engine(query, catalog, **kwargs):
        assert catalog is candidate_catalog
        assert query == scenario(seen.get("case", "long"))[0]
        assert kwargs["owner_user_id"] == OWNER
        assert kwargs["resume_steps"] is None and kwargs["progress"] is None
        assert kwargs["credential_meta"] is None and kwargs["channel"] == "http"
        seen["kwargs"] = kwargs
        outcome, calls = exercise_dispatch(
            monkeypatch, dispatch, catalog, seen.get("case", "long"),
            fault=seen.get("fault"))
        seen["outcome"], seen["calls"] = outcome, calls
        return {"steps": [], "durable_admission": outcome["durable_admission"],
                "final_kind": outcome["final_kind"], "match_source": outcome["match_source"],
                "error_class": outcome["error_class"]}
    monkeypatch.setattr(agent_runtime, "_run_engine", engine)
    return seen


def assert_reset():
    assert measure.CURRENT.get() is None
    assert dispatch._PLANNING_MEASURE.get() is None
    assert measure._LOCK.acquire(blocking=False)
    measure._LOCK.release()


@pytest.mark.parametrize("case,shape", [
    ("long", "ammessa"), ("short", "non applicabile: nessun passo lungo"),
    ("refused", "ERR_LRE_PLAN_LONG_NEEDS_VALUE"),
])
def test_measure_stops_before_all_effect_ports(
        candidate_catalog, engine_boundary, case, shape):
    engine_boundary.update(case=case, fault="no execution")
    report = measure.run(query=scenario(case)[0], owner_user_id=OWNER, catalog=candidate_catalog)
    assert report["shape"] == shape
    assert report["origin"] == "L3" and report["memory"] == "off"
    assert report["admission"].startswith("non verificata")
    assert report["proposed_plan"]["steps"][-1]["tool"] == "final_answer"
    assert_reset()


@pytest.mark.parametrize("fault", ["copy", "redaction", "observe", "best_effort", "finish_return", "finish_raise", "lost_context", "import"])
@pytest.mark.parametrize("case", ["long", "short"])
def test_observer_failure_cannot_reach_admission_or_inline(
        monkeypatch, candidate_catalog, engine_boundary, fault, case):
    engine_boundary.update(case=case, fault=fault)
    def fail(*_args, **_kwargs):
        raise OSError("private sentinel must not escape")
    if fault == "copy":
        monkeypatch.setattr(measure, "deepcopy", fail)
    elif fault == "redaction":
        monkeypatch.setattr(measure, "_redact", fail)
    elif fault in {"observe", "best_effort"}:
        original = measure.observe
        def observe(name, value):
            if name == ("normalize_terminal" if fault == "best_effort" else "pre_execution"):
                fail()
            original(name, value)
        monkeypatch.setattr(measure, "observe", observe)
    elif fault == "import":
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == "agent_runtime":
                fail()
            return original_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", guarded_import)
    elif fault == "finish_raise":
        monkeypatch.setattr(measure, "finish", fail)
    elif fault == "finish_return":
        monkeypatch.setattr(measure, "finish", lambda *_args: None)
    else:
        original = measure.observe
        def observe(name, value):
            original(name, value)
            if name == "pre_execution":
                measure.CURRENT.set(None)
        monkeypatch.setattr(measure, "observe", observe)
    with pytest.raises(measure.MeasureError, match="^measure_(observer_failed|observation_failed|shape_or_redaction_failed|engine_failed)$"):
        measure.run(query=scenario(case)[0], owner_user_id=OWNER, catalog=candidate_catalog)
    assert_reset()


def test_lock_error_never_starts_engine(monkeypatch, candidate_catalog):
    import agent_runtime
    def fail(*_args, **_kwargs):
        raise OSError("lock unavailable")
    monkeypatch.setattr(measure, "_LOCK", SimpleNamespace(acquire=fail))
    monkeypatch.setattr(agent_runtime, "_run_engine", lambda *_a, **_k: pytest.fail("engine started"))
    with pytest.raises((measure.MeasureError, OSError)):
        measure.run(query=QUERY, owner_user_id=OWNER, catalog=candidate_catalog)
    assert measure.CURRENT.get() is None and dispatch._PLANNING_MEASURE.get() is None


def test_real_lock_rejects_second_measure_until_worker_finishes(monkeypatch, candidate_catalog):
    import agent_runtime
    started, finish = Event(), Event()
    errors = []
    def engine(*_args, **_kwargs):
        started.set()
        assert finish.wait(5), "controlled worker was not released"
        raise RuntimeError("controlled end")
    monkeypatch.setattr(agent_runtime, "_run_engine", engine)
    def worker():
        try:
            measure.run(query=QUERY, owner_user_id=OWNER, catalog=candidate_catalog)
        except measure.MeasureError as exc:
            errors.append(str(exc))
    thread = Thread(target=worker)
    thread.start()
    try:
        assert started.wait(5)
        with pytest.raises(measure.MeasureBusy, match="measure_busy"):
            measure.run(query=QUERY, owner_user_id=OWNER, catalog=candidate_catalog)
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive() and errors == ["measure_engine_failed"]
    assert_reset()


def test_ordinary_cold_import_does_not_require_measurement_helper():
    code = """
import builtins, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'planning_measure':
        raise ImportError('observer unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import engine.dispatch
import agent_runtime
assert 'planning_measure' not in sys.modules
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "runtime"))
    result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path,visible", [
    ("Immagini/zz_prova_metnos_a/2001", True),
    ("Immagini/not_zz_prova_metnos_a", False),
    ("Immagini/zz_prova_metnos_a?token=private", False),
    ("/home/person/Immagini/private", False),
])
def test_report_retains_only_whole_synthetic_path_components(
        monkeypatch, candidate_catalog, engine_boundary, path, visible):
    original = scenario
    def with_private_arguments(case):
        query, intent, plan = original(case)
        intent.keywords = ["private_keyword"]
        plan.steps[0].args.update(base_path=path, pattern="private_filename.jpg",
                                  recursive=True)
        return query, intent, plan
    monkeypatch.setitem(globals(), "scenario", with_private_arguments)
    engine_boundary.update(case="long", fault="no execution")
    report = measure.run(query=QUERY, owner_user_id=OWNER, catalog=candidate_catalog)
    args = report["proposed_plan"]["steps"][0]["args"]
    assert args["base_path"] == (path if visible else "<string>")
    assert args["pattern"] == "<string>" and args["recursive"] == "<boolean>"
    assert args["max_results"] == "<integer>"
    for event in report["events"]:
        if "intent" in event:
            assert event["intent"]["keywords"] == ["<string>"]
    assert report["actions"] == [{"verb": "find", "object": "files"}]
    assert_reset()
