#!/usr/bin/env python3
"""mnestoma.py — storage del mnestoma e operazioni sui mnest.

Implementa il microdesign mnest.html v1.1 + mnestoma.html v1.1.

Un mnest e' una traccia di co-attivazione: "l'output di A e' stato passato come
input a B nello stesso turno". Il mnestoma e' il grafo dei mnest, persistito in
un singolo file SQLite.

Un proto-mnest e' un mnest in stato 'proto': il dst non esiste come executor
(nome desiderato), e porta una desired_signature che descrive cosa l'executor
mancante dovrebbe fare.

Decisioni POC v1.1:
- ID via secrets.token_hex (non ULID) per evitare deps esterne.
- record_passing e' atomico (transazione SQLite).
- Decadimento applicato lazy in record_passing (dt dal ts_last) e on-demand
  da apply_ager(); non c'e' scheduler attivo nel POC.
- Path default: workspace/.mnestoma/mnest.sqlite (override via env
  MNESTOMA_DB_PATH per test).
"""
from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from logging_setup import get_logger
log = get_logger(__name__)

# Costanti dal microdesign mnest.html cap.6
REINFORCE_DELTA = 0.15
BOOTSTRAP_WEIGHT = 0.30
DECAY_LAMBDA_DEFAULT = 0.018  # /giorno -> dimezzamento ~38 giorni
PROTO_PURGE_THRESHOLD = 0.05
DECAY_THRESHOLD = 0.20
ARCHIVE_THRESHOLD = 0.05
ARCHIVE_AGE_DAYS = 90

# Soglie synth-trigger (cap.6 mnestoma + cap.13 synt domanda 3 default tentativo 5/30gg)
SYNTH_TRIGGER_USES = 3
SYNTH_TRIGGER_WEIGHT = 0.30

import config as _C  # noqa: E402  ADR 0148 rename-resilient
DEFAULT_DB_PATH = _C.DB_MNESTOMA

# Schema SQLite (cap.4 mnestoma.html)
SCHEMA = """
CREATE TABLE IF NOT EXISTS executors (
  name           TEXT NOT NULL,
  version        TEXT NOT NULL,
  state          TEXT NOT NULL,
  loaded_at      TEXT NOT NULL,
  manifest_hash  TEXT NOT NULL,
  PRIMARY KEY (name, version)
);
CREATE INDEX IF NOT EXISTS idx_executors_state ON executors(state);

CREATE TABLE IF NOT EXISTS mnests (
  id             TEXT PRIMARY KEY,
  src_executor   TEXT NOT NULL,
  src_version    TEXT NOT NULL,
  dst_executor   TEXT NOT NULL,
  dst_version    TEXT,
  weight         REAL NOT NULL CHECK (weight BETWEEN 0 AND 1),
  uses           INTEGER NOT NULL CHECK (uses >= 1),
  ts_first       TEXT NOT NULL,
  ts_last        TEXT NOT NULL,
  decay_lambda   REAL NOT NULL DEFAULT 0.018,
  state          TEXT NOT NULL,
  tags           TEXT,
  desired_sig    TEXT,
  CHECK (ts_last >= ts_first),
  UNIQUE (src_executor, src_version, dst_executor, dst_version, state)
);
CREATE INDEX IF NOT EXISTS idx_mnests_dst    ON mnests(dst_executor);
CREATE INDEX IF NOT EXISTS idx_mnests_src    ON mnests(src_executor);
CREATE INDEX IF NOT EXISTS idx_mnests_weight ON mnests(weight DESC);
CREATE INDEX IF NOT EXISTS idx_mnests_state  ON mnests(state);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  mnest_id    TEXT NOT NULL,
  ts          TEXT NOT NULL,
  kind        TEXT NOT NULL,
  delta       REAL,
  new_state   TEXT,
  reason      TEXT,
  turn_id     TEXT,                          -- 1/5/2026: colonna esplicita per
                                              -- join events↔turns. Prerequisito
                                              -- introvertiva generalize (cluster
                                              -- catene per turn_id). Prima viveva
                                              -- come stringa libera in `reason`
                                              -- per kind=reinforce.
  FOREIGN KEY (mnest_id) REFERENCES mnests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_mnest   ON events(mnest_id);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts);
-- idx_events_turn_id: creato dalla migration in __init__ (su DB pre-1/5/2026
-- la colonna turn_id non esiste ancora a executescript-time).

-- §7.9: idempotente race-safe (no DROP+CREATE: connection concurrent
-- collide su "view already exists"). Se lo schema della view cambia
-- in futuro, gestire migration esplicita una volta nel boot, non qui.
CREATE VIEW IF NOT EXISTS v_mnestoma AS
SELECT id, src_executor, src_version, dst_executor, dst_version,
       weight, uses, ts_last, state, tags, desired_sig
FROM mnests
WHERE state IN ('active', 'proto');

-- ADR 0149: canonical_query log (by-product di normalizzazione del PLANNER).
-- Per ogni turno planner che emette canonical_query non vuota, una entry
-- qui (UPSERT su (canonical, tool, args_shape)). Telemetria proiettata in
-- change_intents (adapter `canonical`, kind cache_pattern). NB 11/6/2026:
-- il matcher L1 che la consumava per il replay e' stato ritirato
-- (ridondante con engine/fastpath L0).
CREATE TABLE IF NOT EXISTS canonical_query_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_query TEXT    NOT NULL,
  tool_name       TEXT    NOT NULL,
  args_shape      TEXT    NOT NULL,  -- JSON template (placeholders)
  uses            INTEGER NOT NULL DEFAULT 1,
  ok_count        INTEGER NOT NULL DEFAULT 0,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  ts_first        TEXT    NOT NULL,
  ts_last         TEXT    NOT NULL,
  state           TEXT    NOT NULL DEFAULT 'candidate',
                                   -- candidate|shadow|active|demoted
  UNIQUE(canonical_query, tool_name, args_shape)
);
CREATE INDEX IF NOT EXISTS idx_cql_canonical
  ON canonical_query_log(canonical_query);
CREATE INDEX IF NOT EXISTS idx_cql_tool
  ON canonical_query_log(tool_name);
CREATE INDEX IF NOT EXISTS idx_cql_uses
  ON canonical_query_log(uses DESC);
"""


