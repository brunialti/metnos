#!/usr/bin/env python3
"""Bench POC su CORPUS REALE: regex-attuali vs LLM-schema su tutte le query
storiche di Metnos (turns). Nessun gold-label → misura ACCORDO + latenza +
i casi dove l'LLM trova struttura che il regex ha perso (valore aggiunto).

Confronto su campi comparabili (categorici/booleani):
  ordering.mode (none/sort/group), ordering.desc, time_window present,
  recurrence present, count_intent, visualize_intent.

Input: /tmp/metnos_queries_uniq.txt (una query/riga). Env LIMIT=N per smoke.
Output: /tmp/poc_corpus_results.jsonl (incrementale) + summary a stdout.
"""
import json
import os
import sys
import time
from pathlib import Path

_R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_R))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ordering_clause as _ord          # noqa: E402
import time_window_resolver as _tw       # noqa: E402
import recurring_tasks as _rt            # noqa: E402
import detection_lexicon as _dl          # noqa: E402
from nlu_extract import metnos_nlu       # noqa: E402

_dl.ensure_seeded()


def regex_extract(q: str) -> dict:
    """Estrazione con i meccanismi DETERMINISTICI attuali (regex/lessico)."""
    o = _ord.detect(q) or {}
    return {
        "ordering_mode": o.get("mode", "none"),
        "ordering_desc": bool(o.get("desc", False)),
        "time_present": bool(_tw.parse_query_time_window(q)),
        "recur_present": bool(_rt.parse_recurrence_query(q)),
        "count": _dl.match("output.count_request", q),
        "viz": _dl.match("output.visualize_request", q),
    }


def llm_to_compare(data: dict) -> dict:
    o = data.get("ordering") or {}
    rec = data.get("recurrence") or {}
    return {
        "ordering_mode": o.get("mode", "none"),
        "ordering_desc": bool(o.get("desc", False)),
        "time_present": bool((data.get("time_window") or "").strip()),
        "recur_present": bool((rec.get("every") or "").strip()),
        "count": bool(data.get("count_intent", False)),
        "viz": bool(data.get("visualize_intent", False)),
    }


FIELDS = ["ordering_mode", "ordering_desc", "time_present", "recur_present",
          "count", "viz"]


def main():
    qs = [l.strip() for l in open("/tmp/metnos_queries_uniq.txt",
                                   errors="replace") if l.strip()]
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        qs = qs[:limit]
    out = open("/tmp/poc_corpus_results.jsonl", "w")
    agree = {f: 0 for f in FIELDS}
    llm_found = {f: 0 for f in FIELDS}   # llm=present/true, regex=absent/false
    regex_found = {f: 0 for f in FIELDS}  # regex=present/true, llm=absent/false
    lat = []
    n = 0
    bad_json = 0
    for q in qs:
        rx = regex_extract(q)
        data, meta = metnos_nlu(q)
        lat.append(meta["latency_ms"])
        if not meta["ok"]:
            bad_json += 1
            continue
        lm = llm_to_compare(data)
        n += 1
        rec = {"q": q, "ms": round(meta["latency_ms"]), "rx": rx, "llm": lm}
        diffs = {}
        for f in FIELDS:
            a, b = rx[f], lm[f]
            if a == b:
                agree[f] += 1
            else:
                diffs[f] = {"rx": a, "llm": b}
                # "found" = trova struttura/intent (non none/false)
                rx_pos = a not in (False, "none")
                lm_pos = b not in (False, "none")
                if lm_pos and not rx_pos:
                    llm_found[f] += 1
                elif rx_pos and not lm_pos:
                    regex_found[f] += 1
        if diffs:
            rec["diffs"] = diffs
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if n % 50 == 0:
            out.flush()
            print(f"  ...{n} done", flush=True)
    out.close()
    warm = sorted(lat[1:]) if len(lat) > 1 else lat
    def pct(p): return warm[min(len(warm)-1, int(len(warm)*p))] if warm else 0
    print("\n===== SUMMARY (corpus reale Metnos) =====")
    print(f"query valutate: {n}  (json invalido: {bad_json})")
    print(f"latency warm: p50={pct(.5):.0f} p90={pct(.9):.0f} p99={pct(.99):.0f} "
          f"max={max(warm):.0f}ms  cold={lat[0]:.0f}ms")
    print(f"\n{'campo':16} {'accordo':>8}  {'LLM>regex':>9}  {'regex>LLM':>9}")
    for f in FIELDS:
        print(f"{f:16} {100*agree[f]/n:7.1f}%  {llm_found[f]:9}  {regex_found[f]:9}")
    print("\nLLM>regex = casi dove l'LLM ha trovato struttura che il regex ha perso")
    print("regex>LLM = casi dove il regex ha trovato e l'LLM no")
    print("dettaglio disaccordi: /tmp/poc_corpus_results.jsonl (campo diffs)")


if __name__ == "__main__":
    main()
