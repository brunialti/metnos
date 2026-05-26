# SPDX-License-Identifier: AGPL-3.0-only
"""multi_tool_paths.py — Layer L2 multi-tool fast-path memoization.

ADR 0150 (19/5/2026 v4). Memoizza sequenze multi-step ricorrenti pre-PLANNER:
quando una canonical_query ha gia' generato una pipeline (`get_urls →
describe_entries`) per uses >= K_path, riesegue la sequenza deterministicamente
SENZA chiamare il PLANNER LLM.

Spec user 19/5/2026 v4:
- TTL = N giorni di **attivita' effettiva** (default 30). Non wall-clock.
  Tabella `system_active_days(date PK, n_turns, day_rank)` traccia il
  counter monotono dei giorni in cui l'utente ha effettivamente usato il
  sistema (>= 1 turno). Una entry scade quando
  `current_active_day - last_used_active_day > N`.
  Razionale: se l'utente sta in ferie per 60 giorni, le entry NON scadono:
  servono di nuovo al ritorno. Wall-clock TTL le butterebbe via.
- `uses >= 3` per promozione (override default ADR 0150 K_path=5).
- BGE-M3 cosine match come canonical_matcher (riusa stesso embedder).
- Threshold 0.93 conservativa (single-tool matcher: 0.95; multi-tool ha piu'
  varianti lessicali della stessa intent).

Architettura:

  query → normalize → BGE encode → cosine vs multi_tool_paths.canonical_query
                                    (uses >= MIN_USES,
                                     last_used_active_day >= current - TTL,
                                     state IN (candidate, active, shadow))
                                    top-1 cosine >= THRESHOLD → playback plan
  miss → caller cade al canonical_matcher single-tool poi PLANNER.

Determinismo §7.9: niente LLM nel matcher; solo ONNX encoder + cosine + sqlite.

V1 (questo file):
- Storage + lookup (try_match) DONE.
- Recording (record_path) DONE: chiamato da TurnLog.write per turn multi-step.
- Playback engine (playback_plan_for): ritorna piano dichiarativo che caller
  esegue. Caller usa invoke_executor del runtime.
- TTL active-day enforcement: filter in load_entries + expire_stale() cleanup.

V2 futuro:
- Args placeholder resolver piu' ricco (regex + LLM fallback opt-in).
- Path_shape verification con consumer_precursor check del loader.
- Shadow mode per K turni post-promozione (gating concordance > 95%).
"""
from __future__ import annotations

# ╔════════════════════════════════════════════════════════════════════╗
# ║ REMOVED-PRAXIS-FINAL (25/5/2026 sera tardissima)                    ║
# ║ Subsumed da praxis.sqlite (ADR 0161). Pentade convergente.          ║
# ║ Removal fisica pianificata post 24h monitor live use.               ║
# ╚════════════════════════════════════════════════════════════════════╝
import hashlib
import json
import logging
import os
import re as _re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)

import config as _C  # noqa: E402 — ADR 0148 rename-resilient

# ---------------------------------------------------------------------------
# Constants (overridable via env per bench/test)
# ---------------------------------------------------------------------------

# TTL in giorni di attivita' effettiva (active-day). Default 30 (user 19/5 v4).
# Fase 12 v5: env override mantenuto come fallback statico, ma il valore di
# runtime e' letto da runtime_settings (toml + env + default).
DEFAULT_TTL_ACTIVE_DAYS = int(os.environ.get("METNOS_MTP_TTL_ACTIVE_DAYS", "30"))
DEFAULT_MIN_USES = int(os.environ.get("METNOS_MTP_MIN_USES", "3"))
DEFAULT_THRESHOLD = float(os.environ.get("METNOS_MTP_THRESHOLD", "0.88"))


def _current_threshold() -> float:
    try:
        from runtime_settings import multi_tool_fast_path_threshold
        return multi_tool_fast_path_threshold()
    except Exception:
        return DEFAULT_THRESHOLD


def _current_min_uses() -> int:
    try:
        from runtime_settings import multi_tool_fast_path_min_uses
        return multi_tool_fast_path_min_uses()
    except Exception:
        return DEFAULT_MIN_USES


def _current_ttl_active_days() -> int:
    try:
        from runtime_settings import multi_tool_fast_path_ttl_active_days
        return multi_tool_fast_path_ttl_active_days()
    except Exception:
        return DEFAULT_TTL_ACTIVE_DAYS

# Stati che il matcher considera attivi.
_ACTIVE_STATES = ("candidate", "active", "shadow")

# Placeholder pattern → regex per playback args extraction.
# Conservativo (§7.9 robustezza confine NL→determinismo): nessun LLM,
# solo regex chiusa.

