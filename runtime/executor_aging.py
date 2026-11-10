"""executor_aging.py — decay degli executor inattivi (3/5/2026).

Simmetrico a `apply_ager` per i mnest (mnestoma). Ogni invocazione di un
executor dal runtime passa da `record_invocation(name, ok)`, che e' `touch()`
con le due proprieta' che servono a un gancio di esercizio (niente registrazione
sotto pytest, nessuna eccezione verso il chiamante); il task notturno
`apply_executor_ager`
porta gli executor inattivi a `deprecated → archived` secondo soglie:

  - inattivita' >= EXECUTOR_DEPRECATED_DAYS (default 30) → deprecated
  - deprecated >= EXECUTOR_ARCHIVED_DAYS (default 14) → archived

Storage: SQLite single-file in `~/.local/state/metnos/executor_stats.db`.

Esclusi dal decay (sempre attivi):
  - tutti gli handcrafted dei builtin verb-unique (admin, sudoer): non sono
    visti dal pool catalog standard, hanno lifecycle proprio in memoria.
  - executor in lista PROTECTED_NAMES (vedi sotto): seed core sempre presenti.

Il loader (`runtime/loader.py`) consulta `executor_stats` al boot:
  - deprecated_at non NULL → manifest.lifecycle override = 'deprecated'
  - archived_at non NULL → escluso dal catalog visibile (filter_for_visibility).

Questo database e' una policy di VISIBILITA' esterna al contratto firmato: non
pubblica, ritira o modifica manifest. Il loader verifica l'unicita' globale dei
nomi autenticati prima di applicare gli override; anche l'unarchive manuale
riusa esplicitamente quel preflight prima di rendere possibile la riapparizione.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
from timefmt import now_iso_z

log = logging.getLogger("metnos.executor_aging")

DB_PATH = Path(
    os.environ.get(
        "METNOS_EXECUTOR_STATS_DB",
        str(_C.PATH_USER_STATE / "executor_stats.db"),
    )
)

DEPRECATED_DAYS = int(os.environ.get("METNOS_EXECUTOR_DEPRECATED_DAYS", "30"))
ARCHIVED_DAYS   = int(os.environ.get("METNOS_EXECUTOR_ARCHIVED_DAYS", "14"))

# Mai retirare questi: sono il seed minimo che il sistema deve avere
# anche dopo lunga inattivita' (es. utente in vacanza un mese). Senza
# questi il bootstrap di alcune pipeline tipiche fallirebbe.
PROTECTED_NAMES: frozenset[str] = frozenset({
    "get_now", "get_location", "read_files", "write_files",
    "list_dirs", "find_files", "send_messages", "read_messages",
    "filter_entries", "sort_entries", "describe_entries",
    "classify_entries", "compute_entries", "undo_last_turn",
    "get_files", "create_dirs", "delete_dirs", "move_files",
    "get_signatures", "set_signatures", "compute_signatures",
    "get_proposals", "get_processes", "get_urls", "filter_texts_lines",
})


SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_stats (
    name           TEXT PRIMARY KEY,
    last_used_at   TEXT,
    total_calls    INTEGER NOT NULL DEFAULT 0,
    last_call_ok   INTEGER,        -- bool, NULL se mai chiamato
    deprecated_at  TEXT,
    archived_at    TEXT,
    first_seen     TEXT NOT NULL DEFAULT (
                       strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    source         TEXT             -- 'handcrafted', 'synth:reactive',
                                    -- 'synth:introvertive_specialize',
                                    -- 'synth:introvertive_generalize',
                                    -- 'synth:promoted', NULL=unknown
);
CREATE INDEX IF NOT EXISTS idx_executor_used   ON executor_stats(last_used_at);
CREATE INDEX IF NOT EXISTS idx_executor_dep    ON executor_stats(deprecated_at);
CREATE INDEX IF NOT EXISTS idx_executor_arc    ON executor_stats(archived_at);
CREATE INDEX IF NOT EXISTS idx_executor_source ON executor_stats(source);

-- History event log (3/5/2026): per ogni transizione di lifecycle e
-- creazione, registriamo una riga. Uso: dashboard storica admin
-- (counts per source × lifecycle nel tempo, mortality curves, ecc.).
CREATE TABLE IF NOT EXISTS executor_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL DEFAULT (
                       strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    name           TEXT NOT NULL,
    event_kind     TEXT NOT NULL,   -- 'created', 'first_used',
                                    -- 'deprecated', 'archived',
                                    -- 'undeprecated', 'auto_applied'
    source         TEXT,            -- come executor_stats.source
    detail         TEXT             -- JSON serializzato (parent_name,
                                    -- arg_name, threshold_used, ecc.)
);
CREATE INDEX IF NOT EXISTS idx_history_ts     ON executor_history(ts);
CREATE INDEX IF NOT EXISTS idx_history_name   ON executor_history(name);
CREATE INDEX IF NOT EXISTS idx_history_kind   ON executor_history(event_kind);
CREATE INDEX IF NOT EXISTS idx_history_source ON executor_history(source);
"""


