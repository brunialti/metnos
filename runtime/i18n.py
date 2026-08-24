#!/usr/bin/env python3
"""i18n — DB centralizzato testi + fetcher con fallback chain.

Design 1/5/2026 sera (vedi `metnos_design_i18n_final.md`):
- Una sola lingua operativa per istanza, risolta da runtime.config
- DB sqlite `~/.local/share/metnos/i18n.sqlite`: (key, lang, text, needs_translation, source_lang)
- Fetch_key EN canonical
- Fallback chain runtime: current_lang → bootstrap language → "<missing:{key}>"
- Lazy translation via daemon introspettivo (vedi `i18n_translator.py`, futuro)

API:
    current_lang() -> str         lingua d'istanza fissata e verificata al boot
    get(key, **kwargs) -> str     fetch con fallback + .format(**kwargs)
    set(key, lang, text)          INSERT/REPLACE
    mark_for_translation(key, target_lang, source_lang) crea placeholder row
    list_pending(limit=50)        rows con needs_translation=1 (per daemon translator)
    set_translated(key, lang, text) UPDATE post-traduzione
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import Path
import sqlite3
import threading

import config as _C  # §7.11 — rispetta METNOS_USER_DATA
DB_PATH = _C.DB_I18N
DEFAULT_LANG = _C.BOOTSTRAP_LANGUAGE
_SEED_DB_PATH = (
    Path(__file__).resolve().parent.parent / "install/data/i18n_seed.sqlite"
)
_log = logging.getLogger(__name__)

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
CREATE TABLE IF NOT EXISTS i18n_seed_state (
    key TEXT NOT NULL,
    lang TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    PRIMARY KEY (key, lang)
);
"""


def _hash_text(text: str) -> str:
    """SHA-256 short hash (16-char). Legacy: usato per `source_hash` (compat)."""
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _sha256_full(text: str) -> str:
    """SHA-256 hex full prefix-encoded (`sha256:<hex>`). Usato per `version_hash`
    e `source_text_hash` del pattern latest-wins (estensione ADR 0092)."""
    from hashutil import sha256_prefixed
    return sha256_prefixed(text)

_conn: sqlite3.Connection | None = None
_conn_owner_thread_id: int | None = None
_conn_path: str | None = None
_thread_connections = threading.local()
_connection_init_lock = threading.RLock()
_instance_context: ContextVar[str | None] = ContextVar(
    "metnos_i18n_language", default=None,
)


def _normalize_lang(value: str | None) -> str:
    """Normalizza un codice lingua senza inventare una lingua supportata.

    Sono ammessi tag BCP-47 semplici (``it``, ``en-GB``, ``pt-BR``). Un valore
    non valido non entra nel contesto: il chiamante ricade sulla lingua
    dell'istanza e il catalogo conserva la propria catena di fallback.
    """
    return _C.normalize_language_tag(value)


def normalize_language(value: str | None) -> str:
    """API pubblica per la grammatica dei codici lingua del catalogo."""
    return _normalize_lang(value)


def current_lang() -> str:
    """Lingua unica dell'istanza, fissata e verificata al boot."""
    contextual = _normalize_lang(_instance_context.get())
    return contextual if contextual == _C.INSTANCE_LANG else _C.INSTANCE_LANG


@contextmanager
def language_context(lang: str | None):
    """Propaga la lingua d'istanza senza consentire override di richiesta.

    Il parametro resta temporaneamente per i chiamanti che saranno rimossi in
    RM-0005/F1. Anche un valore valido differente viene ignorato: una richiesta
    non può sostituire la configurazione globale dell'istanza.
    """
    normalized = _normalize_lang(lang)
    if normalized and normalized != _C.INSTANCE_LANG:
        _log.debug(
            "request language %s ignored; instance language is %s",
            normalized, _C.INSTANCE_LANG,
        )
    token = _instance_context.set(_C.INSTANCE_LANG)
    try:
        yield current_lang()
    finally:
        _instance_context.reset(token)


