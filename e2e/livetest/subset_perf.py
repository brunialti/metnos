#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""subset_perf.py — misuratore perf SEQUENZIALE (no contesa GPU) per A/B leve.

Esegue un subset di query del livetest UNA ALLA VOLTA contro :8770 e riporta
ms + ok + tools per ciascuna, con confronto al baseline r_full_2. NON tocca il
DB del test. Uso: python3 subset_perf.py [web]
"""
import json, sys, time, re, urllib.request

EP = "http://127.0.0.1:8770/agent/turn"
TD = "/tmp/metnos_livetest"; EM = "user@example.com"; RP = "brunialti/metnos"

# (ord, baseline_ms r_full_2, expect_re, query)
SUB = [
    (3, 29338, r"find_issues_github|read_issues_github",
     f"Trova le issue su {RP} e dimmi quante sono aperte e quante chiuse"),
    (5, 30984, r"find_places",
     "Che ore sono adesso e trova le farmacie vicino a Padova ordinate per distanza"),
    (7, 33463, r"get_processes",
     "Trova i 5 processi che usano più memoria e dimmi quanta memoria totale occupano insieme"),
    (1, 41291, r"write_files|create_files",
     f"Trova tutti i file .txt in {TD}, conta le righe totali e scrivi un report con la data di oggi in {TD}/report_sub.txt"),
    (6, 42385, r"read_messages|find_messages",
     "Riassumi le ultime mail ricevute su metnos raggruppate per mittente"),
    (12, 37242, r"find_files|filter|read_files",
     f"Nei file di testo dentro {TD} trova le righe che contengono 'todo' e dimmi quante sono"),
]
WEB = [
    (4, 183192, r"find_urls",
     f"Cerca online cos'è RISC-V, estrai 3 punti chiave e salvali in {TD}/riscv_sub.txt"),
    (13, 98962, r"find_issues_github|read_issues_github",
     f"Trova le issue aperte su {RP}, poi cerca online cos'è il progetto Metnos per dare contesto, e salva un riepilogo che unisce le issue e il contesto in {TD}/issues_brief_sub.txt"),
]
MARK = ["mi sono bloccato", "ricerca interrotta", "non sono riuscito",
        "riformula la richiesta"]


def run(q, expect):
    body = json.dumps({"query": q, "actor": "roberto",
                       "conversation_id": "subperf"}).encode()
    req = urllib.request.Request(EP, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    t0 = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=600))
    ms = int((time.time() - t0) * 1000)
    steps = d.get("steps_summary") or []
    tools = [s.get("tool") for s in steps if s.get("tool")]
    fk = d.get("final_kind") or ""
    fm = (d.get("final_message") or "").strip().lower()
    ok = True; why = ""
    if fk in ("error", "ask") or not fm:
        ok = False; why = f"final_kind={fk}/empty"
    elif any(s.get("ok") is False for s in steps):
        ok = False; why = f"step ok=False {[s.get('tool') for s in steps if s.get('ok') is False]}"
    elif any(m in fm for m in MARK):
        ok = False; why = "fallback/resa"
    elif expect and not any(re.search(expect, t or "") for t in tools):
        ok = False; why = f"misroute (atteso /{expect}/)"
    return ms, ok, why, tools


def main():
    items = SUB + (WEB if "web" in sys.argv else [])
    tot = 0; base = 0; fails = 0
    print(f"=== SUBSET PERF (n={len(items)}, sequenziale) ===", flush=True)
    for ordn, b, ex, q in items:
        try:
            ms, ok, why, tools = run(q, ex)
        except Exception as e:
            ms, ok, why, tools = 0, False, f"EXC {type(e).__name__}: {e}", []
        tot += ms; base += b; fails += 0 if ok else 1
        spd = f"{b/ms:.2f}x" if ms else "-"
        st = "PASS" if ok else f"FAIL[{why}]"
        print(f"q{ordn:>2} {ms:>7}ms (base {b}ms, {spd}) {st} {tools}", flush=True)
    print(f"--- TOT {tot/1000:.1f}s vs base {base/1000:.1f}s · "
          f"speedup {base/tot:.2f}x · FAIL {fails}/{len(items)} ---", flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
