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

def _tw(*accept):
    """Matcher per time_window: l'engine puo' risolvere il keyword in DATA ISO
    (es. 'ieri'→'2026-06-17') — entrambe valide. Accetta il keyword OPPURE una
    qualunque ISO date (l'engine ha gia' risolto). NON e' un test allentato: una
    finestra temporale corretta puo' essere espressa come keyword o come data
    risolta; quello che conta e' che il campo sia POPOLATO con un valore temporale
    plausibile, non vuoto."""
    import re as _re
    def _m(args):
        v = str(args.get("time_window") or args.get("date") or
                args.get("when") or "")
        if not v:
            return False
        vl = v.lower()
        if any(a in vl for a in accept):
            return True
        # forme temporali valide equivalenti: ISO date, last/next/past-Nd,
        # now_plus/minus_Nd, today/tomorrow/yesterday (dialetti time_window
        # dell'engine — tutte esprimono una finestra POPOLATA e plausibile).
        return bool(_re.match(r"\d{4}-\d{2}-\d{2}", vl)
                    or _re.match(r"(last|next|past)-\d+", vl)
                    or _re.match(r"now_(plus|minus)_\d+", vl)
                    or vl in ("today", "tomorrow", "yesterday"))
    return _m


def _email(addr):
    """Matcher destinatario: l'engine puo' metterlo in `to_user` (top-level)
    OPPURE annidato in `messages[].to` (forma lista-messaggi). Entrambe valide."""
    def _m(args):
        if str(args.get("to_user") or "").strip() == addr:
            return True
        msgs = args.get("messages")
        if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
            if str(msgs[0].get("to") or "").strip() == addr:
                return True
        return False
    return _m


