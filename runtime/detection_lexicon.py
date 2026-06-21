#!/usr/bin/env python3
"""detection_lexicon — store traducibile dei lessici di riconoscimento NL.

Gemello *lato input* di `i18n.py` (lato output). Mentre l'i18n traduce i
messaggi che Metnos PRODUCE, questo modulo gestisce i lessici di superficie
che Metnos RICONOSCE nella query utente (e nel testo esterno: web, mail):
hint di notifica, marker di undo/scheduling, pattern di ordinamento, ecc.

Problema risolto (§2.8 — no silent failure): i lessici erano hardcoded IT+EN
sparsi nel runtime. Con `METNOS_LANG` != it/en il matching falliva in
silenzio. Qui i lessici vivono in un DB traducibile con la STESSA meccanica
dell'i18n: seed canonico IT+EN nel codice (`detection_lexicon_seed.py`),
fallback chain `current -> en -> it`, daemon di traduzione automatica
(`jobs/detection_translate_pending.py`), guard di copertura allo startup.

Per it/en il contenuto seed e' IDENTICO ai costrutti hardcoded preesistenti:
la migrazione e' a comportamento invariato (test di proprieta'
`vecchia-costante == nuovo-matcher`). Le altre lingue si popolano via daemon.

Tre forme di lessico (`kind`):
  - "phrases": lista di forme di superficie (match substring o word-boundary)
  - "regex":   lista di pattern regex (compilati con re.I); per it/en i
               pattern hand-tuned restano verbatim, per altre lingue il
               daemon li sintetizza da una word-list tradotta.
  - "mapping": dict {canonical: [forme]} — dati per scoring/resolution
               (es. verbo->forme, oggetto->forme); l'algoritmo del
               chiamante resta invariato, cambia solo la FONTE dei dati.

API principale:
    ensure_seeded()                  carica il seed canonico (idempotente)
    register(concept, kind, it, en)  seed di un concept (idempotente)
    forms(concept) -> list[str]      forme per la lingua corrente (+fallback)
    mapping(concept) -> dict         mapping per la lingua corrente (+fallback)
    match(concept, text) -> bool     True se una forma/pattern matcha `text`
    search(concept, text) -> Match   prima match regex (per capture group)
    verify_coverage(lang) -> dict    {ok, missing:[concept...]} guard anti-silenzio
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading

import config as _C  # §7.11
import i18n as _i18n  # riusa current_lang() — UNICA fonte della lingua

log = logging.getLogger("metnos.detection_lexicon")

DB_PATH = _C.DB_DETECTION
SEED_LANGS = ("it", "en")          # lingue seedate nel codice (sempre coperte)
FALLBACK_CHAIN = ("en", "it")      # tentativi se current_lang non disponibile
VALID_KINDS = ("phrases", "regex", "mapping")
VALID_MATCH_MODES = ("substring", "word")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS detection_lexicon (
    concept TEXT NOT NULL,
    lang TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- phrases | regex | mapping
    match_mode TEXT NOT NULL DEFAULT 'substring',
    payload TEXT,                        -- JSON (list | list | object)
    needs_translation INTEGER NOT NULL DEFAULT 0,
    source_lang TEXT,
    version_hash TEXT,                   -- sha256 del payload corrente
    source_text_hash TEXT,              -- sha256 del payload sorgente tradotto
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (concept, lang)
);
CREATE INDEX IF NOT EXISTS idx_detlex_pending
    ON detection_lexicon(needs_translation, lang) WHERE needs_translation=1;
"""

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
_cache: dict[tuple[str, str], tuple] = {}     # (concept, current_lang) -> resolved
_regex_cache: dict[tuple[str, str], list] = {}
_seeded = False
_coverage_gaps_logged: set[tuple[str, str]] = set()


def _sha256(text: str) -> str:
    from hashutil import sha256_prefixed
    return sha256_prefixed(text)


