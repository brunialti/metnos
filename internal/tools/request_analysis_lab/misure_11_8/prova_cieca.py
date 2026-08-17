#!/usr/bin/env python3
"""Held-out test: does the tuning generalise, or did it fit those 40 queries?

Three arms on queries NEVER used while tuning, so the answer cannot be an echo
of the tuning set:

  A  current prompt, only the output cap fixed          -- reference
  B  A + role contract realigned to the validator       -- provable by reading
                                                           the code, low risk
  C  B + TIE_BREAK removed                              -- the overfitted one:
                                                           chosen by ablation on
                                                           the 40, with the
                                                           explanation falsified

The sample deliberately over-represents the two blind spots -- identity registry
and mailbox moves -- where the tuning set held ZERO examples and TIE_BREAK holds
its longest rules. If C is going to break, it breaks there.

Read-only on production.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import re
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/metnos")
sys.path.insert(0, "/opt/metnos/runtime")

LAB = pathlib.Path("/opt/metnos/internal/tools/request_analysis_lab")
SCRATCH = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRATCH))
TUNING_SEED = 20260810          # the sample used while tuning: excluded here
HELDOUT_SEED = 20260811
BUDGET = 4000
SIZE = 120
BLIND = {"persons": r"enroll|registr|volto|riconosc|chi sono|ospite|persona",
         "mailbox": r"sposta.*(mail|messagg)|archivia|spam|cestino|junk"}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def corpus() -> list[str]:
    turns = pathlib.Path.home() / ".local/share/metnos/turns"
    seen: dict[str, None] = {}
    for f in sorted(turns.glob("*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            query = (record.get("user_query") or "").strip()
            if len(query) >= 8 and not query.startswith(("/", '"', "C:")):
                seen.setdefault(query, None)
    return list(seen)


def sample() -> tuple[list[str], dict[str, list[int]]]:
    every = corpus()
    tuned = set(random.Random(TUNING_SEED).sample(every, 40))
    unseen = [q for q in every if q not in tuned]
    chosen, taken = [], set()
    for pattern in BLIND.values():                  # every blind-spot case first
        for query in unseen:
            if query not in taken and re.search(pattern, query, re.I):
                chosen.append(query)
                taken.add(query)
    rest = [q for q in unseen if q not in taken]
    chosen += random.Random(HELDOUT_SEED).sample(rest, max(0, SIZE - len(chosen)))
    groups = {name: [i for i, q in enumerate(chosen)
                     if re.search(pattern, q, re.I)]
              for name, pattern in BLIND.items()}
    return chosen, groups


def arm(tag: str, *, contract: bool, drop_tie_break: bool):
    module = load(LAB / "unified_query_bench_v23_checkpoint.py", f"arm_{tag}")
    patch = __import__("patch_corrente")
    if contract:
        patch._contract(module)
    if drop_tie_break:
        patch._drop_tie_break(module)
    real = urllib.request.Request

    class _B(real):
        def __init__(self, url, data=None, headers=None, **kw):
            if data:
                body = json.loads(data)
                if "max_tokens" in body:
                    body["max_tokens"] = BUDGET
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            super().__init__(url, data=data, headers=headers or {}, **kw)

    module.urllib.request.Request = _B
    return module


def run(module, queries: list[str]) -> list[dict]:
    rows = []
    for query in queries:
        started = time.perf_counter()
        try:
            frame, info = module.folded_call(query, prompt_variant="v23lite")
            rows.append({"valid": bool(info.get("valid")),
                         "reason": str(info.get("reason") or ""),
                         "routes": [f"{p.get('verb')}/{p.get('object')}"
                                    for p in frame.get("predicates") or []],
                         "ms": (time.perf_counter() - started) * 1000})
        except Exception as error:                          # noqa: BLE001
            rows.append({"valid": False, "reason": type(error).__name__,
                         "routes": [], "ms": (time.perf_counter() - started) * 1000})
    return rows


def main() -> int:
    queries, groups = sample()
    print(f"prova cieca | {len(queries)} query MAI usate | budget {BUDGET}")
    print(f"  punti ciechi inclusi: "
          + ", ".join(f"{k} {len(v)}" for k, v in groups.items()) + "\n", flush=True)

    arms = [("A riferimento", {"contract": False, "drop_tie_break": False}),
            ("B +contratto", {"contract": True, "drop_tie_break": False}),
            ("C +tie_break tolto", {"contract": True, "drop_tie_break": True})]
    results = {}
    for tag, kwargs in arms:
        started = time.perf_counter()
        rows = run(arm(tag.split()[0].lower(), **kwargs), queries)
        results[tag] = rows
        lat = sorted(r["ms"] for r in rows)
        print(f"{tag:22s} valide {sum(1 for r in rows if r['valid']):3d}/{len(rows)}  "
              f"p50 {lat[len(lat) // 2]:.0f} ms  ({time.perf_counter() - started:.0f} s)",
              flush=True)

    base = results["A riferimento"]
    print("\n=== effetto sui punti ciechi (dove il campione di messa a punto era vuoto)")
    for name, indexes in groups.items():
        if not indexes:
            continue
        line = f"  {name:9s} ({len(indexes):2d} query): "
        line += "  ".join(f"{tag.split()[0]} {sum(1 for i in indexes if results[tag][i]['valid']):2d}"
                          for tag, _ in arms)
        print(line)

    print("\n=== chi rompe cosa rispetto al riferimento A")
    for tag, _ in arms[1:]:
        broke = [i for i in range(len(queries))
                 if base[i]["valid"] and not results[tag][i]["valid"]]
        healed = [i for i in range(len(queries))
                  if not base[i]["valid"] and results[tag][i]["valid"]]
        print(f"  {tag:22s} rotte {len(broke):2d}  risanate {len(healed):2d}  "
              f"netto {len(healed) - len(broke):+d}")
        for i in broke:
            print(f"      - {results[tag][i]['reason'][:32]:32s} {queries[i][:52]!r}")

    (SCRATCH / "prova_cieca.json").write_text(json.dumps({
        "heldout_seed": HELDOUT_SEED, "budget": BUDGET, "queries": queries,
        "groups": groups, "results": results,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\nscritto prova_cieca.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
