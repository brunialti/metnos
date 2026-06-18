#!/usr/bin/env python3
"""Compound SCALING bench — limiti intrinseci di #azioni × #domini (composer + engine v3).

Roberto 18/6: allargare lo scope dei test sul composer/engine v3 con >=100 query
multi-azione e multi-dominio, generate INCREMENTALMENTE (azioni e domini crescono
proporzionalmente) per trovare i LIMITI intrinseci dei due valori.

Costruzione: una libreria di CATENE per dominio (producer + ops in ordine
naturale, ogni clausola con il suo gold {verb, object}). Una query (a azioni, d
domini) concatena d sotto-catene distribuendo a clausole. Il gold e' la lista
ORDINATA di {verb, object}. Si pianifica A SECCO (riusa compound_dryrun.plan_only:
nessuna esecuzione, zero side-effect) e si confronta il piano col gold.

Metriche per cella (a, d): copertura clausole HARD, ordine corretto, flakiness.
Output: heatmap accuracy[righe=azioni][colonne=domini] → dove collassa.

Run:  METNOS_ENGINE=v3 python3 bench/compound_scaling_bench.py [--per-cell K]
      [--max-actions A] [--runs N] [--save out.json]
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from collections import defaultdict

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "runtime"))
sys.path.insert(0, str(_ROOT / "bench"))

os.environ.setdefault("METNOS_ENGINE", "metis")
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_PREFILTER_RULES", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")

# Verbi SOFT (trasformativi): clausole opzionali — il planner puo' fonderle/
# ometterle senza che sia un errore di copertura (ma non devono rompere l'ordine).
SOFT = {"filter", "sort", "group", "classify", "describe", "render", "compare", "compute"}

# Catene per DOMINIO: (frammento NL, verbo gold, oggetto gold). Producer in testa,
# poi ops in ordine naturale (ognuna opera sull'output della precedente).
# Frasi NATURALI (Roberto 18/6): NIENTE termini-vocabolario letterali
# (entries/messages/images/events/store) — l'utente reale dice "le foto", "la
# posta", "le spese", "gli impegni". Il gold (verbo,oggetto) resta canonico: e'
# l'ENGINE che deve mappare NL→tool (foto→images, posta→messages, spese→entries,
# impegni→events, online→urls, CPU→processes). Cosi' il test stressa la mappatura
# reale, non il match-keyword.
DOMAIN_CHAINS = {
    "files": [
        ("trova i file di log nella cartella /tmp/logs", "find", "files"),
        ("tieni solo quelli piu' vecchi di una settimana", "filter", "files"),
        ("comprimili in uno zip", "compress", "files"),
        ("sposta l'archivio in /backup", "move", "files"),
        ("poi cancella gli originali", "delete", "files"),
    ],
    "posta": [
        ("controlla la posta non letta di oggi", "read", "messages"),
        ("tieni solo quelle con allegati", "filter", "messages"),
        ("mandami un riassunto a roberto@example.com", "send", "messages"),
        ("archivia le altre", "move", "messages"),
    ],
    "spese": [
        ("trova le spese sopra i 100 euro", "find", "entries"),
        ("raggruppale per categoria", "group", "entries"),
        ("registra il totale fra le spese", "write", "entries"),
        ("togli le spese dell'anno scorso", "delete", "entries"),
    ],
    "impegni": [
        ("che impegni ho domani", "find", "events"),
        ("descrivimeli in breve", "describe", "events"),
        ("mandami la lista a roberto@example.com", "send", "messages"),
    ],
    "foto": [
        ("trova le foto scattate ieri", "find", "images"),
        ("scegli quelle col viso in primo piano", "filter", "images"),
        ("comprimile in uno zip", "compress", "images"),
    ],
    "web": [
        ("cerca online le novita' su AMD ROCm", "find", "urls"),
        ("apri i primi due risultati", "read", "urls"),
        ("riassumimeli", "describe", "urls"),
    ],
    "sistema": [
        ("guarda cosa sta consumando piu' CPU", "find", "processes"),
        ("ordinali per memoria", "sort", "processes"),
    ],
}
DOMAINS = list(DOMAIN_CHAINS.keys())


def distribute(a: int, d: int):
    """Distribuisci `a` clausole su `d` domini (rotazione), cap = profondita'
    catena. Ritorna lista di count per dominio scelto, o None se infeasible."""
    if d > len(DOMAINS) or d > a:
        return None
    doms = DOMAINS[:d]
    caps = [len(DOMAIN_CHAINS[x]) for x in doms]
    if sum(caps) < a:
        return None
    counts = [1] * d
    rem = a - d
    i = 0
    guard = 0
    while rem > 0 and guard < 1000:
        if counts[i] < caps[i]:
            counts[i] += 1
            rem -= 1
        i = (i + 1) % d
        guard += 1
    if rem > 0:
        return None
    return list(zip(doms, counts))


def gen_query(a: int, d: int, idx: int):
    """Genera (query, gold_clauses) per (a azioni, d domini). idx varia i domini
    scelti (rotazione) per diversita'. Ritorna None se infeasible."""
    if d > len(DOMAINS):
        return None
    # rotazione dei domini per varieta' fra le K query della cella
    rot = DOMAINS[idx % len(DOMAINS):] + DOMAINS[:idx % len(DOMAINS)]
    # prova a costruire con i primi d domini della rotazione (rispettando i cap)
    chosen = []
    caps_ok = []
    for x in rot:
        chosen.append(x)
        caps_ok.append(len(DOMAIN_CHAINS[x]))
        if len(chosen) == d:
            break
    if len(chosen) < d or sum(caps_ok) < a:
        return None
    # distribuisci a su chosen
    counts = [1] * d
    rem = a - d
    i = 0
    guard = 0
    while rem > 0 and guard < 1000:
        if counts[i] < caps_ok[i]:
            counts[i] += 1
            rem -= 1
        i = (i + 1) % d
        guard += 1
    if rem > 0:
        return None
    clauses = []
    gold = []
    for dom, cnt in zip(chosen, counts):
        for (nl, v, o) in DOMAIN_CHAINS[dom][:cnt]:
            clauses.append(nl)
            gold.append((v, o, nl))   # nl = chiave per ARGS_GOLD (fase args)
    # join naturale: virgole + "e poi" prima dell'ultima
    if len(clauses) >= 2:
        query = ", ".join(clauses[:-1]) + " e poi " + clauses[-1]
    else:
        query = clauses[0]
    return query, gold


