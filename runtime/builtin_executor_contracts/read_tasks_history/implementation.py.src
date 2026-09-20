#!/usr/bin/env python3
"""recurring_tasks — bridge user-defined recurring tasks to runtime scheduler.

Pattern: l'utente chiede "ogni giorno alle 8 verifica la posta e dimmi se ci
sono mail importanti". Il PLANNER chiama `create_tasks(when="daily@08:00",
query="leggi le mail di oggi importanti", label="check posta mattutina")`. Il
modulo persiste in sqlite e registra una closure nello scheduler builtin.

Al fire, la closure rilancia `run_turn(query, actor=task.actor, channel=...)`
e pusha la `final_message` sul canale dell'actor che ha richiesto.

Schema sqlite (`~/.local/state/metnos/recurring_tasks.db`):
  id INTEGER PK,
  name TEXT UNIQUE,         -- generato auto da label slugified
  schedule TEXT,            -- daily@HH:MM | every_Nm
  query TEXT,               -- query da rilanciare a run_turn
  actor TEXT,               -- 'host' | 'guest_xxxxxx'
  channel TEXT,             -- 'telegram' | ...
  chat_id TEXT,             -- destinazione push (per telegram)
  label TEXT,               -- descrizione utente-leggibile
  mandates TEXT,            -- inviluppi di autorita' per dominio, senza segreti
  created_at TEXT,
  enabled INTEGER DEFAULT 1
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

from logging_setup import get_logger
log = get_logger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
import detection_lexicon_seed_parsers as _parser_lex
DB_PATH = _C.DB_RECURRING_TASKS

_TASK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS recurring_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    scheduler_name TEXT NOT NULL UNIQUE,
    schedule      TEXT NOT NULL,
    query         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    channel       TEXT NOT NULL,
    chat_id       TEXT,
    label         TEXT,
    callback_key  TEXT NOT NULL DEFAULT 'run_user_query',
    times                INTEGER,            -- NULL/0 = forever; N = max fire
    fired_count          INTEGER NOT NULL DEFAULT 0,
    grace_window_minutes INTEGER,            -- recover-missed window oltre il
                                                -- quale il fire viene saltato.
                                                -- NULL = recover illimitato.
                                                -- Es. 240 = recover entro 4h
                                                -- dal target_time, oltre skip.
    mandates      TEXT NOT NULL DEFAULT '{}', -- authority envelope per task;
                                                -- mai token o credenziali.
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    enabled       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(owner_user_id, name)
);
"""

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_recurring_actor "
    "ON recurring_tasks(actor, channel)",
    "CREATE INDEX IF NOT EXISTS idx_recurring_owner "
    "ON recurring_tasks(owner_user_id, name)",
)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create schema without ``executescript``'s implicit transaction commit."""

    connection.execute(_TASK_TABLE_SQL)
    for statement in _INDEX_STATEMENTS:
        connection.execute(statement)

# Callback registry pattern (lezione F1 giorgio2): la closure NON viene
# salvata in DB, solo `callback_key` string. Al boot ogni callback si
# registra qui; `_make_task_fn(record)` dispatcha via `record.callback_key`.
# Sopravvive a refactor della closure (DB resta valido).
_CALLBACKS: dict[str, callable] = {}


def register_callback(key: str, fn) -> None:
    """Registra una callback per chiave. Idempotente."""
    _CALLBACKS[key] = fn


def dispatch_callback(key: str, record: dict):
    """Risolvi la callback dal registry e invoca con record.

    Async-ready: rileva coroutine function via inspect; oggi le esegue in
    sync via `asyncio.run` (bridge), in async-future basta `await fn(record)`.
    """
    fn = _CALLBACKS.get(key)
    if fn is None:
        raise KeyError(
            f"callback_key '{key}' non registrata. Registered: {list(_CALLBACKS)}"
        )
    import inspect
    if inspect.iscoroutinefunction(fn):
        # Sync→async bridge per oggi. Una callback async puo' coesistere
        # nel registry con callback sync; il caller (run_task) resta sync.
        import asyncio
        return asyncio.run(fn(record))
    return fn(record)

_SCHEDULE_RE = re.compile(r"^(daily@\d{1,2}:\d{2}|every_\d+m)$")

# Limite per-actor: protezione runaway.
MAX_TASKS_PER_ACTOR = 50


def _scheduler_entry_name(owner_user_id: str, name: str) -> str:
    """Globally unique scheduler key for an owner-scoped logical name."""

    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("recurring task owner_user_id is required")
    digest = hashlib.sha256(
        ("metnos-recurring-owner-v1\0" + owner).encode("utf-8")
    ).hexdigest()[:16]
    return f"user_{digest}_{name}"


def _validated_existing_owner(row: dict) -> str:
    """Validate an owner UUID already present in a partially migrated row.

    Actor names and channel recipients are deliberately *not* ownership
    evidence: both can be reused after deletion.  A truly legacy row without
    an immutable UUID is retired fail-closed by :func:`_open`.
    """

    owner = str(row.get("owner_user_id") or "").strip()
    if not owner:
        return ""
    try:
        import users
        candidate = users.get_user(owner)
    except Exception as exc:
        # Identity-store outage is not evidence that an owner disappeared.
        # Abort the migration so its SQLite transaction can be retried.
        raise RuntimeError("owner identity validation unavailable") from exc
    if candidate is None or str(candidate.get("id") or "") != owner:
        return ""
    return owner


_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY_PATH: Path | None = None


