#!/usr/bin/env python3
"""i18n — DB centralizzato testi + fetcher con fallback chain.

Design 1/5/2026 sera (vedi `metnos_design_i18n_final.md`):
- Single-lang by default (sistema installato e usato in 1 lingua, env METNOS_LANG)
- DB sqlite `~/.local/share/metnos/i18n.sqlite`: (key, lang, text, needs_translation, source_lang)
- Fetch_key EN canonical
- Fallback chain runtime: current_lang → en → it → "<missing:{key}>"
- Lazy translation via daemon introspettivo (vedi `i18n_translator.py`, futuro)

API:
    current_lang() -> str         lingua corrente (cached al boot, env METNOS_LANG, default "it")
    get(key, **kwargs) -> str     fetch con fallback + .format(**kwargs)
    set(key, lang, text)          INSERT/REPLACE
    mark_for_translation(key, target_lang, source_lang) crea placeholder row
    list_pending(limit=50)        rows con needs_translation=1 (per daemon translator)
    set_translated(key, lang, text) UPDATE post-traduzione
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

import config as _C  # §7.11 — rispetta METNOS_USER_DATA
DB_PATH = _C.DB_I18N
DEFAULT_LANG = "it"
FALLBACK_CHAIN = ("en", "it")  # tentativi se current_lang non disponibile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS i18n (
    key TEXT NOT NULL,
    lang TEXT NOT NULL,
    text TEXT,
    needs_translation INTEGER NOT NULL DEFAULT 0,
    source_lang TEXT,
    source_hash TEXT,                    -- legacy: short-hash 16-char (compat)
    version_hash TEXT,                   -- sha256 full del testo CORRENTE di questa row
    source_text_hash TEXT,               -- sha256 full del source_text al momento della traduzione
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (key, lang)
);
CREATE INDEX IF NOT EXISTS idx_i18n_pending ON i18n(needs_translation, lang)
    WHERE needs_translation=1;
"""


def _hash_text(text: str) -> str:
    """SHA-256 short hash (16-char). Legacy: usato per `source_hash` (compat)."""
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _sha256_full(text: str) -> str:
    """SHA-256 hex full prefix-encoded (`sha256:<hex>`). Usato per `version_hash`
    e `source_text_hash` del pattern latest-wins (estensione ADR 0092)."""
    import hashlib
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()

_lang_cache: str | None = None
_conn: sqlite3.Connection | None = None


def current_lang() -> str:
    """Lingua corrente del sistema. Cached al primo accesso (boot-time)."""
    global _lang_cache
    if _lang_cache is None:
        _lang_cache = os.environ.get("METNOS_LANG", DEFAULT_LANG).lower()
    return _lang_cache


def _open() -> sqlite3.Connection:
    """Apre connessione DB (singleton process-local). Auto-crea schema +
    migration idempotente per colonne aggiunte post-genesi."""
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        # Concorrenza-safe: WAL consente 1 writer + N reader senza lock; il
        # busy_timeout assorbe la contesa fra turno utente e job notturno
        # (i18n_translate_pending) anziche' fallire subito con "database is
        # locked" (§2.8 no silent failure).
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.executescript(_SCHEMA)
        # Migration legacy: source_hash colonna aggiunta 1/5/2026 sera.
        # Migration v2 (6/5/2026): version_hash + source_text_hash per
        # pattern latest-wins simmetrico unificato sui 3 layer multilingua.
        cols = {r[1] for r in c.execute("PRAGMA table_info(i18n)").fetchall()}
        if "source_hash" not in cols:
            c.execute("ALTER TABLE i18n ADD COLUMN source_hash TEXT")
        if "version_hash" not in cols:
            c.execute("ALTER TABLE i18n ADD COLUMN version_hash TEXT")
        if "source_text_hash" not in cols:
            c.execute("ALTER TABLE i18n ADD COLUMN source_text_hash TEXT")
        # Backfill version_hash per row esistenti.
        for row in c.execute(
            "SELECT key, lang, text FROM i18n WHERE version_hash IS NULL AND text IS NOT NULL"
        ).fetchall():
            c.execute(
                "UPDATE i18n SET version_hash=? WHERE key=? AND lang=?",
                (_sha256_full(row[2]), row[0], row[1]),
            )
        c.commit()
        _conn = c
    return _conn