def _match(p, h, _PROD):
    """Una clausola gold (gv,go) e' coperta da uno step (pv,po)?
    - PRODUCER (find/read/get/list): l'OGGETTO deve combaciare — find_events NON
      copre find_images (bugfix: il verbo-solo faceva rubare lo slot fra producer
      diversi). Verbo esatto o famiglia-producer (find/read/get/list scambiabili).
    - CONSUMER/transform: basta il VERBO (opera sull'output del producer, l'object
      puo' essere generico — es. compress_files per 'comprimi le foto')."""
    pv, po = p
    gv, go = h
    if gv in _PROD:
        return bool(po and po == go) and (pv == gv or (pv in _PROD and gv in _PROD))
    return bool(pv and pv == gv)


def _count_match(hard, plan, ordered, _PROD):
    """Quante clausole HARD trovano un plan-step distinto. ordered=True →
    match solo IN AVANTI (rispetta l'ordine del gold); False → qualunque
    posizione (presenza)."""
    used = [False] * len(plan)
    pi = 0
    matched = 0
    for h in hard:
        start = pi if ordered else 0
        for j in range(start, len(plan)):
            if not used[j] and _match(plan[j], h, _PROD):
                used[j] = True
                matched += 1
                if ordered:
                    pi = j + 1
                break
    return matched


def score(plan_steps, gold):
    """Copertura clausole HARD: fwd (in ordine gold) vs anyo (presenza qualunque
    ordine) → distingue DROPPED (anyo<hard) da REORDER (anyo==hard, fwd<hard).
    Salva i tool del piano per ispezione."""
    import naming_grammar as _ng
    try:
        from compound_decomposer import PRODUCER_VERBS as _PROD
    except Exception:
        _PROD = {"find", "read", "get", "list"}
    plan = []
    tools = []
    for s in plan_steps:
        t = s.get("tool")
        if not t or t == "final_answer":
            continue
        nc = _ng.parse_name(t)
        plan.append((nc.verb if nc else "", nc.obj if nc else ""))
        tools.append(t)
    hard = [(g[0], g[1]) for g in gold if g[0] not in SOFT]
    fwd = _count_match(hard, plan, True, _PROD)
    anyo = _count_match(hard, plan, False, _PROD)
    return {"hard_total": len(hard), "fwd": fwd, "anyo": anyo,
            "in_order": fwd == len(hard), "n_plan_exec": len(plan),
            "tools": tools}


