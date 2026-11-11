"""change_applier — daemon che applica fisicamente i change_intent in
stato ACCEPTED (ADR 0158).

Trigger scheduler v2: every_10m (reattivo, no daily — gli accept utente
devono essere applicati in tempi brevi).

Per kind:
  - create_executor       → invoca synth_request pipeline (~150s)
  - extend_executor       → manifest TOML patch + rollback_blob + re-sign
  - dedupe_executors      → alias setup
  - materialize_pipeline  → la pipeline proposta gira una volta come turno reale
  - cache_pattern         → canonical_query_log.state = active
  - reject_pattern        → rejected_patterns.jsonl append

Ogni handler:
  - idempotente (re-run safe)
  - ritorna dict `effect` salvato in change_intents.applied_effect
  - errore → mark_failed con reason (loop sicuro; retry manuale via UI)

Audit JSONL ~/.local/share/metnos/audit/change_applier.jsonl
"""
from __future__ import annotations

import json
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
    STATE_ACCEPTED,
    ChangeIntent,
    list_intents,
    mark_applied,
    mark_failed,
)
from logging_setup import get_logger

_log = get_logger(__name__)


# --- Audit ---------------------------------------------------------------

def _audit(record: dict) -> None:
    audit_path = C.PATH_USER_DATA / "audit" / "change_applier.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), **record}
    try:
        with audit_path.open("a") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- Handler: create_executor --------------------------------------------

class ReviewPending(Exception):
    """Synth made a candidate that waits for the independent review.

    Nothing is applied and nothing failed: the proposal stays accepted until
    the reviewed candidate is admitted (ADR 0224) or the person withdraws it.
    """

    def __init__(self, proposal_id: str) -> None:
        self.proposal_id = proposal_id
        super().__init__(proposal_id)


def _planner_can_use(name: str) -> bool:
    """True when ``name`` is an executor the planner selects from."""
    from loader import VISIBILITY_COMPOSER, filter_for_visibility, load_catalog

    return name in filter_for_visibility(
        load_catalog(verify=True), VISIBILITY_COMPOSER).executors


def _creation_request(ci: ChangeIntent) -> tuple[str, str]:
    """The name a creation proposal asks for and the request Synth builds
    from: the one frozen when the proposal was born, never the page sentence
    (that one is rewritten at every new gap)."""
    body = ci.intent_body or {}
    target = ci.intent_target
    return (body.get("name") or target,
            body.get("intent_text") or body.get("query_sample")
            or ci.intent_summary or target)


def pending_review(ci: ChangeIntent) -> str | None:
    """The retained candidate of this proposal that waits for review, if any:
    under the name asked for or, when Synth chose another name, the one
    built from the same request."""
    name, intent_text = _creation_request(ci)
    try:
        from synt import DEFAULT_PROPOSALS_DIR
        from synth_proposal_store import birth_reason, pending_candidate
        return pending_candidate(DEFAULT_PROPOSALS_DIR, name=name,
                                 reason=birth_reason(intent_text))
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not a candidate
        _log.warning("change_applier: pending review lookup failed: %r", exc)
        return None


