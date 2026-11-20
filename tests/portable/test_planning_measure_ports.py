"""R1: seven real effect boundaries stay closed when the observer cannot stop.

The ordinary bridge builds its own callbacks. Only model extraction and the
external effects are replaced; neither the callbacks nor Executor.run are fake.
The extra fixture code covers seven doors with candidate manifest/form shapes.
"""
from __future__ import annotations

from inspect import getclosurevars
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import tomllib

import pytest

from engine import dispatch
from engine.executor import Executor as PlanExecutor
from engine.types import Framework, StepSpec
from loader import Executor
import planning_measure as measure


ROOT = Path(__file__).resolve().parents[2]
OWNER = "7ef0c9a07f64455b"
PARENT = "/tmp/zz_prova_metnos_a"
QUERY = "Trova i file duplicati nella cartella Immagini/zz_prova_metnos_a"


class EffectReached(BaseException):
    """An external boundary was reached; never actually perform its effect."""


@pytest.fixture
def real_ports(monkeypatch, tmp_path):
    import agent_runtime
    import devices
    import intent_extractor
    import lre_submission
    import orchestration
    import route_disambiguation
    import undo
    import users

    catalog = []
    for name in ("get_now", "find_files_hash", "list_dirs", "move_files"):
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
            filesystem_search=data.get("filesystem_search", {})))
    by_name = {item.name: item for item in catalog}
    effects = []

    def effect(name):
        def reached(*args, **kwargs):
            effects.append((name, args, kwargs))
            raise EffectReached(name)
        return reached

    monkeypatch.setattr(agent_runtime, "invoke_executor", effect("inline"))
    monkeypatch.setattr(agent_runtime, "submit_executor", effect("parallel"))
    monkeypatch.setattr(agent_runtime, "submit_automatic_lre", effect("admission"))
    monkeypatch.setattr(agent_runtime, "invoke_tool_by_name", effect("children"))
    monkeypatch.setattr(agent_runtime, "_invoke_builtin_handler", effect("builtin"))
    monkeypatch.setattr(lre_submission, "submit_rows", effect("rows"))
    monkeypatch.setattr(orchestration.dialog_pending, "save_pending", effect("dialog"))
    monkeypatch.setattr(undo, "DEFAULT_LOG", tmp_path / "undo.jsonl")
    monkeypatch.setattr(intent_extractor, "extract_intent", lambda *_a, **_k: {
        "kind": "action", "verb": "find", "object": "files",
        "actions": [{"verb": "find", "object": "files"}]})
    monkeypatch.setattr(route_disambiguation, "detect_object_ambiguity", lambda *_a, **_k: None)
    monkeypatch.setattr(users, "get_pref", lambda _owner, _key, default=None: default)
    monkeypatch.setattr(devices, "owner_id_for_actor", lambda _actor: OWNER)
    monkeypatch.setattr(agent_runtime, "guard_check", lambda *_a, **_k: (True, ""))
    # Remove both the terminal function and its emergency wrapper. The effect
    # doors must enforce the independent scope even if CURRENT has been lost.
    monkeypatch.setattr(measure, "finish", lambda *_a: None)
    monkeypatch.setattr(dispatch, "_measure", lambda *_a: None)

    def run(active, enter, *, lose_current=False):
        def intercept(**callbacks):
            assert (dispatch._PLANNING_MEASURE.get() is not None) is active
            assert (measure.CURRENT.get() is not None) is active
            if lose_current:
                measure.CURRENT.set(None)
            measure.finish(None, None, catalog)
            dispatch._measure("finish", None, None, catalog)
            return enter(callbacks, by_name)

        monkeypatch.setattr(dispatch, "run_turn", intercept)
        if active:
            return measure.run(QUERY, OWNER, catalog)
        return agent_runtime._run_engine(
            QUERY, catalog, turn_id="45c1ac33e4ab4a178941d8cb9a4e4d02",
            actor="host", channel="http", owner_user_id=OWNER,
            user_query_raw=QUERY, credential_meta=None)

    return SimpleNamespace(run=run, effects=effects, catalog=catalog)