def _open_and_migrate() -> sqlite3.Connection:
    """Bootstrap/migrate once per database path, never on owner hot paths."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    table_exists = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='recurring_tasks'"
    ).fetchone() is not None
    rejected_scheduler_names: list[str] = []
    migrated_scheduler_rows: list[tuple[str, dict]] = []
    if table_exists:
        cols = {
            row[1] for row in c.execute(
                "PRAGMA table_info(recurring_tasks)").fetchall()
        }
        unique_columns = {
            tuple(
                info[2] for info in c.execute(
                    f"PRAGMA index_info('{index_row[1]}')").fetchall()
            )
            for index_row in c.execute(
                "PRAGMA index_list(recurring_tasks)").fetchall()
            if index_row[2]
        }
        needs_owner_migration = (
            "owner_user_id" not in cols
            or "scheduler_name" not in cols
            or ("owner_user_id", "name") not in unique_columns
        )
        if needs_owner_migration:
            rows = [dict(row) for row in c.execute(
                "SELECT * FROM recurring_tasks").fetchall()]
            c.execute("ALTER TABLE recurring_tasks "
                      "RENAME TO recurring_tasks_pre_owner")
            _ensure_schema(c)
            for row in rows:
                owner = _validated_existing_owner(row)
                old_scheduler = str(
                    row.get("scheduler_name") or f"user_{row.get('name') or ''}")
                if not owner:
                    rejected_scheduler_names.append(old_scheduler)
                    continue
                logical_name = str(row.get("name") or "")
                scheduler_name = _scheduler_entry_name(owner, logical_name)
                c.execute(
                    "INSERT INTO recurring_tasks "
                    "(id,name,owner_user_id,scheduler_name,schedule,query,"
                    "actor,channel,chat_id,label,callback_key,times,"
                    "fired_count,grace_window_minutes,mandates,created_at,"
                    "enabled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row.get("id"), logical_name, owner, scheduler_name,
                        row.get("schedule") or "", row.get("query") or "",
                        row.get("actor") or "", row.get("channel") or "",
                        row.get("chat_id"), row.get("label"),
                        row.get("callback_key") or "run_user_query",
                        row.get("times"), int(row.get("fired_count") or 0),
                        row.get("grace_window_minutes"),
                        row.get("mandates") or "{}",
                        row.get("created_at") or time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        int(row.get("enabled", 1)),
                    ),
                )
                migrated_scheduler_rows.append((old_scheduler, {
                    **row,
                    "name": logical_name,
                    "owner_user_id": owner,
                    "scheduler_name": scheduler_name,
                }))
            c.execute("DROP TABLE recurring_tasks_pre_owner")
    _ensure_schema(c)
    try:
        import task_mandates
        import users as _users
        for row in c.execute(
                "SELECT owner_user_id,name,query,actor,mandates "
                "FROM recurring_tasks").fetchall():
            if task_mandates.needs_version_upgrade(
                    row["query"], row["mandates"]):
                live_owner = _users.get_user(row["owner_user_id"])
                if (live_owner is None
                        or str(live_owner.get("id") or "")
                        != str(row["owner_user_id"])
                        or str(live_owner.get("name") or "")
                        != str(row["actor"] or "")):
                    # A mutable/reused actor label cannot be used to rebuild
                    # unattended authority.  Keep the task but retire the
                    # unverifiable envelope; execution will require a fresh
                    # explicit authorization path.
                    envelope = {}
                else:
                    envelope = task_mandates.build_for_task(
                        row["query"], row["actor"],
                        owner_user_id=row["owner_user_id"])
                c.execute(
                    "UPDATE recurring_tasks SET mandates=? "
                    "WHERE owner_user_id=? AND name=?",
                    (json.dumps(envelope, ensure_ascii=True, sort_keys=True,
                                separators=(",", ":")),
                     row["owner_user_id"], row["name"]))
    except Exception as exc:
        log.warning("task mandate migration failed closed: %s", exc)
    # Cross-database migration cannot be one SQLite transaction.  Perform all
    # idempotent scheduler operations *before* committing the new registry:
    # after a crash, the still-legacy registry makes the same operations run
    # again.  Rejected ownerless rows are purged together with their run
    # history; merely cancelling their entry would retain personal data.
    try:
        from scheduler_v2 import client as sched_client
        for old_scheduler_name, row in migrated_scheduler_rows:
            payload = {
            "name": row["name"],
            "owner_user_id": row["owner_user_id"],
            "scheduler_name": row["scheduler_name"],
            "query": row.get("query") or "",
            "actor": row.get("actor") or "",
            "channel": row.get("channel") or "",
            "chat_id": row.get("chat_id"),
            "label": row.get("label"),
            "times": row.get("times"),
            }
            renamed = sched_client.migrate_owner_job(
                old_scheduler_name, row["scheduler_name"], payload)
            if not renamed:
                # This also covers a retry after a crash that already renamed
                # the scheduler row: migrate(new,new) refreshes its payload.
                renamed = sched_client.migrate_owner_job(
                    row["scheduler_name"], row["scheduler_name"], payload)
            if not renamed:
                grace = row.get("grace_window_minutes")
                sched_client.add_job(
                    name=row["scheduler_name"],
                    trigger=row.get("schedule") or "",
                    callback_key=row.get("callback_key") or "run_user_query",
                    payload=payload,
                    origin="user",
                    grace_window_s=(int(grace) * 60 if grace else None),
                    label=row.get("label") or "",
                    description=("user task: "
                                 f"{row.get('label') or row['name']}"),
                    remaining_runs=max(
                        0, int(row.get("times") or 0)
                        - int(row.get("fired_count") or 0)),
                )
                if not int(row.get("enabled", 1)):
                    sched_client.toggle_job(row["scheduler_name"], False)
        if rejected_scheduler_names:
            sched_client.purge_jobs(tuple(rejected_scheduler_names))
    except Exception:
        c.rollback()
        c.close()
        raise
    c.commit()
    return c


def _ensure_database() -> None:
    global _SCHEMA_READY_PATH

    target = DB_PATH.resolve()
    if _SCHEMA_READY_PATH == target and DB_PATH.is_file():
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY_PATH == target and DB_PATH.is_file():
            return
        connection = _open_and_migrate()
        connection.close()
        _SCHEMA_READY_PATH = target


def _open() -> sqlite3.Connection:
    """Open the already initialized registry without global scans or DDL."""

    _ensure_database()
    connection = sqlite3.connect(str(DB_PATH))
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    """Create or migrate the task registry without retaining a connection."""
    _ensure_database()


def _slugify(label: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return s[:max_len] or f"task_{int(time.time())}"


def _parse_when(when: str) -> str:
    """Normalizza when user-provided (es. 'ogni giorno alle 8',
    '8:00', '8 del mattino') → schedule formato scheduler.
    Per ora accetta SOLO i due formati canonici esattii: daily@HH:MM
    o every_Nm. Estrazione NL e' responsabilita' del PLANNER.
    """
    if not isinstance(when, str):
        raise ValueError("when deve essere stringa")
    when = when.strip()
    if not _SCHEDULE_RE.match(when):
        raise ValueError(
            f"when='{when}' non valido. Formati supportati: 'daily@HH:MM' "
            f"(es. 'daily@08:00') oppure 'every_Nm' (es. 'every_30m')."
        )
    return when


# ── Parse deterministico di query NL con ricorrenza (§7.9) ────────────────
# «Every 30 min: <corpo>» / «ogni giorno alle 8 <corpo>» → {when, query,
# label}. La grammatica TARGET dello scheduler e' CHIUSA (daily@HH:MM |
# every_Nm): il mapping NL→schedule per le frasi di ricorrenza comuni e'
# deterministico e limitato. Parse ambiguo (es. «ogni giorno» senza orario,
# «ogni settimana» non rappresentabile, domanda interrogativa) → None: il
# chiamante fa fallthrough al flusso normale. Fix bug live 10/6/2026:
# «Every 30 min: read the new open issues…» finiva nel decomposer/engine
# che eseguivano il CORPO subito → «Pipeline malformata».

def _phrase_alt(forms) -> str:
    return "|".join(
        re.escape(str(form)).replace(r"\ ", r"\s+")
        for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
        if str(form).strip()
    )


def _surface_to_canonical(mapping: dict) -> dict[str, str]:
    return {
        str(surface).casefold(): str(canonical)
        for canonical, forms in mapping.items()
        for surface in forms
    }


def _strip_create_framing(body: str, lexicon: dict[str, object]) -> str:
    """Toglie l'inquadramento di creazione-task lasciando SOLO l'azione.
    Universale e deterministico: nessuna lista di verbi, ancora sul sostantivo
    canonico `tasks` + relativo. No-op se il corpo e' gia' un'azione."""
    articles = _phrase_alt(lexicon["parser.recurrence.article"])
    nouns = _phrase_alt(lexicon["parser.recurrence.task_noun"])
    relatives = _phrase_alt(lexicon["parser.recurrence.relative"])
    if not articles or not nouns or not relatives:
        return body.strip()
    word = r"\w+"
    framing = re.compile(
        rf"^\s*{word}\s+(?:{articles})(?!\w)\s+"
        rf"(?:{word}\s+){{0,2}}?(?:{nouns})(?!\w)"
        rf"(?:\s+{word}){{0,2}}?\s+(?:{relatives})(?!\w)\s+",
        re.IGNORECASE | re.DOTALL | re.UNICODE,
    )
    return framing.sub("", body, count=1).strip()


def _strip_clause(query: str, span: tuple[int, int],
                  lexicon: dict[str, object]) -> str:
    """Rimuove la clausola di schedule dalla query e pulisce i connettori
    residui ai bordi (':', ',', 'e', 'and', 'poi', 'then')."""
    body = (query[: span[0]] + " " + query[span[1]:]).strip()
    connectors = _phrase_alt(lexicon["parser.recurrence.edge_connector"])
    if connectors:
        body = re.sub(
            rf"^(?:[:;,\-]\s*|(?:{connectors})(?!\w)\s+)+", "", body,
            flags=re.IGNORECASE | re.UNICODE,
        )
        body = re.sub(
            rf"(?:\s+(?:{connectors})(?!\w)|[:;,\-])+\s*$", "", body,
            flags=re.IGNORECASE | re.UNICODE,
        )
    else:
        body = re.sub(r"^(?:[:;,\-]\s*)+|(?:[:;,\-])+\s*$", "", body)
    return re.sub(r"\s{2,}", " ", body).strip()


def parse_recurrence_query(query: str) -> dict | None:
    """Parse deterministico di una query utente con ricorrenza esplicita.

    Ritorna {"when": <daily@HH:MM|every_Nm>, "query": <corpo>, "label":
    <etichetta derivata>} se il parse e' PULITO (cadenza rappresentabile +
    corpo non vuoto). Altrimenti None (fallthrough al flusso normale —
    mai indovinare §2.8).
    """
    if not query or not isinstance(query, str):
        return None
    lexicon = _parser_lex.load_family("recurrence")
    if lexicon is None:
        return None
    interrogatives = _phrase_alt(lexicon["parser.recurrence.interrogative"])
    if query.rstrip().endswith("?") or (interrogatives and re.match(
            rf"^\s*(?:{interrogatives})(?!\w)", query,
            re.IGNORECASE | re.UNICODE)):
        return None
    when: str | None = None
    span: tuple[int, int] | None = None
    quantifiers = _phrase_alt(lexicon["parser.recurrence.quantifier"])
    reverse_units = _surface_to_canonical(
        lexicon["parser.recurrence.unit"])
    units = _phrase_alt(reverse_units)
    at_forms = _phrase_alt(lexicon["parser.recurrence.at"])
    if not quantifiers or not units or not at_forms:
        return None
    recurrence_rx = re.compile(
        rf"(?<!\w)(?:{quantifiers})(?!\w)\s+(?:(?P<n>\d+)\s*)?"
        rf"(?P<unit>{units})(?!\w)"
        rf"(?:\s+(?:{at_forms})(?!\w)\s+(?P<hh>\d{{1,2}})"
        rf"(?:[:.](?P<mm>\d{{2}}))?)?",
        re.IGNORECASE | re.UNICODE,
    )
    m = recurrence_rx.search(query)
    if m:
        n = int(m.group("n")) if m.group("n") else 1
        unit = reverse_units.get(m.group("unit").casefold())
        hh, mm = m.group("hh"), m.group("mm")
        if n <= 0:
            return None
        if unit == "half_hour":
            when = "every_30m"
        elif unit == "minute":
            when = f"every_{n}m"
        elif unit == "hour":
            when = f"every_{n * 60}m"
        elif unit == "day":
            # daily richiede l'orario: senza, il parse NON e' pulito.
            if hh is None or int(hh) > 23 or (mm and int(mm) > 59):
                return None
            when = f"daily@{int(hh):02d}:{int(mm) if mm else 0:02d}"
        else:
            return None
        span = m.span()
    else:
        daily = _phrase_alt(lexicon["parser.recurrence.daily"])
        daily_rx = re.compile(
            rf"(?<!\w)(?:{daily})(?!\w)(?:\s+(?:{at_forms})(?!\w)\s+"
            rf"(?P<hh>\d{{1,2}})(?:[:.](?P<mm>\d{{2}}))?)?",
            re.IGNORECASE | re.UNICODE,
        )
        m = daily_rx.search(query) if daily else None
        if m:
            hh, mm = m.group("hh"), m.group("mm")
            if hh is None or int(hh) > 23 or (mm and int(mm) > 59):
                return None
            when = f"daily@{int(hh):02d}:{int(mm) if mm else 0:02d}"
            span = m.span()
        else:
            hourly = _phrase_alt(lexicon["parser.recurrence.hourly"])
            m = re.search(
                rf"(?<!\w)(?:{hourly})(?!\w)", query,
                re.IGNORECASE | re.UNICODE,
            ) if hourly else None
            if m:
                when = "every_60m"
                span = m.span()
    if not when or span is None:
        return None
    if not _SCHEDULE_RE.match(when):
        return None
    body = _strip_clause(query, span, lexicon)
    # «crea un task che <azione>» → memorizza SOLO <azione> (§7.9 universale).
    body = _strip_create_framing(body, lexicon)
    # Corpo vuoto o senza sostanza ("ogni 30 minuti" e basta) → ambiguo.
    if len(re.sub(r"[^\w]", "", body, flags=re.UNICODE).replace("_", "")) < 3:
        return None
    label = body if len(body) <= 60 else body[:60].rsplit(" ", 1)[0]
    return {"when": when, "query": body, "label": label}