def get(key: str, **kwargs) -> str:
    """Fetch testo per chiave. Fallback chain: current → en → it → <missing>.
    `**kwargs` passati a .format() sul template."""
    conn = _open()
    try_langs = [current_lang()]
    for fb in FALLBACK_CHAIN:
        if fb not in try_langs:
            try_langs.append(fb)
    for lang in try_langs:
        row = conn.execute(
            "SELECT text, needs_translation FROM i18n WHERE key=? AND lang=?",
            (key, lang),
        ).fetchone()
        # needs_translation=1 e' un HINT al daemon traduttore, non un blocco
        # per get(): se `text` e' popolato e non-vuoto, la traduzione e' usable.
        # Bug live 12/5/2026: 174 righe MSG_* avevano needs_translation=1
        # (orphan source_lang NULL) ma text valido in entrambe le lingue.
        # i18n.get scartava la IT e cadeva in fallback a EN → test +
        # final_message user-facing in lingua sbagliata. Fix generale:
        # treat needs_translation come metadato del daemon, non come gate.
        if row and row[0]:
            template = row[0]
            try:
                return template.format(**kwargs) if kwargs else template
            except (KeyError, IndexError):
                return template  # template malformato, ritorna grezzo
    return f"<missing:{key}>"


def key_exists(key: str, lang: str | None = None) -> bool:
    """True se la chiave esiste nel DB (per lang specifica o qualsiasi).

    Wiring helper per `register_key_if_missing` e per controlli pre-write
    nel synth pipeline (Fase 11 c, 19/5/2026 v4).
    """
    conn = _open()
    if lang:
        row = conn.execute(
            "SELECT 1 FROM i18n WHERE key=? AND lang=? AND text IS NOT NULL LIMIT 1",
            (key, lang),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM i18n WHERE key=? AND text IS NOT NULL LIMIT 1",
            (key,),
        ).fetchone()
    return row is not None


def keys_for_synth_context(verb: str | None = None,
                            obj: str | None = None,
                            max_per_family: int = 30) -> dict[str, list[str]]:
    """Subset chiavi i18n per il prompt synt_code stage 5 (A2 19/5/2026 v4).

    Ritorna un dict `{family: [keys...]}` con le chiavi piu' rilevanti per il
    verbo+oggetto del nuovo executor. Strategia:
      - Sempre tutte le ERR_* generiche (sono ~8-30, baseline).
      - WARN_* (poche).
      - MSG_* solo top max_per_family per evitare bloat (133 totali).
      - LOG_* tutte (poche, audit).
    Filtra chiavi non semantiche (`.description`/`.affinity`/`prompt.*`/etc).

    Razionale: il LLM stage 5 vede solo le famiglie che probabilmente usera'
    (errori sempre, messaggi solo qualche esempio). Tot ~50-70 chiavi ≈ 1-2 KB
    invece di 7 KB con tutte le 247. Determinismo §7.9.
    """
    conn = _open()
    rows = conn.execute(
        "SELECT DISTINCT key FROM i18n WHERE lang='it' "
        "AND text IS NOT NULL "
        "AND key GLOB '[A-Z]*_*' "  # solo UPPER_CASE_FAMILY style
        "ORDER BY key"
    ).fetchall()
    by_family: dict[str, list[str]] = {"ERR_": [], "WARN_": [], "MSG_": [], "LOG_": []}
    for (k,) in rows:
        for fam in by_family:
            if k.startswith(fam):
                by_family[fam].append(k)
                break
    # Cap MSG_ a max_per_family (le altre sono naturalmente piccole).
    if len(by_family["MSG_"]) > max_per_family:
        by_family["MSG_"] = by_family["MSG_"][:max_per_family]
    return by_family


def register_key_if_missing(
    key: str,
    text_it: str,
    text_en: str | None = None,
    *,
    needs_translation: bool = True,
) -> bool:
    """Registra una chiave i18n SOLO se assente. Idempotente, no-op se gia'
    presente in DB. Ritorna True se ha scritto, False se gia' esisteva.

    Fase 11 (c) scaffolding 19/5/2026 v4: usato dal pipeline synth quando
    emette `messages.get("ERR_NUOVA")` con chiave non in DB, per evitare
    orfani. Il flag `needs_translation=True` marca le entry per review
    successivo da admin (i18n_translator daemon ADR 0092 puo' poi
    completare con LLM se opportuno).

    Convenzione naming chiavi: §6.1 + dedup the design guide 19/5 — famiglie
    ERR_/WARN_/MSG_/LOG_ + suffisso semantico breve (max 2-3 segmenti).
    """
    if key_exists(key):
        return False
    if text_en is None:
        text_en = text_it  # fallback IT come EN (translator daemon lo rifina)
    set(key, "it", text_it, source_lang="it")
    set(key, "en", text_en, source_lang="en")
    if needs_translation:
        # Mark entrambe le lingue per review (set() resetta needs_translation=0
        # di default; qui lo riattiva esplicitamente come "auto-registered").
        conn = _open()
        conn.execute(
            "UPDATE i18n SET needs_translation=1 WHERE key=?",
            (key,),
        )
        conn.commit()
    return True


