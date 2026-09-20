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
fallback sulle baseline editoriali registrate, daemon di traduzione automatica
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

import json
import logging
import re
import sqlite3
import threading

import config as _C  # §7.11
import i18n as _i18n  # riusa current_lang() — UNICA fonte della lingua

log = logging.getLogger("metnos.detection_lexicon")

DB_PATH = _C.DB_DETECTION
VALID_KINDS = ("phrases", "regex", "mapping")
VALID_MATCH_MODES = ("substring", "word")
VALID_REVIEW_POLICIES = ("automatic", "manual")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS detection_lexicon (
    concept TEXT NOT NULL,
    lang TEXT NOT NULL,
    kind TEXT NOT NULL,                  -- phrases | regex | mapping
    match_mode TEXT NOT NULL DEFAULT 'substring',
    payload TEXT,                        -- JSON (list | list | object)
    needs_translation INTEGER NOT NULL DEFAULT 0,
    source_lang TEXT,
    review_policy TEXT NOT NULL DEFAULT 'automatic',
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
_declared_review_policies: dict[str, str] = {}


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
                try:
                    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                    c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                    # WAL server-side; la sandbox bwrap (§7.13) lo monta READ-ONLY
                    # e lo apre `immutable` (fallback sotto) → legge il MAIN, e le
                    # scritture fanno checkpoint(TRUNCATE) per non lasciare frame
                    # solo nel -wal.
                    c.execute("PRAGMA journal_mode=WAL")
                    c.execute("PRAGMA busy_timeout=5000")
                    c.executescript(_SCHEMA)
                    columns = {row[1] for row in c.execute(
                        "PRAGMA table_info(detection_lexicon)"
                    )}
                    if "review_policy" not in columns:
                        c.execute(
                            "ALTER TABLE detection_lexicon ADD COLUMN "
                            "review_policy TEXT NOT NULL DEFAULT 'automatic'"
                        )
                    c.commit()
                    _conn = c
                except sqlite3.OperationalError:
                    # DB read-only (sandbox): schema già creato dal server →
                    # apri immutable read-only, lock-free.
                    _conn = sqlite3.connect(
                        f"file:{DB_PATH}?mode=ro&immutable=1",
                        uri=True, check_same_thread=False)
    return _conn


def _has_column(conn: sqlite3.Connection, column: str) -> bool:
    """Feature-detect additive schema fields for immutable legacy stores."""
    return column in {
        row[1] for row in conn.execute("PRAGMA table_info(detection_lexicon)")
    }


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
    concept. `register` scrive le righe mancanti e riallinea quelle che
    divergono dal seed, quindi e' sicuro chiamarlo ad ogni boot: il DB
    persiste e il seed resta l'autorita' sulle lingue che dichiara.
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
    matching in silenzio. Per lingue nuove indica di eseguire il daemon
    `detection_translate_pending`."""
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
        # il daemon (every_6h) li traduce senza intervento manuale.
        try:
            enqueue_language(rep["lang"])
        except Exception:
            log.exception("detection_lexicon: auto-enqueue fallito")


def register(concept: str, kind: str, *, it=None, en=None,
             translations: dict | None = None,
             match_mode: str = "substring",
             review_policy: str = "automatic") -> bool:
    """Seed a concept from an open language table. Idempotent.

    Scrive una riga assente e RIALLINEA una riga che diverge dal seed (kind,
    match_mode o payload). Le lingue non seedate non vengono mai toccate: le
    scrive il daemon di traduzione.

    ``it``/``en`` remain compatibility arguments for the distributed corpus;
    new callers can provide any BCP-47 keys through ``translations``.
    Ritorna True se ha scritto almeno una riga, False se gia' presente.
    Comportamento gemello di `i18n.register_key_if_missing`.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"kind invalido: {kind!r}")
    if match_mode not in VALID_MATCH_MODES:
        raise ValueError(f"match_mode invalido: {match_mode!r}")
    if review_policy not in VALID_REVIEW_POLICIES:
        raise ValueError(f"review_policy invalida: {review_policy!r}")
    # The declaration is useful even when a sandbox can only open a legacy DB
    # read-only and therefore cannot persist the additive policy column.
    _declared_review_policies[concept] = review_policy
    payloads = dict(translations or {})
    if it is not None:
        payloads.setdefault("it", it)
    if en is not None:
        payloads.setdefault("en", en)
    if not payloads:
        raise ValueError("at least one seed translation is required")
    conn = _open()
    has_review_policy = _has_column(conn, "review_policy")
    wrote = False
    realigned = False
    try:
        for lang, payload in sorted(payloads.items()):
            js = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            policy_expr = "review_policy" if has_review_policy else "'automatic'"
            row = conn.execute(
                "SELECT kind, match_mode, payload, " + policy_expr
                + " FROM detection_lexicon "
                "WHERE concept=? AND lang=?",
                (concept, lang),
            ).fetchone()
            if row is not None:
                # The seed is the authority for the languages it declares, and
                # only for those: a translated row of another language is never
                # touched here.  Without this, changing a concept's KIND — say
                # from a hand-written regex to a translatable phrase list —
                # would reach a new installation and never an existing one, and
                # the two would diverge silently.  Compared canonically, so an
                # unchanged seed still writes nothing and the caches stay warm.
                stored = (row[0], row[1], row[2])
                desired = (kind, match_mode, js)
                policy_matches = (
                    not has_review_policy or row[3] == review_policy
                )
                if stored == desired and policy_matches:
                    continue
                if has_review_policy:
                    conn.execute(
                        "UPDATE detection_lexicon SET kind=?, match_mode=?, "
                        "payload=?, needs_translation=0, source_lang=?, "
                        "review_policy=?, version_hash=?, "
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                        "WHERE concept=? AND lang=?",
                        (kind, match_mode, js, lang, review_policy,
                         _sha256(js), concept, lang),
                    )
                else:
                    conn.execute(
                        "UPDATE detection_lexicon SET kind=?, match_mode=?, "
                        "payload=?, needs_translation=0, source_lang=?, "
                        "version_hash=?, "
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                        "WHERE concept=? AND lang=?",
                        (kind, match_mode, js, lang, _sha256(js), concept, lang),
                    )
                log.info("detection_lexicon: concept %r lingua %r riallineato "
                         "al seed (kind %r -> %r)", concept, lang, row[0], kind)
                wrote = realigned = True
                continue
            if has_review_policy:
                conn.execute(
                    "INSERT INTO detection_lexicon(concept, lang, kind, match_mode,"
                    " payload, needs_translation, source_lang, review_policy, "
                    "version_hash, updated_at) VALUES (?,?,?,?,?,0,?,?,?,"
                    "strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                    (concept, lang, kind, match_mode, js, lang,
                     review_policy, _sha256(js)),
                )
            else:
                conn.execute(
                    "INSERT INTO detection_lexicon(concept, lang, kind, match_mode,"
                    " payload, needs_translation, source_lang, version_hash, "
                    "updated_at) VALUES (?,?,?,?,?,0,?,?,"
                    "strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
                    (concept, lang, kind, match_mode, js, lang, _sha256(js)),
                )
            wrote = True
        if realigned:
            # When the source changes, translations made from that source are
            # stale: they are marked to be redone, not deleted. Without this,
            # `verify_coverage` would keep reporting "covered" for a payload
            # that no longer matches — and for a concept like confirm.* that
            # means a user who can no longer confirm anything (§2.8).
            placeholders = ",".join("?" for _ in payloads)
            conn.execute(
                "UPDATE detection_lexicon SET needs_translation=1 "
                f"WHERE concept=? AND lang NOT IN ({placeholders})",
                (concept, *sorted(payloads)),
            )
        if wrote:
            # Dentro il try: un commit fallito e' un fallimento di questo
            # concetto come gli altri, e deve passare dal rollback invece di
            # lasciare la transazione aperta sulla connessione globale.
            conn.commit()
    except Exception:
        # The DB can be read-only (the sandbox mounts it --ro-bind) or busy on
        # the first boot after a deploy. The exception used to escape here: it
        # cut the seed in half — concepts declared AFTER were never registered,
        # `ensure_seeded` swallowed it and never retried — and it left an open
        # transaction on the global connection, blocking every other writer for
        # the life of the process. Now this one concept is rolled back and the
        # rest of the seed proceeds.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("detection_lexicon: seed of %r not written (DB read-only "
                    "or busy); the concept is left as it is",
                    concept, exc_info=True)
        return False
    if wrote:
        # §7.13: flush WAL→main così l'immutable-reader in sandbox vede il seed.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            pass
        _invalidate(concept)
    return wrote