@dataclass
class ExecutorStat:
    name: str
    last_used_at: str | None
    total_calls: int
    last_call_ok: bool | None
    deprecated_at: str | None
    archived_at: str | None
    first_seen: str
    source: str | None = None

    @property
    def lifecycle_override(self) -> str | None:
        """Stato di lifecycle implicito da campi SQLite (priorita' al loader).
        archived > deprecated > None (nessun override)."""
        if self.archived_at:
            return "archived"
        if self.deprecated_at:
            return "deprecated"
        return None


def _open() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


_GENERATION_COLUMN_READY: set[str] = set()


def _ensure_generation_column(conn: sqlite3.Connection) -> None:
    key = str(DB_PATH)
    if key in _GENERATION_COLUMN_READY:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(executor_stats)")}
    if "loaded_generation" not in columns:
        conn.execute("ALTER TABLE executor_stats ADD COLUMN loaded_generation TEXT")
        conn.commit()
    _GENERATION_COLUMN_READY.add(key)


def note_generation(name: str, generation: str) -> bool:
    """Remember the revision loaded as ``name``; lift a restriction it did not earn.

    Deprecated and archived are judgements on one revision: unused for too
    long, a duplicate, a rolled-back creation. A new revision reaches the
    catalog only through Birth (with review and consent for generated code),
    so it starts unrestricted and with its own inactivity clock, instead of
    inheriting a verdict by name for ever. Returns True when a restriction
    was lifted. A name seen without a recorded revision is only recorded.
    """
    if not name or not generation:
        return False
    conn = _open()
    try:
        _ensure_generation_column(conn)
        row = conn.execute(
            "SELECT loaded_generation, deprecated_at, archived_at, source "
            "FROM executor_stats WHERE name = ?", (name,)).fetchone()
        if row is None or row["loaded_generation"] == generation:
            return False
        lifted = bool(row["loaded_generation"]
                      and (row["deprecated_at"] or row["archived_at"]))
        if lifted:
            conn.execute(
                "UPDATE executor_stats SET deprecated_at = NULL, archived_at = NULL, "
                "last_used_at = NULL, last_call_ok = NULL, first_seen = ?, "
                "loaded_generation = ? WHERE name = ?",
                (now_iso_z(), generation, name))
            _log_event(conn, name, "undeprecated", row["source"],
                       {"by": "new_generation"})
        else:
            conn.execute("UPDATE executor_stats SET loaded_generation = ? WHERE name = ?",
                         (generation, name))
        conn.commit()
        return lifted
    finally:
        conn.close()


