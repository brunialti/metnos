#!/usr/bin/env python3
"""Cattura e classifica le divergenze B(flat) vs A(full) su corpus reale.

Sotto "accuratezza irrinunciabile" la decisione B-vs-A dipende SOLO da: le
query dove differiscono, B e' giusto/equivalente o peggiore? Stampa ogni
divergenza coi campi + i raw, per giudizio. Env N (default 300, passo fisso).
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import optimize as O  # riusa schemi/instr/call/parse/canon/six

N = int(os.environ.get("N", "300"))
qs = [l.strip() for l in open("/tmp/metnos_queries_uniq.txt", errors="replace") if l.strip()]
sample = qs[::max(1, len(qs)//N)][:N]

A = ("A", O.SCHEMA_FULL, O.INSTR, O.canon_nested)
B = ("B", O.SCHEMA_FLAT, O.INSTR_FLAT, O.canon_flat)
FIELDS = ["ordering.mode", "ordering.desc", "time", "recur", "count", "viz"]

def reduce6(c):
    return [c["ordering.mode"], c["ordering.desc"], bool(c["time_window"]),
            bool(c["recurrence.every"]), c["count_intent"], c["visualize_intent"]]

diffs = []
for q in sample:
    try:
        _, _, ra = O.call(q, A[1], A[2], False, None); da = O.parse(ra)
        _, _, rb = O.call(q, B[1], B[2], False, None); db = O.parse(rb)
    except Exception:
        continue
    if da is None or db is None:
        continue
    ca, cb = reduce6(A[3](da)), reduce6(B[3](db))
    if ca != cb:
        d = {FIELDS[i]: (ca[i], cb[i]) for i in range(6) if ca[i] != cb[i]}
        diffs.append((q, d))

print(f"N={len(sample)}  divergenze B-vs-A: {len(diffs)} ({100*len(diffs)/len(sample):.1f}%)")
for q, d in diffs:
    print(f"\nQ: {q[:78]}")
    for f, (a, b) in d.items():
        print(f"   {f}: A={a!r}  B={b!r}")
