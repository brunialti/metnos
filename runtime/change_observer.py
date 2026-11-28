"""change_observer — daemon che monitora i change_intent in stato
APPLIED, aggiorna metrics e transiziona a FINALIZED o ROLLED_BACK
(ADR 0158, Fase 3).

Trigger scheduler v2: daily@03:00 (notte, dopo materializer 01:00 e
applier every_10m). Cap 200 intent per fire.

Logica per kind:
  - create_executor / extend_executor:
      metrics = executor_stats of the executor (all its calls, not a window)
      finalize: past the grace period, with at least one call and the last
                one not failed, or never called
      rollback: the executor was deprecated by the system, or its LAST call
                failed and it has at least 3 calls in all (no failure rate
                is computed)
      A created (generated) tool is not taken back for a failed call: one
      that fails by its own code is withdrawn at once and written again
      (generated_rewrite, ADR 0239 rule 4).

  - dedupe_executors:
      finalize: alias file presente e executor B non chiamato (deprecato ok)
      rollback: executor B chiamato dopo deprecation (alias not enforced)

  - materialize_pipeline:
      the proposal was run once as a real turn; judged on that turn's outcome
      finalize: the turn answered, past the grace period
      rollback: false success, error, loop, or failures without any effect

  - reject_pattern:
      finalize: no new ✗ on that request after applied_at
      rollback: new ✗ on that request after applied_at (the ban does not work)

Grace period default: 7 giorni (env METNOS_CHANGE_GRACE_DAYS).
"""
from __future__ import annotations

import calendar
import json
import os
import time
from typing import Callable

import config as C
from messages import get as _msg
from change_intents import (
    KIND_CREATE_EXECUTOR,
    KIND_DEDUPE_EXECUTORS,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    KIND_REJECT_PATTERN,
    STATE_APPLIED,
    STATE_FINALIZED,
    STATE_OBSERVED,
    ChangeIntent,
    list_intents,
    mark_finalized,
    mark_observed,
    mark_rolled_back,
    update_observed_metrics,
)


# --- Config --------------------------------------------------------------

def _grace_days() -> int:
    try:
        return int(os.environ.get("METNOS_CHANGE_GRACE_DAYS", "7"))
    except (ValueError, TypeError):
        return 7


# --- Audit ---------------------------------------------------------------

def _audit(record: dict) -> None:
    audit_path = C.PATH_USER_DATA / "audit" / "change_observer.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), **record}
    try:
        with audit_path.open("a") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- Verdict types -------------------------------------------------------

# return (verdict, metrics_dict, reason_if_rollback)
#   verdict ∈ "finalize" | "rollback" | "observing"
Verdict = tuple[str, dict, str | None]


def _iso_to_epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return 0.0


def _age_days(iso: str | None) -> float:
    epoch = _iso_to_epoch(iso)
    if epoch == 0.0:
        return 0.0
    return (time.time() - epoch) / 86400.0


# --- Verifier: create_executor / extend_executor -------------------------

def _set_aside_by_the_store(ci: ChangeIntent) -> bool:
    # No broad except: an unreadable store is an error of the night, not
    # «nothing set aside» (15/11/2026).
    from executor_aging import lookup

    effect = ci.applied_effect or {}
    stat = lookup(effect.get("executor_name") or ci.intent_target)
    return bool(stat is not None and (stat.deprecated_at or stat.archived_at))


def _verify_created_executor(ci: ChangeIntent) -> Verdict:
    """A generated tool is not taken back here for a failed call.

    One that fails by its own code is withdrawn at once and written again
    (``generated_rewrite``, ADR 0239 rule 4); a declared failure is an answer
    of the tool. What is left for this check is the tool nobody uses any
    more, which the life cycle store has set aside (rule 5): its proposal is
    taken back too, in the reversible way, and says why in words.
    """
    verdict, metrics, reason = _verify_executor_health(ci, failure_rolls_back=False)
    if verdict == "rollback":
        from executor_aging import DEPRECATED_DAYS, last_restriction

        # «Nobody used it» only when that is what took it out: a duplicate or
        # another decision is said as such (15/11/2026).
        effect = ci.applied_effect or {}
        unused = "days_inactive" in last_restriction(effect.get("executor_name") or ci.intent_target)
        reason = (_msg("MSG_CHANGE_SET_ASIDE_UNUSED", days=DEPRECATED_DAYS) if unused
                  else _msg("MSG_CHANGE_TAKEN_OUT_OF_USE"))
    return verdict, metrics, reason


