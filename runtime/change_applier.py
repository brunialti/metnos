"""change_applier — daemon che applica fisicamente i change_intent in
stato ACCEPTED (ADR 0158).

Trigger scheduler v2: every_10m (reattivo, no daily — gli accept utente
devono essere applicati in tempi brevi).

Per kind:
  - create_executor       → invoca synth_request pipeline (~150s)
  - extend_executor       → manifest TOML patch + rollback_blob + re-sign
  - dedupe_executors      → alias setup
  - materialize_pipeline  → multi_tool_paths.state = active
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

def apply_create_executor(ci: ChangeIntent) -> dict:
    """Invoca pipeline synt_request per generare nuovo executor.

    Idempotente: short-circuit se l'executor esiste gia' nel catalog.
    Reuse handle_synth_request che ha gia' i suoi short-circuit
    (already_in_catalog, redirected alias).

    Latenza: ~150s wall per generazione full (stage 1-5). Daemon ok.
    """
    body = ci.intent_body or {}
    target = ci.intent_target
    expected_name = body.get("name") or target
    intent_text = body.get("intent_text") or ci.intent_summary or target

    # Short-circuit: gia' nel catalog
    try:
        from loader import load_catalog
        cat = load_catalog(verify=True)
        if expected_name in cat.executors:
            return {
                "executor_name": expected_name,
                "short_circuit": "already_in_catalog",
            }
    except Exception:
        pass

    # Invoca synth
    from synth_request import handle_synth_request
    result = handle_synth_request(
        {"expected_name": expected_name, "intent": intent_text},
        user_query=intent_text,
        progress=None,
        verbose=False,
    )
    if not result.get("ok", True):
        raise RuntimeError(f"synth_request failed: {result.get('error', 'unknown')}")
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
    db = C.PATH_USER_STATE / "executor_stats.db"
    if db.exists():
        try:
            cn = sqlite3.connect(str(db), timeout=10.0)
            cn.execute(
                """UPDATE executor_stats SET deprecated_at=?
                    WHERE name=? AND deprecated_at IS NULL""",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), b),
            )
            cn.commit()
            cn.close()
        except sqlite3.Error:
            pass

    return {"alias_from": b, "alias_to": a, "deprecated": b}


# --- Handler: materialize_pipeline ---------------------------------------

def apply_materialize_pipeline(ci: ChangeIntent) -> dict:
    """Marca pipeline come 'active' in multi_tool_paths (promote da
    candidate/shadow → active). Idempotente."""
    body = ci.intent_body or {}
    shape_hash = body.get("path_shape_hash")
    if not shape_hash:
        raise ValueError("materialize_pipeline needs path_shape_hash in body")
    db = C.DB_MULTI_TOOL_PATHS
    if not db.exists():
        raise RuntimeError(f"multi_tool_paths.sqlite missing: {db}")
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        cur = cn.execute(
            "UPDATE multi_tool_paths SET state='active' WHERE path_shape_hash=?",
            (shape_hash,),
        )
        n_updated = cur.rowcount
        cn.commit()
    finally:
        cn.close()
    if n_updated == 0:
        raise RuntimeError(f"no multi_tool_paths row matching shape {shape_hash}")
    return {"shape_hash": shape_hash, "state_set_to": "active",
            "rows_updated": n_updated}


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
    """Append in rejected_patterns.jsonl. Agent_runtime legge e usa
    come hard constraint nel planner prompt."""
    body = ci.intent_body or {}
    canonical = body.get("canonical_query") or ci.intent_target
    tools = body.get("tools_sequence") or []
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
            mark_applied(ci.id, effect=effect)
            applied_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "applied", "effect": effect})
            _audit({"event": "applied", "id": ci.id, "kind": ci.intent_kind,
                    "effect": effect})
        except NotImplementedError as exc:
            mark_failed(ci.id, reason=f"not_yet_implemented: {exc}")
            skipped_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "skipped_not_implemented",
                            "reason": str(exc)})
            _audit({"event": "not_implemented", "id": ci.id,
                    "kind": ci.intent_kind, "reason": str(exc)})
        except Exception as exc:
            mark_failed(ci.id, reason=f"{type(exc).__name__}: {exc}")
            failed_count += 1
            details.append({"id": ci.id, "kind": ci.intent_kind,
                            "result": "failed", "error": str(exc)[:200]})
            _audit({"event": "failed", "id": ci.id, "kind": ci.intent_kind,
                    "error": str(exc)[:200]})

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