def register_user_task(
    *,
    label: str,
    when: str,
    query: str,
    actor: str,
    owner_user_id: str,
    channel: str,
    chat_id: str | None = None,
    times: int | None = None,
    grace_window_minutes: int | None = None,
    mandates: dict | None = None,
) -> dict:
    """Registra un task ricorrente user-defined. Restituisce il record.
    Idempotente: se name esiste gia', UPDATE.
    """
    owner_user_id = str(owner_user_id or "").strip()
    if not owner_user_id:
        raise ValueError("recurring task owner_user_id is required")
    schedule = _parse_when(when)
    name = _slugify(label or query)
    scheduler_name = _scheduler_entry_name(owner_user_id, name)
    if mandates is None:
        try:
            import task_mandates
            mandates = task_mandates.build_for_task(
                query, actor, owner_user_id=owner_user_id)
        except Exception as exc:
            log.warning("task mandate build failed closed: %s", exc)
            mandates = {}
    mandates_json = json.dumps(
        mandates if isinstance(mandates, dict) else {},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    conn = _open()
    try:
        # Quota check (anti-runaway).
        n_existing = conn.execute(
            "SELECT COUNT(*) FROM recurring_tasks "
            "WHERE owner_user_id=? AND name!=?",
            (owner_user_id, name),
        ).fetchone()[0]
        if n_existing >= MAX_TASKS_PER_ACTOR:
            raise ValueError(
                f"actor={actor} ha gia' {n_existing} task ricorrenti "
                f"(limite {MAX_TASKS_PER_ACTOR}). Cancellane uno con "
                f"delete_tasks prima di registrarne di nuovi."
            )
        # times: None/<=0 = forever; >=1 = max fire (one-shot=1).
        # fired_count reset a 0 per nuovo task / re-register stesso name.
        times_val = int(times) if times is not None and int(times) > 0 else None
        gw = int(grace_window_minutes) if grace_window_minutes else None
        conn.execute(
            "INSERT INTO recurring_tasks "
            "(name, owner_user_id, scheduler_name, schedule, query, actor, "
            " channel, chat_id, label, "
            " times, fired_count, grace_window_minutes, mandates, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1) "
            "ON CONFLICT(owner_user_id,name) DO UPDATE SET "
            "scheduler_name=excluded.scheduler_name, "
            "schedule=excluded.schedule, query=excluded.query, "
            "actor=excluded.actor, channel=excluded.channel, "
            "chat_id=excluded.chat_id, label=excluded.label, "
            "times=excluded.times, fired_count=0, "
            "grace_window_minutes=excluded.grace_window_minutes, "
            "mandates=excluded.mandates, enabled=1",
            (name, owner_user_id, scheduler_name, schedule, query, actor,
             channel, chat_id, label, times_val, gw, mandates_json),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM recurring_tasks "
            "WHERE owner_user_id=? AND name=?", (owner_user_id, name)
        ).fetchone())
    finally:
        conn.close()


