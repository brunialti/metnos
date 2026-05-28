"""engine/fastpath.py — Layer 0: path utente-approvati.

Bypass completo dell'engine quando la query matcha un fastpath approvato
esplicitamente dall'utente.

Match in 2 sotto-layer:
  0a — hash lookup deterministic (<5ms, no LLM, no embed)
  0b — semantic cosine via BGE-M3 (<150ms, embed query nuova)

User approva tramite bottone client "🌟 approva fast-path" su risposta OK:
salva (canonical_text, canonical_hash, embedding, framework).

Conflitto con autopath: fast-path vince sempre. Hook in approve_one() demote
skill_cache.skill con framework diverso per stesso cluster.

§7.9 deterministic. LLM mai chiamato in lookup. Embed BGE-M3 in 0b.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .types import Framework
from . import cluster as _cluster

log = logging.getLogger(__name__)

_DB_INIT_DONE = False


@dataclass
class FastpathHit:
    fp_id: int
    canonical_text: str
    framework: Framework
    match_kind: str  # 'hash' | 'cosine'
    similarity: float = 1.0  # 1.0 per hash, cosine per semantic


def _db_path() -> Path:
    import config as _C
    return _C.PATH_USER_DATA / "fastpaths.sqlite"


def _conn() -> sqlite3.Connection:
    """Apre connessione + DDL idempotent ad ogni call."""
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    if True:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS fastpaths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_text TEXT NOT NULL,
            canonical_hash TEXT NOT NULL UNIQUE,
            embedding BLOB,
            framework_json TEXT NOT NULL,
            approved_by TEXT,
            approved_at TEXT NOT NULL,
            n_uses INTEGER NOT NULL DEFAULT 0,
            last_used TEXT
        );
        CREATE INDEX IF NOT EXISTS fp_hash ON fastpaths(canonical_hash);
        CREATE INDEX IF NOT EXISTS fp_uses ON fastpaths(n_uses DESC);
        """)
        c.commit()
    return c


def lookup(query: str) -> Optional[FastpathHit]:
    """Tenta match query → fastpath. Layer 0a (hash) prima, poi 0b (cosine).

    Ritorna FastpathHit se match, None se miss.
    """
    if not query or not query.strip():
        return None
    # Layer 0a: hash deterministic
    h = _cluster.normalize_hash(query)
    try:
        c = _conn()
        row = c.execute(
            "SELECT id, canonical_text, framework_json FROM fastpaths "
            "WHERE canonical_hash = ?", (h,)).fetchone()
        c.close()
        if row:
            try:
                fw = Framework.from_dict(json.loads(row[2]))
                _touch(row[0])
                return FastpathHit(fp_id=row[0], canonical_text=row[1],
                                    framework=fw, match_kind="hash",
                                    similarity=1.0)
            except Exception as ex:
                log.warning("fastpath: parse framework_json failed: %r", ex)
    except Exception as ex:
        log.warning("fastpath: 0a lookup failed: %r", ex)
        return None
    # Layer 0b: semantic cosine
    eb = _cluster.embed(query)
    if not eb:
        return None  # BGE-M3 unavailable, miss
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, framework_json, embedding "
            "FROM fastpaths WHERE embedding IS NOT NULL").fetchall()
        c.close()
    except Exception as ex:
        log.warning("fastpath: 0b query failed: %r", ex)
        return None
    threshold = _cluster.COSINE_HIGH + 0.02  # leggermente più stretto del cluster
    best = None
    best_sim = 0.0
    for fp_id, ctext, fjson, stored_eb in rows:
        if not stored_eb:
            continue
        sim = _cluster.cosine(eb, stored_eb)
        if sim > best_sim:
            best_sim = sim
            best = (fp_id, ctext, fjson)
    if best and best_sim >= threshold:
        try:
            fw = Framework.from_dict(json.loads(best[2]))
            _touch(best[0])
            return FastpathHit(fp_id=best[0], canonical_text=best[1],
                                framework=fw, match_kind="cosine",
                                similarity=best_sim)
        except Exception:
            return None
    return None


def approve(query: str, framework: Framework, *, approved_by: str = "") -> int:
    """Approva nuovo fastpath. Idempotente sull'hash canonical.

    Returns fp_id. Hook: demote autopath.skill con framework_hash diverso
    per cluster simile (delegato a chi chiama).
    """
    if not query or not framework:
        return 0
    canonical = _cluster.normalize_query(query)
    h = _cluster.normalize_hash(query)
    eb = _cluster.embed(query)  # può essere None se BGE-M3 mancante
    fjson = json.dumps(framework.to_dict(), ensure_ascii=False)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        c = _conn()
        cur = c.execute(
            "INSERT INTO fastpaths(canonical_text, canonical_hash, embedding, "
            "framework_json, approved_by, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(canonical_hash) DO UPDATE SET "
            "framework_json = excluded.framework_json, "
            "approved_at = excluded.approved_at",
            (canonical, h, eb, fjson, approved_by, ts))
        c.commit()
        fp_id = cur.lastrowid or 0
        c.close()
        return fp_id
    except Exception as ex:
        log.warning("fastpath.approve failed: %r", ex)
        return 0


def _touch(fp_id: int) -> None:
    """Aggiorna n_uses + last_used."""
    try:
        c = _conn()
        c.execute(
            "UPDATE fastpaths SET n_uses = n_uses + 1, last_used = ? "
            "WHERE id = ?",
            (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), fp_id))
        c.commit()
        c.close()
    except Exception:
        pass


def list_all(limit: int = 100) -> list[dict]:
    """Lista fastpaths per admin UI."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, approved_by, approved_at, "
            "n_uses, last_used FROM fastpaths "
            "ORDER BY n_uses DESC LIMIT ?", (limit,)).fetchall()
        c.close()
        return [
            {"id": r[0], "canonical_text": r[1], "approved_by": r[2],
             "approved_at": r[3], "n_uses": r[4], "last_used": r[5]}
            for r in rows
        ]
    except Exception:
        return []


def delete(fp_id: int) -> bool:
    """Cancella fastpath. Usato da admin UI."""
    try:
        c = _conn()
        cur = c.execute("DELETE FROM fastpaths WHERE id = ?", (fp_id,))
        c.commit()
        c.close()
        return cur.rowcount > 0
    except Exception:
        return False
