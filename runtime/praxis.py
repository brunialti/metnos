"""Praxis — cognitive memory layer + workflow orchestrator (Metnos cognition engine).

Praxis (πρᾶξις, "pratica appresa") e' il modulo memoria della triade greca
che governa la cognizione di Metnos:

  Mētis  (Μῆτις, consiglio strategico)  → praxis_propose.py (LLM 1-shot)
  Noûs   (νοῦς, intelletto puro)         → praxis_executor.py (deterministico)
  Praxis (πρᾶξις, pratica appresa)       → praxis.py (questo modulo, sqlite)

Nel mito greco Zeus ingoiò Mētis per averla sempre dentro come consigliera
silenziosa. Allo stesso modo Praxis ha ingoiato il PLANNER step-by-step
di Metnos (~6500 LOC fragili, LLM chiamato 5-7 volte/turn): il LLM vive
ancora come Mētis dentro Praxis, ma parla solo quando la cache non sa.
Ogni feedback ✓ consolida una skill → Mētis tace progressivamente →
Metnos diventa il proprio consigliere.

ADR 0161 documenta la genesi e il mito.

Praxis (πρᾶξις, "pratica appresa") sostituisce il PLANNER step-by-step con:
- Cache deterministica di workflow appresi da feedback utente (✓✗↻).
- Lookup O(1) per (verb, object) + keywords, fallback Jaccard fuzzy.
- LLM 1-shot framework proposer per workflow non-cached (in praxis_propose.py).
- Execution engine deterministica con LLM fast tier solo per filler args.

Lifecycle 8 stadi (ADR 0161):
1. OBSERVE — agent_runtime registra ogni turn (intent_sig + framework + latency).
2. MATCH   — next turn: try_match → cache hit O(1) → execute deterministico.
3. PROPOSE — cache miss → praxis_propose.LLM 1-shot → execute.
4. FEEDBACK — chat ✓→promote / ✗→anti_skill / ↻→exclude+retry.
5. PROMOTE — 3+ ✓ stessi (intent_hash, framework_hash) + success_rate≥80% → skill active.
6. DECAY   — skill non usata 30gg → archived.
7. ANTI_TTL — anti_skill TTL 30gg, re-prova path vecchio dopo.
8. EVOLVE  — skill esistente con stats peggiorati → propose v2 via L_propose.

Filosofia (§7.9, §7.3, §2.2):
- Storage sqlite hash O(1), no scan.
- Keywords deterministiche §7.9 (regex+stopword, no LLM).
- intent_sig universale: funziona per QUALSIASI dominio (mail, file, web, ecc.).
- Vocab compositional §2.2: id skill = <verb>_<object>[_<qualifier>]_vN.M.K.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import config as _C
except Exception:
    from runtime import config as _C  # pragma: no cover

log = logging.getLogger(__name__)

DB_PATH = _C.PATH_USER_DATA / "praxis.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
  id              TEXT PRIMARY KEY,
  intent_sig      TEXT NOT NULL,
  intent_hash     TEXT NOT NULL,
  keywords_csv    TEXT NOT NULL DEFAULT '',
  framework_json  TEXT NOT NULL,
  source          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  uses            INTEGER NOT NULL DEFAULT 0,
  ok_count        INTEGER NOT NULL DEFAULT 0,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  avg_latency_ms  INTEGER NOT NULL DEFAULT 0,
  alignment_score REAL    NOT NULL DEFAULT 0.0,
  ts_created      TEXT    NOT NULL,
  ts_last_used    TEXT,
  born_from       TEXT,
  version         TEXT    NOT NULL DEFAULT 'v1.0.0',
  UNIQUE(intent_sig, version)
);
CREATE INDEX IF NOT EXISTS idx_skills_hash ON skills(intent_hash, status);
CREATE INDEX IF NOT EXISTS idx_skills_last ON skills(ts_last_used DESC);

CREATE TABLE IF NOT EXISTS anti_skills (
  intent_hash     TEXT NOT NULL,
  framework_hash  TEXT NOT NULL,
  fail_count      INTEGER NOT NULL DEFAULT 1,
  ttl_expires_at  TEXT NOT NULL,
  reason          TEXT,
  ts_last_fail    TEXT NOT NULL,
  PRIMARY KEY (intent_hash, framework_hash)
);
CREATE INDEX IF NOT EXISTS idx_anti_ttl ON anti_skills(ttl_expires_at);

CREATE TABLE IF NOT EXISTS filler_cache (
  intent_hash     TEXT NOT NULL,
  filler_name     TEXT NOT NULL,
  value           TEXT NOT NULL,
  uses            INTEGER NOT NULL DEFAULT 1,
  ts_first        TEXT NOT NULL,
  ts_last         TEXT NOT NULL,
  PRIMARY KEY (intent_hash, filler_name)
);
CREATE INDEX IF NOT EXISTS idx_filler_last ON filler_cache(ts_last DESC);

CREATE TABLE IF NOT EXISTS observations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id         TEXT NOT NULL,
  intent_hash     TEXT NOT NULL,
  intent_sig      TEXT NOT NULL,
  framework_json  TEXT NOT NULL,
  framework_hash  TEXT NOT NULL,
  verdict         TEXT,
  verdict_ts      TEXT,
  latency_ms      INTEGER,
  ts              TEXT NOT NULL,
  promoted_to     TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_intent ON observations(intent_hash);
CREATE INDEX IF NOT EXISTS idx_obs_turn   ON observations(turn_id);
CREATE INDEX IF NOT EXISTS idx_obs_verdict ON observations(verdict, intent_hash);
"""