def set(key: str, lang: str, text: str, *, source_lang: str | None = None) -> None:
    """INSERT o REPLACE testo per (key, lang). Resetta needs_translation=0.

    Auto-recalc `version_hash = sha256(text)` (estensione ADR 0092 v2,
    6/5/2026) e mantiene `source_hash` legacy (16-char) per compat.

    Pattern latest-wins simmetrico (6/5/2026): set su QUALSIASI lingua
    invalida le altre lingue per la stessa key dove il `source_text_hash`
    salvato non corrisponde piu' al nuovo testo. IT non e' piu' la
    canonical-source rigida; qualunque lingua editata diventa edit-source
    delle altre. Allineato a `align_prompts()` di i18n_translator.
    """
    conn = _open()
    legacy_hash = _hash_text(text)
    new_version_hash = _sha256_full(text)
    conn.execute(
        "INSERT OR REPLACE INTO i18n(key, lang, text, needs_translation, source_lang, "
        "source_hash, version_hash, updated_at) "
        "VALUES (?, ?, ?, 0, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (key, lang, text, source_lang, legacy_hash, new_version_hash),
    )
    # Latest-wins: invalida ogni altra lingua per la stessa key il cui
    # source_text_hash non corrisponde al nuovo testo (qualunque lingua sia
    # stata editata). Symmetric: niente preferenza per DEFAULT_LANG.
    conn.execute(
        "UPDATE i18n SET needs_translation=1 "
        "WHERE key=? AND lang!=? AND text IS NOT NULL "
        "AND (source_text_hash IS NULL OR source_text_hash != ?)",
        (key, lang, new_version_hash),
    )
    conn.commit()


def mark_for_translation(key: str, target_lang: str, source_lang: str) -> None:
    """Crea placeholder row per traduzione lazy (text=NULL, needs_translation=1)."""
    conn = _open()
    conn.execute(
        "INSERT OR IGNORE INTO i18n(key, lang, text, needs_translation, source_lang, updated_at) "
        "VALUES (?, ?, NULL, 1, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (key, target_lang, source_lang),
    )
    conn.commit()


def list_pending(limit: int = 50) -> list[dict]:
    """Rows con needs_translation=1 + source text. Usato dal daemon translator."""
    conn = _open()
    rows = conn.execute(
        "SELECT i.key, i.lang AS target_lang, i.source_lang, "
        "       (SELECT text FROM i18n WHERE key=i.key AND lang=i.source_lang) AS source_text "
        "FROM i18n i WHERE needs_translation=1 LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"key": r[0], "target_lang": r[1], "source_lang": r[2], "source_text": r[3]}
            for r in rows]


def set_translated(key: str, lang: str, text: str) -> None:
    """UPDATE post-traduzione: text + needs_translation=0. Salva sia il
    `source_hash` legacy 16-char (compat) che il `source_text_hash` v2
    (sha256 full prefisso `sha256:`) e ricalcola `version_hash` del
    testo tradotto.

    Pattern latest-wins (6/5/2026): `source_text_hash` permette al
    daemon di detect "source ha cambiato → ritraduci" senza dipendere
    dalla lingua canonical."""
    conn = _open()
    src_lang_row = conn.execute(
        "SELECT source_lang FROM i18n WHERE key=? AND lang=?", (key, lang)
    ).fetchone()
    src_lang = (src_lang_row[0] if src_lang_row else None) or DEFAULT_LANG
    src_text_row = conn.execute(
        "SELECT text FROM i18n WHERE key=? AND lang=?", (key, src_lang)
    ).fetchone()
    src_text = (src_text_row[0] if src_text_row else "") or ""
    legacy_src_hash = _hash_text(src_text) if src_text else None
    src_text_hash_v2 = _sha256_full(src_text) if src_text else None
    new_version_hash = _sha256_full(text)
    conn.execute(
        "UPDATE i18n SET text=?, needs_translation=0, source_hash=?, "
        "source_text_hash=?, version_hash=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE key=? AND lang=?",
        (text, legacy_src_hash, src_text_hash_v2, new_version_hash, key, lang),
    )
    conn.commit()


def stats() -> dict:
    """Diagnostic: count per lingua, pending."""
    conn = _open()
    out = {"total": 0, "by_lang": {}, "pending": 0}
    for row in conn.execute("SELECT lang, COUNT(*), SUM(needs_translation) FROM i18n GROUP BY lang"):
        lang, count, pending = row
        out["by_lang"][lang] = {"count": count, "pending": pending or 0}
        out["total"] += count
        out["pending"] += pending or 0
    return out


def bulk_load(items: Iterable[tuple[str, str, str]]) -> int:
    """Bulk INSERT OR REPLACE. items: iterable di (key, lang, text). Ritorna count."""
    conn = _open()
    n = 0
    for key, lang, text in items:
        conn.execute(
            "INSERT OR REPLACE INTO i18n(key, lang, text, needs_translation, updated_at) "
            "VALUES (?, ?, ?, 0, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (key, lang, text),
        )
        n += 1
    conn.commit()
    return n
