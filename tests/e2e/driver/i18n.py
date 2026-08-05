"""i18n helper — legge `i18n.sqlite` direttamente (NO import runtime/).

Pattern fallback chain `current → en → it → <missing:KEY>`. Risolve sotto
`METNOS_USER_DATA` (puntato dal test al tmp dir isolato) per non
contaminare il DB live.

Convenzioni chiavi del simulatore:
  E2E_MSG_*   messaggi di stato (es. "import skill completato")
  E2E_ERR_*   errori (es. "manifest sign invalid")
  E2E_WARN_*  warning (es. "judge cache miss, slow")
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


_FALLBACK_CHAIN = ("en", "it")


def _db_path() -> Path:
    """Risolve il DB i18n dal `METNOS_USER_DATA` corrente (tmp test)."""
    user_data = os.environ.get("METNOS_USER_DATA")
    if user_data:
        return Path(user_data) / "i18n.sqlite"
    return Path.home() / ".local/share/metnos/i18n.sqlite"


def _current_lang() -> str:
    return os.environ.get("METNOS_LANG", "it")


def get(__code__: str, /, **kwargs) -> str:
    """Lookup template con substitution kwargs. Fallback chain
    `current_lang → en → it → <missing:KEY>`.

    Primo arg positional-only (PEP 570) per non collidere con kwargs
    nominati `key` (es. template `<missing:{key}>`)."""
    db = _db_path()
    if not db.exists():
        return f"<missing:{__code__}>"
    try_langs = [_current_lang()]
    for fb in _FALLBACK_CHAIN:
        if fb not in try_langs:
            try_langs.append(fb)
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        for lang in try_langs:
            row = cn.execute(
                "SELECT text FROM i18n WHERE key=? AND lang=? AND text IS NOT NULL",
                (__code__, lang),
            ).fetchone()
            if row and row[0]:
                template = row[0]
                try:
                    return template.format(**kwargs) if kwargs else template
                except (KeyError, IndexError):
                    return template
        return f"<missing:{__code__}>"
    finally:
        cn.close()


def key_exists(key: str) -> bool:
    db = _db_path()
    if not db.exists():
        return False
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        row = cn.execute(
            "SELECT 1 FROM i18n WHERE key=? AND text IS NOT NULL LIMIT 1",
            (key,),
        ).fetchone()
        return row is not None
    finally:
        cn.close()


def register_keys(keys: list[tuple[str, str, str]]) -> int:
    """Registra chiavi (key, it_text, en_text) se mancanti. Ritorna n.
    scritte. Usato dal bootstrap del simulatore al primo run."""
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    cn = sqlite3.connect(str(db), timeout=10.0)
    try:
        # Schema minimo (compatibile con runtime/i18n.py)
        cn.execute("""
            CREATE TABLE IF NOT EXISTS i18n (
                key TEXT NOT NULL,
                lang TEXT NOT NULL,
                text TEXT,
                needs_translation INTEGER NOT NULL DEFAULT 0,
                source_lang TEXT,
                PRIMARY KEY (key, lang)
            )
        """)
        n = 0
        for key, it_text, en_text in keys:
            existing = cn.execute(
                "SELECT 1 FROM i18n WHERE key=? LIMIT 1", (key,),
            ).fetchone()
            if existing:
                continue
            cn.execute(
                "INSERT INTO i18n(key, lang, text, source_lang) VALUES(?, ?, ?, ?)",
                (key, "it", it_text, "it"),
            )
            cn.execute(
                "INSERT INTO i18n(key, lang, text, source_lang) VALUES(?, ?, ?, ?)",
                (key, "en", en_text, "en"),
            )
            n += 1
        cn.commit()
        return n
    finally:
        cn.close()


# --- Chiavi i18n del simulatore E2E ---------------------------------------

_E2E_KEYS: list[tuple[str, str, str]] = [
    # Server lifecycle
    ("E2E_MSG_SERVER_SPAWNING",
     "avvio server isolato su porta {port}",
     "spawning isolated server on port {port}"),
    ("E2E_MSG_SERVER_READY",
     "server pronto: {url}",
     "server ready: {url}"),
    ("E2E_MSG_SERVER_STOPPED",
     "server fermato (pid {pid})",
     "server stopped (pid {pid})"),
    ("E2E_ERR_SERVER_START_TIMEOUT",
     "timeout avvio server dopo {timeout_s}s",
     "server start timeout after {timeout_s}s"),
    ("E2E_ERR_SERVER_DIED",
     "server morto inaspettatamente (exit {exit_code})",
     "server died unexpectedly (exit {exit_code})"),

    # Lifecycle scenarios
    ("E2E_MSG_LIFECYCLE_KIND_OK",
     "lifecycle {kind} ok: {state_from}→{state_to}",
     "lifecycle {kind} ok: {state_from}→{state_to}"),
    ("E2E_ERR_LIFECYCLE_TRANSITION",
     "transizione non valida {kind}: {state_from}→{state_to} ({reason})",
     "invalid transition {kind}: {state_from}→{state_to} ({reason})"),

    # Skill import
    ("E2E_MSG_SKILL_IMPORT_START",
     "import skill «{skill}» da {source}",
     "importing skill «{skill}» from {source}"),
    ("E2E_MSG_SKILL_IMPORT_DONE",
     "import «{skill}» ok: {n} executor",
     "import «{skill}» ok: {n} executors"),
    ("E2E_ERR_SKILL_IMPORT_FAILED",
     "import «{skill}» fallito: {reason}",
     "import «{skill}» failed: {reason}"),
    ("E2E_MSG_SKILL_UNINSTALL_OK",
     "uninstall «{skill}» ok (source preservata={source_kept})",
     "uninstall «{skill}» ok (source preserved={source_kept})"),

    # Judge LLM
    ("E2E_MSG_JUDGE_VERDICT",
     "judge: {ok_label} score={score:.2f} — {reason}",
     "judge: {ok_label} score={score:.2f} — {reason}"),
    ("E2E_WARN_JUDGE_DISABLED",
     "judge LLM disabilitato (METNOS_E2E_LLM_JUDGE=0): solo lint",
     "judge LLM disabled (METNOS_E2E_LLM_JUDGE=0): lint only"),
    ("E2E_WARN_JUDGE_CACHE_MISS",
     "judge cache miss per «{query_excerpt}»: ~5s",
     "judge cache miss for «{query_excerpt}»: ~5s"),
    ("E2E_ERR_JUDGE_PARSE",
     "judge: JSON output malformato — {raw}",
     "judge: malformed JSON output — {raw}"),

    # Lint
    ("E2E_ERR_LINT_MISSING_KEY",
     "risposta contiene `<missing:>` ({key})",
     "answer contains `<missing:>` ({key})"),
    ("E2E_ERR_LINT_TRACEBACK",
     "risposta contiene Traceback Python",
     "answer contains Python Traceback"),
    ("E2E_ERR_LINT_LANG_MISMATCH",
     "risposta in {got} ma query in {expected}",
     "answer in {got} but query in {expected}"),
    ("E2E_ERR_LINT_PIPELINE_SHAPE",
     "pipeline shape non valida: {shape} (atteso E+ (F|A)?)",
     "invalid pipeline shape: {shape} (expected E+ (F|A)?)"),

    # Corpus
    ("E2E_MSG_CORPUS_EXTRACTED",
     "corpus estratto: {n_queries} query, {n_categories} categorie",
     "corpus extracted: {n_queries} queries, {n_categories} categories"),
    ("E2E_WARN_CORPUS_EMPTY",
     "corpus vuoto: nessun turno trovato in {since}",
     "corpus empty: no turns found in {since}"),
]


def bootstrap() -> int:
    """Registra le chiavi E2E_* del simulatore. Idempotente."""
    return register_keys(_E2E_KEYS)
