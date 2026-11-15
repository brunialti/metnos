# SPDX-License-Identifier: MIT
"""A generated tool that is wrong is withdrawn at once and written again
(ADR 0239, rule 4).

Two things find a tool wrong: a fault in use, seen here after every call, and
the person, from the changes page.  A fault is a failure of the tool's own
code: it crashed, or it answered outside its own contract.  A declared failure
(arguments refused, a provider that does not answer, a permission denied) is an
answer, not a fault, and withdraws nothing.

A fault withdraws at once: the tool is hidden from the planner through the
store that owns the life cycle.  Nothing is signed and nothing is deleted, so
the corrected version is admitted through the ordinary door as the next
version of the same tool, and admitting it lifts the restriction.  A signed
retirement would close the name for good: no later version could be born
under it.  What the person finds wrong is not a fault: the version in use
stays in use until the corrected one replaces it.
"""
from __future__ import annotations

import json
import threading
import time

import config as C
from logging_setup import get_logger

log = get_logger(__name__)

# The error classes the runtime gives to a failure of the tool's own code.
FAULT_CLASSES = frozenset({"non_json", "executor_unknown", "contract_violation"})

ORIGIN_FAULT = "fault"
ORIGIN_PERSON = "person"

_SHAPE_ARGUMENTS = 12
_SHAPE_KEYS = 8
_FILLER = "a"
_SKELETON_TEXT = 64
_SKELETON_ITEMS = 8
_SKELETON_DEPTH = 3
WITHDRAWN_CASE = "call_that_failed_in_use"

# What the builder is told.  The words of a person are quoted as they are.
_FAULT_REASON = (
    "It failed in real use with an internal error ({found}) when it was called with: "
    "{shape}. One functional case must make that same call.")
_PERSON_REASON = "The person who uses it found it wrong and said: «{words}»."


def fault_class(executor: object, result: object) -> str | None:
    """The fault this call shows, or None: only a generated tool is judged."""
    from executor_metadata import source_kind

    if getattr(executor, "source", None) != source_kind(synthesized=True):
        return None
    if not isinstance(result, dict) or result.get("ok"):
        return None
    found = result.get("error_class")
    return found if found in FAULT_CLASSES else None


def _shape(value: object) -> str:
    if value is None or isinstance(value, (bool, int, float)):
        return repr(value)
    if isinstance(value, str):
        return f"text of {len(value)} characters"
    if isinstance(value, (list, tuple)):
        kinds = sorted({type(item).__name__ for item in value})
        return f"list of {len(value)} ({', '.join(kinds) or 'empty'})"
    if isinstance(value, dict):
        return f"object with keys {sorted(map(str, value))[:_SHAPE_KEYS]}"
    return type(value).__name__


def args_shape(args: object) -> str:
    """The call that failed without what it carried: names, types and sizes.

    Texts are never copied: the reason may reach the external model when the
    person allows it for the rewrite.
    """
    if not isinstance(args, dict):
        return type(args).__name__
    parts = [f"{name}: {_shape(args[name])}" for name in sorted(map(str, args))[:_SHAPE_ARGUMENTS]
             if name in args]
    return "; ".join(parts) or "no arguments"


def call_skeleton(args: object) -> dict:
    """The call that failed with every text replaced by a neutral one of the
    same length: same names, types and sizes, none of what it carried."""
    def bare(value: object, depth: int = 0):
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return _FILLER * min(len(value), _SKELETON_TEXT)
        if depth < _SKELETON_DEPTH and isinstance(value, (list, tuple)):
            return [bare(item, depth + 1) for item in value[:_SKELETON_ITEMS]]
        if depth < _SKELETON_DEPTH and isinstance(value, dict):
            return {str(key): bare(item, depth + 1)
                    for key, item in list(value.items())[:_SKELETON_ITEMS]}
        return None

    if not isinstance(args, dict):
        return {}
    return {str(name): bare(value) for name, value in list(args.items())[:_SHAPE_ARGUMENTS]}


def answered_case(call: object, known_arguments) -> dict | None:
    """The functional case that repeats a failed call and asks only for an
    answer: a tool that fails again by its own code on it does not pass.

    None when the call names an argument the new signature does not have: the
    case would test another tool.
    """
    if not isinstance(call, dict) or not set(call) <= set(known_arguments or ()):
        return None
    return {"name": WITHDRAWN_CASE, "input": call, "expect": {"error_contains": ""}}


def creation_in_use(name: str):
    """The creation proposal whose tool ``name`` is in use, if any."""
    from change_intents import IN_USE_STATES, KIND_CREATE_EXECUTOR, list_intents

    for intent in list_intents(state=list(IN_USE_STATES), intent_kind=KIND_CREATE_EXECUTOR,
                               limit=500):
        effect = intent.applied_effect or {}
        if (effect.get("executor_name") or intent.intent_target) == name:
            return intent
    return None


def _audit(record: dict) -> None:
    path = C.PATH_USER_DATA / "audit" / "generated_rewrite.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"ts": time.time(), **record}, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("generated_rewrite: audit not written: %r", exc)


def _tell(body_key: str, name: str, *, intent_id: str | None) -> None:
    """One notice where the person is; a channel that is down stops nothing."""
    try:
        import notify_admin
        from messages import get as _msg

        notify_admin.notify(
            _msg(body_key, name=name), kind="changes", severity="warning",
            event_key=f"generated_withdrawn:{name}:{int(time.time())}",
            metadata={"change_intent": intent_id} if intent_id else {})
    except Exception as exc:  # noqa: BLE001
        log.warning("generated_rewrite: notice not sent: %r", exc)


