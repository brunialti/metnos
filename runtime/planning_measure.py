"""Request-local planning observation, stopped before work admission or execution."""

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
import re
from threading import Lock
from uuid import uuid4


CURRENT: ContextVar = ContextVar("planning_measure", default=None)
_LOCK = Lock()
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_SYNTHETIC_COMPONENT = re.compile(r"zz_[A-Za-z0-9_.-]+\Z")


class MeasureError(RuntimeError):
    """A safe diagnostic code; raw engine errors must not enter the response."""


class MeasureBusy(MeasureError):
    """Another measurement still owns its worker and the local model."""


class MeasureComplete(BaseException):
    """Terminal control flow, deliberately outside ordinary engine handlers."""

    def __init__(self, report: dict):
        super().__init__()
        self.report = report


class _MeasureFailed(BaseException):
    """Observation failure must also escape best-effort engine handlers."""

    def __init__(self, code: str):
        super().__init__()
        self.code = code


@dataclass
class _State:
    owner: str
    events: list = field(default_factory=list)
    previous: dict = field(default_factory=dict)
    initial_intent: dict | None = None
    proposed_plan: dict | None = None
    report: dict | None = None


def _snapshot(value) -> dict:
    if callable(getattr(value, "to_dict", None)):
        value = value.to_dict()
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    result = deepcopy(value)
    if not isinstance(result, dict):
        raise TypeError("observation is not structured")
    return result


def observe(name: str, value) -> None:
    """Keep independent snapshots, recording only changes in the same category."""
    state = CURRENT.get()
    if state is None or value is None:
        return
    try:
        if not isinstance(name, str) or not name:
            raise ValueError("observation name is missing")
        data = _snapshot(value)
        category = "plan" if "steps" in data else "intent"
        if category == "intent" and not {"verb", "object"} <= data.keys():
            raise ValueError("intent shape is missing")
        if name == "intent_extracted" and state.initial_intent is None:
            state.initial_intent = data
        if name == "proposer.propose" and category == "plan":
            if state.proposed_plan is None:
                state.proposed_plan = data
        if category not in state.previous or data != state.previous[category]:
            state.events.append({"name": name, category: data})
            state.previous[category] = data
    except Exception:
        raise _MeasureFailed("measure_observation_failed") from None


def _synthetic_path(value: str) -> bool:
    # Reject prose, URLs and query strings even when they contain a fake folder.
    if (not value or any(char.isspace() or ord(char) < 32 for char in value)
            or any(char in value for char in "?:#${}")):
        return False
    parts = re.split(r"[/\\]", value)
    return (not any(part in {".", ".."} for part in parts)
            and any(_SYNTHETIC_COMPONENT.fullmatch(part) for part in parts))


def _literal(value: str) -> str:
    return value if value and all(char in "/\\*" for char in value) else "<string>"


