# SPDX-License-Identifier: AGPL-3.0-only
"""turn_feedback.py — Feedback OK/Errore utente su risposte Metnos.

Loop di rinforzo esplicito (22/5/2026). L'utente preme:
- `ok`: rinforza il path usato. Se il turno e' partito da `multi_tool_paths`
  fast-path HIT, incrementa `uses` della entry corrispondente (segnale piu'
  forte = piu' alto rank).
- `error`: il path usato e' sbagliato. Se fast-path HIT, demote o cancella
  la entry; comunque marca il turno come negativo in
  `~/.local/share/metnos/turn_feedback.jsonl` (audit + base per future
  riformulazioni / retraining).

Non premere = nessun segnale (default neutro).

API pubblica:
    apply_feedback(turn_id, action, by="user") -> dict
    feedback_history(limit=100) -> list[dict]
    feedback_for_turn(turn_id) -> dict | None

Determinismo §7.9. Storage append-only JSONL.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from logging_setup import get_logger
import config as _C  # §7.11
log = get_logger(__name__)


_DATA_DIR = _C.PATH_USER_DATA
FEEDBACK_PATH = _DATA_DIR / "turn_feedback.jsonl"
TURNS_DIR = _DATA_DIR / "turns"

VALID_ACTIONS = frozenset({"ok", "error", "repeat"})


def _load_turn(turn_id: str) -> Optional[dict]:
    """Cerca il turno per `turn_id` nei file *.jsonl della turn_log dir.

    Lookup linear sui file recenti (ordinati da piu' nuovo): tipicamente il
    feedback arriva entro pochi minuti dal turno, quindi il primo file
    contiene il match.
    """
    if not TURNS_DIR.is_dir():
        return None
    for fp in sorted(TURNS_DIR.glob("*.jsonl"), reverse=True):
        try:
            with fp.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"' + turn_id not in line:
                        continue
                    try:
                        t = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if t.get("turn_id") == turn_id:
                        return t
        except OSError:
            continue
    return None


def _canonical_from_turn(turn: dict) -> Optional[str]:
    """Estrae la canonical_query dal primo step (usata da multi_tool_paths)."""
    steps = turn.get("steps") or []
    for s in steps:
        if isinstance(s, dict):
            cq = s.get("canonical_query")
            if cq:
                return cq.strip().lower()
    return None


def _was_fast_path_hit(turn: dict) -> bool:
    """True se il turno e' stato risolto via multi_tool_paths fast-path
    (nessun llm_in_tokens > 0 sugli step iniziali eccetto final_answer).
    Euristica: se tutti gli step pre-final hanno llm_latency_ms=0 e
    llm_in_tokens=0, e' stato playback puro.
    """
    steps = turn.get("steps") or []
    if not steps:
        return False
    non_final = [s for s in steps
                 if isinstance(s, dict) and s.get("chosen_tool") != "final_answer"]
    if not non_final:
        return False
    return all(
        (s.get("llm_in_tokens") or 0) == 0 and (s.get("llm_latency_ms") or 0) == 0
        for s in non_final
    )


# Soglia uses per promozione automatica candidate→active dopo feedback OK.
# Pattern: 3 feedback positivi indipendenti = signal robusto.
_PROMOTE_USES_THRESHOLD = 3


def _reinforce_path(canonical: str) -> dict:
    """Incrementa `uses` della entry multi_tool_paths con questa canonical.

    Se multiple entries con stessa canonical (path_shape diverso), incrementa
    quella con piu' uses (la dominante). Se cumulative uses raggiunge
    _PROMOTE_USES_THRESHOLD e state=candidate → promuove a active (signal
    utente forte e ripetuto bypassa l'aging passivo).
    """
    if not canonical:
        return {"action": "noop", "reason": "no_canonical"}
    try:
        from multi_tool_paths import MultiToolPathsDB
        store = MultiToolPathsDB()
    except Exception as ex:
        log.warning("turn_feedback: cannot open multi_tool_paths: %r", ex)
        return {"action": "noop", "reason": "store_unavailable"}
    try:
        with store._lock, store.conn:
            row = store.conn.execute(
                """SELECT id, uses, state FROM multi_tool_paths
                   WHERE canonical_query = ?
                   ORDER BY uses DESC LIMIT 1""",
                (canonical,),
            ).fetchone()
            if not row:
                return {"action": "noop", "reason": "canonical_not_in_cache"}
            row_id, uses, state = row
            new_uses = uses + 1
            promoted = False
            new_state = state
            if state == "candidate" and new_uses >= _PROMOTE_USES_THRESHOLD:
                new_state = "active"
                promoted = True
            store.conn.execute(
                """UPDATE multi_tool_paths
                   SET uses = ?, state = ? WHERE id = ?""",
                (new_uses, new_state, row_id),
            )
            out = {"action": "reinforced", "row_id": row_id,
                   "uses_before": uses, "uses_after": new_uses}
            if promoted:
                out["promoted"] = f"{state}→{new_state}"
            return out
    except Exception as ex:
        log.warning("turn_feedback: reinforce failed: %r", ex)
        return {"action": "noop", "reason": f"db_error: {ex}"}


def _demote_path(canonical: str) -> dict:
    """Cancella le entry multi_tool_paths con questa canonical_query.

    Approccio aggressivo: il signal "error" dell'utente e' forte; meglio
    cancellare il path sbagliato che lasciarlo a 'demoted' (potrebbe
    riemergere). L'utente potra' sempre re-imparare la pipeline corretta
    al prossimo turno passando dal planner LLM.
    """
    if not canonical:
        return {"action": "noop", "reason": "no_canonical"}
    try:
        from multi_tool_paths import MultiToolPathsDB
        store = MultiToolPathsDB()
    except Exception as ex:
        log.warning("turn_feedback: cannot open multi_tool_paths: %r", ex)
        return {"action": "noop", "reason": "store_unavailable"}
    try:
        with store._lock, store.conn:
            cur = store.conn.execute(
                "DELETE FROM multi_tool_paths WHERE canonical_query = ?",
                (canonical,),
            )
            n = cur.rowcount
            return {"action": "demoted",
                    "rows_deleted": n, "canonical": canonical}
    except Exception as ex:
        log.warning("turn_feedback: demote failed: %r", ex)
        return {"action": "noop", "reason": f"db_error: {ex}"}


def apply_feedback(turn_id: str, action: str, by: str = "user") -> dict:
    """Applica il feedback. Persistente in FEEDBACK_PATH e propaga gli
    effetti (rinforzo/demote di multi_tool_paths se applicabile).

    Ritorna dict con: turn_id, action, by, ts, effects (lista).
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be in {sorted(VALID_ACTIONS)}, got {action!r}")
    turn = _load_turn(turn_id)
    if turn is None:
        # Senza turno non possiamo applicare effects; salviamo solo il feedback.
        record = {
            "turn_id": turn_id, "action": action, "by": by,
            "ts": time.time(), "effects": [],
            "warning": "turn_not_found",
        }
        _append_feedback(record)
        return record

    canonical = _canonical_from_turn(turn)
    fast_path_hit = _was_fast_path_hit(turn)
    effects: list[dict] = []

    # Regola design (22/5/2026): il feedback agisce sulla CACHE fast-path,
    # non sui path LLM-generati. Razionale: un path appena generato dal
    # LLM e' incerto; promuoverlo/penalizzarlo al primo feedback umano
    # cementa pattern dubbi. Restano neutri (registrati in audit) — il
    # sistema dovra' osservare ripetizioni multiple prima di stabilizzare.
    if action == "ok":
        if canonical and fast_path_hit:
            # Rinforzo cache: uses+=1, promote candidate→active se ≥3.
            effects.append({"type": "reinforce_path",
                            **_reinforce_path(canonical)})
        else:
            effects.append({"type": "noop",
                            "reason": "ok_neutral_llm_path" if canonical
                                      else "ok_no_canonical"})
    elif action == "error":
        if canonical and fast_path_hit:
            # Demote: cancella la entry cache che ha prodotto la risposta
            # sbagliata. Il prossimo turno con query simile passera' dal
            # planner LLM (eventualmente con prompt updated nel frattempo).
            effects.append({"type": "demote_path",
                            **_demote_path(canonical)})
        else:
            effects.append({"type": "noop",
                            "reason": "error_neutral_llm_path" if canonical
                                      else "error_no_canonical"})

    record = {
        "turn_id": turn_id, "action": action, "by": by,
        "ts": time.time(),
        "canonical": canonical,
        "fast_path_hit": fast_path_hit,
        "effects": effects,
    }
    # Registriamo user_query + tool-sequence in entrambi i casi (ok / error)
    # cosi' `rejected_pipelines_for_query` puo' applicare LWW per
    # (query, pipeline_signature): un ok successivo annulla un err
    # precedente sulla stessa pipeline (caso utente preme ✗ per errore,
    # poi ↻, sistema rifa stesso path corretto, utente preme ✓ — la
    # pipeline DEVE tornare considerata valida).
    record["user_query"] = turn.get("user_query", "")
    steps = turn.get("steps") or []
    pipeline = [
        s.get("chosen_tool") for s in steps
        if isinstance(s, dict) and s.get("chosen_tool")
        and s.get("chosen_tool") != "final_answer"
    ]
    if pipeline:
        if action == "error":
            record["rejected_pipeline"] = pipeline
        else:  # ok
            record["approved_pipeline"] = pipeline

    # E12 feedback→demote (24/5/2026): su action="error" con pipeline nota,
    # controlla se uno dei tool ha superato la soglia ✗ consecutive. Il
    # count comprende il feedback CORRENTE (lookback storia + 1). Demote
    # solo synth non-protetti (ADR 0114 L3, enforcement in apply_feedback_ager).
    if action == "error" and pipeline:
        try:
            from runtime_settings import feedback_error_demote_threshold
            from executor_aging import apply_feedback_ager
            threshold = feedback_error_demote_threshold()
        except Exception as ex:
            log.warning("turn_feedback: demote setup failed: %r", ex)
            threshold = 0
        if threshold > 0:
            for tool_name in dict.fromkeys(pipeline):  # dedup preservando ordine
                prior = count_consecutive_errors_for_tool(tool_name)
                consecutive = prior + 1  # include feedback corrente
                if consecutive >= threshold:
                    try:
                        out = apply_feedback_ager(
                            tool_name, consecutive_errors=consecutive)
                    except Exception as ex:
                        log.warning(
                            "turn_feedback: apply_feedback_ager(%s) failed: %r",
                            tool_name, ex)
                        out = {"action": "noop",
                               "reason": f"ager_error: {ex}"}
                    effects.append({"type": "feedback_demote", **out})

    # ── Autopath feedback hook (engine v2) ────────────────────────────
    # Dispatcha verdict a engine.autopath.record_feedback (flusso vivo).
    # Bonifica 2026-05-28: rimosso il ramo legacy V2=0 (praxis.sqlite vuoto,
    # store dismesso con Engine v2). Engine v2 è l'unico flusso decisionale.
    # Vedi decisions/_metis_wiring_consolidation.md §3.
    try:
        _verdict_map = {"ok": "ok", "error": "fail", "repeat": "repeat"}
        _verdict = _verdict_map.get(action)
        if _verdict:
            from engine.autopath import record_feedback as _rec_fb
            _out = _rec_fb(turn_id, _verdict)
            if _out.get("ok"):
                effects.append({"type": "praxis_feedback", **_out})
    except Exception as ex:
        log.warning("turn_feedback: autopath hook failed: %r", ex)

    _append_feedback(record)
    return record


def _append_feedback(record: dict) -> None:
    """Append-only JSONL."""
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def feedback_for_turn(turn_id: str) -> Optional[dict]:
    """Ultimo feedback per il turn_id (LWW). None se nessuno."""
    if not FEEDBACK_PATH.is_file():
        return None
    last = None
    with FEEDBACK_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or turn_id not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("turn_id") == turn_id:
                last = rec
    return last


def count_consecutive_errors_for_query(user_query: str,
                                        *, lookback: int = 200) -> int:
    """Conteggio feedback ✗ consecutive (no ✓ in mezzo) per `user_query`.

    Usato per escalation strato 2 (E.3): se >=2, il negative example nel
    planner prompt diventa HARD CONSTRAINT (wording piu' severo + suggerisce
    request_new_executor/frontier). LWW: un ✓ resetta il counter a 0.
    """
    if not user_query or not FEEDBACK_PATH.is_file():
        return 0
    needle = user_query.strip().lower()
    with FEEDBACK_PATH.open(encoding="utf-8") as fh:
        lines = fh.readlines()
    count = 0
    # Scan dal piu' recente all'indietro: stop al primo ✓ (resetta).
    for line in reversed(lines[-lookback:]):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        q = rec.get("user_query") or ""
        if q.strip().lower() != needle:
            continue
        if rec.get("action") == "ok":
            break  # un ok resetta il counter consecutivo
        if rec.get("action") == "error":
            count += 1
    return count


def count_consecutive_errors_for_tool(tool_name: str,
                                       *, lookback: int = 200) -> int:
    """Conteggio feedback ✗ consecutive (cross-query) per `tool_name`.

    Usato da E12 feedback→demote: dopo N ✗ consecutive sullo stesso tool,
    l'executor viene demoted. LWW: un ✓ su un feedback che include
    `tool_name` nella `approved_pipeline` resetta il counter (signal che
    il tool e' valido in qualche altro contesto).

    Scan dal piu' recente all'indietro, stop al primo ✓ che menziona
    `tool_name`. ✗ che menzionano `tool_name` incrementano. Feedback su
    altri tool sono ignorati (non interrompono la sequenza).
    """
    if not tool_name or not FEEDBACK_PATH.is_file():
        return 0
    with FEEDBACK_PATH.open(encoding="utf-8") as fh:
        lines = fh.readlines()
    count = 0
    for line in reversed(lines[-lookback:]):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = rec.get("action")
        if action == "ok":
            pipeline = rec.get("approved_pipeline") or []
            if tool_name in pipeline:
                break  # ✓ sullo stesso tool resetta
        elif action == "error":
            pipeline = rec.get("rejected_pipeline") or []
            if tool_name in pipeline:
                count += 1
    return count


def rejected_pipelines_for_query(user_query: str,
                                  *, lookback: int = 200) -> list[list[str]]:
    """Pipeline (tool sequence) rifiutate dall'utente per una query.

    LWW per (query, pipeline_signature): un feedback `ok` su una pipeline
    annulla qualunque `error` PRECEDENTE sulla stessa pipeline per la
    stessa query. Caso edge utente 22/5/2026: preme ✗ per errore, poi
    ↻, sistema rifa stesso path (legittimo), preme ✓ — la pipeline non
    deve restare in rejected.

    Match query: case-insensitive trimmed exact.
    """
    if not user_query:
        return []
    needle = user_query.strip().lower()
    if not FEEDBACK_PATH.is_file():
        return []
    with FEEDBACK_PATH.open(encoding="utf-8") as fh:
        lines = fh.readlines()
    # Scan in ordine cronologico (oldest first): l'ultimo record per
    # (query, signature) vince (LWW).
    latest: dict[str, str] = {}  # sig → last action ("ok"|"error")
    pipeline_by_sig: dict[str, list[str]] = {}
    for line in lines[-lookback:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        q = rec.get("user_query") or ""
        if q.strip().lower() != needle:
            continue
        # Cerca pipeline in entrambe le chiavi (approved_pipeline o
        # rejected_pipeline): registriamo entrambi i tipi di feedback con
        # la pipeline, cosi' LWW funziona.
        pipeline = (rec.get("rejected_pipeline") or
                    rec.get("approved_pipeline") or [])
        if not pipeline:
            continue
        sig = ">".join(pipeline)
        latest[sig] = rec.get("action") or ""
        pipeline_by_sig[sig] = pipeline
    # Ritorna solo pipeline il cui ULTIMO record e' "error".
    return [pipeline_by_sig[sig] for sig, act in latest.items()
            if act == "error"]


def reset_rejected_for_query(user_query: str) -> int:
    """Compensa demote precedenti per `user_query` appending feedback `ok`
    LWW per ogni pipeline che era in stato `error`. Usato quando l'utente
    sceglie "ritenta" dopo escalation strato 3 e la nuova esecuzione SUCCESS:
    le pipeline prima rifiutate vanno ri-promosse a OK.

    Ritorna numero di pipeline rese OK.
    Universal §7.9: scan + LWW append, no LLM.
    """
    if not user_query:
        return 0
    rejected = rejected_pipelines_for_query(user_query)
    if not rejected:
        return 0
    import time as _t
    count = 0
    for pipeline in rejected:
        sig = ",".join(pipeline)
        rec = {
            "ts": _t.time(),
            "turn_id": f"strato3-retry-{int(_t.time()*1000)}",
            "action": "ok",
            "by": "strato3_retry_success",
            "user_query": user_query,
            "approved_pipeline": pipeline,
            "signature": sig,
        }
        _append_feedback(rec)
        count += 1
    return count


def feedback_history(limit: int = 100) -> list[dict]:
    """Ultimi `limit` feedback in ordine cronologico inverso (newest first)."""
    if not FEEDBACK_PATH.is_file():
        return []
    out: list[dict] = []
    with FEEDBACK_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(out))[:limit]
