#!/usr/bin/env python3
"""change_intents — single source of truth per il ciclo di vita delle
proposte di cambiamento al sistema (ADR 0158).

Sostituisce 6 sorgenti frammentate (telos jsonl, introvertiva sqlite,
synt jsonl, multi_tool_paths, canonical_query_log, turn_feedback) e 7 state
machine separate. Un solo schema, un solo state machine, una sola UI.

Stati:
  proposed → accepted → applied → observed → finalized
                ↓          ↓        ↓
             rejected   failed   rolled_back
                ↓
             staged

Schema: vedi `_SCHEMA` sotto. Storage: sqlite in DB_CHANGE_INTENTS.

Helper canonici:
  - upsert_intent(ci)            inserisce o aggiorna by fingerprint
  - get_intent(id_)              ChangeIntent | None
  - list_intents(state=..., ...) elenco con filtri + sort + cap
  - apply_decision(id_, action,  registra accept/reject/stage
                   by, reason)
  - mark_applied(id_, effect)    transition accepted → applied
  - mark_observed(id_, metrics)  transition applied → observed
  - mark_finalized(id_)          transition observed → finalized
  - mark_rolled_back(id_, reason) transition any → rolled_back
  - mark_failed(id_, reason)     transition accepted → failed
  - count_by_state()             dict {state: int} per badge UI

NON contiene logica di adapter — quella vive in `change_intent_adapters/`.
NON contiene logica di applicazione — quella vive in `change_applier.py`.
NON contiene logica di osservazione — quella vive in `change_observer.py`.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import config as C


# --- Schema --------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_intents (
  id                 TEXT    PRIMARY KEY,
  fingerprint        TEXT    NOT NULL,
  state              TEXT    NOT NULL DEFAULT 'proposed',
  origin_family      TEXT    NOT NULL,
  origin_module      TEXT    NOT NULL,
  origin_source_id   TEXT,
  discovered_at      TEXT    NOT NULL,

  intent_kind        TEXT    NOT NULL,
  intent_target      TEXT    NOT NULL,
  intent_summary     TEXT    NOT NULL,
  intent_rationale   TEXT,
  intent_body        TEXT    NOT NULL,

  score              REAL    NOT NULL DEFAULT 0.0,
  confidence         REAL    NOT NULL DEFAULT 0.8,
  convergence        INTEGER NOT NULL DEFAULT 1,

  decision_by        TEXT,
  decision_ts        TEXT,
  decision_action    TEXT,
  decision_reason    TEXT,

  applied_at         TEXT,
  applied_effect     TEXT,

  observed_at        TEXT,
  observed_metrics   TEXT,

  finalized_at       TEXT,
  rolled_back_at     TEXT,
  rolled_back_reason TEXT,
  failed_at          TEXT,
  failed_reason      TEXT,

  updated_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ci_state         ON change_intents(state);
CREATE INDEX IF NOT EXISTS idx_ci_origin_family ON change_intents(origin_family);
CREATE INDEX IF NOT EXISTS idx_ci_origin_module ON change_intents(origin_module);
CREATE INDEX IF NOT EXISTS idx_ci_intent_kind   ON change_intents(intent_kind);
CREATE INDEX IF NOT EXISTS idx_ci_score         ON change_intents(score DESC);
CREATE INDEX IF NOT EXISTS idx_ci_target        ON change_intents(intent_target);
CREATE INDEX IF NOT EXISTS idx_ci_discovered    ON change_intents(discovered_at DESC);
"""


# --- State machine -------------------------------------------------------

STATE_PROPOSED     = "proposed"
STATE_ACCEPTED     = "accepted"
STATE_APPLIED      = "applied"
STATE_OBSERVED     = "observed"
STATE_FINALIZED    = "finalized"
STATE_REJECTED     = "rejected"
STATE_STAGED       = "staged"
STATE_FAILED       = "failed"
STATE_ROLLED_BACK  = "rolled_back"

ALL_STATES = (
    STATE_PROPOSED, STATE_ACCEPTED, STATE_APPLIED, STATE_OBSERVED,
    STATE_FINALIZED, STATE_REJECTED, STATE_STAGED, STATE_FAILED,
    STATE_ROLLED_BACK,
)