def list_user_tasks(actor: str | None = None, *,
                    owner_user_id: str | None = None) -> list[dict]:
    conn = _open()
    try:
        if owner_user_id:
            rows = conn.execute(
                "SELECT * FROM recurring_tasks WHERE owner_user_id=? "
                "ORDER BY name", (owner_user_id,),
            ).fetchall()
        elif actor:
            rows = conn.execute(
                "SELECT * FROM recurring_tasks WHERE actor=? ORDER BY name",
                (actor,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recurring_tasks ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_user_tasks_readonly(owner_user_id: str) -> list[dict]:
    """Read one owner's tasks without DDL, migrations or cross-owner scans."""

    owner = str(owner_user_id or "").strip()
    if not owner or not DB_PATH.is_file():
        return []
    conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(recurring_tasks)").fetchall()
        }
        required = {"owner_user_id", "scheduler_name", "name", "query"}
        if not required.issubset(columns):
            raise RuntimeError("recurring task owner schema not initialized")
        return [dict(row) for row in conn.execute(
            "SELECT * FROM recurring_tasks WHERE owner_user_id=? "
            "ORDER BY name", (owner,),
        ).fetchall()]
    finally:
        conn.close()


def get_user_task_by_scheduler_name(owner_user_id: str,
                                    scheduler_name: str) -> dict | None:
    """Resolve one scheduler entry inside one immutable owner boundary.

    Scheduler payloads are deliberately treated as references, not as the
    current authority for identity, delivery coordinates or task contents.
    The live registry row is reloaded at every fire/callback.
    """

    owner = str(owner_user_id or "").strip()
    entry_name = str(scheduler_name or "").strip()
    if not owner or not entry_name:
        return None
    conn = _open()
    try:
        row = conn.execute(
            "SELECT * FROM recurring_tasks "
            "WHERE owner_user_id=? AND scheduler_name=? LIMIT 1",
            (owner, entry_name),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def get_user_task_by_id(owner_user_id: str, task_id: int | str) -> dict | None:
    """Resolve a short public task reference, scoped by its logical owner."""

    owner = str(owner_user_id or "").strip()
    try:
        numeric_id = int(task_id)
    except (TypeError, ValueError):
        return None
    if not owner or numeric_id < 1:
        return None
    conn = _open()
    try:
        row = conn.execute(
            "SELECT * FROM recurring_tasks "
            "WHERE owner_user_id=? AND id=? LIMIT 1",
            (owner, numeric_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def cancel_user_task(name_or_id, *, actor: str | None = None,
                     owner_user_id: str | None = None) -> bool:
    """Cancella un task per name (slug) O id numerico.
    Se actor specificato, cancella SOLO se appartiene a quell'actor.
    Ritorna True se trovato + cancellato."""
    conn = _open()
    try:
        # Discrimina: se int (o str che converte a int) → cerca per id;
        # altrimenti → cerca per name.
        as_int = None
        try:
            as_int = int(name_or_id)
        except (TypeError, ValueError):
            pass
        if as_int is not None:
            sql = "DELETE FROM recurring_tasks WHERE id=?"
            params: tuple = (as_int,)
        elif str(name_or_id).startswith("user_"):
            sql = "DELETE FROM recurring_tasks WHERE scheduler_name=?"
            params = (str(name_or_id),)
        else:
            sql = "DELETE FROM recurring_tasks WHERE name=?"
            params = (str(name_or_id),)
        if actor:
            sql += " AND actor=?"
            params = params + (actor,)
        if owner_user_id:
            sql += " AND owner_user_id=?"
            params = params + (owner_user_id,)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def purge_owner(owner_user_id: str) -> dict[str, int]:
    """Delete an owner's task registry, scheduler payloads and run history."""

    owner = str(owner_user_id or "").strip()
    if not owner:
        return {"tasks": 0, "schedule_entries": 0, "runs": 0}
    conn = _open()
    try:
        rows = conn.execute(
            "SELECT scheduler_name FROM recurring_tasks "
            "WHERE owner_user_id=?", (owner,),
        ).fetchall()
        scheduler_names = tuple(str(row["scheduler_name"]) for row in rows)
    finally:
        conn.close()
    from scheduler_v2 import client as sched_client
    # Scheduler state is execution-authoritative.  Purge it first; if this
    # fails the registry remains available for an idempotent retry instead of
    # losing the only mapping to personal history.
    purged = sched_client.purge_owner_jobs(
        owner,
        name_prefix=_scheduler_entry_name(owner, ""),
        hinted_names=scheduler_names,
    )
    conn = _open()
    try:
        with conn:
            tasks = conn.execute(
                "DELETE FROM recurring_tasks WHERE owner_user_id=?",
                (owner,),
            ).rowcount
    finally:
        conn.close()
    return {
        "tasks": tasks,
        "schedule_entries": purged["entries"],
        "runs": purged["runs"],
    }


def _set_user_task_enabled(name: str, enabled: bool, *,
                           owner_user_id: str) -> bool:
    """Keep the user registry mirror aligned with scheduler runtime state.

    ``schedule_entries`` is authoritative for execution; this mirror powers
    list/delete UX and must not claim that an auto-suspended task is active.
    """
    owner = str(owner_user_id or "").strip()
    if not owner:
        return False
    conn = _open()
    try:
        cur = conn.execute(
            "UPDATE recurring_tasks SET enabled=? "
            "WHERE owner_user_id=? AND (name=? OR scheduler_name=?)",
            (1 if enabled else 0, owner, name, name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_user_scheduler_enabled(scheduler_name: str, enabled: bool, *,
                               owner_user_id: str,
                               resume: bool = False) -> bool:
    """Synchronize the user registry and execution-authoritative scheduler.

    Enabling writes the blocking registry guard first and compensates it when
    the scheduler operation fails.  Disabling also writes the guard first, so
    a scheduler outage cannot accidentally leave the task executable.
    """

    owner = str(owner_user_id or "").strip()
    name = str(scheduler_name or "").strip()
    if not owner or not name:
        return False
    if not _set_user_task_enabled(name, enabled, owner_user_id=owner):
        return False
    try:
        from scheduler_v2 import client as sched_client
        if enabled and resume:
            changed = sched_client.resume_job(name)
        else:
            changed = sched_client.toggle_job(name, bool(enabled))
    except Exception:
        changed = False
    if changed:
        return True
    if enabled:
        # Restore the safe, suspended state if scheduler activation failed.
        _set_user_task_enabled(name, False, owner_user_id=owner)
    return False


def get_user_task_by_scheduler_name_admin(scheduler_name: str) -> dict | None:
    """Resolve a globally unique scheduler key for the local admin CLI."""

    name = str(scheduler_name or "").strip()
    if not name:
        return None
    conn = _open()
    try:
        row = conn.execute(
            "SELECT * FROM recurring_tasks WHERE scheduler_name=? LIMIT 1",
            (name,),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _live_telegram_recipient(owner_user_id: str) -> str:
    """Return the currently verified, runnable Telegram delivery binding."""

    import pairing
    import users

    binding = users.get_channel(owner_user_id, "telegram")
    recipient = str((binding or {}).get("recipient_id") or "").strip()
    if not binding or not binding.get("verified_at") or not recipient:
        return ""
    authority = pairing.get_pairing("telegram", recipient)
    if authority is None or authority.autonomy_level == "ReadOnly":
        return ""
    return recipient


# --- Bootstrap nel scheduler builtin --------------------------------------

def _increment_fired_and_check_done(
        name: str, *, owner_user_id: str) -> tuple[int, bool]:
    """Atomico: increment fired_count, ritorna (new_count, done).
    done=True se fired_count >= times (e times non-NULL/0)."""
    conn = _open()
    try:
        conn.execute(
            "UPDATE recurring_tasks SET fired_count = fired_count + 1 "
            "WHERE owner_user_id=? AND name=?",
            (owner_user_id, name),
        )
        row = conn.execute(
            "SELECT fired_count, times FROM recurring_tasks "
            "WHERE owner_user_id=? AND name=?",
            (owner_user_id, name),
        ).fetchone()
        conn.commit()
        if row is None:
            return 0, False
        fc = row["fired_count"]
        t = row["times"]
        done = bool(t) and fc >= t
        return fc, done
    finally:
        conn.close()


def _scheduled_push_is_noop(log) -> bool:
    """True se il run schedulato NON ha prodotto nulla → push soppresso §2.8.

    Regole (deterministiche §7.9, su `log.effect_counts` calcolato da
    TurnLog.write via pipeline_effect_counts):
    - dialog/cap pendenti → MAI sopprimere (serve risposta utente);
    - final_kind != answer → MAI sopprimere (errori restano visibili);
    - step con ok=False → MAI sopprimere (fallimenti §2.8 vanno riportati);
    - turno con step MUTATING tentati → sopprimi se 0 mutazioni effettive
      (es. maintenance issue: trovate N issue ma 0 nuove registrate);
    - turno solo-lettura → sopprimi se 0 items prodotti (0 mail, 0 issue).
    Caso live 12/6: maintenance github ogni 30m su 0 issue aperte spingeva
    «analizzato, salvato bozze pronte, notificato» — falso successo.
    """
    if getattr(log, "expandable_caps", None):
        return False
    if getattr(log, "final_kind", "") != "answer":
        return False
    from pipeline_effects import counts_indicate_noop
    return counts_indicate_noop(getattr(log, "effect_counts", None))


def _scheduled_turn_outcome(log):
    """Esito semantico del turno: success, partial o error.

    La consegna al canale e' un effetto successivo e non puo' cambiare questa
    classificazione. I conteggi sono quelli autoritativi di TurnLog.write.
    """
    from scheduler_v2.models import CallbackOutcome
    final_kind = getattr(log, "final_kind", "")
    counts = getattr(log, "effect_counts", None) or {}
    failures = int(counts.get("failures") or 0)
    produced = int(counts.get("items") or 0)
    mutations = int(counts.get("mutations") or 0)
    if final_kind not in ("error", "loop_break") and failures <= 0:
        return None
    detail = ""
    try:
        from engine.types import result_error_detail
        for step in reversed(getattr(log, "steps", None) or []):
            result = step.result if isinstance(step.result, dict) else None
            if isinstance(result, dict) and result.get("ok") is False:
                detail = result_error_detail(result)
                if detail:
                    break
    except Exception:
        detail = ""
    status = "partial" if produced > 0 or mutations > 0 else "error"
    if not detail:
        detail = f"turn kind={final_kind or '?'} failures={failures}"
    return CallbackOutcome(status=status, error=detail)


def _run_user_query_callback(record: dict):
    """Esegue un task nella lingua autorevole dell'istanza."""
    owner = None
    record_owner = str(record.get("owner_user_id") or "").strip()
    if not record_owner:
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error", error="ownerless scheduled task rejected",
            output=f"[{record.get('name') or '?'}] owner unavailable",
        )
    scheduler_name = str(record.get("scheduler_name") or "").strip()
    if not scheduler_name:
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error", error="scheduled task reference unavailable",
            output=f"[{record.get('name') or '?'}] task reference unavailable",
        )
    try:
        import users as _users
        owner = _users.get_user(record_owner)
        if (owner is not None
                and str(owner.get("id") or "") != record_owner):
            owner = None
    except Exception as ex:
        log.debug("scheduled user lookup unavailable: %s", ex)
    if owner is None:
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error",
            error="scheduled logical owner unavailable",
            output=f"[{record.get('name') or '?'}] owner unavailable",
        )
    if str(owner.get("autonomy_level") or "") == "read_only":
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error",
            error="scheduled execution blocked by current autonomy",
            output=f"[{record.get('name') or '?'}] autonomy is read_only",
        )
    live_record = get_user_task_by_scheduler_name(
        record_owner, scheduler_name)
    if live_record is None or not int(live_record.get("enabled") or 0):
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error", error="scheduled task registry row unavailable",
            output=f"[{record.get('name') or '?'}] task unavailable",
        )
    if str(live_record.get("name") or "") != str(record.get("name") or ""):
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error", error="scheduled task identity mismatch",
            output=f"[{record.get('name') or '?'}] task identity mismatch",
        )
    scoped_record = dict(live_record)
    scoped_record["owner_user_id"] = str(owner["id"])
    scoped_record["actor"] = str(owner.get("name") or "")
    if scoped_record.get("channel") == "telegram":
        try:
            recipient = _live_telegram_recipient(record_owner)
        except Exception as exc:
            from scheduler_v2.models import CallbackOutcome
            return CallbackOutcome(
                status="error", error="scheduled delivery authority unavailable",
                output=(f"[{record.get('name') or '?'}] delivery authority "
                        f"unavailable: {type(exc).__name__}"),
            )
        if not recipient:
            from scheduler_v2.models import CallbackOutcome
            return CallbackOutcome(
                status="error", error="scheduled delivery authority revoked",
                output=f"[{record.get('name') or '?'}] delivery revoked",
            )
    import i18n as _i18n
    try:
        from user_lifecycle import OwnerUnavailable, owner_session
        with owner_session(scoped_record["owner_user_id"]):
            with _i18n.instance_language_context():
                return _run_user_query_callback_scoped(scoped_record)
    except OwnerUnavailable:
        from scheduler_v2.models import CallbackOutcome
        return CallbackOutcome(
            status="error",
            error="scheduled logical owner is being deleted",
            output=f"[{record.get('name') or '?'}] owner unavailable",
        )


def _run_user_query_callback_scoped(record: dict):
    """Callback canonica `run_user_query`: rilancia run_turn + pusha canale.

    Registrata in `_CALLBACKS` come 'run_user_query' al boot. Refactor
    della funzione NON rompe i record DB (callback_key resta uguale).

    Robustezza:
    - Try/except attorno a run_turn (no propagazione exception).
    - Push canale con 1 retry su transient.
    - Output diagnostico salvato in scheduler.runs.output.
    - Run a vuoto (0 effetti reali) → NESSUN push, solo log (§2.8:
      niente notifiche di falso successo; vedi _scheduled_push_is_noop).
    """
    from scheduler_v2.models import CallbackOutcome

    log_msg = []
    try:
        from agent_runtime import run_turn
        # Scope turno SCHEDULATO (12/6/2026): attiva il guard deterministico
        # `treated_issues_guard` — i work-item già trattati (issue in
        # `issue_qa`) non ri-entrano negli step LLM-costosi (classify/
        # describe/extract → frontier). Solo run ricorrenti: i turni
        # interattivi restano intoccati. Reset garantito dal context manager.
        from treated_issues_guard import scheduled_turn_scope
        with scheduled_turn_scope(
                task_name=record.get("scheduler_name") or ""):
            log = run_turn(
                record["query"],
                actor=record["actor"],
                channel=record["channel"],
                owner_user_id=record["owner_user_id"],
            )
        msg = (log.final_message or "").strip()
        if not msg:
            return CallbackOutcome(
                status="error",
                error="run_turn returned an empty final_message",
                output=(f"[{record['name']}] run_turn empty final_message "
                        f"(kind={getattr(log,'final_kind',None)})"),
            )
        log_msg.append(f"[{record['name']}] run_turn ok kind={getattr(log,'final_kind',None)} steps={len(log.steps or [])}")
    except Exception as e:
        detail = f"run_turn crashed: {type(e).__name__}: {e}"
        return CallbackOutcome(
            status="error", error=detail,
            output=f"[{record['name']}] {detail}")
    turn_outcome = _scheduled_turn_outcome(log)

    def _finish(output: str, *, delivery_error: str = ""):
        if delivery_error:
            return CallbackOutcome(status="error", output=output,
                                   error=delivery_error)
        if turn_outcome is not None:
            return CallbackOutcome(
                status=turn_outcome.status, output=output,
                error=turn_outcome.error)
        return output
    # §2.8 notifica onesta (12/6/2026): run schedulato a vuoto → niente push.
    # Diagnostica in runs.output (consultabile da /admin/runs), zero rumore
    # verso l'utente. Idempotente col loop dello scheduler: N run a vuoto =
    # N log silenziosi, 0 notifiche.
    if _scheduled_push_is_noop(log):
        _c = getattr(log, "effect_counts", None) or {}
        log_msg.append(
            f"empty run (items={_c.get('items', 0)} "
            f"mutations={_c.get('mutations', 0)}) → push suppressed (§2.8)")
        return _finish(" | ".join(log_msg))
    delivery_recipient = ""
    if record["channel"] == "telegram":
        try:
            # A turn may take minutes.  Do not retain the preflight recipient:
            # a revocation or rebind during execution must take effect before
            # any result or pending approval is delivered.
            delivery_recipient = _live_telegram_recipient(
                str(record.get("owner_user_id") or ""))
        except Exception as exc:
            return _finish(
                " | ".join(log_msg),
                delivery_error=("scheduled delivery authority unavailable: "
                                f"{type(exc).__name__}: {exc}"),
            )
        if not delivery_recipient:
            return _finish(
                " | ".join(log_msg),
                delivery_error="scheduled delivery authority revoked",
            )
    if record["channel"] == "telegram" and delivery_recipient:
        prefix = (
            f"[task: {record['label'] or record['name']}]\n"
            if record.get("label") else ""
        )
        # Proposta interattiva lasciata dal turno schedulato (dialog
        # get_inputs di autorizzazione — es. approva/edita/rifiuta bozza
        # del flusso manutenzione — oppure admin_approval): senza daemon
        # in mezzo, il push deve (1) salvare il cap_pending per il chat_id
        # cosi' una RISPOSTA TESTUALE al messaggio risolve il dialogo allo
        # stesso modo del percorso interattivo, e (2) allegare la inline
        # keyboard (i callback `dlg:`/`cap:` sono self-contained: il
        # daemon li risolve dallo stato persistito). Niente keyboard per
        # fmt dialogue/form = degrado onesto §2.8 (lista numerata, testo).
        caps = list(getattr(log, "expandable_caps", None) or [])
        buttons = None
        if caps and isinstance(caps[0], dict):
            try:
                from channels.daemon import _cap_pending_save
                _cap_pending_save(delivery_recipient, record["query"],
                                  caps[0], getattr(log, "turn_id", ""),
                                  owner_user_id=record["owner_user_id"])
            except Exception as e:
                log_msg.append(
                    f"cap_pending save failed: {type(e).__name__}: {e}")
            try:
                from channels.inline_ui import (
                    keyboard_for_proposal, sender_state_candidates,
                )
                candidates = sender_state_candidates(
                    "telegram", delivery_recipient,
                    actor=record.get("actor"),
                    sender_for_state=caps[0].get("sender_for_state"),
                )
                # preview_step ignorato: l'album thumb richiede il daemon;
                # la keyboard coi label resta utilizzabile (degrado onesto).
                buttons, _preview = keyboard_for_proposal(
                    caps[0], sender_candidates=candidates,
                    owner_user_id=record["owner_user_id"],
                    turn_id=getattr(log, "turn_id", None),
                )
            except Exception as e:
                log_msg.append(
                    f"inline keyboard build failed: {type(e).__name__}: {e}")
        for attempt in (1, 2):
            try:
                from channels.telegram import TelegramChannel
                from channels import OutboundMessage
                ch = TelegramChannel()
                resp = ch.send(delivery_recipient,
                                OutboundMessage(text=prefix + msg,
                                                 buttons=buttons))
                if isinstance(resp, dict) and not resp.get("ok", True):
                    raise RuntimeError(resp.get("error") or "send returned ok:false")
                log_msg.append(
                    f"pushed telegram chat={delivery_recipient} "
                    f"attempt={attempt}")
                return _finish(" | ".join(log_msg))
            except Exception as e:
                log_msg.append(f"push attempt {attempt} failed: {type(e).__name__}: {e}")
                if attempt == 2:
                    detail = f"channel push failed: {type(e).__name__}: {e}"
                    return _finish(" | ".join(log_msg),
                                   delivery_error=detail)
                time.sleep(2)
    out = " | ".join(log_msg) + f" | no push channel: msg[:80]={msg[:80]}"
    return _finish(out)


def _notify_circuit_break(entry, error) -> None:
    """Notify only while the immutable owner is live and leased."""

    payload = getattr(entry, "payload", None) or {}
    owner_user_id = str(payload.get("owner_user_id") or "").strip()
    entry_name = str(getattr(entry, "name", "") or "")
    if (not owner_user_id
            or payload.get("scheduler_name") != entry_name
            or getattr(entry, "origin", "") != "user"):
        log.warning("ownerless/non-user circuit-break hook rejected: %s",
                    entry_name)
        return
    try:
        from user_lifecycle import OwnerUnavailable, owner_session
        with owner_session(owner_user_id):
            import users
            owner = users.get_user(owner_user_id)
            if (owner is None
                    or str(owner.get("id") or "") != owner_user_id):
                return
            binding = users.get_channel(owner_user_id, "telegram")
            if not binding or not binding.get("verified_at"):
                return
            chat_id = str(binding.get("recipient_id") or "").strip()
            if not chat_id:
                return
            _notify_circuit_break_scoped(
                entry, error, owner_user_id=owner_user_id, chat_id=chat_id)
    except OwnerUnavailable:
        log.info("circuit-break notify skipped for deleted owner %s",
                 owner_user_id)


def _notify_circuit_break_scoped(entry, error, *, owner_user_id: str,
                                 chat_id: str) -> None:
    """Notifica l'owner che il suo task ricorrente e' stato auto-disabilitato
    dal circuit-breaker (N fallimenti consecutivi). Offre 3 scelte inline:
    Continua (riattiva) / Sospendi (resta off, ripristinabile) / Cancella
    (rimuove la schedulazione). callback_data = `sched:<azione>:<task_id>`.

    Best-effort: nessuna eccezione propagata (il disable e' gia' persistito).
    Se il task non nasce da Telegram, risolve il canale Telegram verificato
    del suo owner; per l'host usa infine il default configurato del canale.
    Testo user-facing via i18n DB (§11, builtin=multilang): chiavi
    MSG_SCHED_CIRCUIT_BREAK + MSG_BTN_SCHED_*."""
    from messages import get as _msg
    payload = getattr(entry, "payload", None) or {}
    label = payload.get("label") or payload.get("name") or getattr(entry, "name", "?")
    entry_name = getattr(entry, "name", "")
    task = get_user_task_by_scheduler_name(owner_user_id, entry_name)
    if task is None:
        log.warning("circuit-break registry mapping unavailable for '%s'",
                    entry_name)
        return
    task_id = int(task["id"])
    try:
        from scheduler_v2.daemon import _CIRCUIT_BREAK_AFTER as _n
    except Exception:
        _n = 3
    try:
        _set_user_task_enabled(
            entry_name, False, owner_user_id=owner_user_id)
    except Exception as exc:
        log.warning("circuit-break mirror sync failed for '%s': %s",
                    entry_name, exc)
    err_line = (str(error)[:300]) if error else _msg("MSG_ERR_UNKNOWN")
    text = _msg("MSG_SCHED_CIRCUIT_BREAK", label=label, n=_n, error=err_line)
    buttons = [[
        {"text": _msg("MSG_BTN_SCHED_CONTINUE"), "data": f"sched:cont:{task_id}"},
        {"text": _msg("MSG_BTN_SCHED_SUSPEND"), "data": f"sched:susp:{task_id}"},
        {"text": _msg("MSG_BTN_SCHED_CANCEL"), "data": f"sched:canc:{task_id}"},
    ]]
    try:
        current_chat_id = _live_telegram_recipient(owner_user_id)
        if not current_chat_id:
            return
        from channels.telegram import TelegramChannel
        from channels import OutboundMessage
        ch = TelegramChannel()
        result = ch.send(
            current_chat_id, OutboundMessage(text=text, buttons=buttons))
        if isinstance(result, dict) and not result.get("ok", True):
            raise RuntimeError(result.get("error") or "send returned ok:false")
        log.info("circuit-break notificato a chat=%s per task '%s'",
                 current_chat_id, entry_name)
    except Exception as e:
        log.warning("circuit-break notify failed for '%s': %s", entry_name, e)


def _wrap_with_times_tracking(fn):
    """Wrap callback con auto-increment fired_count + auto-cancel se done."""
    def _wrapped(record):
        out = fn(record)
        owner_user_id = str(record.get("owner_user_id") or "")
        if not owner_user_id:
            return out
        fc, done = _increment_fired_and_check_done(
            record["name"], owner_user_id=owner_user_id)
        if done:
            try:
                # Lo scheduler v2 possiede il countdown e disabilita la entry
                # nel suo finally. Qui si rimuove solo la proiezione legacy.
                cancel_user_task(
                    record["name"], owner_user_id=owner_user_id)
            except Exception as _e:
                log.warning("recurring task mirror cleanup failed: %s", _e)
            suffix = (f"times reached ({fc}/{record.get('times')}) "
                      "→ auto-disabled")
        else:
            suffix = f"fired_count={fc}"
        try:
            from scheduler_v2.models import CallbackOutcome
            if isinstance(out, CallbackOutcome):
                return out.append_output(suffix)
        except Exception:
            pass
        return f"{out} | {suffix}"
    return _wrapped


# Auto-register canonical callback al import-time. Future callback aggiunte
# qui (o da plugin) via `register_callback("nome", fn)`.
register_callback("run_user_query", _wrap_with_times_tracking(_run_user_query_callback))


def _make_task_fn(record: dict):
    """Wrapper closure che dispatch via callback_key del record.
    NON salva la closure in DB: solo la chiave persiste, la closure
    viene risolta al fire dal registry. Sopravvive refactor della
    callback canonica.
    """
    def _fire():
        try:
            return dispatch_callback(record.get("callback_key", "run_user_query"), record)
        except KeyError as e:
            return f"[{record['name']}] callback dispatch failed: {e}"
    return _fire


# --- Tool definitions per agent_runtime PLANNER ---------------------------

CREATE_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "create_tasks",
        "description": (
            "Registra un task temporizzato che Metnos esegue automaticamente "
            "alla cadenza specificata, senza interazione utente. Al fire "
            "lancia la query come se fosse un turno utente reale e invia "
            "il risultato sul canale dell'utente che lo ha richiesto. "
            "USA QUESTO TOOL per: "
            "(a) ricorrenze infinite ('ogni giorno alle X', 'ogni N ore/minuti', "
            "'sempre') → omettere `times` (default forever); "
            "(b) ONE-SHOT ('fra 30 minuti', 'domani alle 14', 'una volta sola') "
            "→ `times=1` con `when` calcolato (es. 'fra 30 min' → "
            "`when='every_30m', times=1`); "
            "(c) max-N-volte ('per le prossime 5 settimane', 'fai 10 volte') "
            "→ `times=N`. "
            "REGISTRA SUBITO senza chiedere conferma. "
            "Dopo aver registrato conferma all'utente in 1 frase con il nome "
            "esatto del task creato e i parametri salienti."
        ),
        "parameters": {
            "type": "object",
            "required": ["label", "when", "query"],
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Etichetta umana del task (es. 'check posta mattutina'). Usata per cancellare/elencare e come prefisso del messaggio di output.",
                },
                "when": {
                    "type": "string",
                    "description": "Quando: 'daily@HH:MM' (es. 'daily@08:00') oppure 'every_Nm' (es. 'every_30m'). HH:MM e' ora locale del SO host (es. CEST in Italia, gestita automaticamente dallo scheduler): registra l'orario cosi' come l'ha detto l'utente, senza convertire fusi. Per 'fra X minuti' o 'fra X ore' usa 'every_Xm' + times=1.",
                },
                "query": {
                    "type": "string",
                    "description": "Query da rilanciare al fire del task, in italiano. Es. 'leggi le mail di oggi importanti', 'ricordami di chiamare Roberto'. Il task la passera' a un nuovo run_turn.",
                },
                "times": {
                    "type": "integer",
                    "description": "Numero massimo di esecuzioni. OMETTI per ricorrenza infinita (default). 1 = ONE-SHOT (esegue una volta sola, poi auto-cancella). N = max N volte poi auto-cancella. Combinabile con qualsiasi `when`.",
                    "minimum": 1,
                },
                "grace_window_minutes": {
                    "type": "integer",
                    "description": "Tolleranza ritardo in minuti per recover-missed (daemon down al `when`). Es. 240 = fire entro 4h dal target, oltre skip. OMETTI = recover illimitato.",
                    "minimum": 1,
                },
            },
        },
    },
}