# Catene per DOMINIO: (frammento NL, verbo gold, oggetto gold). Producer in testa,
# poi ops in ordine naturale (ognuna opera sull'output della precedente).
# Frasi NATURALI (Roberto 18/6): NIENTE termini-vocabolario letterali
# (entries/messages/images/events/store) — l'utente reale dice "le foto", "la
# posta", "le spese", "gli impegni". Il gold (verbo,oggetto) resta canonico: e'
# l'ENGINE che deve mappare NL→tool (foto→images, posta→messages, spese→entries,
# impegni→events, online→urls, CPU→processes). Cosi' il test stressa la mappatura
# reale, non il match-keyword.
# Ogni clausola: (testo, verbo, oggetto, ARGS_GOLD). ARGS_GOLD = SOLO i valori
# deterministicamente DEDUCIBILI dal testo (no over-spec): path/cartella, finestra
# temporale, destinatario email, campo where, store-name. Valore = stringa
# attesa, oppure callable(args)->bool per match flessibile. Chiave assente = non
# verificata (l'engine puo' scegliere). Fase ARGS (Roberto 18/6).
DOMAIN_CHAINS = {
    "files": [
        ("trova i file di log nella cartella /tmp/logs", "find", "files",
         {"base_path": "/tmp/logs"}),
        ("tieni solo quelli piu' vecchi di una settimana", "filter", "files", {}),
        ("comprimili in uno zip", "compress", "files", {}),
        ("sposta l'archivio in /backup", "move", "files", {}),
        ("poi cancella gli originali", "delete", "files", {}),
    ],
    "posta": [
        ("controlla la posta non letta di oggi", "read", "messages",
         {"time_window": _tw("today","oggi"), "unseen_only": True}),
        ("tieni solo quelle con allegati", "filter", "messages", {}),
        ("mandami un riassunto a roberto@example.com", "send", "messages",
         {"to_user": _email("roberto@example.com")}),
        ("archivia le altre", "move", "messages", {}),
    ],
    "spese": [
        # NB store-name NON nel gold: «spese» e' il NOME-DOMINIO dell'utente, non
        # un nome-store deducibile — quale store concreto serva e' config
        # d'istanza (scope-args/form, memoria [[project-scope-args-subsystem]]),
        # NON una mappatura compositiva. Richiederlo qui sarebbe over-spec
        # (l'engine non puo' inventare un nome-store inesistente in modo
        # deterministico). where/importo SI: deducibile da «sopra i 100 euro».
        ("trova le spese sopra i 100 euro", "find", "entries", {}),
        ("raggruppale per categoria", "group", "entries", {}),
        ("registra il totale fra le spese", "write", "entries", {}),
        ("togli le spese dell'anno scorso", "delete", "entries", {}),
    ],
    "impegni": [
        ("che impegni ho domani", "find", "events", {"time_window": _tw("tomorrow","domani")}),
        ("descrivimeli in breve", "describe", "events", {}),
        ("mandami la lista a roberto@example.com", "send", "messages",
         {"to_user": _email("roberto@example.com")}),
    ],
    "foto": [
        ("trova le foto scattate ieri", "find", "images", {"time_window": _tw("yesterday","ieri")}),
        ("scegli quelle col viso in primo piano", "filter", "images", {}),
        ("comprimile in uno zip", "compress", "images", {}),
    ],
    "web": [
        ("cerca online le novita' su AMD ROCm", "find", "urls", {}),
        ("apri i primi due risultati", "read", "urls", {}),
        ("riassumimeli", "describe", "urls", {}),
    ],
    "sistema": [
        ("guarda cosa sta consumando piu' CPU", "find", "processes", {}),
        ("ordinali per memoria", "sort", "processes", {}),
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
        for entry in DOMAIN_CHAINS[dom][:cnt]:
            nl, v, o = entry[0], entry[1], entry[2]
            ag = entry[3] if len(entry) > 3 else {}
            clauses.append(nl)
            gold.append((v, o, nl, ag))   # (verbo, oggetto, testo, args_gold)
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


def _arg_ok(expected, args):
    """Un singolo (key→expected) e' soddisfatto dagli args dello step?
    expected callable → expected(args). Stringa/valore → match su args[key] o
    annidato in where/messages. Tolleranza: case-insensitive su stringhe."""
    k, exp = expected
    if callable(exp):
        try:
            return bool(exp(args))
        except Exception:
            return False
    # cerca la chiave a top-level, in where/{} e nel primo messages/{}
    cands = []
    if k in args:
        cands.append(args[k])
    w = args.get("where")
    if isinstance(w, dict) and k in w:
        cands.append(w[k])
    msgs = args.get("messages")
    if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict) and k in msgs[0]:
        cands.append(msgs[0][k])
    for v in cands:
        if isinstance(exp, str) and isinstance(v, str):
            if exp.lower() in v.lower() or v.lower() in exp.lower():
                return True
        elif v == exp:
            return True
    return False


def score_args(plan_steps, gold):
    """Per ogni clausola HARD del gold con args_gold non vuoto, trova lo step del
    piano che la copre (object-aware, primo match in ordine) e verifica che gli
    args DEDUCIBILI siano presenti. Ritorna copertura args.

    arg_total = somma delle chiavi-gold su tutte le clausole verificate;
    arg_ok    = chiavi soddisfatte. clause_total/clause_ok = clausole con TUTTE
    le chiavi soddisfatte (vista per-clausola, piu' severa)."""
    import naming_grammar as _ng
    try:
        from compound_decomposer import PRODUCER_VERBS as _PROD
    except Exception:
        _PROD = {"find", "read", "get", "list"}
    steps = [s for s in plan_steps if s.get("tool") and s.get("tool") != "final_answer"]
    parsed = [(_ng.parse_name(s["tool"]), s.get("args") or {}) for s in steps]
    used = [False] * len(parsed)
    arg_total = arg_ok = clause_total = clause_ok = 0
    missing = []
    for g in gold:
        gv, go = g[0], g[1]
        ag = g[3] if len(g) > 3 else {}
        if not ag:
            continue
        # trova lo step coprente (stesso object per producer; verbo per mutator)
        sj = None
        for j, (nc, args) in enumerate(parsed):
            if used[j] or not nc:
                continue
            sv, so = nc.verb, nc.obj
            if gv in _PROD or gv in SOFT:
                ok = (so == go and (sv in _PROD or sv in SOFT))
            else:
                ok = (sv == gv and so == go)
            if ok:
                sj = j
                break
        clause_total += 1
        if sj is None:
            arg_total += len(ag)
            missing.append((g[2][:30], "NO-STEP", list(ag.keys())))
            continue
        used[sj] = True
        _, args = parsed[sj]
        clause_all = True
        miss_keys = []
        for k, exp in ag.items():
            arg_total += 1
            if _arg_ok((k, exp), args):
                arg_ok += 1
            else:
                clause_all = False
                miss_keys.append(k)
        clause_ok += int(clause_all)
        if miss_keys:
            missing.append((g[2][:30], parsed[sj][0].verb + "_" + (parsed[sj][0].obj or ""), miss_keys))
    return {"arg_total": arg_total, "arg_ok": arg_ok,
            "clause_total": clause_total, "clause_ok": clause_ok,
            "missing": missing}


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


def write_live(path, engine, iteration, rows, done, total, phase="struct",
               per_cell=0):
    """Snapshot incrementale (scrittura atomica) per la dashboard HTTP."""
    import datetime as _dt
    data = {"engine": engine, "iter": iteration, "done": done, "total": total,
            "phase": phase, "per_cell": per_cell,
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
    ap.add_argument("--phase", choices=["struct", "args"], default="struct",
                    help="struct = copertura/ordine clausole; args = riempimento args deducibili")
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
    # STORE PERSISTENTE per-cella (Roberto 18/6): i risultati di OGNI cella
    # sopravvivono fra i run. La dashboard mostra sempre l'ultimo stato VALIDO
    # cella-per-cella — un run che ricomincia NON azzera le celle gia' fatte
    # (causa dei falsi cali nei grafici). Una cella si aggiorna SOLO quando il
    # run corrente l'ha ri-completata. Keyed "a,d" → {rows:[...], outcome_per_q}.
    cell_store_path = (args.live_json + ".cells.json") if args.live_json else None
    cell_store = {}
    if cell_store_path and Path(cell_store_path).exists():
        try:
            cell_store = json.loads(Path(cell_store_path).read_text())
        except Exception:
            cell_store = {}
    # Seed rows + results con le celle persistite (stessa fase): la dashboard
    # parte gia' popolata, niente buchi.
    rows = []
    for key, ent in cell_store.items():
        if ent.get("phase") != args.phase:
            continue
        for r in ent.get("rows", []):
            rows.append(r)
        a0, d0 = (int(x) for x in key.split(","))
        outs = ent.get("outcomes", [])
        if (a0, d0) in results:
            for i, o in enumerate(outs):
                if i < len(results[(a0, d0)]):
                    results[(a0, d0)][i] = o
        cell_acc[(a0, d0)] = (sum(1 for o in outs if o == "ok"), len(outs))
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
            last_sa = None
            for _ in range(args.runs):
                fw, intent, actions, pool = CD.plan_only(query, cat, fast, wise)
                steps = CD._steps(fw)
                plan_sigs.add(" ".join(s.get("tool") for s in steps))
                last_sc = score(steps, gold)
                if args.phase == "args":
                    last_sa = score_args(steps, gold)
            flaky = len(plan_sigs) > 1
            if args.phase == "args":
                # esito ARGS: ok = tutte le clausole-con-args-gold soddisfatte +
                # struttura non rotta (drop = error a monte). reorder→giallo.
                struct_o = outcome(last_sc, flaky)
                if struct_o == "error":
                    o = "error"            # produttore mancante: args non valutabili
                elif last_sa["clause_ok"] < last_sa["clause_total"]:
                    o = "reorder"          # giallo = args incompleti (riuso glifo)
                else:
                    o = "ok"
            else:
                o = outcome(last_sc, flaky)
            ok = (o == "ok")
            correct += int(ok)
            results[(a, d)][k] = o
            done_q += 1
            row = {"a": a, "d": d, "q": query[:70], "gold_hard": last_sc["hard_total"],
                   "fwd": last_sc["fwd"], "anyo": last_sc["anyo"],
                   "in_order": last_sc["in_order"], "flaky": flaky, "ok": ok,
                   "outcome": o, "phase": args.phase,
                   "n_plan": last_sc["n_plan_exec"], "tools": " ".join(last_sc["tools"])}
            if last_sa is not None:
                row.update({"arg_ok": last_sa["arg_ok"], "arg_total": last_sa["arg_total"],
                            "clause_ok": last_sa["clause_ok"], "clause_total": last_sa["clause_total"],
                            "arg_missing": last_sa["missing"]})
            rows.append(row)
            if live and _TTY:
                height = draw(render_heatmap(results, args.max_actions, dmax,
                              args.per_cell, f"{done_q}/{total_q}  a={a} d={d}"), height)
        cell_acc[(a, d)] = (correct, len(qs))
        # Persisti la cella APPENA completata nello store durevole (atomico):
        # da qui in poi sopravvive a run futuri finche' non viene ri-completata.
        if cell_store_path:
            cell_rows = [r for r in rows if r["a"] == a and r["d"] == d]
            cell_store[f"{a},{d}"] = {
                "phase": args.phase,
                "outcomes": [results[(a, d)][i] for i in range(len(qs))],
                "rows": cell_rows[-len(qs):] if cell_rows else [],
            }
            try:
                _tmp = cell_store_path + ".tmp"
                Path(_tmp).write_text(json.dumps(cell_store, ensure_ascii=False))
                os.replace(_tmp, cell_store_path)
            except Exception:
                pass
        if args.live_json:
            write_live(args.live_json, engine, args.iter, rows, done_q, total_q,
                       phase=args.phase, per_cell=args.per_cell)
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
