"""engine/autopath.py — Layer 1: skill auto-promosse da feedback ✓.

Caching framework dopo N feedback ✓ utente nello stesso cluster semantico.

Differenza vs Fastpath (Layer 0):
  - Fastpath: cache della STESSA query (hash/cosine) auto-prodotta a ogni
    turno-successo del piano pieno; ammette piani query-specific (solo 0a).
  - Autopath: generalizzazione a cluster/intent col consenso del feedback ✓
    (2+ stesso framework_hash + cluster); rifiuta piani query-specific.

Storage: ~/.local/share/metnos/autopath.sqlite (rename da praxis.sqlite).

Sostituisce la logica Praxis cache mantenendo:
  - intent_hash + cluster_id (BGE-M3) lookup
  - auto-promote dopo 2 ok (configurable)
  - demote/anti-skill su 3+ fail (TTL 30gg)
  - champion/challenger composite score
  - LWW simmetrico (✓ rimuove anti-skill matching)

§7.3: nessuna logica domain-specific. Solo storage + match.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import Intent, Framework
from . import cluster as _cluster
from .executor import compute_framework_hash, is_query_specific as _is_query_specific

log = logging.getLogger(__name__)

_DB_INIT_DONE = False

MIN_OBS_PROMOTE = int(os.environ.get("METNOS_AUTOPATH_MIN_OBS", "1"))
# v2: 1 obs sufficient se cluster cosine ≥ COSINE_HIGH (semantic equivalence).
# Cache hit prima → -50% latency su ricorrenze.
TTL_ANTISKILL_SECS = int(os.environ.get("METNOS_AUTOPATH_TTL_ANTI", "2592000"))  # 30gg
TTL_ANTISKILL_REPEAT_SECS = int(
    os.environ.get("METNOS_AUTOPATH_TTL_REPEAT", "3600"))  # 1h soft (verdict repeat)


@dataclass
class AutopathHit:
    skill_id: str
    framework: Framework
    cluster_id: str
    uses: int
    composite_score: float = 0.0


def _db_path() -> Path:
    import config as _C
    return _C.PATH_USER_DATA / "autopath.sqlite"


def _conn() -> sqlite3.Connection:
    """Apre connessione + DDL idempotent ad ogni call.
    Evita bug global-flag stale se DB deleted da fuori (bench reset)."""
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    if True:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            intent_sig TEXT NOT NULL,
            intent_hash TEXT NOT NULL,
            cluster_id TEXT,
            framework_json TEXT NOT NULL,
            framework_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            uses INTEGER NOT NULL DEFAULT 0,
            ok_count INTEGER NOT NULL DEFAULT 0,
            fail_count INTEGER NOT NULL DEFAULT 0,
            avg_latency_ms INTEGER DEFAULT 0,
            latency_p50_ms INTEGER DEFAULT 0,
            composite_score REAL DEFAULT 0.5,
            champion INTEGER DEFAULT 1,
            ts_created TEXT NOT NULL,
            ts_last_used TEXT
        );
        CREATE INDEX IF NOT EXISTS sk_cluster ON skills(cluster_id, status);
        CREATE INDEX IF NOT EXISTS sk_intent ON skills(intent_hash, status);

        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn_id TEXT NOT NULL,
            intent_hash TEXT NOT NULL,
            intent_sig TEXT NOT NULL,
            framework_json TEXT NOT NULL,
            framework_hash TEXT NOT NULL,
            cluster_id TEXT,
            embedding BLOB,
            verdict TEXT,
            verdict_ts TEXT,
            latency_ms INTEGER,
            ts TEXT NOT NULL,
            promoted_to TEXT
        );
        CREATE INDEX IF NOT EXISTS obs_intent ON observations(intent_hash);
        CREATE INDEX IF NOT EXISTS obs_cluster ON observations(cluster_id);

        CREATE TABLE IF NOT EXISTS anti_skills (
            intent_hash TEXT NOT NULL,
            framework_hash TEXT NOT NULL,
            fail_count INTEGER NOT NULL DEFAULT 1,
            ttl_expires_at TEXT NOT NULL,
            reason TEXT,
            ts_last_fail TEXT NOT NULL,
            PRIMARY KEY (intent_hash, framework_hash)
        );
        """)
        c.commit()
    return c