@contextmanager
def instance_language_context():
    """Propaga esclusivamente la lingua autorevole dell'istanza.

    È il solo contesto ammesso nel codice operativo.  Non accetta input da
    richieste, utenti o workload e rende quindi impossibile trasformare una
    preferenza locale in un override della configurazione firmata.
    """
    token = _instance_context.set(_C.INSTANCE_LANG)
    try:
        yield current_lang()
    finally:
        _instance_context.reset(token)


def available_languages() -> tuple[str, ...]:
    """Lingue presenti nel catalogo, ordinate per gli strumenti amministrativi.

    Il registro i18n, non una lista della UI, è la fonte canonica. Le singole
    chiavi incomplete ricadono sulla lingua bootstrap secondo la normale
    catena di fallback; la selezione operativa resta a livello d'istanza.
    """
    rows = _open().execute(
        "SELECT DISTINCT lower(lang) FROM i18n "
        "WHERE lang IS NOT NULL AND trim(lang)<>'' ORDER BY lower(lang)"
    ).fetchall()
    return tuple(sorted({
        lang for (raw,) in rows
        if (lang := _normalize_lang(raw))
    }))


def _open() -> sqlite3.Connection:
    """Apre una connessione per thread sul DB di processo.

    SQLite permette di disattivare il controllo di appartenenza del thread,
    ma una stessa connessione non diventa per questo sicura per operazioni
    concorrenti. Il server usa thread di lavoro per alcune sonde: condividere
    il vecchio singleton produceva sporadicamente ``InterfaceError`` durante
    il rendering amministrativo. Ogni thread riceve quindi la propria
    connessione WAL; ``_conn`` resta la connessione primaria per compatibilita'
    con gli strumenti amministrativi e i test esistenti.
    """
    global _conn, _conn_owner_thread_id, _conn_path
    thread_id = threading.get_ident()
    wanted_path = str(DB_PATH)
    local_conn = getattr(_thread_connections, "conn", None)
    local_path = getattr(_thread_connections, "path", None)

    with _connection_init_lock:
        if (_conn is not None and _conn_owner_thread_id == thread_id
                and _conn_path == wanted_path):
            _thread_connections.conn = _conn
            _thread_connections.path = wanted_path
            return _conn
        if (_conn is not None and local_conn is not None
                and local_path == wanted_path):
            return local_conn

        try:
            opened = _open_rw()
        except sqlite3.OperationalError:
            # DB montato READ-ONLY (sandbox bwrap: §7.13 — l'executor legge i
            # messaggi ma non può scrivere schema/migration, già fatti dal
            # server). Apri immutable read-only: niente -wal/-shm, lock-free.
            opened = sqlite3.connect(
                f"file:{DB_PATH}?mode=ro&immutable=1",
                uri=True, check_same_thread=False)
        _thread_connections.conn = opened
        _thread_connections.path = wanted_path
        if _conn is None or _conn_path != wanted_path:
            _conn = opened
            _conn_owner_thread_id = thread_id
            _conn_path = wanted_path
        return opened


def _open_rw() -> sqlite3.Connection:
    """Apertura READ-WRITE con schema+migration (percorso server)."""
    if True:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        # WAL server-side (1 writer + N reader lock-free, condiviso col daemon
        # telegram). La sandbox bwrap (§7.13) monta il DB READ-ONLY e lo apre
        # `immutable` (fallback in _open): legge il MAIN file → `set()` fa un
        # checkpoint(TRUNCATE) dopo ogni scrittura così le chiavi fresche sono
        # subito nel main (no staleness per l'immutable-reader).
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
        # Upgrade non distruttivo del catalogo per ogni utente. Il seed è la
        # baseline distribuita: aggiungiamo soltanto coppie (key, lang)
        # assenti, senza sovrascrivere traduzioni o revisioni locali. Questo
        # rende disponibili nuove stringhe e nuove lingue anche a chi conserva
        # il proprio i18n.sqlite fra un rilascio e il successivo.
        try:
            _merge_missing_seed_rows(c)
        except sqlite3.Error as exc:
            # Un catalogo utente già valido deve restare usabile anche se il
            # seed del checkout è temporaneamente assente o illeggibile.
            _log.warning("i18n seed merge skipped: %s", exc)
        c.commit()
    return c