def apply_create_executor(ci: ChangeIntent) -> dict:
    """Ask Synth for the executor and report only what really happened.

    «Applied» means the planner can use the executor now. A candidate that
    waits for the independent review raises ``ReviewPending``; a redirect to
    an existing executor or a candidate that is not active is a failure with
    its reason, never a silent success (§2.8).

    Latenza: ~150s wall per generazione full (stage 1-5). Daemon ok.
    """
    from messages import get as _msg

    expected_name, intent_text = _creation_request(ci)

    try:
        if _planner_can_use(expected_name):
            return {
                "executor_name": expected_name,
                "short_circuit": "already_in_catalog",
            }
    except Exception as exc:  # noqa: BLE001 - Synth below reports the real state
        _log.warning("change_applier: catalog check failed: %r", exc)

    waiting = pending_review(ci)
    if waiting:
        raise ReviewPending(waiting)

    from synth_request import handle_synth_request
    result = handle_synth_request(
        {"expected_name": expected_name, "intent": intent_text},
        user_query=intent_text,
        progress=None,
        verbose=False,
    )
    if result.get("review_pending"):
        raise ReviewPending(str(result.get("proposal_id") or ""))
    if not result.get("ok", True):
        # Synth says why in one of three places: an error before it starts,
        # a refusal at delivery, or the reason a stage refused the candidate.
        reason = (result.get("error") or result.get("install_error")
                  or result.get("reason") or "unknown")
        raise RuntimeError(f"synth_request failed: {reason}")
    if result.get("redirected"):
        raise RuntimeError(_msg("MSG_CHANGE_REDIRECTED", name=expected_name,
                                existing=str(result.get("name") or "")))
    if not _planner_can_use(expected_name):
        raise RuntimeError(_msg("MSG_CHANGE_NOT_ACTIVE", name=expected_name))
    return {
        "executor_name": expected_name,
        "synth_proposal_id": result.get("proposal_id"),
        "stages_completed": result.get("stages_completed"),
        "final_state": result.get("final_state"),
    }


# --- Handler: extend_executor --------------------------------------------

def apply_extend_executor(ci: ChangeIntent) -> dict:
    """Modifica manifest TOML in place + rollback_blob + re-sign.

    Implementazione in `change_applier_extend.extend_executor_manifest`
    (Fase 2.2). Se il modulo non e' ancora deployato, solleva
    NotImplementedError — il daemon marca FAILED con reason chiaro,
    l'utente vedra' Retry attivo quando 2.2 e' live.
    """
    try:
        from change_applier_extend import extend_executor_manifest
    except ImportError as exc:
        raise NotImplementedError(
            "extend_executor handler deferred to Fase 2.2: " + str(exc)
        )
    return extend_executor_manifest(ci)


# --- Handler: dedupe_executors -------------------------------------------

def apply_dedupe_executors(ci: ChangeIntent) -> dict:
    """Marca uno dei due executor come alias dell'altro.

    Strategia MVP: l'executor B (intent_target) viene marcato
    `deprecated_at=<now>` in executor_stats; alias write in
    `~/.local/share/metnos/executor_aliases.json` (executor_target_b →
    executor_a). Loader puo' leggere alias e route runtime.

    Idempotente: re-run no-op se gia' deprecated.
    """
    body = ci.intent_body or {}
    a = body.get("a")
    b = body.get("b") or ci.intent_target
    if not a or not b or a == b:
        raise ValueError(f"dedupe needs distinct a,b — got a={a} b={b}")
    # La lista e' la stessa SoT usata dall'ager: una dedupe automatica non
    # deve poter ritirare il seed minimo del sistema. Un eventuale override
    # umano futuro dovra' essere una decisione esplicita e auditabile, non un
    # booleano nascosto nel body di una proposta autogenerata.
    from executor_aging import PROTECTED_NAMES
    if b in PROTECTED_NAMES:
        raise PermissionError(f"cannot deprecate protected executor: {b}")
    if (C.PATH_EXECUTORS / b).is_dir():
        raise PermissionError(f"cannot deprecate handcrafted executor: {b}")
    # Provenienza e demote passano dal deposito che possiede la decisione:
    # le due istruzioni dirette qui sotto scrivevano un file esatto qualunque
    # cosa l'installazione avesse migrato, e saltavano la storia del deposito.
    from executor_lifecycle_state import recorded_source
    source = recorded_source(b)
    if not isinstance(source, str) or not source.startswith("synth"):
        raise PermissionError(
            f"cannot auto-deprecate executor without synth provenance: {b}")

    aliases_path = C.PATH_USER_DATA / "executor_aliases.json"
    aliases_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(aliases_path.read_text()) if aliases_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        existing = {}
    if existing.get(b) == a:
        return {"alias_existing": True, "alias_from": b, "alias_to": a}
    existing[b] = a
    aliases_path.write_text(json.dumps(existing, indent=2))

    # Deprecate B
    from executor_lifecycle_state import Restriction, restrict_executor
    demoted = restrict_executor(b, restriction=Restriction.DEMOTED,
                                reason=f"duplicate of {a}")

    return {"alias_from": b, "alias_to": a, "deprecated": b, "demoted": demoted}