# ── Heatmap colorata dinamica (Roberto 18/6) ──────────────────────────────
_TTY = sys.stdout.isatty()


def _c(s, code):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def outcome(sc, flaky):
    """Esito di una query: ok | reorder (tutte le clausole ma fuori ordine) |
    error (clausola HARD persa) | flaky (piano instabile fra i run)."""
    if flaky:
        return "flaky"
    if sc["anyo"] < sc["hard_total"]:
        return "error"
    if not sc["in_order"]:
        return "reorder"
    return "ok"


_OUT_CH = {"ok": "●", "reorder": "◑", "error": "✗",
           "flaky": "≈", None: "·"}
_OUT_COL = {"ok": "32", "reorder": "33", "error": "31", "flaky": "35", None: "90"}


def _block(o):
    return _c(_OUT_CH.get(o, "·"), _OUT_COL.get(o, "90"))


def render_heatmap(results, amax, dmax, k, status):
    """results[(a,d)] = lista esiti (len = #query cella; None = da-fare). Ogni
    cella = k blocchi colorati (uno per query). Ritorna lista di righe."""
    lines = [_c("  SCALING — #azioni (righe) x #domini (colonne)", "1")]
    head = "  a\\d "
    for d in range(1, dmax + 1):
        head += ("d" + str(d)).ljust(k + 1)
    lines.append(head)
    for a in range(2, amax + 1):
        row = f"  {a:>2}  "
        for d in range(1, dmax + 1):
            cell = results.get((a, d))
            row += (" " * (k + 1)) if cell is None else \
                ("".join(_block(o) for o in cell) + " ")
        lines.append(row)
    lines.append("  " + "  ".join([
        f"{_block('ok')}=ok", f"{_block('reorder')}=fuori-ordine",
        f"{_block('error')}=errore", f"{_block('flaky')}=flaky",
        f"{_block(None)}=da-fare"]))
    lines.append(_c(f"  {status}", "2"))
    return lines


def draw(lines, prev_h):
    """Redraw in place su TTY (cursore su prev_h righe + clear). Non-TTY: stampa."""
    out = sys.stdout
    if _TTY and prev_h:
        out.write(f"\033[{prev_h}A")
    out.write("\033[J")
    out.write("\n".join(lines) + "\n")
    out.flush()
    return len(lines)