# Transizioni ammesse (from → set of to). Una qualunque from può andare a
# rolled_back come escape hatch (rollback da audit umano).
_TRANSITIONS: dict[str, set[str]] = {
    STATE_PROPOSED:    {STATE_ACCEPTED, STATE_REJECTED, STATE_STAGED},
    STATE_STAGED:      {STATE_ACCEPTED, STATE_REJECTED, STATE_PROPOSED},
    STATE_ACCEPTED:    {STATE_APPLIED, STATE_FAILED, STATE_REJECTED},
    STATE_APPLIED:     {STATE_OBSERVED, STATE_ROLLED_BACK},
    STATE_OBSERVED:    {STATE_FINALIZED, STATE_ROLLED_BACK},
    STATE_FINALIZED:   {STATE_ROLLED_BACK},
    STATE_REJECTED:    {STATE_PROPOSED},          # ri-proposta dopo dismissal
    STATE_FAILED:      {STATE_ACCEPTED, STATE_REJECTED},  # retry o give up
    STATE_ROLLED_BACK: set(),                     # terminale (audit only)
}


# --- Intent kinds (chiusi) ------------------------------------------------

KIND_CREATE_EXECUTOR     = "create_executor"
KIND_EXTEND_EXECUTOR     = "extend_executor"
KIND_DEDUPE_EXECUTORS    = "dedupe_executors"
KIND_MATERIALIZE_PIPELINE = "materialize_pipeline"
KIND_CACHE_PATTERN       = "cache_pattern"
KIND_REJECT_PATTERN      = "reject_pattern"

ALL_KINDS = (
    KIND_CREATE_EXECUTOR, KIND_EXTEND_EXECUTOR, KIND_DEDUPE_EXECUTORS,
    KIND_MATERIALIZE_PIPELINE, KIND_CACHE_PATTERN, KIND_REJECT_PATTERN,
)


# --- Dataclass -----------------------------------------------------------

@dataclass
class ChangeIntent:
    id: str
    fingerprint: str
    state: str
    origin_family: str          # telos|introvertiva|synt|user|observation|multi_tool|canonical
    origin_module: str          # scamper|dedupe|request_new_executor|L1|L2|feedback|...
    origin_source_id: str | None
    discovered_at: str          # ISO 8601 UTC
    intent_kind: str            # ALL_KINDS
    intent_target: str          # executor name | pattern key
    intent_summary: str         # 1 frase user-facing (lang neutro o multilingua)
    intent_rationale: str | None
    intent_body: dict           # kind-specific fields
    score: float = 0.0
    confidence: float = 0.8
    convergence: int = 1

    decision_by: str | None = None
    decision_ts: str | None = None
    decision_action: str | None = None
    decision_reason: str | None = None

    applied_at: str | None = None
    applied_effect: dict | None = None

    observed_at: str | None = None
    observed_metrics: dict | None = None

    finalized_at: str | None = None
    rolled_back_at: str | None = None
    rolled_back_reason: str | None = None
    failed_at: str | None = None
    failed_reason: str | None = None

    updated_at: str = ""

    @staticmethod
    def new(
        *,
        origin_family: str,
        origin_module: str,
        intent_kind: str,
        intent_target: str,
        intent_summary: str,
        intent_body: dict,
        intent_rationale: str | None = None,
        score: float = 0.0,
        confidence: float = 0.8,
        origin_source_id: str | None = None,
        fingerprint: str | None = None,
        discovered_at: str | None = None,
    ) -> "ChangeIntent":
        now = _iso_utc_now()
        fp = fingerprint or compute_fingerprint(
            origin_family=origin_family,
            intent_kind=intent_kind,
            intent_target=intent_target,
            intent_body=intent_body,
        )
        return ChangeIntent(
            id=str(uuid.uuid4()),
            fingerprint=fp,
            state=STATE_PROPOSED,
            origin_family=origin_family,
            origin_module=origin_module,
            origin_source_id=origin_source_id,
            discovered_at=discovered_at or now,
            intent_kind=intent_kind,
            intent_target=intent_target,
            intent_summary=intent_summary,
            intent_rationale=intent_rationale,
            intent_body=intent_body,
            score=score,
            confidence=confidence,
            convergence=1,
            updated_at=now,
        )


# --- Fingerprint deterministico per dedup cross-source -------------------