def _verify_executor_health(ci: ChangeIntent, *, failure_rolls_back: bool = True) -> Verdict:
    """Common health check via executor_stats."""
    effect = ci.applied_effect or {}
    name = effect.get("executor_name") or ci.intent_target
    age = _age_days(ci.applied_at)
    from executor_aging import lookup

    # An unreadable store is not «never called»: until 15/11/2026 it was, and
    # the proposal was finalized as unused. The error reaches the observer
    # task, which counts it and leaves the proposal for the next night.
    stat = lookup(name)
    if stat is None:
        # Executor non in stats — non ancora chiamato, ok finche' giovane
        if age >= _grace_days():
            return ("finalize", {"calls": 0, "age_days": round(age, 1),
                                  "note": _msg("MSG_CHANGE_OBSERVER_NO_CALLS")}, None)
        return ("observing", {"calls": 0, "age_days": round(age, 1)}, None)

    metrics = {
        "executor_name": name,
        "total_calls": stat.total_calls,
        "last_call_ok": stat.last_call_ok,
        "deprecated_at": stat.deprecated_at,
        "age_days": round(age, 1),
    }
    # Rollback hard: deprecated dal sistema (fallisce ager check)
    if stat.deprecated_at:
        return ("rollback", metrics,
                _msg("MSG_CHANGE_OBSERVER_DEPRECATED", date=stat.deprecated_at))
    # Rollback soft: last_call_ok=False con calls >= 3. The reason says what
    # is measured: the last call, not the last three.
    if failure_rolls_back and stat.last_call_ok is False and stat.total_calls >= 3:
        return ("rollback", metrics,
                _msg("MSG_CHANGE_OBSERVER_LAST_CALL_FAILED", calls=stat.total_calls))
    # Finalize: oltre grace e calls >= 1 success
    if age >= _grace_days():
        if stat.total_calls >= 1 and (stat.last_call_ok is not False or not failure_rolls_back):
            return ("finalize", metrics, None)
        if stat.total_calls == 0:
            # Non chiamato, ma siamo a grace — finalize "dormiente"
            return ("finalize", {**metrics, "note": _msg("MSG_CHANGE_OBSERVER_NO_CALLS")}, None)
    return ("observing", metrics, None)


# --- Verifier: dedupe_executors ------------------------------------------

def _verify_dedupe(ci: ChangeIntent) -> Verdict:
    effect = ci.applied_effect or {}
    b = effect.get("alias_from") or ci.intent_body.get("b")
    a = effect.get("alias_to") or ci.intent_body.get("a")
    age = _age_days(ci.applied_at)
    aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
    if not aliases_path.exists():
        reason = _msg("MSG_CHANGE_OBSERVER_ALIASES_MISSING")
        return ("rollback", {"reason": reason}, reason)
    try:
        aliases = json.loads(aliases_path.read_text())
    except (json.JSONDecodeError, OSError):
        reason = _msg("MSG_CHANGE_OBSERVER_ALIASES_UNREADABLE")
        return ("rollback", {"reason": reason}, reason)
    if aliases.get(b) != a:
        return ("rollback", {"alias_now": aliases.get(b), "expected": a},
                _msg("MSG_CHANGE_OBSERVER_ALIAS_CHANGED"))
    metrics = {"alias_intact": True, "age_days": round(age, 1)}
    if age >= _grace_days():
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


# --- Verifier: materialize_pipeline --------------------------------------