def set_payload(concept: str, lang: str, payload, *,
                kind: str | None = None, match_mode: str | None = None,
                source_lang: str | None = None) -> None:
    """INSERT/REPLACE payload per (concept, lang). Usato da daemon e admin."""
    conn = _open()
    has_review_policy = _has_column(conn, "review_policy")
    policy_expr = "review_policy" if has_review_policy else "'automatic'"
    meta = conn.execute(
        "SELECT kind, match_mode, " + policy_expr
        + " FROM detection_lexicon WHERE concept=? "
        "ORDER BY CASE WHEN lang=? THEN 0 ELSE 1 END, lang LIMIT 1",
        (concept, lang),
    ).fetchone()
    kind = kind or (meta[0] if meta else "phrases")
    match_mode = match_mode or (meta[1] if meta else "substring")
    # The source declaration is authoritative. In particular, a human rewrite
    # must be able to promote a legacy third-language row from ``automatic``
    # to the newly declared ``manual`` policy; preserving the target row first
    # would leave that language permanently unusable by the safety gate.
    review_policy = _declared_review_policies.get(
        concept, meta[2] if meta else "automatic",
    )
    js = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if has_review_policy:
        conn.execute(
            "INSERT OR REPLACE INTO detection_lexicon(concept, lang, kind, "
            "match_mode, payload, needs_translation, source_lang, review_policy, "
            "version_hash, updated_at) VALUES (?,?,?,?,?,0,?,?,?,"
            "strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (concept, lang, kind, match_mode, js, source_lang, review_policy,
             _sha256(js)),
        )
    else:
        conn.execute(
            "INSERT OR REPLACE INTO detection_lexicon(concept, lang, kind, "
            "match_mode, payload, needs_translation, source_lang, version_hash, "
            "updated_at) VALUES (?,?,?,?,?,0,?,?,"
            "strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
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


def resource_for_language(concept: str, lang: str, *, fallback: bool = True,
                          ready_only: bool = False) -> dict | None:
    """Risorsa di detection per una lingua esplicita, senza mutare il turno.

    E' l'API dei generatori e dei gate RM-0005.  ``fallback=False`` misura la
    copertura nativa; ``fallback=True`` rende una risorsa operativa seguendo
    la stessa catena documentata del runtime.  ``ready_only`` esclude righe
    ancora marcate per riallineamento, anche quando conservano il vecchio
    payload come fallback temporaneo.
    """
    ensure_seeded()
    normalized = _i18n.normalize_language(lang)
    if not normalized:
        return None
    languages = [normalized]
    if fallback:
        for candidate in baseline_languages(concept):
            if candidate not in languages:
                languages.append(candidate)
    conn = _open()
    has_review_policy = _has_column(conn, "review_policy")
    policy_expr = "review_policy" if has_review_policy else "'automatic'"
    for candidate in languages:
        row = conn.execute(
            "SELECT kind, match_mode, payload, needs_translation, "
            "source_lang, version_hash, source_text_hash, " + policy_expr + " "
            "FROM detection_lexicon WHERE concept=? AND lang=? "
            "AND payload IS NOT NULL",
            (concept, candidate),
        ).fetchone()
        if not row or (ready_only and int(row[3] or 0)):
            continue
        try:
            payload = json.loads(row[2])
        except Exception:
            continue
        return {
            "concept": concept,
            "requested_lang": normalized,
            "lang": candidate,
            "kind": row[0],
            "match_mode": row[1],
            "payload": payload,
            "needs_translation": bool(row[3]),
            "source_lang": row[4],
            "version_hash": row[5],
            "source_text_hash": row[6],
            "review_policy": (
                row[7] if has_review_policy
                else _declared_review_policies.get(concept, row[7])
            ),
            "fallback": candidate != normalized,
        }
    return None


def mapping_for_language(concept: str, lang: str, *, fallback: bool = True,
                         ready_only: bool = False) -> dict:
    """Mapping localizzato esatto/fallback per una risorsa ``kind=mapping``."""
    resource = resource_for_language(
        concept, lang, fallback=fallback, ready_only=ready_only,
    )
    if not resource or resource["kind"] != "mapping":
        return {}
    payload = resource["payload"]
    return dict(payload) if isinstance(payload, dict) else {}


def native_ready_forms(concept: str, *, require_manual: bool = False) -> list[str]:
    """Validated native phrase forms admitted for the active language."""
    resource = resource_for_language(
        concept, current_lang(), fallback=False, ready_only=True,
    )
    if (not resource or resource.get("kind") != "phrases"
            or not isinstance(resource.get("payload"), list)
            or not resource["payload"]
            or not all(
                isinstance(form, str) and form.strip()
                for form in resource["payload"]
            )
            or (require_manual
                and resource.get("review_policy") != "manual")):
        return []
    return [form.strip() for form in resource["payload"]]


def validate_mapping_payload(source: dict, candidate) -> dict:
    """Valida strutturalmente una localizzazione di tipo mapping.

    Le chiavi canoniche devono restare esattamente quelle della sorgente e
    ogni categoria deve conservare almeno una forma naturale non vuota. Le
    collisioni fra categorie sono riportate, non cancellate: possono essere
    vera polisemia della lingua e il consumer deve risolverle senza assegnare
    arbitrariamente un significato.
    """
    source_keys = {str(key) for key in source} if isinstance(source, dict) else set()
    candidate_keys = (
        {str(key) for key in candidate} if isinstance(candidate, dict) else set()
    )
    missing = sorted(source_keys - candidate_keys)
    extra = sorted(candidate_keys - source_keys)
    invalid: list[str] = []
    normalized: dict[str, list[str]] = {}
    owners: dict[str, set[str]] = {}
    if isinstance(candidate, dict):
        for canonical, raw_forms in candidate.items():
            if not isinstance(raw_forms, list):
                invalid.append(str(canonical))
                continue
            forms: list[str] = []
            seen: set[str] = set()
            for raw in raw_forms:
                if not isinstance(raw, str):
                    invalid.append(str(canonical))
                    continue
                form = str(raw or "").strip()
                folded = form.casefold()
                if not form or folded in seen:
                    continue
                seen.add(folded)
                forms.append(form)
                owners.setdefault(folded, set()).add(str(canonical))
            if not forms:
                invalid.append(str(canonical))
            normalized[str(canonical)] = forms
    ambiguities = {
        surface: sorted(canonicals)
        for surface, canonicals in sorted(owners.items())
        if len(canonicals) > 1
    }
    return {
        "ok": bool(source_keys) and not missing and not extra and not invalid,
        "mapping": normalized,
        "missing_keys": missing,
        "extra_keys": extra,
        "invalid_keys": sorted(set(invalid)),
        "ambiguous_surfaces": ambiguities,
    }


def baseline_languages(concept: str | None = None) -> list[str]:
    """Enumerate editorial source languages from registry metadata.

    A row is a baseline when it names itself as source. English is ordered
    first only because it is the signed safe-bootstrap language, not because
    the runtime owns a finite language list.
    """
    conn = _open()
    where = "source_lang=lang AND payload IS NOT NULL"
    params: tuple = ()
    if concept is not None:
        where += " AND concept=?"
        params = (concept,)
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT lang FROM detection_lexicon WHERE " + where
        + " ORDER BY CASE WHEN lang=? THEN 0 ELSE 1 END, lang",
        (*params, _C.BOOTSTRAP_LANGUAGE),
    )]


