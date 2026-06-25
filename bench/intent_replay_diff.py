#!/usr/bin/env python3
"""intent_replay_diff.py — rete di regressione per il pilota intent boundary.

Rieseguito l'intent extractor su un CAMPIONE di query REALI del corpus turni
(~/.local/share/metnos/turns/*.jsonl), DUE volte: flag OFF (template v3
attuale) e flag ON (v4 boundary-driven). Diff verb/object per query.

Oracolo = DIFF rivisto, NON uguaglianza: alcuni delta sono FIX voluti
(es. url-content get→read). Il report mostra ogni delta old→new per vaglio.

Campione: (A) query con termini read/get-sensibili (dove il confine morde) +
(B) controllo casuale (per scoprire regressioni inattese altrove).

Uso:  python3 bench/intent_replay_diff.py [N_targeted] [N_control]
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "runtime"))

TURNS_DIR = Path.home() / ".local/share/metnos/turns"

# Termini dove il confine read/get/find/list/filter morde davvero.
SENSITIVE = (
    "scarica", "download", "leggi", "leggimi", "apri", "ottieni", "dammi",
    "dimmi", "mostra", "contenuto", "url", "http", "json", "endpoint", "api",
    "dimension", "metadat", "size", "peso", "sha", "data ", "elenca", "lista",
    "filtra", "scarta", "tieni", "cerca", "trova", "fetch", "read", "get",
)


def _load_distinct_queries() -> list[str]:
    seen = set()
    out = []
    for fn in sorted(glob.glob(str(TURNS_DIR / "*.jsonl"))):
        try:
            fh = open(fn)
        except OSError:
            continue
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            q = (r.get("user_query") or "").strip()
            if not q or len(q) < 4 or q in seen:
                continue
            seen.add(q)
            out.append(q)
    return out


def _vo(d) -> str:
    if not d:
        return "None"
    return f"{d.get('verb')}/{d.get('object')}"


def main() -> int:
    n_targeted = int(sys.argv[1]) if len(sys.argv) > 1 else 70
    n_control = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    spec = importlib.util.spec_from_file_location(
        "rsb", str(Path(__file__).resolve().parent / "routing_subset_bench.py"))
    rsb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rsb)
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    allq = _load_distinct_queries()
    print(f"corpus: {len(allq)} query distinte")

    targeted = [q for q in allq if any(t in q.lower() for t in SENSITIVE)]
    control = [q for q in allq if q not in set(targeted)]
    # campionamento deterministico (stride) — niente random per riproducibilità
    def _stride(lst, n):
        if n <= 0 or not lst:
            return []
        if len(lst) <= n:
            return lst
        step = len(lst) / n
        return [lst[int(i * step)] for i in range(n)]

    sample = _stride(targeted, n_targeted) + _stride(control, n_control)
    print(f"campione: {len(sample)} (targeted={min(n_targeted,len(targeted))} "
          f"+ control={min(n_control,len(control))})\n")

    def _run(q, flag):
        os.environ["METNOS_INTENT_BOUNDARIES"] = flag
        return _vo(extract_intent(q, fast))

    same = 0
    changed = []
    for i, q in enumerate(sample):
        a, b = _run(q, "0"), _run(q, "1")
        if a == b:
            same += 1
        else:
            changed.append((q, a, b))
        if (i + 1) % 20 == 0:
            print(f"  ...{i+1}/{len(sample)}")

    # Conferma di stabilità sui delta: K run extra per flag. Un delta è REALE
    # solo se off e on sono ciascuno STABILE (sempre lo stesso) e diversi fra
    # loro; altrimenti è RUMORE (non-determinismo LLM, noto sugli output lunghi).
    K = 4
    print(f"\n  ...conferma stabilità su {len(changed)} delta (K={K} run/flag)")
    confirmed, flaky = [], []
    for q, a, b in changed:
        offs = {a} | {_run(q, "0") for _ in range(K)}
        ons = {b} | {_run(q, "1") for _ in range(K)}
        if len(offs) == 1 and len(ons) == 1 and offs != ons:
            confirmed.append((q, next(iter(offs)), next(iter(ons))))
        else:
            flaky.append((q, sorted(offs), sorted(ons)))

    os.environ["METNOS_INTENT_BOUNDARIES"] = "0"
    n = len(sample)
    print(f"\n==== RISULTATO ====")
    print(f"identici (no delta):        {same}/{n} = {100*same/n:.1f}%")
    print(f"delta osservati:            {len(changed)}")
    print(f"  → STABILI (reali):        {len(confirmed)}")
    print(f"  → FLAKY (rumore LLM):     {len(flaky)}")
    print(f"\n-- DELTA STABILI (da classificare fix|regress) --")
    for q, a, b in confirmed:
        print(f"  Δ {a:18s} → {b:18s}  | {q[:68]}")
    print(f"\n-- FLAKY (oscillano, non imputabili al cambio) --")
    for q, offs, ons in flaky:
        print(f"  ~ off{offs} on{ons} | {q[:55]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