_URL_RE = _re.compile(r"https?://\S+")
_PATH_RE = _re.compile(r"(?:^|\s)((?:~|\.{1,2})?/(?:[\w.\-]+/?)+|~/[\w.\-/]*)")
_INT_RE = _re.compile(r"(?:^|\s)(\d+)(?:\s|$|[^\w.])")
_EMAIL_RE = _re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


# ---------------------------------------------------------------------------
# Schema sqlite
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS multi_tool_paths (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_query       TEXT    NOT NULL,
  tools_sequence        TEXT    NOT NULL,  -- JSON array di tool names
  args_shape            TEXT    NOT NULL,  -- JSON array di dict args templates
  path_shape_hash       TEXT    NOT NULL,  -- sha256(tools+shape)[:16]
  uses                  INTEGER NOT NULL DEFAULT 1,
  ok_count              INTEGER NOT NULL DEFAULT 0,
  fail_count            INTEGER NOT NULL DEFAULT 0,
  ts_first              TEXT    NOT NULL,
  ts_last               TEXT    NOT NULL,
  last_used_active_day  INTEGER NOT NULL,  -- day_rank di system_active_days
  state                 TEXT    NOT NULL DEFAULT 'candidate',
                                            -- candidate|shadow|active|demoted
  UNIQUE(canonical_query, path_shape_hash)
);
CREATE INDEX IF NOT EXISTS idx_mtp_canonical    ON multi_tool_paths(canonical_query);
CREATE INDEX IF NOT EXISTS idx_mtp_uses         ON multi_tool_paths(uses DESC);
CREATE INDEX IF NOT EXISTS idx_mtp_active_day   ON multi_tool_paths(last_used_active_day DESC);
CREATE INDEX IF NOT EXISTS idx_mtp_state        ON multi_tool_paths(state);

