"""runtime.admin.promotions_review — form aggregator unico per le decisioni
admin sulle promozioni synth (E3, 11/5/2026).

Pattern (Roberto, MEMORY 11/5 e the design guide ADR 0090):
- Riuso engine `get_inputs` invece di pagine HTTP separate + callback
  Telegram per-item.
- UN solo dialog con 3 step-group:
    * Promossi in grazia (`promoted_grace`) — opzioni
      `Conferma promozione | Rollback | Skip`.
    * Da decidere (`review_needed` con `needs_human_review=1`) — opzioni
      `Promuovi ora | Archivia | Skip`.
    * Bocciati recenti (`archived` finestra 7 giorni) — opzioni
      `Conferma archiviazione | Resurrect a pending | Skip`.
- Submit unico atomico (transazione SQLite); audit JSONL UNICO per
  sessione.

Determinismo §7.9: pure deterministic. La build del dialog interroga
sqlite + costruisce un dict ADR 0090-shape; l'apply applica le decisioni
via API esistenti del state DB + audit_append.

API esposta:
    build_review_dialog(*, max_per_group=10, archived_days=7) -> dict
    apply_review_decisions(values: dict) -> dict
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys as _sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as _C  # §7.11

# Cap N per group (paginazione: se >max, mostra top-max + nota).
DEFAULT_MAX_PER_GROUP = 10
DEFAULT_ARCHIVED_DAYS = 7

# Opzioni per gruppo. La label e' user-facing (passata al render `choice`);
# il value e' la chiave interna che `apply_review_decisions` interpreta.
_OPTIONS_PROMOTED_GRACE = (
    ("confirm", "Conferma promozione"),
    ("rollback", "Rollback"),
    ("skip", "Skip"),
)
_OPTIONS_REVIEW_NEEDED = (
    ("promote_now", "Promuovi ora"),
    ("archive", "Archivia"),
    ("skip", "Skip"),
)
_OPTIONS_ARCHIVED = (
    ("confirm_archive", "Conferma archiviazione"),
    ("resurrect", "Resurrect a pending"),
    ("skip", "Skip"),
)


def _group_id(state: str, proposal_id: str) -> str:
    """Var-name unico per ogni step del dialog. Pattern: `<state>__<proposal_id>`.

    Necessario perche' `get_inputs` indicizza per `var` dentro `values`
    raccolti. Lo state prefix permette al callback di dispatchare per
    gruppo senza dover ri-interrogare il DB per ogni var.
    """
    return f"{state}__{proposal_id}"


def _parse_var(var: str) -> tuple[str, str]:
    """Inversa di `_group_id`. Ritorna `(state, proposal_id)`."""
    if "__" not in var:
        return ("", var)
    state, _, pid = var.partition("__")
    return (state, pid)


def _format_item_prompt(row: dict, state: str) -> str:
    """Prompt user-facing per UN item della review form.

    Stile compatto (1-2 righe): `<name>` + breve diff dell'esempio
    pratico. La UI HTML mostra il practical_example completo dentro
    una card collassabile separata.
    """
    name = row.get("name") or "?"
    pid = row.get("proposal_id") or "?"
    promoted = row.get("promoted_at") or row.get("created_at") or ""
    # Truncate timestamp ai primi 10 char (YYYY-MM-DD).
    promoted_short = promoted[:10] if promoted else "-"
    if state == "promoted_grace":
        return f"`{name}` (id {pid[:24]}, promosso {promoted_short})"
    if state == "review_needed":
        return f"`{name}` (id {pid[:24]}, in attesa di review)"
    if state == "archived":
        archived = row.get("archived_at") or ""
        archived_short = archived[:10] if archived else "-"
        return f"`{name}` (id {pid[:24]}, archiviato {archived_short})"
    return f"`{name}` (id {pid[:24]})"


def _build_choice_step(row: dict, state: str,
                        options: tuple[tuple[str, str], ...]) -> dict:
    """Costruisce uno step `choice` per UN item della review form."""
    pid = row.get("proposal_id") or ""
    return {
        "var": _group_id(state, pid),
        "prompt": _format_item_prompt(row, state),
        "schema": {
            "kind": "choice",
            "choices": [label for _value, label in options],
            "values": [value for value, _label in options],
        },
        "optional": False,
        "default": options[-1][1],  # ultimo = "Skip" → default safe §7.9
        # Hint UI: nome dell'executor + practical_example per render card.
        "_meta": {
            "name": row.get("name") or "?",
            "proposal_id": pid,
            "practical_example": row.get("practical_example") or "",
            "state": state,
        },
    }


def _audit_dir() -> Path:
    env = os.environ.get("METNOS_PROMOTER_AUDIT_DIR")
    if env:
        return Path(env)
    return _C.PATH_USER_DATA / "synth_audit"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_review_dialog(
    *,
    max_per_group: int = DEFAULT_MAX_PER_GROUP,
    archived_days: int = DEFAULT_ARCHIVED_DAYS,
) -> dict:
    """Scansiona `promoter.sqlite` e costruisce un payload `get_inputs`
    (ADR 0090) con 3 step-group.

    Ritorna shape:
        {
            "dialog_id": "<uuid hex16>",
            "title": "Review promozioni synth",
            "description": "...",
            "dialog": [<step1>, <step2>, ...],
            "on_complete": {"type": "apply_review_decisions"},
            "groups": {
                "promoted_grace": {"count": N, "more": M},
                "review_needed": {"count": N, "more": M},
                "archived":      {"count": N, "more": M},
            }
        }

    `groups[state].more` = item NON inclusi (paginazione). Se >0, la UI
    mostra "(+M altri non visualizzati)".
    """
    from jobs.promoter_state import list_by_state, archived_within_days

    grace_rows = list_by_state(["promoted_grace"], limit=500)
    review_rows = [r for r in list_by_state(["review_needed"], limit=500)
                   if (r.get("needs_human_review") or 0) == 1]
    archived_rows = archived_within_days(archived_days, limit=500)

    steps: list[dict] = []
    groups: dict[str, dict] = {}

    for state, rows, options in (
        ("promoted_grace", grace_rows, _OPTIONS_PROMOTED_GRACE),
        ("review_needed", review_rows, _OPTIONS_REVIEW_NEEDED),
        ("archived", archived_rows, _OPTIONS_ARCHIVED),
    ):
        total = len(rows)
        keep = rows[:max_per_group]
        for r in keep:
            steps.append(_build_choice_step(r, state, options))
        groups[state] = {
            "count": len(keep),
            "more": max(0, total - max_per_group),
            "total": total,
        }

    dialog_id = uuid.uuid4().hex[:16]
    return {
        "dialog_id": dialog_id,
        "title": "Review promozioni synth",
        "description": (
            "Decisioni admin per le promozioni in attesa. Le scelte "
            "vengono applicate al submit in una sola transazione."
        ),
        "dialog": steps,
        "on_complete": {"type": "apply_review_decisions"},
        "groups": groups,
    }


def _audit_append_session(events: list[dict], *, session_id: str) -> Path:
    """Audit JSONL UNICO per sessione review.

    File: `<audit_dir>/promoter_review_<YYYY-MM-DD>_<session_id>.jsonl`.
    Una sola scrittura batch (apri+fsync una volta sola).
    """
    d = _audit_dir()
    d.mkdir(parents=True, exist_ok=True)
    date_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    audit_path = d / f"promoter_review_{date_iso}_{session_id}.jsonl"
    with open(audit_path, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True,
                                default=str) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    return audit_path


def _label_to_value(label: str, options: tuple[tuple[str, str], ...]) -> str:
    """Mappa la label user-facing al value interno della tabella opzioni.

    Tollerante a case + spazi: il rendering form HTML potrebbe normalizzare.
    Ritorna "" se la label non matcha (caller decide se skippare).
    """
    norm = (label or "").strip().lower()
    for value, lbl in options:
        if lbl.strip().lower() == norm:
            return value
    # Fallback: la label e' gia' il value (caller passa direttamente).
    for value, _lbl in options:
        if value == norm:
            return value
    return ""


def _options_for(state: str) -> tuple[tuple[str, str], ...]:
    if state == "promoted_grace":
        return _OPTIONS_PROMOTED_GRACE
    if state == "review_needed":
        return _OPTIONS_REVIEW_NEEDED
    if state == "archived":
        return _OPTIONS_ARCHIVED
    return ()


def apply_review_decisions(values: dict) -> dict:
    """Applica le scelte raccolte dal form in batch atomico.

    `values` shape (dict raccolto dal dialog completato):
        {
            "promoted_grace__<id1>": "Conferma promozione",
            "promoted_grace__<id2>": "Rollback",
            "review_needed__<id3>": "Promuovi ora",
            ...
        }

    Comportamento per choice value (mappato da label tramite tabella
    opzioni):
        - `confirm`         → `mark_finalized` (state promoted_grace).
        - `rollback`        → `rollback_promotion`.
        - `promote_now`     → `promote_to_catalog` + `upsert_promoted_grace`.
        - `archive`         → `archive_review_needed`.
        - `confirm_archive` → no-op (gia' archived; audit only).
        - `resurrect`       → `resurrect_from_archive`.
        - `skip`            → no-op.

    Atomicita': l'apply itera in ordine var-name; ogni step e' un punto
    di failure dichiarato (per-row, niente abort globale). Audit JSONL
    UNICO per sessione (campo `session_id` come prefix del filename) e
    flush singolo al termine.

    Ritorna shape:
        {
            "ok": bool,
            "applied": int,        # decisioni eseguite (non skip)
            "skipped": int,
            "failed": int,
            "session_id": str,
            "audit_path": str,
            "by_action": {action: count, ...},
        }
    """
    from jobs.promoter_state import (
        mark_finalized, archive_review_needed, resurrect_from_archive,
    )
    from jobs.promoter_rollback import rollback_promotion

    session_id = uuid.uuid4().hex[:12]
    events: list[dict] = []
    applied = 0
    skipped = 0
    failed = 0
    by_action: dict[str, int] = {}

    for var, raw_choice in (values or {}).items():
        if not isinstance(var, str):
            continue
        state, pid = _parse_var(var)
        if not state or not pid:
            continue
        options = _options_for(state)
        if not options:
            continue
        # Risolvi la scelta utente (puo' essere label "Promuovi ora" o
        # value "promote_now" — accettiamo entrambi).
        value = _label_to_value(str(raw_choice), options)
        if not value:
            failed += 1
            events.append({
                "ts": _now_iso(),
                "session_id": session_id,
                "proposal_id": pid,
                "state": state,
                "choice": str(raw_choice)[:80],
                "action": "unknown_choice",
                "ok": False,
            })
            continue
        if value == "skip":
            skipped += 1
            continue

        ev: dict = {
            "ts": _now_iso(),
            "session_id": session_id,
            "proposal_id": pid,
            "state": state,
            "choice": value,
        }
        try:
            if state == "promoted_grace" and value == "confirm":
                ok = mark_finalized(pid)
                ev["action"] = "finalized"
                ev["ok"] = ok
            elif state == "promoted_grace" and value == "rollback":
                res = rollback_promotion(pid)
                ev["action"] = "rolled_back"
                ev["ok"] = bool(res.get("ok"))
                if not ev["ok"]:
                    ev["error"] = res.get("error")
            elif state == "review_needed" and value == "promote_now":
                # Per ora: marca review_needed → finalized direttamente
                # NON e' supportato perche' manca lo step di promote
                # (no rollback blob). Quindi: triggeriamo un re-eval
                # alla prossima fire del task `promoter` segnando la
                # row pending. Audit deciso esplicitamente.
                ok = _mark_review_to_pending(pid)
                ev["action"] = "marked_pending_for_repromote"
                ev["ok"] = ok
            elif state == "review_needed" and value == "archive":
                ok = archive_review_needed(pid)
                ev["action"] = "archived_from_review"
                ev["ok"] = ok
            elif state == "archived" and value == "confirm_archive":
                # No-op deterministico: la row e' gia' archived.
                ev["action"] = "archive_confirmed"
                ev["ok"] = True
            elif state == "archived" and value == "resurrect":
                ok = resurrect_from_archive(pid)
                ev["action"] = "resurrected_to_review"
                ev["ok"] = ok
            else:
                ev["action"] = "no_action_for_state_value"
                ev["ok"] = False
                failed += 1
                events.append(ev)
                continue
        except Exception as ex:  # noqa: BLE001
            ev["action"] = ev.get("action") or "exception"
            ev["ok"] = False
            ev["error"] = str(ex)[:200]
            failed += 1
            events.append(ev)
            continue
        if ev["ok"]:
            applied += 1
        else:
            failed += 1
        by_action[ev["action"]] = by_action.get(ev["action"], 0) + 1
        events.append(ev)

    # Audit JSONL UNICO per sessione (anche se events e' vuoto, lasciamo
    # traccia che un submit e' stato fatto).
    summary_ev = {
        "ts": _now_iso(),
        "session_id": session_id,
        "action": "review_session_summary",
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "by_action": dict(by_action),
        "total_decisions": len(values or {}),
    }
    events.insert(0, summary_ev)
    audit_path = _audit_append_session(events, session_id=session_id)

    return {
        "ok": failed == 0,
        "applied": applied,
        "skipped": skipped,
        "failed": failed,
        "session_id": session_id,
        "audit_path": str(audit_path),
        "by_action": dict(by_action),
    }


def _mark_review_to_pending(proposal_id: str) -> bool:
    """Inverso parziale di `upsert_review_needed`: rimuove flag review.

    Il task `promoter` rivedra' la proposta al prossimo fire (in stato
    'pending', se il JSON e' ancora in synt_proposals dir).
    """
    from pathlib import Path as _Path
    db_env = os.environ.get("METNOS_PROMOTER_DB")
    db = _Path(db_env) if db_env else (
        _C.PATH_USER_DATA / "promoter.sqlite"
    )
    if not db.exists():
        return False
    conn = sqlite3.connect(str(db), timeout=30.0)
    try:
        row = conn.execute(
            "SELECT state FROM proposal_promote WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return False
        if (row[0] or "") != "review_needed":
            return False
        conn.execute(
            "UPDATE proposal_promote SET state = 'pending', "
            "needs_human_review = 0 WHERE proposal_id = ?",
            (proposal_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


__all__ = [
    "build_review_dialog",
    "apply_review_decisions",
    "DEFAULT_MAX_PER_GROUP",
    "DEFAULT_ARCHIVED_DAYS",
]
