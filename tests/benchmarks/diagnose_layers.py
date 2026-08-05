#!/usr/bin/env python3
"""diagnose_layers.py — ANALISI per-layer dei fallimenti compound (read-only).

Per ogni query fallita, classifica DOVE si perde ogni clausola HARD del gold,
lungo i 4 stadi della pipeline:

  INTENT   : la clausola (verbo,oggetto) e' in intent.actions?      (LLM intent)
  PROPOSER : e' nel piano DOPO proposer.propose (prima dei guard)?   (LLM proposer)
  ENFORCE  : e' nel piano DOPO _enforce_missing_* ?                  (guard determin.)
  CONFORM  : e' nell'ORDINE giusto DOPO _conform_to_intent_order?    (guard determin.)

Cosi' si vede la DISTRIBUZIONE delle cause (non si assume una causa sola) prima
di decidere i fix. Nessuna modifica, nessun fix: solo osservazione.

Uso: METNOS_ENGINE=v3 python3 tests/benchmarks/diagnose_layers.py
"""
import sys, os, json
from pathlib import Path
from collections import Counter

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))
sys.path.insert(0, str(_ROOT / "tests" / "benchmarks"))
os.environ.setdefault("METNOS_ENGINE", "v3")
for k, v in {"METNOS_PROPOSER_GRAMMAR": "1", "METNOS_PROPOSER_VERB_FILTER": "1",
             "METNOS_PREFILTER_RULES": "1", "METNOS_ENGINE_POOL_SIZE": "12"}.items():
    os.environ.setdefault(k, v)

RESULTS = _ROOT / "internal/reports/scaling_results/args_final.json"


def _objs(steps_tools, _ng, soft):
    """(verb,obj) HARD degli step-executor (esclusi final_answer + soft helpers)."""
    out = []
    for t in steps_tools:
        if not t or t == "final_answer":
            continue
        nc = _ng.parse_name(t)
        if nc:
            out.append((nc.verb, nc.obj))
    return out


def main():
    import compound_dryrun as CD
    import compound_scaling_bench as SB
    import naming_grammar as _ng
    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from agent_runtime import _engine_v2_catalog_with_builtins
    from engine.types import Intent
    from engine.routing_pool import build_routing_pool
    from engine.proposer import get_proposer
    from engine import dispatch as D
    from intent_extractor import extract_intent
    cat = _engine_v2_catalog_with_builtins(
        filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER))
    fast, wise = CD.build_calls()

    # ricostruisci le query fallite dai risultati salvati
    d = json.loads(RESULTS.read_text())
    targets = []
    seen = set()
    for r in d["rows"]:
        if r["ok"]:
            continue
        key = (r["a"], r["d"], r["q"][:34])
        if key in seen:
            continue
        seen.add(key)
        for idx in range(4):
            g = SB.gen_query(r["a"], r["d"], idx)
            if g and g[0][:34] == r["q"][:34]:
                targets.append((r["a"], r["d"], g[0], g[1]))
                break

    layer_blame = Counter()
    print(f"FALLIMENTI da analizzare: {len(targets)}\n" + "=" * 80)
    for (a, dd, q, gold) in targets:
        hard = [(v, o) for v, o, nl, *_ in gold if v not in SB.SOFT]

        # STADIO 0 — intent
        ir = extract_intent(q, fast) or {}
        intent = Intent(verb=(ir.get("verb") or "").lower(),
                        object=(ir.get("object") or "").lower(),
                        keywords=list(ir.get("keywords") or []),
                        confidence=float(ir.get("confidence") or 1.0),
                        lang="it", actions=list(ir.get("actions") or []))
        intent_vo = [((x.get("verb") or "").lower(), (x.get("object") or "").lower())
                     for x in intent.actions]

        # STADIO 1 — proposer puro (prima dei guard)
        pool = build_routing_pool(q, intent, cat)
        fw = get_proposer().propose(query=q, intent=intent, pool=pool,
                                    excluded_hashes=set(), llm_call=wise,
                                    lang="it", catalog=cat)
        prop_tools = [s.tool for s in (fw.steps if fw else [])]
        prop_vo = _objs(prop_tools, _ng, SB.SOFT)

        # STADIO 2 — dopo enforce (align+enforce_clauses+enforce_objects), SENZA conform
        import copy
        fw_e = copy.deepcopy(fw) if fw else fw
        if fw_e:
            fw_e = D._align_framework_objects(fw_e, intent, cat)
            fw_e = D._enforce_missing_clauses(fw_e, intent, q, cat)
            fw_e = D._enforce_missing_objects(fw_e, intent, q, cat)
        enf_tools = [s.tool for s in (fw_e.steps if fw_e else [])]
        enf_vo = _objs(enf_tools, _ng, SB.SOFT)

        # STADIO 3 — piano finale (con conform + fill_args)
        fw_f, _i, _a, _p = CD.plan_only(q, cat, fast, wise)
        fin_tools = [s.get("tool") for s in CD._steps(fw_f)]
        sc = SB.score(CD._steps(fw_f), gold)

        # per ogni clausola HARD: a che stadio sparisce / si disordina?
        def _has(vo_list, h):
            # match by object (producer) o esatto
            hv, ho = h
            return any(o == ho for (v, o) in vo_list)
        print(f"\na={a} d={dd}  anyo={sc['anyo']}/{sc['hard_total']} fwd={sc['fwd']}")
        print(f"  Q: {q[:95]}")
        print(f"  HARD gold ({len(hard)}): {hard}")
        print(f"  intent.actions: {intent_vo}")
        # conteggio per-oggetto (cattura same-object multipli)
        from collections import Counter as C
        gold_obj = C(o for v, o in hard)
        for obj, need in gold_obj.items():
            in_int = sum(1 for v, o in intent_vo if o == obj)
            in_pro = sum(1 for v, o in prop_vo if o == obj)
            in_enf = sum(1 for v, o in enf_vo if o == obj)
            in_fin = sum(1 for t in fin_tools if t and t != "final_answer"
                         and _ng.parse_name(t) and _ng.parse_name(t).obj == obj)
            flag = ""
            if in_int < need: flag = "INTENT manca"; layer_blame["intent"] += (need - in_int)
            elif in_fin < need: flag = "perso a valle"; layer_blame["downstream"] += (need - in_fin)
            if flag:
                print(f"    obj={obj}: gold×{need} | intent×{in_int} proposer×{in_pro} "
                      f"enforce×{in_enf} final×{in_fin}  -> {flag}")
        if sc['fwd'] < sc['anyo']:
            print(f"    ORDINE: anyo={sc['anyo']} ma fwd={sc['fwd']} -> CONFORM disordina")
            layer_blame["conform_order"] += 1
        print(f"  plan finale: {[t for t in fin_tools if t!='final_answer']}")

    print("\n" + "=" * 80)
    print("DISTRIBUZIONE CAUSE (quante clausole/ordini persi per layer):")
    for k, v in layer_blame.most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