CREATE TABLE IF NOT EXISTS system_active_days (
  date      TEXT PRIMARY KEY,   -- YYYY-MM-DD UTC
  n_turns   INTEGER NOT NULL DEFAULT 1,
  day_rank  INTEGER NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_sad_rank ON system_active_days(day_rank DESC);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_FIND_EXECUTORS_WITH_COUNT_METADATA = frozenset({
    "find_files", "find_dirs", "find_messages", "find_events", "find_urls",
    "find_persons", "find_persons_indices", "find_contacts",
    "find_images_indices", "find_signatures", "get_processes",
})


def _is_count_antipattern(tools_sequence: list[str],
                          args_shape: list[dict]) -> bool:
    """True se la sequenza include `<find executor> → compute_entries(op=count
    senza key)`. Anti-pattern: il count e' gia' nei metadata del find_*.

    Esamina coppie consecutive (find, compute_entries). Non chiediamo che
    siano gli unici step: la sequenza puo' avere altri step dopo (final_answer,
    sort, ecc.), ma se trova questo binomio è anti-pattern.
    """
    if not tools_sequence or not args_shape:
        return False
    if len(tools_sequence) != len(args_shape):
        return False
    for i, tool in enumerate(tools_sequence[:-1]):
        if tool not in _FIND_EXECUTORS_WITH_COUNT_METADATA:
            continue
        if tools_sequence[i + 1] != "compute_entries":
            continue
        next_args = args_shape[i + 1] if isinstance(args_shape[i + 1], dict) else {}
        op = next_args.get("op")
        key = next_args.get("key")
        if op == "count" and not key:
            return True
    return False


def _path_shape_hash(tools: list[str], args_shape: list[dict]) -> str:
    payload = json.dumps(
        {"tools": tools, "shape": args_shape},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def derive_synth_name(tools: list[str]) -> str:
    """Heuristica naming sintetizzato: <verb_last>_<obj_first>.

    Stage 1 NAMING canonico (azione_oggetto) preserva la convenzione.
    Esempi:
      [get_urls, describe_entries]              → describe_urls
      [find_files, filter_files, move_files]    → move_files
      [read_messages, classify_entries]         → classify_messages

    Esposta come funzione modulo (non solo nel job di promozione) perche'
    serve anche al matcher L2 per il check di duplicazione: se l'executor
    sintetizzato con questo nome ESISTE gia' nel catalog, la entry L2
    viene demoted in modo che il PLANNER (o L1) usi il nuovo executor
    monolitico. Invariante: executor > fast-path.

    Pseudo-tool (`final_answer`) escluso dal calcolo: e' un terminatore
    di sintesi LLM, non un executor reinvocabile, e non deve influenzare
    il nome della capacita' unificata.
    """
    _PSEUDO = {"final_answer", "request_new_executor", "undo_last_turn"}
    real_tools = [t for t in (tools or []) if t not in _PSEUDO]
    if not real_tools:
        return "synthesized_pipeline"
    if len(real_tools) == 1:
        return real_tools[0]
    first = real_tools[0]
    last = real_tools[-1]
    first_parts = first.split("_", 1)
    last_parts = last.split("_", 1)
    if len(last_parts) == 2 and len(first_parts) == 2:
        verb = last_parts[0]
        obj = first_parts[1]
        return f"{verb}_{obj}"
    return f"{first}__then__{last}"


def derive_args_shape(query: str, raw_args_per_step: list[dict],
                       schemas_per_step: list[dict] | None = None
                       ) -> list[dict]:
    """Costruisce args_shape sostituendo valori variabili con placeholder typed.

    Per ogni step, ispeziona raw_args:
    - `from_step: <int>` → keep literal (link a observation precedente).
    - args che `args_extractor` saprebbe ri-derivare dal query
      (paths, urls, emails, glob, ints, time_window, date) → placeholder
      `<DYNAMIC>`. Al playback vengono ri-estratti dal query corrente
      via `args_extractor.regex_extract`. Garantisce che memoization
      generalizzi senza replicare valori query-specific (es. "today"
      memorizzato da "leggi mail oggi" non viene applicato a "ultime
      24 ore"). General + lang-independent: il set di "args ri-estraibili"
      e' definito UNA volta in args_extractor, scelta canonica del
      vocabolario.
    - altri (literal flag, lang code, scope id, default planner) → keep
      literal.

    Razionale §2.4: distinguere "valore concreto della query" (variabile fra
    chiamate, va sostituito) da "scelta del PLANNER" (lang, flag, default che
    e' parte del pattern). I primi vanno generalizzati con placeholder; i
    secondi memorizzati letterali.

    Args:
      schemas_per_step: opzionale, schema args dell'executor per ciascun
        step (parallelo a raw_args_per_step). Quando presente, abilita
        l'inference query-derived via args_extractor. Senza schema,
        fallback a placeholder per URL/PATH/EMAIL come prima (back-compat).
    """
    try:
        from args_extractor import regex_extract as _arg_re
    except Exception:
        _arg_re = None
    out = []
    qlow = query.lower() if isinstance(query, str) else ""
    urls = set(_URL_RE.findall(query or ""))
    emails = set(_EMAIL_RE.findall(query or ""))
    for idx, step_args in enumerate(raw_args_per_step):
        shape: dict = {}
        if not isinstance(step_args, dict):
            out.append({})
            continue
        # Set di args che `args_extractor` ri-deriverebbe dal query
        # corrente per questo step. Universale: se l'extractor lo
        # gestisce, e' query-dependent → memoizziamo come placeholder.
        _query_derived: set = set()
        if _arg_re is not None and schemas_per_step is not None:
            try:
                _sch = (schemas_per_step[idx]
                         if idx < len(schemas_per_step) else None)
                if _sch:
                    _query_derived = set(
                        _arg_re(query or "", _sch).keys()
                    )
            except Exception:
                pass
        for k, v in step_args.items():
            if k in _query_derived:
                # args_extractor ri-estraibile → placeholder con type hint
                # cosi' al playback ricostruiamo la stessa shape (array vs
                # singolo) anche senza accesso allo schema.
                _t = "array" if isinstance(v, list) else "string"
                shape[k] = f"<DYNAMIC:{_t}>"
                continue
            if k == "from_step" and isinstance(v, int):
                shape[k] = v
            elif isinstance(v, str):
                if v in urls:
                    shape[k] = "<URL>"
                elif v in emails:
                    shape[k] = "<EMAIL>"
                elif v.startswith("/") or v.startswith("~") or v.startswith("./"):
                    # path-ish
                    shape[k] = "<PATH>" if v.lower() in qlow else v
                else:
                    shape[k] = v  # literal (lang code, scope, dst_folder, ...)
            elif isinstance(v, list):
                # list of strings or list of ints → placeholderize element-wise
                norm_list = []
                for item in v:
                    if isinstance(item, str):
                        if item in urls:
                            norm_list.append("<URL>")
                        elif item in emails:
                            norm_list.append("<EMAIL>")
                        elif (item.startswith("/") or item.startswith("~")
                              or item.startswith("./")):
                            norm_list.append(
                                "<PATH>" if item.lower() in qlow else item
                            )
                        else:
                            norm_list.append(item)
                    else:
                        norm_list.append(item)
                shape[k] = norm_list
            elif isinstance(v, int):
                # int come max_total, top etc — variabile
                shape[k] = "<INT>"
            else:
                shape[k] = v
        out.append(shape)
    return out


def resolve_args_from_shape(shape: dict, query: str,
                             url_pool: list[str] | None = None,
                             email_pool: list[str] | None = None,
                             path_pool: list[str] | None = None,
                             int_pool: list[int] | None = None) -> Optional[dict]:
    """Risolve placeholders nello shape ai valori concreti estratti da `query`.

    `*_pool` permettono al chiamante di passare estrazioni pre-calcolate
    (esempio: in pipeline multi-step, il primo step consuma l'URL, il
    secondo step risolve da entries piping). Se None, estrae da query
    on-the-fly.

    Returns:
      dict args risolto, oppure None se un placeholder required non si
      risolve (caller fa fallback al PLANNER, no harm).
    """
    if url_pool is None:
        url_pool = list(_URL_RE.findall(query or ""))
    if email_pool is None:
        email_pool = list(_EMAIL_RE.findall(query or ""))
    if path_pool is None:
        path_pool = [m.group(1).strip() for m in _PATH_RE.finditer(query or "")]
        # Filtra path che sono in realta' URL
        path_pool = [p for p in path_pool if not any(p in u for u in url_pool)]
    if int_pool is None:
        int_pool = [int(m) for m in _INT_RE.findall(query or "")]

    resolved: dict = {}
    for k, v in shape.items():
        if isinstance(v, str):
            if v == "<URL>":
                if not url_pool:
                    return None
                resolved[k] = url_pool[0]
            elif v == "<EMAIL>":
                if not email_pool:
                    return None
                resolved[k] = email_pool[0]
            elif v == "<PATH>":
                if not path_pool:
                    return None
                resolved[k] = path_pool[0]
            elif v == "<INT>":
                if not int_pool:
                    return None
                resolved[k] = int_pool[0]
            elif isinstance(v, str) and v.startswith("<DYNAMIC"):
                # Ri-estrai dal query corrente via args_extractor.
                # Schema-driven: l'extractor sa quale tipo l'arg vuole
                # (URL/PATH/EMAIL/INT/TIME_WINDOW/DATE/GLOB) dal name.
                # Type hint nel placeholder (`<DYNAMIC:array>` o
                # `<DYNAMIC:string>`) ricostruisce shape originale.
                try:
                    from args_extractor import regex_extract as _are
                    _is_array = v == "<DYNAMIC:array>"
                    _spec = {"type": "array"} if _is_array else {}
                    _ext = _are(query, {"properties": {k: _spec}})
                    if k in _ext:
                        resolved[k] = _ext[k]
                    # Se non estraibile dal nuovo query: skip (executor
                    # usa il suo default).
                except Exception:
                    pass
            else:
                resolved[k] = v  # literal
        elif isinstance(v, list):
            out_list = []
            for item in v:
                if isinstance(item, str):
                    if item == "<URL>":
                        if not url_pool:
                            return None
                        out_list.append(url_pool[0])
                    elif item == "<EMAIL>":
                        if not email_pool:
                            return None
                        out_list.append(email_pool[0])
                    elif item == "<PATH>":
                        if not path_pool:
                            return None
                        out_list.append(path_pool[0])
                    elif item == "<INT>":
                        if not int_pool:
                            return None
                        out_list.append(int_pool[0])
                    else:
                        out_list.append(item)
                else:
                    out_list.append(item)
            resolved[k] = out_list
        else:
            resolved[k] = v
    return resolved


# ---------------------------------------------------------------------------
# DB singleton
# ---------------------------------------------------------------------------

class MultiToolPathsDB:
    """Singleton thread-safe per il fast-path multi-tool memoization."""

    _INSTANCE: Optional["MultiToolPathsDB"] = None
    _INSTANCE_LOCK = threading.Lock()

    @classmethod
    def get(cls) -> "MultiToolPathsDB":
        if cls._INSTANCE is not None:
            return cls._INSTANCE
        with cls._INSTANCE_LOCK:
            if cls._INSTANCE is None:
                cls._INSTANCE = cls()
        return cls._INSTANCE

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (solo per test)."""
        with cls._INSTANCE_LOCK:
            if cls._INSTANCE is not None:
                try:
                    cls._INSTANCE.conn.close()
                except Exception:
                    pass
            cls._INSTANCE = None

    def __init__(self, db_path: str | Path | None = None) -> None:
        env_path = os.environ.get("METNOS_MTP_DB")
        self.db_path = Path(db_path or env_path or _C.DB_MULTI_TOOL_PATHS)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        # Matcher cache (lazy)
        self._embedder = None  # BGEEmbeddingService o False
        self._entries: list[dict] = []
        self._vectors = None  # np.ndarray (N, D) L2-normalized
        self._entries_sig: str = ""

    # ─── active-day tracking ──────────────────────────────────────────────

    def record_active_day(self) -> int:
        """UPSERT today nella system_active_days. Idempotente: chiamabile
        N volte per turn senza creare righe duplicate. Increment n_turns.

        Returns:
          day_rank corrente (counter monotono dei giorni di attivita').
        """
        today = _today_utc()
        with self._lock, self.conn:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                "SELECT day_rank FROM system_active_days WHERE date = ?",
                (today,),
            ).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE system_active_days SET n_turns = n_turns + 1 "
                    "WHERE date = ?",
                    (today,),
                )
                return int(row["day_rank"])
            max_row = self.conn.execute(
                "SELECT MAX(day_rank) AS m FROM system_active_days",
            ).fetchone()
            new_rank = (max_row["m"] or 0) + 1
            self.conn.execute(
                "INSERT INTO system_active_days(date, n_turns, day_rank) "
                "VALUES (?, 1, ?)",
                (today, new_rank),
            )
            return new_rank

    def current_active_day(self) -> int:
        """day_rank di oggi. 0 se nessuna riga (mai inizializzato)."""
        row = self.conn.execute(
            "SELECT day_rank FROM system_active_days WHERE date = ?",
            (_today_utc(),),
        ).fetchone()
        return int(row["day_rank"]) if row else 0

    def max_active_day(self) -> int:
        """Max day_rank conosciuto (per test/expire quando non c'e' riga oggi)."""
        row = self.conn.execute(
            "SELECT MAX(day_rank) AS m FROM system_active_days",
        ).fetchone()
        return int(row["m"] or 0) if row else 0

    # ─── recording ────────────────────────────────────────────────────────

    def record_path(
        self,
        canonical: str,
        tools_sequence: list[str],
        args_shape: list[dict],
        *,
        ok: bool = True,
        available_tool_names: set | None = None,
    ) -> int:
        """UPSERT path observation. Idempotente per
        (canonical_query, path_shape_hash).

        - Se entry esiste: incrementa uses + aggiorna ts_last +
          last_used_active_day.
        - Altrimenti: insert candidate.

        Args:
          available_tool_names: set dei nomi executor nel catalog corrente.
            Se passato e l'executor sintetizzato che farebbe la stessa
            cosa (`derive_synth_name(tools)`) e' gia' presente → NON
            registra il fast-path. Invariante simmetrica al matcher:
            executor esistente > fast-path candidato. Se il fast-path
            non viene mai creato, non c'e' nulla da demoting al match.

        Returns:
          row id (>0). 0 se input invalido o se il fast-path equivalente
          a un executor gia' esistente (skip silenzioso).
        """
        if not canonical or not isinstance(canonical, str):
            return 0
        if not tools_sequence or len(tools_sequence) < 2:
            # MVP: solo sequenze multi-step (>= 2). Single-tool e' coperto
            # da canonical_matcher (ADR 0149).
            return 0
        # Anti-pattern (22/5/2026): `find_* + compute_entries(op=count, no key)`
        # è ridondante quando il source ha già aggregati nei metadata
        # (find_dirs.count_dirs/file_count_total, find_files.metadata.count/
        # available_total). Il LLM lo invoca per abitudine, ma:
        # - per find_dirs conta dirs invece di file → risposta sbagliata
        # - per find_files conta entries materializzate ignorando available_total
        # Vedi footer planner "COUNT DA METADATA UPSTREAM". Non memoizziamo
        # questo anti-pattern così il prossimo turno passa dal planner e
        # legge i metadata.
        if _is_count_antipattern(tools_sequence, args_shape):
            _LOG.info(
                "multi_tool_paths: skip record anti-pattern "
                "find_* → compute_entries(op=count) per '%s'", canonical[:60],
            )
            return 0
        # Strato 1 anti-loop feedback (E.3, 22/5/2026): se la pipeline e'
        # gia' in `rejected_pipelines_for_query(canonical)`, NON memorizzare.
        # Razionale: l'utente ha rifiutato esplicitamente questo path per
        # questa query; re-cacharlo via record_path automatico al termine
        # del turno produce il loop infinito ✗→↻→stessa pipeline→✗.
        try:
            from turn_feedback import rejected_pipelines_for_query
            for rej in rejected_pipelines_for_query(canonical):
                if list(rej) == list(tools_sequence):
                    _LOG.info(
                        "multi_tool_paths: skip record (pipeline rifiutata "
                        "dall'utente per query '%s'): %r",
                        canonical[:60], tools_sequence,
                    )
                    return 0
        except Exception as _ex:
            _LOG.debug("rejected_pipelines check failed: %r", _ex)
        # Regola simmetrica "executor > fast-path" (19/5 v5): se la
        # sintesi della pipeline corrisponderebbe a un executor gia' in
        # catalog, NON registrare. Niente da memoizzare se la capacita'
        # unificata esiste gia'.
        if available_tool_names is not None:
            synth_name = derive_synth_name(tools_sequence)
            if (synth_name in available_tool_names
                    and synth_name not in tools_sequence):
                _LOG.debug(
                    "multi_tool_paths: skip record (synth %r esiste in catalog)",
                    synth_name,
                )
                return 0
        cq = canonical.strip().lower()
        if not cq:
            return 0
        h = _path_shape_hash(tools_sequence, args_shape)
        tools_str = json.dumps(tools_sequence, ensure_ascii=False)
        shape_str = json.dumps(args_shape, sort_keys=True, ensure_ascii=False)
        now = _now_iso()
        active_day = self.record_active_day()
        with self._lock, self.conn:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                """SELECT id, uses FROM multi_tool_paths
                   WHERE canonical_query = ? AND path_shape_hash = ?""",
                (cq, h),
            ).fetchone()
            if row:
                self.conn.execute(
                    """UPDATE multi_tool_paths
                       SET uses = uses + 1,
                           ts_last = ?,
                           ok_count = ok_count + ?,
                           fail_count = fail_count + ?,
                           last_used_active_day = ?
                       WHERE id = ?""",
                    (now, 1 if ok else 0, 0 if ok else 1, active_day,
                     row["id"]),
                )
                return int(row["id"])
            cur = self.conn.execute(
                """INSERT INTO multi_tool_paths
                   (canonical_query, tools_sequence, args_shape, path_shape_hash,
                    uses, ok_count, fail_count, ts_first, ts_last,
                    last_used_active_day, state)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'candidate')""",
                (cq, tools_str, shape_str, h,
                 1 if ok else 0, 0 if ok else 1, now, now, active_day),
            )
            return int(cur.lastrowid or 0)

    # ─── expiration / cleanup ─────────────────────────────────────────────

    def expire_stale(self, *,
                      ttl_active_days: int | None = None) -> int:
        """DELETE entries con last_used_active_day piu' vecchio di N giorni
        di attivita' (corrente_active_day - last_used > N).

        Razionale: l'inattivita' del sistema (es. ferie utente) NON conta.
        Se il sistema e' rimasto idle 30 wall-days ma con 0 active-day,
        nessuna entry scade. Wall-clock TTL non protegge questo caso.

        Returns:
          numero di entry rimosse.
        """
        if ttl_active_days is None:
            ttl_active_days = _current_ttl_active_days()
        current = self.current_active_day()
        if current == 0:
            # Mai inizializzato: nessun riferimento, niente da scadere.
            current = self.max_active_day()
            if current == 0:
                return 0
        threshold = current - ttl_active_days
        if threshold <= 0:
            # Storia troppo breve per scadere alcunche'.
            return 0
        with self._lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM multi_tool_paths WHERE last_used_active_day < ?",
                (threshold,),
            )
            return int(cur.rowcount or 0)

    # ─── lookup ───────────────────────────────────────────────────────────

    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder if self._embedder is not False else None
        try:
            from bge_embedding import BGEEmbeddingService
            self._embedder = BGEEmbeddingService()
        except Exception as ex:
            _LOG.info("multi_tool_paths: BGE non disponibile (%r); "
                      "matcher disattivo", ex)
            self._embedder = False
            return None
        return self._embedder

    def _load_entries(self, min_uses: int,
                       ttl_active_days: int) -> list[dict]:
        current = self.current_active_day()
        if current == 0:
            current = self.max_active_day()
        threshold = max(0, current - ttl_active_days) if current > 0 else 0
        # Parameterized IN (?, ?, ...) — niente f-string in SQL (§7.3).
        _placeholders = ",".join("?" * len(_ACTIVE_STATES))
        _sql = (
            "SELECT id, canonical_query, tools_sequence, args_shape, "
            "uses, last_used_active_day, state "
            "FROM multi_tool_paths "
            "WHERE uses >= ? AND state IN (" + _placeholders + ") "
            "AND last_used_active_day >= ? "
            "ORDER BY uses DESC, id"
        )
        rows = self.conn.execute(
            _sql, (min_uses, *_ACTIVE_STATES, threshold),
        ).fetchall()
        out = []
        for r in rows:
            try:
                tools = json.loads(r["tools_sequence"])
                shapes = json.loads(r["args_shape"])
            except Exception:
                continue
            if not isinstance(tools, list) or not isinstance(shapes, list):
                continue
            out.append({
                "id": int(r["id"]),
                "canonical": r["canonical_query"],
                "tools": tools,
                "shapes": shapes,
                "uses": int(r["uses"]),
                "state": r["state"],
            })
        return out

    @staticmethod
    def _sig(entries: list[dict]) -> str:
        h = hashlib.sha256()
        for e in entries:
            h.update(f"{e['id']}:{e['uses']}\n".encode("utf-8"))
        return h.hexdigest()[:16]

    def _refresh_if_stale(self, min_uses: int, ttl_active_days: int) -> bool:
        entries = self._load_entries(min_uses, ttl_active_days)
        sig = self._sig(entries)
        if sig == self._entries_sig and self._vectors is not None:
            return len(self._entries) > 0
        if not entries:
            self._entries = []
            self._vectors = None
            self._entries_sig = sig
            return False
        emb = self._get_embedder()
        if emb is None or np is None:
            return False
        try:
            vectors = emb.embed_texts([e["canonical"] for e in entries])
            if not isinstance(vectors, np.ndarray):
                vectors = np.asarray(vectors, dtype=np.float32)
        except Exception as ex:
            _LOG.warning("multi_tool_paths: encode entries fallito: %r", ex)
            self._entries = []
            self._vectors = None
            self._entries_sig = sig
            return False
        self._entries = entries
        self._vectors = vectors
        self._entries_sig = sig
        return True

    def delete_entries_matching_query(
        self, query: str, *, cosine_threshold: float = 0.7,
    ) -> int:
        """Cancella tutte le entries cache (L2 multi_tool_paths) la cui
        canonical_query ha similarity BGE >= threshold con `query`. Usato
        post-feedback ✗ per assicurare che il prossimo retry non re-hitti
        via cosine match (E.2, 22/5/2026).

        Limitazione: copre SOLO L2. Per L1 (canonical_query_log) usare
        `delete_canonical_query_log_matching` (mnestoma).

        Ritorna n. entries cancellate. Logging info se >0.
        """
        if not query or not query.strip():
            return 0
        with self._lock:
            if not self._refresh_if_stale(0, 9999):
                return 0
            emb = self._get_embedder()
            if emb is None or np is None:
                return 0
            try:
                qv = emb.embed_query(query)
                if not isinstance(qv, np.ndarray):
                    qv = np.asarray(qv, dtype=np.float32)
            except Exception:
                return 0
            scores = self._vectors @ qv
            ids_to_delete = [
                self._entries[i]["id"]
                for i in range(len(self._entries))
                if scores[i] >= cosine_threshold
            ]
            if not ids_to_delete:
                return 0
        with self._lock, self.conn:
            self.conn.executemany(
                "DELETE FROM multi_tool_paths WHERE id = ?",
                [(i,) for i in ids_to_delete],
            )
        self._entries_sig = ""  # invalida cache
        _LOG.info(
            "multi_tool_paths: deleted %d entries matching %r "
            "(cosine >= %.2f)",
            len(ids_to_delete), query[:60], cosine_threshold,
        )
        return len(ids_to_delete)


    def try_match(self, query: str, *,
                   threshold: float | None = None,
                   min_uses: int | None = None,
                   ttl_active_days: int | None = None,
                   available_tool_names: set | None = None,
                   ) -> Optional[dict]:
        """Match query → playback plan via BGE cosine.

        Args:
          available_tool_names: set dei nomi executor presenti nel catalog
            corrente. Se passato, ogni entry L2 viene confrontata col
            nome derivato dalla sintesi (`derive_synth_name`); se quel
            nome compare nel catalog, la entry viene immediatamente
            demoted (state='demoted') e il match scarta a None.
            Invariante: l'executor sintetizzato ha sempre priorita' sul
            fast-path L2 (regola architetturale 19/5 v5).

        Returns:
          None se nessun match sopra soglia (caller fa fallback).
          dict {kind, id, canonical, tools, shapes, uses, cosine, plan_steps}
          se hit. `plan_steps` e' lista di {tool, resolved_args} pronti
          per `invoke_executor`.

        Se uno qualunque dei placeholders non si risolve dalla query →
        ritorna None (no harm: PLANNER prende il controllo).
        """
        if not query or not query.strip():
            return None
        # Default lazy via runtime_settings (toml + env + fallback).
        if threshold is None:
            threshold = _current_threshold()
        if min_uses is None:
            min_uses = _current_min_uses()
        if ttl_active_days is None:
            ttl_active_days = _current_ttl_active_days()
        with self._lock:
            if not self._refresh_if_stale(min_uses, ttl_active_days):
                return None
            emb = self._get_embedder()
            if emb is None or np is None:
                return None
            try:
                qv = emb.embed_query(query)
                if not isinstance(qv, np.ndarray):
                    qv = np.asarray(qv, dtype=np.float32)
            except Exception as ex:
                _LOG.warning("multi_tool_paths: encode query fallito: %r", ex)
                return None
            scores = self._vectors @ qv  # (N,)
            idx = int(np.argmax(scores))
            top = float(scores[idx])
            _LOG.debug(
                "multi_tool_paths.try_match query=%r best_cosine=%.3f "
                "threshold=%.3f canonical=%r min_uses=%d n_entries=%d",
                (query or "")[:60], top, threshold,
                self._entries[idx]["canonical"],
                min_uses, len(self._entries),
            )
            if top < threshold:
                return None
            entry = self._entries[idx]

        # Invariante "executor > fast-path" (19/5/2026 v5).
        # Se il catalog corrente contiene un executor con il nome derivato
        # dalla sintesi della pipeline, demota la entry L2: il PLANNER (o
        # L1 canonical_matcher) usera' direttamente l'executor sintetizzato.
        if available_tool_names is not None:
            synth_name = derive_synth_name(entry["tools"])
            _LOG.debug(
                "multi_tool_paths.try_match auto_demote_check synth=%r "
                "in_catalog=%s in_tools=%s",
                synth_name,
                synth_name in available_tool_names,
                synth_name in entry["tools"],
            )
            if synth_name in available_tool_names and synth_name not in entry["tools"]:
                # Demote: la entry diventa state='demoted' nel DB.
                # Idempotente: una entry gia' demoted non rientra in
                # _load_entries (filtro state IN active/candidate/shadow).
                # Costo cache: UNA full re-encode al prossimo match (l'entry
                # demoted sparisce dal load), poi cache stabile finche'
                # un'altra entry non viene demoted. Non e' "ogni call".
                try:
                    with self._lock, self.conn:
                        self.conn.execute(
                            "UPDATE multi_tool_paths SET state='demoted' "
                            "WHERE id = ?",
                            (entry["id"],),
                        )
                    # Invalida la cache per ricaricare al prossimo match.
                    self._entries_sig = ""
                    _LOG.info(
                        "multi_tool_paths: entry %d demoted "
                        "(synth executor %r esiste in catalog)",
                        entry["id"], synth_name,
                    )
                except Exception as ex:
                    _LOG.warning(
                        "multi_tool_paths: demote entry %d fallito: %r",
                        entry["id"], ex,
                    )
                return None

        # Pre-risolvi i placeholders per OGNI step. Se il primo step ha
        # placeholder che non si risolvono → None subito.
        plan_steps = []
        url_pool = list(_URL_RE.findall(query))
        email_pool = list(_EMAIL_RE.findall(query))
        path_pool = [m.group(1).strip() for m in _PATH_RE.finditer(query)]
        path_pool = [p for p in path_pool
                     if not any(p in u for u in url_pool)]
        int_pool = [int(m) for m in _INT_RE.findall(query)]
        for i, (tool, shape) in enumerate(
                zip(entry["tools"], entry["shapes"])):
            resolved = resolve_args_from_shape(
                shape, query,
                url_pool=url_pool, email_pool=email_pool,
                path_pool=path_pool, int_pool=int_pool,
            )
            if resolved is None:
                # Placeholder non risolto: probabilmente la query non porta
                # i valori richiesti dal pattern (es. pattern memoizza un
                # URL ma la nuova query non ha URL). Fallback PLANNER.
                _LOG.info(
                    "multi_tool_paths.try_match shape_unresolved step=%d "
                    "tool=%r shape=%r query=%r",
                    i, tool, shape, (query or "")[:60],
                )
                return None
            plan_steps.append({"tool": tool, "args": resolved})
        _LOG.info(
            "multi_tool_paths HIT cosine=%.3f tools=%r",
            top, entry["tools"],
        )
        return {
            "kind": "multi_tool_path",
            "id": entry["id"],
            "canonical": entry["canonical"],
            "tools": entry["tools"],
            "shapes": entry["shapes"],
            "uses": entry["uses"],
            "cosine": top,
            "plan_steps": plan_steps,
        }

    # ─── inspection ───────────────────────────────────────────────────────

    def stats(self) -> dict:
        c = self.conn
        out = {
            "total_paths": c.execute(
                "SELECT COUNT(*) FROM multi_tool_paths").fetchone()[0],
            "active_paths": c.execute(
                "SELECT COUNT(*) FROM multi_tool_paths WHERE state='active'"
            ).fetchone()[0],
            "candidate_paths": c.execute(
                "SELECT COUNT(*) FROM multi_tool_paths WHERE state='candidate'"
            ).fetchone()[0],
            "active_days_count": c.execute(
                "SELECT COUNT(*) FROM system_active_days").fetchone()[0],
            "current_active_day": self.current_active_day(),
        }
        return out

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers (mirror canonical_matcher style)
# ---------------------------------------------------------------------------

def try_multi_tool_match(query: str, *,
                          threshold: float | None = None,
                          min_uses: int | None = None,
                          ttl_active_days: int | None = None,
                          available_tool_names: set | None = None,
                          ) -> Optional[dict]:
    return MultiToolPathsDB.get().try_match(
        query,
        threshold=threshold,
        min_uses=min_uses,
        ttl_active_days=ttl_active_days,
        available_tool_names=available_tool_names,
    )


def record_path_observation(
    canonical_query: str,
    tools_sequence: list[str],
    args_shape: list[dict],
    *,
    ok: bool = True,
    available_tool_names: set | None = None,
) -> int:
    return MultiToolPathsDB.get().record_path(
        canonical_query, tools_sequence, args_shape, ok=ok,
        available_tool_names=available_tool_names,
    )


def expire_stale_paths(*, ttl_active_days: int | None = None) -> int:
    if ttl_active_days is None:
        ttl_active_days = _current_ttl_active_days()
    return MultiToolPathsDB.get().expire_stale(
        ttl_active_days=ttl_active_days,
    )
