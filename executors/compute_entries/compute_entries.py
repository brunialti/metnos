#!/usr/bin/env python3
"""compute_entries — riduzioni numeriche su lista di entries.

Spec:
- input: entries (lista di flat dict), key (chiave del dict), op (operazione).
- op in: max, min, avg, sum, count, count_distinct.
- output: scalare numerico (o entry stessa per max/min se return_entry=true).
- entries senza la key, con valore None, o valore non numerico (per
  max/min/avg/sum) sono SKIPPATE; il count viene riportato in
  ignored_non_numeric.

Differenza con sort_entries:
- sort_entries ritorna lista ordinata (top-K). compute_entries ritorna
  scalare ridotto.
- sort_entries(by=X, desc=true, top=1) e' equivalente a
  compute_entries(key=X, op="max", return_entry=true) ma compute e' piu'
  diretto e legge solo il valore.

Pure compute, no I/O esterna.

Contratto:
    stdin:  JSON {entries: list[dict], key: str, op: str, return_entry?: bool}
    stdout: JSON {ok, value: <scalare>, op, key, count_input,
                  ignored_non_numeric, entry?: dict (se return_entry)}
"""
from __future__ import annotations

import json
import sys


_OPS_NUMERIC = {"max", "min", "avg", "sum"}
_OPS_COUNT = {"count", "count_distinct"}
_OPS = _OPS_NUMERIC | _OPS_COUNT


def invoke(args):
    entries = args.get("entries")
    key = args.get("key")
    op = args.get("op")
    return_entry = bool(args.get("return_entry", False))

    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid 'entries' (must be a list)"}
    if op not in _OPS:
        return {"ok": False,
                "error": f"invalid op {op!r}. Valid: {sorted(_OPS)}"}
    # 'count' (senza key) e' permesso: count totale di entries.
    if op != "count" and (not isinstance(key, str) or not key):
        return {"ok": False,
                "error": f"missing 'key' (string) for op={op!r}"}

    count_input = len(entries)
    ignored = 0

    if op == "count":
        # count: se key e' specificata, conta entries dove key e' presente
        # con valore non-None; altrimenti, conta tutte.
        if not key:
            return {"ok": True, "value": count_input, "op": op,
                    "count_input": count_input, "ignored_non_numeric": 0}
        v = sum(1 for e in entries
                if isinstance(e, dict) and e.get(key) is not None)
        return {"ok": True, "value": v, "op": op, "key": key,
                "count_input": count_input, "ignored_non_numeric": 0}

    if op == "count_distinct":
        seen = set()
        for e in entries:
            if not isinstance(e, dict):
                continue
            v = e.get(key)
            if v is None:
                continue
            try:
                seen.add(v if isinstance(v, (int, float, str, bool)) else json.dumps(v, sort_keys=True))
            except Exception:
                ignored += 1
        return {"ok": True, "value": len(seen), "op": op, "key": key,
                "count_input": count_input, "ignored_non_numeric": ignored}

    # Numeric ops: max/min/avg/sum.
    # Mantengo il tipo originale (int o float) per preservare l'int output
    # quando tutti i valori sono int (sum/max/min su int → int).
    values: list[tuple[int | float, dict]] = []
    for e in entries:
        if not isinstance(e, dict):
            ignored += 1
            continue
        v = e.get(key)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            ignored += 1
            continue
        values.append((v, e))

    if not values:
        return {"ok": True, "value": None, "op": op, "key": key,
                "count_input": count_input, "ignored_non_numeric": ignored,
                "_note": "no numeric values found for key"}

    nums = [v for v, _ in values]
    if op == "sum":
        result = sum(nums); winner_entry = None
    elif op == "avg":
        result = sum(nums) / len(nums); winner_entry = None
    elif op == "max":
        result, winner_entry = max(values, key=lambda t: t[0])
    elif op == "min":
        result, winner_entry = min(values, key=lambda t: t[0])
    else:
        return {"ok": False, "error": f"unreachable op: {op}"}

    out = {
        "ok": True,
        "value": result,
        "op": op,
        "key": key,
        "count_input": count_input,
        "ignored_non_numeric": ignored,
    }
    if return_entry and winner_entry is not None:
        out["entry"] = winner_entry
    return out


def main():
    try:
        args = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": f"invalid input json: {e}"}))
        return
    sys.stdout.write(json.dumps(invoke(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
