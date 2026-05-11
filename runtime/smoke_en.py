#!/usr/bin/env python3
"""smoke_en.py — Battery di regression test "must work" in INGLESE.

ADR 0092 Phase 3 milestone (5/5/2026): l'EN deve produrre stessi outcome
canonici di IT. Le 8 query equivalenti a `runtime/smoke.py` ma scritte in
inglese. Esecuzione: `METNOS_LANG=en python3 -m runtime.smoke_en`.

Filosofia: se la traduzione introduce drift semantico, il PLANNER EN
chiama tool sbagliati o cade in cap_same. Smoke EN come milestone di
"Phase 3 done" — 8/8 atteso. Se rosso, iterare sul translator (NON
modificare le query per farle passare, vedi CLAUDE.md §8.5).

Uso:
    METNOS_LANG=en python3 -m runtime.smoke_en
    METNOS_LANG=en python3 -m runtime.smoke_en --json
    METNOS_LANG=en python3 -m runtime.smoke_en --skip-invariants
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Forza METNOS_LANG=en se non gia' settato (utility di lancio).
os.environ.setdefault("METNOS_LANG", "en")

from agent_runtime import run_turn  # noqa: E402
from loader import load_catalog  # noqa: E402
from prefilter import rank_with_intent, _PRODUCER_VERBS  # noqa: E402


# --- Battery EN: stesse 8 query di smoke.py ma in inglese ------------------
# Ogni case ha il MEDESIMO `tool_re`/`kind` di smoke.py: il tool atteso e'
# canonical EN-only (CLAUDE.md §2.2 vocab chiuso). La traduzione del PLANNER
# IT→EN non cambia il vocabolario tool, solo le istruzioni linguistiche.

BATTERY_EN = [
    {"q": "what time is it?",
     "tool_re": r"^get_now$",
     "kind": "answer"},
    {"q": "list files in /tmp",
     "tool_re": r"^list_dirs$",
     "kind": "answer"},
    {"q": "find *.py files in /opt/myclaw/runtime",
     "tool_re": r"^find_files$",
     "kind": "answer"},
    {"q": "read /tmp/smoke_test.txt",
     "tool_re": r"^read_files$",
     "kind": "answer",
     "setup": "echo 'hello smoke' > /tmp/smoke_test.txt"},
    {"q": "summarize today's emails, only the most important ones",
     "tool_re": r"^read_messages$",
     "kind": "answer"},
    {"q": "where am I?",
     "tool_re": r"^get_location$",
     "kind": "answer"},
    {"q": "fetch https://httpbin.org/get",
     "tool_re": r"^get_urls$",
     "kind": "answer"},
    {"q": "what date is today",
     "tool_re": r"^get_now$",
     "kind": "answer"},
]


# --- Invariants (clone di smoke.py) ----------------------------------------

CONSUMER_VERBS_NEEDING_PRECURSOR = [
    ("describe", "messages"),
    ("describe", "files"),
    ("classify", "messages"),
    ("filter", "messages"),
    ("filter", "files"),
    ("move", "messages"),
    ("move", "files"),
    ("delete", "messages"),
    ("delete", "files"),
    ("send", "messages"),
]


def check_invariants(verbose: bool = True):
    """Identico a smoke.py — il catalog non dipende da METNOS_LANG."""
    errors = []
    catalog = load_catalog()
    if verbose:
        print(f"[invariants] catalog: {len(catalog)} executors, {len(catalog.rejected)} rejected")
        for path, reason in catalog.rejected:
            print(f"[invariants]   rejected: {path} — {reason}")
    for verb, obj in CONSUMER_VERBS_NEEDING_PRECURSOR:
        if verb in _PRODUCER_VERBS:
            continue
        intent = {"verb": verb, "object": obj}
        picked = rank_with_intent("dummy", catalog, intent, k=3)
        if not picked:
            errors.append(f"[invariant precursor] verb={verb} object={obj}: nessun candidato dal prefilter")
            continue
        has_producer = any(
            e.name.split("_")[0] in _PRODUCER_VERBS and obj in e.name.split("_")
            for e in picked
        )
        if not has_producer:
            names = [e.name for e in picked]
            errors.append(
                f"[invariant precursor] verb={verb} object={obj}: nessun producer "
                f"per object='{obj}' nei candidates {names}."
            )
    if verbose:
        if not errors:
            print(f"[invariants] OK ({len(CONSUMER_VERBS_NEEDING_PRECURSOR)} consumer/precursor checks)")
        else:
            print(f"[invariants] FAIL: {len(errors)} errori")
            for e in errors:
                print(f"  - {e}")
    return (len(errors) == 0), errors


def run_one(case: dict, idx: int, total: int) -> dict:
    q = case["q"]
    print(f"[smoke-en {idx}/{total}] {q}", flush=True)
    if case.get("setup"):
        import subprocess
        subprocess.run(case["setup"], shell=True, check=False, capture_output=True)
    t0 = time.time()
    try:
        log = run_turn(q, verbose=False, cap_steps=12)
    except Exception as e:
        return {
            "query": q, "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - t0, 1),
        }
    tools = [s.chosen_tool for s in log.steps if s.chosen_tool]
    pat = re.compile(case["tool_re"])
    tool_match = any(pat.fullmatch(t) for t in tools)
    kind_match = log.final_kind == case["kind"]
    ok = tool_match and kind_match
    flag = "PASS" if ok else "FAIL"
    print(f"  -> {flag}  kind={log.final_kind}  tools={tools[:5]}  {round(time.time()-t0,1)}s")
    return {
        "query": q, "ok": ok,
        "kind": log.final_kind, "expected_kind": case["kind"],
        "tools": tools, "tool_re": case["tool_re"], "tool_match": tool_match,
        "duration_s": round(time.time() - t0, 1),
        "final_message": (log.final_message or "")[:200],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--invariants-only", action="store_true")
    p.add_argument("--skip-invariants", action="store_true")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    args = p.parse_args()

    print(f"=== METNOS_LANG={os.environ.get('METNOS_LANG', '?')} ===")
    inv_ok = True
    inv_errors = []
    if not args.skip_invariants:
        print("=== INVARIANTS ===")
        inv_ok, inv_errors = check_invariants(verbose=True)
        print()

    if args.invariants_only:
        sys.exit(0 if inv_ok else 1)

    print("=== BATTERY (EN) ===")
    results = []
    for i, case in enumerate(BATTERY_EN, 1):
        results.append(run_one(case, i, len(BATTERY_EN)))
    n_pass = sum(1 for r in results if r["ok"])
    print()
    print(f"=== SMOKE-EN: {n_pass}/{len(results)} pass; invariants {'OK' if inv_ok else 'FAIL'}")

    if args.json:
        json.dump({"battery": results, "invariants_ok": inv_ok,
                   "invariants_errors": inv_errors,
                   "lang": os.environ.get("METNOS_LANG", "?")},
                  sys.stdout, indent=2, default=str)

    sys.exit(0 if (n_pass == len(results) and inv_ok) else 1)


if __name__ == "__main__":
    main()
