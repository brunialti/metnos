#!/usr/bin/env python3
"""capture_examples.py — cattura esempi REALI (query→intent→executor→args) per
la documentazione/post. ESEGUIRE SOLO con GPU LIBERA (mai in parallelo al grid:
la contesa produce piani spazzatura).

Sceglie alcune query complesse (multi-azione, multi-dominio), pianifica a secco
e salva il risultato strutturato in internal/reports/scaling_results/examples.json
+ stampa una resa leggibile.

Uso: METNOS_ENGINE=v3 python3 bench/capture_examples.py
"""
import sys, json, os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime")); sys.path.insert(0, str(_ROOT / "bench"))
os.environ.setdefault("METNOS_ENGINE", "v3")
for k, v in {"METNOS_PROPOSER_GRAMMAR": "1", "METNOS_PROPOSER_VERB_FILTER": "1",
             "METNOS_PREFILTER_RULES": "1", "METNOS_ENGINE_POOL_SIZE": "12"}.items():
    os.environ.setdefault(k, v)

_OUT = _ROOT / "internal/reports/scaling_results/examples.json"

# Query SCELTE a mano: complesse, naturali, multi-dominio — buone vetrine.
QUERIES = [
    "trova i file di log in /tmp/logs piu' vecchi di una settimana, comprimili "
    "in uno zip, mandami l'archivio a roberto@example.com e poi cancella gli originali",
    "controlla la posta non letta di oggi, trova le foto scattate ieri, "
    "guarda che impegni ho domani e mandami un riepilogo a roberto@example.com",
    "cerca online le novita' su AMD ROCm, apri i primi due risultati, "
    "riassumimeli e salva il riassunto",
]


def main():
    import compound_dryrun as CD
    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from agent_runtime import _engine_v2_catalog_with_builtins
    from intent_extractor import extract_intent
    cat = _engine_v2_catalog_with_builtins(
        filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER))
    fast, wise = CD.build_calls()

    out = []
    for q in QUERIES:
        ir = extract_intent(q, fast) or {}
        intent_actions = [(a.get("verb"), a.get("object"))
                          for a in (ir.get("actions") or [])]
        fw, intent, acts, pool = CD.plan_only(q, cat, fast, wise)
        steps = [{"tool": s.get("tool"), "args": s.get("args") or {}}
                 for s in CD._steps(fw) if s.get("tool") != "final_answer"]
        out.append({"query": q, "intent": intent_actions, "plan": steps})
        print("\n" + "=" * 78)
        print("QUERY:", q)
        print("INTENT:", " · ".join(f"{v}/{o}" for v, o in intent_actions))
        print("PIANO:")
        for i, s in enumerate(steps, 1):
            print(f"  {i}. {s['tool']}")
            for k, v in s["args"].items():
                print(f"        {k} = {v!r}")
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\nsalvato: {_OUT}")


if __name__ == "__main__":
    main()