def compute_fingerprint(
    *,
    origin_family: str,
    intent_kind: str,
    intent_target: str,
    intent_body: dict,
) -> str:
    """Fingerprint stabile per (kind, target, body essenziale). Non include
    origin_family per permettere convergence cross-source.

    Body essenziale = subset chiave kind-specific:
      - create_executor:      name (two proposals to create the same
                              executor are one proposal, whoever wrote them
                              and however each producer filled the body)
      - extend_executor:      {target, arg_name}
      - dedupe_executors:     sorted set di {a, b}
      - materialize_pipeline: tools_sequence
      - cache_pattern:        canonical_query
      - reject_pattern:       (canonical_query, tools_sequence)
    """
    import hashlib
    essential: Any
    if intent_kind == KIND_CREATE_EXECUTOR:
        essential = {"name": intent_body.get("name") or intent_target}
    elif intent_kind == KIND_EXTEND_EXECUTOR:
        essential = {
            "target": intent_target,
            "arg_name": intent_body.get("arg_name"),
        }
    elif intent_kind == KIND_DEDUPE_EXECUTORS:
        members = sorted([
            intent_body.get("a") or "",
            intent_body.get("b") or intent_target,
        ])
        essential = {"members": members}
    elif intent_kind == KIND_MATERIALIZE_PIPELINE:
        essential = {
            "tools": intent_body.get("tools_sequence") or [],
        }
    elif intent_kind == KIND_CACHE_PATTERN:
        essential = {
            "canonical_query": intent_body.get("canonical_query") or intent_target,
            "tool_name": intent_body.get("tool_name"),
        }
    elif intent_kind == KIND_REJECT_PATTERN:
        essential = {
            "canonical_query": intent_body.get("canonical_query") or intent_target,
            "tools": intent_body.get("tools_sequence") or [],
        }
    else:
        essential = {"target": intent_target, "body": intent_body}
    payload = json.dumps(
        {"k": intent_kind, "e": essential},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# --- Storage layer -------------------------------------------------------

def _conn() -> sqlite3.Connection:
    db = C.DB_CHANGE_INTENTS
    db.parent.mkdir(parents=True, exist_ok=True)
    cn = sqlite3.connect(str(db), timeout=30.0, isolation_level=None)
    cn.row_factory = sqlite3.Row
    cn.execute("PRAGMA journal_mode=WAL")
    cn.execute("PRAGMA synchronous=NORMAL")
    return cn


# Kept when two rows turn out to be the same proposal: what happened in the
# world first, then a decision in flight, then a refusal (it must not come
# back), last a proposal nobody decided on.
_MERGE_PRECEDENCE = (
    STATE_FINALIZED, STATE_OBSERVED, STATE_APPLIED, STATE_ACCEPTED,
    STATE_REJECTED, STATE_FAILED, STATE_ROLLED_BACK, STATE_STAGED,
    STATE_PROPOSED,
)
_SCHEMA_VERSION = 1


def _migrate(cn: sqlite3.Connection) -> None:
    """One proposal per fingerprint (review of 10/11/2026). Repeatable.

    Fingerprints of creation proposals are recomputed with the current rule,
    rows that are the same proposal are merged keeping the most advanced
    one, and the fingerprint becomes unique so a concurrent save cannot
    duplicate it again.
    """
    cn.execute("BEGIN IMMEDIATE")
    try:
        if cn.execute("PRAGMA user_version").fetchone()[0] >= _SCHEMA_VERSION:
            cn.execute("COMMIT")
            return
        columns = {row[1] for row in cn.execute("PRAGMA table_info(change_intents)")}
        if "seen_families" not in columns:
            cn.execute("ALTER TABLE change_intents "
                       "ADD COLUMN seen_families TEXT NOT NULL DEFAULT ''")
        for row in cn.execute(
                "SELECT id, origin_family, intent_target, intent_body FROM change_intents "
                "WHERE intent_kind = ?", (KIND_CREATE_EXECUTOR,)).fetchall():
            try:
                body = json.loads(row["intent_body"] or "{}")
            except ValueError:
                body = {}
            cn.execute("UPDATE change_intents SET fingerprint = ? WHERE id = ?", (
                compute_fingerprint(
                    origin_family=row["origin_family"], intent_kind=KIND_CREATE_EXECUTOR,
                    intent_target=row["intent_target"],
                    intent_body=body if isinstance(body, dict) else {}),
                row["id"]))
        rank = {state: index for index, state in enumerate(_MERGE_PRECEDENCE)}
        kept: dict[str, sqlite3.Row] = {}
        for row in cn.execute(
                "SELECT id, fingerprint, state, origin_family, updated_at "
                "FROM change_intents ORDER BY updated_at DESC, id").fetchall():
            best = kept.get(row["fingerprint"])
            if best is None:
                kept[row["fingerprint"]] = row
                continue
            if rank.get(row["state"], len(rank)) < rank.get(best["state"], len(rank)):
                best, row = row, best
                kept[best["fingerprint"]] = best
            cn.execute("DELETE FROM change_intents WHERE id = ?", (row["id"],))
        cn.execute("DROP INDEX IF EXISTS idx_ci_fingerprint")
        cn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ci_fingerprint_unique "
                   "ON change_intents(fingerprint)")
        cn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        cn.execute("COMMIT")
    except BaseException:
        cn.execute("ROLLBACK")
        raise


