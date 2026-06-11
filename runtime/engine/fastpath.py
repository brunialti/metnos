"""engine/fastpath.py — Layer 0: cache query→piano AUTO-PRODOTTA.

Bypass completo dell'engine quando la query matcha un fastpath. I fastpath
si producono IN AUTOMATICO a ogni turno completato con successo dal piano
pieno (L3 engine/recovery, mai da hit L0/L1): le catene sono executor GIÀ
vagliati e testati, nessuna approvazione esplicita (decisione 11/6/2026; il
bottone «approva fast-path» citato in passato non è mai esistito). Valvole:
delete da admin (/admin/praxis) + aging deterministico (prune).

Match in 2 sotto-layer:
  0a — hash lookup deterministic (<5ms, no LLM, no embed)
  0b — semantic cosine via BGE-M3 (<150ms, embed query nuova); serve SOLO
       framework non query-specific: un piano con literal content-bearing
       («name=Silvia») replicherebbe gli arg di UN'ALTRA query simile
       («foto di Marco», sim>soglia) → pertinenza, non sicurezza.

Confine vs autopath (L1): L0 = ripetizione della STESSA query, ammette piani
query-specific (via 0a); L1 = generalizzazione a cluster/intent col consenso
del feedback ✓. Il fast-path vince sempre (primo in cascata).

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
from .executor import is_query_specific

log = logging.getLogger(__name__)

_DB_INIT_DONE = False

# Step-tool il cui replay fuori dal turno d'origine è semanticamente
# scorretto: undo_last_turn si riferisce al TURNO PRECEDENTE (replay =
# annullare un turno arbitrario), get_inputs apre un dialog interattivo
# (flusso non riproducibile). Set CHIUSO (§2.2): estendere solo per la
# stessa classe di motivi (semantica dipendente dal contesto del turno).
NON_CACHEABLE_TOOLS = frozenset({"undo_last_turn", "get_inputs"})


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
            origin TEXT NOT NULL DEFAULT 'auto',
            intent_verb TEXT NOT NULL DEFAULT '',
            intent_object TEXT NOT NULL DEFAULT '',
            query_specific INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT '',
            n_uses INTEGER NOT NULL DEFAULT 0,
            last_used TEXT
        );
        CREATE INDEX IF NOT EXISTS fp_hash ON fastpaths(canonical_hash);
        CREATE INDEX IF NOT EXISTS fp_uses ON fastpaths(n_uses DESC);
        """)
        c.commit()
        _migrate_schema(c)
    return c


def _migrate_schema(c: sqlite3.Connection) -> None:
    """ALTER idempotente per DB con lo schema pre-auto-produzione (v1,
    colonne approved_by/approved_at, mai popolato in produzione — il bottone
    di approvazione non è mai esistito). Aggiunge le colonne v2 mancanti e
    backfilla created_at da approved_at. Non distruttivo (§2.9 spirito):
    le colonne v1 restano, ignorate."""
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(fastpaths)")}
        added = False
        for col, decl in (
            ("origin", "TEXT NOT NULL DEFAULT 'auto'"),
            ("intent_verb", "TEXT NOT NULL DEFAULT ''"),
            ("intent_object", "TEXT NOT NULL DEFAULT ''"),
            ("query_specific", "INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in cols:
                c.execute(f"ALTER TABLE fastpaths ADD COLUMN {col} {decl}")
                added = True
        if added and "approved_at" in cols:
            c.execute("UPDATE fastpaths SET created_at = approved_at "
                      "WHERE created_at = '' AND approved_at IS NOT NULL")
        if added:
            c.commit()
    except sqlite3.Error as ex:
        log.warning("fastpath: migrate schema fallita: %r", ex)


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
    # Layer 0b: semantic cosine. SOLO framework non query-specific (guard di
    # PERTINENZA §2.4/§7.9: i piani con literal content-bearing valgono per
    # quella query esatta → servibili solo via hash 0a).
    eb = _cluster.embed(query)
    if not eb:
        return None  # BGE-M3 unavailable, miss
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, framework_json, embedding "
            "FROM fastpaths WHERE embedding IS NOT NULL "
            "AND query_specific = 0").fetchall()
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


def record_success(query: str, framework: Framework, *,
                   intent=None, origin: str = "auto") -> int:
    """Auto-produce un fastpath da un turno completato con SUCCESSO dal piano
    pieno (chiamato da dispatch.run_turn sui percorsi engine/recovery).

    Nessuna approvazione esplicita: gli step sono executor già vagliati e
    testati; le valvole sono delete admin + aging (prune). Idempotente
    sull'hash canonico: la ripetizione RINFRESCA framework e metadati
    (self-healing: il piano cached segue l'ultimo successo).

    Non cacheabile (ritorna 0): framework senza step-executor reali (prosa
    statica → replay darebbe risposta in scatola) o con step il cui replay è
    context-dependent (NON_CACHEABLE_TOOLS: undo_last_turn, get_inputs).

    `intent` (engine.types.Intent, opzionale): verb/object salvati per la
    morte-su-executor-equivalente (vedi prune). Returns fp_id, 0 su skip/errore.
    """
    if not query or not query.strip() or not framework:
        return 0
    exec_steps = [s.tool for s in framework.steps
                  if s.tool and s.tool != "final_answer"]
    if not exec_steps:
        return 0
    if NON_CACHEABLE_TOOLS.intersection(exec_steps):
        return 0
    canonical = _cluster.normalize_query(query)
    h = _cluster.normalize_hash(query)
    eb = _cluster.embed(query)  # può essere None se BGE-M3 mancante → solo 0a
    fjson = json.dumps(framework.to_dict(), ensure_ascii=False)
    qspec = 1 if is_query_specific(fjson) else 0
    iverb = (getattr(intent, "verb", "") or "").lower().strip()
    iobj = (getattr(intent, "object", "") or "").lower().strip()
    try:
        c = _conn()
        cur = c.execute(
            "INSERT INTO fastpaths(canonical_text, canonical_hash, embedding, "
            "framework_json, origin, intent_verb, intent_object, "
            "query_specific, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(canonical_hash) DO UPDATE SET "
            "framework_json = excluded.framework_json, "
            "query_specific = excluded.query_specific, "
            "intent_verb = excluded.intent_verb, "
            "intent_object = excluded.intent_object",
            (canonical, h, eb, fjson, origin, iverb, iobj, qspec, _now_iso()))
        c.commit()
        fp_id = cur.lastrowid or 0
        c.close()
        return fp_id
    except Exception as ex:
        log.warning("fastpath.record_success failed: %r", ex)
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
    """Lista fastpaths per admin UI (telemetria: usi + ultimo uso)."""
    try:
        c = _conn()
        rows = c.execute(
            "SELECT id, canonical_text, origin, intent_verb, intent_object, "
            "query_specific, created_at, n_uses, last_used FROM fastpaths "
            "ORDER BY n_uses DESC, created_at DESC LIMIT ?",
            (limit,)).fetchall()
        c.close()
        return [
            {"id": r[0], "canonical_text": r[1], "origin": r[2],
             "intent_verb": r[3], "intent_object": r[4],
             "query_specific": bool(r[5]), "created_at": r[6],
             "n_uses": r[7], "last_used": r[8]}
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