def _merge_missing_seed_rows(
    connection: sqlite3.Connection,
    seed_path: Path | str | None = None,
) -> int:
    """Merge a released seed without overwriting user-authored translations.

    Missing rows are inserted.  Existing rows are refreshed only when their
    actual text is the current seed text, the last seed text observed by this
    installation, or a historical release recorded by the seed database.
    Comparing the text hash instead of trusting row metadata also fails safe
    after a manual database edit that forgot to update ``version_hash``.

    The intersection of the two schemas keeps upgrades compatible with older
    per-user databases.  The return value remains the number of newly inserted
    ``(key, lang)`` rows; seed refreshes are deliberately not counted as new
    catalog entries.
    """
    seed = Path(seed_path) if seed_path is not None else _SEED_DB_PATH
    if not seed.is_file():
        return 0
    try:
        if seed.resolve() == Path(DB_PATH).resolve():
            return 0
    except OSError:
        pass

    source = sqlite3.connect(f"file:{seed}?mode=ro", uri=True)
    try:
        source_columns = {
            row[1] for row in source.execute("PRAGMA table_info(i18n)")
        }
        target_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(i18n)")
        ]
        columns = [name for name in target_columns if name in source_columns]
        if not {"key", "lang", "text"}.issubset(columns):
            raise sqlite3.DatabaseError("seed i18n schema is incomplete")
        quoted = ", ".join(f'"{name}"' for name in columns)
        placeholders = ", ".join("?" for _ in columns)
        key_index = columns.index("key")
        lang_index = columns.index("lang")
        text_index = columns.index("text")
        non_key_columns = [
            name for name in columns if name not in {"key", "lang"}
        ]
        assignments = ", ".join(
            f'"{name}"=?' for name in non_key_columns
        )

        history_exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='i18n_seed_history'"
        ).fetchone() is not None
        history = (
            {
                (str(key), str(lang), str(version_hash))
                for key, lang, version_hash in source.execute(
                    "SELECT key, lang, version_hash FROM i18n_seed_history"
                )
            }
            if history_exists
            else frozenset()
        )

        inserted = 0
        for values in source.execute(f"SELECT {quoted} FROM i18n"):
            row = dict(zip(columns, values, strict=True))
            key = str(values[key_index])
            lang = str(values[lang_index])
            seed_text = values[text_index]
            seed_version = _sha256_full(seed_text or "")
            current = connection.execute(
                "SELECT text FROM i18n WHERE key=? AND lang=?",
                (key, lang),
            ).fetchone()

            if current is None:
                connection.execute(
                    f"INSERT INTO i18n ({quoted}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1
                refresh = True
            else:
                current_text = current[0]
                current_version = _sha256_full(current_text or "")
                tracked = connection.execute(
                    "SELECT version_hash FROM i18n_seed_state "
                    "WHERE key=? AND lang=?",
                    (key, lang),
                ).fetchone()
                refresh = (
                    current_text is None
                    or current_version == seed_version
                    or (tracked is not None and current_version == tracked[0])
                    or (key, lang, current_version) in history
                )
                if refresh:
                    connection.execute(
                        f"UPDATE i18n SET {assignments} "
                        "WHERE key=? AND lang=?",
                        tuple(row[name] for name in non_key_columns)
                        + (key, lang),
                    )

            if refresh:
                connection.execute(
                    "INSERT INTO i18n_seed_state(key, lang, version_hash) "
                    "VALUES (?, ?, ?) ON CONFLICT(key, lang) DO UPDATE SET "
                    "version_hash=excluded.version_hash",
                    (key, lang, seed_version),
                )
        return inserted
    finally:
        source.close()


def _language_chain(lang: str | None = None) -> tuple[str, ...]:
    """Catena localizzata deterministica senza modificare il contesto.

    ``lang`` e' la lingua di una risorsa da renderizzare o verificare, non un
    override operativo per utente/turno.  La stessa funzione governa il
    percorso normale e i generatori RM-0005, evitando due fallback diversi.
    """
    requested = _normalize_lang(lang) or current_lang()
    ordered = [requested]
    bootstrap = _normalize_lang(_C.BOOTSTRAP_LANGUAGE)
    if bootstrap and bootstrap not in ordered:
        ordered.append(bootstrap)
    return tuple(ordered)


def language_chain(lang: str | None = None) -> tuple[str, ...]:
    """API pubblica della catena di fallback localizzata."""
    return _language_chain(lang)


def get_for_language(key: str, lang: str, /, **kwargs) -> str:
    """Fetch di una risorsa per lingua esplicita con fallback catalogato.

    Serve ai renderer e ai controlli di localizzazione. Non cambia
    ``current_lang`` e quindi non consente preferenze linguistiche per
    richiesta in contrasto con RM-0005.
    """
    resource = resource_for_language(key, lang, fallback=True)
    if resource:
        template = resource["text"]
        try:
            return template.format(**kwargs) if kwargs else template
        except (KeyError, IndexError, ValueError):
            return template
    return f"<missing:{key}>"


def editorial_text(
    key: str,
    lang: str,
    baselines: dict[str, str],
) -> str:
    """Resolve one versioned catalog value without a finite language branch.

    Runtime registries may carry bundled editorial baselines for bootstrap and
    upgrade compatibility.  An exact, admitted catalog row wins; otherwise the
    declared baseline map is consulted in deterministic order.  New target
    languages therefore arrive through the ordinary RM-0005 message registry,
    without adding fields or conditionals to their consumers.
    """

    normalized = _normalize_lang(lang) or current_lang()
    exact = resource_for_language(
        key, normalized, fallback=False, ready_only=True,
    )
    if exact is not None:
        return str(exact["text"])
    clean = {
        (_normalize_lang(code) or str(code)): str(text)
        for code, text in baselines.items()
        if text is not None and str(text).strip()
    }
    if normalized in clean:
        return clean[normalized]
    bootstrap = _normalize_lang(_C.BOOTSTRAP_LANGUAGE)
    if bootstrap in clean:
        return clean[bootstrap]
    return next(iter(clean.values()), f"<missing:{key}>")


def resource_for_language(key: str, lang: str, *, fallback: bool = True,
                          ready_only: bool = False) -> dict | None:
    """Riga i18n esatta/fallback per renderer e coverage gate.

    Il normale ``get`` continua a usare anche un testo temporaneamente
    pending; un gate di rilascio passa invece ``ready_only=True`` per contare
    come coperta soltanto una risorsa allineata alla sorgente corrente.
    """
    normalized = _normalize_lang(lang)
    if not normalized:
        return None
    languages = list(_language_chain(normalized)) if fallback else [normalized]
    conn = _open()
    for candidate in languages:
        row = conn.execute(
            "SELECT text, needs_translation, source_lang, version_hash, "
            "source_text_hash FROM i18n WHERE key=? AND lang=?",
            (key, candidate),
        ).fetchone()
        if not row or not row[0] or (ready_only and int(row[1] or 0)):
            continue
        return {
            "key": key,
            "requested_lang": normalized,
            "lang": candidate,
            "text": row[0],
            "needs_translation": bool(row[1]),
            "source_lang": row[2],
            "version_hash": row[3],
            "source_text_hash": row[4],
            "fallback": candidate != normalized,
        }
    return None


def get(key: str, /, **kwargs) -> str:
    """Fetch testo per chiave. Fallback: current → bootstrap → <missing>.
    `**kwargs` passati a .format() sul template.

    La chiave e' POSIZIONALE-SOLTANTO: i `**kwargs` sono segnaposto del
    template, e senza questo vincolo un messaggio che contiene `{key}` — parola
    ovvia in mezzo dominio — esploderebbe con «got multiple values for argument
    'key'» invece di rendersi. Il nome del parametro non deve poter collidere
    con il vocabolario dei testi.
    """
    return get_for_language(key, current_lang(), **kwargs)


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
        "SELECT DISTINCT key FROM i18n WHERE lang=? "
        "AND text IS NOT NULL "
        "AND key GLOB '[A-Z]*_*' "  # solo UPPER_CASE_FAMILY style
        "ORDER BY key",
        (_C.BOOTSTRAP_LANGUAGE,),
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
    needs_translation: bool = False,
) -> bool:
    """Registra una chiave i18n SOLO se assente. Idempotente, no-op se gia'
    presente in DB. Ritorna True se ha scritto, False se gia' esisteva.

    Fase 11 (c) scaffolding 19/5/2026 v4: usato dal pipeline synth quando
    emette `messages.get("ERR_NUOVA")` con chiave non in DB, per evitare
    orfani. `needs_translation` descrive SOLO una traduzione da eseguire,
    non una review editoriale: la review delle stringhe generate usa il
    metadato separato `auto_translated` del job i18n.

    Convenzione naming chiavi: §6.1 + dedup the design guide 19/5 — famiglie
    ERR_/WARN_/MSG_/LOG_ + suffisso semantico breve (max 2-3 segmenti).
    """
    if key_exists(key):
        return False
    if text_en is None:
        # Una sola lingua disponibile: nessun falso testo EN. Il fallback di
        # get() serve l'IT finche' il daemon materializza la vera traduzione.
        set(key, "it", text_it)
        mark_for_translation(key, "en", "it")
        return True

    # Due testi completi sono un'unita' editoriale gia' allineata. Scriverli
    # con due set() consecutivi attiverebbe latest-wins sul primo e creerebbe
    # pending stantie. La write atomica registra invece la relazione IT→EN.
    # Positional compatibility contract: this legacy helper historically
    # receives an Italian editorial source followed by its English rendering.
    # New language-neutral callers use ``set_catalog_translations`` directly.
    set_catalog_translations(
        key, {"it": text_it, "en": text_en}, source_lang="it",
    )
    if needs_translation:
        # Compat esplicita: se il caller chiede davvero una traduzione,
        # invalida solo EN rispetto alla sorgente IT, mai entrambe le lingue.
        conn = _open()
        conn.execute(
            "UPDATE i18n SET needs_translation=1, source_lang='it' "
            "WHERE key=? AND lang='en'",
            (key,),
        )
        conn.commit()
        _checkpoint(conn)
    return True