def current_lang() -> str:
    return _i18n.current_lang()


def _open() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA busy_timeout=5000")
                c.executescript(_SCHEMA)
                c.commit()
                _conn = c
    return _conn


def _invalidate(concept: str | None = None) -> None:
    if concept is None:
        _cache.clear()
        _regex_cache.clear()
        return
    for k in [k for k in _cache if k[0] == concept]:
        _cache.pop(k, None)
    for k in [k for k in _regex_cache if k[0] == concept]:
        _regex_cache.pop(k, None)


# --------------------------------------------------------------------------
# Seed / registrazione
# --------------------------------------------------------------------------
def ensure_seeded() -> None:
    """Carica il seed canonico una sola volta per processo (idempotente).

    Importa `detection_lexicon_seed` che chiama `register(...)` per ogni
    concept. `register` e' no-op se la riga (concept, lang) esiste gia', quindi
    e' sicuro chiamarlo ad ogni boot: il DB persiste, il seed riallinea solo
    le righe mancanti.
    """
    global _seeded
    if _seeded:
        return
    with _lock:
        if _seeded:
            return
        try:
            import detection_lexicon_seed as _seed
            _seed.register_all()
        except Exception:
            log.exception("detection_lexicon: seed fallito")
        _seeded = True
        _startup_coverage_check()


def _startup_coverage_check() -> None:
    """Guard anti-silenzio (§2.8): se la lingua d'istanza non e' coperta da
    ogni concept, lo rende ESPLICITO nei log invece di lasciar fallire il
    matching in silenzio. Muto per it/en (sempre seedate). Per lingue nuove
    indica di eseguire il daemon `detection_translate_pending`."""
    try:
        rep = verify_coverage(current_lang())
    except Exception:
        return
    if not rep["ok"]:
        log.warning(
            "detection_lexicon: lingua %r coperta %d/%d concept; %d non "
            "tradotti (%s). Accodo per il daemon detection_translate_pending.",
            rep["lang"], rep["covered"], rep["total"],
            len(rep["missing"]), ", ".join(rep["missing"][:8]))
        # Turnkey: per una lingua non-seed, accoda i concept scoperti cosi'
        # il daemon (every_6h) li traduce senza intervento manuale. it/en
        # sono sempre coperte => questo ramo non scatta in esercizio normale.
        if rep["lang"] not in SEED_LANGS:
            try:
                enqueue_language(rep["lang"])
            except Exception:
                log.exception("detection_lexicon: auto-enqueue fallito")