# --- Handler: materialize_pipeline ---------------------------------------

def apply_materialize_pipeline(ci: ChangeIntent) -> dict:
    """ESEGUE la pipeline proposta UNA volta come turno reale (2/7/2026).

    Regola dei livelli (Roberto 13/6): una pipeline di tool esistenti e'
    territorio della CACHE (L1 impara dai turni reali con args e feedback),
    non di un deposito parallelo. L'accept umano = «provala»: il turno gira
    in `scheduled_turn_scope` (valgono consent-gate outbound, notify-guard
    e le guardie del vaglio), l'esito onesto finisce nell'effect e
    l'observer giudica come per ogni apply. Se il turno funziona, L0/L1
    imparano DA SOLI dal turno vero — zero evidenza sintetica.

    (La vecchia semantica — attivare la riga in multi_tool_paths via
    path_shape_hash — e' morta con la famiglia multi_tool: store senza
    writer dall'11/6, adapter ritirato 2/7. Il body telos non aveva
    comunque mai avuto lo shape_hash: ogni accept falliva.)
    """
    body = ci.intent_body or {}
    query = (body.get("suggested_query") or ci.intent_summary or "").strip()
    if not query:
        raise ValueError("materialize_pipeline needs suggested_query in body")
    from treated_issues_guard import scheduled_turn_scope
    from agent_runtime import run_turn
    with scheduled_turn_scope():
        log = run_turn(query, actor="host", channel="telos_apply")
    fk = getattr(log, "final_kind", None) or ""
    effect = {
        "ran_query": query[:200],
        "final_kind": fk,
        "turn_id": getattr(log, "turn_id", None),
        "final_message": (getattr(log, "final_message", "") or "")[:400],
        "steps": [getattr(s, "chosen_tool", None)
                  for s in (getattr(log, "steps", None) or [])][:10],
        "effect_counts": dict(getattr(log, "effect_counts", None) or {}),
        "false_success_detected": bool(
            getattr(log, "false_success_detected", False)),
    }
    if fk == "error":
        raise RuntimeError(
            f"pipeline run failed: {effect['final_message'][:150]}")
    return effect


# --- Handler: cache_pattern ----------------------------------------------

def apply_cache_pattern(ci: ChangeIntent) -> dict:
    """Marca canonical_query_log row come 'active' (promote da
    candidate/shadow). Idempotente."""
    body = ci.intent_body or {}
    canonical = body.get("canonical_query")
    tool_name = body.get("tool_name") or ci.intent_target
    if not canonical or not tool_name:
        raise ValueError("cache_pattern needs canonical_query + tool_name in body")
    db = C.DB_MNESTOMA
    if not db.exists():
        raise RuntimeError(f"mnest.sqlite missing: {db}")
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        cur = cn.execute(
            """UPDATE canonical_query_log SET state='active'
                WHERE canonical_query=? AND tool_name=?""",
            (canonical, tool_name),
        )
        n_updated = cur.rowcount
        cn.commit()
    finally:
        cn.close()
    if n_updated == 0:
        raise RuntimeError(
            f"no canonical_query_log row matching ({canonical}, {tool_name})"
        )
    return {"canonical_query": canonical, "tool_name": tool_name,
            "state_set_to": "active", "rows_updated": n_updated}


# --- Handler: reject_pattern ---------------------------------------------