def prune(*, keep_observations: int | None = None) -> dict:
    """Reaper dello storage autopath (chiamato dal state_reaper builtin).

    - anti_skills: rimuove le righe con TTL scaduto (`ttl_expires_at < now`),
      che prima venivano cancellate SOLO via feedback ✓ matching (LWW) →
      accumulo silenzioso.
    - observations: tiene solo le piu' recenti N (la lookup legge una finestra
      breve via LIMIT, lo storico illimitato e' solo crescita disco: una riga
      ~4KB di embedding per turno engine).
    Idempotente. Ritorna un report dei conteggi rimossi.
    """
    from datetime import datetime, timezone
    if keep_observations is None:
        keep_observations = int(os.environ.get("METNOS_AUTOPATH_KEEP_OBS", "5000"))
    now_iso = datetime.now(timezone.utc).isoformat()
    c = _conn()
    try:
        anti = c.execute(
            "DELETE FROM anti_skills WHERE ttl_expires_at < ?", (now_iso,)
        ).rowcount
        obs = c.execute(
            "DELETE FROM observations WHERE rowid NOT IN "
            "(SELECT rowid FROM observations ORDER BY rowid DESC LIMIT ?)",
            (int(keep_observations),),
        ).rowcount
        c.commit()
        try:
            c.execute("VACUUM")
        except sqlite3.Error:
            pass
        return {"anti_skills_removed": max(0, anti),
                "observations_removed": max(0, obs),
                "kept_observations": int(keep_observations)}
    finally:
        c.close()


# ── Intent signature ──────────────────────────────────────────────────────

def _compute_intent_sig(intent: Intent) -> tuple[str, str]:
    """Ritorna (intent_sig leggibile, intent_hash 16-char)."""
    v = (intent.verb or "").lower().strip()
    o = (intent.object or "").lower().strip()
    kw = sorted(set(k.lower().strip() for k in intent.keywords if k))
    sig = f"{v}|{o}|{'_'.join(kw)}"
    h = hashlib.sha256(f"{v}|{o}".encode()).hexdigest()[:16]
    return sig, h


# ── Lookup ────────────────────────────────────────────────────────────────
# Predicato query-specificity condiviso con L0 fastpath: vive in
# engine/executor.py (is_query_specific + CONTENT_ARG_KEYS).


def lookup(query: str, intent: Intent) -> Optional[AutopathHit]:
    """Tenta match skill cached. Cluster semantic-first poi intent_hash fallback.

    Ritorna AutopathHit o None.
    """
    if not intent.is_complete():
        return None
    _, ihash = _compute_intent_sig(intent)
    eb = _cluster.embed(query)
    c = _conn()
    try:
        # 1. Cluster semantic match
        if eb:
            cur = c.execute(
                "SELECT cluster_id, embedding FROM observations "
                "WHERE embedding IS NOT NULL ORDER BY ts DESC LIMIT 200")
            best_sim = 0.0
            best_cid = None
            for cid, oeb in cur:
                if not oeb:
                    continue
                sim = _cluster.cosine(eb, oeb)
                if sim > best_sim:
                    best_sim = sim
                    best_cid = cid
            if best_sim >= _cluster.COSINE_HIGH and best_cid:
                row = c.execute(
                    "SELECT id, framework_json, uses, composite_score "
                    "FROM skills WHERE cluster_id = ? AND status = 'active' "
                    "AND champion = 1 LIMIT 1", (best_cid,)).fetchone()
                if row and not _is_query_specific(row[1]):
                    fw = Framework.from_dict(json.loads(row[1]))
                    return AutopathHit(skill_id=row[0], framework=fw,
                                        cluster_id=best_cid, uses=row[2],
                                        composite_score=row[3] or 0.5)
        # 2. Intent hash fallback (exact)
        row = c.execute(
            "SELECT id, framework_json, cluster_id, uses, composite_score "
            "FROM skills WHERE intent_hash = ? AND status = 'active' "
            "AND champion = 1 LIMIT 1", (ihash,)).fetchone()
        if row and not _is_query_specific(row[1]):
            fw = Framework.from_dict(json.loads(row[1]))
            return AutopathHit(skill_id=row[0], framework=fw,
                                cluster_id=row[2] or "", uses=row[3],
                                composite_score=row[4] or 0.5)
    finally:
        c.close()
    return None