def register(concept: str, kind: str, *, it, en,
             match_mode: str = "substring") -> bool:
    """Seed di un concept (lingue it+en) SOLO se assente. Idempotente.

    `it`/`en` sono: list[str] per kind=phrases/regex; dict per kind=mapping.
    Ritorna True se ha scritto almeno una riga, False se gia' presente.
    Comportamento gemello di `i18n.register_key_if_missing`.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"kind invalido: {kind!r}")
    if match_mode not in VALID_MATCH_MODES:
        raise ValueError(f"match_mode invalido: {match_mode!r}")
    conn = _open()
    wrote = False
    for lang, payload in (("it", it), ("en", en)):
        row = conn.execute(
            "SELECT 1 FROM detection_lexicon WHERE concept=? AND lang=?",
            (concept, lang),
        ).fetchone()
        if row:
            continue
        js = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        conn.execute(
            "INSERT INTO detection_lexicon(concept, lang, kind, match_mode, "
            "payload, needs_translation, source_lang, version_hash, updated_at) "
            "VALUES (?,?,?,?,?,0,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (concept, lang, kind, match_mode, js, lang, _sha256(js)),
        )
        wrote = True
    if wrote:
        conn.commit()
        _invalidate(concept)
    return wrote


def set_payload(concept: str, lang: str, payload, *,
                kind: str | None = None, match_mode: str | None = None,
                source_lang: str | None = None) -> None:
    """INSERT/REPLACE payload per (concept, lang). Usato da daemon e admin."""
    conn = _open()
    meta = conn.execute(
        "SELECT kind, match_mode FROM detection_lexicon WHERE concept=? "
        "ORDER BY lang LIMIT 1", (concept,),
    ).fetchone()
    kind = kind or (meta[0] if meta else "phrases")
    match_mode = match_mode or (meta[1] if meta else "substring")
    js = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "INSERT OR REPLACE INTO detection_lexicon(concept, lang, kind, "
        "match_mode, payload, needs_translation, source_lang, version_hash, "
        "updated_at) VALUES (?,?,?,?,?,0,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (concept, lang, kind, match_mode, js, source_lang, _sha256(js)),
    )
    conn.commit()
    _invalidate(concept)


# --------------------------------------------------------------------------
# Risoluzione + matcher
# --------------------------------------------------------------------------
def _native(concept: str, lang: str):
    """(kind, match_mode, payload_obj) per la lingua ESATTA, o None."""
    conn = _open()
    row = conn.execute(
        "SELECT kind, match_mode, payload FROM detection_lexicon "
        "WHERE concept=? AND lang=? AND payload IS NOT NULL",
        (concept, lang),
    ).fetchone()
    if not row:
        return None
    try:
        return (row[0], row[1], json.loads(row[2]))
    except Exception:
        return None


def _union_langs() -> list[str]:
    """Lingue da unire al match: corrente + seed (it/en), senza duplicati.

    Per istanze it/en l'insieme e' esattamente {it, en} => comportamento
    IDENTICO ai costrutti lang-agnostici preesistenti (che gia' univano
    IT+EN). Per una lingua nuova si AGGIUNGE la sua riga, preservando i
    comandi-prestito it/en (es. «undo», «send me»).
    """
    langs = [current_lang()]
    for seed in SEED_LANGS:
        if seed not in langs:
            langs.append(seed)
    return langs


def _resolve(concept: str):
    """Risolve (kind, match_mode, merged_payload, langs) unendo le forme su
    `{lingua_corrente} ∪ {it,en}`.

    Anti-silenzio: se la lingua corrente non e' seedata e non ha payload
    nativo, registra il gap (deduplicato). Il match continua via union it/en
    (best-effort sui prestiti), ma `verify_coverage` allo startup rende il
    gap ESPLICITO invece di lasciarlo silenzioso.
    """
    ensure_seeded()
    cur = current_lang()
    key = (concept, cur)
    if key in _cache:
        return _cache[key]
    if cur not in SEED_LANGS and _native(concept, cur) is None:
        gap = (concept, cur)
        if gap not in _coverage_gaps_logged:
            _coverage_gaps_logged.add(gap)
            log.warning(
                "detection_lexicon: concept %r privo di forme native per "
                "lingua %r — match via union it/en (best-effort); esegui il "
                "daemon di traduzione per coprire la lingua", concept, cur)
    kind = match_mode = None
    merged_list: list = []
    merged_map: dict = {}
    used: list[str] = []
    seen: set = set()
    for lang in _union_langs():
        nat = _native(concept, lang)
        if nat is None:
            continue
        kind, match_mode, payload = nat
        used.append(lang)
        if kind == "mapping" and isinstance(payload, dict):
            for canon, fl in payload.items():
                bucket = merged_map.setdefault(canon, [])
                for f in fl:
                    if f not in bucket:
                        bucket.append(f)
        elif isinstance(payload, list):
            for f in payload:
                if f not in seen:
                    seen.add(f)
                    merged_list.append(f)
    if not used:
        out = None
    else:
        payload = merged_map if kind == "mapping" else merged_list
        out = (kind, match_mode, payload, used)
    _cache[key] = out
    return out


def forms(concept: str) -> list[str]:
    """Forme di superficie per la lingua corrente (kind=phrases/regex)."""
    res = _resolve(concept)
    if not res:
        return []
    payload = res[2]
    return list(payload) if isinstance(payload, list) else []


def mapping(concept: str) -> dict:
    """Mapping {canonical: [forme]} per la lingua corrente (kind=mapping)."""
    res = _resolve(concept)
    if not res:
        return {}
    payload = res[2]
    return dict(payload) if isinstance(payload, dict) else {}


def _compiled(concept: str) -> list:
    """Pattern regex compilati per la lingua corrente (cache per processo)."""
    lang = current_lang()
    key = (concept, lang)
    if key in _regex_cache:
        return _regex_cache[key]
    res = _resolve(concept)
    out: list = []
    if res and res[0] == "regex" and isinstance(res[2], list):
        for pat in res[2]:
            try:
                out.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                log.warning("detection_lexicon: regex invalido in %r: %r",
                            concept, pat)
    _regex_cache[key] = out
    return out


def match(concept: str, text: str) -> bool:
    """True se una forma (phrases) o un pattern (regex) matcha `text`.

    phrases+substring: `forma in text` (case-insensitive).
    phrases+word:      forma come parola intera (\\b...\\b).
    regex:             `pattern.search(text)`.
    Deterministico §7.9.
    """
    if not text:
        return False
    res = _resolve(concept)
    if not res:
        return False
    kind, match_mode, payload, _lang = res
    low = text.lower()
    if kind == "regex":
        return any(p.search(text) for p in _compiled(concept))
    if not isinstance(payload, list):
        return False
    if match_mode == "word":
        # Byte-identico a tool_grammar._has_word: \b<forma>\b per ogni forma
        # (singola o multi-parola). Evita falsi positivi qua/qualcosa.
        return any(re.search(r"\b" + re.escape(f.lower()) + r"\b", low)
                   for f in payload)
    return any(f.lower() in low for f in payload)


def search(concept: str, text: str):
    """Prima `re.Match` fra i pattern regex del concept (per capture)."""
    if not text:
        return None
    for p in _compiled(concept):
        m = p.search(text)
        if m:
            return m
    return None


def regexes(concept: str) -> list:
    """Pattern compilati (lingua corrente) — per chi compone match custom."""
    return list(_compiled(concept))


def match_any(forms, text: str, mode: str = "word") -> bool:
    """Matcha `text` contro una lista di forme gia' risolta (es. un valore di
    `mapping()`). Stessa semantica di `match`: mode='word' usa \\b<forma>\\b,
    'substring' usa contenimento. Per i call-site che iterano sotto-liste
    (es. provider markers per suffisso)."""
    if not text or not forms:
        return False
    low = text.lower()
    if mode == "word":
        return any(re.search(r"\b" + re.escape(f.lower()) + r"\b", low)
                   for f in forms)
    return any(f.lower() in low for f in forms)


# --------------------------------------------------------------------------
# Coverage guard (anti-silenzio) + supporto daemon
# --------------------------------------------------------------------------
def registered_concepts() -> list[str]:
    ensure_seeded()
    conn = _open()
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT concept FROM detection_lexicon ORDER BY concept")]


def has_native(concept: str, lang: str) -> bool:
    return _native(concept, lang) is not None


def coverage(lang: str | None = None) -> dict:
    """{concept: bool} — True se `lang` ha forme native per il concept."""
    lang = (lang or current_lang()).lower()
    return {c: has_native(c, lang) for c in registered_concepts()}


def verify_coverage(lang: str | None = None) -> dict:
    """Guard anti-silenzio: ogni concept ha forme native per `lang`?

    Ritorna {lang, ok, total, covered, missing:[concept...]}. it/en seedate
    => sempre ok. Per lingue nuove, `missing` elenca i concept da tradurre:
    il chiamante (startup/health/install) lo rende ESPLICITO invece di
    lasciar fallire il matching in silenzio.
    """
    cov = coverage(lang)
    missing = sorted(c for c, ok in cov.items() if not ok)
    return {
        "lang": (lang or current_lang()).lower(),
        "ok": not missing,
        "total": len(cov),
        "covered": len(cov) - len(missing),
        "missing": missing,
    }


def list_pending(limit: int = 100) -> list[dict]:
    """Righe needs_translation=1 + payload sorgente (per il daemon)."""
    conn = _open()
    rows = conn.execute(
        "SELECT d.concept, d.lang, d.source_lang, d.kind, d.match_mode, "
        "(SELECT payload FROM detection_lexicon WHERE concept=d.concept "
        " AND lang=d.source_lang) AS source_payload "
        "FROM detection_lexicon d WHERE d.needs_translation=1 LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"concept": r[0], "target_lang": r[1], "source_lang": r[2],
             "kind": r[3], "match_mode": r[4], "source_payload": r[5]}
            for r in rows]


def mark_for_translation(concept: str, target_lang: str,
                         source_lang: str = "en") -> None:
    """Placeholder row (payload NULL, needs_translation=1) per lazy translate."""
    conn = _open()
    meta = conn.execute(
        "SELECT kind, match_mode FROM detection_lexicon WHERE concept=? "
        "AND lang=? LIMIT 1", (concept, source_lang),
    ).fetchone()
    if not meta:
        return
    conn.execute(
        "INSERT OR IGNORE INTO detection_lexicon(concept, lang, kind, "
        "match_mode, payload, needs_translation, source_lang, updated_at) "
        "VALUES (?,?,?,?,NULL,1,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (concept, target_lang, meta[0], meta[1], source_lang),
    )
    conn.commit()
    _invalidate(concept)


def set_translated(concept: str, lang: str, payload) -> None:
    """UPDATE post-traduzione: payload + needs_translation=0 + hash sorgente."""
    conn = _open()
    src_row = conn.execute(
        "SELECT source_lang FROM detection_lexicon WHERE concept=? AND lang=?",
        (concept, lang),
    ).fetchone()
    src_lang = (src_row[0] if src_row else None) or "en"
    src_payload_row = conn.execute(
        "SELECT payload FROM detection_lexicon WHERE concept=? AND lang=?",
        (concept, src_lang),
    ).fetchone()
    src_text = src_payload_row[0] if src_payload_row else ""
    js = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    conn.execute(
        "UPDATE detection_lexicon SET payload=?, needs_translation=0, "
        "source_text_hash=?, version_hash=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE concept=? AND lang=?",
        (js, _sha256(src_text or ""), _sha256(js), concept, lang),
    )
    conn.commit()
    _invalidate(concept)


def enqueue_language(lang: str) -> int:
    """Marca per traduzione ogni concept non ancora coperto in `lang`.

    Usato a install/aggiunta-lingua: rende l'estensione a una nuova lingua
    una singola operazione (come per l'i18n). Ritorna il numero di concept
    accodati. Sorgente = en (canonico), fallback it.
    """
    n = 0
    for c in registered_concepts():
        if has_native(c, lang):
            continue
        src = "en" if has_native(c, "en") else "it"
        mark_for_translation(c, lang, source_lang=src)
        n += 1
    return n


def stats() -> dict:
    conn = _open()
    out = {"concepts": len(registered_concepts()), "by_lang": {}, "pending": 0}
    for lang, cnt, pend in conn.execute(
        "SELECT lang, COUNT(*), SUM(needs_translation) FROM detection_lexicon "
        "GROUP BY lang"):
        out["by_lang"][lang] = {"count": cnt, "pending": pend or 0}
        out["pending"] += pend or 0
    return out


if __name__ == "__main__":
    import sys
    ensure_seeded()
    if len(sys.argv) > 1 and sys.argv[1] == "coverage":
        lang = sys.argv[2] if len(sys.argv) > 2 else current_lang()
        print(json.dumps(verify_coverage(lang), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(stats(), ensure_ascii=False, indent=2))