LIST_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "list_tasks",
        "description": (
            "Elenca i TASK RICORRENTI / PROMEMORIA / TIMER schedulati "
            "(NON processi di sistema, NON eventi calendar). "
            "USA per: 'che task ho schedulato', 'mostrami i miei promemoria', "
            "'cosa ho pianificato', 'quali timer ho attivi', 'lista task ricorrenti', "
            "'cosa fa Metnos automaticamente'. "
            "NON CONFONDERE CON: `get_processes` (processi sistema), "
            "`read_events` (eventi calendar/appuntamenti)."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

DELETE_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "delete_tasks",
        "description": (
            "Cancella/ferma un TASK RICORRENTE / PROMEMORIA / TIMER schedulato. "
            "USA per: 'cancella il task ping', 'ferma il timer X', "
            "'rimuovi il promemoria delle mail', 'stoppa il task ricorrente'. "
            "Accetta `id` numerico (preferito, univoco) o `name` slug. "
            "Se l'utente non specifica chiaramente quale task, chiama prima "
            "list_tasks per mostrare l'elenco con id. "
            "Un actor cancella solo i propri task. "
            "NON CONFONDERE CON: `delete_events` (eventi calendar), "
            "`kill` processo sistema."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "ID numerico univoco del task. PREFERITO se l'utente cita un numero o se ci sono task con nomi simili.",
                },
                "name": {
                    "type": "string",
                    "description": "Slug del task (es. 'check_posta_mattutina'). Usa solo se l'id non e' disponibile.",
                },
            },
        },
    },
}

