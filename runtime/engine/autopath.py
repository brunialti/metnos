"""engine/autopath.py — Layer 1: autopath auto-promosse da feedback ✓.

Caching framework dopo N feedback ✓ utente nello stesso cluster semantico.

Differenza vs Fastpath (Layer 0):
  - Fastpath: cache della STESSA query (hash/cosine) auto-prodotta a ogni
    turno-successo del piano pieno; ammette piani query-specific (solo 0a).
  - Autopath: generalizzazione a cluster/intent col consenso del feedback ✓
    UMANO esplicito (MIN_OBS_PROMOTE obs stesso framework_hash + cluster,
    default 1 — v2); rifiuta piani query-specific.

Storage: ~/.local/share/metnos/autopath.sqlite (rename da praxis.sqlite).

Sostituisce la logica Praxis cache mantenendo:
  - intent_hash + cluster_id (BGE-M3) lookup
  - auto-promote dopo MIN_OBS_PROMOTE ok (default 1, configurable)
  - demote/anti-autopath su 3+ fail (TTL 30gg)
  - champion/challenger composite score
  - LWW simmetrico (✓ rimuove anti-autopath matching)

§7.3: nessuna logica domain-specific. Solo storage + match.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from timefmt import now_iso_z

from .types import Intent, Framework
from . import cluster as _cluster
from .executor import compute_framework_hash, is_query_specific as _is_query_specific

log = logging.getLogger(__name__)

_DB_INIT_DONE = False

MIN_OBS_PROMOTE = int(os.environ.get("METNOS_AUTOPATH_MIN_OBS", "1"))
# v2: 1 obs sufficient se cluster cosine ≥ COSINE_HIGH (semantic equivalence).
# Cache hit prima → -50% latency su ricorrenze.
TTL_ANTIAUTOPATH_SECS = int(os.environ.get("METNOS_AUTOPATH_TTL_ANTI", "2592000"))  # 30gg
TTL_ANTIAUTOPATH_REPEAT_SECS = int(
    os.environ.get("METNOS_AUTOPATH_TTL_REPEAT", "3600"))  # 1h soft (verdict repeat)
# Cosine-floor del fallback intent_hash (path 2 di lookup, 14/6). L'intent_hash
# (verb|object) NON basta: due query stesso intent ma slot diversi («mail di X»
# vs «tutte le mailbox 24h») non devono ereditare lo stesso champion. Sotto
# FLOOR la query e' troppo lontana dal cluster dell'autopath → astieniti (full
# engine decide). Calibrato 14/6 su dati reali: within-cluster p05=0.870 (≈0
# regressione), same-intent cross-cluster p50=0.795 (rigetta i misroute). FLOOR
# < COSINE_HIGH (0.90, path 1) per costruzione.
# ALZATO 0.82→0.87 (Roberto 9/7): 0.82 lasciava passare troppi cross-cluster
# (over-matching L1: «come sta il server» ereditava un piano `read_urls_html`).
# 0.87 = ~within-cluster p05 → i legittimi same-cluster passano ancora, i
# misroute cross-cluster sotto 0.87 sono rigettati (full engine ripianifica).
COSINE_FLOOR_INTENT = float(os.environ.get("METNOS_AUTOPATH_FLOOR", "0.87"))


@dataclass
class AutopathHit:
    autopath_id: str
    framework: Framework
    cluster_id: str
    uses: int
    composite_score: float = 0.0
    # ADR 0182: firme del mondo alla promozione — verificate al hit dal
    # dispatch (cache_validity.validate); vuote = riga pre-migrazione = MISS.
    tools_sig: str = ""
    pool_sig: str = ""


def _db_path() -> Path:
    import config as _C
    return _C.PATH_USER_DATA / "autopath.sqlite"


def _conn() -> sqlite3.Connection:
    """Apre connessione + DDL idempotent ad ogni call.
    Evita bug global-flag stale se DB deleted da fuori (bench reset)."""
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    # Migrazione rename skills→autopaths (14/6): preserva i piani L1 GIA' appresi
    # (ALTER TABLE RENAME, idempotente). §7.1 rename pulito ma niente data-loss.
    _tabs = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "skills" in _tabs and "autopaths" not in _tabs:
        c.execute("ALTER TABLE skills RENAME TO autopaths")
        c.execute("DROP INDEX IF EXISTS sk_cluster")
        c.execute("DROP INDEX IF EXISTS sk_intent")
    if "anti_skills" in _tabs and "anti_autopaths" not in _tabs:
        c.execute("ALTER TABLE anti_skills RENAME TO anti_autopaths")
    if True:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS autopaths (
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
        CREATE INDEX IF NOT EXISTS ap_cluster ON autopaths(cluster_id, status);
        CREATE INDEX IF NOT EXISTS ap_intent ON autopaths(intent_hash, status);

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

        CREATE TABLE IF NOT EXISTS anti_autopaths (
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
    # ADR 0182 (cache-validity): firme del mondo. Sulle OBSERVATIONS al
    # momento del turno (il mondo visto); copiate su AUTOPATHS alla promozione.
    # Migrazione additiva preserva-dati; vuote = MISS una volta al hit.
    for tab in ("observations", "autopaths"):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({tab})")}
        for col in ("tools_sig", "pool_sig"):
            if col not in cols:
                c.execute(f"ALTER TABLE {tab} ADD COLUMN {col} "
                          "TEXT NOT NULL DEFAULT ''")
    # W1 learning-loop (ADR 0185): autopath SEMINATO da turni engine riusciti
    # e costosi (senza ✓ umano) = shadow=1; il primo feedback ✓ lo conferma
    # champion (la ri-promozione azzera shadow). Additiva preserva-dati.
    ap_cols = {r[1] for r in c.execute("PRAGMA table_info(autopaths)")}
    if "shadow" not in ap_cols:
        c.execute("ALTER TABLE autopaths ADD COLUMN shadow "
                  "INTEGER NOT NULL DEFAULT 0")
    c.commit()
    return c


def flush() -> dict:
    """Svuota L1 COMPLETO: autopaths + anti_autopaths + observations
    (opzione admin, 6/7). I CLUSTER semantici restano (embedding-based,
    engine-agnostici); il riapprendimento riparte dal traffico e dai ✓."""
    c = _conn()
    try:
        out = {}
        for tab in ("autopaths", "anti_autopaths", "observations"):
            out[f"{tab}_deleted"] = c.execute(
                f"SELECT COUNT(*) FROM {tab}").fetchone()[0]
            c.execute(f"DELETE FROM {tab}")
        c.commit()
        return out
    finally:
        c.close()


def prune(*, keep_observations: int | None = None,
          catalog_names: Optional[set] = None) -> dict:
    """Reaper dello storage autopath (chiamato dal state_reaper builtin).

    - anti_autopaths: rimuove le righe con TTL scaduto (`ttl_expires_at < now`),
      che prima venivano cancellate SOLO via feedback ✓ matching (LWW) →
      accumulo silenzioso.
    - observations: tiene solo le piu' recenti N (la lookup legge una finestra
      breve via LIMIT, lo storico illimitato e' solo crescita disco: una riga
      ~4KB di embedding per turno engine).
    - autopaths (aging, 1/7/2026 — prima NESSUNA valvola: zombie eterni):
      demoted piu' vecchie di METNOS_AUTOPATH_DEMOTED_TTL_DAYS (30gg,
      allineato a TTL_ANTIAUTOPATH: oltre, anche la memoria del fallimento
      e' scaduta e la riga serviva solo alla riattivazione LWW a ridosso
      del demote); active con ts_last_used piu' vecchio di
      METNOS_AUTOPATH_STALE_DAYS (90gg = 3x L0 stale: il piano e'
      generalizzato e la ri-promozione costa un feedback ✓ reale, quindi
      orizzonte piu' conservativo). Un ✓ successivo ri-promuove da zero.
    - MORTE da catalogo (C3, ADR 0182 follow-up 6/7): con `catalog_names`
      (set COMPLETO dei tool invocabili — None = nessuna morte, mai falsi
      kill §2.8) rimuove gli autopath che referenziano un tool SPARITO:
      invaliderebbero a ogni hit per sempre (C1), sono peso morto.
    Idempotente. Ritorna un report dei conteggi rimossi.
    """
    from datetime import datetime, timedelta, timezone
    if keep_observations is None:
        keep_observations = int(os.environ.get("METNOS_AUTOPATH_KEEP_OBS", "5000"))
    stale_days = int(os.environ.get("METNOS_AUTOPATH_STALE_DAYS", "90"))
    demoted_ttl_days = int(os.environ.get("METNOS_AUTOPATH_DEMOTED_TTL_DAYS", "30"))
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    c = _conn()
    try:
        anti = c.execute(
            "DELETE FROM anti_autopaths WHERE ttl_expires_at < ?", (now_iso,)
        ).rowcount
        # C3: morte-da-catalogo (tool sparito ⇒ hit sempre-invalido C1).
        dead_catalog = 0
        if catalog_names:
            rows = c.execute(
                "SELECT id, framework_json FROM autopaths").fetchall()
            for ap_id, fj in rows:
                try:
                    tools = {st.get("tool") for st in
                             (json.loads(fj).get("steps") or [])
                             if isinstance(st, dict) and st.get("tool")}
                except Exception:
                    continue
                missing = tools - set(catalog_names) - {"final_answer"}
                if missing:
                    c.execute("DELETE FROM autopaths WHERE id = ?", (ap_id,))
                    dead_catalog += 1
                    log.info("[autopath.prune] morte da catalogo %s "
                             "(tool spariti: %s)", ap_id, sorted(missing))
        # La finestra pota SOLO le righe senza verdict (2/7/2026, review
        # Fable): le observations votate (✓/✗ umano) sono la memoria di
        # promote/demote — poche e preziose; una finestra piena di
        # verdict=NULL espelleva l'unica `ok` rendendo il re-promote
        # impossibile.
        obs = c.execute(
            "DELETE FROM observations WHERE verdict IS NULL "
            "AND rowid NOT IN "
            "(SELECT rowid FROM observations ORDER BY rowid DESC LIMIT ?)",
            (int(keep_observations),),
        ).rowcount
        # Cutoff in formato Z, lo stesso di ts_created/ts_last_used
        # (timefmt.now_iso_z): il confronto lessicografico resta corretto.
        demoted = 0
        if demoted_ttl_days > 0:
            cut = (now - timedelta(days=demoted_ttl_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            demoted = c.execute(
                "DELETE FROM autopaths WHERE status = 'demoted' "
                "AND COALESCE(ts_last_used, ts_created) < ?", (cut,)
            ).rowcount
        stale = 0
        if stale_days > 0:
            cut = (now - timedelta(days=stale_days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            stale = c.execute(
                "DELETE FROM autopaths WHERE status = 'active' "
                "AND COALESCE(ts_last_used, ts_created) < ?", (cut,)
            ).rowcount
        c.commit()
        try:
            c.execute("VACUUM")
        except sqlite3.Error:
            pass
        return {"anti_autopaths_removed": max(0, anti),
                "observations_removed": max(0, obs),
                "kept_observations": int(keep_observations),
                "autopaths_demoted_removed": max(0, demoted),
                "autopaths_stale_removed": max(0, stale),
            "autopaths_dead_catalog": dead_catalog}
    finally:
        c.close()


# ── Intent signature ──────────────────────────────────────────────────────

def _compute_intent_sig(intent: Intent) -> tuple[str, str]:
    """Ritorna (intent_sig leggibile, intent_hash 16-char).

    Compound-aware (D3-D, 18/6): l'intent_hash include la SEQUENZA completa
    delle `actions` (verb|object per clausola), non solo il verb|object
    PRIMARIO. Senza, due compound con la stessa PRIMA clausola ma seconda
    diversa (es. {find,issues}+{write,entries} vs {find,issues}+{delete,
    entries}) collidono sotto lo stesso ihash → cross-serve dei piani in
    cache (path-2 intent_hash, privo di object-boundary). Mono-azione:
    `actions` vuoto → base = "verb|object" → ihash IDENTICO al precedente
    (back-compat: zero invalidazione della cache esistente)."""
    v = (intent.verb or "").lower().strip()
    o = (intent.object or "").lower().strip()
    kw = sorted(set(k.lower().strip() for k in intent.keywords if k))
    sig = f"{v}|{o}|{'_'.join(kw)}"
    acts = getattr(intent, "actions", None) or []
    acts_sig = ";".join(
        f"{(a.get('verb') or '').lower().strip()}|{(a.get('object') or '').lower().strip()}"
        for a in acts if isinstance(a, dict))
    base = f"{v}|{o}|{acts_sig}" if acts_sig else f"{v}|{o}"
    h = hashlib.sha256(base.encode()).hexdigest()[:16]
    return sig, h


def _sig_object(sig: str) -> str:
    """Object (categoria) da un intent_sig 'verb|object|keywords'. '' se assente."""
    parts = (sig or "").split("|")
    return parts[1].strip().lower() if len(parts) > 1 else ""


# ── Lookup ────────────────────────────────────────────────────────────────
# Predicato query-specificity condiviso con L0 fastpath: vive in
# engine/executor.py (is_query_specific + CONTENT_ARG_KEYS).


def _max_cosine_to_cluster(c, eb: bytes, cluster_id: str, limit: int = 50) -> float:
    """Max cosine fra `eb` e le osservazioni recenti del cluster (per il
    cosine-floor del path 2). 0.0 se il cluster non ha osservazioni con embed."""
    best = 0.0
    for (oeb,) in c.execute(
        "SELECT embedding FROM observations WHERE cluster_id = ? "
        "AND embedding IS NOT NULL ORDER BY ts DESC LIMIT ?",
        (cluster_id, int(limit))):
        if oeb:
            s = _cluster.cosine(eb, oeb)
            if s > best:
                best = s
    return best


def _touch_served(c, autopath_id: str) -> None:
    """Rinfresca ts_last_used quando il champion viene SERVITO dalla cache
    (2/7/2026, review Fable): l'aging del prune (`stale <90gg`) legge
    ts_last_used, che prima veniva scritto SOLO su ✓-repromote — un champion
    servito attivamente ma mai ri-votato veniva potato come zombie.
    Best-effort: il fallimento non blocca il serve."""
    try:
        c.execute("UPDATE autopaths SET ts_last_used = ? WHERE id = ?",
                  (now_iso_z(), autopath_id))
        c.commit()
    except sqlite3.Error as ex:
        log.debug("autopath._touch_served noop: %r", ex)


def lookup(query: str, intent: Intent) -> Optional[AutopathHit]:
    """Tenta match autopath cached. Cluster semantic-first poi intent_hash fallback.

    Ritorna AutopathHit o None.
    """
    if not intent.is_complete():
        return None
    _, ihash = _compute_intent_sig(intent)
    _qobj = (intent.object or "").lower().strip()
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
                # ORDER BY deterministico (1/7): con PIÙ champion attivi nello
                # stesso cluster (post fix-collisione id) vince il migliore per
                # merito, non l'ordine fisico delle righe (§11 determinismo).
                row = c.execute(
                    "SELECT id, framework_json, uses, composite_score, intent_sig, "
                    "tools_sig, pool_sig "
                    "FROM autopaths WHERE cluster_id = ? AND status = 'active' "
                    "AND champion = 1 ORDER BY composite_score DESC, "
                    "ok_count DESC, uses DESC, id LIMIT 1",
                    (best_cid,)).fetchone()
                # CONFINE OGGETTO (16/6, turn 9805fb61/af045d18/1175b2f8): il
                # match cluster e' puramente cosine sul TESTO → una query
                # «fatture sulla mail» (object=messages) cade vicino al cluster
                # di un autopath object=files e ne erediterebbe il piano
                # (read_files_csv con path inventato) → misroute cross-oggetto
                # che BYPASSA il proposer (llm_out_tokens=0). L'object dell'intent
                # e' un confine di CATEGORIA: un piano `files` NON serve una query
                # `messages`. §7.9 deterministico (path 2 gia' vincola via ihash).
                if (row and not _is_query_specific(row[1])
                        and _sig_object(row[4]) == _qobj):
                    fw = Framework.from_dict(json.loads(row[1]))
                    _touch_served(c, row[0])
                    return AutopathHit(autopath_id=row[0], framework=fw,
                                        cluster_id=best_cid, uses=row[2],
                                        composite_score=row[3] or 0.5,
                                        tools_sig=row[5] or "",
                                        pool_sig=row[6] or "")
        # 2. Intent hash fallback (exact) + COSINE-FLOOR di pertinenza (14/6).
        # L'intent_hash (verb|object) coincide anche fra query con SLOT diversi:
        # esige che la query sia cosine ≥ FLOOR al cluster dell'autopath servito,
        # altrimenti astieniti (None → engine pieno). Difesa-in-profondita' a
        # monte: anche se il piano fosse servito, b8e10be ri-risolverebbe gli
        # slot — qui evitiamo proprio di servire un champion semanticamente
        # distante. Floor saltato se manca l'embedding o il cluster (no segnale).
        row = c.execute(
            "SELECT id, framework_json, cluster_id, uses, composite_score, "
            "intent_sig, tools_sig, pool_sig "
            "FROM autopaths WHERE intent_hash = ? AND status = 'active' "
            "AND champion = 1 ORDER BY composite_score DESC, ok_count DESC, "
            "uses DESC, id LIMIT 1", (ihash,)).fetchone()
        # CONFINE OGGETTO anche su path-2 (D3-D, 18/6): gemello del path-1.
        # L'ihash e' ora compound-aware (encode tutti gli object), ma il confine
        # esplicito sull'object PRIMARIO e' difesa-in-profondita' contro le
        # collisioni residue del troncamento sha256-16. No-op se l'object
        # combacia (caso normale).
        if (row and not _is_query_specific(row[1])
                and _sig_object(row[5]) == _qobj):
            ap_cluster = row[2]
            if eb and ap_cluster:
                if _max_cosine_to_cluster(c, eb, ap_cluster) < COSINE_FLOOR_INTENT:
                    return None
            fw = Framework.from_dict(json.loads(row[1]))
            _touch_served(c, row[0])
            return AutopathHit(autopath_id=row[0], framework=fw,
                                cluster_id=ap_cluster or "", uses=row[3],
                                composite_score=row[4] or 0.5,
                                tools_sig=row[6] or "",
                                pool_sig=row[7] or "")
    finally:
        c.close()
    return None


# ── Observation recording ─────────────────────────────────────────────────

def record_observation(*, turn_id: str, intent: Intent, framework: Framework,
                        query: str = "", latency_ms: int = 0,
                        catalog=None) -> str:
    """Registra turno per future promote/demote."""
    sig, ihash = _compute_intent_sig(intent)
    fhash = compute_framework_hash(framework)
    fjson = json.dumps(framework.to_dict(), ensure_ascii=False)
    eb = _cluster.embed(query) if query else None
    cid = None
    if eb:
        cid = _assign_cluster(eb)
    ts = now_iso_z()
    # ADR 0182: firma del mondo AL MOMENTO del turno; la promozione la copia
    # sull'autopath. Best-effort (sig vuote = MISS al primo hit, poi refresh).
    try:
        from .cache_validity import plan_sigs
        # ADR 0182: firma contro il mondo del CHIAMANTE (vedi fastpath).
        _tsig, _psig = plan_sigs(framework, intent, catalog)
    except Exception as ex:  # noqa: BLE001
        log.warning("autopath: plan_sigs fallita (sig vuote): %r", ex)
        _tsig, _psig = "", ""
    try:
        with closing(_conn()) as c:
            c.execute(
                "INSERT INTO observations(turn_id, intent_hash, intent_sig, "
                "framework_json, framework_hash, cluster_id, embedding, "
                "latency_ms, ts, tools_sig, pool_sig) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (turn_id, ihash, sig, fjson, fhash, cid, eb, latency_ms, ts,
                 _tsig, _psig))
            c.commit()
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
      ok     → maybe promote (≥MIN_OBS_PROMOTE stesso framework_hash → autopath)
               + LWW remove anti_autopath matching
      fail   → fail_count++ + maybe anti_autopath (≥3 fail)
      repeat → soft anti_autopath TTL 1h (caller re-propose via recovery)
    """
    if verdict not in ("ok", "fail", "repeat"):
        return {"ok": False, "reason": "bad_verdict"}
    c = None
    try:
        c = _conn()
        row = c.execute(
            "SELECT intent_hash, intent_sig, framework_json, framework_hash, "
            "cluster_id, latency_ms, tools_sig, pool_sig "
            "FROM observations WHERE turn_id = ? "
            "ORDER BY id DESC LIMIT 1", (turn_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "no_observation"}
        ihash, sig, fjson, fhash, cid, lat, _tsig, _psig = row
        ts = now_iso_z()
        c.execute("UPDATE observations SET verdict = ?, verdict_ts = ? "
                  "WHERE turn_id = ?", (verdict, ts, turn_id))
        out: dict = {"ok": True, "verdict": verdict,
                      "intent_hash": ihash, "framework_hash": fhash}
        if verdict == "ok":
            # LWW remove anti-autopath
            rm = c.execute(
                "DELETE FROM anti_autopaths WHERE intent_hash = ? "
                "AND framework_hash = ?", (ihash, fhash)).rowcount
            if rm:
                out["anti_autopath_removed"] = rm
                c.execute(
                    "UPDATE autopaths SET status = 'active' "
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
                    autopath_id = _promote_autopath(c, ihash, sig, fhash, fjson, cid, ts,
                                                    tools_sig=_tsig or "", pool_sig=_psig or "")
                    if autopath_id:
                        out["promoted_autopath_id"] = autopath_id
        elif verdict == "fail":
            # Anti-autopath se 3+ fail
            n_fail = c.execute(
                "SELECT COUNT(*) FROM observations "
                "WHERE intent_hash = ? AND framework_hash = ? "
                "AND verdict = 'fail'", (ihash, fhash)).fetchone()[0]
            if n_fail >= 3:
                ttl = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(time.time() + TTL_ANTIAUTOPATH_SECS))
                c.execute(
                    "INSERT INTO anti_autopaths(intent_hash, framework_hash, "
                    "fail_count, ttl_expires_at, reason, ts_last_fail) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO UPDATE SET "
                    "fail_count = anti_autopaths.fail_count + 1, "
                    "ttl_expires_at = excluded.ttl_expires_at, "
                    "ts_last_fail = excluded.ts_last_fail",
                    (ihash, fhash, n_fail, ttl, f"feedback_fail:{n_fail}", ts))
                # Demote autopath. ts_last_used = ora (2/7/2026): la finestra
                # `demoted <30gg` del prune decorre da ts_last_used — senza
                # questo touch decorreva dall'ULTIMO promote (riattivazione
                # LWW a ridosso del demote ~impossibile).
                c.execute(
                    "UPDATE autopaths SET status = 'demoted', ts_last_used = ? "
                    "WHERE intent_hash = ? AND framework_hash = ?",
                    (ts, ihash, fhash))
                out["anti_autopath_added"] = True
        elif verdict == "repeat":
            # Soft anti-autopath TTL breve (1h): il framework è stato ri-proposto
            # ma l'utente ha chiesto un retry → escludilo temporaneamente cosi'
            # il caller (recovery) ri-propone una shape diversa. Riusa lo stesso
            # path di insert anti_autopath del ramo `fail`, con TTL corto.
            ttl = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(time.time() + TTL_ANTIAUTOPATH_REPEAT_SECS))
            c.execute(
                "INSERT INTO anti_autopaths(intent_hash, framework_hash, "
                "fail_count, ttl_expires_at, reason, ts_last_fail) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO UPDATE SET "
                "fail_count = anti_autopaths.fail_count + 1, "
                "ttl_expires_at = excluded.ttl_expires_at, "
                "ts_last_fail = excluded.ts_last_fail",
                (ihash, fhash, 1, ttl, "feedback_repeat", ts))
            out["anti_autopath_repeat"] = True
        c.commit()
        return out
    except Exception as ex:
        log.warning("autopath.record_feedback: %r", ex)
        return {"ok": False, "reason": str(ex)}
    finally:
        if c is not None:
            c.close()


def _promote_autopath(c, ihash: str, sig: str, fhash: str, fjson: str,
                    cid: Optional[str], ts: str, *,
                    tools_sig: str = "", pool_sig: str = "",
                    shadow: int = 0) -> Optional[str]:
    """Crea autopath ACTIVE se non già presente. Ritorna autopath_id.

    L'id include ihash+fhash (1/7/2026): il vecchio `{sig[:40]}_v1.0.0` era
    PRIMARY KEY ma NON dipendeva dal framework — lo stesso intent con un
    framework NUOVO (champion vecchio demotato, piano migliore appreso)
    collideva sull'id e l'INSERT OR IGNORE lo scartava IN SILENZIO, riportando
    comunque `promoted_autopath_id` (§2.8 violata: il piano nuovo non diventava
    mai autopath). Le righe esistenti restano valide: il lookup non dipende dal
    formato dell'id."""
    base = sig.replace("|", "_")[:40] or "autopath"
    autopath_id = f"{base}_{ihash[:6]}{fhash[:6]}"
    existing = c.execute(
        "SELECT id FROM autopaths WHERE intent_hash = ? AND framework_hash = ?",
        (ihash, fhash)).fetchone()
    if existing:
        # ADR 0182: la ri-promozione RINFRESCA anche le firme del mondo —
        # senza, una riga pre-migrazione (sig vuota) resterebbe invalida per
        # sempre malgrado il nuovo feedback ✓ su un turno del mondo corrente.
        # W1: la promozione da FEEDBACK UMANO (shadow=0) conferma un seed
        # shadow → champion pieno; una ri-semina (shadow=1) non degrada mai
        # una riga già confermata.
        c.execute("UPDATE autopaths SET uses = uses + 1, ok_count = ok_count + 1, "
                  "ts_last_used = ?, "
                  "tools_sig = CASE WHEN ? != '' THEN ? ELSE tools_sig END, "
                  "pool_sig  = CASE WHEN ? != '' THEN ? ELSE pool_sig END, "
                  "shadow    = CASE WHEN ? = 0 THEN 0 ELSE shadow END "
                  "WHERE id = ?",
                  (ts, tools_sig, tools_sig, pool_sig, pool_sig,
                   shadow, existing[0]))
        return existing[0]
    cur = c.execute(
        "INSERT OR IGNORE INTO autopaths(id, intent_sig, intent_hash, cluster_id, "
        "framework_json, framework_hash, status, uses, ok_count, "
        "ts_created, ts_last_used, tools_sig, pool_sig, shadow) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active', 1, 1, ?, ?, ?, ?, ?)",
        (autopath_id, sig, ihash, cid, fjson, fhash, ts, ts,
         tools_sig, pool_sig, shadow))
    if cur.rowcount == 0:
        # Collisione id residua (2⁻⁴⁸: stesso prefisso ihash+fhash di un'ALTRA
        # coppia): l'IGNORE ha scartato l'insert — §2.8, non dichiarare una
        # promozione mai avvenuta.
        log.warning("autopath._promote_autopath: collisione id %r, insert "
                    "scartato", autopath_id)
        return None
    return autopath_id


SEED_STEPS = int(os.environ.get("METNOS_SEED_STEPS", "4"))
SEED_REPEAT = int(os.environ.get("METNOS_SEED_REPEAT", "2"))


def seed_from_run(*, intent: Intent, framework: Framework,
                  n_steps: int, catalog=None) -> Optional[str]:
    """W1 learning-loop (ADR 0185): semina un autopath SHADOW da turni engine
    riusciti e COSTOSI, senza aspettare il feedback ✓ umano.

    Condizioni (tutte, deterministiche — [[feedback-no-training-amplify-reality]]:
    si amplifica un'esecuzione REALE ripetuta, niente ML):
      - il turno ha n_steps >= SEED_STEPS (default 4: sotto, il cold-start
        engine costa poco e il ✓ umano resta l'unica via);
      - lo STESSO INTENT è stato osservato con successo almeno SEED_REPEAT
        volte (default 2). Il conteggio è per intent, NON per coppia
        (intent, framework): le parafrasi producono piani leggermente
        diversi dal proposer (misurato 6/7) e la coppia identica non
        ricorre quasi mai — scegliere il piano fra varianti è il ruolo
        champion/challenger di L1. Si semina il framework del RUN CORRENTE
        (l'ultimo successo osservato);
      - nessun autopath ACTIVE esiste già per l'intent.
    Il seed entra `shadow=1`: servito come hit normale (guard 0174 + firme
    0182 lo validano a lettura), il primo ✓ umano lo conferma champion.
    Ritorna autopath_id o None (condizioni non soddisfatte)."""
    if n_steps < SEED_STEPS:
        return None
    sig, ihash = _compute_intent_sig(intent)
    fhash = compute_framework_hash(framework)
    c = _conn()
    try:
        if c.execute("SELECT 1 FROM autopaths WHERE intent_hash = ? "
                     "AND status = 'active' LIMIT 1", (ihash,)).fetchone():
            return None
        n_obs = c.execute(
            "SELECT COUNT(*) FROM observations WHERE intent_hash = ?",
            (ihash,)).fetchone()[0]
        if n_obs < SEED_REPEAT:
            return None
        row = c.execute(
            "SELECT framework_json, cluster_id, tools_sig, pool_sig "
            "FROM observations WHERE intent_hash = ? AND framework_hash = ? "
            "ORDER BY id DESC LIMIT 1", (ihash, fhash)).fetchone()
        if not row:
            return None
        fjson, cid, _tsig, _psig = row
        ap_id = _promote_autopath(
            c, ihash, sig, fhash, fjson, cid, now_iso_z(),
            tools_sig=_tsig or "", pool_sig=_psig or "", shadow=1)
        c.commit()
        if ap_id:
            log.info("[learning-loop] autopath SHADOW seminato %s "
                     "(n_obs=%d, n_steps=%d)", ap_id, n_obs, n_steps)
        return ap_id
    finally:
        c.close()


# ── Anti-autopath check (per Proposer exclusion) ─────────────────────────────

def excluded_framework_hashes(intent: Intent) -> set[str]:
    """Anti-autopaths attivi (TTL non scaduto) per intent."""
    _, ihash = _compute_intent_sig(intent)
    ts = now_iso_z()
    try:
        c = _conn()
        rows = c.execute(
            "SELECT framework_hash FROM anti_autopaths "
            "WHERE intent_hash = ? AND ttl_expires_at > ?",
            (ihash, ts)).fetchall()
        c.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


# ── Introspezione read-only (admin UI /admin/praxis) ──────────────────────
# Sola lettura: NESSUNA logica di promote/lookup/demote. Colonne esplicite
# (niente SELECT * → embedding BLOB resta fuori dal payload UI).

_AUTOPATH_COLS = ("id", "intent_sig", "intent_hash", "cluster_id", "status",
               "uses", "ok_count", "fail_count", "composite_score",
               "champion", "ts_created", "ts_last_used")

_OBS_COLS = ("id", "turn_id", "intent_hash", "intent_sig", "framework_json",
             "framework_hash", "cluster_id", "verdict", "verdict_ts",
             "latency_ms", "ts", "promoted_to")

_ANTI_COLS = ("intent_hash", "framework_hash", "fail_count",
              "ttl_expires_at", "reason", "ts_last_fail")


def stats() -> dict:
    """Aggregati per la dashboard admin."""
    now = now_iso_z()
    c = _conn()
    try:
        by_status = dict(c.execute(
            "SELECT status, COUNT(*) FROM autopaths GROUP BY status").fetchall())
        obs_total = c.execute(
            "SELECT COUNT(*) FROM observations").fetchone()[0]
        anti_active = c.execute(
            "SELECT COUNT(*) FROM anti_autopaths WHERE ttl_expires_at > ?",
            (now,)).fetchone()[0]
        return {"autopaths_by_status": by_status,
                "observations_total": obs_total,
                "anti_autopaths_active": anti_active}
    finally:
        c.close()


def list_autopaths(status: str = "active", limit: int = 50) -> list[dict]:
    """Autopath per status, le piu' usate prima."""
    c = _conn()
    try:
        rows = c.execute(
            f"SELECT {', '.join(_AUTOPATH_COLS)} FROM autopaths "
            "WHERE status = ? ORDER BY uses DESC, ts_last_used DESC LIMIT ?",
            (status, int(limit))).fetchall()
        return [dict(zip(_AUTOPATH_COLS, r)) for r in rows]
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


def active_anti_autopaths(limit: int = 20) -> list[dict]:
    """Anti-autopath con TTL non scaduto, fail piu' recenti prima."""
    now = now_iso_z()
    c = _conn()
    try:
        rows = c.execute(
            f"SELECT {', '.join(_ANTI_COLS)} FROM anti_autopaths "
            "WHERE ttl_expires_at > ? ORDER BY ts_last_fail DESC LIMIT ?",
            (now, int(limit))).fetchall()
        return [dict(zip(_ANTI_COLS, r)) for r in rows]
    finally:
        c.close()


# NB: `demote_autopath_for_query` (LWW utente-prevale su approvazione manuale
# fastpath) RIMOSSA 11/6/2026: serviva il bottone «approva fast-path» mai
# implementato; con l'auto-produzione L0 (nessun consenso esplicito) il demote
# L1 non ha base — L0 vince comunque in cascata sulla query esatta, la autopath
# L1 resta utile per le sorelle del cluster.