def manual_review_concepts() -> frozenset[str]:
    """Concepts governed by registry policy rather than a Python name list."""
    ensure_seeded()
    conn = _open()
    declared = {
        concept for concept, policy in _declared_review_policies.items()
        if policy == "manual"
    }
    if not _has_column(conn, "review_policy"):
        return frozenset(declared)
    persisted = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT concept FROM detection_lexicon "
            "WHERE review_policy='manual' ORDER BY concept"
        )
    }
    return frozenset(declared | persisted)


def _union_langs(concept: str | None = None) -> list[str]:
    """Languages to merge: current locale plus registered baselines."""
    langs = [current_lang()]
    for seed in baseline_languages(concept):
        if seed not in langs:
            langs.append(seed)
    return langs


def _resolve(concept: str):
    """Risolve (kind, match_mode, merged_payload, langs) unendo le forme su
    `{lingua_corrente} ∪ {baseline editoriali registrate}`.

    Anti-silenzio: se la lingua corrente non e' seedata e non ha payload
    nativo, registra il gap (deduplicato). Il match continua via unione delle
    baseline (best-effort sui prestiti), ma `verify_coverage` allo startup rende il
    gap ESPLICITO invece di lasciarlo silenzioso.
    """
    ensure_seeded()
    cur = current_lang()
    key = (concept, cur)
    if key in _cache:
        return _cache[key]
    baselines = baseline_languages(concept)
    if cur not in baselines and _native(concept, cur) is None:
        gap = (concept, cur)
        if gap not in _coverage_gaps_logged:
            _coverage_gaps_logged.add(gap)
            log.warning(
                "detection_lexicon: concept %r privo di forme native per "
                "lingua %r — match via baseline editoriali (best-effort); esegui il "
                "daemon di traduzione per coprire la lingua", concept, cur)
    # The authoritative form comes from the SEED languages, not from the first
    # row that happens to be found. Two reasons, both measured:
    #  - a translated row carrying `match_mode='substring'` could loosen the
    #    comparison for every other language, and on `confirm.*` that meant
    #    «sinistra» counted as a yes;
    #  - an installation in a third language that still holds the OLD kind of a
    #    concept (the seed only realigns the languages it declares) would win
    #    the vote, discard it/en, and leave the user unable to confirm anything
    #    — silently. Letting the seed decide keeps the loanword forms usable.
    kind = match_mode = None
    for _seed_lang in baselines:
        _seed_row = _native(concept, _seed_lang)
        if _seed_row is not None:
            if kind is None:
                kind = _seed_row[0]
            elif _seed_row[0] != kind:
                log.warning(
                    "detection_lexicon: baseline kind disagreement for %r: %r/%r",
                    concept, kind, _seed_row[0],
                )
                continue
            if match_mode is None or _seed_row[1] == "word":
                match_mode = _seed_row[1]
    merged_list: list = []
    merged_map: dict = {}
    used: list[str] = []
    seen: set = set()
    for lang in _union_langs(concept):
        nat = _native(concept, lang)
        if nat is None:
            continue
        row_kind, row_mode, payload = nat
        if kind is None:
            kind, match_mode = row_kind, row_mode
        elif row_kind != kind:
            # Rows of a different form are never mixed: a `regex` payload
            # merged into a phrase list would sit among the forms as a literal
            # string, match nothing, and the hole would be invisible.
            log.warning("detection_lexicon: concept %r language %r has kind %r "
                        "instead of %r — row ignored in the union",
                        concept, lang, row_kind, kind)
            continue
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