_STOPWORDS_IT = frozenset({
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "che", "chi", "cui", "come", "se", "ma", "ed", "ad",
    "del", "della", "dei", "degli", "delle",
    "al", "alla", "ai", "agli", "alle",
    "nel", "nella", "nei", "negli", "nelle",
    "mi", "ti", "ci", "vi", "si", "mio", "tuo", "suo", "sua", "tua", "mia",
    "li", "ne", "ce", "ve", "lui", "lei",
    "non", "anche", "solo", "ora", "qui", "li", "quello", "questo",
    "quella", "questi", "queste", "quelli", "quelle",
    "essere", "avere", "fare", "molto", "poco", "tanto",
    "sulla", "sullo", "sulle", "sui",
    "ed", "od", "le",
})
_STOPWORDS_EN = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "my", "your", "his", "her", "its",
    "i", "you", "he", "she", "it", "we", "they", "them", "us",
    "do", "does", "did", "have", "has", "had", "will", "would", "could",
    "from", "as", "if", "so", "than", "then", "now", "very", "just",
})
_TOKEN_RE = re.compile(r"\b[\w\-]{2,}\b", re.UNICODE)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Helpers deterministici §7.9 ─────────────────────────────────────────

def extract_keywords(query: str, top_n: int = 5) -> list[str]:
    """Token significativi (post-stopword, sorted, dedup). §7.9 no LLM."""
    if not query:
        return []
    tokens = [t.lower() for t in _TOKEN_RE.findall(query)]
    stop = _STOPWORDS_IT | _STOPWORDS_EN
    keep = sorted({t for t in tokens if t not in stop and len(t) >= 3})
    return keep[:top_n]


def compute_intent_sig(verb: str, obj: str, keywords: list[str]) -> tuple[str, str]:
    """Ritorna (intent_sig, intent_hash).

    intent_sig leggibile (include keywords per debug).
    intent_hash basato SOLO su (verb, object) per bucketing ampio — keywords
    variano lessicalmente fra query semanticamente equivalenti (es. "quanti
    file /tmp" vs "conta file /tmp") e bloccherebbero ogni promozione (bucket
    di 1). Bucket per coppia (verb, object) = 23×19 = 437 cluster massimi.
    """
    verb = (verb or "").lower().strip()
    obj = (obj or "").lower().strip()
    kw_norm = sorted(set(k.lower().strip() for k in (keywords or []) if k))
    sig = f"{verb}|{obj}|{'_'.join(kw_norm)}"
    h = hashlib.sha256(f"{verb}|{obj}".encode("utf-8")).hexdigest()[:16]
    return sig, h


