#!/usr/bin/env python3
"""e2e backend Google — accettazione «tutto funziona».

Divisione dei ruoli (4/7/2026): **Roberto scrive il fix** della lettura Drive
per NOME; **questo test verifica** che l'intera catena Google funzioni end-to-end
via turni reali (`run_turn`), senza mutare Drive di default.

Baseline PRE-fix: S1/S4 VERDI (ricerca Drive + regressione locale), S2/S3 ROSSI
(lettura per nome: il proposer allucina l'id opaco / read senza sorgente).
POST-fix atteso: tutti VERDI.

Live: richiede OAuth `google_token.json`. Uso:
    python3 tests/e2e/manual/google_backend_live.py            # read-only
    python3 tests/e2e/manual/google_backend_live.py --mutating # + create/delete roundtrip
Esce 0 se ogni scenario `must_pass` è VERDE.
"""
import os
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SUPRA = _ROOT.parent / "suprastructure" / "src"
sys.path[:0] = [str(_ROOT), str(_ROOT / "runtime"), str(_SUPRA)]

# ENV di PROD applicati SOLO in esecuzione diretta (6/7): questo è uno SCRIPT
# e2e manuale, non un test pytest. Col vecchio nome test_* e l'env-block a
# module-level, la COLLEZIONE pytest della suite completa lo importava e
# avvelenava i test successivi (grammar/prefilter/praxis forzati) — era LUI
# il «flaky» di test_X_al_mare + test_find_files_still_top (riproduttore
# -k "planner_routing_composition or prefilter"). File rinominato senza
# prefisso test_ + guardia __main__: doppia protezione.
def _apply_prod_env() -> None:
    for _k, _v in {
        "METNOS_ENGINE": "v3", "METNOS_INTENT_CLASSIFIER": "1",
        "METNOS_PROPOSER_GRAMMAR": "1", "METNOS_PROPOSER_VERB_FILTER": "1",
        "METNOS_PREFILTER_RULES": "1", "METNOS_PROPOSER_FAST_CONFIDENCE": "0.70",
        "METNOS_DEFAULT_MAIL_ACCOUNT": "knowcastle", "METNOS_PRAXIS": "1",
        "METNOS_PRAXIS_AUTO_PROMOTE": "1", "METNOS_PLANNER_LEGACY": "0",
        "METNOS_PRAXIS_FALLBACK": "1",
    }.items():
        os.environ[_k] = _v

# Token che devono comparire nel contenuto letto del KAKEBO (id reali su Drive).
KAKEBO_TOKENS = ("kakebo", "spesa", "111,18", "palestra")


def _steps(res):
    out = []
    for s in getattr(res, "steps", []) or []:
        r = s.result if isinstance(s.result, dict) else {}
        ent = r.get("entries") if isinstance(r.get("entries"), list) else []
        out.append({
            "tool": s.chosen_tool or "",
            "client": (s.resolved_args or {}).get("client"),
            "ok": r.get("ok"),
            "content": r.get("content") or "",
            "entries": ent,
            "error": str(r.get("error") or "")[:80],
        })
    return out


def _aggregate_text(steps):
    parts = []
    for st in steps:
        parts.append(st["content"])
        for e in st["entries"]:
            if isinstance(e, dict):
                parts.append(str(e.get("content") or e.get("body_text") or ""))
            elif isinstance(e, list):
                # read_files_spreadsheet ritorna le entries come RIGHE (list di
                # celle), non dict (§2.6): i valori-cella provano la lettura.
                parts.append(" ".join(str(c) for c in e))
            else:
                parts.append(str(e))
    return " ".join(parts).lower()


# ── checks ──────────────────────────────────────────────────────────────────
def chk_search_drive(res, steps):
    n = sum(len(st["entries"]) for st in steps
            if "find_files" in st["tool"] and st["client"] == "google_workspace" and st["ok"])
    return n >= 1, f"find_files(gw) entries={n}"


def chk_read_name(res, steps):
    txt = _aggregate_text(steps)
    has = any(t in txt for t in KAKEBO_TOKENS)
    kind_ok = getattr(res, "final_kind", "") == "answer"
    any_ok = any(st["ok"] for st in steps)
    return (has and kind_ok and any_ok), \
        f"content_kakebo={has} kind={getattr(res,'final_kind','?')} any_ok={any_ok}"


def chk_local_regression(res, steps):
    n = sum(len(st["entries"]) for st in steps
            if st["ok"] and st["client"] in (None, "local"))
    no_gw = all(st["client"] != "google_workspace" for st in steps)
    return (n >= 1 and no_gw), f"local entries={n} no_gw={no_gw}"


SCENARIOS = [
    {"id": "S1", "must_pass": True, "green_now": True,
     "query": "cerca su google drive KAKEBO SPESE 2026",
     "check": chk_search_drive},
    {"id": "S2", "must_pass": True, "green_now": False,   # ← target del fix
     "query": "leggimi il contenuto del documento KAKEBO SPESE 2026 su google drive",
     "check": chk_read_name},
    {"id": "S3", "must_pass": True, "green_now": False,   # ← target del fix (foglio)
     "query": "leggi il foglio KAKEBO SPESE 2026 su google drive",
     "check": chk_read_name},
    {"id": "S4", "must_pass": True, "green_now": True,
     "query": "trova i file manifest.toml in /opt/metnos/executors/read_files",
     "check": chk_local_regression},
]


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutating", action="store_true",
                    help="include create/delete roundtrip su Drive")
    args = ap.parse_args()

    print("=" * 72)
    print("e2e backend Google — accettazione «tutto funziona»")
    print("=" * 72)
    rows, failed_must = [], 0
    for sc in SCENARIOS:
        try:
            from agent_runtime import run_turn  # lazy: solo in esecuzione diretta
            res = run_turn(sc["query"], actor="host", channel="http")
            steps = _steps(res)
            ok, detail = sc["check"](res, steps)
        except Exception as ex:  # noqa: BLE001
            ok, detail, steps = False, f"EXC {ex!r}", []
        tools = ">".join(st["tool"] for st in steps) or "(no steps)"
        status = "VERDE" if ok else "ROSSO"
        note = ""
        if not ok and not sc["green_now"]:
            note = " (atteso ROSSO pre-fix)"
        if not ok and sc["green_now"]:
            note = " ⚠ REGRESSIONE (era verde)"
        if ok and not sc["green_now"]:
            note = " ✓ FIXATO"
        if not ok and sc["must_pass"]:
            failed_must += 1
        rows.append((sc["id"], status, sc["query"][:52], tools[:46], detail, note))

    w = max(len(r[2]) for r in rows)
    for rid, status, q, tools, detail, note in rows:
        print(f"[{rid}] {status:5} | {q:<{w}} | {tools}")
        print(f"        {detail}{note}")
    print("-" * 72)
    print(f"must_pass falliti: {failed_must}/{sum(1 for s in SCENARIOS if s['must_pass'])}")
    if args.mutating:
        print("(--mutating: roundtrip create/delete non ancora implementato)")
    print("VERDETTO:", "TUTTO VERDE" if failed_must == 0 else
          f"{failed_must} da fixare (S2/S3 sono il target di Roberto)")
    return 0 if failed_must == 0 else 1


if __name__ == "__main__":
    _apply_prod_env()
    sys.exit(run())
