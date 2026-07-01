#!/usr/bin/env python3
"""describe_numbers — executor di Metnos v1.1.

Statistiche descrittive su una lista numerica. Pattern fields[]: l'utente
seleziona quali statistiche calcolare.

Pure compute: nessuna I/O esterna, niente dipendenze (solo statistics
e math da stdlib).

Contratto:
    stdin: JSON {values: list[number], fields?: list[str] | "all"}
    stdout: JSON {ok, n, statistics: {field: value, ...}}
"""
import json
import math
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402

ALL_FIELDS = [
    "n", "mean", "median", "stdev", "variance",
    "min", "max", "range", "sum",
    "p25", "p50", "p75", "p90", "p95", "p99",
    "n_missing",
]


def _percentile(values_sorted, p):
    if not values_sorted:
        return None
    if p <= 0:
        return values_sorted[0]
    if p >= 100:
        return values_sorted[-1]
    k = (len(values_sorted) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values_sorted[int(k)]
    return values_sorted[f] + (values_sorted[c] - values_sorted[f]) * (k - f)


def invoke(args):
    values = args.get("values")
    # §2.1/§2.8: values non pipato (None) o non-lista (scalare/dict per piping
    # impreciso) → lista vuota (stats n=0 ok), MAI hard-fail che spezza la
    # pipeline (describe_numbers è spesso uno step accessorio). Bug q44 5/6.
    if not isinstance(values, list):
        values = [values] if isinstance(values, (int, float)) and not isinstance(values, bool) else []
    fields = args.get("fields")
    if fields is None:
        fields = ["n", "mean", "median", "stdev", "min", "max"]
    if fields == "all":
        fields = list(ALL_FIELDS)
    if not isinstance(values, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="values", of="numbers")}
    if not isinstance(fields, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="fields", of="strings | 'all'")}
    unknown = [f for f in fields if f not in ALL_FIELDS]
    if unknown:
        return {"ok": False, "error": _msg("ERR_UNKNOWN_FIELDS", unknown=unknown, supported=ALL_FIELDS)}

    nums = []
    n_missing = 0
    for v in values:
        if v is None:
            n_missing += 1
            continue
        if isinstance(v, bool):
            n_missing += 1
            continue
        if isinstance(v, (int, float)):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                n_missing += 1
                continue
            nums.append(float(v))
        else:
            n_missing += 1

    n = len(nums)
    out = {}
    if n == 0:
        for f in fields:
            out[f] = 0 if f in ("n", "n_missing", "sum") else None
        out["n_missing"] = n_missing
        return {"ok": True, "n": 0, "n_missing": n_missing, "statistics": out}

    nums_sorted = sorted(nums)

    def _safe(fn):
        try:
            return fn()
        except statistics.StatisticsError:
            return None

    mapping = {
        "n":           lambda: n,
        "n_missing":   lambda: n_missing,
        "mean":        lambda: statistics.fmean(nums),
        "median":      lambda: statistics.median(nums),
        "stdev":       lambda: _safe(lambda: statistics.stdev(nums) if n >= 2 else 0.0),
        "variance":    lambda: _safe(lambda: statistics.variance(nums) if n >= 2 else 0.0),
        "min":         lambda: nums_sorted[0],
        "max":         lambda: nums_sorted[-1],
        "range":       lambda: nums_sorted[-1] - nums_sorted[0],
        "sum":         lambda: math.fsum(nums),
        "p25":         lambda: _percentile(nums_sorted, 25),
        "p50":         lambda: _percentile(nums_sorted, 50),
        "p75":         lambda: _percentile(nums_sorted, 75),
        "p90":         lambda: _percentile(nums_sorted, 90),
        "p95":         lambda: _percentile(nums_sorted, 95),
        "p99":         lambda: _percentile(nums_sorted, 99),
    }
    for f in fields:
        out[f] = mapping[f]()

    return {"ok": True, "n": n, "n_missing": n_missing, "statistics": out}


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