def apply_reject_pattern(ci: ChangeIntent) -> dict:
    """Append in rejected_patterns.jsonl, letto da
    `turn_feedback.rejected_pipelines_for_query`: il planner esclude quella
    pipeline per la stessa richiesta, per tutti gli utenti."""
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
    tools = body.get("tools_sequence") or []
    if not tools:
        # Nothing to avoid: recording it would be an effect in name only.
        from messages import get as _msg
        raise RuntimeError(_msg("MSG_CHANGE_REJECT_EMPTY"))
    out_path = C.PATH_USER_DATA / "rejected_patterns.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "canonical_query": canonical,
        "tools_sequence": tools,
        "n_rejections": body.get("n_rejections"),
        "applied_by": "change_applier",
        "change_intent_id": ci.id,
    }
    # Dedup: skip se identico record gia' presente
    if out_path.exists():
        try:
            with out_path.open() as fp:
                for line in fp:
                    try:
                        ex = json.loads(line)
                        if (ex.get("canonical_query") == canonical
                                and ex.get("tools_sequence") == tools):
                            return {"already_present": True,
                                    "canonical_query": canonical}
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    with out_path.open("a") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"canonical_query": canonical, "tools_sequence": tools,
            "appended_to": str(out_path)}


# --- Dispatcher ----------------------------------------------------------

_HANDLERS: dict[str, Callable[[ChangeIntent], dict]] = {
    KIND_CREATE_EXECUTOR:      apply_create_executor,
    KIND_EXTEND_EXECUTOR:      apply_extend_executor,
    KIND_DEDUPE_EXECUTORS:     apply_dedupe_executors,
    KIND_MATERIALIZE_PIPELINE: apply_materialize_pipeline,
    KIND_CACHE_PATTERN:        apply_cache_pattern,
    KIND_REJECT_PATTERN:       apply_reject_pattern,
}


def task_change_applier(payload: dict | None = None) -> dict:
    """Daemon job. Legge ACCEPTED, applica, transition a APPLIED o FAILED.

    Cap interno: max 20 intent per fire (evita un fire infinito).
    Long-running handler (create_executor ~150s) bloccherebbe il
    scheduler — meglio batchare e lasciare al fire successivo.
    """
    max_per_fire = (payload or {}).get("max_per_fire", 20)
    accepted = list_intents(state=STATE_ACCEPTED, limit=max_per_fire,
                             order_by="discovered_desc")
    applied_count = 0
    failed_count = 0
    skipped_count = 0
    details: list[dict] = []
    for ci in accepted:
        handler = _HANDLERS.get(ci.intent_kind)
        if handler is None:
            mark_failed(ci.id, reason=f"no handler for kind={ci.intent_kind}")
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "failed_no_handler"})
            _audit({"event": "no_handler", "id": ci.id, "kind": ci.intent_kind})
            continue
        try:
            effect = handler(ci)
        except ReviewPending as pending:
            # Still accepted: the next run checks again without asking Synth.
            skipped_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "review_pending",
                            "proposal_id": pending.proposal_id})
            _audit({"event": "review_pending", "id": ci.id,
                    "kind": ci.intent_kind, "proposal_id": pending.proposal_id})
            continue
        except NotImplementedError as exc:
            mark_failed(ci.id, reason=f"not_yet_implemented: {exc}")
            skipped_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "skipped_not_implemented",
                            "reason": str(exc)})
            _audit({"event": "not_implemented", "id": ci.id,
                    "kind": ci.intent_kind, "reason": str(exc)})
            continue
        except Exception as exc:
            mark_failed(ci.id, reason=f"{type(exc).__name__}: {exc}")
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "failed", "error": str(exc)[:200]})
            _audit({"event": "failed", "id": ci.id, "kind": ci.intent_kind,
                    "error": str(exc)[:200]})
            continue
        mark_applied(ci.id, effect=effect)
        applied_count += 1
        details.append({"id": ci.id, "kind": ci.intent_kind,
                        "result": "applied", "effect": effect})
        _audit({"event": "applied", "id": ci.id, "kind": ci.intent_kind,
                "effect": effect})

    return {
        "ok": True,
        "n_accepted": len(accepted),
        "applied": applied_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "details": details[:10],  # cap audit log
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(task_change_applier())
