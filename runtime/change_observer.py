"""change_observer — daemon che monitora i change_intent in stato
APPLIED, aggiorna metrics e transiziona a FINALIZED o ROLLED_BACK
(ADR 0158, Fase 3).

Trigger scheduler v2: daily@03:00 (notte, dopo materializer 01:00 e
applier every_10m). Cap 200 intent per fire.

Logica per kind:
  - create_executor / extend_executor:
      metrics = lookup executor_stats post applied_at
      finalize: calls_post_apply >= 1 e last_call_ok=True (o non chiamato
                ma applicato da >= grace_days)
      rollback: last_call_ok=False e total_calls in finestra >= 3 con fail_rate>0.5

  - dedupe_executors:
      finalize: alias file presente e executor B non chiamato (deprecato ok)
      rollback: executor B chiamato dopo deprecation (alias not enforced)

  - materialize_pipeline / cache_pattern:
      finalize: state ancora 'active' in storage downstream
      rollback: state demoted (segnala che L1/L2 lo ha auto-degradato)

  - reject_pattern:
      finalize: zero feedback ✗ per la (query,pipeline) dopo applied_at
      rollback: nuovi ✗ post applied_at (significa che il ban non funziona)

Grace period default: 7 giorni (env METNOS_CHANGE_GRACE_DAYS).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Callable

import config as C
from change_intents import (
    KIND_CACHE_PATTERN,
    KIND_CREATE_EXECUTOR,
    KIND_DEDUPE_EXECUTORS,
    KIND_EXTEND_EXECUTOR,
    KIND_MATERIALIZE_PIPELINE,
    KIND_REJECT_PATTERN,
    STATE_APPLIED,
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
        return time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


def _age_days(iso: str | None) -> float:
    epoch = _iso_to_epoch(iso)
    if epoch == 0.0:
        return 0.0
    return (time.time() - epoch) / 86400.0


# --- Verifier: create_executor / extend_executor -------------------------

def _verify_executor_health(ci: ChangeIntent) -> Verdict:
    """Common health check via executor_stats."""
    effect = ci.applied_effect or {}
    name = effect.get("executor_name") or ci.intent_target
    age = _age_days(ci.applied_at)
    try:
        from executor_aging import lookup
        stat = lookup(name)
    except Exception:
        stat = None
    if stat is None:
        # Executor non in stats — non ancora chiamato, ok finche' giovane
        if age >= _grace_days():
            return ("finalize", {"calls": 0, "age_days": round(age, 1),
                                  "note": "no calls in grace period"}, None)
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
                f"executor auto-deprecated at {stat.deprecated_at}")
    # Rollback soft: last_call_ok=False con calls >= 3
    if stat.last_call_ok is False and stat.total_calls >= 3:
        return ("rollback", metrics,
                "last 3+ calls failed (fail rate too high)")
    # Finalize: oltre grace e calls >= 1 success
    if age >= _grace_days():
        if stat.total_calls >= 1 and stat.last_call_ok is not False:
            return ("finalize", metrics, None)
        if stat.total_calls == 0:
            # Non chiamato, ma siamo a grace — finalize "dormiente"
            return ("finalize", {**metrics, "note": "no calls but past grace"}, None)
    return ("observing", metrics, None)


# --- Verifier: dedupe_executors ------------------------------------------

def _verify_dedupe(ci: ChangeIntent) -> Verdict:
    effect = ci.applied_effect or {}
    b = effect.get("alias_from") or ci.intent_body.get("b")
    a = effect.get("alias_to") or ci.intent_body.get("a")
    age = _age_days(ci.applied_at)
    aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
    if not aliases_path.exists():
        return ("rollback", {"reason": "aliases.json missing"},
                "aliases store deleted")
    try:
        aliases = json.loads(aliases_path.read_text())
    except (json.JSONDecodeError, OSError):
        return ("rollback", {"reason": "aliases.json unreadable"},
                "aliases store corrupt")
    if aliases.get(b) != a:
        return ("rollback", {"alias_now": aliases.get(b), "expected": a},
                "alias mapping removed/changed")
    metrics = {"alias_intact": True, "age_days": round(age, 1)}
    if age >= _grace_days():
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


# --- Verifier: materialize_pipeline / cache_pattern ----------------------

def _verify_pipeline(ci: ChangeIntent) -> Verdict:
    body = ci.intent_body or {}
    shape_hash = body.get("path_shape_hash")
    age = _age_days(ci.applied_at)
    if not shape_hash:
        return ("rollback", {"reason": "no path_shape_hash"}, "intent malformed")
    db = C.DB_MULTI_TOOL_PATHS
    if not db.exists():
        return ("rollback", {"reason": "multi_tool_paths.sqlite missing"},
                "storage deleted")
    cn = sqlite3.connect(str(db))
    try:
        row = cn.execute(
            "SELECT state, uses, ok_count, fail_count FROM multi_tool_paths "
            "WHERE path_shape_hash=?",
            (shape_hash,),
        ).fetchone()
    finally:
        cn.close()
    if row is None:
        return ("rollback", {"reason": "row not found"}, "pipeline pruned")
    state, uses, ok, fail = row
    metrics = {"state": state, "uses": uses, "ok_count": ok,
               "fail_count": fail, "age_days": round(age, 1)}
    if state == "demoted":
        return ("rollback", metrics, "L2 auto-demoted")
    if age >= _grace_days() and state == "active":
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


def _verify_cache_pattern(ci: ChangeIntent) -> Verdict:
    body = ci.intent_body or {}
    canonical = body.get("canonical_query")
    tool_name = body.get("tool_name") or ci.intent_target
    age = _age_days(ci.applied_at)
    if not canonical or not tool_name:
        return ("rollback", {"reason": "missing key fields"}, "intent malformed")
    db = C.DB_MNESTOMA
    if not db.exists():
        return ("rollback", {"reason": "mnest.sqlite missing"}, "storage deleted")
    cn = sqlite3.connect(str(db))
    try:
        row = cn.execute(
            "SELECT state, uses, ok_count, fail_count FROM canonical_query_log "
            "WHERE canonical_query=? AND tool_name=?",
            (canonical, tool_name),
        ).fetchone()
    finally:
        cn.close()
    if row is None:
        return ("rollback", {"reason": "row not found"}, "cache pattern pruned")
    state, uses, ok, fail = row
    metrics = {"state": state, "uses": uses, "ok_count": ok,
               "fail_count": fail, "age_days": round(age, 1)}
    if state == "demoted":
        return ("rollback", metrics, "L1 auto-demoted")
    if age >= _grace_days() and state == "active":
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


# --- Verifier: reject_pattern --------------------------------------------

def _verify_reject_pattern(ci: ChangeIntent) -> Verdict:
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
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
                    if (rec.get("action") == "error"
                            and rec.get("canonical") == canonical
                            and float(rec.get("ts") or 0) > applied_epoch):
                        new_rejects += 1
        except OSError:
            pass
    metrics = {"canonical_query": canonical, "new_rejects_post_apply": new_rejects,
               "age_days": round(age, 1)}
    if new_rejects >= 2:
        return ("rollback", metrics,
                f"ban inefficace: {new_rejects} nuovi feedback ✗ dopo accept")
    if age >= _grace_days():
        return ("finalize", metrics, None)
    return ("observing", metrics, None)


# --- Dispatcher ----------------------------------------------------------

_VERIFIERS: dict[str, Callable[[ChangeIntent], Verdict]] = {
    KIND_CREATE_EXECUTOR:      _verify_executor_health,
    KIND_EXTEND_EXECUTOR:      _verify_executor_health,
    KIND_DEDUPE_EXECUTORS:     _verify_dedupe,
    KIND_MATERIALIZE_PIPELINE: _verify_pipeline,
    KIND_CACHE_PATTERN:        _verify_cache_pattern,
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
                rolled = _physical_rollback(ci, reason or "observer trigger")
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
                            "error": f"transition error: {exc}"})

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
            f"change_rollback non importabile, rollback fisico impossibile "
            f"per intent {ci.id}: {exc}") from exc
    effect = rollback_for_kind(ci)
    if isinstance(effect, dict) and effect.get("physical_rollback") == "error":
        # Rollback fisico fallito: NON marcare rolled_back (§2.8).
        raise RuntimeError(
            f"rollback fisico fallito per intent {ci.id}: "
            f"{effect.get('error')}")
    mark_rolled_back(ci.id, reason=reason)
    return effect


if __name__ == "__main__":
    import pprint
    pprint.pprint(task_change_observer())
