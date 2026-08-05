#!/usr/bin/env python3
"""cmp_graph_vs_proposer.py — confronto curiosità (31/5/2026).

Sugli STESSI 446 q del frozen set (test_set_FROZEN_v3.json), misura top-1
(path == expected_path) di:
  - graph_fwd : graph_search_v2 forward-only (deterministico, post-parse)
  - graph_bwd : graph_search_v2 forward+backward (SIM_BACKWARD)
  - proposer  : engine v2 live (intent_extractor + prefilter + proposer.propose, LLM)

Output incrementale: /tmp/cmp_graph_proposer.jsonl + summary a fine run.
Niente esecuzione executor (solo selezione del path) → nessun side-effect.
"""
import os, sys, json, time
from pathlib import Path

SIMDIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SIMDIR))
# runtime path
for p in SIMDIR.parents:
    if (p / "runtime" / "config.py").is_file():
        sys.path.insert(0, str(p / "runtime")); ROOT = p; break

os.environ.setdefault("METNOS_INSTALL_ROOT", str(ROOT))
os.environ["METNOS_PROPOSER_GRAMMAR"] = "1"
os.environ["METNOS_PROPOSER_VERB_FILTER"] = "1"

OUT = Path("/tmp/cmp_graph_proposer.jsonl")
OUT.write_text("")

def log(m):
    print(m, flush=True)

# ---- carica frozen set (campione via step, override con CMP_LIMIT) ----
TS_ALL = json.load(open(SIMDIR / "test_set_FROZEN_v3.json"))
LIMIT = int(os.environ.get("CMP_LIMIT", "64"))
step = max(1, len(TS_ALL) // LIMIT)
TS = TS_ALL[::step][:LIMIT]
log(f"frozen set: {len(TS_ALL)} → campione {len(TS)} (step {step})")

# ---- GRAPH side (setup come run_simulation_v2: registry da typing_cache, parse_query) ----
from query_parser import parse_query
import graph_search_v2 as gsv2
from registry import ExecutorRegistry
TYPING_CACHE = SIMDIR / "typing_cache"
REG = ExecutorRegistry(json_dir=TYPING_CACHE)

def graph_paths(qd, backward):
    os.environ["SIM_BACKWARD"] = "1" if backward else "0"
    cands = gsv2.search(qd, REG)
    return [[s.tool for s in c.steps if s.tool != "final_answer"] for c in cands[:3]]

# ---- PROPOSER side (engine v2 live) ----
from loader import load_catalog
from prefilter import rank_with_intent
from tool_grammar import _UNIVERSAL_HELPERS
from engine.types import Intent
from engine.proposer import get_proposer
from intent_extractor import extract_intent
from llm_router import LLMRouter

CAT = load_catalog()
PROP = get_proposer()

def _llm_fast(sys_msg, user_msg, *, max_tokens=80, think=False, **_):
    try:
        r = LLMRouter().provider("fast").chat(sys_msg, user_msg, max_tokens=max_tokens)
        return (getattr(r, "text", r) or "").strip()
    except Exception:
        return ""

def _llm_wise(sys_msg, user_msg, *, max_tokens=2048, think=True, **kw):
    try:
        grammar = kw.get("grammar")
        tier = kw.get("tier_override") or ("precise" if grammar else "wise")
        call_kw = {"max_tokens": max_tokens}
        if grammar is not None:
            call_kw["grammar"] = grammar
        r = LLMRouter().provider(tier).chat(sys_msg, user_msg, **call_kw)
        return (getattr(r, "text", r) or "").strip()
    except Exception:
        return ""

def proposer_path(query):
    iraw = extract_intent(query, _llm_fast)
    if not iraw:
        return None
    intent = Intent(verb=(iraw.get("verb") or "").lower(),
                    object=(iraw.get("object") or "").lower(),
                    keywords=list(iraw.get("keywords") or []),
                    confidence=float(iraw.get("confidence") or 1.0), lang="it")
    pool = CAT
    if intent.verb and intent.object:
        try:
            idict = {"verb": intent.verb, "object": intent.object, "keywords": intent.keywords}
            filt = rank_with_intent(query, CAT, idict, k=12)
            present = {getattr(e, "name", None) for e in filt}
            for ex in CAT:
                nm = getattr(ex, "name", None)
                if nm in _UNIVERSAL_HELPERS and nm not in present:
                    filt = filt + [ex]; present.add(nm)
            pool = filt
        except Exception:
            pool = CAT
    pool_names = [getattr(e, "name", None) for e in pool if getattr(e, "name", None)]
    fw = PROP.propose(query=query, intent=intent, pool=pool_names,
                      excluded_hashes=set(), llm_call=_llm_wise, lang="it", catalog=CAT)
    if fw is None:
        return None
    return [s.tool for s in fw.steps if s.tool != "final_answer"]

# ---- run ----
agg = {"graph_fwd_t1": 0, "graph_fwd_t2": 0, "graph_bwd_t1": 0, "graph_bwd_t2": 0,
       "prop_t1": 0, "n": 0, "graph_err": 0, "prop_err": 0}
t0 = time.time()
for i, rec in enumerate(TS, 1):
    q = rec["query"]; exp = rec.get("expected_path") or []
    row = {"q": q, "exp": exp}
    try:
        qd = parse_query(q, use_cache=True)
        gf = graph_paths(qd, backward=False) if qd else []
        row["gf"] = gf[:3] if gf else []
        if gf:
            agg["graph_fwd_t1"] += (gf[0] == exp)
            agg["graph_fwd_t2"] += any(p == exp for p in gf[:2])
        gb = graph_paths(qd, backward=True) if qd else []
        row["gb"] = gb[:3] if gb else []
        if gb:
            agg["graph_bwd_t1"] += (gb[0] == exp)
            agg["graph_bwd_t2"] += any(p == exp for p in gb[:2])
    except Exception as ex:
        agg["graph_err"] += 1; row["graph_exc"] = str(ex)[:120]
    try:
        pp = proposer_path(q)
        row["pp"] = pp
        if pp is not None:
            agg["prop_t1"] += (pp == exp)
    except Exception as ex:
        agg["prop_err"] += 1; row["prop_exc"] = str(ex)[:120]
    agg["n"] += 1
    with OUT.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if i % 25 == 0:
        n = agg["n"]
        log(f"[{i}/{len(TS)}] {time.time()-t0:.0f}s | "
            f"graph_fwd t1={agg['graph_fwd_t1']/n*100:.0f}% t2={agg['graph_fwd_t2']/n*100:.0f}% | "
            f"graph_bwd t1={agg['graph_bwd_t1']/n*100:.0f}% t2={agg['graph_bwd_t2']/n*100:.0f}% | "
            f"prop t1={agg['prop_t1']/n*100:.0f}%")

n = agg["n"] or 1
log("\n==================== SUMMARY ====================")
log(f"N={agg['n']}  (graph_err={agg['graph_err']} prop_err={agg['prop_err']})  elapsed={time.time()-t0:.0f}s")
log(f"graph_fwd : top1={agg['graph_fwd_t1']/n*100:.1f}%  top2={agg['graph_fwd_t2']/n*100:.1f}%")
log(f"graph_bwd : top1={agg['graph_bwd_t1']/n*100:.1f}%  top2={agg['graph_bwd_t2']/n*100:.1f}%")
log(f"proposer  : top1={agg['prop_t1']/n*100:.1f}%  (single-shot, no top2)")
log("=================================================")