def compute_framework_hash(framework: dict) -> str:
    """Hash della SHAPE del framework: tool sequence + args keys + template.

    Include `final_message` template (ADR 0162 ext, 26/5/2026): permette a
    Mētis di rigenerare almeno il template anche quando la pipeline minima
    e' semanticamente univoca (es. "quanti file in X" → find_files+final
    e' shape unica). Senza template nel hash, ↻ exclude={fw_hash} non puo'
    produrre framework diverso.

    Cosi' diversi valori dst_folder NON formano framework distinti — la
    struttura e' la stessa, solo il filler cambia. Anti_skill matcha per
    shape+template, non valore di filler.
    """
    steps = framework.get("steps") or []
    minimal = {
        "steps": [
            {
                "tool": s.get("tool"),
                "args_keys": sorted((s.get("args") or {}).keys()),
            }
            for s in steps if isinstance(s, dict)
        ],
        "template": (framework.get("final_message") or "").strip(),
    }
    payload = json.dumps(minimal, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ── LLM judge helper (Gemma 26B middle tier per ClusterLLM) ─────────────

def _get_middle_llm_call():
    """Ritorna callable (system, user, max_tokens, think) → text per
    praxis_cluster.judge_same_intent. Lazy import per evitare cicli."""
    def _call(system: str, user: str, max_tokens: int = 10,
              think: bool = False) -> str:
        try:
            from llm_router import LLMRouter
            r = LLMRouter()
            res = r.chat(system, user, tier="middle",
                          max_tokens=max_tokens, think=think)
            return (res.text or "") if res else ""
        except Exception as ex:
            log.warning("middle llm call failed: %r", ex)
            return ""
    return _call


# ── Store ───────────────────────────────────────────────────────────────

class PraxisStore:
    """Memoria cognitiva: skill cached + lookup O(1) + feedback loop."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        # ClusterLLM extension (ADR 0161 ext, 26/5/2026): embedding +
        # cluster_id + composite_score + champion columns.
        try:
            import praxis_cluster
            praxis_cluster.ensure_schema(self.conn)
        except Exception as ex:
            log.warning("praxis_cluster.ensure_schema failed: %r", ex)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ── MATCH ─────────────────────────────────────────────────────────

    def try_match(self, verb: str, obj: str, keywords: list[str],
                   *, exclude_framework_hashes: Optional[set[str]] = None,
                   query: str = ""
                   ) -> Optional[dict]:
        """Match: ClusterLLM semantic (preferred) → intent_hash exact → Jaccard.

        Returns:
          None se nessuna skill matching (caller fa L_propose).
          dict {skill_id, framework, match_kind, stats} se hit.

        Match algorithm (ADR 0161 ext, 26/5/2026):
          0. ClusterLLM: embed(query) → top-1 cosine ≥ COSINE_HIGH → cluster
             match → champion skill di quel cluster.
          1. Exact intent_hash match (legacy, lessicale).
          2. Fallback: stesso (verb, object), Jaccard keywords ≥ 0.9.
        """
        if not verb or not obj:
            return None
        sig, h = compute_intent_sig(verb, obj, keywords)
        excl = exclude_framework_hashes or set()

        # 0. Cluster lookup semantico (preferito).
        if query:
            try:
                import praxis_cluster
                eb = praxis_cluster.embed(query)
                if eb:
                    nbrs = praxis_cluster.find_neighbors(self.conn, eb, k=5)
                    if nbrs and nbrs[0][0] >= praxis_cluster.COSINE_HIGH:
                        cluster_id = nbrs[0][3]
                        skill = praxis_cluster.select_skill_for_cluster(
                            self.conn, cluster_id)
                        if skill:
                            fw_hash = compute_framework_hash(skill["framework"])
                            if fw_hash not in excl:
                                self._touch_skill(skill["skill_id"])
                                return {
                                    "skill_id": skill["skill_id"],
                                    "framework": skill["framework"],
                                    "match_kind": "cluster_exact",
                                    "stats": skill["stats"],
                                }
            except Exception as ex:
                log.warning("praxis.try_match cluster_lookup: %r", ex)

        # 1. Exact hash match (best, O(1))
        cur = self.conn.execute(
            "SELECT id, framework_json, uses, ok_count, fail_count, alignment_score "
            "FROM skills WHERE intent_hash = ? AND status IN ('active','shadow') "
            "ORDER BY ok_count DESC, ts_last_used DESC LIMIT 5",
            (h,),
        )
        for sid, fw_json, uses, oks, fails, align in cur:
            framework = json.loads(fw_json)
            fw_hash = compute_framework_hash(framework)
            if fw_hash in excl:
                continue
            self._touch_skill(sid)
            return {
                "skill_id": sid,
                "framework": framework,
                "match_kind": "exact",
                "stats": {"uses": uses, "ok": oks, "fail": fails,
                          "alignment": align},
            }

        # 2. Fuzzy fallback DISABILITATO di default (25/5/2026 v2):
        # Jaccard 0.5 troppo aggressivo: query semanticamente diverse ma
        # con verb+object identico (es. "foto al mare" vs "foto enrollment")
        # matchavano la stessa skill → args literal sbagliati. Soglia 0.9
        # tiene solo match quasi-esatti.
        # Per re-abilitare match piu' largo: env METNOS_PRAXIS_FUZZY_THRESHOLD.
        import os as _os
        _fuzzy_thr = float(_os.environ.get(
            "METNOS_PRAXIS_FUZZY_THRESHOLD", "0.9"))
        cur = self.conn.execute(
            "SELECT id, keywords_csv, framework_json, uses, ok_count, fail_count, "
            "alignment_score FROM skills WHERE intent_sig LIKE ? "
            "AND status IN ('active','shadow')",
            (f"{verb}|{obj}|%",),
        )
        best: Optional[tuple] = None
        best_score = 0.0
        kw_set = set(k.lower() for k in keywords)
        for sid, kw_csv, fw_json, uses, oks, fails, align in cur:
            framework = json.loads(fw_json)
            fw_hash = compute_framework_hash(framework)
            if fw_hash in excl:
                continue
            cand_kw = {k for k in (kw_csv or "").split(",") if k}
            if not cand_kw and not kw_set:
                jacc = 1.0  # entrambe vuote — match per verb+object soli
            elif not cand_kw or not kw_set:
                jacc = 0.0
            else:
                jacc = len(kw_set & cand_kw) / len(kw_set | cand_kw)
            if jacc < _fuzzy_thr:
                continue
            success = oks / max(1, oks + fails)
            score = jacc * 0.2 + success * 0.5 + align * 0.3
            if score > best_score:
                best_score = score
                best = (sid, framework, uses, oks, fails, align, jacc)
        if best:
            sid, framework, uses, oks, fails, align, jacc = best
            self._touch_skill(sid)
            return {
                "skill_id": sid,
                "framework": framework,
                "match_kind": "fuzzy",
                "stats": {"uses": uses, "ok": oks, "fail": fails,
                          "alignment": align, "jaccard": jacc,
                          "score": best_score},
            }
        return None

    def _touch_skill(self, skill_id: str) -> None:
        """Aggiorna SOLO ts_last_used. uses/ok_count/fail_count sono
        gestiti da praxis_cluster.update_skill_metrics su feedback esplicito
        (ADR 0161 ext, 26/5/2026): separazione lookup-hit (no counter) vs
        execution outcome (counters incrementati con verdict)."""
        self.conn.execute(
            "UPDATE skills SET ts_last_used = ? WHERE id = ?",
            (_utcnow(), skill_id),
        )
        self.conn.commit()

    # ── Retry on repeat ───────────────────────────────────────────────

    def _is_framework_idempotent(self, framework: dict) -> bool:
        """True se nessuno step e' un verb mutating (move/delete/send/...).
        §2.3 reverse_pattern + §2.1 mutating semantica."""
        _MUTATING = {"move", "delete", "send", "write", "create", "set",
                     "change", "share", "order", "extract", "compress",
                     "render"}
        for s in framework.get("steps", []) or []:
            tool = (s.get("tool") or "").lower()
            verb = tool.split("_", 1)[0] if "_" in tool else tool
            if verb in _MUTATING:
                return False
        return True

    def retry_on_repeat(self, turn_id: str, fw_hash: str,
                          intent_hash: str) -> Optional[dict]:
        """Mētis re-propose framework alternativo INLINE (no execute).
        Salva nuovo framework su skill esistente + soft anti_skill TTL 1h
        sul vecchio fw_hash. Al prossimo lookup cluster, restituisce skill
        con framework nuovo (senza nuovo costo Mētis). Re-execute lasciato
        al prossimo turn utente (evita side-effects su framework mutating).

        Returns: {new_fw_hash, anti_added} se ok, None se fallisce.
        """
        try:
            from turn_feedback import _load_turn
            turn = _load_turn(turn_id) or {}
            query = turn.get("user_query") or turn.get("query") or ""
            if not query:
                return None
            cur = self.conn.execute(
                "SELECT intent_sig, cluster_id FROM observations "
                "WHERE turn_id = ?", (turn_id,))
            row = cur.fetchone()
            if not row:
                return None
            sig, cluster_id = row
            try:
                verb, obj, kw = sig.split("|", 2)
            except ValueError:
                verb, obj, kw = sig, "", ""
            keywords = [k for k in kw.split("_") if k]
        except Exception as ex:
            log.warning("retry_on_repeat: load turn: %r", ex)
            return None
        # Mētis re-propose con exclude.
        try:
            import praxis_propose
            from llm_router import LLMRouter
            router = LLMRouter()
            def _wise(system, user, **kwargs):
                res = router.chat(system, user, tier="wise", **kwargs)
                return (res.text or "") if res else ""
            intent = {"verb": verb, "object": obj, "keywords": keywords}
            tools = []
            try:
                from loader import load_catalog
                tools = [e.name for e in load_catalog()]
            except Exception:
                pass
            new_fw = praxis_propose.propose_framework(
                query=query, intent=intent, available_tools=tools,
                excluded_frameworks={fw_hash},
                llm_call=_wise,
            )
            if not new_fw:
                return None
            new_fw_hash = compute_framework_hash(new_fw)
        except Exception as ex:
            log.warning("retry_on_repeat: Mētis: %r", ex)
            return None
        # Update skill esistente (se cluster ha skill) con nuovo framework.
        try:
            if cluster_id:
                cur = self.conn.execute(
                    "SELECT id FROM skills WHERE cluster_id = ? "
                    "AND framework_hash = ? AND status='active' LIMIT 1",
                    (cluster_id, fw_hash))
                sk_row = cur.fetchone()
                if sk_row:
                    self.conn.execute(
                        "UPDATE skills SET framework_json = ?, "
                        "framework_hash = ?, ts_last_used = ? WHERE id = ?",
                        (json.dumps(new_fw, ensure_ascii=False),
                         new_fw_hash, _utcnow(), sk_row[0]))
                    self.conn.commit()
                    try:
                        import praxis_cluster
                        praxis_cluster.log_skill_version(
                            self.conn, sk_row[0], "retry_repeat",
                            old_fw_hash=fw_hash, new_fw_hash=new_fw_hash,
                            reason="user_repeat")
                    except Exception:
                        pass
        except Exception as ex:
            log.warning("retry_on_repeat: save: %r", ex)
        # Soft anti_skill TTL (constants centralizzati).
        try:
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            from praxis_constants import TTL_REPEAT_SOFT_SECS
            ttl = (_dt.now(_tz.utc) + _td(seconds=TTL_REPEAT_SOFT_SECS)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.conn.execute(
                "INSERT INTO anti_skills(intent_hash, framework_hash, "
                "fail_count, ttl_expires_at, reason, ts_last_fail) "
                "VALUES (?, ?, 1, ?, 'repeat_soft', ?) "
                "ON CONFLICT(intent_hash, framework_hash) DO UPDATE "
                "SET ttl_expires_at = excluded.ttl_expires_at, "
                "reason = 'repeat_soft', ts_last_fail = excluded.ts_last_fail",
                (intent_hash, fw_hash, ttl, _utcnow()))
            self.conn.commit()
        except Exception as ex:
            log.warning("retry_on_repeat: anti_soft: %r", ex)
        return {"new_fw_hash": new_fw_hash, "anti_added": True}

    # ── Template refresh on format_fail ───────────────────────────────

    def _refresh_template_on_format_fail(self, skill_id: str,
                                          turn_id: str,
                                          old_fw_hash: str) -> Optional[str]:
        """Mētis re-propose framework escludendo old_fw_hash. Aggiorna skill.
        Ritorna new framework_hash se riuscito, None altrimenti.
        Chiamato post format_fail. Latency ~2-3s, in-line (caller già
        post-feedback ✗, UX non bloccata da turno corrente)."""
        # Recupera contesto turno + observation.
        try:
            from turn_feedback import _load_turn
            turn = _load_turn(turn_id) or {}
            query = turn.get("user_query") or turn.get("query") or ""
            if not query:
                return None
            cur = self.conn.execute(
                "SELECT intent_sig FROM observations WHERE turn_id = ?",
                (turn_id,))
            row = cur.fetchone()
            if not row:
                return None
            sig = row[0] or ""
            try:
                verb, obj, kw = sig.split("|", 2)
            except ValueError:
                verb, obj, kw = sig, "", ""
            keywords = [k for k in kw.split("_") if k]
        except Exception as ex:
            log.warning("_refresh_template: load turn: %r", ex)
            return None
        # Mētis re-propose con exclusion fw_hash.
        try:
            import praxis_propose
            from llm_router import LLMRouter
            router = LLMRouter()
            def _wise(system, user, **kwargs):
                res = router.chat(system, user, tier="wise", **kwargs)
                return (res.text or "") if res else ""
            intent = {"verb": verb, "object": obj, "keywords": keywords}
            tools = []
            try:
                from loader import load_catalog
                tools = [e.name for e in load_catalog()]
            except Exception:
                pass
            new_fw = praxis_propose.propose_framework(
                query=query, intent=intent,
                available_tools=tools,
                excluded_frameworks={old_fw_hash},
                llm_call=_wise,
            )
            if not new_fw:
                return None
        except Exception as ex:
            log.warning("_refresh_template: Mētis re-propose: %r", ex)
            return None
        # Salva nuova framework su skill + audit trail.
        try:
            new_fw_json = json.dumps(new_fw, ensure_ascii=False)
            new_fw_hash = compute_framework_hash(new_fw)
            self.conn.execute(
                "UPDATE skills SET framework_json = ?, framework_hash = ?, "
                "template_issue = 0, ts_last_used = ? WHERE id = ?",
                (new_fw_json, new_fw_hash, _utcnow(), skill_id))
            self.conn.commit()
            try:
                import praxis_cluster
                praxis_cluster.log_skill_version(
                    self.conn, skill_id, "refresh_template",
                    old_fw_hash=old_fw_hash, new_fw_hash=new_fw_hash,
                    reason="format_fail")
            except Exception:
                pass
            log.info("praxis: template refreshed skill=%s old_fw=%s new_fw=%s",
                      skill_id, old_fw_hash, new_fw_hash)
            return new_fw_hash
        except Exception as ex:
            log.warning("_refresh_template: save skill: %r", ex)
            return None

    # ── EXCLUSIONS for L_propose ──────────────────────────────────────

    def excluded_framework_hashes(self, verb: str, obj: str,
                                    keywords: list[str]) -> set[str]:
        """Anti_skills attivi (TTL non scaduto) per questo intent."""
        _, h = compute_intent_sig(verb, obj, keywords)
        cur = self.conn.execute(
            "SELECT framework_hash FROM anti_skills "
            "WHERE intent_hash = ? AND ttl_expires_at > ?",
            (h, _utcnow()),
        )
        return {r[0] for r in cur}

    # ── RECORD: observation post-turn ─────────────────────────────────

    def record_observation(self, *, turn_id: str, verb: str, obj: str,
                            keywords: list[str], framework: dict,
                            latency_ms: int, query: str = "") -> str:
        """Registra il turn appena eseguito. Ritorna framework_hash per
        feedback hook successivo.

        Cluster assignment (ADR 0161 ext, 26/5/2026): se BGE-M3 disponibile,
        embed(query) + assign_cluster (deterministic + LLM judge zona grigia).
        """
        sig, h = compute_intent_sig(verb, obj, keywords)
        fw_hash = compute_framework_hash(framework)
        embedding = None
        cluster_id = None
        try:
            import praxis_cluster
            embedding = praxis_cluster.embed(
                query or sig.replace("|", " ").replace("_", " "))
            if embedding:
                cluster_id = praxis_cluster.assign_cluster(
                    self.conn, query or sig, embedding,
                    llm_call=_get_middle_llm_call(),
                    exclude_turn_id=turn_id,
                )
        except Exception as ex:
            log.warning("praxis_cluster.assign on record_observation: %r", ex)
        try:
            self.conn.execute(
                "INSERT INTO observations(turn_id, intent_hash, intent_sig, "
                "framework_json, framework_hash, latency_ms, ts, "
                "embedding, cluster_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (turn_id, h, sig,
                 json.dumps(framework, ensure_ascii=False),
                 fw_hash, latency_ms, _utcnow(),
                 embedding, cluster_id),
            )
            self.conn.commit()
        except Exception as ex:
            log.warning("praxis.record_observation failed: %r", ex)
        return fw_hash

    # ── FEEDBACK ✓✗↻ ──────────────────────────────────────────────────

    def record_feedback(self, turn_id: str, verdict: str) -> dict:
        """Hook chat: ✓ ok | ✗ fail | ↻ repeat (ADR 0161 ext, 26/5/2026).

        Effetti per verdict:
          ok     → promote check, uses/ok_count++, champion swap
          repeat → Mētis re-propose framework, soft anti_skill TTL 1h,
                   uses/fail_count++ (soft penalty)
          fail   → Pronoia classify in {format/args/pipeline}_fail, dispatch:
                     format_fail → immediate template refresh via Mētis
                     args_fail   → invalidate filler_cache
                     pipeline_fail → fail_count++ + hard anti_skill TTL 30gg
        """
        if verdict not in ("ok", "fail", "repeat"):
            return {"ok": False, "reason": "bad_verdict"}
        ctx = self._load_feedback_ctx(turn_id)
        if not ctx:
            return {"ok": False, "reason": "no_observation"}
        # Update verdict on observation (audit trail).
        self.conn.execute(
            "UPDATE observations SET verdict = ?, verdict_ts = ? "
            "WHERE turn_id = ?", (verdict, _utcnow(), turn_id))
        outcome: dict = {"ok": True, "verdict": verdict,
                          "intent_hash": ctx["intent_hash"],
                          "framework_hash": ctx["fw_hash"],
                          "cluster_id": ctx["cluster_id"]}
        # Dispatch al handler per verdict.
        if verdict == "ok":
            self._handle_feedback_ok(ctx, outcome)
        elif verdict == "repeat":
            self._handle_feedback_repeat(turn_id, ctx, outcome)
        elif verdict == "fail":
            self._handle_feedback_fail(turn_id, ctx, outcome)
        self.conn.commit()
        return outcome

    # ── Feedback helpers (ADR 0161 ext, 26/5/2026) ────────────────────

    def _load_feedback_ctx(self, turn_id: str) -> Optional[dict]:
        """Carica contesto observation per feedback: intent_hash, intent_sig,
        framework_json, framework_hash, cluster_id, latency_ms, skill_id_hit.
        Ritorna None se observation mancante."""
        cur = self.conn.execute(
            "SELECT intent_hash, intent_sig, framework_json, framework_hash, "
            "cluster_id, latency_ms FROM observations "
            "WHERE turn_id = ? ORDER BY id DESC LIMIT 1", (turn_id,))
        row = cur.fetchone()
        if not row:
            return None
        intent_hash, sig, fw_json, fw_hash, cluster_id, latency_ms = row
        # Lookup skill esistente (cluster_id + framework_hash) per update.
        skill_id_hit = None
        if cluster_id:
            try:
                cur = self.conn.execute(
                    "SELECT id FROM skills "
                    "WHERE cluster_id = ? AND framework_hash = ? "
                    "AND status IN ('active','shadow') LIMIT 1",
                    (cluster_id, fw_hash))
                sk_row = cur.fetchone()
                skill_id_hit = sk_row[0] if sk_row else None
            except Exception:
                pass
        return {
            "intent_hash": intent_hash, "sig": sig,
            "fw_json": fw_json, "fw_hash": fw_hash,
            "cluster_id": cluster_id, "latency_ms": latency_ms or 0,
            "skill_id_hit": skill_id_hit,
        }

    def _handle_feedback_ok(self, ctx: dict, outcome: dict) -> None:
        """✓ feedback: promote check + update metrics + swap champion.

        LWW simmetrico (26/5/2026): se esiste anti_skill per
        (intent_hash, framework_hash), un ✓ successivo lo cancella e
        ripristina skill demoted → active. Caso: utente preme ✗ per errore
        su pipeline corretta, poi ↻/✓ corregge. Senza questo, anti_skill
        TTL 30gg blocca path unico indefinitamente.
        """
        # LWW: remove matching anti_skill + restore demoted skill.
        try:
            anti_removed = self.conn.execute(
                "DELETE FROM anti_skills "
                "WHERE intent_hash = ? AND framework_hash = ?",
                (ctx["intent_hash"], ctx["fw_hash"])).rowcount
            if anti_removed:
                outcome["anti_skill_removed"] = anti_removed
                self.conn.execute(
                    "UPDATE skills SET status = 'active' "
                    "WHERE intent_hash = ? AND framework_hash = ? "
                    "AND status = 'demoted'",
                    (ctx["intent_hash"], ctx["fw_hash"]))
                try:
                    import praxis_cluster
                    praxis_cluster.log_skill_version(
                        self.conn, ctx["skill_id_hit"] or "",
                        "anti_skill_lww_remove",
                        old_fw_hash=ctx["fw_hash"],
                        new_fw_hash=ctx["fw_hash"],
                        reason="ok_feedback_after_error")
                except Exception:
                    pass
        except Exception as ex:
            log.warning("anti_skill LWW remove on ok: %r", ex)
        promoted = self._maybe_promote(
            ctx["intent_hash"], ctx["sig"], ctx["fw_hash"], ctx["fw_json"],
            cluster_id=ctx["cluster_id"])
        outcome["promoted_skill_id"] = promoted
        # Se la promote ha appena creato la skill, rileggi skill_id_hit.
        skill_id = ctx["skill_id_hit"] or promoted
        if skill_id and ctx["cluster_id"]:
            try:
                import praxis_cluster
                praxis_cluster.update_skill_metrics(
                    self.conn, skill_id, True, ctx["latency_ms"])
                swapped = praxis_cluster.maybe_swap_champion(
                    self.conn, ctx["cluster_id"])
                if swapped:
                    outcome["champion_swapped_to"] = swapped
            except Exception as ex:
                log.warning("praxis_cluster on ok feedback: %r", ex)

    def _handle_feedback_repeat(self, turn_id: str, ctx: dict,
                                  outcome: dict) -> None:
        """↻ feedback: Mētis re-propose framework alternativo INLINE +
        soft anti_skill TTL 1h. Ordering: retry PRIMA dell'update metrics
        per garantire metrics applied su skill aggiornata."""
        try:
            retry_out = self.retry_on_repeat(
                turn_id, ctx["fw_hash"], ctx["intent_hash"])
            if retry_out:
                outcome["retry_proposed"] = True
                outcome["new_fw_hash"] = retry_out.get("new_fw_hash")
        except Exception as ex:
            log.warning("retry_on_repeat dispatch: %r", ex)
        # Update metrics DOPO retry (skill puo' avere fw_hash aggiornato).
        if ctx["skill_id_hit"] and ctx["cluster_id"]:
            try:
                import praxis_cluster
                praxis_cluster.update_skill_metrics(
                    self.conn, ctx["skill_id_hit"], False, ctx["latency_ms"])
                swapped = praxis_cluster.maybe_swap_champion(
                    self.conn, ctx["cluster_id"])
                if swapped:
                    outcome["champion_swapped_to"] = swapped
            except Exception as ex:
                log.warning("praxis_cluster on repeat feedback: %r", ex)

    def _handle_feedback_fail(self, turn_id: str, ctx: dict,
                                outcome: dict) -> None:
        """✗ feedback: Pronoia classify → dispatch differenziato.

        format_fail   → mark template_issue + Mētis refresh template inline
                        (skill resta active, fail_count INVARIATO)
        args_fail     → DELETE filler_cache per intent_hash
                        (skill resta active, fail_count INVARIATO)
        pipeline_fail → update_skill_metrics(fail) + maybe anti_skill TTL 30gg
        """
        fail_class = self._classify_fail(turn_id, ctx, outcome)
        if fail_class == "format_fail":
            self._dispatch_format_fail(turn_id, ctx, outcome)
        elif fail_class == "args_fail":
            self._dispatch_args_fail(ctx, outcome)
        else:  # pipeline_fail (default)
            self._dispatch_pipeline_fail(ctx, outcome)

    def _classify_fail(self, turn_id: str, ctx: dict,
                        outcome: dict) -> str:
        """LLM judge (Pronoia) per classe fail. Default safe: pipeline_fail."""
        fail_class = "pipeline_fail"
        try:
            import pronoia_classify_fail as _pcf
            if not _pcf.is_enabled():
                return fail_class
            from turn_feedback import _load_turn
            turn = _load_turn(turn_id) or {}
            query_raw = turn.get("user_query") or turn.get("query") or ""
            steps = turn.get("steps") or []
            results = [s.get("result", {}) if isinstance(s, dict) else {}
                        for s in steps]
            final_msg = turn.get("final_message") or ""
            framework = json.loads(ctx["fw_json"]) if ctx["fw_json"] else {}
            classified = _pcf.classify_fail(
                query_raw, framework, results, final_msg,
                llm_call=_get_middle_llm_call())
            fail_class = classified.get("class", "pipeline_fail")
            outcome["fail_class"] = fail_class
            outcome["fail_reason"] = classified.get("reason", "")
            outcome["pronoia_elapsed_ms"] = classified.get("elapsed_ms", 0)
        except Exception as ex:
            log.warning("pronoia_classify_fail dispatch: %r", ex)
        return fail_class

    def _dispatch_pipeline_fail(self, ctx: dict, outcome: dict) -> None:
        """Hard fail: penalizza skill + anti_skill TTL 30gg."""
        if ctx["skill_id_hit"]:
            try:
                import praxis_cluster
                praxis_cluster.update_skill_metrics(
                    self.conn, ctx["skill_id_hit"], False,
                    ctx["latency_ms"])
            except Exception:
                pass
        anti = self._maybe_add_anti(ctx["intent_hash"], ctx["fw_hash"])
        outcome["anti_skill_added"] = anti

    def _dispatch_args_fail(self, ctx: dict, outcome: dict) -> None:
        """Args sbagliati: invalida filler_cache, skill resta active."""
        try:
            self.conn.execute(
                "DELETE FROM filler_cache WHERE intent_hash = ?",
                (ctx["intent_hash"],))
            self.conn.commit()
            outcome["filler_invalidated"] = True
        except Exception:
            pass

    def _dispatch_format_fail(self, turn_id: str, ctx: dict,
                                outcome: dict) -> None:
        """Template buggy: mark template_issue=1 + Mētis refresh inline."""
        if ctx["skill_id_hit"]:
            try:
                self.conn.execute(
                    "UPDATE skills SET template_issue = 1 WHERE id = ?",
                    (ctx["skill_id_hit"],))
                self.conn.commit()
            except Exception:
                pass
            try:
                refreshed = self._refresh_template_on_format_fail(
                    ctx["skill_id_hit"], turn_id, ctx["fw_hash"])
                if refreshed:
                    outcome["template_refreshed"] = True
                    outcome["new_framework_hash"] = refreshed
            except Exception as ex:
                log.warning("template refresh failed: %r", ex)
        outcome["template_issue"] = True

    def _maybe_promote(self, intent_hash: str, sig: str,
                        fw_hash: str, fw_json: str,
                        cluster_id: Optional[str] = None) -> Optional[str]:
        # Soglia auto-promote configurabile via env (ADR 0161 ext).
        # Default aggressive: 2 obs + 100% ok (decisione utente 25/5/2026).
        # Adaptive (26/5/2026): mutating verb = 1 obs sufficient (azione esplicita
        # utente, side-effect verificabile); producer verb = 2 obs (bucket piu'
        # ampio post-rimozione keywords da intent_hash, prudenza statistica).
        from praxis_constants import (
            MIN_OBS_PRODUCER, MIN_OBS_MUTATING, MIN_SUCCESS,
        )
        verb_in_sig = (sig.split("|", 1)[0] if sig else "").strip().lower()
        _MUTATING = {"move", "delete", "send", "write", "create", "set",
                     "change", "share", "order", "extract", "compress",
                     "render"}
        min_obs = (MIN_OBS_MUTATING if verb_in_sig in _MUTATING
                    else MIN_OBS_PRODUCER)
        # Cluster vuoto: primo successo nasce champion (override 1).
        # Selezione darwiniana §7.9 (ADR 0161 ext).
        if cluster_id:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM skills "
                "WHERE cluster_id = ? AND status IN ('active','shadow')",
                (cluster_id,))
            if (cur.fetchone()[0] or 0) == 0:
                min_obs = 1
        min_success = MIN_SUCCESS
        # Bucket: cluster_id (ADR 0161 ext, semantic) se disponibile,
        # fallback intent_hash (legacy, lessicale).
        if cluster_id:
            cur = self.conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN verdict='ok' THEN 1 ELSE 0 END) "
                "FROM observations WHERE cluster_id = ? "
                "AND framework_hash = ?",
                (cluster_id, fw_hash),
            )
        else:
            cur = self.conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN verdict='ok' THEN 1 ELSE 0 END) "
                "FROM observations WHERE intent_hash = ? "
                "AND framework_hash = ?",
                (intent_hash, fw_hash),
            )
        total, oks = cur.fetchone()
        oks = oks or 0
        if total < min_obs or oks / max(1, total) < min_success:
            return None
        # Skill esiste gia' con questo cluster_id/intent_hash + framework_hash?
        # Se SI con stesso fw_hash → ritorna skill_id (no-op, gia' rappresentato).
        # Se SI con fw_hash DIVERSO → nuovo framework = challenger, no insert qui.
        # Se NO → procedi a INSERT come champion.
        if cluster_id:
            cur = self.conn.execute(
                "SELECT id, framework_hash FROM skills "
                "WHERE cluster_id = ? "
                "AND status IN ('active','shadow') LIMIT 1",
                (cluster_id,),
            )
            existing = cur.fetchone()
            if existing:
                if existing[1] == fw_hash:
                    return existing[0]  # ritorna ID esistente (no-op)
                return None  # challenger candidate, no insert
        else:
            cur = self.conn.execute(
                "SELECT id, framework_hash FROM skills "
                "WHERE intent_hash = ? LIMIT 1",
                (intent_hash,),
            )
            existing = cur.fetchone()
            if existing:
                if existing[1] == fw_hash:
                    return existing[0]
                return None
        skill_id = self._next_skill_id(sig)
        try:
            verb, obj, kw = sig.split("|", 2)
        except ValueError:
            verb, obj, kw = sig, "", ""
        self.conn.execute(
            "INSERT INTO skills(id, intent_sig, intent_hash, keywords_csv, "
            "framework_json, source, status, uses, ok_count, ts_created, "
            "born_from, cluster_id, framework_hash, champion) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (skill_id, sig, intent_hash, kw.replace("_", ","),
             fw_json, "feedback_ok", "active",
             total, oks, _utcnow(), "observations_threshold_met",
             cluster_id, fw_hash, 1),
        )
        # Marca le observations corrispondenti.
        if cluster_id:
            self.conn.execute(
                "UPDATE observations SET promoted_to = ? "
                "WHERE cluster_id = ? AND framework_hash = ?",
                (skill_id, cluster_id, fw_hash),
            )
        else:
            self.conn.execute(
                "UPDATE observations SET promoted_to = ? "
                "WHERE intent_hash = ? AND framework_hash = ?",
                (skill_id, intent_hash, fw_hash),
            )
        try:
            import praxis_cluster
            praxis_cluster.log_skill_version(
                self.conn, skill_id, "created",
                new_fw_hash=fw_hash,
                reason=f"observations_threshold_met obs={total} ok={oks}")
        except Exception:
            pass
        log.info("praxis: promoted skill %s (cluster=%s sig=%s obs=%d ok=%d)",
                  skill_id, cluster_id or "-", sig, total, oks)
        return skill_id

    def _next_skill_id(self, intent_sig: str) -> str:
        try:
            verb, obj, kw = intent_sig.split("|", 2)
        except ValueError:
            verb, obj, kw = intent_sig, "", ""
        kw_first = (kw.split("_")[0] if kw else "")
        base = f"{verb}_{obj}" + (f"_{kw_first}" if kw_first else "")
        cur = self.conn.execute(
            "SELECT version FROM skills WHERE id LIKE ? "
            "ORDER BY version DESC LIMIT 1",
            (f"{base}_v%",),
        )
        row = cur.fetchone()
        if row and row[0]:
            try:
                major = int(row[0].lstrip("v").split(".")[0])
                return f"{base}_v{major + 1}.0.0"
            except Exception:
                pass
        return f"{base}_v1.0.0"

    def _maybe_add_anti(self, intent_hash: str, fw_hash: str) -> bool:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM observations "
            "WHERE intent_hash = ? AND framework_hash = ? AND verdict='fail'",
            (intent_hash, fw_hash),
        )
        n_fails = cur.fetchone()[0]
        if n_fails < 3:
            return False
        ttl = (datetime.now(timezone.utc) + timedelta(days=30))\
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute(
            "INSERT INTO anti_skills(intent_hash, framework_hash, fail_count, "
            "ttl_expires_at, reason, ts_last_fail) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(intent_hash, framework_hash) DO UPDATE SET "
            "fail_count = anti_skills.fail_count + 1, "
            "ttl_expires_at = excluded.ttl_expires_at, "
            "ts_last_fail = excluded.ts_last_fail",
            (intent_hash, fw_hash, n_fails, ttl,
             f"feedback_fail:{n_fails}", _utcnow()),
        )
        # Demote skill se promossa con questo framework_hash
        self.conn.execute(
            "UPDATE skills SET status = 'demoted' "
            "WHERE intent_hash = ? AND id IN ("
            "  SELECT promoted_to FROM observations "
            "  WHERE intent_hash = ? AND framework_hash = ? AND promoted_to IS NOT NULL"
            ")",
            (intent_hash, intent_hash, fw_hash),
        )
        log.info("praxis: anti_skill added intent_hash=%s fw_hash=%s "
                  "(after %d fails)", intent_hash, fw_hash, n_fails)
        return True

    # ── FILLER CACHE (CRITICO #2) ─────────────────────────────────────

    def filler_cache_get(self, intent_hash: str, filler_name: str
                          ) -> Optional[str]:
        """Lookup O(1) filler value cachato. Ritorna None se miss."""
        if not intent_hash or not filler_name:
            return None
        cur = self.conn.execute(
            "SELECT value FROM filler_cache "
            "WHERE intent_hash = ? AND filler_name = ?",
            (intent_hash, filler_name),
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE filler_cache SET uses = uses + 1, ts_last = ? "
                "WHERE intent_hash = ? AND filler_name = ?",
                (_utcnow(), intent_hash, filler_name),
            )
            self.conn.commit()
            return row[0]
        return None

    def filler_cache_put(self, intent_hash: str, filler_name: str,
                          value: str) -> None:
        """Upsert filler value. Idempotente."""
        if not intent_hash or not filler_name or not value:
            return
        try:
            now = _utcnow()
            self.conn.execute(
                "INSERT INTO filler_cache(intent_hash, filler_name, value, "
                "uses, ts_first, ts_last) VALUES (?,?,?,1,?,?) "
                "ON CONFLICT(intent_hash, filler_name) DO UPDATE SET "
                "value = excluded.value, uses = filler_cache.uses + 1, "
                "ts_last = excluded.ts_last",
                (intent_hash, filler_name, value, now, now),
            )
            self.conn.commit()
        except Exception as ex:
            log.warning("praxis.filler_cache_put failed: %r", ex)

    # ── ADMIN telemetry ───────────────────────────────────────────────

    def stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT status, COUNT(*) FROM skills GROUP BY status"
        )
        by_status = dict(cur.fetchall())
        cur = self.conn.execute("SELECT COUNT(*) FROM observations")
        n_obs = cur.fetchone()[0]
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM anti_skills WHERE ttl_expires_at > ?",
            (_utcnow(),),
        )
        n_anti = cur.fetchone()[0]
        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT verdict) FROM observations WHERE verdict IS NOT NULL"
        )
        return {
            "skills_by_status": by_status,
            "observations_total": n_obs,
            "anti_skills_active": n_anti,
        }

    def list_skills(self, status: Optional[str] = None, limit: int = 100
                     ) -> list[dict]:
        q = ("SELECT id, intent_sig, status, uses, ok_count, fail_count, "
              "alignment_score, ts_created, ts_last_used, source, version "
              "FROM skills")
        params: tuple = ()
        if status:
            q += " WHERE status = ?"
            params = (status,)
        q += " ORDER BY ts_last_used DESC LIMIT ?"
        params = params + (limit,)
        cur = self.conn.execute(q, params)
        cols = ["id", "intent_sig", "status", "uses", "ok_count", "fail_count",
                 "alignment_score", "ts_created", "ts_last_used", "source",
                 "version"]
        return [dict(zip(cols, r)) for r in cur]


# ── Module-level singleton lazy ─────────────────────────────────────────

_INSTANCE: Optional[PraxisStore] = None


def get_store() -> PraxisStore:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = PraxisStore()
    return _INSTANCE


def reset_for_test() -> None:
    """Reset singleton — usato dai test E2E."""
    global _INSTANCE
    if _INSTANCE is not None:
        _INSTANCE.close()
    _INSTANCE = None