READ_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "read_tasks",
        "description": (
            "Mostra dettaglio di UN TASK RICORRENTE / PROMEMORIA / TIMER per nome: "
            "schedule, ultima esecuzione, esito ultimo fire, query, label, storico. "
            "USA per: 'mostra dettaglio task X', 'quando ha girato l'ultima volta', "
            "'che esito ha avuto', 'storico esecuzioni task X', 'ultima esecuzione del timer'. "
            "NON CONFONDERE CON: `get_processes` (processi sistema), "
            "`read_events` (eventi calendar)."
        ),
        "parameters": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Nome del task (slug)."},
            },
        },
    },
}

SET_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "set_tasks",
        "description": (
            "Cambia lo stato di un task ricorrente esistente. Due operazioni "
            "in mutua esclusione: "
            "(a) `enabled=bool` abilita/disabilita temporaneamente "
            "il task (USA per 'metti in pausa X', 'riattiva X', 'sospendi'); "
            "(b) `fire_now=true` forza l'esecuzione immediata fuori cadenza "
            "(USA per 'esegui subito X', 'forza il fire', 'prova adesso X'). "
            "Specifica esattamente uno dei due. fire_now solo HOST."
        ),
        "parameters": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "description": "Nome del task (slug)."},
                "enabled": {"type": "boolean", "description": "true=abilita, false=disabilita. Mutex con fire_now."},
                "fire_now": {"type": "boolean", "description": "true=fire immediato. Mutex con enabled. Solo HOST."},
            },
        },
    },
}

READ_TASKS_HISTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "read_tasks_history",
        "description": (
            "Ritorna lo STORICO ESECUZIONI di un TASK RICORRENTE / PROMEMORIA / TIMER "
            "(o di tutti). Per ogni fire: timestamp, status (ok/error/timeout/skipped), "
            "duration, output. "
            "USA per: 'mostrami gli ultimi N fire del task X', 'cronologia task', "
            "'storico esecuzioni del task ricorrente', 'storico timer', "
            "'ha mai dato errore il task', 'log esecuzioni schedulate', "
            "'storico ultimi N giorni'. "
            "Dopo il primo ok EMETTI final_answer con un riassunto: NON ripetere "
            "la call con limit diverso (l'observation gia' contiene history completa). "
            "NON CONFONDERE CON: `get_processes` (processi sistema correnti), "
            "`read_events` (eventi calendar)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome task. Omesso = tutti i task."},
                "limit": {"type": "integer", "description": "Max righe ritornate. Default 200.", "default": 200},
                "time_window": {
                    "type": "string",
                    "description": (
                        "Filtro temporale canonical applicato a started_at del fire: "
                        "'last-Nd' (es. 'last-7d'), 'last-Nh', 'today', 'yesterday', "
                        "ISO range 'YYYY-MM-DD/YYYY-MM-DD', anno 'YYYY'. Omesso = tutto."
                    ),
                },
            },
        },
    },
}

# --- Catalog inproc-tool injection (loader pattern) ----------------------
# Esponiamo i 6 tool builtin a `loader._inject_inproc_tool_specs` cosi'
# entrano nel catalog `/admin/executors` e nei coverage check §2.2 (object
# `tasks`). Idempotente: handcrafted vince per costruzione (ADR 0079).
BUILTIN_INPROC_SPECS = [
    {"name": "create_tasks", "tool_spec": CREATE_TASKS_TOOL,
     "affinity": ["task", "ricorrente", "schedule", "promemoria", "timer",
                  "recurring", "reminder", "every", "daily"]},
    {"name": "list_tasks", "tool_spec": LIST_TASKS_TOOL,
     "affinity": ["task", "elenco", "lista", "scheduled", "ricorrenti", "list"]},
    {"name": "delete_tasks", "tool_spec": DELETE_TASKS_TOOL,
     "affinity": ["task", "cancella", "elimina", "rimuovi", "delete",
                  "remove"]},
    {"name": "read_tasks", "tool_spec": READ_TASKS_TOOL,
     "affinity": ["task", "dettaglio", "info", "read", "show"]},
    {"name": "set_tasks", "tool_spec": SET_TASKS_TOOL,
     "affinity": ["task", "abilita", "disabilita", "pausa", "enable",
                  "disable", "fire"]},
    {"name": "read_tasks_history", "tool_spec": READ_TASKS_HISTORY_TOOL,
     "affinity": ["task", "storico", "history", "fire", "log",
                  "esecuzioni"]},
]

# NB: run_scheduled_task_now fuso in set_tasks(fire_now=true) per coerenza
# §2.2 (no verb `execute`). Vedi handle_set_tasks per dispatch interno.


# --- Handler dispatcher --------------------------------------------------