# ── Observation recording ─────────────────────────────────────────────────

def record_observation(*, turn_id: str, intent: Intent, framework: Framework,
                        query: str = "", latency_ms: int = 0) -> str:
    """Registra turno per future promote/demote."""
    sig, ihash = _compute_intent_sig(intent)
    fhash = compute_framework_hash(framework)
    fjson = json.dumps(framework.to_dict(), ensure_ascii=False)
    eb = _cluster.embed(query) if query else None
    cid = None
    if eb:
        cid = _assign_cluster(eb)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        c = _conn()
        c.execute(
            "INSERT INTO observations(turn_id, intent_hash, intent_sig, "
            "framework_json, framework_hash, cluster_id, embedding, "
            "latency_ms, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, ihash, sig, fjson, fhash, cid, eb, latency_ms, ts))
        c.commit()
        c.close()
    except Exception as ex:
        log.warning("autopath.record_observation: %r", ex)
    return fhash


def _assign_cluster(eb: bytes) -> str:
    """Cluster_id deterministic: cosine vs neighbors. LLM judge opt-in.

    Default: top cosine ≥ COSINE_HIGH → riusa. Sotto LOW → nuovo. Zona
    grigia → nuovo (no LLM judge in default per latency).
    """
    try:
        c = _conn()
        rows = c.execute(
            "SELECT cluster_id, embedding FROM observations "
            "WHERE embedding IS NOT NULL ORDER BY ts DESC LIMIT 200").fetchall()
        c.close()
    except Exception:
        return _cluster.new_cluster_id()
    best_sim = 0.0
    best_cid = None
    for cid, oeb in rows:
        if not oeb or not cid:
            continue
        sim = _cluster.cosine(eb, oeb)
        if sim > best_sim:
            best_sim = sim
            best_cid = cid
    if best_sim >= _cluster.COSINE_HIGH and best_cid:
        return best_cid
    return _cluster.new_cluster_id()


# ── Feedback hooks (✓ ✗ ↻) ────────────────────────────────────────────────

def record_feedback(turn_id: str, verdict: str) -> dict:
    """Hook chiamato da turn_feedback dopo click utente.

    verdict ∈ {ok, fail, repeat}. Side effects:
      ok     → maybe promote (≥MIN_OBS_PROMOTE stesso framework_hash → skill)
               + LWW remove anti_skill matching
      fail   → fail_count++ + maybe anti_skill (≥3 fail)
      repeat → soft anti_skill TTL 1h (caller re-propose via recovery)
    """
    if verdict not in ("ok", "fail", "repeat"):
        return {"ok": False, "reason": "bad_verdict"}
    try:
        c = _conn()
        row = c.execute(
            "SELECT intent_hash, intent_sig, framework_json, framework_hash, "
            "cluster_id, latency_ms FROM observations WHERE turn_id = ? "
            "ORDER BY id DESC LIMIT 1", (turn_id,)).fetchone()
        if not row:
            c.close()
            return {"ok": False, "reason": "no_observation"}
        ihash, sig, fjson, fhash, cid, lat = row
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        c.execute("UPDATE observations SET verdict = ?, verdict_ts = ? "
                  "WHERE turn_id = ?", (verdict, ts, turn_id))
        out: dict = {"ok": True, "verdict": verdict,
                      "intent_hash": ihash, "framework_hash": fhash}
        if verdict == "ok":
            # LWW remove anti-skill
            rm = c.execute(
                "DELETE FROM anti_skills WHERE intent_hash = ? "
                "AND framework_hash = ?", (ihash, fhash)).rowcount
            if rm:
                out["anti_skill_removed"] = rm
                c.execute(
                    "UPDATE skills SET status = 'active' "
                    "WHERE intent_hash = ? AND framework_hash = ? "
                    "AND status = 'demoted'", (ihash, fhash))
            # Promote check: N obs same hash with verdict ok?
            n_ok = c.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE intent_hash = ? AND framework_hash = ? "
                "AND verdict = 'ok'", (ihash, fhash)).fetchone()[0]
            if n_ok >= MIN_OBS_PROMOTE:
                if _is_query_specific(fjson):
                    # Framework legato alla query (arg content-bearing literal):
                    # non generalizza, non diventa champion (anti-poisoning).
                    out["promotion_skipped"] = "query_specific_literal_args"
                else:
                    skill_id = _promote_skill(c, ihash, sig, fhash, fjson, cid, ts)
                    if skill_id:
                        out["promoted_skill_id"] = skill_id
        elif verdict == "fail":
            # Anti-skill se 3+ fail
            n_fail = c.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE intent_hash = ? AND framework_hash = ? "
                "AND verdict = 'fail'", (ihash, fhash)).fetchone()[0]
            if n_fail >= 3:
                ttl = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(time.time() + TTL_ANTISKILL_SECS))
                c.execute(
                    "INSERT INTO anti_skills(intent_hash, framework_hash, "
                    "fail_count, ttl_expires_at, reason, ts_last_fail) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO UPDATE SET "
                    "fail_count = anti_skills.fail_count + 1, "
                    "ttl_expires_at = excluded.ttl_expires_at, "
                    "ts_last_fail = excluded.ts_last_fail",
                    (ihash, fhash, n_fail, ttl, f"feedback_fail:{n_fail}", ts))
                # Demote skill
                c.execute(
                    "UPDATE skills SET status = 'demoted' "
                    "WHERE intent_hash = ? AND framework_hash = ?",
                    (ihash, fhash))
                out["anti_skill_added"] = True
        elif verdict == "repeat":
            # Soft anti-skill TTL breve (1h): il framework è stato ri-proposto
            # ma l'utente ha chiesto un retry → escludilo temporaneamente cosi'
            # il caller (recovery) ri-propone una shape diversa. Riusa lo stesso
            # path di insert anti_skill del ramo `fail`, con TTL corto.
            ttl = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + TTL_ANTISKILL_REPEAT_SECS))
            c.execute(
                "INSERT INTO anti_skills(intent_hash, framework_hash, "
                "fail_count, ttl_expires_at, reason, ts_last_fail) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO UPDATE SET "
                "fail_count = anti_skills.fail_count + 1, "
                "ttl_expires_at = excluded.ttl_expires_at, "
                "ts_last_fail = excluded.ts_last_fail",
                (ihash, fhash, 1, ttl, "feedback_repeat", ts))
            out["anti_skill_repeat"] = True
        c.commit()
        c.close()
        return out
    except Exception as ex:
        log.warning("autopath.record_feedback: %r", ex)
        return {"ok": False, "reason": str(ex)}