def set_catalog_translations(
    key: str,
    translations: dict[str, str],
    *,
    source_lang: str = DEFAULT_LANG,
) -> None:
    """Scrive atomicamente un set di traduzioni gia' approvate.

    Il testo `source_lang` e' la sorgente editoriale; le altre righe salvano
    il suo hash come baseline. Nessuna riga viene accodata al traduttore.
    Questa API e' il percorso corretto per seed, manifest e registrazioni
    bilingui; `set()` resta l'edit di UNA lingua e quindi invalida le altre.
    """
    clean = {
        str(lang).strip().lower(): str(text)
        for lang, text in translations.items()
        if str(lang).strip() and text is not None
    }
    if not clean:
        raise ValueError("translations must contain at least one text")
    if source_lang not in clean:
        source_lang = DEFAULT_LANG if DEFAULT_LANG in clean else sorted(clean)[0]

    conn = _open()
    source_text = clean[source_lang]
    source_legacy_hash = _hash_text(source_text)
    source_version_hash = _sha256_full(source_text)
    for lang, text in clean.items():
        is_source = lang == source_lang
        conn.execute(
            "INSERT INTO i18n(key, lang, text, needs_translation, source_lang, "
            "source_hash, version_hash, source_text_hash, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?, "
            "strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
            "ON CONFLICT(key, lang) DO UPDATE SET "
            "text=excluded.text, needs_translation=0, "
            "source_lang=excluded.source_lang, source_hash=excluded.source_hash, "
            "version_hash=excluded.version_hash, "
            "source_text_hash=excluded.source_text_hash, "
            "updated_at=excluded.updated_at",
            (
                key,
                lang,
                text,
                None if is_source else source_lang,
                None if is_source else source_legacy_hash,
                _sha256_full(text),
                None if is_source else source_version_hash,
            ),
        )
    # Se lo schema esteso del job e' gia' presente, una write editoriale
    # manuale non deve restare marcata come auto-generata.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(i18n)")}
    optional_sets = []
    if "translated_at_iso" in cols:
        optional_sets.append("translated_at_iso=NULL")
    if "translated_by" in cols:
        optional_sets.append("translated_by=NULL")
    if "auto_translated" in cols:
        optional_sets.append("auto_translated=0")
    if optional_sets:
        conn.execute(
            f"UPDATE i18n SET {', '.join(optional_sets)} WHERE key=?",
            (key,),
        )
    conn.commit()
    _checkpoint(conn)


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
    _checkpoint(conn)