def init_schema(cn: sqlite3.Connection) -> None:
    """Bring ``cn`` to the current schema: the one definition for the real
    store and for anyone who needs to know its exact shape (the retention
    owner compares a disposable store built here with the real one)."""
    factory = cn.row_factory
    cn.row_factory = sqlite3.Row
    try:
        cn.executescript(_SCHEMA)
        if cn.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
            _migrate(cn)
    finally:
        cn.row_factory = factory


def init_db() -> None:
    """Crea schema + indici (idempotente) e porta i dati alla forma corrente."""
    cn = _conn()
    try:
        init_schema(cn)
    finally:
        cn.close()


def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_to_intent(row: sqlite3.Row) -> ChangeIntent:
    return ChangeIntent(
        id=row["id"],
        fingerprint=row["fingerprint"],
        state=row["state"],
        origin_family=row["origin_family"],
        origin_module=row["origin_module"],
        origin_source_id=row["origin_source_id"],
        discovered_at=row["discovered_at"],
        intent_kind=row["intent_kind"],
        intent_target=row["intent_target"],
        intent_summary=row["intent_summary"],
        intent_rationale=row["intent_rationale"],
        intent_body=json.loads(row["intent_body"]) if row["intent_body"] else {},
        score=row["score"],
        confidence=row["confidence"],
        convergence=row["convergence"],
        decision_by=row["decision_by"],
        decision_ts=row["decision_ts"],
        decision_action=row["decision_action"],
        decision_reason=row["decision_reason"],
        applied_at=row["applied_at"],
        applied_effect=json.loads(row["applied_effect"]) if row["applied_effect"] else None,
        observed_at=row["observed_at"],
        observed_metrics=json.loads(row["observed_metrics"]) if row["observed_metrics"] else None,
        finalized_at=row["finalized_at"],
        rolled_back_at=row["rolled_back_at"],
        rolled_back_reason=row["rolled_back_reason"],
        failed_at=row["failed_at"],
        failed_reason=row["failed_reason"],
        updated_at=row["updated_at"],
    )


def _intent_to_params(ci: ChangeIntent) -> dict:
    d = asdict(ci)
    d["intent_body"] = json.dumps(ci.intent_body, ensure_ascii=False)
    d["applied_effect"] = json.dumps(ci.applied_effect, ensure_ascii=False) if ci.applied_effect is not None else None
    d["observed_metrics"] = json.dumps(ci.observed_metrics, ensure_ascii=False) if ci.observed_metrics is not None else None
    return d