def _promote_skill(c, ihash: str, sig: str, fhash: str, fjson: str,
                    cid: Optional[str], ts: str) -> Optional[str]:
    """Crea skill ACTIVE se non già presente. Ritorna skill_id."""
    base = sig.replace("|", "_")[:40] or "skill"
    skill_id = f"{base}_v1.0.0"
    existing = c.execute(
        "SELECT id FROM skills WHERE intent_hash = ? AND framework_hash = ?",
        (ihash, fhash)).fetchone()
    if existing:
        c.execute("UPDATE skills SET uses = uses + 1, ok_count = ok_count + 1, "
                  "ts_last_used = ? WHERE id = ?", (ts, existing[0]))
        return existing[0]
    c.execute(
        "INSERT OR IGNORE INTO skills(id, intent_sig, intent_hash, cluster_id, "
        "framework_json, framework_hash, status, uses, ok_count, "
        "ts_created, ts_last_used) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 1, ?, ?)",
        (skill_id, sig, ihash, cid, fjson, fhash, ts, ts))
    return skill_id


# ── Anti-skill check (per Proposer exclusion) ─────────────────────────────

def excluded_framework_hashes(intent: Intent) -> set[str]:
    """Anti-skills attivi (TTL non scaduto) per intent."""
    _, ihash = _compute_intent_sig(intent)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        c = _conn()
        rows = c.execute(
            "SELECT framework_hash FROM anti_skills "
            "WHERE intent_hash = ? AND ttl_expires_at > ?",
            (ihash, ts)).fetchall()
        c.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