def phrase_before(concept: str, text: str, end: int) -> tuple[int, int] | None:
    """Span of the longest localized phrase immediately before ``end``.

    Only whitespace may separate the phrase from ``end``.  The matcher is
    Unicode-aware and consumes the complete registry form, so translated
    accents and multiword surfaces are data rather than Python branches.
    """
    if not isinstance(text, str) or not isinstance(end, int):
        return None
    boundary = max(0, min(end, len(text)))
    prefix = text[:boundary]
    found: list[tuple[int, int, str]] = []
    for raw_form in forms(concept):
        if not isinstance(raw_form, str):
            continue
        form = raw_form.strip()
        if not form:
            continue
        pattern = re.compile(
            r"(?<!\w)(?P<form>" + re.escape(form) + r")\s*\Z",
            re.IGNORECASE | re.UNICODE,
        )
        match = pattern.search(prefix)
        if match is not None:
            found.append((
                match.start("form"), match.end("form"), form.casefold(),
            ))
    if not found:
        return None
    start, stop, _form = min(
        found, key=lambda item: (-(item[1] - item[0]), item[0], item[2]))
    return start, stop


def phrase_spans(concept: str, text: str, *, start: int = 0) -> list[tuple[int, int]]:
    """Non-overlapping spans of complete localized forms in ``text``.

    The longest form wins when registry entries overlap at the same offset.
    This lets consumers reason about clause polarity without duplicating any
    language-specific surface in their own module.
    """
    if not isinstance(text, str) or not isinstance(start, int):
        return []
    boundary = max(0, min(start, len(text)))
    candidates: list[tuple[int, int, str]] = []
    for raw_form in forms(concept):
        if not isinstance(raw_form, str):
            continue
        form = raw_form.strip()
        if not form:
            continue
        pattern = re.compile(
            r"(?<!\w)(?P<form>" + re.escape(form) + r")(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        for match in pattern.finditer(text, boundary):
            candidates.append((
                match.start("form"), match.end("form"), form.casefold(),
            ))
    chosen: list[tuple[int, int]] = []
    occupied_until = boundary
    for begin, stop, _form in sorted(
            candidates, key=lambda item: (item[0], -(item[1] - item[0]), item[2])):
        if begin < occupied_until:
            continue
        chosen.append((begin, stop))
        occupied_until = stop
    return chosen


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


def _last_phrase_end(text: str, candidates: list[str]) -> int:
    """End offset of the last whole-phrase occurrence, or ``-1``."""
    last = -1
    low = text.lower()
    for candidate in candidates:
        pattern = re.compile(r"\b" + re.escape(candidate.lower()) + r"\b")
        for found in pattern.finditer(low):
            last = max(last, found.end())
    return last


def polarity_state_at(
        text: str, start: int, *, command_scope: bool = False,
        target_scope: bool = False) -> str:
    """Return ``asserted``, ``negated`` or ``unavailable`` for one match.

    The rule is domain-neutral: the clause begins after the last punctuation
    boundary or contrast conjunction.  A translatable syntax-level negation
    inside that clause makes the following match non-asserted.  Callers keep
    their domain markers; polarity and language data remain centralized here.
    ``command_scope`` applies the stricter punctuation rule needed for shell
    syntax. ``target_scope`` binds a reviewed sequence immediately before a
    placement anchor. The explicit unavailable state lets safety consumers
    distinguish a prohibition from missing native grammar.
    """
    if (not isinstance(text, str) or not isinstance(start, int)
            or (command_scope and target_scope)):
        return "unavailable"
    polarity_concepts = [
        "syntax.negation", "syntax.inhibition", "syntax.contrast",
        "syntax.negative_coordination", "syntax.sequence",
    ]
    if command_scope or target_scope:
        polarity_concepts.append("syntax.command_invocation")
    for concept in polarity_concepts:
        if not native_ready_forms(concept, require_manual=True):
            # Polarity is safety relevant. Baseline fallback is useful for
            # ordinary recognition, but it cannot prove that a negation in a
            # partially materialized active language was understood.
            log.warning(
                "detection_lexicon: native ready polarity unavailable for %r",
                concept,
            )
            return "unavailable"
    negation_forms = forms("syntax.negation")
    inhibition_forms = forms("syntax.inhibition")
    contrast_forms = forms("syntax.contrast")
    prefix = text[:max(0, min(start, len(text)))]
    hard_boundaries = ".;!?"
    punctuation_end = max(
        (prefix.rfind(character) + 1 for character in hard_boundaries),
        default=0,
    )
    local_prefix = prefix[punctuation_end:]

    if command_scope:
        # Commas and colons may coordinate or introduce negative command
        # targets. They reset polarity only when the marker being evaluated is
        # itself immediately preceded by an invocation that begins just after
        # the separator. An invocation for explanatory prose must not reopen a
        # later marker and expose the guarded admin route.
        separator_end = max(
            local_prefix.rfind(",") + 1,
            local_prefix.rfind(":") + 1,
        )
        if separator_end:
            invocation = phrase_before(
                "syntax.command_invocation", text, start,
            )
            invocation_start = (
                invocation[0] - punctuation_end
                if invocation is not None else -1
            )
            gap = (
                local_prefix[separator_end:invocation_start]
                if invocation_start >= separator_end else ""
            )
            sequence_spans = phrase_spans("syntax.sequence", gap)
            gap_is_sequence = any(
                not gap[:begin].strip() and not gap[stop:].strip()
                for begin, stop in sequence_spans
            )
            if (invocation_start >= separator_end
                    and (not gap.strip() or gap_is_sequence)):
                punctuation_end += separator_end
                local_prefix = local_prefix[separator_end:]
    elif target_scope:
        # A reviewed sequence immediately before this target anchor starts a
        # new placement clause (``not on server, and on pc-roberto``). Other
        # comma/colon forms retain polarity so coordinated negative target
        # lists cannot become affirmative by punctuation alone.
        separator_end = max(
            local_prefix.rfind(",") + 1,
            local_prefix.rfind(":") + 1,
        )
        if separator_end:
            sequence = phrase_before("syntax.sequence", text, start)
            sequence_start = (
                sequence[0] - punctuation_end
                if sequence is not None else -1
            )
            negative_coordination_forms = {
                form.casefold()
                for form in forms("syntax.negative_coordination")
            }
            sequence_surface = (
                text[sequence[0]:sequence[1]].casefold()
                if sequence is not None else ""
            )
            direct_sequence = (
                sequence_start >= separator_end
                and sequence_surface not in negative_coordination_forms
                and not local_prefix[
                    separator_end:sequence_start
                ].strip()
            )
            invocation = phrase_before(
                "syntax.command_invocation", text, start,
            )
            invocation_start = (
                invocation[0] - punctuation_end
                if invocation is not None else -1
            )
            invocation_gap = (
                local_prefix[separator_end:invocation_start]
                if invocation_start >= separator_end else ""
            )
            sequence_spans = phrase_spans(
                "syntax.sequence", invocation_gap,
            )
            sequenced_invocation = (
                invocation_start >= separator_end
                and (
                    not invocation_gap.strip()
                    or any(
                        not invocation_gap[:begin].strip()
                        and not invocation_gap[stop:].strip()
                        for begin, stop in sequence_spans
                    )
                )
            )
            if direct_sequence or sequenced_invocation:
                punctuation_end += separator_end
                local_prefix = local_prefix[separator_end:]
    else:
        # A comma normally resets domain-neutral polarity. A reviewed
        # coordination at the start of its suffix is the exception: it keeps
        # negative target lists such as ``not on the computer, or on the
        # server`` negative without treating domain verbs as command syntax.
        comma_end = local_prefix.rfind(",") + 1
        colon_end = local_prefix.rfind(":") + 1
        separator_end = max(comma_end, colon_end)
        if separator_end:
            suffix = local_prefix[separator_end:]
            negative_coordination_forms = {
                form.casefold()
                for form in forms("syntax.negative_coordination")
            }
            sequence_reset = any(
                not suffix[:begin].strip()
                and suffix[begin:stop].casefold()
                    not in negative_coordination_forms
                for begin, stop in phrase_spans("syntax.sequence", suffix)
            )
            coordinated = any(
                not suffix[:begin].strip()
                for begin, _stop in phrase_spans(
                    "syntax.negative_coordination", suffix,
                )
            )
            # A colon commonly introduces a target list and therefore carries
            # polarity unless an explicit sequence starts a new clause. A
            # comma keeps its historical reset when it is not coordinating.
            colon_is_latest = colon_end == separator_end
            if sequence_reset or (not coordinated and not colon_is_latest):
                punctuation_end += separator_end
                local_prefix = suffix
    inhibition_end = _last_phrase_end(local_prefix, inhibition_forms)
    contrast_end = _last_phrase_end(local_prefix, contrast_forms)
    if inhibition_end > contrast_end:
        # Inhibitory phrases are polarity, not a new affirmative clause.
        # Check them before ``syntax.contrast`` so ``invece di`` / ``instead
        # of`` cannot be shortened to the contrast word and reset itself.  A
        # later real contrast (``evita X ma esegui Y``) starts a new clause.
        return "negated"
    clause_start = punctuation_end
    if contrast_end >= 0:
        clause_start += contrast_end
    clause = prefix[clause_start:]
    return (
        "negated" if match_any(negation_forms, clause, mode="word")
        else "asserted"
    )


def asserted_at(
        text: str, start: int, *, command_scope: bool = False,
        target_scope: bool = False) -> bool:
    """Whether a surface match is asserted under the selected syntax scope."""
    return polarity_state_at(
        text, start, command_scope=command_scope, target_scope=target_scope,
    ) == "asserted"


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
        # Un lessico VUOTO non e' un lessico coperto: senza concetti registrati
        # (seed fallito, DB assente) `not missing` era vero e la guardia
        # anti-silenzio taceva proprio nel caso peggiore.
        "ok": bool(cov) and not missing,
        "total": len(cov),
        "covered": len(cov) - len(missing),
        "missing": missing,
    }