def _checkpoint(conn) -> None:
    """Flush WAL→main (§7.13): l'immutable-reader in sandbox legge il MAIN file,
    quindi le chiavi appena scritte devono esserci subito. Best-effort (no-op su
    connessione read-only/immutable)."""
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001
        pass


def mark_for_translation(key: str, target_lang: str, source_lang: str) -> None:
    """Accoda una traduzione, creando il target o invalidando quello esistente."""
    conn = _open()
    target_lang = str(target_lang).strip().lower()
    source_lang = str(source_lang).strip().lower()
    if not target_lang or not source_lang or target_lang == source_lang:
        raise ValueError("target_lang and source_lang must be different")
    source = conn.execute(
        "SELECT text FROM i18n WHERE key=? AND lang=?",
        (key, source_lang),
    ).fetchone()
    if not source or not source[0]:
        raise ValueError(f"missing source text for {key}[{source_lang}]")
    conn.execute(
        "INSERT INTO i18n(key, lang, text, needs_translation, source_lang, updated_at) "
        "VALUES (?, ?, NULL, 1, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
        "ON CONFLICT(key, lang) DO UPDATE SET needs_translation=1, "
        "source_lang=excluded.source_lang, "
        "updated_at=excluded.updated_at",
        (key, target_lang, source_lang),
    )
    conn.commit()
    _checkpoint(conn)


