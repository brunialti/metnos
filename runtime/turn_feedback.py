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
log = get_logger(__name__)


_DATA_DIR = Path.home() / ".local" / "share" / "metnos"
FEEDBACK_PATH = _DATA_DIR / "turn_feedback.jsonl"
TURNS_DIR = _DATA_DIR / "turns"

VALID_ACTIONS = frozenset({"ok", "error"})


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
