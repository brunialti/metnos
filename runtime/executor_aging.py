"""executor_aging.py — decay degli executor inattivi (3/5/2026).

Simmetrico a `apply_ager` per i mnest (mnestoma). Ogni invocazione di un
executor dal runtime fa `touch(name, ok)`; il task notturno `apply_executor_ager`
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
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
from timefmt import now_iso_z

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
    lifecycle override non si tocca. Resuscitare richiede un'azione
    esplicita (gestita da Synt promotion / undeprecate).

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


def lifecycle_override_map() -> dict[str, str]:
    """Ritorna {name → lifecycle override} per l'integrazione con loader.py.
    Solo entries che hanno un override attivo (deprecated_at o archived_at)."""
    conn = _open()
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
    modulo + simmetria con apply_feedback_ager): sono capacita' core curate a
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
            # successo §2.8). Simmetrico con `apply_feedback_ager` (handcrafted
            # MAI demoted da efficacy) e col docstring del modulo ("Esclusi dal
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


def undeprecate(name: str) -> bool:
    """Resuscita un executor deprecated/archived (azione manuale, rara).
    Non riapre il file su disco: l'executor deve essere ancora presente
    sul filesystem. Ritorna True se uno stato e' stato cambiato.
    """
    conn = _open()
    try:
        cur = conn.execute(
            "UPDATE executor_stats SET deprecated_at = NULL, archived_at = NULL "
            "WHERE name = ?",
            (name,),
        )
        if cur.rowcount > 0:
            _log_event(conn, name, "undeprecated", None, None)
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


# ── Layer 3 admission policy: efficacy ager (ADR 0114) ──────────────
#
# A differenza di `apply_executor_ager` (sopra) — che decay per inattivita'
# temporale — `apply_efficacy_ager` decay per *inefficacia*: synth con
# success_rate basso vengono demoted a deprecated (>=100 invocations) o
# archived (success_rate < 0.05 dopo demotion + altri 30 inv).
#
# Stats source: turn JSONL log in `~/.local/share/metnos/turns/*.jsonl`.
# Filtra per chosen_tool e calcola: success = (result.ok == True OR
# entries non vuote) AND error_class non in fail set.

EFFICACY_DEPRECATE_THRESHOLD = float(os.environ.get(
    "METNOS_EFFICACY_DEPRECATE_THRESHOLD", "0.20"))
EFFICACY_ARCHIVE_THRESHOLD   = float(os.environ.get(
    "METNOS_EFFICACY_ARCHIVE_THRESHOLD", "0.05"))
EFFICACY_MIN_INVOCATIONS     = int(os.environ.get(
    "METNOS_EFFICACY_MIN_INVOCATIONS", "100"))
EFFICACY_RE_EVAL_INVOCATIONS = int(os.environ.get(
    "METNOS_EFFICACY_RE_EVAL_INVOCATIONS", "30"))

EFFICACY_AUDIT_DIR = _C.PATH_USER_DATA / "synth_audit"


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


def _step_was_successful(step: dict) -> bool:
    """Definizione di "step successful":
       result.ok == True AND nessun error_class fatale.
       In assenza di entries esplicite, ok=True basta.
       Se result.entries presente → richiede entries non vuote.
    """
    err = step.get("error")
    if err and err != "":
        return False
    res = step.get("result") or {}
    if not isinstance(res, dict):
        return False
    if res.get("ok") is False:
        return False
    if res.get("error_class"):
        return False
    # Se l'executor produce entries, esigi qualcosa.
    if "entries" in res and not res.get("entries"):
        return False
    if "results" in res and not res.get("results"):
        # results vuoto → no work done (es. move 0 files): tratta come fail.
        return False
    return True


def collect_invocation_stats(turns_dir: Path | None = None,
                              tool_name: str | None = None) -> dict:
    """Itera tutti i turn JSONL, raccoglie per-tool (nome → {ok, total}).

    Args:
        turns_dir: dir dei turn log (default ~/.local/share/metnos/turns/).
        tool_name: se passato, filtra solo le invocations di quel tool.

    Returns:
        dict {tool_name: {"total": int, "ok": int, "success_rate": float}}.
    """
    import json as _json
    if turns_dir is None:
        turns_dir = _C.PATH_USER_DATA / "turns"
    out: dict[str, dict] = {}
    if not turns_dir.exists():
        return out
    for jsonl_path in sorted(turns_dir.glob("*.jsonl")):
        try:
            with jsonl_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        turn = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    for step in turn.get("steps") or []:
                        ct = step.get("chosen_tool")
                        if not ct:
                            continue
                        if tool_name is not None and ct != tool_name:
                            continue
                        bucket = out.setdefault(ct, {"total": 0, "ok": 0})
                        bucket["total"] += 1
                        if _step_was_successful(step):
                            bucket["ok"] += 1
        except OSError:
            continue
    for ct, b in out.items():
        b["success_rate"] = (b["ok"] / b["total"]) if b["total"] > 0 else 0.0
    return out


def _efficacy_audit_path() -> Path:
    EFFICACY_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return EFFICACY_AUDIT_DIR / "efficacy_demotions.jsonl"


def apply_efficacy_ager(
    *,
    deprecate_threshold: float | None = None,
    archive_threshold: float | None = None,
    min_invocations: int | None = None,
    re_eval_invocations: int | None = None,
    turns_dir: Path | None = None,
    now_iso: str | None = None,
) -> dict:
    """Demote synth con success_rate basso (ADR 0114, 8/5/2026).

    Per ogni synth con invocations >= `min_invocations` (default 100):
      - success_rate < `deprecate_threshold` (default 0.20) → deprecated.
      - success_rate < `archive_threshold` (default 0.05) DOPO demotion
        e con altre `re_eval_invocations` (default 30) post-demotion →
        archived.

    Idempotente: re-run tocca solo synth con stati nuovi/modificati.
    Handcrafted (source='handcrafted' o NULL) MAI demoted da efficacy ager.

    Returns:
      summary dict con liste deprecated/archived + audit info.
    """
    import json as _json
    if deprecate_threshold is None:
        deprecate_threshold = EFFICACY_DEPRECATE_THRESHOLD
    if archive_threshold is None:
        archive_threshold = EFFICACY_ARCHIVE_THRESHOLD
    if min_invocations is None:
        min_invocations = EFFICACY_MIN_INVOCATIONS
    if re_eval_invocations is None:
        re_eval_invocations = EFFICACY_RE_EVAL_INVOCATIONS
    now_iso = now_iso or now_iso_z()

    stats = collect_invocation_stats(turns_dir)
    deprecated_now: list[dict] = []
    archived_now: list[dict] = []
    skipped_handcrafted = 0
    skipped_below_threshold = 0
    skipped_protected = 0

    audit_lines: list[dict] = []
    conn = _open()
    try:
        for tool_name, st in stats.items():
            if st["total"] < min_invocations:
                skipped_below_threshold += 1
                continue
            row = conn.execute(
                "SELECT * FROM executor_stats WHERE name = ?", (tool_name,)
            ).fetchone()
            if row is None:
                continue
            row = _row(row)
            if row.name in PROTECTED_NAMES:
                skipped_protected += 1
                continue
            if not _is_synth(row.name, row.source):
                skipped_handcrafted += 1
                continue
            sr = st["success_rate"]
            # Already archived → nothing to do.
            if row.archived_at:
                continue
            # Already deprecated → check archive threshold.
            if row.deprecated_at:
                # Re-eval requires extra invocations dopo deprecation.
                # Stima conservativa: confronta total invocations corrente
                # con un soglia inv >= min_invocations + re_eval_invocations.
                if st["total"] < min_invocations + re_eval_invocations:
                    continue
                if sr < archive_threshold:
                    conn.execute(
                        "UPDATE executor_stats SET archived_at = ? "
                        "WHERE name = ?",
                        (now_iso, row.name),
                    )
                    detail = {"success_rate": round(sr, 3),
                              "total_invocations": st["total"],
                              "ok_invocations": st["ok"]}
                    _log_event(conn, row.name, "archived", row.source,
                               {**detail, "by": "efficacy_ager"})
                    archived_now.append({"name": row.name, **detail})
                    audit_lines.append({
                        "ts": now_iso, "event": "archived",
                        "name": row.name, **detail,
                    })
                continue
            # Active → check deprecate threshold.
            if sr < deprecate_threshold:
                conn.execute(
                    "UPDATE executor_stats SET deprecated_at = ? "
                    "WHERE name = ?",
                    (now_iso, row.name),
                )
                detail = {"success_rate": round(sr, 3),
                          "total_invocations": st["total"],
                          "ok_invocations": st["ok"]}
                _log_event(conn, row.name, "deprecated", row.source,
                           {**detail, "by": "efficacy_ager"})
                deprecated_now.append({"name": row.name, **detail})
                audit_lines.append({
                    "ts": now_iso, "event": "deprecated",
                    "name": row.name, **detail,
                })
        conn.commit()
    finally:
        conn.close()

    # Audit log JSONL append. Fail-safe: se OS error, prosegui.
    if audit_lines:
        try:
            audit_path = _efficacy_audit_path()
            with audit_path.open("a", encoding="utf-8") as fh:
                for line in audit_lines:
                    fh.write(_json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    return {
        "deprecated": deprecated_now,
        "archived": archived_now,
        "skipped_handcrafted": skipped_handcrafted,
        "skipped_below_threshold": skipped_below_threshold,
        "skipped_protected": skipped_protected,
        "thresholds": {
            "deprecate": deprecate_threshold,
            "archive": archive_threshold,
            "min_invocations": min_invocations,
            "re_eval_invocations": re_eval_invocations,
        },
    }


# ── Layer 3 admission policy: feedback ager (E12, ADR 0114 reinforcement) ───
#
# A differenza di `apply_efficacy_ager` (success_rate dai turn JSONL su
# bulk di ≥100 invocations), `apply_feedback_ager` reagisce al signal
# esplicito ✗ dell'utente: dopo N feedback negative consecutive su uno
# stesso tool synth (cross-query, LWW), demota l'executor a `deprecated`.
#
# Vincoli ADR 0114 L3:
#   - Handcrafted MAI demoted (source NOT starts with 'synth').
#   - PROTECTED_NAMES MAI demoted.
#   - Idempotente: se gia' deprecated/archived, no-op.
#
# Audit: stesso JSONL di efficacy_ager (`synth_audit/efficacy_demotions.jsonl`)
# con campo `by: "feedback_ager"`.

def apply_feedback_ager(
    tool_name: str,
    *,
    consecutive_errors: int,
    now_iso: str | None = None,
) -> dict:
    """Demote `tool_name` a deprecated se synth e non protetto.

    Args:
      tool_name:           nome executor candidato a demotion.
      consecutive_errors:  conteggio ✗ consecutive che ha triggerato
                           la chiamata (per audit/history detail).
      now_iso:             timestamp di riferimento (default: now).

    Returns:
      dict {action, name, reason?} dove action ∈
        {"demoted", "skip_protected", "skip_handcrafted",
         "skip_already_deprecated", "skip_unknown"}.
    """
    import json as _json
    if not tool_name:
        return {"action": "skip_unknown", "name": tool_name,
                "reason": "empty_name"}
    now_iso = now_iso or now_iso_z()

    if tool_name in PROTECTED_NAMES:
        return {"action": "skip_protected", "name": tool_name}

    conn = _open()
    try:
        row = conn.execute(
            "SELECT * FROM executor_stats WHERE name = ?", (tool_name,)
        ).fetchone()
        if row is None:
            return {"action": "skip_unknown", "name": tool_name,
                    "reason": "not_in_stats"}
        row = _row(row)
        if not _is_synth(row.name, row.source):
            return {"action": "skip_handcrafted", "name": tool_name,
                    "source": row.source}
        if row.archived_at or row.deprecated_at:
            return {"action": "skip_already_deprecated", "name": tool_name,
                    "deprecated_at": row.deprecated_at,
                    "archived_at": row.archived_at}
        conn.execute(
            "UPDATE executor_stats SET deprecated_at = ? WHERE name = ?",
            (now_iso, tool_name),
        )
        detail = {"by": "feedback_ager",
                  "consecutive_errors": int(consecutive_errors)}
        _log_event(conn, tool_name, "deprecated", row.source, detail)
        conn.commit()
    finally:
        conn.close()

    # Audit JSONL append. Fail-safe.
    try:
        audit_path = _efficacy_audit_path()
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps({
                "ts": now_iso, "event": "deprecated",
                "name": tool_name, "by": "feedback_ager",
                "consecutive_errors": int(consecutive_errors),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass

    return {"action": "demoted", "name": tool_name,
            "deprecated_at": now_iso,
            "consecutive_errors": int(consecutive_errors)}


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