def list_pending(limit: int = 50) -> list[dict]:
    """Traduzioni realmente eseguibili, ordinate e senza righe stantie.

    Una pending e' azionabile solo se dichiara una lingua sorgente diversa
    dal target e la relativa riga sorgente contiene testo. Il filtro evita
    che flag legacy con `source_lang=NULL` o self-reference monopolizzino la
    testa della coda ad ogni ciclo.
    """
    conn = _open()
    rows = conn.execute(
        "SELECT i.key, i.lang AS target_lang, i.source_lang, s.text "
        "FROM i18n i "
        "JOIN i18n s ON s.key=i.key AND s.lang=i.source_lang "
        "WHERE i.needs_translation=1 "
        "AND i.source_lang IS NOT NULL AND i.source_lang!=i.lang "
        "AND s.text IS NOT NULL AND trim(s.text)!='' "
        "ORDER BY i.key, i.lang LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"key": r[0], "target_lang": r[1], "source_lang": r[2], "source_text": r[3]}
            for r in rows]


def count_pending(*, actionable_only: bool = False) -> int:
    """Conta la coda totale o soltanto le traduzioni realmente azionabili."""
    conn = _open()
    if not actionable_only:
        return int(conn.execute(
            "SELECT COUNT(*) FROM i18n WHERE needs_translation=1"
        ).fetchone()[0])
    return int(conn.execute(
        "SELECT COUNT(*) FROM i18n i "
        "JOIN i18n s ON s.key=i.key AND s.lang=i.source_lang "
        "WHERE i.needs_translation=1 "
        "AND i.source_lang IS NOT NULL AND i.source_lang!=i.lang "
        "AND s.text IS NOT NULL AND trim(s.text)!=''"
    ).fetchone()[0])