def write_live(path, engine, iteration, rows, done, total):
    """Snapshot incrementale (scrittura atomica) per la dashboard HTTP."""
    import datetime as _dt
    data = {"engine": engine, "iter": iteration, "done": done, "total": total,
            "ts": _dt.datetime.now().strftime("%H:%M:%S"), "rows": rows}
    tmp = path + ".tmp"
    Path(tmp).write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-cell", type=int, default=4)
    ap.add_argument("--max-actions", type=int, default=8)
    ap.add_argument("--max-domains", type=int, default=len(DOMAINS))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--save")
    ap.add_argument("--live", action="store_true",
                    help="heatmap colorata dinamica (verde=ok, giallo=fuori-ordine, rosso=errore)")
    ap.add_argument("--live-json", help="scrive i risultati incrementali (per cella) qui (dashboard)")
    ap.add_argument("--iter", type=int, default=0, help="etichetta iterazione (per la dashboard)")
    args = ap.parse_args()

    import compound_dryrun as CD
    from store_bootstrap import register_builtin_stores
    register_builtin_stores()
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from agent_runtime import _engine_v2_catalog_with_builtins
    cat = _engine_v2_catalog_with_builtins(
        filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER))
    fast, wise = CD.build_calls()

    engine = os.environ.get("METNOS_ENGINE")
    print(f"===== SCALING BENCH  engine={engine}  per_cell={args.per_cell}  runs={args.runs} =====")

    # genera il grid
    cells = []  # (a, d, [queries])
    total_q = 0
    for a in range(2, args.max_actions + 1):
        for d in range(1, min(a, args.max_domains) + 1):
            qs = []
            for k in range(args.per_cell):
                g = gen_query(a, d, k)
                if g:
                    qs.append(g)
            if qs:
                cells.append((a, d, qs))
                total_q += len(qs)
    print(f"grid: {len(cells)} celle (a,d), {total_q} query totali\n")

    dmax = max(d for (_, d, _) in cells)
    results = {(a, d): [None] * len(qs) for (a, d, qs) in cells}
    cell_acc = {}   # (a,d) -> (correct, total)
    rows = []
    height = 0
    done_q = 0
    live = args.live
    if live:
        height = draw(render_heatmap(results, args.max_actions, dmax,
                                     args.per_cell, f"0/{total_q} query"), 0)
    for (a, d, qs) in cells:
        correct = 0
        for k, (query, gold) in enumerate(qs):
            plan_sigs = set()
            last_sc = None
            for _ in range(args.runs):
                fw, intent, actions, pool = CD.plan_only(query, cat, fast, wise)
                steps = CD._steps(fw)
                plan_sigs.add(" ".join(s.get("tool") for s in steps))
                last_sc = score(steps, gold)
            flaky = len(plan_sigs) > 1
            o = outcome(last_sc, flaky)
            ok = (o == "ok")
            correct += int(ok)
            results[(a, d)][k] = o
            done_q += 1
            rows.append({"a": a, "d": d, "q": query[:70], "gold_hard": last_sc["hard_total"],
                          "fwd": last_sc["fwd"], "anyo": last_sc["anyo"],
                          "in_order": last_sc["in_order"], "flaky": flaky, "ok": ok,
                          "n_plan": last_sc["n_plan_exec"],
                          "tools": " ".join(last_sc["tools"])})
            if live and _TTY:
                height = draw(render_heatmap(results, args.max_actions, dmax,
                              args.per_cell, f"{done_q}/{total_q}  a={a} d={d}"), height)
        cell_acc[(a, d)] = (correct, len(qs))
        if args.live_json:
            write_live(args.live_json, engine, args.iter, rows, done_q, total_q)
        if live and _TTY:
            pass  # gia' ridisegnato per-query (redraw in place)
        elif live:
            # non-TTY: snapshot completo per cella (glifi distinti, no colore) →
            # l'ultima stampa nel file e' lo stato corrente (watch via tail/monitor).
            print("\n".join(render_heatmap(results, args.max_actions, dmax,
                  args.per_cell, f"{done_q}/{total_q}  ultima: a={a} d={d}")) + "\n")
        else:
            print(f"  a={a} d={d}: {correct}/{len(qs)} ok  [{' '.join(results[(a,d)])}]")
    if live:
        height = draw(render_heatmap(results, args.max_actions, dmax, args.per_cell,
                                     f"DONE {done_q}/{total_q}"), height)

    # heatmap
    print("\n=== HEATMAP accuracy (righe=azioni, colonne=domini) ===")
    dmax = max(d for (_, d) in cell_acc)
    header = "a\\d " + "".join(f"{d:>6}" for d in range(1, dmax + 1))
    print(header)
    for a in range(2, args.max_actions + 1):
        line = f"{a:>3} "
        for d in range(1, dmax + 1):
            if (a, d) in cell_acc:
                c, t = cell_acc[(a, d)]
                line += f"{int(100*c/t):>5}%"
            else:
                line += "     ·"
        print(line)

    tot_ok = sum(c for c, _ in cell_acc.values())
    tot = sum(t for _, t in cell_acc.values())
    print(f"\nTOTALE: {tot_ok}/{tot} = {100*tot_ok/tot:.1f}%")

    if args.save:
        Path(args.save).write_text(json.dumps(
            {"engine": engine, "total": [tot_ok, tot],
             "cells": {f"{a},{d}": cell_acc[(a, d)] for (a, d) in cell_acc},
             "rows": rows}, ensure_ascii=False, indent=1))
        print(f"salvato: {args.save}")


if __name__ == "__main__":
    main()
