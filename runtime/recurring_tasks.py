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
  created_at TEXT,
  enabled INTEGER DEFAULT 1
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from pathlib import Path

from logging_setup import get_logger
log = get_logger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

import config as _C  # §7.11 — rispetta METNOS_USER_STATE
DB_PATH = _C.DB_RECURRING_TASKS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recurring_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
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
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_recurring_actor ON recurring_tasks(actor, channel);
"""

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


def _open() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    # Migration idempotente: aggiunta callback_key colonna per DB pre-1/5/2026 sera.
    cols = {r[1] for r in c.execute("PRAGMA table_info(recurring_tasks)").fetchall()}
    if "callback_key" not in cols:
        c.execute("ALTER TABLE recurring_tasks ADD COLUMN callback_key TEXT NOT NULL DEFAULT 'run_user_query'")
    # Migration: times + fired_count (1/5/2026 sera, supporto one-shot e
    # max-N-times). NULL/0 = forever.
    if "times" not in cols:
        c.execute("ALTER TABLE recurring_tasks ADD COLUMN times INTEGER")
    if "fired_count" not in cols:
        c.execute("ALTER TABLE recurring_tasks ADD COLUMN fired_count INTEGER NOT NULL DEFAULT 0")
    if "grace_window_minutes" not in cols:
        c.execute("ALTER TABLE recurring_tasks ADD COLUMN grace_window_minutes INTEGER")
    c.commit()
    return c


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


def register_user_task(
    *,
    label: str,
    when: str,
    query: str,
    actor: str,
    channel: str,
    chat_id: str | None = None,
    times: int | None = None,
    grace_window_minutes: int | None = None,
) -> dict:
    """Registra un task ricorrente user-defined. Restituisce il record.
    Idempotente: se name esiste gia', UPDATE.
    """
    schedule = _parse_when(when)
    name = _slugify(label or query)
    conn = _open()
    try:
        # Quota check (anti-runaway).
        n_existing = conn.execute(
            "SELECT COUNT(*) FROM recurring_tasks WHERE actor=? AND name!=?",
            (actor, name),
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
            "INSERT OR REPLACE INTO recurring_tasks "
            "(name, schedule, query, actor, channel, chat_id, label, "
            " times, fired_count, grace_window_minutes, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1)",
            (name, schedule, query, actor, channel, chat_id, label,
              times_val, gw),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM recurring_tasks WHERE name=?", (name,)
        ).fetchone())
    finally:
        conn.close()


def list_user_tasks(actor: str | None = None) -> list[dict]:
    conn = _open()
    try:
        if actor:
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


def cancel_user_task(name_or_id, *, actor: str | None = None) -> bool:
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
        else:
            sql = "DELETE FROM recurring_tasks WHERE name=?"
            params = (str(name_or_id),)
        if actor:
            sql += " AND actor=?"
            params = params + (actor,)
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# --- Bootstrap nel scheduler builtin --------------------------------------

def _increment_fired_and_check_done(name: str) -> tuple[int, bool]:
    """Atomico: increment fired_count, ritorna (new_count, done).
    done=True se fired_count >= times (e times non-NULL/0)."""
    conn = _open()
    try:
        conn.execute(
            "UPDATE recurring_tasks SET fired_count = fired_count + 1 WHERE name = ?",
            (name,),
        )
        row = conn.execute(
            "SELECT fired_count, times FROM recurring_tasks WHERE name = ?",
            (name,),
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


def _run_user_query_callback(record: dict) -> str:
    """Callback canonica `run_user_query`: rilancia run_turn + pusha canale.

    Registrata in `_CALLBACKS` come 'run_user_query' al boot. Refactor
    della funzione NON rompe i record DB (callback_key resta uguale).

    Robustezza:
    - Try/except attorno a run_turn (no propagazione exception).
    - Push canale con 1 retry su transient.
    - Output diagnostico salvato in scheduler.runs.output.
    """
    log_msg = []
    try:
        from agent_runtime import run_turn
        log = run_turn(
            record["query"],
            actor=record["actor"],
            channel=record["channel"],
        )
        msg = (log.final_message or "").strip()
        if not msg:
            return f"[{record['name']}] run_turn ok ma empty final_message (kind={getattr(log,'final_kind',None)})"
        log_msg.append(f"[{record['name']}] run_turn ok kind={getattr(log,'final_kind',None)} steps={len(log.steps or [])}")
    except Exception as e:
        return f"[{record['name']}] run_turn crashed: {type(e).__name__}: {e}"
    if record["channel"] == "telegram" and record.get("chat_id"):
        prefix = (
            f"[task: {record['label'] or record['name']}]\n"
            if record.get("label") else ""
        )
        for attempt in (1, 2):
            try:
                from channels.telegram import TelegramChannel
                from channels import OutboundMessage
                ch = TelegramChannel()
                resp = ch.send(record["chat_id"],
                                OutboundMessage(text=prefix + msg))
                if isinstance(resp, dict) and not resp.get("ok", True):
                    raise RuntimeError(resp.get("error") or "send returned ok:false")
                log_msg.append(f"pushed telegram chat={record['chat_id']} attempt={attempt}")
                return " | ".join(log_msg)
            except Exception as e:
                log_msg.append(f"push attempt {attempt} failed: {type(e).__name__}: {e}")
                if attempt == 2:
                    return " | ".join(log_msg)
                time.sleep(2)
    out = " | ".join(log_msg) + f" | no push channel: msg[:80]={msg[:80]}"
    return out


def _notify_circuit_break(entry, error) -> None:
    """Notifica l'owner che il suo task ricorrente e' stato auto-disabilitato
    dal circuit-breaker (N fallimenti consecutivi). Offre 3 scelte inline:
    Continua (riattiva) / Sospendi (resta off, ripristinabile) / Cancella
    (rimuove la schedulazione). callback_data = `sched:<azione>:<entry_name>`.

    Best-effort: nessuna eccezione propagata (il disable e' gia' persistito).
    Solo canale telegram con chat_id noto; altri canali → solo log.
    Testo user-facing via i18n DB (§11, builtin=multilang): chiavi
    MSG_SCHED_CIRCUIT_BREAK + MSG_BTN_SCHED_*."""
    from messages import get as _msg
    payload = getattr(entry, "payload", None) or {}
    channel = payload.get("channel")
    chat_id = payload.get("chat_id")
    label = payload.get("label") or payload.get("name") or getattr(entry, "name", "?")
    entry_name = getattr(entry, "name", "")
    try:
        from scheduler_v2.daemon import _CIRCUIT_BREAK_AFTER as _n
    except Exception:
        _n = 3
    if channel != "telegram" or not chat_id:
        log.warning(
            "circuit-break su task '%s' ma canale non notificabile "
            "(channel=%s chat_id=%s) — task disabilitato senza notifica",
            entry_name, channel, chat_id,
        )
        return
    err_line = (str(error)[:300]) if error else _msg("MSG_ERR_UNKNOWN")
    text = _msg("MSG_SCHED_CIRCUIT_BREAK", label=label, n=_n, error=err_line)
    buttons = [[
        {"text": _msg("MSG_BTN_SCHED_CONTINUE"), "data": f"sched:cont:{entry_name}"},
        {"text": _msg("MSG_BTN_SCHED_SUSPEND"), "data": f"sched:susp:{entry_name}"},
        {"text": _msg("MSG_BTN_SCHED_CANCEL"), "data": f"sched:canc:{entry_name}"},
    ]]
    try:
        from channels.telegram import TelegramChannel
        from channels import OutboundMessage
        ch = TelegramChannel()
        ch.send(chat_id, OutboundMessage(text=text, buttons=buttons))
        log.info("circuit-break notificato a chat=%s per task '%s'",
                 chat_id, entry_name)
    except Exception as e:
        log.warning("circuit-break notify failed for '%s': %s", entry_name, e)


def _wrap_with_times_tracking(fn):
    """Wrap callback con auto-increment fired_count + auto-cancel se done."""
    def _wrapped(record):
        out = fn(record)
        fc, done = _increment_fired_and_check_done(record["name"])
        if done:
            cancel_user_task(record["name"])
            try:
                from scheduler_v2 import client as sched_client
                sched_client.cancel_job(f"user_{record['name']}")
            except Exception as _e:  # silent swallow (auto-fixed)
                log.warning("silent exception in %s: %s", __name__, _e)
            return f"{out} | times reached ({fc}/{record.get('times')}) → auto-cancelled"
        return f"{out} | fired_count={fc}"
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
            actor=actor, channel=channel, chat_id=chat_id,
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
            name=f"user_{rec['name']}",
            trigger=rec["schedule"],
            callback_key=rec.get("callback_key") or "run_user_query",
            payload={
                "name": rec["name"],
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
        )
    except Exception as _e:
        log.warning("scheduler_v2 hot-register failed: %s", _e)
        # write to recurring_tasks.db is durable; daemon picks it up later.
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


def handle_list_tasks(args: dict, *, actor: str, **_) -> dict:
    tasks = list_user_tasks(actor=actor)
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
        sched_row = sched_state.get(f"user_{t['name']}", {})
        last_run = sched_row.get("last_run_at")
        last_status = sched_row.get("last_status")
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
        lines.append(_msg(
            "MSG_TASKS_LIST_ROW",
            tid=t.get("id", "?"),
            name=t.get("name", "?"),
            sched=t.get("schedule_human") or t.get("schedule", "?"),
            last=last,
        ))
    detail_md = "\n".join(lines)
    return {
        "ok": True,
        "count": len(enriched),
        "tasks": enriched,
        "summary": detail_md,
        "detail_md": detail_md,
    }


def handle_delete_tasks(args: dict, *, actor: str, **_) -> dict:
    tid = args.get("id")
    name = args.get("name")
    if tid is None and not name:
        return {"ok": False, "error": "missing: serve 'id' (preferito) o 'name'"}
    # Risolvi id → name slug per hot-unregister scheduler.
    target_name = name
    if tid is not None:
        conn = _open()
        try:
            row = conn.execute(
                "SELECT name FROM recurring_tasks WHERE id=? AND (?='' OR actor=?)",
                (int(tid), actor or "", actor or ""),
            ).fetchone()
            if row:
                target_name = row["name"]
        finally:
            conn.close()
    # Provo prima tid (preferito se valido), poi fallback su name se tid fail.
    # Bug live 15/5/2026: LLM emette {id=<inventato>, name=<corretto>} → tid
    # fallisce e l'handler non tentava il name. Fix: cascade tid → name.
    ok = False
    if tid is not None:
        ok = cancel_user_task(tid, actor=actor)
    if not ok and name:
        ok = cancel_user_task(name, actor=actor)
        if ok:
            target_name = name
    if not ok:
        ref = tid if tid is not None else name
        return {"ok": False, "error": f"task ref='{ref}' non trovato per actor={actor}"}
    if target_name:
        try:
            from scheduler_v2 import client as sched_client
            sched_client.cancel_job(f"user_{target_name}")
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    return {"ok": True, "message": f"Task '{target_name or tid or name}' cancellato."}


def _normalize_task_name(name: str) -> str:
    """Aggiunge prefisso user_ se manca per i recurring user task; lascia
    nudo per system task (apply_ager, synt_suggest)."""
    if name in ("apply_ager", "synt_suggest"):
        return name
    return name if name.startswith("user_") else f"user_{name}"


def handle_read_tasks(args: dict, *, actor: str, **_) -> dict:
    name = args.get("name")
    if not name:
        return {"ok": False, "error": "missing required: name"}
    full_name = _normalize_task_name(name)
    try:
        from scheduler_v2 import client as sched_client
        rows = [r for r in sched_client.list_jobs() if r["name"] == full_name]
    except Exception as e:
        return {"ok": False, "error": f"scheduler unreachable: {e}"}
    if not rows:
        return {"ok": False, "error": f"task '{full_name}' non trovato"}
    sched_row = rows[0]
    detail = {"task": sched_row}
    # Arricchisci con record user se applicabile + actor restrict
    if full_name.startswith("user_"):
        user_name = full_name[len("user_"):]
        urs = [u for u in list_user_tasks() if u["name"] == user_name]
        if urs:
            ur = urs[0]
            if actor != "host" and ur.get("actor") != actor:
                return {"ok": False, "error": "task non tuo (security)"}
            detail["user_record"] = ur
    return {"ok": True, **detail}


def handle_set_tasks(args: dict, *, actor: str, **_) -> dict:
    """Cambia stato di un task ricorrente. Dispatch interno fra:
    (a) enabled=bool → toggle abilitazione (host only);
    (b) fire_now=true → esecuzione immediata (host only, ex
        run_scheduled_task_now accorpato 15/5/2026).
    Mutex: esattamente uno dei due deve essere specificato."""
    if actor != "host":
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
    full_name = _normalize_task_name(name)
    if enabled is not None:
        try:
            from scheduler_v2 import client as sched_client
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


def handle_read_tasks_history(args: dict, *, actor: str, **_) -> dict:
    name = args.get("name")
    # Default 200 (vs 10 storico): l'utente che chiede "storico ultimi 7
    # giorni" si aspetta vedere TUTTO; 10 fa troppi truncation prompts.
    limit = int(args.get("limit") or 200)
    full_name = _normalize_task_name(name) if name else None
    time_window = args.get("time_window")
    try:
        from scheduler_v2 import client as sched_client
        rows = sched_client.history(name=full_name, limit=limit)
    except Exception as e:
        return {"ok": False, "error": f"history fetch failed: {e}"}
    # Actor restrict per task user
    if full_name and full_name.startswith("user_") and actor != "host":
        user_name = full_name[len("user_"):]
        urs = [u for u in list_user_tasks() if u["name"] == user_name]
        if urs and urs[0].get("actor") != actor:
            return {"ok": False, "error": "task non tuo (security)"}
    # Time window filter applicato post-fetch (scheduler v2 client non lo
    # supporta nativamente). Usa time_window_parser canonical §2.1.
    if time_window:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from time_window_parser import parse_time_window
            start_iso, end_iso = parse_time_window(time_window)
            from datetime import datetime
            start_ts = datetime.fromisoformat(start_iso).timestamp()
            end_ts = datetime.fromisoformat(end_iso).timestamp()
            filtered = []
            for r in rows:
                # row started_at puo' essere ISO o epoch
                started = r.get("started_at") or r.get("ts") or r.get("fired_at")
                if started is None:
                    continue
                try:
                    if isinstance(started, str):
                        ts = datetime.fromisoformat(
                            started.replace("Z", "+00:00")
                        ).timestamp()
                    else:
                        ts = float(started)
                except (ValueError, TypeError):
                    continue
                if start_ts <= ts <= end_ts:
                    filtered.append(r)
            rows = filtered
        except Exception as ex:
            log.warning("time_window parse failed: %r — ignored", ex)
    # Aggregati per il final_message_hint (auto_final-friendly).
    by_status: dict[str, int] = {}
    by_task: dict[str, dict] = {}
    for r in rows:
        st = (r.get("status") or "other").lower()
        by_status[st] = by_status.get(st, 0) + 1
        tn = r.get("entry_name") or r.get("name") or "?"
        if tn.startswith("user_"):
            tn = tn[len("user_"):]
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
        "time_window": time_window,
        "by_status": by_status, "by_task": by_task,
        "summary": hint,
        "final_message_hint": hint,
        "detail_md": md_block,
    }


# run_scheduled_task_now: accorpato in handle_set_tasks (fire_now=true)
# per coerenza vocab §2.2 (no verb `execute`).