def enter_port(port, callbacks, by_name):
    if port == "inline":
        return callbacks["invoke_executor_cb"]("get_now", {"timezone": "Europe/Rome"})
    if port == "parallel":
        return callbacks["submit_executor_cb"]("get_now", {"timezone": "Europe/Rome"})
    if port == "admission":
        return callbacks["admit_long_work_cb"](Framework([
            StepSpec("find_files_hash", {"base_path": PARENT, "max_results": 0}),
            StepSpec("final_answer", {})]))
    if port == "children":
        import partitioned_runs
        admit = callbacks["admit_long_work_cb"]
        partition_children = getclosurevars(admit).nonlocals["_partition_children"]
        policy = partitioned_runs.policy_of(by_name["find_files_hash"])
        return partition_children(policy, excluded_files={})(PARENT)
    if port == "rows":
        plan = Framework([
            StepSpec("move_files", {
                "entries": [{"path": PARENT + "/a.txt"}],
                "dst_template": PARENT + "/raccolta/{name}"}),
            StepSpec("final_answer", {})])
        executor = PlanExecutor(
            invoke_executor=callbacks["invoke_executor_cb"],
            catalog=list(by_name.values()))
        return executor.run(plan, query="Sposta a.txt nella cartella raccolta",
                            runtime_ctx={**callbacks["runtime_ctx"], "_gate_approved": True})
    import orchestration
    if port == "confirmation":
        dialog = [{"var": "decision", "prompt": "Confermi?",
                   "schema": {"kind": "choice", "choices": [
                       {"label": "Approva", "value": "apply"},
                       {"label": "Annulla", "value": "cancel"}]},
                   "optional": False}]
    else:
        assert port == "form"
        dialog = [{"var": "base_path", "prompt": "Cartella",
                   "schema": {"kind": "text"}, "optional": False,
                   "default": PARENT}]
    return orchestration.invoke_get_inputs_internal(
        sender_id="http:host", title="Conferma" if port == "confirmation" else "Cartella",
        description=None, dialog=dialog, fmt="form", actor="host",
        owner_user_id=OWNER, channel="http", timeout_s=3600,
        origin_turn_id="45c1ac33e4ab4a178941d8cb9a4e4d02")


PORTS = ("inline", "parallel", "admission", "children", "rows", "confirmation", "form")


@pytest.mark.parametrize("port", PORTS)
@pytest.mark.parametrize("lose_current", [False, True], ids=["context-present", "context-lost"])
def test_active_measurement_refuses_real_ports_without_finish(real_ports, port, lose_current):
    with pytest.raises(measure.MeasureError, match="^measure_observer_failed$"):
        real_ports.run(True, lambda cb, cat: enter_port(port, cb, cat),
                       lose_current=lose_current)
    assert real_ports.effects == []
    assert measure.CURRENT.get() is None
    assert dispatch._PLANNING_MEASURE.get() is None


@pytest.mark.parametrize("port", PORTS)
def test_off_measurement_preserves_real_ports(real_ports, port):
    expected = "dialog" if port in {"confirmation", "form"} else port
    with pytest.raises(EffectReached, match=f"^{expected}$"):
        real_ports.run(False, lambda cb, cat: enter_port(port, cb, cat))
    assert [effect[0] for effect in real_ports.effects] == [expected]
    assert measure.CURRENT.get() is None
    assert dispatch._PLANNING_MEASURE.get() is None


@pytest.mark.parametrize("port", ["executor", "opener"])
def test_direct_cold_ports_do_not_import_dispatch(port):
    code = """
import sys
from pathlib import Path
import tomllib

blocked = []
class ForbidDispatch:
    def find_spec(self, fullname, *_args):
        if fullname == 'engine.dispatch':
            blocked.append(fullname)
            raise ImportError('direct ports must not import the dispatcher')
sys.meta_path.insert(0, ForbidDispatch())
assert 'engine.dispatch' not in sys.modules
seen = []
class EffectReached(BaseException):
    pass
def reached(*args, **kwargs):
    seen.append((args, kwargs))
    raise EffectReached()

try:
    if sys.argv[1] == 'executor':
        from engine.executor import Executor as PlanExecutor
        from engine.types import Framework, StepSpec
        from loader import Executor
        path = Path(sys.argv[2]) / 'executors/get_now/manifest.toml'
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        contract = Executor(
            name=data['name'], version=data['version'],
            description=data['description']['it'], affinity=data['affinity'],
            args_schema=data['args'], capabilities=data.get('capabilities', []),
            tests=[], code_path=None, manifest_path=path, signed_by='',
            timeout_s=data.get('timeout_s', 30), execution_policy=data.get('execution', {}),
            execution_policy_declared='execution' in data)
        executor = PlanExecutor(invoke_executor=reached, catalog=[contract])
        executor.run(Framework([
            StepSpec('get_now', {'timezone': 'Europe/Rome'}), StepSpec('final_answer', {})]),
            query='Che ora è a Roma?', runtime_ctx={'owner_user_id': '7ef0c9a07f64455b',
                                                  'channel': 'http', '_gate_approved': True})
    else:
        import orchestration
        orchestration.dialog_pending.save_pending = reached
        orchestration.invoke_get_inputs_internal(
            sender_id='http:host', title='Cartella', description=None,
            dialog=[{'var': 'base_path', 'prompt': 'Cartella', 'schema': {'kind': 'text'},
                     'optional': False, 'default': '/tmp/zz_prova_metnos_a'}],
            fmt='form', actor='host', owner_user_id='7ef0c9a07f64455b', channel='http',
            timeout_s=3600, origin_turn_id='45c1ac33e4ab4a178941d8cb9a4e4d02')
except EffectReached:
    pass
assert len(seen) == 1, seen
if sys.argv[1] == 'executor':
    tool, args = seen[0][0]
    assert tool == 'get_now' and args['timezone'] == 'Europe/Rome', seen
    assert args['_channel'] == 'http' and isinstance(args['_lang'], str), seen
assert blocked == [], blocked
assert 'engine.dispatch' not in sys.modules
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "runtime"))
    result = subprocess.run([sys.executable, "-B", "-c", code, port, str(ROOT)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