def list_pending(limit: int = 100, exclude_concepts: tuple = (),
                 exclude_kinds: tuple = ()) -> list[dict]:
    """Rows with needs_translation=1 plus their source payload, for the daemon.

    The two exclusions are for rows a model must not localize on its own
    (regex, consent gates). They are filtered in SQL rather than skipped by
    the caller because the caller reads a bounded window: a handful of rows
    that can never be translated would otherwise occupy the same slots at
    every fire and starve the work that CAN be done. Coverage still counts
    them as gaps — that is `verify_coverage`, a different question.
    """
    conn = _open()
    where = ["d.needs_translation=1"]
    params: list = []
    if exclude_concepts:
        where.append("d.concept NOT IN (%s)"
                     % ",".join("?" * len(exclude_concepts)))
        params.extend(exclude_concepts)
    if exclude_kinds:
        where.append("d.kind NOT IN (%s)" % ",".join("?" * len(exclude_kinds)))
        params.extend(exclude_kinds)
    # the design guide §2.4: a cap of 0 means «no limit». SQLite reads a negative
    # LIMIT as unbounded, so the convention translates directly and a caller
    # that wants the whole picture does not have to invent a big number.
    params.append(int(limit) if int(limit) > 0 else -1)
    rows = conn.execute(
        "SELECT d.concept, d.lang, d.source_lang, d.kind, d.match_mode, "
        "(SELECT payload FROM detection_lexicon WHERE concept=d.concept "
        " AND lang=d.source_lang) AS source_payload "
        "FROM detection_lexicon d WHERE " + " AND ".join(where) + " LIMIT ?",
        tuple(params),
    ).fetchall()
    return [{"concept": r[0], "target_lang": r[1], "source_lang": r[2],
             "kind": r[3], "match_mode": r[4], "source_payload": r[5]}
            for r in rows]


