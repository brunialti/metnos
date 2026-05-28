"""engine/terminator.py — Protocol + SimpleTerminator (default).

Terminator chiude il turno quando Engine non può risolvere. Honest fail:
spiega all'utente cosa è successo e suggerisce un'azione.

§2.8 No silent failure: mai pretendere "answer" su un'esecuzione fallita.
Terminator produce final_kind='answer' con messaggio che ammette il
limite + suggested_action concreto.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .types import Intent, RunResult

log = logging.getLogger(__name__)


@dataclass
class TerminatorResponse:
    final_text: str
    root_cause: str = ""
    suggested_action: str = ""
    lacuna_id: str = ""


# ── Protocol ──────────────────────────────────────────────────────────────

class Terminator(Protocol):
    def explain(self, *, query: str, intent: Intent,
                failed_run: Optional[RunResult],
                error_class: str = "") -> TerminatorResponse: ...


# ── Storage lacune (audit ricorrente) ─────────────────────────────────────

_DB_INIT_DONE = False


def _db_path() -> Path:
    import config as _C
    return _C.PATH_USER_DATA / "terminator_log.sqlite"


def _ensure_db() -> sqlite3.Connection:
    """DDL idempotent ad ogni call."""
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    if True:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS lacune (
            lacuna_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            query TEXT NOT NULL,
            intent_verb TEXT,
            intent_object TEXT,
            error_class TEXT,
            root_cause TEXT,
            suggested_action TEXT,
            n_seen INTEGER DEFAULT 1,
            last_seen TEXT
        );
        CREATE INDEX IF NOT EXISTS lacune_n_seen ON lacune(n_seen DESC);
        """)
        conn.commit()
    return conn


def _record_lacuna(query: str, intent: Intent, error_class: str,
                    root_cause: str, suggested_action: str) -> str:
    """Registra/aggiorna lacuna. Ritorna lacuna_id."""
    import hashlib
    sig = f"{intent.verb}|{intent.object}|{error_class}|{query.lower()[:100]}"
    lid = hashlib.sha256(sig.encode()).hexdigest()[:16]
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        conn = _ensure_db()
        conn.execute("""
            INSERT INTO lacune(lacuna_id, ts, query, intent_verb, intent_object,
                                error_class, root_cause, suggested_action,
                                last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lacuna_id) DO UPDATE SET
                n_seen = lacune.n_seen + 1,
                last_seen = excluded.last_seen
        """, (lid, ts, query, intent.verb, intent.object, error_class,
              root_cause, suggested_action, ts))
        conn.commit()
        conn.close()
    except Exception as ex:
        log.warning("terminator: record_lacuna failed: %r", ex)
    return lid


# ── SimpleTerminator ──────────────────────────────────────────────────────

_CAUSE_TEMPLATES = {
    "wrong_tool": ("Strumento sbagliato per la richiesta",
                    "Riformula con dettagli o usa /engine metis|frontier"),
    "wrong_args": ("Pipeline malformata o argomenti insufficienti",
                    "Aggiungi dettagli concreti (percorso, nome, periodo)"),
    "missing_input": ("Dati o backend non disponibili",
                       "Verifica path/indice o configura il provider"),
    "out_of_scope": ("Richiesta fuori scope del sistema",
                      "Richiede azione fisica (es. condivisione posizione) "
                      "o capability esterna non installata"),
}


class SimpleTerminator:
    """Default: template fisso per classe errore + record lacuna."""

    def explain(self, *, query: str, intent: Intent,
                failed_run: Optional[RunResult],
                error_class: str = "") -> TerminatorResponse:
        cause, action = _CAUSE_TEMPLATES.get(
            error_class, _CAUSE_TEMPLATES["out_of_scope"])
        text = f"Non posso risolvere: {cause}. Per procedere: {action}."
        lid = _record_lacuna(query, intent, error_class, cause, action)
        return TerminatorResponse(
            final_text=text, root_cause=cause,
            suggested_action=action, lacuna_id=lid,
        )


# ── Factory ───────────────────────────────────────────────────────────────

def get_terminator() -> Terminator:
    from . import get_engine_name
    name = get_engine_name()
    if name == "metis":
        try:
            from . import terminator_metis
            return terminator_metis.MetisTerminator()
        except Exception as ex:
            log.warning("MetisTerminator unavailable (%r), fallback simple", ex)
    return SimpleTerminator()
