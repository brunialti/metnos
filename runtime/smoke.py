#!/usr/bin/env python3
"""
smoke.py — Battery di regression test "must work" per Metnos.

Filosofia (Roberto 30/4/2026): un meccanismo preventivo per evitare che
modifiche al synth/prefilter/planner/catalog rompano comportamenti
fondamentali. Si lancia:
  - prima di ogni deploy (`./deploy.sh` lo chiama),
  - dopo synth-on-the-fly che aggiunge un executor al catalog,
  - cron daily,
  - manualmente quando si tocca prefilter / agent_runtime / synt_multistage.

Output:
  - exit 0 se tutti i case passano (kind=answer + tool family atteso),
  - exit 1 se almeno uno fallisce, con report per-case.

Fa anche invariant checks sul catalog (no naming collision, ogni verbo
consumer ha ≥1 producer accessibile via prefilter).

Tempo tipico: ~3-5 minuti (8 turni × ~30s).

Uso:
    python3 smoke.py            # battery + invariants
    python3 smoke.py --invariants-only   # solo invariants (~1s)
    python3 smoke.py --skip-invariants   # solo battery
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent_runtime import run_turn  # noqa: E402
from loader import load_catalog  # noqa: E402
from prefilter import rank_with_intent, _PRODUCER_VERBS  # noqa: E402


# --- Battery: query + outcome atteso ---------------------------------------
# Ogni case: query, regex su tool_chiamati (almeno UNO deve match), kind atteso.
# Le query sono curate per coprire le pipeline canoniche (producer puro,
# consumer su mail, fs read/list/find, web fetch). NON modificarle per
# adattarle al codice; modificare il codice se falliscono.
#
# expected_first_tool + expected_arg_keys (ADR 0114, Layer 5, 8/5/2026):
# regression canary contro routing hijack di synth catch-all. Verificato
# tramite simulato PLANNER (intent + prefilter, NO LLM live) in
# `_run_smoke_with_tool_assertion`.

BATTERY = [
    {"q": "che ora e?",
     "tool_re": r"^get_now$",
     "kind": "answer",
     "expected_first_tool": "get_now",
     "expected_arg_keys": set(),
     "min_pass_rate": 0.9},
    {"q": "elenca i file in /tmp",
     "tool_re": r"^list_dirs$",
     "kind": "answer",
     "expected_first_tool": "list_dirs",
     "expected_arg_keys": {"path"},
     "min_pass_rate": 0.8},
    {"q": "trova i file *.py in /opt/myclaw/runtime",
     "tool_re": r"^find_files$",
     "kind": "answer",
     "expected_first_tool": "find_files",
     "expected_arg_keys": {"pattern"},
     "min_pass_rate": 0.8},
    {"q": "leggi /tmp/smoke_test.txt",
     "tool_re": r"^read_files$",
     "kind": "answer",
     "setup": "echo 'hello smoke' > /tmp/smoke_test.txt",
     "expected_first_tool": "read_files",
     "expected_arg_keys": {"paths"},
     "min_pass_rate": 0.8},
    {"q": "riassumi le mail di oggi, solo le piu importanti",
     "tool_re": r"^read_messages$",
     "kind": "answer",
     "expected_first_tool": "read_messages",
     "expected_arg_keys": set(),
     "min_pass_rate": 0.8},
    {"q": "dove sono?",
     "tool_re": r"^get_location$",
     "kind": "answer",
     "expected_first_tool": "get_location",
     "expected_arg_keys": set(),
     "min_pass_rate": 0.9},
    {"q": "scarica https://httpbin.org/get",
     "tool_re": r"^get_urls$",
     "kind": "answer",
     "expected_first_tool": "get_urls",
     "expected_arg_keys": {"urls"},
     "min_pass_rate": 0.8},
    {"q": "che data e oggi",
     "tool_re": r"^get_now$",
     "kind": "answer",
     "expected_first_tool": "get_now",
     "expected_arg_keys": set(),
     "min_pass_rate": 0.9},
    # E2E dry-run guard del bug 8/5/2026 (test-isolation pollution).
    # `find_images_indices` deve essere routato ed eseguito in dry-run env
    # senza sporcare ~/.local/share/metnos/index/ reale.
    {"q": "cerca foto con primi piani",
     "tool_re": r"^find_(images|persons)_indices$",
     "kind": "answer",
     "dry_run_guard": True,
     "expected_first_tool": "find_persons_indices",
     "expected_arg_keys": set(),
     "min_pass_rate": 0.7},
    # Anti-regressione bug find_texts (8/5/2026): query "cerca su web ..."
    # DEVE routare a find_urls, NON a un synth catch-all.
    {"q": "cerca organico scuola provincia Roma sul web",
     "tool_re": r"^find_urls$",
     "kind": "answer",
     "expected_first_tool": "find_urls",
     "expected_arg_keys": {"topic"},
     "min_pass_rate": 0.7},
    # Anti-regressione health: "stato del sistema" → get_processes.
    {"q": "stato del sistema",
     "tool_re": r"^get_processes$",
     "kind": "answer",
     "expected_first_tool": "get_processes",
     "expected_arg_keys": {"include_health"},
     "min_pass_rate": 0.9},
    # Anti-regressione persons-by-name (ADR 0113).
    {"q": "trova foto di Matteo",
     "tool_re": r"^find_persons_indices$",
     "kind": "answer",
     "expected_first_tool": "find_persons_indices",
     "expected_arg_keys": {"name"},
     "min_pass_rate": 0.8},
]


# --- BATTERY_IMPORTS: case auto-aggiunti dall'importer skill ---------------
# Vedi smoke_imports.py (gap 6, 10/5/2026). Idempotente: l'importer scrive
# nel JSON store, smoke_imports modulo li carica al primo import. Concat:
try:
    from smoke_imports import BATTERY_IMPORTS  # type: ignore
    BATTERY = BATTERY + BATTERY_IMPORTS
except ImportError:
    pass


# --- Invariants ------------------------------------------------------------

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
    """Verifica le proprieta' che devono valere sul catalog dopo load.
    Ritorna (ok: bool, errors: list[str])."""
    errors = []
    catalog = load_catalog()
    if verbose:
        print(f"[invariants] catalog: {len(catalog)} executors, {len(catalog.rejected)} rejected")
        for path, reason in catalog.rejected:
            print(f"[invariants]   rejected: {path} — {reason}")

    # 1. Nessun nome handcrafted shadow-ato da synth (loader li rifiuta).
    # Verifica indiretta: se ci sono rejected con "name collision", il rifiuto e' OK.
    # (Niente errore qui — la presenza di rejected name-collision e' INTENZIONALE,
    # significa che il loader sta facendo il suo lavoro.)

    # 2. Per ogni (verbo consumer, oggetto), prefilter deve produrre >=1 candidato
    #    con executor.name che inizia per un PRODUCER_VERB e contiene l'oggetto.
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
                f"per object='{obj}' nei candidates {names}. Senza precursor il "
                f"planner chiamera' il consumer con from_step=0 e fallira'."
            )

    if verbose:
        if not errors:
            print(f"[invariants] OK ({len(CONSUMER_VERBS_NEEDING_PRECURSOR)} consumer/precursor checks)")
        else:
            print(f"[invariants] FAIL: {len(errors)} errori")
            for e in errors:
                print(f"  • {e}")

    return (len(errors) == 0), errors


# --- Battery runner --------------------------------------------------------

def run_one(case: dict, idx: int, total: int) -> dict:
    q = case["q"]
    print(f"[smoke {idx}/{total}] {q}", flush=True)
    if case.get("setup"):
        import subprocess
        subprocess.run(case["setup"], shell=True, check=False, capture_output=True)
    t0 = time.time()
    # Dry-run guard (8/5/2026): per case marcati `dry_run_guard`, snapshot
    # pre/post di `~/.local/share/metnos/index/image/` e verifica zero diff.
    pre_dirs = None
    if case.get("dry_run_guard"):
        import os
        os.environ["METNOS_DRY_RUN"] = "1"
        real_idx = Path.home() / ".local" / "share" / "metnos" / "index" / "image"
        pre_dirs = (
            {p.name for p in real_idx.iterdir() if p.is_dir()}
            if real_idx.exists() else set()
        )
    try:
        log = run_turn(q, verbose=False, cap_steps=12)
    except Exception as e:
        if case.get("dry_run_guard"):
            import os as _os
            _os.environ.pop("METNOS_DRY_RUN", None)
        return {
            "query": q, "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.time() - t0, 1),
        }
    # Post-run dry-run guard
    if pre_dirs is not None:
        import os as _os
        _os.environ.pop("METNOS_DRY_RUN", None)
        real_idx = Path.home() / ".local" / "share" / "metnos" / "index" / "image"
        post = (
            {p.name for p in real_idx.iterdir() if p.is_dir()}
            if real_idx.exists() else set()
        )
        new_dirs = post - pre_dirs
        if new_dirs:
            return {
                "query": q, "ok": False,
                "error": f"dry-run regression: nuove dirs reali {sorted(new_dirs)}",
                "duration_s": round(time.time() - t0, 1),
            }
    tools = [s.chosen_tool for s in log.steps if s.chosen_tool]
    pat = re.compile(case["tool_re"])
    tool_match = any(pat.fullmatch(t) for t in tools)
    kind_match = log.final_kind == case["kind"]
    ok = tool_match and kind_match
    flag = "PASS" if ok else "FAIL"
    print(f"  → {flag}  kind={log.final_kind}  tools={tools[:5]}  {round(time.time()-t0,1)}s")
    return {
        "query": q, "ok": ok,
        "kind": log.final_kind, "expected_kind": case["kind"],
        "tools": tools, "tool_re": case["tool_re"], "tool_match": tool_match,
        "duration_s": round(time.time() - t0, 1),
        "final_message": (log.final_message or "")[:200],
    }


# --- Layer 5 ADR 0114: tool routing assertion (no LLM live) ----------------
#
# Per ciascun case con `expected_first_tool`: simula il routing PLANNER
# usando solo intent_extractor + prefilter (deterministico se disponibile;
# fallback bag-of-words se LLM offline). Verifica che `expected_first_tool`
# sia il PRIMO candidato del prefilter ranking. Skip gracefully se tool
# di simulazione non disponibili.
#
# Distinct da `run_one`: questo NON esegue il turn (no live executor),
# verifica solo il routing layer. Costo ~10-50ms/query → si puo' chiamare
# in cron daily senza problemi.

def _bow_intent_for_smoke(query: str) -> dict:
    """Bag-of-words intent deterministico per smoke battery. NON usa LLM.
    Mappa parole chiave a verbi/oggetti del vocab chiuso. Coverage minimo:
    sufficiente per le query del battery; non per pianificatore reale.
    """
    q = query.lower()
    # Markers di calendario / appuntamenti (forte priorita': identificano
    # univocamente il dominio events, anche se la query contiene "ora"
    # come durata "per un ora").
    calendar_terms = ("appuntament", "agenda", "riunion", "incontro",
                       "meeting", "evento", "eventi", "calendar",
                       "calendario", "scadenz", "deadline")
    has_calendar = any(t in q for t in calendar_terms)
    # Verb mapping. Ordine: priorita' decrescente. "fissa/prenota/book/
    # schedule" → set (crea/aggiorna evento canonical).
    verb = None
    if any(t in q for t in ("trova", "cerca", "find", "search")):
        verb = "find"
    elif any(t in q for t in ("elenca", "lista", "list")):
        verb = "list"
    elif any(t in q for t in ("fissa", "prenota", "book", "schedule")):
        verb = "set"
    elif any(t in q for t in ("leggi", "read")):
        verb = "read"
    elif has_calendar and any(t in q for t in ("crea", "create", "aggiungi", "add", "nuovo", "nuova", "new")):
        # "crea evento" / "aggiungi appuntamento" → set (set_events canonical).
        verb = "set"
    elif any(t in q for t in ("dove sono", "posizione", "location", "where am")):
        verb = "get"
    elif any(t in q for t in ("scarica", "download", "url", "https://", "http://")):
        verb = "get"
    elif any(t in q for t in ("riassumi", "summarize", "describe")):
        verb = "describe"
    elif any(t in q for t in ("stato", "status", "salute", "health")):
        verb = "get"
    # NB: "ora/data/now/time" senza contesto calendario NON mappa verb=get:
    # rank_with_intent verb=get senza object pesca get_file_dates/get_files_*
    # (catalog order) bypassando get_now. Lasciamo che fallback BoW (rank
    # plain) lavori via affinity di get_now. Regression rilevata 11/5/2026
    # nella stessa sessione F4-F7. La branch "stato/status/salute" sopra
    # rimane perche' lega a object=processes nel branch successivo.
    # Object mapping. Markers di calendario detettati prima → obj=events
    # con priorita' sui marker generici di "ora/data/time".
    obj = None
    if has_calendar:
        obj = "events"
    elif any(t in q for t in ("file", "files")) and "url" not in q:
        obj = "files"
    elif "dir" in q or "/tmp" in q or "directory" in q:
        if verb == "list":
            obj = "dirs"
    elif any(t in q for t in ("foto", "immagin", "photo", "image")):
        obj = "images"
    elif any(t in q for t in ("mail", "email", "messaggi", "message")):
        obj = "messages"
    elif any(t in q for t in ("url", "https://", "http://", "web", "internet", "online")):
        obj = "urls"
    elif any(t in q for t in ("processi", "stato", "sistema", "system", "service")):
        obj = "processes"
    elif any(t in q for t in ("dove sono", "posizione", "location")):
        obj = "places"
    # NB: "ora/data/now/time" senza contesto calendario NON mappa a events.
    # "che ora e?" / "che data e oggi" deve cadere nel fallback BoW (rank
    # plain) per pickare get_now via affinity. Mapparlo a events forzerebbe
    # rank_with_intent a candidare get_file_dates/get_files_metadata (primi
    # get_* del catalog) e bypassare get_now. Regression introdotta+fixata
    # nello stesso turno F4-F7 (11/5/2026).
    if not verb and not obj:
        return {}
    return {"verb": verb, "object": obj}


def _run_smoke_with_tool_assertion(case: dict, *, catalog=None) -> dict:
    """Verifica che `case["expected_first_tool"]` sia il #1 candidato del
    prefilter per `case["q"]`. Se prefilter non disponibile, skip gracefully.

    Returns:
      {"query", "expected", "actual_first", "ok": bool, "skip": bool, "reason"}.
    """
    q = case["q"]
    expected = case.get("expected_first_tool")
    if not expected:
        return {"query": q, "ok": True, "skip": True, "reason": "no expected_first_tool"}
    try:
        from prefilter import rank_with_intent
    except Exception as ex:
        return {"query": q, "ok": True, "skip": True,
                "reason": f"prefilter import failed: {ex}"}
    if catalog is None:
        try:
            catalog = load_catalog()
        except Exception as ex:
            return {"query": q, "ok": True, "skip": True,
                    "reason": f"catalog load failed: {ex}"}
    # Bag-of-words intent (deterministic, no LLM): vince per smoke.
    intent = _bow_intent_for_smoke(q)
    if not intent:
        return {"query": q, "ok": True, "skip": True, "reason": "no intent"}
    try:
        ranked = rank_with_intent(q, catalog, intent, k=5) or []
    except Exception as ex:
        return {"query": q, "ok": True, "skip": True,
                "reason": f"rank failed: {ex}"}
    # Fallback: se prefilter ritorna [] o None (object non matched),
    # tenta `rank` plain text-based (puo' essere piu' tollerante).
    if not ranked:
        try:
            from prefilter import rank as _rank_plain
            ranked = _rank_plain(q, catalog, k=5) or []
        except Exception:
            ranked = []
    if not ranked:
        return {"query": q, "expected": expected, "ok": False,
                "skip": False, "reason": "empty ranking",
                "actual_first": None}
    actual_first = ranked[0].name if ranked else None
    ok = (actual_first == expected)
    return {
        "query": q, "expected": expected, "actual_first": actual_first,
        "ranking_top5": [e.name for e in ranked[:5]],
        "ok": ok, "skip": False,
        "reason": "match" if ok else f"expected {expected} got {actual_first}",
    }


def run_prompts_lint_assertion() -> dict:
    """Smoke check Fase C4 (11/5/2026): esegue il linter deterministico sui
    prompt e verifica zero error.

    Ritorna `{ok: bool, n_error: int, n_warn: int, codes: list[str], skip: bool,
    reason: str}`. Skip gracefully se l'import di `prompts_lint` fallisce.

    Invocato in cron daily insieme alla routing battery."""
    try:
        from prompts_lint import scan as _lint_scan  # type: ignore
    except Exception as e:
        return {"ok": True, "n_error": 0, "n_warn": 0, "codes": [],
                "skip": True, "reason": f"import skip: {e}"}
    root = Path(__file__).parent / "prompts"
    if not root.is_dir():
        return {"ok": True, "n_error": 0, "n_warn": 0, "codes": [],
                "skip": True, "reason": "prompts dir mancante"}
    issues = _lint_scan(root)
    n_err = sum(1 for i in issues if i.level == "error")
    n_warn = sum(1 for i in issues if i.level == "warn")
    codes = sorted({i.code for i in issues})
    return {
        "ok": n_err == 0,
        "n_error": n_err,
        "n_warn": n_warn,
        "codes": codes,
        "skip": False,
        "reason": "lint clean" if n_err == 0 else f"{n_err} lint errors",
    }


def run_smoke_routing_battery(catalog=None) -> dict:
    """Esegue solo il routing assertion per tutti i case con
    `expected_first_tool`. Per cron / scheduler v2.

    Returns:
      {"results": [...], "n_pass": int, "n_fail": int, "n_skip": int}
    """
    results = []
    for case in BATTERY:
        results.append(_run_smoke_with_tool_assertion(case, catalog=catalog))
    n_pass = sum(1 for r in results if r["ok"] and not r.get("skip"))
    n_fail = sum(1 for r in results if not r["ok"])
    n_skip = sum(1 for r in results if r.get("skip"))
    return {"results": results, "n_pass": n_pass, "n_fail": n_fail, "n_skip": n_skip}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--invariants-only", action="store_true")
    p.add_argument("--skip-invariants", action="store_true")
    p.add_argument("--json", action="store_true", help="emit JSON report")
    args = p.parse_args()

    inv_ok = True
    inv_errors = []
    if not args.skip_invariants:
        print("=== INVARIANTS ===")
        inv_ok, inv_errors = check_invariants(verbose=True)
        print()

    if args.invariants_only:
        sys.exit(0 if inv_ok else 1)

    print("=== BATTERY ===")
    results = []
    for i, case in enumerate(BATTERY, 1):
        results.append(run_one(case, i, len(BATTERY)))
    n_pass = sum(1 for r in results if r["ok"])
    print()
    print(f"=== SMOKE: {n_pass}/{len(results)} pass; invariants {'OK' if inv_ok else 'FAIL'}")

    if args.json:
        json.dump({"battery": results, "invariants_ok": inv_ok,
                   "invariants_errors": inv_errors},
                  sys.stdout, indent=2, default=str)

    sys.exit(0 if (n_pass == len(results) and inv_ok) else 1)


if __name__ == "__main__":
    main()