# --- Tipi ------------------------------------------------------------------

@dataclass
class DesiredSignature:
    summary: str
    inputs: list[str]
    outputs: list[str]
    errors: list[str] = field(default_factory=list)


@dataclass
class Mnest:
    id: str
    src_executor: str
    src_version: str
    dst_executor: str
    dst_version: str | None
    weight: float
    uses: int
    ts_first: str
    ts_last: str
    decay_lambda: float
    state: str  # proto | active | decaying | superseded
    tags: list[str] = field(default_factory=list)
    desired_sig: dict | None = None


# --- Helpers ---------------------------------------------------------------

def _id() -> str:
    return "mn_" + secrets.token_hex(12)


from timefmt import now_iso_z as _now_iso


def _parse_iso(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _days_between(ts_old: str, ts_new: str) -> float:
    return (_parse_iso(ts_new) - _parse_iso(ts_old)).total_seconds() / 86400.0


def _decay(w0: float, dt_days: float, lam: float) -> float:
    if dt_days <= 0:
        return w0
    return w0 * math.exp(-lam * dt_days)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _row_to_mnest(row: sqlite3.Row) -> Mnest:
    return Mnest(
        id=row["id"],
        src_executor=row["src_executor"],
        src_version=row["src_version"],
        dst_executor=row["dst_executor"],
        dst_version=row["dst_version"],
        weight=row["weight"],
        uses=row["uses"],
        ts_first=row["ts_first"],
        ts_last=row["ts_last"],
        decay_lambda=row["decay_lambda"],
        state=row["state"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        desired_sig=json.loads(row["desired_sig"]) if row["desired_sig"] else None,
    )


# --- Mnestoma --------------------------------------------------------------

class Mnestoma:
    """Storage e operazioni sui mnest. Thread-unsafe: una connessione per processo."""

    def __init__(self, db_path: str | Path | None = None):
        env_path = os.environ.get("MNESTOMA_DB_PATH")
        self.db_path = Path(db_path or env_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        # Migration idempotente: aggiungi colonna events.turn_id ai DB pre-1/5/2026.
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(events)").fetchall()}
        if "turn_id" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN turn_id TEXT")
        # Indice (idempotente: su DB nuovi creato qui per la prima volta;
        # su DB vecchi creato dopo ALTER TABLE).
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_turn_id ON events(turn_id) WHERE turn_id IS NOT NULL"
        )
        if "turn_id" not in cols:
            # Backfill best-effort: per kind=reinforce il vecchio codice usava
            # `reason` come slot turn_id (vedi ts<2026-05-01T17). Pattern:
            # reason e' una stringa esadecimale di 16 char (turn_id parziale)
            # o 32 char (turn_id pieno). Spostiamo in turn_id e azzeriamo
            # reason per i record che matchano.
            self.conn.execute(
                """UPDATE events
                   SET turn_id = reason, reason = NULL
                   WHERE turn_id IS NULL
                     AND kind = 'reinforce'
                     AND reason IS NOT NULL
                     AND length(reason) IN (16, 32)
                     AND reason GLOB '[0-9a-f]*'"""
            )
        # Migration idempotente: aggiungi colonna canonical_query_log.args_observed
        # (Fase 14 19/5/2026 v5, args_extractor V1.5). Memorizza i VALORI args
        # osservati al primo planner pass per memoization (no LLM call al
        # second pass). Separato da args_shape che e' il template placeholder.
        cql_cols = {
            r[1] for r in self.conn.execute(
                "PRAGMA table_info(canonical_query_log)"
            ).fetchall()
        }
        if "args_observed" not in cql_cols:
            self.conn.execute(
                "ALTER TABLE canonical_query_log ADD COLUMN args_observed TEXT"
            )

    # --- write -------------------------------------------------------------

    def record_canonical_query(
        self,
        canonical_query: str,
        tool_name: str,
        args_shape: str | dict,
        *,
        ok: bool = True,
        args_observed: dict | None = None,
    ) -> int:
        """ADR 0149 + 0150 v5: UPSERT canonical_query observation.

        Idempotente per chiave (canonical_query, tool_name, args_shape).
        Incrementa `uses` e aggiorna `ts_last` se esiste; insert candidate
        altrimenti. `ok` distingue success/fail count per gating promozione.

        Args:
          canonical_query: forma lemma emessa dal planner come by-product.
                            Vuoto/None → no-op (ritorna 0).
          tool_name: executor scelto al primo step della query corrente.
          args_shape: template JSON degli args (placeholders). Stringa o
                      dict (serializzato).
          ok: True se l'osservazione finale del turno e' kind=answer.
          args_observed: dict dei VALORI args reali osservati (Fase 14 v5
                          args_extractor V1.5). Persistito come JSON
                          nella colonna `args_observed` per memoization
                          al second pass del matcher.

        Returns:
          row id (>0). 0 se canonical_query vuota.
        """
        if not canonical_query or not isinstance(canonical_query, str):
            return 0
        if not tool_name or not isinstance(tool_name, str):
            return 0
        if isinstance(args_shape, dict):
            shape_str = json.dumps(args_shape, sort_keys=True, ensure_ascii=False)
        elif isinstance(args_shape, str):
            shape_str = args_shape
        else:
            shape_str = "{}"
        cq = canonical_query.strip().lower()
        if not cq:
            return 0
        # args_observed: serialize JSON solo se non-vuoto (NULL altrimenti per
        # risparmiare storage e permettere il check di esistenza).
        args_obs_str = None
        if isinstance(args_observed, dict) and args_observed:
            try:
                args_obs_str = json.dumps(
                    args_observed, sort_keys=True, ensure_ascii=False,
                )
            except (TypeError, ValueError):
                args_obs_str = None
        now = _now_iso()
        with self.conn:
            row = self.conn.execute(
                """SELECT id, uses FROM canonical_query_log
                   WHERE canonical_query = ?
                     AND tool_name = ?
                     AND args_shape = ?""",
                (cq, tool_name, shape_str),
            ).fetchone()
            if row:
                # UPDATE: aggiorna args_observed solo se passato non-null
                # (evita di sovrascrivere un valore appreso con NULL).
                if args_obs_str is not None:
                    self.conn.execute(
                        """UPDATE canonical_query_log
                           SET uses = uses + 1, ts_last = ?,
                               ok_count = ok_count + ?,
                               fail_count = fail_count + ?,
                               args_observed = ?
                           WHERE id = ?""",
                        (now, 1 if ok else 0, 0 if ok else 1,
                         args_obs_str, row["id"]),
                    )
                else:
                    self.conn.execute(
                        """UPDATE canonical_query_log
                           SET uses = uses + 1, ts_last = ?,
                               ok_count = ok_count + ?,
                               fail_count = fail_count + ?
                           WHERE id = ?""",
                        (now, 1 if ok else 0, 0 if ok else 1, row["id"]),
                    )
                return int(row["id"])
            cur = self.conn.execute(
                """INSERT INTO canonical_query_log
                   (canonical_query, tool_name, args_shape,
                    uses, ok_count, fail_count, ts_first, ts_last, state,
                    args_observed)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'candidate', ?)""",
                (cq, tool_name, shape_str,
                 1 if ok else 0, 0 if ok else 1, now, now, args_obs_str),
            )
            return int(cur.lastrowid or 0)

    def record_passing(
        self,
        src_executor: str,
        src_version: str,
        dst_executor: str,
        dst_version: str | None = None,
        *,
        dst_exists: bool = True,
        desired_signature: DesiredSignature | dict | None = None,
        tags: list[str] | None = None,
        decay_lambda: float = DECAY_LAMBDA_DEFAULT,
        turn_id: str | None = None,
    ) -> str:
        """Registra (o rinforza) un mnest. Atomico per coppia.

        Se dst_exists=False, registra un proto-mnest con desired_signature.
        Per i proto, dst_version e' forzato a NULL.

        Restituisce mnest_id.
        """
        if not dst_exists:
            return self._record_proto(
                src_executor, src_version, dst_executor,
                desired_signature=desired_signature, tags=tags,
                decay_lambda=decay_lambda, turn_id=turn_id,
            )

        now = _now_iso()
        tags_json = json.dumps(tags or [])
        with self.conn:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                """SELECT * FROM mnests
                   WHERE src_executor = ? AND src_version = ?
                     AND dst_executor = ? AND dst_version = ?
                     AND state = 'active'""",
                (src_executor, src_version, dst_executor, dst_version),
            ).fetchone()
            if row:
                dt_days = _days_between(row["ts_last"], now)
                w_decayed = _decay(row["weight"], dt_days, row["decay_lambda"])
                w_new = _clamp01(w_decayed + REINFORCE_DELTA)
                delta_eff = w_new - row["weight"]
                self.conn.execute(
                    """UPDATE mnests SET weight = ?, uses = uses + 1, ts_last = ?
                       WHERE id = ?""",
                    (w_new, now, row["id"]),
                )
                self.conn.execute(
                    """INSERT INTO events (mnest_id, ts, kind, delta, turn_id)
                       VALUES (?, ?, 'reinforce', ?, ?)""",
                    (row["id"], now, delta_eff, turn_id),
                )
                return row["id"]
            else:
                mid = _id()
                self.conn.execute(
                    """INSERT INTO mnests
                       (id, src_executor, src_version, dst_executor, dst_version,
                        weight, uses, ts_first, ts_last, decay_lambda, state, tags)
                       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'active', ?)""",
                    (mid, src_executor, src_version, dst_executor, dst_version,
                     BOOTSTRAP_WEIGHT, now, now, decay_lambda, tags_json),
                )
                self.conn.execute(
                    """INSERT INTO events (mnest_id, ts, kind, delta, turn_id)
                       VALUES (?, ?, 'reinforce', ?, ?)""",
                    (mid, now, BOOTSTRAP_WEIGHT, turn_id),
                )
                return mid

    def _record_proto(
        self,
        src_executor: str,
        src_version: str,
        desired_name: str,
        *,
        desired_signature: DesiredSignature | dict | None,
        tags: list[str] | None,
        decay_lambda: float,
        turn_id: str | None,
    ) -> str:
        """Registra o rinforza un proto-mnest. Chiave: (src, src_ver, desired_name, NULL)."""
        if isinstance(desired_signature, DesiredSignature):
            sig_dict = asdict(desired_signature)
        else:
            sig_dict = desired_signature or {}
        sig_json = json.dumps(sig_dict)
        tags_json = json.dumps(tags or [])
        now = _now_iso()
        with self.conn:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                """SELECT * FROM mnests
                   WHERE src_executor = ? AND src_version = ?
                     AND dst_executor = ? AND dst_version IS NULL
                     AND state = 'proto'""",
                (src_executor, src_version, desired_name),
            ).fetchone()
            if row:
                dt_days = _days_between(row["ts_last"], now)
                w_decayed = _decay(row["weight"], dt_days, row["decay_lambda"])
                w_new = _clamp01(w_decayed + REINFORCE_DELTA)
                delta_eff = w_new - row["weight"]
                self.conn.execute(
                    """UPDATE mnests SET weight = ?, uses = uses + 1, ts_last = ?,
                                          desired_sig = COALESCE(?, desired_sig)
                       WHERE id = ?""",
                    (w_new, now, sig_json if sig_dict else None, row["id"]),
                )
                self.conn.execute(
                    """INSERT INTO events (mnest_id, ts, kind, delta, turn_id)
                       VALUES (?, ?, 'reinforce', ?, ?)""",
                    (row["id"], now, delta_eff, turn_id),
                )
                return row["id"]
            else:
                mid = _id()
                self.conn.execute(
                    """INSERT INTO mnests
                       (id, src_executor, src_version, dst_executor, dst_version,
                        weight, uses, ts_first, ts_last, decay_lambda, state, tags, desired_sig)
                       VALUES (?, ?, ?, ?, NULL, ?, 1, ?, ?, ?, 'proto', ?, ?)""",
                    (mid, src_executor, src_version, desired_name,
                     BOOTSTRAP_WEIGHT, now, now, decay_lambda, tags_json, sig_json),
                )
                self.conn.execute(
                    """INSERT INTO events (mnest_id, ts, kind, delta, turn_id)
                       VALUES (?, ?, 'reinforce', ?, ?)""",
                    (mid, now, BOOTSTRAP_WEIGHT, turn_id),
                )
                return mid

    def delete_canonical_query_log_matching(
        self,
        query: str,
        *,
        cosine_threshold: float = 0.7,
    ) -> int:
        """Cancella entries `canonical_query_log` la cui canonical_query ha
        BGE similarity >= threshold con `query` (E.2 retry, 22/5/2026:
        il retry di un turno bocciato non deve riusare pattern appena
        rifiutati).

        Usa BGEEmbeddingService se disponibile. Se BGE non installato →
        fallback a EXACT match case-insensitive trimmed.
        Fix 11/6/2026 (latente): l'import `canonical_matcher._get_embedder`
        non e' mai esistito a livello modulo e `embed_documents` non e' un
        metodo del servizio → il ramo BGE cadeva SEMPRE nel fallback exact.
        Ora import diretto + `embed_texts` (L2-normalized → dot = cosine).

        Ritorna n. entries cancellate.
        """
        if not query or not query.strip():
            return 0
        rows = self.conn.execute(
            "SELECT id, canonical_query FROM canonical_query_log"
        ).fetchall()
        if not rows:
            return 0
        ids_to_delete: list[int] = []
        try:
            import numpy as np
            from bge_embedding import BGEEmbeddingService
            emb = BGEEmbeddingService()
        except Exception:
            emb = None
            np = None
        if emb is not None and np is not None:
            try:
                qv = np.asarray(emb.embed_query(query), dtype=np.float32)
                texts = [r["canonical_query"] or "" for r in rows]
                ev = emb.embed_texts(texts)
                if not isinstance(ev, np.ndarray):
                    ev = np.asarray(ev, dtype=np.float32)
                scores = ev @ qv
                for r, sc in zip(rows, scores):
                    if float(sc) >= cosine_threshold:
                        ids_to_delete.append(int(r["id"]))
            except Exception:
                emb = None
        if emb is None:
            # Fallback exact match.
            needle = query.strip().lower()
            for r in rows:
                if (r["canonical_query"] or "").strip().lower() == needle:
                    ids_to_delete.append(int(r["id"]))
        if not ids_to_delete:
            return 0
        self.conn.executemany(
            "DELETE FROM canonical_query_log WHERE id = ?",
            [(i,) for i in ids_to_delete],
        )
        self.conn.commit()
        return len(ids_to_delete)


    def transition_state(self, mnest_id: str, new_state: str, *, reason: str) -> None:
        """Cambia stato + traccia evento (cap.5 update di stato)."""
        if new_state not in ("proto", "active", "decaying", "superseded"):
            raise ValueError(f"stato sconosciuto: {new_state}")
        now = _now_iso()
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "UPDATE mnests SET state = ? WHERE id = ?", (new_state, mnest_id),
            )
            self.conn.execute(
                """INSERT INTO events (mnest_id, ts, kind, new_state, reason)
                   VALUES (?, ?, 'state_change', ?, ?)""",
                (mnest_id, now, new_state, reason),
            )

    def promote_proto_to_active(self, mnest_id: str, dst_version: str, *, reason: str) -> None:
        """Quando l'executor desiderato nasce, promuove proto -> active.
        Setta dst_version e azzera desired_sig."""
        now = _now_iso()
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """UPDATE mnests SET state = 'active', dst_version = ?, desired_sig = NULL
                   WHERE id = ? AND state = 'proto'""",
                (dst_version, mnest_id),
            )
            self.conn.execute(
                """INSERT INTO events (mnest_id, ts, kind, new_state, reason)
                   VALUES (?, ?, 'state_change', 'active', ?)""",
                (mnest_id, now, reason),
            )

    # --- read --------------------------------------------------------------

    def get(self, mnest_id: str) -> Mnest | None:
        row = self.conn.execute(
            "SELECT * FROM mnests WHERE id = ?", (mnest_id,),
        ).fetchone()
        return _row_to_mnest(row) if row else None

    def top_k_outgoing(self, executor: str, k: int = 10) -> list[Mnest]:
        rows = self.conn.execute(
            """SELECT * FROM mnests
               WHERE src_executor = ? AND state IN ('active', 'proto')
               ORDER BY weight DESC LIMIT ?""",
            (executor, k),
        ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def top_k_incoming(self, executor: str, k: int = 10) -> list[Mnest]:
        rows = self.conn.execute(
            """SELECT * FROM mnests
               WHERE dst_executor = ? AND state = 'active'
               ORDER BY weight DESC LIMIT ?""",
            (executor, k),
        ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def walk(
        self,
        start: str,
        max_depth: int = 3,
        *,
        state_filter: str | tuple[str, ...] = ("active",),
    ) -> list[list[Mnest]]:
        """BFS pesata dal nodo start; ritorna lista di percorsi (liste di mnest)
        ordinati per peso medio decrescente.

        Default: solo archi attivi (per la composizione di synt). Passare
        ('active', 'proto') per includere anche i desideri.
        """
        if max_depth < 1:
            return []
        if isinstance(state_filter, str):
            states = {state_filter}
        else:
            states = set(state_filter)
        paths: list[list[Mnest]] = []
        frontier = [(start, [])]
        for _ in range(max_depth):
            next_frontier = []
            for node, path in frontier:
                neighbors = self.top_k_outgoing(node, k=20)
                for m in neighbors:
                    if m.state not in states:
                        continue
                    new_path = path + [m]
                    paths.append(new_path)
                    if m.state == "active":
                        next_frontier.append((m.dst_executor, new_path))
            frontier = next_frontier
            if not frontier:
                break
        paths.sort(key=lambda p: sum(m.weight for m in p) / len(p), reverse=True)
        return paths

    def recurring_protos(
        self,
        min_uses: int = SYNTH_TRIGGER_USES,
        min_weight: float = SYNTH_TRIGGER_WEIGHT,
    ) -> list[Mnest]:
        rows = self.conn.execute(
            """SELECT * FROM mnests
               WHERE state = 'proto' AND uses >= ? AND weight >= ?
               ORDER BY weight DESC, uses DESC""",
            (min_uses, min_weight),
        ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def by_tag(self, tag: str) -> list[Mnest]:
        # SQLite JSON1 non sempre disponibile; filtro Python
        rows = self.conn.execute(
            "SELECT * FROM mnests WHERE state IN ('active', 'proto')",
        ).fetchall()
        out = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            if tag in tags:
                out.append(_row_to_mnest(r))
        return out

    def decaying(self) -> list[Mnest]:
        rows = self.conn.execute(
            """SELECT * FROM mnests
               WHERE state = 'decaying' ORDER BY weight ASC""",
        ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def all_active(self) -> list[Mnest]:
        rows = self.conn.execute(
            """SELECT * FROM mnests
               WHERE state IN ('active', 'proto')
               ORDER BY weight DESC""",
        ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def events_for(self, mnest_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE mnest_id = ? ORDER BY id ASC", (mnest_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- ager (cap.6 mnestoma.html) ---------------------------------------

    def apply_ager(self, *, now_iso: str | None = None) -> dict:
        """Esegue il giro notturno dell'ager. Idempotente per chiamata.

        Ritorna {decayed, demoted_to_decaying, proposed_archive, purged_protos,
                 recurring_protos, snapshot_path}.
        """
        now = now_iso or _now_iso()
        stats = {
            "decayed": 0, "demoted_to_decaying": 0, "proposed_archive": 0,
            "purged_protos": 0, "recurring_protos": 0,
        }
        # 1+2: decay + demote
        rows = self.conn.execute(
            "SELECT id, weight, ts_last, decay_lambda FROM mnests WHERE state = 'active'",
        ).fetchall()
        with self.conn:
            self.conn.execute("BEGIN")
            for r in rows:
                dt = _days_between(r["ts_last"], now)
                if dt <= 0:
                    continue
                w_new = _decay(r["weight"], dt, r["decay_lambda"])
                delta = w_new - r["weight"]
                self.conn.execute(
                    "UPDATE mnests SET weight = ?, ts_last = ? WHERE id = ?",
                    (w_new, now, r["id"]),
                )
                self.conn.execute(
                    """INSERT INTO events (mnest_id, ts, kind, delta, reason)
                       VALUES (?, ?, 'decay', ?, 'ager nightly')""",
                    (r["id"], now, delta),
                )
                stats["decayed"] += 1
                if w_new < DECAY_THRESHOLD:
                    self.conn.execute(
                        "UPDATE mnests SET state = 'decaying' WHERE id = ?", (r["id"],),
                    )
                    self.conn.execute(
                        """INSERT INTO events (mnest_id, ts, kind, new_state, reason)
                           VALUES (?, ?, 'state_change', 'decaying', 'ager nightly')""",
                        (r["id"], now),
                    )
                    stats["demoted_to_decaying"] += 1

        # 3: proposte di archiviazione
        rows = self.conn.execute(
            """SELECT id, ts_last, weight FROM mnests
               WHERE state = 'decaying' AND weight < ?""",
            (ARCHIVE_THRESHOLD,),
        ).fetchall()
        for r in rows:
            age = _days_between(r["ts_last"], now)
            if age >= ARCHIVE_AGE_DAYS:
                stats["proposed_archive"] += 1
        # (Roberto decide; nessuna scrittura qui — solo conteggio.)

        # 4: proto purge
        with self.conn:
            self.conn.execute("BEGIN")
            cur = self.conn.execute(
                """DELETE FROM mnests
                   WHERE state = 'proto' AND weight < ?""",
                (PROTO_PURGE_THRESHOLD,),
            )
            stats["purged_protos"] = cur.rowcount

        # 5: recurring proto detection (conteggio per logging)
        stats["recurring_protos"] = len(self.recurring_protos())

        return stats

    # --- analitica operativa ---------------------------------------------

    def top_active(self, limit: int = 20, *, state: str | tuple[str, ...] | None = "active") -> list[Mnest]:
        """Top-N mnest globali per peso, opzionalmente filtrati per state.

        state=None ritorna qualunque stato; default 'active' (mnest reali).
        Per i proto-mnest passare state='proto'.
        """
        if state is None:
            rows = self.conn.execute(
                "SELECT * FROM mnests ORDER BY weight DESC, uses DESC LIMIT ?",
                (limit,),
            ).fetchall()
        elif isinstance(state, str):
            rows = self.conn.execute(
                "SELECT * FROM mnests WHERE state = ? ORDER BY weight DESC, uses DESC LIMIT ?",
                (state, limit),
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(state))
            rows = self.conn.execute(
                f"SELECT * FROM mnests WHERE state IN ({placeholders}) "
                f"ORDER BY weight DESC, uses DESC LIMIT ?",
                (*state, limit),
            ).fetchall()
        return [_row_to_mnest(r) for r in rows]

    def executor_summary(self, executor: str) -> dict:
        """Aggregato per nome executor: archi in entrata/uscita, uses, peso medio."""
        c = self.conn
        out_row = c.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(uses),0) AS uses,
                      COALESCE(AVG(weight),0) AS avg_w
               FROM mnests WHERE src_executor = ? AND state = 'active'""",
            (executor,),
        ).fetchone()
        in_row = c.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(uses),0) AS uses,
                      COALESCE(AVG(weight),0) AS avg_w
               FROM mnests WHERE dst_executor = ? AND state = 'active'""",
            (executor,),
        ).fetchone()
        proto_in = c.execute(
            "SELECT COUNT(*) FROM mnests WHERE dst_executor = ? AND state = 'proto'",
            (executor,),
        ).fetchone()[0]
        return {
            "executor": executor,
            "outgoing": {"edges": out_row["n"], "uses": out_row["uses"], "avg_weight": out_row["avg_w"]},
            "incoming": {"edges": in_row["n"], "uses": in_row["uses"], "avg_weight": in_row["avg_w"]},
            "proto_incoming": proto_in,
        }

    def audit_recent(self, limit: int = 50) -> list[dict]:
        """Ultimi N event (cronologia) con join sui mnest per nomi leggibili."""
        rows = self.conn.execute(
            """SELECT e.id, e.ts, e.kind, e.delta, e.reason,
                      m.src_executor, m.dst_executor, m.state
               FROM events e
               LEFT JOIN mnests m ON m.id = e.mnest_id
               ORDER BY e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- inspection / debug -----------------------------------------------

    def stats(self) -> dict:
        c = self.conn
        out = {
            "total_mnests": c.execute("SELECT COUNT(*) FROM mnests").fetchone()[0],
            "active": c.execute("SELECT COUNT(*) FROM mnests WHERE state='active'").fetchone()[0],
            "proto": c.execute("SELECT COUNT(*) FROM mnests WHERE state='proto'").fetchone()[0],
            "decaying": c.execute("SELECT COUNT(*) FROM mnests WHERE state='decaying'").fetchone()[0],
            "superseded": c.execute("SELECT COUNT(*) FROM mnests WHERE state='superseded'").fetchone()[0],
            "events": c.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        }
        return out

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
# --- Helper per costruire desired_signature da contesto runtime -----------

def build_desired_signature(
    desired_name: str,
    args: dict | None,
    user_query: str | None = None,
) -> DesiredSignature:
    """Heuristica POC: costruisce desired_signature da nome desiderato + args."""
    summary = f"executor desiderato '{desired_name}'"
    if user_query:
        summary += f" (contesto turno: {user_query[:80]})"
    inputs = list((args or {}).keys()) or ["unknown"]
    return DesiredSignature(
        summary=summary, inputs=inputs, outputs=["unknown"], errors=[],
    )


# --- CLI ------------------------------------------------------------------

def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Mnestoma inspector")
    ap.add_argument("--db", default=None, help="path al file SQLite")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    sub.add_parser("ager")
    p_top = sub.add_parser("top-out"); p_top.add_argument("executor"); p_top.add_argument("--k", type=int, default=10)
    p_in = sub.add_parser("top-in"); p_in.add_argument("executor"); p_in.add_argument("--k", type=int, default=10)
    sub.add_parser("protos")
    p_walk = sub.add_parser("walk"); p_walk.add_argument("start"); p_walk.add_argument("--depth", type=int, default=3)
    p_top_g = sub.add_parser("top", help="top-N mnest globali per peso")
    p_top_g.add_argument("--limit", type=int, default=20)
    p_top_g.add_argument("--state", default="active", choices=["active", "proto", "all"])
    p_sum = sub.add_parser("summary", help="aggregato in/out per nome executor")
    p_sum.add_argument("executor")
    p_audit = sub.add_parser("audit", help="ultimi N event cronologici")
    p_audit.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    m = Mnestoma(args.db)
    if args.cmd == "stats":
        print(json.dumps(m.stats(), indent=2))
    elif args.cmd == "ager":
        print(json.dumps(m.apply_ager(), indent=2))
    elif args.cmd == "top-out":
        for x in m.top_k_outgoing(args.executor, k=args.k):
            print(f"  {x.src_executor}->{x.dst_executor} w={x.weight:.3f} uses={x.uses} state={x.state}")
    elif args.cmd == "top-in":
        for x in m.top_k_incoming(args.executor, k=args.k):
            print(f"  {x.src_executor}->{x.dst_executor} w={x.weight:.3f} uses={x.uses}")
    elif args.cmd == "protos":
        for x in m.recurring_protos():
            sig = x.desired_sig.get("summary", "") if x.desired_sig else ""
            print(f"  {x.src_executor}->{x.dst_executor} w={x.weight:.3f} uses={x.uses} sig={sig}")
    elif args.cmd == "walk":
        for path in m.walk(args.start, max_depth=args.depth)[:10]:
            chain = " -> ".join([path[0].src_executor] + [edge.dst_executor for edge in path])
            avg = sum(edge.weight for edge in path) / len(path)
            print(f"  avg_w={avg:.3f}  {chain}")
    elif args.cmd == "top":
        state = None if args.state == "all" else args.state
        for x in m.top_active(limit=args.limit, state=state):
            print(f"  {x.state:10s} {x.src_executor}->{x.dst_executor} w={x.weight:.3f} uses={x.uses}")
    elif args.cmd == "summary":
        print(json.dumps(m.executor_summary(args.executor), indent=2))
    elif args.cmd == "audit":
        for ev in m.audit_recent(limit=args.limit):
            edge = f"{ev.get('src_executor') or '?'}->{ev.get('dst_executor') or '?'}"
            print(f"  [{ev['ts']}] {ev['kind']:12s} delta={ev['delta']:+.3f}  {edge}")


if __name__ == "__main__":
    _cli()