def upsert_intent(ci: ChangeIntent) -> str:
    """Insert se fingerprint nuovo. Se esiste già:
      - convergence +1 only when a family that had not said so yet agrees
      - aggiorna score = ultima valutazione: scende quando i dati peggiorano
        (RM-0009, nucleo); quante fonti concordano lo dice convergence
      - aggiorna discovered_at = piu' recente
      - PRESERVA decision_*/applied_*/observed_*/state se gia' progrediti

    One writer at a time: the lookup and the write are one transaction, and
    the fingerprint is unique, so two turns crossing the threshold together
    cannot create the same proposal twice.

    Ritorna l'`id` (esistente o nuovo).
    """
    init_db()
    now = _iso_utc_now()
    ci.updated_at = now
    cn = _conn()
    try:
        cn.execute("BEGIN IMMEDIATE")
        existing = cn.execute(
            "SELECT * FROM change_intents WHERE fingerprint=? LIMIT 1",
            (ci.fingerprint,),
        ).fetchone()
        if existing is None:
            params = {**_intent_to_params(ci), "seen_families": ci.origin_family}
            cn.execute(
                """
                INSERT INTO change_intents
                  (id, fingerprint, state, origin_family, origin_module,
                   origin_source_id, discovered_at, intent_kind, intent_target,
                   intent_summary, intent_rationale, intent_body,
                   score, confidence, convergence,
                   decision_by, decision_ts, decision_action, decision_reason,
                   applied_at, applied_effect, observed_at, observed_metrics,
                   finalized_at, rolled_back_at, rolled_back_reason,
                   failed_at, failed_reason, updated_at, seen_families)
                VALUES
                  (:id, :fingerprint, :state, :origin_family, :origin_module,
                   :origin_source_id, :discovered_at, :intent_kind, :intent_target,
                   :intent_summary, :intent_rationale, :intent_body,
                   :score, :confidence, :convergence,
                   :decision_by, :decision_ts, :decision_action, :decision_reason,
                   :applied_at, :applied_effect, :observed_at, :observed_metrics,
                   :finalized_at, :rolled_back_at, :rolled_back_reason,
                   :failed_at, :failed_reason, :updated_at, :seen_families)
                """,
                params,
            )
            cn.execute("COMMIT")
            return ci.id
        # Esiste → merge non-distruttivo
        existing_id = existing["id"]
        families = [family for family in
                    (existing["seen_families"] or existing["origin_family"]).split(",")
                    if family]
        new_conv = existing["convergence"]
        if ci.origin_family not in families:
            families.append(ci.origin_family)
            new_conv = new_conv + 1
        # Discovered_at = il piu' recente
        new_discovered = max(existing["discovered_at"], ci.discovered_at)
        # A proposal given up with a date comes back, as a proposal, when the
        # same gap is seen again after that date (ADR 0239): never before.
        reopened = False
        if existing["state"] == STATE_REJECTED:
            try:
                until = (json.loads(existing["intent_body"] or "{}") or {}).get("closed_until")
            except (TypeError, ValueError):
                until = None
            reopened = bool(until) and now >= str(until)
        if reopened:
            cn.execute(
                "UPDATE change_intents SET state=?, failed_at=NULL, failed_reason=NULL WHERE id=?",
                (STATE_PROPOSED, existing["id"]))
        # What a person decided on stays as they read it: summary and
        # rationale follow new evidence only while the proposal is open.
        open_for_text = existing["state"] == STATE_PROPOSED or reopened
        cn.execute(
            """
            UPDATE change_intents
               SET score        = ?,
                   convergence  = ?,
                   seen_families = ?,
                   discovered_at = ?,
                   intent_summary = COALESCE(NULLIF(?, ''), intent_summary),
                   intent_rationale = COALESCE(NULLIF(?, ''), intent_rationale),
                   updated_at   = ?
             WHERE id = ?
            """,
            (ci.score, new_conv, ",".join(families), new_discovered,
             (ci.intent_summary or "") if open_for_text else "",
             (ci.intent_rationale or "") if open_for_text else "",
             now, existing_id),
        )
        cn.execute("COMMIT")
        return existing_id
    except BaseException:
        if cn.in_transaction:
            cn.execute("ROLLBACK")
        raise
    finally:
        cn.close()


def get_intent(id_: str) -> ChangeIntent | None:
    init_db()
    with _conn() as cn:
        row = cn.execute(
            "SELECT * FROM change_intents WHERE id=? LIMIT 1", (id_,),
        ).fetchone()
        return _row_to_intent(row) if row else None