def _verify_pipeline(ci: ChangeIntent) -> Verdict:
    effect = ci.applied_effect or {}
    age = _age_days(ci.applied_at)
    final_kind = str(effect.get("final_kind") or "")
    metrics = {
        "turn_id": effect.get("turn_id"),
        "final_kind": final_kind or "unknown",
        "step_count": len(effect.get("steps") or []),
        "age_days": round(age, 1),
        "effect_counts": dict(effect.get("effect_counts") or {}),
        "false_success_detected": bool(
            effect.get("false_success_detected")),
    }
    counts = metrics["effect_counts"]
    failures = int(counts.get("failures") or 0)
    produced = int(counts.get("items") or 0) + int(
        counts.get("mutations") or 0)
    if metrics["false_success_detected"]:
        return ("rollback", metrics, _msg("MSG_CHANGE_OBSERVER_FALSE_SUCCESS"))
    if final_kind in {"error", "loop_break"}:
        return ("rollback", metrics,
                _msg("MSG_CHANGE_OBSERVER_TURN_ERROR" if final_kind == "error"
                     else "MSG_CHANGE_OBSERVER_TURN_LOOP"))
    if failures > 0:
        if produced > 0:
            metrics["status"] = "partial"
            return ("observing", metrics, None)
        return ("rollback", metrics,
                _msg("MSG_CHANGE_OBSERVER_NO_EFFECTS"))
    if final_kind == "answer" and age >= _grace_days():
        return ("finalize", metrics, None)
    # ask/needs_inputs restano osservabili: una capability JIT puo' essere
    # completata in seguito e non giustifica un rollback distruttivo.
    return ("observing", metrics, None)


# --- Verifier: reject_pattern --------------------------------------------

def _verify_reject_pattern(ci: ChangeIntent) -> Verdict:
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
    from turn_feedback import normalized_request
    wanted = normalized_request(canonical)
    age = _age_days(ci.applied_at)
    applied_epoch = _iso_to_epoch(ci.applied_at)
    fpath = C.PATH_USER_DATA / "turn_feedback.jsonl"
    new_rejects = 0
    if fpath.exists():
        try:
            with fpath.open() as fp:
                for line in fp:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # A record names its request twice: the text and, when
                    # the engine fills it, the canonical form. The engine
                    # leaves `canonical` empty, and comparing only that made
                    # every ban look effective.
                    names = {normalized_request(rec.get("user_query")),
                             normalized_request(rec.get("canonical"))}
                    if (rec.get("action") == "error"
                            and wanted and wanted in names
                            and float(rec.get("ts") or 0) > applied_epoch):
                        new_rejects += 1
        except OSError:
            pass
    metrics = {"canonical_query": canonical, "new_rejects_post_apply": new_rejects,
               "age_days": round(age, 1)}
    if new_rejects >= 2:
        return ("rollback", metrics,
                _msg("MSG_CHANGE_OBSERVER_NEW_REJECTIONS", count=new_rejects))
    if age >= _grace_days():
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


# --- Dispatcher ----------------------------------------------------------

_VERIFIERS: dict[str, Callable[[ChangeIntent], Verdict]] = {
    KIND_CREATE_EXECUTOR:      _verify_created_executor,
    KIND_EXTEND_EXECUTOR:      _verify_executor_health,
    KIND_DEDUPE_EXECUTORS:     _verify_dedupe,
    KIND_MATERIALIZE_PIPELINE: _verify_pipeline,
    KIND_REJECT_PATTERN:       _verify_reject_pattern,
}