def handle_create_tasks(args: dict, *, actor: str, channel: str,
                        owner_user_id: str = "",
                        chat_id: str | None = None) -> dict:
    label = args.get("label")
    when = args.get("when")
    query = args.get("query")
    times = args.get("times")
    grace = args.get("grace_window_minutes")
    if not (label and when and query):
        return {"ok": False, "error": "missing required: label/when/query"}
    try:
        rec = register_user_task(
            label=label, when=when, query=query,
            actor=actor, owner_user_id=owner_user_id,
            channel=channel, chat_id=chat_id,
            times=times, grace_window_minutes=grace,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    # Hot-register nel scheduler v2: scrive in schedule_entries con UPSERT;
    # la closure NON viene salvata, callback_key='run_user_query' viene
    # risolto dal CallbackRegistry del daemon al fire (lezione F1 giorgio2).
    try:
        from scheduler_v2 import client as sched_client
        gw_min = rec.get("grace_window_minutes")
        gw_s = int(gw_min) * 60 if gw_min else None
        sched_client.add_job(
            name=rec["scheduler_name"],
            trigger=rec["schedule"],
            callback_key=rec.get("callback_key") or "run_user_query",
            payload={
                "name": rec["name"],
                "owner_user_id": rec["owner_user_id"],
                "scheduler_name": rec["scheduler_name"],
                "query": rec["query"],
                "actor": rec["actor"],
                "channel": rec["channel"],
                "chat_id": rec.get("chat_id"),
                "label": rec.get("label"),
                "times": rec.get("times"),
            },
            origin="user",
            grace_window_s=gw_s,
            label=rec.get("label") or "",
            description=f"user task: {rec.get('label')} (actor={actor})",
            remaining_runs=int(rec.get("times") or 0),
        )
    except Exception as _e:
        log.warning("scheduler_v2 hot-register failed: %s", _e)
        # Fail closed: a durable registry row must never claim that a task is
        # active while its execution projection is absent or indeterminate.
        # Keep the row (and therefore the identity needed for repair/audit),
        # but suspend it before reporting the failure to the caller.
        suspended = _set_user_task_enabled(
            rec["scheduler_name"], False,
            owner_user_id=rec["owner_user_id"],
        )
        rec["enabled"] = False
        return {
            "ok": False,
            "error": "scheduler registration failed",
            "task": rec,
            "persisted_suspended": bool(suspended),
        }
    return {
        "ok": True,
        "task": rec,
        "message": f"Task '{rec['name']}' registrato. Cadenza: {rec['schedule']}.",
    }


def _schedule_human(sched: str) -> str:
    """daily@08:00 → 'ogni giorno alle 08:00'; every_5m → 'ogni 5 minuti'."""
    if sched.startswith("daily@"):
        return f"ogni giorno alle {sched[len('daily@'):]}"
    if sched.startswith("every_") and sched.endswith("m"):
        try:
            n = int(sched[len("every_"):-1])
            if n == 1:
                return "ogni minuto"
            if n < 60:
                return f"ogni {n} minuti"
            h, m = divmod(n, 60)
            if m == 0:
                return f"ogni {h} ore" if h > 1 else "ogni ora"
            return f"ogni {h}h {m}min"
        except ValueError:
            pass
    return sched  # fallback


def _next_fire_estimate(sched: str, last_run: str | None) -> str:
    """Stima prossimo fire in italiano. last_run = ISO string o None.

    `daily@HH:MM` e' interpretato in ora locale del SO host (vedi
    `scheduler._local_target_today_utc`). Il confronto avviene in UTC
    ma la stima viene resa in HH:MM locali coerenti con la registrazione.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    if sched.startswith("daily@"):
        try:
            hh, mm = sched[len("daily@"):].split(":")
            h, m = int(hh), int(mm)
        except (ValueError, IndexError):
            return "?"
        # Calcoliamo target nella TZ locale del SO, poi convertiamo a UTC.
        local_now = now.astimezone()
        local_target = local_now.replace(
            hour=h, minute=m, second=0, microsecond=0,
        )
        target = local_target.astimezone(timezone.utc)
        is_tomorrow = target <= now
        if is_tomorrow:
            target += timedelta(days=1)
        delta = target - now
        hrs = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        when_word = "domani" if is_tomorrow else "oggi"
        if hrs == 0:
            return f"fra {mins} minuti"
        return f"{when_word} alle {h:02d}:{m:02d} (fra ~{hrs}h{mins:02d}m)"
    if sched.startswith("every_") and sched.endswith("m"):
        try:
            n = int(sched[len("every_"):-1])
        except ValueError:
            return "?"
        if last_run:
            try:
                last = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                target = last + timedelta(minutes=n)
                if target <= now:
                    return "imminente (al prossimo tick)"
                delta = target - now
                mins = max(1, int(delta.total_seconds() // 60))
                return f"fra ~{mins} minuti"
            except (ValueError, TypeError):
                pass
        return f"fra ~{n} minuti (mai eseguito)"
    return "?"


def handle_list_tasks(args: dict, *, actor: str,
                      owner_user_id: str = "", **_) -> dict:
    if not owner_user_id:
        return {"ok": False, "error": "logical owner unavailable"}
    tasks = list_user_tasks(owner_user_id=owner_user_id)
    if not tasks:
        return {"ok": True, "count": 0, "tasks": [],
                "summary_human": "Nessun task pianificato."}
    # Join con stato runtime scheduler v2 per last_run_at + last_status.
    sched_state = {}
    try:
        from scheduler_v2 import client as sched_client
        for r in sched_client.list_jobs():
            sched_state[r["name"]] = r
    except Exception as _e:  # silent swallow (auto-fixed)
        log.warning("silent exception in %s: %s", __name__, _e)
    enriched = []
    for t in tasks:
        times = t.get("times")
        fired = t.get("fired_count") or 0
        # frequency_human
        if not times:
            t["frequency_human"] = "ricorrente infinita"
            t["remaining"] = None
        elif times == 1:
            t["frequency_human"] = "one-shot"
            t["remaining"] = max(0, times - fired)
        else:
            t["frequency_human"] = f"max {times} esecuzioni"
            t["remaining"] = max(0, times - fired)
        # schedule_human
        t["schedule_human"] = _schedule_human(t["schedule"])
        # last_run + status human
        sched_row = sched_state.get(t["scheduler_name"], {})
        last_run = sched_row.get("last_run_at")
        last_status = sched_row.get("last_status")
        scheduler_enabled = sched_row.get("enabled")
        t["registry_enabled"] = bool(t.get("enabled"))
        if scheduler_enabled is not None:
            t["scheduler_enabled"] = bool(scheduler_enabled)
            t["enabled"] = bool(scheduler_enabled)
        else:
            t["scheduler_enabled"] = None
            t["enabled"] = bool(t.get("enabled"))
        t["last_status"] = last_status
        t["last_error"] = str(sched_row.get("last_error") or "")
        t["consecutive_failures"] = int(
            sched_row.get("consecutive_failures") or 0)
        if last_run:
            try:
                from datetime import datetime
                # Persistito in UTC, mostrato all'utente in ora locale del SO
                # per coerenza con il campo HH:MM dello schedule.
                dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
                dt_local = dt.astimezone()
                t["last_fire_human"] = (
                    f"{dt_local.strftime('%d/%m %H:%M')} (esito: {last_status or '?'})"
                )
            except (ValueError, TypeError):
                t["last_fire_human"] = f"{last_run} (esito: {last_status})"
        else:
            t["last_fire_human"] = "mai eseguito"
        # next_fire stimato
        t["next_fire_human"] = _next_fire_estimate(t["schedule"], last_run)
        # warning ultimo se remaining=1
        if t.get("remaining") == 1 and times and times > 1:
            t["warning"] = "ULTIMA esecuzione, poi auto-cancella"
        enriched.append(t)
    # Summary user-facing con ID esplicito (15/5/2026): niente numerazione
    # progressiva 1./2./3. — usa l'id del DB per cancellazione precisa
    # ("cancella timer 17"). i18n via i18n.sqlite (ADR 0104).
    from messages import get as _msg
    lines = [_msg("MSG_TASKS_LIST_HEADER", count=len(enriched))]
    for t in enriched:
        last = t.get("last_fire_human") or _msg("MSG_TASKS_LAST_NEVER")
        if not t.get("enabled") and t.get("last_status") in {"error", "timeout"}:
            state = _msg("MSG_TASK_STATE_FAILED")
        elif not t.get("enabled"):
            state = _msg("MSG_TASK_STATE_SUSPENDED")
        elif t.get("consecutive_failures"):
            state = _msg(
                "MSG_TASK_STATE_DEGRADED",
                count=t["consecutive_failures"],
            )
        else:
            state = _msg("MSG_TASK_STATE_ACTIVE")
        error_detail = ""
        if t.get("last_error"):
            error_detail = _msg(
                "MSG_TASK_LAST_ERROR", error=t["last_error"][:300])
        lines.append(_msg(
            "MSG_TASKS_LIST_ROW",
            tid=t.get("id", "?"),
            name=t.get("name", "?"),
            sched=t.get("schedule_human") or t.get("schedule", "?"),
            last=last,
            state=state,
            error_detail=error_detail,
            query=(t.get("query") or "").strip(),
        ))
    detail_md = "\n".join(lines)
    # final_message_hint (15/6/2026): l'elenco task è un'ENUMERAZIONE fedele
    # (id + cadenza + query completa), NON un riassunto tematico. Emettere il
    # hint fa sì che il runtime lo usi come risposta finale e SALTI
    # describe_entries (che altrimenti riassume e perde id/query). Stesso
    # pattern di get_persons per gli enrollati (§5, NIENTE describe).
    return {
        "ok": True,
        "count": len(enriched),
        "tasks": enriched,
        "summary": detail_md,
        "detail_md": detail_md,
        "final_message_hint": detail_md,
    }


def handle_delete_tasks(args: dict, *, actor: str,
                        owner_user_id: str = "", **_) -> dict:
    if not owner_user_id:
        return {"ok": False, "error": "logical owner unavailable"}
    tid = args.get("id")
    name = args.get("name")
    if tid is None and not name:
        return {"ok": False, "error": "missing: serve 'id' (preferito) o 'name'"}
    # Resolve first, retaining the mapping until authoritative scheduler
    # state and history have been removed successfully.
    row = None
    conn = _open()
    try:
        if tid is not None:
            try:
                row = conn.execute(
                    "SELECT id,name,scheduler_name FROM recurring_tasks "
                    "WHERE id=? AND owner_user_id=?",
                    (int(tid), owner_user_id),
                ).fetchone()
            except (TypeError, ValueError):
                row = None
        if row is None and name:
            row = conn.execute(
                "SELECT id,name,scheduler_name FROM recurring_tasks "
                "WHERE owner_user_id=? AND name=?",
                (owner_user_id, name),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        ref = tid if tid is not None else name
        return {"ok": False, "error": f"task ref='{ref}' non trovato per actor={actor}"}
    target_name = str(row["name"])
    target_scheduler_name = str(row["scheduler_name"])
    try:
        from scheduler_v2 import client as sched_client
        sched_client.purge_jobs((target_scheduler_name,))
    except Exception as exc:
        return {"ok": False, "error": f"scheduler purge failed: {exc}"}
    ok = cancel_user_task(row["id"], owner_user_id=owner_user_id)
    if not ok:
        return {"ok": False, "error": "task registry cleanup failed"}
    return {"ok": True, "message": f"Task '{target_name}' cancellato."}


def _normalize_task_name(name: str, *, owner_user_id: str = "") -> str:
    """Prefix user tasks while preserving every live system task name.

    Builtin names come just-in-time from the scheduler's canonical specs;
    keeping a local allowlist made newly added jobs unreachable by name.
    """
    from scheduler_v2.builtin_callbacks import builtin_job_names

    if name in builtin_job_names():
        return name
    if name.startswith("user_"):
        return name
    if not owner_user_id:
        return name
    conn = _open()
    try:
        row = conn.execute(
            "SELECT scheduler_name FROM recurring_tasks "
            "WHERE owner_user_id=? AND name=?",
            (owner_user_id, name),
        ).fetchone()
        return str(row["scheduler_name"]) if row else name
    finally:
        conn.close()


def _principal_is_host(*, actor: str, owner_user_id: str) -> bool:
    owner = str(owner_user_id or "").strip()
    if not owner:
        return False
    try:
        import users
        principal = users.get_user(owner)
    except Exception:
        return False
    return bool(
        principal
        and str(principal.get("id") or "") == owner
        and principal.get("role") == "host"
    )


def _task_access_scope(*, actor: str, owner_user_id: str
                       ) -> tuple[dict[str, dict], set[str]]:
    """Return owner task map and system jobs visible to this principal.

    User jobs are never made visible through their globally unique scheduler
    key alone.  Hosts may inspect canonical system jobs, but even a host does
    not inherit another user's task history.
    """

    owner = str(owner_user_id or "").strip()
    if not owner:
        return {}, set()
    owned = {
        str(row["scheduler_name"]): row
        for row in list_user_tasks(owner_user_id=owner)
    }
    if not _principal_is_host(actor=actor, owner_user_id=owner):
        return owned, set()
    from scheduler_v2.builtin_callbacks import builtin_job_names
    return owned, set(builtin_job_names())


def _resolve_visible_task_name(name: str, *, actor: str,
                               owner_user_id: str) -> tuple[str, dict | None]:
    """Resolve a logical reference inside the caller's visibility boundary."""

    requested = str(name or "").strip()
    owned, system_names = _task_access_scope(
        actor=actor, owner_user_id=owner_user_id)
    if requested in owned:
        return requested, owned[requested]
    for scheduler_name, row in owned.items():
        if requested == str(row.get("name") or ""):
            return scheduler_name, row
    if requested in system_names:
        return requested, None
    return "", None


def handle_read_tasks(args: dict, *, actor: str,
                      owner_user_id: str = "", **_) -> dict:
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "missing required: name"}
    full_name, user_record = _resolve_visible_task_name(
        name, actor=actor, owner_user_id=owner_user_id)
    if not full_name:
        return {"ok": False, "error": "task non disponibile per questo utente"}
    try:
        from scheduler_v2 import client as sched_client
        rows = [r for r in sched_client.list_jobs() if r["name"] == full_name]
    except Exception as e:
        return {"ok": False, "error": f"scheduler unreachable: {e}"}
    if not rows:
        return {"ok": False, "error": f"task '{full_name}' non trovato"}
    sched_row = rows[0]
    detail = {"task": sched_row}
    if user_record is not None:
        detail["user_record"] = user_record
    return {"ok": True, **detail}