def _redact(value, *, reference: bool = False):
    """Retain containers, booleans and engine references; mask private scalars."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("argument names are not strings")
        return {key: _redact(item, reference=key in {"from_step", "from_steps"})
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, reference=reference) for item in value]
    if value is None:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        return value if reference and value > 0 else "<integer>"
    if type(value) is float:
        return "<number>"
    if isinstance(value, str):
        if _synthetic_path(value):
            return value
        from engine.executor import _FILLER_RE, _RUNTIME_RE, _STEPREF_RE, _STEPSREF_RE
        matches = sorted(
            (match for pattern in (_STEPREF_RE, _STEPSREF_RE, _RUNTIME_RE, _FILLER_RE)
             for match in pattern.finditer(value)), key=lambda match: match.start())
        if matches:
            # Only separators/wildcards are public in literal reference gaps.
            result, cursor = [], 0
            for match in matches:
                if match.start() > cursor:
                    result.append(_literal(value[cursor:match.start()]))
                result.append(match.group())
                cursor = match.end()
            if cursor < len(value):
                result.append(_literal(value[cursor:]))
            return "".join(result)
        return _literal(value)
    raise TypeError("unsupported observed value")


def _name(value) -> str:
    return value if isinstance(value, str) and _NAME.fullmatch(value) else "<name>"


def _intent(data: dict) -> dict:
    actions = data.get("actions") or [{"verb": data.get("verb"),
                                       "object": data.get("object")}]
    result = {"verb": _name(data.get("verb")),
              "object": _name(data.get("object")),
              "actions": [{"verb": _name(action.get("verb")),
                           "object": _name(action.get("object"))}
                          for action in actions]}
    for key, value in data.items():
        if key not in result:
            result[key] = _redact(value)
    return result


def _plan(data: dict) -> dict:
    result = _redact(data)
    for source, target in zip(data["steps"], result["steps"]):
        target["tool"] = _name(source.get("tool"))
        if "if_prev_entries_nonempty" in source:
            target["if_prev_entries_nonempty"] = bool(source["if_prev_entries_nonempty"])
    return result


def _report(state: _State, plan: dict, *, shape: str, admission: str,
            gate: bool | None, boundary: dict | None = None) -> dict:
    report = {
        "actions": _intent(state.initial_intent)["actions"] if state.initial_intent is not None else [],
        "proposed_plan": _plan(state.proposed_plan) if state.proposed_plan is not None else None,
        "events": [{"name": event["name"],
                    **({"plan": _plan(event["plan"])} if "plan" in event
                       else {"intent": _intent(event["intent"])})}
                   for event in state.events],
        "final_plan": _plan(plan) if plan is not None else None, "shape": shape, "admission": admission,
        "gate_before_long": gate, "origin": "L3" if state.initial_intent is not None else "non osservata", "memory": "off",
    }
    if boundary is not None:
        report["boundary"] = boundary
    try:
        from engine.turn_diagnostics import _state
        if _state.get() and _state.get().get("candidates"):
            report["candidates"] = _state.get()["candidates"]
    except Exception:
        pass
    return report


def _early_report(state: _State, result) -> dict:
    """A real pre-execution refusal has no shape verdict, but retains its trace."""
    if (not isinstance(result, dict) or result.get("steps") != []
            or result.get("durable_admission") is not None):
        raise MeasureError("measure_terminal_not_reached")
    keys = ("final_kind", "match_source", "error_class")
    boundary = tuple(result.get(key) for key in keys)
    if state.proposed_plan is None:
        return _report(state, None, shape="non raggiunta",
                       admission="non verificata (richiede registrazione)", gate=None,
                       boundary={key: _name(value) for key, value in zip(keys, boundary)})
    if state.initial_intent is None:
        raise MeasureError("measure_origin_not_l3")
    if boundary not in {
        ("error", "terminator", "capability_missing"),
        ("needs_inputs", "record_kind", "missing_input"),
        ("error", "partition_destination", "wrong_args"),
        ("error", "validator", "invalid_args"),
    }:
        raise MeasureError("measure_terminal_not_reached")
    # The terminator combines several refusals. Its text cannot disambiguate them.
    return _report(state, state.previous["plan"], shape="non raggiunta",
                   admission="non verificata (richiede registrazione)", gate=None,
                   boundary=dict(zip(keys, boundary)))


def finish(framework, intent, executors) -> None:
    """Compute the existing shape verdict and terminate, including inline plans."""
    state = CURRENT.get()
    if state is None:
        return
    try:
        if framework is None:
            raise _MeasureFailed("measure_plan_missing")
        if state.proposed_plan is None or state.initial_intent is None:
            raise _MeasureFailed("measure_origin_not_l3")
        from durable_workloads.direct_invocation import (
            asks_the_person, is_intrinsically_long, row_policy,
        )
        from engine.partition_plans import _every_child_of
        from lre_answers import rest_can_follow
        from lre_submission import _admissible_shape, _catalog_by_name
        from partitioned_runs import policy_of

        steps = tuple(getattr(framework, "steps", ()) or ())
        by_name = _catalog_by_name(executors)
        long_steps = []
        for index, step in enumerate(steps):
            tool = str(getattr(step, "tool", "") or "")
            if tool == "final_answer":
                continue
            executor = by_name.get(tool)
            if (executor is not None and row_policy(executor)
                    and executor.execution_policy["effect"] != "read_only"):
                continue
            if executor is not None and (
                    is_intrinsically_long(executor) or getattr(executor, "lre_plan", "")):
                long_steps.append((index, step, executor))
        shape = "non applicabile: nessun passo lungo"
        gate = False
        needs_children = False
        if long_steps:
            _, refusal = _admissible_shape(
                steps, long_steps, by_name,
                fillers=bool(getattr(framework, "fillers", {}) or {}),
                rest_follows=rest_can_follow(owner_user_id=state.owner, channel="http"))
            shape = refusal["error_code"] if refusal is not None else "ammessa"
            gate = any(asks_the_person(by_name.get(getattr(step, "tool", "")))
                       for step in steps[:long_steps[0][0]])
            for _, step, executor in long_steps:
                policy = policy_of(executor)
                args = getattr(step, "args", None)
                if (policy is not None and not row_policy(executor)
                        and isinstance(args, dict)
                        and _every_child_of(args.get(policy["list_arg"])) is not None):
                    needs_children = True
        admission = ("non verificata (richiede l'elenco delle cartelle)" if needs_children
                     else "non verificata (richiede registrazione)")
        state.report = _report(state, _snapshot(framework), shape=shape,
                               admission=admission, gate=gate)
    except Exception:
        raise _MeasureFailed("measure_shape_or_redaction_failed") from None
    raise MeasureComplete(state.report)


def run(query: str, owner_user_id: str, catalog: list) -> dict:
    """Run inside the ordinary turn worker; retain ownership until it finishes."""
    if (not isinstance(query, str) or not query.strip()
            or not isinstance(owner_user_id, str) or not owner_user_id
            or not isinstance(catalog, list) or not catalog):
        raise MeasureError("measure_input_invalid")
    from engine.dispatch import _PLANNING_MEASURE, _MeasureObserverFailed

    if not _LOCK.acquire(blocking=False):
        raise MeasureBusy("measure_busy")
    token = scope_token = None
    try:
        state = _State(owner_user_id)
        token = CURRENT.set(state)
        scope_token = _PLANNING_MEASURE.set((CURRENT, state, observe, finish))
        from agent_runtime import _run_engine
        from detection_lexicon import current_lang

        result = _run_engine(
            query, catalog, turn_id=uuid4().hex, actor="host", channel="http",
            owner_user_id=owner_user_id, source_request_id="", lang=current_lang(),
            verbose=False, progress=None, pre_approved_gate=False,
            conversation_id="", forced_object="", device_named=False,
            reference_images=None, resume_steps=None, placement_target=None,
            user_query_raw=query, credential_meta=None)
        return _early_report(state, result)
    except MeasureComplete as terminal:
        if state.report is None or terminal.report is not state.report:
            raise MeasureError("measure_terminal_invalid") from None
        return state.report
    except _MeasureObserverFailed:
        raise MeasureError("measure_observer_failed") from None
    except _MeasureFailed as failure:
        raise MeasureError(failure.code) from None
    except MeasureError:
        raise
    except Exception:
        raise MeasureError("measure_engine_failed") from None
    finally:
        if scope_token is not None:
            _PLANNING_MEASURE.reset(scope_token)
        if token is not None:
            CURRENT.reset(token)
        _LOCK.release()