def _open_read_only() -> sqlite3.Connection | None:
    """Open an existing statistics database without creating or migrating it."""
    if not DB_PATH.exists():
        return None
    absolute = Path(os.path.abspath(DB_PATH))
    connection = sqlite3.connect(
        absolute.as_uri() + "?mode=ro", uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _row(r) -> ExecutorStat:
    # SQLite Row supports both index and key access; tolerate missing columns
    # if an older DB is opened (forward-compat for the `source` field).
    try:
        src = r["source"]
    except (IndexError, KeyError):
        src = None
    return ExecutorStat(
        name=r["name"],
        last_used_at=r["last_used_at"],
        total_calls=int(r["total_calls"] or 0),
        last_call_ok=(None if r["last_call_ok"] is None else bool(r["last_call_ok"])),
        deprecated_at=r["deprecated_at"],
        archived_at=r["archived_at"],
        first_seen=r["first_seen"],
        source=src,
    )


def _log_event(conn, name: str, event_kind: str,
                source: str | None = None, detail: dict | None = None) -> None:
    import json as _json
    detail_s = _json.dumps(detail, ensure_ascii=False) if detail else None
    conn.execute(
        "INSERT INTO executor_history (name, event_kind, source, detail) "
        "VALUES (?, ?, ?, ?)",
        (name, event_kind, source, detail_s),
    )


def register(name: str, source: str = "handcrafted",
              detail: dict | None = None) -> None:
    """Registra la *creazione* di un executor. Chiamata da:
    - bootstrap loader (per handcrafted seed: source='handcrafted')
    - Synt.react/specialize/generalize (per synth: source='synth:<kind>')
    Idempotente: se name esiste, aggiorna source se ancora NULL. Inoltre
    SELF-HEAL: una skill importata mis-taggata 'synth:*' (perche' vive sotto
    SYNTHESIZED_EXECUTORS_DIR) viene corretta a 'skill' — autoritativo ed
    esente da aging (§reference aging-inactivity-trap; ADR 0170).
    """
    if not name:
        return
    conn = _open()
    try:
        existing = conn.execute(
            "SELECT name, source FROM executor_stats WHERE name=?", (name,)
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO executor_stats (name, source) VALUES (?, ?)",
                (name, source),
            )
            _log_event(conn, name, "created", source, detail)
        elif source and (
            existing["source"] is None
            or (source == "skill"
                and str(existing["source"]).startswith("synth"))
        ):
            conn.execute(
                "UPDATE executor_stats SET source=? WHERE name=?",
                (source, name),
            )
            if source == "skill" and existing["source"]:
                _log_event(conn, name, "source_corrected", source,
                           {"from": existing["source"]})
        conn.commit()
    finally:
        conn.close()


def touch(name: str, ok: bool | None = None) -> None:
    """Registra una chiamata all'executor `name`. Idempotente, best-effort.

    Se l'executor era deprecated o archived, NON viene resuscitato
    automaticamente — la chiamata viene comunque tracciata, ma il
    lifecycle override non si tocca. Lo riapre una nuova revisione ammessa
    da Birth (`note_generation`) oppure un'azione esplicita (`undeprecate`).

    Storia: emette evento `first_used` solo la prima volta che l'executor
    viene chiamato (per il timeline). Le successive chiamate aggiornano
    solo `total_calls` e `last_used_at`.
    """
    if not name:
        return
    conn = _open()
    try:
        # Detect first call by reading existing total_calls
        existing = conn.execute(
            "SELECT total_calls FROM executor_stats WHERE name=?", (name,)
        ).fetchone()
        is_first = existing is None or (existing["total_calls"] or 0) == 0

        conn.execute(
            """
            INSERT INTO executor_stats (name, last_used_at, total_calls, last_call_ok)
            VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ','now'), 1,
                    CASE WHEN ? IS NULL THEN NULL
                         WHEN ?=1 THEN 1 ELSE 0 END)
            ON CONFLICT(name) DO UPDATE SET
              last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
              total_calls  = total_calls + 1,
              last_call_ok = CASE WHEN ? IS NULL THEN last_call_ok
                                  WHEN ?=1 THEN 1 ELSE 0 END
            """,
            (name, ok, 1 if ok else 0, ok, 1 if ok else 0),
        )
        if is_first:
            _log_event(conn, name, "first_used", None, None)
        conn.commit()
    finally:
        conn.close()


def record_invocation(name: str, ok: bool | None) -> None:
    """Un'invocazione REALE dal runtime, dal choke-point centrale (ADR 0196).

    Perche' non basta `touch()` (6/8/2026). Il gancio per-invocazione fu
    cancellato il 4/7 con `af6c7b87` insieme al planner legacy e mai ricablato
    in engine v3: per un mese `last_used_at` e' rimasto vuoto su 124 executor
    su 193, mentre `apply_executor_ager` decideva le deprecazioni e
    `change_observer` i rollback su numeri fermi. Un gancio che vive nel
    runtime deve portarsi dietro due proprieta' che `touch()` non ha, ed e'
    per questo che sta qui e non nel chiamante:

      - NON registra sotto pytest. La suite invoca executor veri migliaia di
        volte con casi costruiti a mano: sommarli al traffico reale falserebbe
        l'unica misura su cui un executor viene deprecato. Stesso motivo, e
        stessa forma, di `engine/guard_stats`.
      - non solleva mai. Un contatore non deve far fallire l'invocazione che
        sta contando.

    Che cosa conta come chiamata: un'invocazione AMMESSA dal choke-point,
    qualunque sia l'esito — compreso un rifiuto del runtime prima di eseguire
    (placement, permesso negato, contesto mancante). L'instradamento a
    quell'unita' e' avvenuto, ed e' esattamente cio' che l'aging deve vedere:
    un executor a cui il planner arriva non e' inattivo, e deprecarlo
    nasconderebbe una capacita' viva. `ok=None` = esito ignoto: `last_call_ok`
    resta com'era, la chiamata conta lo stesso.
    """
    if not name or "PYTEST_CURRENT_TEST" in os.environ:
        return
    try:
        touch(name, ok)
    except Exception as ex:  # noqa: BLE001
        log.warning("executor_aging: uso di %s non registrato: %r", name, ex)


def lookup(name: str) -> ExecutorStat | None:
    conn = _open()
    try:
        r = conn.execute(
            "SELECT * FROM executor_stats WHERE name = ?", (name,)
        ).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def all_stats() -> list[ExecutorStat]:
    conn = _open()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM executor_stats ORDER BY name"
        )]
    finally:
        conn.close()