def list_intents(
    *,
    state: str | Iterable[str] | None = None,
    origin_family: str | None = None,
    origin_module: str | None = None,
    intent_kind: str | None = None,
    min_score: float | None = None,
    limit: int = 100,
    offset: int = 0,
    order_by: str = "score_desc",
) -> list[ChangeIntent]:
    """Filtri AND. order_by ∈ {score_desc, discovered_desc, convergence_desc}."""
    init_db()
    wh: list[str] = []
    args: list[Any] = []
    if state is not None:
        if isinstance(state, str):
            wh.append("state = ?")
            args.append(state)
        else:
            states = list(state)
            wh.append(f"state IN ({','.join('?' for _ in states)})")
            args.extend(states)
    if origin_family:
        wh.append("origin_family = ?")
        args.append(origin_family)
    if origin_module:
        wh.append("origin_module = ?")
        args.append(origin_module)
    if intent_kind:
        wh.append("intent_kind = ?")
        args.append(intent_kind)
    if min_score is not None:
        wh.append("score >= ?")
        args.append(min_score)
    where = ("WHERE " + " AND ".join(wh)) if wh else ""
    order = {
        "score_desc": "score DESC, discovered_at DESC",
        "discovered_desc": "discovered_at DESC",
        "convergence_desc": "convergence DESC, score DESC",
    }.get(order_by, "score DESC, discovered_at DESC")
    sql = f"""
      SELECT * FROM change_intents
      {where}
      ORDER BY {order}
      LIMIT ? OFFSET ?
    """
    args.extend([int(limit), int(offset)])
    with _conn() as cn:
        rows = cn.execute(sql, args).fetchall()
        return [_row_to_intent(r) for r in rows]


def count_by_state() -> dict[str, int]:
    """Conteggio per badge UI. Include stati a 0."""
    init_db()
    out = {s: 0 for s in ALL_STATES}
    with _conn() as cn:
        for s, n in cn.execute(
            "SELECT state, COUNT(*) FROM change_intents GROUP BY state"
        ).fetchall():
            out[s] = int(n)
    return out


def count_by_filters(
    *,
    state: str | Iterable[str] | None = None,
    origin_family: str | None = None,
    origin_module: str | None = None,
    intent_kind: str | None = None,
    min_score: float | None = None,
) -> int:
    """Conteggio con stessi filtri di list_intents (per paginazione)."""
    init_db()
    wh: list[str] = []
    args: list[Any] = []
    if state is not None:
        if isinstance(state, str):
            wh.append("state = ?")
            args.append(state)
        else:
            states = list(state)
            wh.append(f"state IN ({','.join('?' for _ in states)})")
            args.extend(states)
    if origin_family:
        wh.append("origin_family = ?")
        args.append(origin_family)
    if origin_module:
        wh.append("origin_module = ?")
        args.append(origin_module)
    if intent_kind:
        wh.append("intent_kind = ?")
        args.append(intent_kind)
    if min_score is not None:
        wh.append("score >= ?")
        args.append(min_score)
    where = ("WHERE " + " AND ".join(wh)) if wh else ""
    sql = f"SELECT COUNT(*) FROM change_intents {where}"
    with _conn() as cn:
        return int(cn.execute(sql, args).fetchone()[0])


# --- Transizioni (singolo punto di mutate per ogni edge) -----------------

class TransitionError(Exception):
    pass


def _transition(
    id_: str,
    *,
    to_state: str,
    extra_cols: dict[str, Any] | None = None,
) -> ChangeIntent:
    """Cambia stato con check transizione + update extra cols + updated_at.
    Solleva TransitionError se from→to non ammessa."""
    init_db()
    now = _iso_utc_now()
    with _conn() as cn:
        row = cn.execute(
            "SELECT state FROM change_intents WHERE id=? LIMIT 1", (id_,),
        ).fetchone()
        if row is None:
            raise TransitionError(f"intent {id_} not found")
        from_state = row["state"]
        if to_state != from_state and to_state not in _TRANSITIONS.get(from_state, set()):
            raise TransitionError(
                f"transition {from_state}→{to_state} not allowed for {id_}"
            )
        cols = {"state": to_state, "updated_at": now}
        if extra_cols:
            for k, v in extra_cols.items():
                if isinstance(v, (dict, list)):
                    cols[k] = json.dumps(v, ensure_ascii=False)
                else:
                    cols[k] = v
        set_clause = ", ".join(f"{k}=?" for k in cols)
        cn.execute(
            f"UPDATE change_intents SET {set_clause} WHERE id=?",
            list(cols.values()) + [id_],
        )
    out = get_intent(id_)
    if out is None:
        raise TransitionError(f"intent {id_} disappeared after transition")
    return out