def handle_set_tasks(args: dict, *, actor: str,
                     owner_user_id: str = "", **_) -> dict:
    """Cambia stato di un task ricorrente. Dispatch interno fra:
    (a) enabled=bool → toggle abilitazione (host only);
    (b) fire_now=true → esecuzione immediata (host only, ex
        run_scheduled_task_now accorpato 15/5/2026).
    Mutex: esattamente uno dei due deve essere specificato."""
    if not _principal_is_host(actor=actor, owner_user_id=owner_user_id):
        return {"ok": False, "error": "solo HOST puo' modificare task (admin)"}
    name = args.get("name")
    enabled = args.get("enabled")
    fire_now = args.get("fire_now")
    if not name:
        return {"ok": False, "error": "missing required: name"}
    n_ops = (enabled is not None) + bool(fire_now)
    if n_ops == 0:
        return {"ok": False, "error": "specifica 'enabled' (abilita/disabilita) o 'fire_now=true' (esegui subito)"}
    if n_ops > 1:
        return {"ok": False, "error": "enabled e fire_now sono mutex"}
    full_name, user_record = _resolve_visible_task_name(
        name, actor=actor, owner_user_id=owner_user_id)
    if not full_name:
        return {"ok": False, "error": "task non disponibile per questo utente"}
    if enabled is not None:
        try:
            from scheduler_v2 import client as sched_client
            if user_record is not None:
                ok = set_user_scheduler_enabled(
                    full_name, bool(enabled),
                    owner_user_id=owner_user_id)
            else:
                ok = sched_client.toggle_job(full_name, bool(enabled))
            if not ok:
                return {"ok": False, "error": f"task '{full_name}' non trovato"}
        except Exception as e:
            return {"ok": False, "error": f"toggle failed: {e}"}
        return {"ok": True, "message": f"Task '{full_name}' "
                f"{'abilitato' if enabled else 'disabilitato'}."}
    # fire_now=true
    try:
        from scheduler_v2 import client as sched_client
        out = sched_client.run_now(full_name)
        if not out.get("ok"):
            return {"ok": False, "error": out.get("error") or "run_now failed"}
    except Exception as e:
        return {"ok": False, "error": f"fire failed: {e}"}
    return {"ok": True, "status": "scheduled",
            "message": f"Task '{full_name}' next_fire_at avanzato a now; "
                       f"il daemon lo eseguira' al prossimo tick. Vedi history."}


def handle_read_tasks_history(args: dict, *, actor: str,
                              owner_user_id: str = "", **_) -> dict:
    name = args.get("name")
    # Default 200 (vs 10 storico): l'utente che chiede "storico ultimi 7
    # giorni" si aspetta vedere TUTTO; 10 fa troppi truncation prompts.
    try:
        limit = int(args.get("limit") or 200)
    except (TypeError, ValueError):
        return {"ok": False, "error": "limit must be an integer",
                "error_class": "invalid_args", "history": []}
    if limit < 1 or limit > 1000:
        return {"ok": False, "error": "limit must be between 1 and 1000",
                "error_class": "invalid_args", "history": []}
    owned, system_names = _task_access_scope(
        actor=actor, owner_user_id=owner_user_id)
    if name:
        full_name, _ = _resolve_visible_task_name(
            name, actor=actor, owner_user_id=owner_user_id)
        if not full_name:
            return {"ok": False, "error": "task non disponibile per questo utente"}
        visible_names = (full_name,)
    else:
        visible_names = tuple(sorted(set(owned) | system_names))
    time_window = args.get("time_window")
    started_from = started_to = None
    if time_window:
        try:
            from datetime import datetime, timezone
            from time_window_parser import parse_time_window
            start_iso, end_iso = parse_time_window(time_window)
            started_from = datetime.fromisoformat(start_iso).astimezone(
                timezone.utc).isoformat(timespec="seconds")
            started_to = datetime.fromisoformat(end_iso).astimezone(
                timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc),
                    "error_class": "invalid_args", "history": []}
    try:
        from scheduler_v2 import client as sched_client
        # Never request global history: even hosts see system jobs plus their
        # own tasks, not payloads/results belonging to other users.
        rows = []
        for visible_name in visible_names:
            rows.extend(sched_client.history(
                name=visible_name, limit=limit + 1,
                started_from=started_from, started_to=started_to))
        rows.sort(key=lambda row: str(row.get("started_at") or ""),
                  reverse=True)
    except Exception as e:
        return {"ok": False, "error": f"history fetch failed: {e}"}
    truncated = len(rows) > limit
    rows = rows[:limit]
    # Aggregati per il final_message_hint (auto_final-friendly).
    by_status: dict[str, int] = {}
    by_task: dict[str, dict] = {}
    for r in rows:
        st = (r.get("status") or "other").lower()
        by_status[st] = by_status.get(st, 0) + 1
        tn = r.get("entry_name") or r.get("name") or "?"
        if tn in owned:
            tn = owned[tn]["name"]
        d = by_task.setdefault(tn, {"total": 0, "ok": 0, "error": 0})
        d["total"] += 1
        if st == "success" or st == "ok":
            d["ok"] += 1
        elif st in ("error", "fail", "failure", "timeout"):
            d["error"] += 1
    # Hint user-facing: il auto_final / final_answer puo' usarlo.
    # Build summary line + detail_md markdown multi-line
    if not rows:
        win_label = (f"per time_window={time_window}" if time_window
                     else "trovata")
        hint = f"Nessuna esecuzione di task {win_label}."
        md_block = hint
    else:
        win_str = f" ({time_window})" if time_window else ""
        # 1-line summary (compatto)
        status_str = ", ".join(
            f"{n} {st}" for st, n
            in sorted(by_status.items(), key=lambda p: -p[1])
        )
        hint = (
            f"{len(rows)} esecuzioni totali{win_str}. "
            f"Esiti: {status_str}."
        )
        # Markdown detail (multi-line, usato come final pulito quando ok)
        md_lines = [
            f"**Storico esecuzioni task**{win_str}",
            "",
            f"- **Totale**: {len(rows)} esecuzioni",
            f"- **Esiti**: {status_str}",
            "",
            "**Per task** (ordinati per totale):",
        ]
        for name, d in sorted(by_task.items(),
                                key=lambda p: -p[1]["total"])[:15]:
            err = f", {d['error']} errori" if d["error"] else ""
            md_lines.append(
                f"- `{name}`: {d['total']} fire ({d['ok']} ok{err})"
            )
        n_tasks = len(by_task)
        if n_tasks > 15:
            md_lines.append(f"- _... e altri {n_tasks - 15} task._")
        md_block = "\n".join(md_lines)
    return {
        "ok": True, "count": len(rows), "history": rows,
        "used": len(rows), "cap_value": limit, "truncated": truncated,
        "time_window": time_window,
        "by_status": by_status, "by_task": by_task,
        "summary": hint,
        "final_message_hint": hint,
        "detail_md": md_block,
    }


# run_scheduled_task_now: accorpato in handle_set_tasks (fire_now=true)
# per coerenza vocab §2.2 (no verb `execute`).