def mark_for_translation(
    concept: str,
    target_lang: str,
    source_lang: str = _C.BOOTSTRAP_LANGUAGE,
) -> None:
    """Placeholder row (payload NULL, needs_translation=1) per lazy translate."""
    conn = _open()
    meta = conn.execute(
        "SELECT kind, match_mode, review_policy FROM detection_lexicon WHERE concept=? "
        "AND lang=? LIMIT 1", (concept, source_lang),
    ).fetchone()
    if not meta:
        return
    conn.execute(
        "INSERT OR IGNORE INTO detection_lexicon(concept, lang, kind, "
        "match_mode, payload, needs_translation, source_lang, review_policy, updated_at) "
        "VALUES (?,?,?,?,NULL,1,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
        (concept, target_lang, meta[0], meta[1], source_lang, meta[2]),
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
    src_lang = (
        (src_row[0] if src_row else None) or _C.BOOTSTRAP_LANGUAGE
    )
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
    accodati. La sorgente è la prima baseline editoriale non vuota registrata.
    """
    n = 0
    for c in registered_concepts():
        if has_native(c, lang):
            continue
        sources = [
            candidate for candidate in baseline_languages(c)
            if (_native(c, candidate) or (None, None, None))[2]
        ]
        if not sources:
            continue
        src = sources[0]
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
