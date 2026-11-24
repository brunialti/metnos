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
        -- The days a gap was seen: what a window counts. `n_seen` is a count
        -- for the whole life of the row and cannot be cut to a window.
        CREATE TABLE IF NOT EXISTS lacune_days (
            lacuna_id TEXT NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY (lacuna_id, day)
        );
        INSERT OR IGNORE INTO lacune_days(lacuna_id, day)
            SELECT lacuna_id, substr(last_seen, 1, 10) FROM lacune
             WHERE last_seen IS NOT NULL;
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
                -- The same sentence counts once a day: a retry, or a
                -- recurring task failing every hour, is one request.
                n_seen = lacune.n_seen + (
                    substr(lacune.last_seen, 1, 10) <> substr(excluded.last_seen, 1, 10)),
                last_seen = excluded.last_seen
        """, (lid, ts, query, intent.verb, intent.object, error_class,
              root_cause, suggested_action, ts))
        conn.execute("INSERT OR IGNORE INTO lacune_days(lacuna_id, day) VALUES (?, ?)",
                     (lid, ts[:10]))
        conn.commit()
        # W1 learning-loop (ADR 0185): lacuna RICORRENTE → proposta di
        # capacità (change_intent PROPOSED, triage umano su /admin/changes).
        # Nel choke-point: copre SimpleTerminator E MetisTerminator.
        try:
            # The same missing capability is asked with different words: a
            # proposal counts every recent request for the same verb and
            # object, whatever capability class each one failed with.
            import learning_loop as _ll
            _ll.propose_from_lacuna(
                lacuna_id=lid, query=query, verb=intent.verb or "",
                object_=intent.object or "", error_class=error_class,
                n_seen=_recent_gap_count(conn, intent.verb, intent.object))
        except Exception as ex:  # noqa: BLE001 — mai rompere la risposta
            log.warning("terminator: gap not offered to the learning loop: %r", ex)
        conn.close()
    except Exception as ex:
        log.warning("terminator: record_lacuna failed: %r", ex)
    return lid


GAP_WINDOW_DAYS = 30
GAP_RETENTION_DAYS = 90


def _days_ago(days: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))


def _recent_gap_count(conn: sqlite3.Connection, verb, object_) -> int:
    """Requests for a capability on (verb, object) in the last 30 days.

    A proposal's score follows this number: it goes down when the requests
    stop arriving, not only up while they come. Each sentence counts once a
    day, and only the days inside the window: until 15/11/2026 the lifetime
    count of every sentence seen in the window was summed, so a sentence
    asked on five days two months ago and once yesterday counted six.
    """
    import learning_loop as _ll
    classes = sorted(_ll._CAPABILITY_GAP_CLASSES)
    row = conn.execute(
        "SELECT COUNT(*) FROM lacune_days d JOIN lacune l ON l.lacuna_id = d.lacuna_id "
        "WHERE l.intent_verb IS ? AND l.intent_object IS ? AND d.day >= ? "
        f"AND l.error_class IN ({','.join('?' * len(classes))})",
        (verb, object_, _days_ago(GAP_WINDOW_DAYS)[:10], *classes)).fetchone()
    return int(row[0]) if row else 0


def recent_gap_count(verb, object_) -> int:
    conn = _ensure_db()
    try:
        return _recent_gap_count(conn, verb, object_)
    finally:
        conn.close()


def last_gap_seen(verb, object_) -> str | None:
    """When a capability on (verb, object) was last asked for; None when no
    request is kept any more (they are dropped after 90 days)."""
    import learning_loop as _ll
    classes = sorted(_ll._CAPABILITY_GAP_CLASSES)
    conn = _ensure_db()
    try:
        row = conn.execute(
            "SELECT MAX(last_seen) FROM lacune WHERE intent_verb IS ? AND intent_object IS ? "
            f"AND error_class IN ({','.join('?' * len(classes))})",
            (verb, object_, *classes)).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else None


def prune_gaps() -> int:
    """Drop gaps not seen for 90 days: they hold the request as it was typed."""
    conn = _ensure_db()
    try:
        removed = conn.execute("DELETE FROM lacune WHERE last_seen < ?",
                               (_days_ago(GAP_RETENTION_DAYS),)).rowcount
        conn.execute("DELETE FROM lacune_days WHERE day < ? OR lacuna_id NOT IN "
                     "(SELECT lacuna_id FROM lacune)", (_days_ago(GAP_RETENTION_DAYS)[:10],))
        conn.commit()
        return int(removed or 0)
    finally:
        conn.close()


def record_missing_actions(query: str, intent: Intent, verbs, *,
                           planned=()) -> None:
    """Requested actions that no plan can carry are capability gaps.

    Each verb keeps its own object from the intent decomposition; when the
    same verb is asked on several objects, the gap is the one the plan
    (``planned``, its verb/object pairs) does not carry. The primary object
    belongs to the primary verb only. A verb without an object is still
    counted, and the learning loop never proposes it.
    """
    from messages import get as _msg
    actions = [a for a in (getattr(intent, "actions", None) or []) if isinstance(a, dict)]
    cause = _msg("MSG_REQUIRED_ACTION_NOT_PLANNED")
    carried = set(planned or ())
    for verb in sorted(verbs or ()):
        objects = [str(a.get("object") or "") for a in actions
                   if a.get("verb") == verb and a.get("object")]
        obj = next((item for item in objects if (verb, item) not in carried),
                   objects[0] if objects else "")
        if not obj and verb == intent.verb:
            obj = intent.object
        _record_lacuna(query, Intent(verb=verb, object=obj),
                       "capability_missing", cause, "")


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
    # The request selects on a piece of data the results do not carry
    # («contacts older than ten years»: a contact has no date). The tool says
    # which data there are; asking for «path, name, period» again is no help.
    "unknown_where_field": "MSG_TERM_UNKNOWN_FIELD_ACTION",
}

# Alcune classi executor non sono error_code ma descrivono comunque l'azione
# utile. La decisione resta strutturata e indipendente dal testo localizzato.
_ERROR_CLASS_ACTION_KEYS = {
    "search_no_results": "MSG_LOOP_BREAK_HINT_URLS",
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


def _is_tools_own_code(code: str) -> bool:
    """The refusal code was chosen by the tool, not filed by a provider
    wrapper under the generic code of an error class."""
    if not code:
        return False
    from skill_wrapper import ERROR_CODE_BY_CLASS
    return code not in ERROR_CODE_BY_CLASS.values()


class SimpleTerminator:
    """Default: errore concreto dello step fallito (§2.8), altrimenti template
    fisso per classe errore + record lacuna."""

    def explain(self, *, query: str, intent: Intent,
                failed_run: Optional[RunResult],
                error_class: str = "") -> TerminatorResponse:
        from messages import get as _msg
        # Keep the existing localized templates while turn records retain
        # the concrete class. An unclassified failure used the argument-error
        # template before the cause and the replanning policy were separated.
        display_class = error_class
        if display_class not in _CAUSE_KEYS:
            display_class = "out_of_scope"
            for step in (failed_run.steps if failed_run else []):
                if getattr(step, "ok", False):
                    continue
                display_class = "wrong_args"
                classes = result_error_classes(step.result)
                if classes and classes[0] == "search_no_results":
                    display_class = "missing_input"
                elif (any(cls in OPERATIONAL_ERROR_CLASSES for cls in classes)
                      or classes and classes[0] in (
                          "needs_user_action", "capability_missing")):
                    display_class = "out_of_scope"
                break
        ck, ak = _CAUSE_KEYS[display_class]
        action = _msg(ak)
        # §2.8: se uno step ha fallito con un errore concreto/azionabile, mostra
        # QUELLO come causa invece del generico per-classe (che lo mascherava).
        step_err, step_error_class = _first_step_failure(failed_run)
        step_error_code = _first_step_error_code(failed_run)
        # Backend traces are diagnostics, not localized user-facing text.
        # A missing remote capability already has a structured class, so use
        # the existing localized out-of-scope wording instead of exposing the
        # provider traceback (or coupling behavior to its prose).
        # A tool that refuses with a code of its own worded the refusal
        # itself, in the language of the instance: those are the words to
        # give («on this machine only its administrator installs programs»,
        # turn b9a5bbad, 16/11/2026, was replaced by «needs a physical
        # action»). A provider wrapper files a failure under the one code of
        # its class, and its text is the provider's trace.
        tool_worded = False
        if step_error_class == "capability_missing":
            tool_worded = bool(step_err) and _is_tools_own_code(step_error_code)
            if not tool_worded:
                step_err = ""
        action_key = _ERROR_ACTION_KEYS.get(step_error_code)
        if action_key:
            action = _msg(action_key)
        elif step_error_class in _ERROR_CLASS_ACTION_KEYS:
            action = _msg(_ERROR_CLASS_ACTION_KEYS[step_error_class])
        elif step_error_class in OPERATIONAL_ERROR_CLASSES:
            action = _msg("MSG_CHAT_FB_RETRY")
        # A concrete user-action failure already carries the executor instruction.
        # Recovery out_of_scope must not invent a physical/capability remedy.
        concrete_user_action = tool_worded or (
            step_error_class in ("needs_user_action", "filesystem_permission")
            and bool(step_err))
        if concrete_user_action:
            action = ""
        cause = step_err if step_err else _msg(ck)
        # I frammenti i18n possono essere gia' frasi concluse mentre il wrapper
        # aggiunge il punto. Togli soltanto il punto duplicabile; conserva
        # domanda/esclamazione e i valori originali nei campi diagnostici.
        text = step_err if concrete_user_action else _msg(
            "MSG_TERM_WRAPPER",
            cause=cause.rstrip().rstrip("."),
            action=action.rstrip().rstrip("."),
        )
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
    try:
        from . import terminator_metis
        return terminator_metis.MetisTerminator()
    except Exception as ex:
        log.warning("MetisTerminator unavailable (%r), fallback simple", ex)
    return SimpleTerminator()
