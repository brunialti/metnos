#!/usr/bin/env python3
"""Misura COVERAGE (decomposer vs engine) + LATENZA di planning, prod-faithful.
Vedi project-compound-planning-refactor: decisione A(ritira)/B(tieni) data-driven."""
import sys, os, time, json
sys.path.insert(0, "runtime"); sys.path.insert(0, "bench")
os.environ.setdefault("METNOS_ENGINE", "v3")
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_PREFILTER_RULES", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")
os.environ.setdefault("METNOS_LLM_SEED", "42")

from store_bootstrap import register_builtin_stores
register_builtin_stores()
from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
from agent_runtime import _BUILTIN_TOOL_HANDLERS
from compound_decomposer import decompose_query
from engine.dispatch import finalize_decomposed_plan
from vocab import COVERAGE_REQUIRED_VERBS as CRV, ACTIONS as ACT
import prefilter as PF
import compound_dryrun as cd
from compound_scaling_bench import DOMAIN_CHAINS

catalog = filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)
avail = {e.name for e in catalog} | set(_BUILTIN_TOOL_HANDLERS) | {"final_answer"}
schemas = {e.name: getattr(e, "args_schema", None) for e in catalog}
fast, wise = cd.build_calls()

def q_verbs(q):
    return set(PF.detect_canonical_verbs_all(PF.tokenize(q)))

def decompose_prod(q):
    """Replica il path prod: decompose + coverage guard."""
    st = decompose_query(q, avail, schemas)
    if st:
        sv = {s["tool"].split("_",1)[0] for s in st
              if s.get("tool") and s["tool"]!="final_answer"
              and s["tool"].split("_",1)[0] in ACT}
        if (q_verbs(q) & set(CRV)) - sv:
            st = None
    return st

# --- Corpus: catene compound per dominio (2 e 3 azioni) ---
def build_queries():
    out = []
    for dom, chain in DOMAIN_CHAINS.items():
        frags = [c[0] for c in chain]
        if len(frags) >= 2:
            out.append((dom, 2, ", e ".join(frags[:2]) + "."))
        if len(frags) >= 3:
            out.append((dom, 3, ", ".join(frags[:2]) + ", e " + frags[2] + "."))
    # casi reali della sessione
    real = [
        ("eventi", 3, "Leggi gli eventi della settimana, estrai titolo e orario, e crea un foglio."),
        ("mail-fatture", 3, "Cerca nelle mie email i pagamenti Anthropic dell'ultimo anno, estrai data e importo, e crea un foglio."),
        ("mail-ordini", 3, "Trova nelle mie email gli ordini, estrai numero e totale, e salvali in un csv."),
        ("pdf", 3, "Leggi i pdf nella cartella Documenti, estrai i totali, e crea un foglio di calcolo."),
    ]
    return out + real

queries = build_queries()
print(f"Corpus: {len(queries)} query compound\n")

# --- COVERAGE ---
decomp, defer = [], []
for dom, a, q in queries:
    st = decompose_prod(q)
    (decomp if st else defer).append((dom, a, q))
print(f"=== COVERAGE ===")
print(f"  DECOMPOSER: {len(decomp)}/{len(queries)} = {len(decomp)/len(queries):.0%}")
print(f"  ENGINE    : {len(defer)}/{len(queries)} = {len(defer)/len(queries):.0%}")
print(f"  decomposer domains: {sorted(set(d for d,_,_ in decomp))}")
print(f"  engine domains    : {sorted(set(d for d,_,_ in defer))}")

# --- LATENZA planning (decomposer-handled queries) ---
print(f"\n=== LATENZA planning (solo query DECOMPOSER-handled) ===")
rows = []
for dom, a, q in decomp:
    t0 = time.time()
    st = decompose_prod(q)
    fw = finalize_decomposed_plan(st, q, catalog)
    t_dec = (time.time()-t0)*1000
    t0 = time.time()
    try:
        cd.plan_only(q, catalog, fast, wise)  # engine: intent+pool+propose+guards
        t_eng = (time.time()-t0)*1000
    except Exception as e:
        t_eng = float("nan")
    rows.append((dom, t_dec, t_eng))
    print(f"  {dom:14} decomposer={t_dec:7.1f}ms   engine={t_eng:8.1f}ms   delta=+{t_eng-t_dec:7.1f}ms")
if rows:
    import statistics as S
    dd = [r[1] for r in rows]; ee = [r[2] for r in rows if r[2]==r[2]]
    print(f"\n  MEDIANA decomposer={S.median(dd):.1f}ms  engine={S.median(ee):.1f}ms  → engine ~{S.median(ee)/max(S.median(dd),0.1):.0f}x più lento in planning")