def apply_decision(
    id_: str,
    *,
    action: str,            # accept|reject|stage
    by: str,
    reason: str | None = None,
) -> ChangeIntent:
    """Registra una decisione utente. Mappa action → state:
      accept → ACCEPTED, reject → REJECTED, stage → STAGED.
    """
    mapping = {
        "accept": STATE_ACCEPTED,
        "reject": STATE_REJECTED,
        "stage":  STATE_STAGED,
    }
    if action not in mapping:
        raise ValueError(f"unknown action {action}; expected accept|reject|stage")
    return _transition(id_, to_state=mapping[action], extra_cols={
        "decision_by": by,
        "decision_ts": _iso_utc_now(),
        "decision_action": action,
        "decision_reason": reason,
    })


def mark_applied(id_: str, *, effect: dict) -> ChangeIntent:
    return _transition(id_, to_state=STATE_APPLIED, extra_cols={
        "applied_at": _iso_utc_now(),
        "applied_effect": effect,
    })


def mark_observed(id_: str, *, metrics: dict) -> ChangeIntent:
    return _transition(id_, to_state=STATE_OBSERVED, extra_cols={
        "observed_at": _iso_utc_now(),
        "observed_metrics": metrics,
    })


def mark_finalized(id_: str) -> ChangeIntent:
    return _transition(id_, to_state=STATE_FINALIZED, extra_cols={
        "finalized_at": _iso_utc_now(),
    })


def mark_rolled_back(id_: str, *, reason: str) -> ChangeIntent:
    """Transizione da QUALSIASI stato (escape hatch audit)."""
    init_db()
    now = _iso_utc_now()
    with _conn() as cn:
        row = cn.execute(
            "SELECT id FROM change_intents WHERE id=? LIMIT 1", (id_,),
        ).fetchone()
        if row is None:
            raise TransitionError(f"intent {id_} not found")
        cn.execute(
            """UPDATE change_intents
                  SET state=?, rolled_back_at=?, rolled_back_reason=?, updated_at=?
                WHERE id=?""",
            (STATE_ROLLED_BACK, now, reason, now, id_),
        )
    out = get_intent(id_)
    if out is None:
        raise TransitionError(f"intent {id_} disappeared after rollback")
    return out


def mark_failed(id_: str, *, reason: str) -> ChangeIntent:
    return _transition(id_, to_state=STATE_FAILED, extra_cols={
        "failed_at": _iso_utc_now(),
        "failed_reason": reason,
    })


# A proposal closed after every attempt failed stays closed this long: new
# evidence does not rebuild at once what was just given up (ADR 0239).
CLOSED_DAYS = 30


def update_body(id_: str, **fields: Any) -> ChangeIntent:
    """Merge fields into the proposal's body; state and decisions untouched."""
    current = get_intent(id_)
    if current is None:
        raise TransitionError(f"intent {id_} not found")
    body = {**(current.intent_body or {}), **fields}
    return _transition(id_, to_state=current.state, extra_cols={"intent_body": body})


def close_until(id_: str, *, reason: str, days: int = CLOSED_DAYS,
                by: str = "metnos") -> ChangeIntent:
    """Give a failed proposal up for ``days``: it leaves the open list and
    comes back only if the same gap is still seen after that date.

    ``by``: who closed it. Without it the row kept the name of whoever had
    decided before, and a closure nobody remembered could not be traced
    (12/11/2026).
    """
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + days * 86400))
    update_body(id_, closed_until=until, external_model_offer=False)
    return _transition(id_, to_state=STATE_REJECTED, extra_cols={
        "decision_by": by, "decision_action": "close", "decision_reason": reason,
        "decision_ts": _iso_utc_now(),
    })


# What a closure or a refusal leaves in the body and a reopened proposal drops.
_CLOSED_FACTS = ("closed_until", "external_model_offer", "external_model_consent")


REOPEN = "reopen"


def is_reopen(action: str) -> bool:
    """The action that takes a discarded proposal back among the proposals."""
    return action == REOPEN