def lifecycle_override_map(*, read_only: bool = False) -> dict[str, str]:
    """Ritorna {name → lifecycle override} per l'integrazione con loader.py.
    Solo entries che hanno un override attivo (deprecated_at o archived_at)."""
    if type(read_only) is not bool:
        raise ValueError("read_only must be bool")
    conn = _open_read_only() if read_only else _open()
    if conn is None:
        return {}
    try:
        out = {}
        for r in conn.execute(
            "SELECT name, deprecated_at, archived_at FROM executor_stats "
            "WHERE deprecated_at IS NOT NULL OR archived_at IS NOT NULL"
        ):
            if r["archived_at"]:
                out[r["name"]] = "archived"
            elif r["deprecated_at"]:
                out[r["name"]] = "deprecated"
        return out
    finally:
        conn.close()


def _days_between_iso(later_iso: str, earlier_iso: str) -> float:
    """Differenza in giorni fra due ISO timestamps (positiva se later > earlier)."""
    try:
        l = datetime.fromisoformat(later_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(earlier_iso.replace("Z", "+00:00"))
        return (l - e).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


def apply_executor_ager(
    *,
    deprecate_days: int | None = None,
    archive_days: int | None = None,
    now_iso: str | None = None,
    catalog_names: list[str] | None = None,
) -> dict:
    """Job notturno: deprecate/archive executor inattivi.

    Args:
      deprecate_days: soglia inattivita' per deprecate (default DEPRECATED_DAYS).
      archive_days:   soglia di permanenza in deprecated prima di archive
                      (default ARCHIVED_DAYS).
      now_iso:        timestamp di riferimento (default: now). Util per test.
      catalog_names:  se passato, applica solo a questi nomi (ignora il resto).

    Returns:
      summary {deprecated:[name,...], archived:[name,...], protected_skipped:int,
               handcrafted_skipped:int, already_deprecated:int,
               already_archived:int, total_seen:int}.

    NB (13/6/2026): solo gli executor SYNTH invecchiano per inattivita'. Gli
    handcrafted (source non-synth) sono esclusi dal decay (vedi docstring
    modulo): sono capacita' core curate a
    mano, un raro uso non e' obsolescenza, e ritirarli causa misroute silenzioso.
    """
    deprecate_days = deprecate_days if deprecate_days is not None else DEPRECATED_DAYS
    archive_days   = archive_days   if archive_days   is not None else ARCHIVED_DAYS
    now_iso = now_iso or now_iso_z()

    deprecated_now: list[str] = []
    archived_now: list[str] = []
    protected_skipped = 0
    handcrafted_skipped = 0
    already_dep = 0
    already_arc = 0
    total_seen = 0

    conn = _open()
    try:
        rows = conn.execute(
            "SELECT * FROM executor_stats"
        ).fetchall()
        for r in rows:
            row = _row(r)
            if catalog_names is not None and row.name not in catalog_names:
                continue
            total_seen += 1

            # Salta i protetti: PROTECTED_NAMES + verb-unique (mai in stats DB
            # comunque, ma per safety).
            if row.name in PROTECTED_NAMES:
                protected_skipped += 1
                continue

            # Mai deprecare per INATTIVITA' un executor handcrafted (bug
            # 13/6/2026): l'aging serve a culling della proliferazione SYNTH
            # (§3), non a ritirare capacita' core curate a mano. Un raro uso
            # (es. `delete_persons`: si cancella un enrollment ogni mesi) NON
            # e' obsolescenza. Deprecato per inattivita', l'handcrafted sparisce
            # dal catalog composer (filter_for_visibility) → il pool di routing
            # perde l'unico provider della capacita' → misroute silenzioso a un
            # fratello sbagliato (delete_persons→delete_credentials, falso
            # successo §2.8). Coerente col docstring del modulo ("Esclusi dal
            # decay: tutti gli handcrafted"). Solo synth invecchiano.
            if not _is_synth(row.name, row.source):
                handcrafted_skipped += 1
                continue

            # Caso 1: gia' archived → niente
            if row.archived_at:
                already_arc += 1
                continue

            # Caso 2: gia' deprecated → check se passare ad archived
            if row.deprecated_at:
                already_dep += 1
                days_dep = _days_between_iso(now_iso, row.deprecated_at)
                if days_dep >= archive_days:
                    conn.execute(
                        "UPDATE executor_stats SET archived_at = ? "
                        "WHERE name = ?",
                        (now_iso, row.name),
                    )
                    _log_event(
                        conn, row.name, "archived", row.source,
                        {"after_deprecated_days": int(days_dep)},
                    )
                    archived_now.append(row.name)
                continue

            # Caso 3: active → check se passare a deprecated
            # Se non e' mai stato chiamato, riferimento e' first_seen
            anchor = row.last_used_at or row.first_seen
            days_inactive = _days_between_iso(now_iso, anchor)
            if days_inactive >= deprecate_days:
                conn.execute(
                    "UPDATE executor_stats SET deprecated_at = ? "
                    "WHERE name = ?",
                    (now_iso, row.name),
                )
                _log_event(
                    conn, row.name, "deprecated", row.source,
                    {"days_inactive": int(days_inactive)},
                )
                deprecated_now.append(row.name)

        conn.commit()
        return {
            "deprecated": deprecated_now,
            "archived": archived_now,
            "protected_skipped": protected_skipped,
            "handcrafted_skipped": handcrafted_skipped,
            "already_deprecated": already_dep,
            "already_archived": already_arc,
            "total_seen": total_seen,
            "thresholds": {
                "deprecate_days": deprecate_days,
                "archive_days": archive_days,
            },
        }
    finally:
        conn.close()


def undeprecate(name: str, *, by: str = "") -> bool:
    """Bring back a deprecated or archived executor (a person's decision).

    The executor must still be published; nothing is rewritten. Its
    inactivity clock restarts, as for a new revision: otherwise the next
    nightly pass would hide it again for the same old silence. Returns True
    when a restriction was lifted.
    """
    # Reappearance is a visibility change, not contract publication.  Prove
    # the complete current policy first; the check includes archived contracts
    # because aging overrides are applied only after authenticated admission.
    from skill_registry import validate_current_skill_policy

    validate_current_skill_policy()
    conn = _open()
    try:
        cur = conn.execute(
            "UPDATE executor_stats SET deprecated_at = NULL, archived_at = NULL, "
            "last_used_at = NULL, last_call_ok = NULL, first_seen = ? "
            "WHERE name = ? AND (deprecated_at IS NOT NULL OR archived_at IS NOT NULL)",
            (now_iso_z(), name),
        )
        if cur.rowcount > 0:
            _log_event(conn, name, "undeprecated", None, {"by": by} if by else None)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── History introspection (per CLI/dashboard) ────────────────────────

def history(*, limit: int = 200, name: str | None = None,
             since_iso: str | None = None) -> list[dict]:
    """Ritorna eventi recenti di executor_history, newest first."""
    conn = _open()
    try:
        sql = "SELECT * FROM executor_history WHERE 1=1"
        params: list = []
        if name:
            sql += " AND name = ?"
            params.append(name)
        if since_iso:
            sql += " AND ts >= ?"
            params.append(since_iso)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def counts_by_source_lifecycle() -> dict:
    """Conteggi correnti: {source: {active, deprecated, archived}}.
    Per dashboard admin (snapshot istantaneo)."""
    conn = _open()
    try:
        out: dict[str, dict[str, int]] = {}
        for r in conn.execute(
            "SELECT source, "
            " SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) AS arc, "
            " SUM(CASE WHEN archived_at IS NULL AND deprecated_at IS NOT NULL THEN 1 ELSE 0 END) AS dep, "
            " SUM(CASE WHEN archived_at IS NULL AND deprecated_at IS NULL THEN 1 ELSE 0 END) AS act "
            "FROM executor_stats GROUP BY source"
        ):
            src = r["source"] or "unknown"
            out[src] = {"active": int(r["act"] or 0),
                        "deprecated": int(r["dep"] or 0),
                        "archived": int(r["arc"] or 0)}
        return out
    finally:
        conn.close()


def _is_synth(name: str, source: str | None) -> bool:
    """True se l'executor e' synth (NON handcrafted).

    Verità AUTOREVOLE: un handcrafted ESISTE come dir sotto config.PATH_EXECUTORS
    (repo, curato a mano). Il `source` del DB stats può essere MAL-REGISTRATO
    ('synth:reactive' su un handcrafted core, bug 21/6: delete_files/
    find_events_empty/delete_events/read_contacts/set_messages) → l'ager lo
    deprecherebbe per inattività, sparirebbe dal catalog composer → misroute al
    fratello (delete_files→delete_entries, find_events_empty→read_events). La
    presenza on-disk nel repo VINCE sul source: un core non invecchia mai. §7.9.
    """
    try:
        import config as _C
        if (_C.PATH_EXECUTORS / name).is_dir():
            return False
    except Exception:  # noqa: BLE001 — best-effort, ricade sul source
        pass
    if source is None:
        return False
    return source.startswith("synth")


def daily_event_counts(*, days: int = 30) -> list[dict]:
    """Per i grafici «andamento meccanismi di distruzione/disabilitazione».
    Ritorna [{date, source, event_kind, n}] aggregato per giorno × source × kind.
    """
    conn = _open()
    try:
        rows = conn.execute(
            "SELECT DATE(ts) AS d, source, event_kind, COUNT(*) AS n "
            "FROM executor_history "
            "WHERE ts >= datetime('now', ?) "
            "GROUP BY d, source, event_kind ORDER BY d ASC",
            (f"-{int(days)} days",),
        ).fetchall()
        return [{"date": r["d"], "source": r["source"] or "unknown",
                 "event_kind": r["event_kind"], "n": int(r["n"])}
                for r in rows]
    finally:
        conn.close()