def withdraw(name: str, *, reason: str, origin: str, by: str,
             call: dict | None = None) -> dict:
    """Hide ``name`` from the planner and send its creation back to the builder.

    Returns what really happened: ``hidden`` is true only when this call hid
    the tool, ``rewriting`` only when a creation proposal took the reason.  A
    second fault of the same tool finds both already done and does nothing.
    """
    from change_intents import TransitionError, send_back
    from executor_lifecycle_state import Restriction, restrict_executor

    hidden = restrict_executor(name, restriction=Restriction.DEMOTED,
                               reason=f"withdrawn to be written again ({origin})")
    intent = creation_in_use(name)
    rewriting = False
    if intent is not None:
        try:
            send_back(intent.id, reason=reason, by=by, origin=origin, call=call)
            rewriting = True
        except TransitionError as exc:
            log.warning("generated_rewrite: %s not sent back: %r", name, exc)
    if hidden or rewriting:
        _audit({"event": "withdrawn", "name": name, "origin": origin, "by": by,
                "hidden": hidden, "rewriting": rewriting,
                "intent": intent.id if intent is not None else None})
    return {"hidden": hidden, "rewriting": rewriting,
            "intent": intent.id if intent is not None else None}


def _withdraw_after_fault(name: str, found: str, shape: str, call: dict) -> None:
    try:
        done = withdraw(name, reason=_FAULT_REASON.format(found=found, shape=shape),
                        origin=ORIGIN_FAULT, by="metnos", call=call)
        if done["hidden"] or done["rewriting"]:
            _tell("MSG_GENERATED_WITHDRAWN_REWRITING" if done["rewriting"]
                  else "MSG_GENERATED_WITHDRAWN", name, intent_id=done.get("intent"))
    except Exception as exc:  # noqa: BLE001 - the next fault tries again
        log.exception("generated_rewrite: withdrawal of %s failed: %r", name, exc)


def observe(executor: object, args: object, result: object) -> None:
    """Called after every call.  It never raises and never delays the answer:
    the withdrawal works in a thread of its own."""
    try:
        found = fault_class(executor, result)
        if found is None:
            return
        name = str(getattr(executor, "name", "") or "")
        if not name:
            return
        threading.Thread(
            target=_withdraw_after_fault,
            args=(name, found, args_shape(args), call_skeleton(args)),
            name=f"withdraw-{name[:24]}", daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        log.warning("generated_rewrite: observation failed: %r", exc)


def creation_set_aside(name: str):
    """The creation proposal whose tool ``name`` was set aside, if any."""
    from change_intents import KIND_CREATE_EXECUTOR, STATE_ROLLED_BACK, is_set_aside, list_intents

    for intent in list_intents(state=STATE_ROLLED_BACK, intent_kind=KIND_CREATE_EXECUTOR, limit=500):
        effect = intent.applied_effect or {}
        if is_set_aside(intent) and (effect.get("executor_name") or intent.intent_target) == name:
            return intent
    return None


def back_in_service(name: str, *, by: str):
    """The person took ``name`` back: its creation says «in use» again.

    Called after the life cycle store lifted the restriction, from wherever
    the person did it. A tool without a creation set aside changes nothing.
    """
    from change_intents import TransitionError, reactivate

    intent = creation_set_aside(name)
    if intent is None:
        return None
    try:
        returned = reactivate(intent.id, by=by)
    except TransitionError as exc:
        log.warning("generated_rewrite: %s not marked in use again: %r", name, exc)
        return None
    _audit({"event": "reactivated", "name": name, "by": by, "intent": intent.id})
    return returned


def reactivate_for_person(intent_id: str, *, by: str):
    """«Riattiva» on a tool set aside, from the changes page: the life cycle
    store brings the tool back first; only then the proposal says so."""
    from change_intents import TransitionError, get_intent, is_set_aside, reactivate
    from executor_lifecycle_state import revive_executor
    from messages import get as _msg

    intent = get_intent(intent_id)
    if intent is None:
        raise TransitionError(_msg("ERR_CHANGE_NOT_FOUND", id=intent_id))
    if not is_set_aside(intent):
        raise TransitionError(_msg("ERR_CHANGE_REACTIVATE_NOT_SET_ASIDE"))
    name = str((intent.applied_effect or {}).get("executor_name") or intent.intent_target)
    if not revive_executor(name, reason=by):
        raise TransitionError(_msg("ERR_CHANGE_REACTIVATE_NOT_DONE", name=name))
    returned = reactivate(intent_id, by=by)
    _audit({"event": "reactivated", "name": name, "by": by, "intent": intent_id})
    return returned


def rewrite_for_person(intent_id: str, *, words: str, by: str):
    """The person says in words what is wrong with the tool of this proposal:
    it is written again with those words.

    The version in use stays in use: it works, and the corrected one replaces
    it when it is activated.  Only a fault withdraws at once.
    """
    from change_intents import TransitionError, send_back
    from messages import get as _msg

    said = " ".join(str(words or "").split())
    if not said:
        raise TransitionError(_msg("ERR_CHANGE_REWRITE_WORDS_REQUIRED"))
    sent = send_back(intent_id, reason=_PERSON_REASON.format(words=said),
                     by=by, origin=ORIGIN_PERSON, words=said)
    _audit({"event": "rewrite_asked", "intent": intent_id, "by": by})
    return sent
