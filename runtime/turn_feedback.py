# SPDX-License-Identifier: AGPL-3.0-only
"""turn_feedback.py — Feedback OK/Errore utente su risposte Metnos.

Loop di rinforzo esplicito (22/5/2026). L'utente preme:
- `ok`: rinforza il path usato (verdict propagato a engine.autopath).
- `error`: il path usato e' sbagliato: marca il turno come negativo in
  `~/.local/share/metnos/turn_feedback.jsonl` (audit + rejected pipelines
  per il PLANNER), propaga il verdict a engine.autopath e — dopo N ✗
  consecutive sullo stesso tool — demote l'executor synth (E12).

Non premere = nessun segnale (default neutro).

NB (11/6/2026): rimossi rinforzo/demote della cache `multi_tool_paths`
(ADR 0150 ritirato — il meccanismo vivo e' engine/fastpath L0 + autopath,
che riceve il verdict via l'hook in coda a `apply_feedback`).

API pubblica:
    apply_feedback(turn_id, action, by="user") -> dict

Determinismo §7.9. Storage append-only JSONL.
"""
from __future__ import annotations

import json
import time
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
    """Estrae la canonical_query dal primo step (campo audit del record,
    consumato dall'adapter change_intents `user_feedback`)."""
    steps = turn.get("steps") or []
    for s in steps:
        if isinstance(s, dict):
            cq = s.get("canonical_query")
            if cq:
                return cq.strip().lower()
    return None


def _was_fast_path_hit(turn: dict) -> bool:
    """True se il turno e' stato risolto da un replay fast-path/cache
    (nessun llm_in_tokens > 0 sugli step iniziali eccetto final_answer).
    Euristica: se tutti gli step pre-final hanno llm_latency_ms=0 e
    llm_in_tokens=0, e' stato playback puro. Campo audit del record.
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


def apply_feedback(turn_id: str, action: str, by: str = "user") -> dict:
    """Applica il feedback. Persistente in FEEDBACK_PATH e propaga gli
    effetti (E12 demote executor, verdict a engine.autopath).

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

    # ── Fastpath L0 valve (12/6/2026, §2.8) ───────────────────────────
    # Un ✗ cancella la riga L0 della query del turno (qualunque layer
    # l'abbia servito: il record L0 nasce dallo stesso piano appena
    # eseguito). Senza valvola un fastpath sbagliato e colpito rinfresca
    # last_used (l'aging non lo vede) e L0, vincendo in cascata, impedisce
    # al piano pieno di ri-succedere → immortale fino al delete admin.
    # LWW simmetrico con autopath: si ri-crea al prossimo turno-successo.
    if action == "error":
        try:
            from engine.fastpath import delete_by_query as _fp_delete
            _n_fp = _fp_delete(turn.get("user_query") or "")
            if _n_fp:
                effects.append({"type": "fastpath_deleted", "rows": _n_fp})
        except Exception as ex:
            log.warning("turn_feedback: fastpath delete hook failed: %r", ex)

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