def task_change_observer(payload: dict | None = None) -> dict:
    """Job daily. Verifica APPLIED + OBSERVED → finalize/rollback/observing.

    Anche gli OBSERVED rientrano (re-verifica metrics in caso post-finalize
    audit). Cap 200 per fire.
    """
    max_per_fire = (payload or {}).get("max_per_fire", 200)
    targets = list_intents(state=[STATE_APPLIED, STATE_OBSERVED],
                            limit=max_per_fire, order_by="discovered_desc")
    # A consolidated creation is looked at for one thing only: a tool that
    # nobody used any more and that the life cycle store set aside (ADR 0239,
    # rule 5). Its proposal must say so, with the way back.
    targets += [ci for ci in list_intents(state=STATE_FINALIZED, intent_kind=KIND_CREATE_EXECUTOR,
                                          limit=max_per_fire)
                if _set_aside_by_the_store(ci)]
    counts = {"finalized": 0, "rolled_back": 0, "observing": 0, "errors": 0}
    details: list[dict] = []
    for ci in targets:
        verifier = _VERIFIERS.get(ci.intent_kind)
        if verifier is None:
            counts["errors"] += 1
            continue
        try:
            verdict, metrics, reason = verifier(ci)
        except Exception as exc:
            counts["errors"] += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "error": str(exc)[:200]})
            _audit({"event": "verifier_error", "id": ci.id,
                    "error": str(exc)[:200]})
            continue
        try:
            if verdict == "finalize":
                # Move to OBSERVED first if currently APPLIED (state machine)
                if ci.state == STATE_APPLIED:
                    mark_observed(ci.id, metrics=metrics)
                mark_finalized(ci.id)
                counts["finalized"] += 1
                details.append({"id": ci.id, "kind": ci.intent_kind,
                                "result": "finalized", "metrics": metrics})
                _audit({"event": "finalized", "id": ci.id,
                        "kind": ci.intent_kind, "metrics": metrics})
            elif verdict == "rollback":
                # Trigger rollback fisico via change_rollback module
                rolled = _physical_rollback(ci, reason or _msg("MSG_CHANGE_OBSERVER_TRIGGER"))
                counts["rolled_back"] += 1
                details.append({"id": ci.id, "kind": ci.intent_kind,
                                "result": "rolled_back", "reason": reason,
                                "rollback_effect": rolled})
                _audit({"event": "rolled_back", "id": ci.id,
                        "kind": ci.intent_kind, "reason": reason,
                        "rollback_effect": rolled})
            else:  # observing
                if ci.state == STATE_APPLIED:
                    mark_observed(ci.id, metrics=metrics)
                else:
                    update_observed_metrics(ci.id, metrics=metrics)
                counts["observing"] += 1
        except Exception as exc:
            counts["errors"] += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "error": _msg("LOG_CHANGE_OBSERVER_TRANSITION_ERROR", detail=exc)})

    return {
        "ok": True,
        "n_targets": len(targets),
        **counts,
        "details": details[:20],
    }


def _physical_rollback(ci: ChangeIntent, reason: str) -> dict:
    """Esegue il rollback fisico per kind, poi transition state DB.

    Delegato a `change_rollback` module (Fase 3.2). Import flat coerente col
    resto dei moduli runtime (config/change_intents importati flat sopra).

    §2.8 no-silent-failure: lo state passa a ROLLED_BACK SOLO se il rollback
    fisico è stato eseguito davvero. Se `change_rollback` non è importabile o
    solleva eccezione, NON marchiamo `rolled_back` (sarebbe una bugia): lasciamo
    l'intent nello stato corrente e ritorniamo l'errore al chiamante, che lo
    conta come errore e lo riproverà al prossimo fire.
    """
    try:
        from change_rollback import rollback_for_kind
    except ImportError as exc:
        # Modulo non disponibile: fail-loud, NON falso rolled_back.
        raise RuntimeError(
            _msg("LOG_CHANGE_OBSERVER_ROLLBACK_UNAVAILABLE", id=ci.id, detail=exc)) from exc
    effect = rollback_for_kind(ci)
    # Every handler reports a failure under `error` (or `re_sign_error` when
    # the old manifest could not be published again): looking for another key
    # marked a rollback that had not happened as done (§2.8).
    failure = None
    if isinstance(effect, dict):
        failure = (effect.get("error") or effect.get("re_sign_error")
                   or ("error" if effect.get("physical_rollback") == "error" else None))
    if failure:
        raise RuntimeError(
            _msg("LOG_CHANGE_OBSERVER_ROLLBACK_FAILED", id=ci.id, detail=failure))
    mark_rolled_back(ci.id, reason=reason)
    return effect


def rollback_change(ci: ChangeIntent, reason: str) -> dict:
    """Take an applied change back for real, then record it (also for the
    person's own decision on the changes page)."""
    return _physical_rollback(ci, reason)


if __name__ == "__main__":
    import pprint
    pprint.pprint(task_change_observer())