def repair_complete_pending() -> dict[str, int]:
    """Accetta come baseline il catalogo completo legacy e ripara i link.

    Per ogni chiave con tutti i testi presenti sceglie la row editata piu' di
    recente come sorgente, azzera gli eventuali flag e collega le altre lingue
    al suo hash. Normalizzare anche le righe gia' non-pending evita che un
    futuro `align_messages()` riaccodi falsi drift per metadati v2 mancanti.
    Stub auto-synth e righe `auto_translated=1` sono esclusi: non sono
    traduzioni editoriali approvate. Operazione amministrativa esplicita usata
    dal CLI dopo audit; non gira automaticamente al boot.
    """
    conn = _open()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(i18n)")}
    auto_expr = "coalesce(auto_translated,0)" if "auto_translated" in cols else "0"
    keys = [row[0] for row in conn.execute(
        "SELECT DISTINCT key FROM i18n ORDER BY key"
    )]
    repaired_keys = 0
    repaired_rows = 0
    skipped_keys = 0
    for key in keys:
        rows = conn.execute(
            f"SELECT lang, text, updated_at, {auto_expr} FROM i18n "
            "WHERE key=? ORDER BY updated_at DESC, lang DESC",
            (key,),
        ).fetchall()
        if not rows or any(
            not row[1] or row[1].startswith("<auto-synth: ") or int(row[3] or 0)
            for row in rows
        ):
            skipped_keys += 1
            continue
        src_lang, src_text = rows[0][0], rows[0][1]
        src_legacy_hash = _hash_text(src_text)
        src_version_hash = _sha256_full(src_text)
        for lang, text, _updated_at, _auto in rows:
            is_source = lang == src_lang
            conn.execute(
                "UPDATE i18n SET needs_translation=0, source_lang=?, "
                "source_hash=?, version_hash=?, source_text_hash=? "
                "WHERE key=? AND lang=?",
                (
                    None if is_source else src_lang,
                    None if is_source else src_legacy_hash,
                    _sha256_full(text),
                    None if is_source else src_version_hash,
                    key,
                    lang,
                ),
            )
            repaired_rows += 1
        repaired_keys += 1
    conn.commit()
    _checkpoint(conn)
    return {
        "keys": repaired_keys,
        "rows": repaired_rows,
        "skipped_keys": skipped_keys,
    }


def delete_keys(keys: list[str] | tuple[str, ...] | set[str]) -> dict[str, int]:
    """Elimina SOLO chiavi esatte (tutte le lingue); niente glob/prefix."""
    exact = sorted({str(key).strip() for key in keys if str(key).strip()})
    if not exact:
        return {"keys": 0, "rows": 0}
    conn = _open()
    placeholders = ",".join("?" for _ in exact)
    found = int(conn.execute(
        f"SELECT COUNT(DISTINCT key) FROM i18n WHERE key IN ({placeholders})",
        exact,
    ).fetchone()[0])
    cur = conn.execute(
        f"DELETE FROM i18n WHERE key IN ({placeholders})",
        exact,
    )
    conn.commit()
    _checkpoint(conn)
    return {"keys": found, "rows": int(cur.rowcount)}


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
