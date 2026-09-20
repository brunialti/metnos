"""engine/terminator.py — Protocol + SimpleTerminator (default).

Terminator chiude il turno quando Engine non può risolvere. Honest fail:
spiega all'utente cosa è successo e suggerisce un'azione.

§2.8 No silent failure: mai pretendere "answer" su un'esecuzione fallita.
Terminator produce final_kind='answer' con messaggio che ammette il
limite + suggested_action concreto.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .types import (Intent, RunResult, OPERATIONAL_ERROR_CLASSES,
                    result_error_classes, result_error_detail)

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
        # W1 learning-loop (ADR 0185): lacuna RICORRENTE → proposta di
        # capacità (change_intent PROPOSED, triage umano su /admin/changes).
        # Nel choke-point: copre SimpleTerminator E MetisTerminator.
        try:
            row = conn.execute(
                "SELECT n_seen FROM lacune WHERE lacuna_id = ?",
                (lid,)).fetchone()
            n_seen = int(row[0]) if row else 1
            import learning_loop as _ll
            _ll.propose_from_lacuna(
                lacuna_id=lid, query=query, verb=intent.verb or "",
                object_=intent.object or "", error_class=error_class,
                n_seen=n_seen)
        except Exception as ex:  # noqa: BLE001 — mai rompere la risposta
            log.debug("learning_loop hook noop: %r", ex)
        conn.close()
    except Exception as ex:
        log.warning("terminator: record_lacuna failed: %r", ex)
    return lid


# ── SimpleTerminator ──────────────────────────────────────────────────────

# §11: cause/azione user-facing risolte via DB i18n nella lingua dell'istanza
# (prima erano stringhe IT hardcoded nel final_text mostrato all'utente).
_CAUSE_KEYS = {
    "wrong_tool": ("MSG_TERM_WRONG_TOOL_CAUSE", "MSG_TERM_WRONG_TOOL_ACTION"),
    "wrong_args": ("MSG_TERM_WRONG_ARGS_CAUSE", "MSG_TERM_WRONG_ARGS_ACTION"),
    "missing_input": ("MSG_TERM_MISSING_INPUT_CAUSE", "MSG_TERM_MISSING_INPUT_ACTION"),
    "out_of_scope": ("MSG_TERM_OUT_OF_SCOPE_CAUSE", "MSG_TERM_OUT_OF_SCOPE_ACTION"),
}

# Azioni specifiche per codici executor strutturati. La classe di recovery
# (es. ``wrong_args``) e' volutamente piu' larga del problema concreto: quando
# l'executor conosce la causa, non chiedere di nuovo dettagli che l'utente ha
# gia' fornito.
_ERROR_ACTION_KEYS = {
    "ERR_PATH_NOT_FOUND": "MSG_TERM_PATH_NOT_FOUND_ACTION",
}


def _first_step_failure(failed_run: Optional[RunResult]) -> tuple[str, str]:
    """Errore CONCRETO del primo step fallito (§2.8). Il template generico
    per `error_class` ("Pipeline malformata") MASCHERA l'errore reale e
    azionabile che l'executor sa dare (es. "Nessuna directory foto trovata.
    Crea X o passa base_path" — bug live 8/6: l'utente vedeva "malformata" e
    pensava a una regressione, mentre il NAS era smontato). Ritorna "" se
    nessuno step ha un messaggio d'errore utile. Già localizzato dall'executor
    via i18n (§11) → nessuna stringa hardcoded qui."""
    if not failed_run or not getattr(failed_run, "steps", None):
        return "", ""
    for s in failed_run.steps:
        r = getattr(s, "result", None)
        if not isinstance(r, dict) or r.get("ok") is not False:
            continue
        classes = result_error_classes(r)
        error_class = classes[0] if classes else ""
        err = result_error_detail(r)
        if err:
            return err, error_class
    return "", ""


def _first_step_error(failed_run: Optional[RunResult]) -> str:
    """Compatibilita' interna: solo il testo della prima failure concreta."""
    return _first_step_failure(failed_run)[0]


def _first_step_error_code(failed_run: Optional[RunResult]) -> str:
    """Primo ``error_code`` strutturato, top-level o per-item.

    Separato dal testo localizzato: le decisioni di protocollo non devono
    dipendere dal wording del backend o dalla lingua dell'istanza.
    """
    if not failed_run or not getattr(failed_run, "steps", None):
        return ""
    for step in failed_run.steps:
        result = getattr(step, "result", None)
        if not isinstance(result, dict) or result.get("ok") is not False:
            continue
        candidates = [result]
        failed = result.get("failed")
        if isinstance(failed, list):
            candidates.extend(item for item in failed if isinstance(item, dict))
        for item in candidates:
            code = item.get("error_code")
            if isinstance(code, str) and code.strip():
                return code.strip()
    return ""


class SimpleTerminator:
    """Default: errore concreto dello step fallito (§2.8), altrimenti template
    fisso per classe errore + record lacuna."""

    def explain(self, *, query: str, intent: Intent,
                failed_run: Optional[RunResult],
                error_class: str = "") -> TerminatorResponse:
        from messages import get as _msg
        ck, ak = _CAUSE_KEYS.get(error_class, _CAUSE_KEYS["out_of_scope"])
        action = _msg(ak)
        # §2.8: se uno step ha fallito con un errore concreto/azionabile, mostra
        # QUELLO come causa invece del generico per-classe (che lo mascherava).
        step_err, step_error_class = _first_step_failure(failed_run)
        step_error_code = _first_step_error_code(failed_run)
        # Backend traces are diagnostics, not localized user-facing text.
        # A missing remote capability already has a structured class, so use
        # the existing localized out-of-scope wording instead of exposing the
        # provider traceback (or coupling behavior to its prose).
        if step_error_class == "capability_missing":
            step_err = ""
        action_key = _ERROR_ACTION_KEYS.get(step_error_code)
        if action_key:
            action = _msg(action_key)
        elif step_error_class in OPERATIONAL_ERROR_CLASSES:
            action = _msg("MSG_CHAT_FB_RETRY")
        cause = step_err if step_err else _msg(ck)
        text = _msg("MSG_TERM_WRAPPER", cause=cause, action=action)
        # Registra la classe executor reale: ``network`` non deve diventare una
        # falsa lacuna ``out_of_scope`` candidata alla sintesi di un tool.
        recorded_class = step_error_class or error_class
        lid = _record_lacuna(query, intent, recorded_class, cause, action)
        return TerminatorResponse(
            final_text=text, root_cause=cause,
            suggested_action=action, lacuna_id=lid,
        )


# ── Factory ───────────────────────────────────────────────────────────────

def get_terminator() -> Terminator:
    from . import get_engine_name
    name = get_engine_name()
    # v3 drop-in di metis (nessun terminator_v3): prod v3 usa MetisTerminator.
    if name in ("metis", "v3"):
        try:
            from . import terminator_metis
            return terminator_metis.MetisTerminator()
        except Exception as ex:
            log.warning("MetisTerminator unavailable (%r), fallback simple", ex)
    return SimpleTerminator()