def reopen(id_: str, *, by: str) -> ChangeIntent:
    """Bring a discarded proposal back among the proposals.

    The way back from a «close» or a «reject» given by mistake: one tap
    closed a proposal for thirty days and nothing could take it back. The
    closing date, the failure it followed and a consent given for that
    attempt are dropped, so the proposal is judged as a new one.
    """
    from messages import get as _msg

    current = get_intent(id_)
    if current is None:
        raise TransitionError(_msg("ERR_CHANGE_NOT_FOUND", id=id_))
    if current.state != STATE_REJECTED:
        raise TransitionError(_msg("ERR_CHANGE_REOPEN_NOT_REJECTED", state=current.state))
    body = {key: value for key, value in (current.intent_body or {}).items()
            if key not in _CLOSED_FACTS}
    return _transition(id_, to_state=STATE_PROPOSED, extra_cols={
        "intent_body": body, "failed_at": None, "failed_reason": None,
        "decision_by": by, "decision_ts": _iso_utc_now(),
        "decision_action": REOPEN, "decision_reason": None,
    })


def waits_for_external_model(intent: ChangeIntent) -> bool:
    """A failed creation whose local attempts are over and that asks the
    person whether the external model may finish it."""
    return bool(intent.state == STATE_FAILED
                and (intent.intent_body or {}).get("external_model_offer"))


def waits_for_person(intent: ChangeIntent) -> str | None:
    """The kind of answer this proposal waits for from the person, or None.

    The changes page shows these first, on every tab: a question left in the
    last tab was not found by the person it was for (11/11/2026).
    """
    if waits_for_external_model(intent):
        return "external_model"
    return None


def is_person_answer(action: str) -> bool:
    """The two answers to that question: allow the external model, or close."""
    return action in ("external", "close")


def person_answer(id_: str, action: str, *, by: str, reason: str | None,
                  via: str = "page") -> ChangeIntent:
    """Record the person's answer; nothing else may give this consent.

    ``via``: where the answer was given (the page, a pushed message). It is
    kept with the closure, the answer that one tap gives and nothing undoes.
    """
    from messages import get as _msg

    current = get_intent(id_)
    if current is None:
        raise TransitionError(_msg("ERR_CHANGE_NOT_FOUND", id=id_))
    if current.state != STATE_FAILED:
        raise TransitionError(_msg("ERR_CHANGE_RETRY_NOT_FAILED", state=current.state))
    if action == "close":
        return close_until(id_, reason=reason or f"closed by the person ({via})", by=by)
    if not waits_for_external_model(current):
        raise TransitionError(_msg("ERR_CHANGE_EXTERNAL_NOT_OFFERED"))
    update_body(id_, external_model_offer=False,
                external_model_consent={"by": by, "ts": _iso_utc_now()})
    return _transition(id_, to_state=STATE_ACCEPTED, extra_cols={
        "decision_by": by, "decision_ts": _iso_utc_now(),
        "decision_action": "external", "decision_reason": reason,
    })


def set_score(id_: str, score: float) -> None:
    """Record a recomputed score; state and decision fields are untouched."""
    init_db()
    cn = _conn()
    try:
        cn.execute("UPDATE change_intents SET score = ?, updated_at = ? WHERE id = ?",
                   (float(score), _iso_utc_now(), id_))
    finally:
        cn.close()


# --- Utility per UI / adapter --------------------------------------------

def update_observed_metrics(id_: str, *, metrics: dict) -> ChangeIntent:
    """Aggiorna metrics SENZA cambiare stato (per polling observer
    durante grace period)."""
    init_db()
    now = _iso_utc_now()
    with _conn() as cn:
        row = cn.execute(
            "SELECT state, observed_metrics FROM change_intents WHERE id=? LIMIT 1",
            (id_,),
        ).fetchone()
        if row is None:
            raise TransitionError(f"intent {id_} not found")
        cn.execute(
            """UPDATE change_intents
                  SET observed_metrics=?, updated_at=?
                WHERE id=?""",
            (json.dumps(metrics, ensure_ascii=False), now, id_),
        )
    out = get_intent(id_)
    if out is None:
        raise TransitionError(f"intent {id_} disappeared")
    return out


def delete_intent(id_: str) -> bool:
    """Hard delete (per test / cleanup). Ritorna True se cancellato."""
    init_db()
    with _conn() as cn:
        cur = cn.execute("DELETE FROM change_intents WHERE id=?", (id_,))
        return cur.rowcount > 0


def get_by_fingerprint(fingerprint: str) -> ChangeIntent | None:
    init_db()
    with _conn() as cn:
        row = cn.execute(
            "SELECT * FROM change_intents WHERE fingerprint=? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return _row_to_intent(row) if row else None