# ── Introspezione read-only (admin UI /admin/praxis) ──────────────────────
# Sola lettura: NESSUNA logica di promote/lookup/demote. Colonne esplicite
# (niente SELECT * → embedding BLOB resta fuori dal payload UI).

_SKILL_COLS = ("id", "intent_sig", "intent_hash", "cluster_id", "status",
               "uses", "ok_count", "fail_count", "composite_score",
               "champion", "ts_created", "ts_last_used")

_OBS_COLS = ("id", "turn_id", "intent_hash", "intent_sig", "framework_json",
             "framework_hash", "cluster_id", "verdict", "verdict_ts",
             "latency_ms", "ts", "promoted_to")

_ANTI_COLS = ("intent_hash", "framework_hash", "fail_count",
              "ttl_expires_at", "reason", "ts_last_fail")


def stats() -> dict:
    """Aggregati per la dashboard admin."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    c = _conn()
    try:
        by_status = dict(c.execute(
            "SELECT status, COUNT(*) FROM skills GROUP BY status").fetchall())
        obs_total = c.execute(
            "SELECT COUNT(*) FROM observations").fetchone()[0]
        anti_active = c.execute(
            "SELECT COUNT(*) FROM anti_skills WHERE ttl_expires_at > ?",
            (now,)).fetchone()[0]
        return {"skills_by_status": by_status,
                "observations_total": obs_total,
                "anti_skills_active": anti_active}
    finally:
        c.close()


def list_skills(status: str = "active", limit: int = 50) -> list[dict]:
    """Skill per status, le piu' usate prima."""
    c = _conn()
    try:
        rows = c.execute(
            f"SELECT {', '.join(_SKILL_COLS)} FROM skills "
            "WHERE status = ? ORDER BY uses DESC, ts_last_used DESC LIMIT ?",
            (status, int(limit))).fetchall()
        return [dict(zip(_SKILL_COLS, r)) for r in rows]
    finally:
        c.close()


def recent_observations(limit: int = 30) -> list[dict]:
    """Ultime osservazioni, senza embedding."""
    c = _conn()
    try:
        rows = c.execute(
            f"SELECT {', '.join(_OBS_COLS)} FROM observations "
            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(zip(_OBS_COLS, r)) for r in rows]
    finally:
        c.close()


def active_anti_skills(limit: int = 20) -> list[dict]:
    """Anti-skill con TTL non scaduto, fail piu' recenti prima."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    c = _conn()
    try:
        rows = c.execute(
            f"SELECT {', '.join(_ANTI_COLS)} FROM anti_skills "
            "WHERE ttl_expires_at > ? ORDER BY ts_last_fail DESC LIMIT ?",
            (now, int(limit))).fetchall()
        return [dict(zip(_ANTI_COLS, r)) for r in rows]
    finally:
        c.close()


# NB: `demote_skill_for_query` (LWW utente-prevale su approvazione manuale
# fastpath) RIMOSSA 11/6/2026: serviva il bottone «approva fast-path» mai
# implementato; con l'auto-produzione L0 (nessun consenso esplicito) il demote
# L1 non ha base — L0 vince comunque in cascata sulla query esatta, la skill
# L1 resta utile per le sorelle del cluster.
