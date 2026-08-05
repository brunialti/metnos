#!/usr/bin/env python3
"""routing_scale_bench.py — test su SCALA GRANDE (corpus reale) dell'hybrid intent.

Oracolo: il POOL di routing (build_routing_pool, deterministico ~0.5s — NON il
proposer wise, troppo lento su migliaia di query). Per ogni query reale, misura
hybrid OFF (v4) vs ON (scaffold):
  - intent (verb/object + n. clausole)
  - pool: dimensione, e se il pool CAMBIA (membership) fra OFF e ON.
Un cambio di pool è un DELTA da rivedere (fix o regressione); l'identità è
stabilità. Niente gold (1333 query non hanno label): l'oracolo è il DIFF OFF↔ON
+ il replay self-consistency (K run → flakiness).

Campione: stride deterministico sul corpus (no random §workflow). Default 400.

Uso: METNOS_ENGINE=metis python3 tests/benchmarks/routing_scale_bench.py [N] [--compound-only]
"""
from __future__ import annotations
import glob, importlib.util, json, os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))
os.environ.setdefault("METNOS_PROPOSER_GRAMMAR", "1")
os.environ.setdefault("METNOS_PROPOSER_VERB_FILTER", "1")
os.environ.setdefault("METNOS_ENGINE_POOL_SIZE", "12")
TURNS = Path.home() / ".local/share/metnos/turns"

# Scenari REALISTICI multi-azione (Roberto 24/6: «query composte e con molte
# azioni che abbiano un senso»). Flussi plausibili di un assistente personale,
# 3-6 azioni intrecciate. Tool-gold = REALI on-disk (alternative ammesse);
# builtin in-process (describe/classify/filter/extract/compute) = {"v": verbo}.
SCENARIOS = [
    ("controlla le mail di oggi, scarta quelle di spam, riassumi le importanti e archivia il resto",
     ["read_messages", {"v": "filter"}, {"v": "describe"}, "move_messages"]),
    ("trova le fatture nelle mail dell'ultimo mese, estrai importo e data, mettile in un foglio e mandamelo",
     ["read_messages", {"v": "extract"}, "create_files_spreadsheet", "send_messages"]),
    ("cerca le foto delle vacanze 2024, raggruppale per luogo, comprimi ogni gruppo e caricali sul drive",
     ["find_images_indices", {"v": "group"}, "compress_files", ["share_files", "write_files"]]),
    ("guarda quali processi consumano più memoria, scrivi un report, salvalo e fissami un promemoria per controllarli domani",
     ["get_processes", "write_files", "create_events"]),
    ("leggi le issue aperte su github, classificale per priorità e per ognuna scrivi una bozza di risposta nel db",
     [["find_issues_github", "read_issues_github"], {"v": "classify"}, ["write_entries", "create_files"]]),
    ("trova i file di log più grandi di 100MB in /var/log, comprimili, e cancella gli originali",
     ["find_files", "compress_files", "delete_files"]),
    ("cerca online le ultime novità su AMD ROCm, estrai i punti chiave, salvali in una nota e avvisami",
     ["find_urls", {"v": "extract"}, "write_files", "send_messages"]),
    ("prendi gli appuntamenti di domani, controlla quali si sovrappongono e mandami il riepilogo via mail",
     ["read_events", {"v": "filter"}, "send_messages"]),
    ("scarica il pdf da https://site.org/doc.pdf, estrai la tabella, salvala come csv e mandamela",
     [["read_urls_pdf", "read_urls"], {"v": "extract"}, ["create_files_spreadsheet", "write_files"], "send_messages"]),
    # "senza email"=criterio di find (non filter separato); "elenca i nomi"=list.
    ("trova i contatti senza email, elenca i nomi e crea un task per completarli la settimana prossima",
     ["find_contacts", {"v": ("filter", "list", "find")}, "create_tasks"]),
    # "se ci sono mail da X leggile"=read condizionale (filter implicito o find).
    ("controlla la posta, se ci sono mail da knowcastle leggile, estrai gli allegati pdf e salvali in /tmp/docs",
     ["read_messages", {"v": ("filter", "find", "read")}, ["read_files", "extract_entries"], "write_files"]),
    # "dimmi quante"=conteggio: describe (aggregato) o get (snapshot) entrambi validi.
    ("cerca le foto sfocate nell'album, spostale in una cartella scarti e dimmi quante erano",
     ["find_images_indices", "move_files", {"v": ("describe", "get", "find")}]),
]


def _corpus():
    seen, out = set(), []
    for fn in sorted(glob.glob(str(TURNS / "*.jsonl"))):
        try: fh = open(fn)
        except OSError: continue
        for line in fh:
            try: r = json.loads(line)
            except Exception: continue
            q = (r.get("user_query") or "").strip()
            if q and len(q) >= 4 and q not in seen:
                seen.add(q); out.append(q)
    return out


def _stride(lst, n):
    if n >= len(lst): return lst
    step = len(lst) / n
    return [lst[int(i*step)] for i in range(n)]


def main():
    n = 400
    compound_only = "--compound-only" in sys.argv
    for a in sys.argv[1:]:
        if a.isdigit(): n = int(a)

    rsb_spec = importlib.util.spec_from_file_location("rsb", str(Path(__file__).parent / "routing_subset_bench.py"))
    rsb = importlib.util.module_from_spec(rsb_spec); rsb_spec.loader.exec_module(rsb)
    from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
    from engine.types import Intent
    from engine.routing_pool import build_routing_pool
    from compound_decomposer import split_query_chunks
    cat = filter_for_visibility(load_catalog(verify=True), VISIBILITY_COMPOSER)
    fast, _ = rsb.build_calls()
    from intent_extractor import extract_intent

    # --- SCENARI realistici multi-azione (gold pool-recall, hybrid ON) ---
    os.environ["METNOS_INTENT_BOUNDARIES"] = "1"; os.environ["METNOS_INTENT_SCAFFOLD"] = "1"
    def _clause_ok(g, pool, verbs):
        if isinstance(g, dict):
            gv = g.get("v")
            gvs = gv if isinstance(gv, tuple) else (gv,)
            return any(x in verbs for x in gvs)
        alts = g if isinstance(g, list) else [g]
        return any(a in pool for a in alts)
    print("==== SCENARI multi-azione (pool recall, hybrid ON) ====")
    sc_ok = sc_cl_ok = sc_cl_tot = 0
    for q, gold in SCENARIOS:
        ir = extract_intent(q, fast) or {}
        intent = Intent(verb=(ir.get("verb") or "").lower(), object=(ir.get("object") or "").lower(),
                        keywords=[], confidence=1.0, lang="it", actions=list(ir.get("actions") or []))
        pool = set(build_routing_pool(q, intent, cat))
        verbs = {(a.get("verb") or "").lower() for a in (ir.get("actions") or []) if isinstance(a, dict)}
        verbs.add((ir.get("verb") or "").lower())
        miss = [g for g in gold if not _clause_ok(g, pool, verbs)]
        sc_cl_tot += len(gold); sc_cl_ok += len(gold) - len(miss)
        if not miss: sc_ok += 1
        else:
            print(f"  XX miss {miss}")
            print(f"     nact={len(ir.get('actions') or [])} | {q[:62]}")
    print(f"SCENARI: {sc_ok}/{len(SCENARIOS)} pieni, clausole {sc_cl_ok}/{sc_cl_tot} = {100*sc_cl_ok/sc_cl_tot:.1f}%\n")

    corpus = _corpus()
    if compound_only:
        corpus = [q for q in corpus if len(split_query_chunks(q)) >= 2]
    sample = _stride(corpus, n)
    print(f"corpus {len(corpus)} → campione {len(sample)} (compound_only={compound_only})")

    def _run(q, flag_b, flag_s):
        os.environ["METNOS_INTENT_BOUNDARIES"] = flag_b
        os.environ["METNOS_INTENT_SCAFFOLD"] = flag_s
        ir = extract_intent(q, fast) or {}
        intent = Intent(verb=(ir.get("verb") or "").lower(),
                        object=(ir.get("object") or "").lower(),
                        keywords=[], confidence=1.0, lang="it",
                        actions=list(ir.get("actions") or []))
        pool = build_routing_pool(q, intent, cat)
        nacts = len(ir.get("actions") or []) or (1 if ir.get("verb") else 0)
        return (f"{intent.verb}/{intent.object}", nacts, set(pool))

    pool_same = pool_changed = intent_same = 0
    none_off = none_on = 0
    deltas = []
    for i, q in enumerate(sample):
        off = _run(q, "1", "0")   # v4 (boundaries on, scaffold off)
        on  = _run(q, "1", "1")   # hybrid (scaffold on)
        if off[0] == on[0]:
            intent_same += 1
        # pool: il tool TOP del pool OFF sopravvive nel pool ON? (no recall loss)
        survives = bool(off[2] & on[2])  # intersezione non vuota
        if off[2] == on[2]:
            pool_same += 1
        else:
            pool_changed += 1
            # registra solo se la membership cambia in modo sostanziale
            lost = off[2] - on[2]; gained = on[2] - off[2]
            if lost or gained:
                deltas.append((q, off[0], on[0], off[1], on[1], len(lost), len(gained)))
        if off[2] == set(): none_off += 1
        if on[2] == set(): none_on += 1
        if (i+1) % 25 == 0:
            msg = (f"  ...{i+1}/{len(sample)} | intent_same={intent_same} "
                   f"pool_same={pool_same} changed={pool_changed}")
            print(msg, flush=True)
            # progress su file separato (visibile durante la run)
            try:
                Path(os.getenv("SCALE_PROGRESS",
                    "/tmp/claude-1000/-opt-metnos/aa51dedd-f07f-4c7d-aaee-130822475826/scratchpad/scale_progress.txt")
                    ).write_text(msg + "\n")
            except Exception:
                pass

    os.environ["METNOS_INTENT_BOUNDARIES"] = "0"; os.environ["METNOS_INTENT_SCAFFOLD"] = "0"
    m = len(sample)
    out = Path(os.getenv("SCALE_OUT", "/tmp/claude-1000/-opt-metnos/aa51dedd-f07f-4c7d-aaee-130822475826/scratchpad/scale_report.json"))
    out.write_text(json.dumps({"n": m, "intent_same": intent_same, "pool_same": pool_same,
                               "pool_changed": pool_changed, "deltas": deltas[:80]}, ensure_ascii=False, indent=1))
    print(f"\n==== SCALE {m} query (hybrid OFF v4 vs ON scaffold) ====")
    print(f"intent identico:  {intent_same}/{m} = {100*intent_same/m:.1f}%")
    print(f"pool IDENTICO:    {pool_same}/{m} = {100*pool_same/m:.1f}%")
    print(f"pool cambiato:    {pool_changed}/{m}  (delta da rivedere)")
    print(f"pool vuoto OFF/ON: {none_off}/{none_on}  (vuoto=intent None→fallback BoW)")
    print(f"\n-- primi 30 DELTA pool (q | intent off→on | nact | -lost +gained) --")
    for q, ioff, ion, noff, non_, lost, gained in deltas[:30]:
        tag = "⚠REGR?" if lost > gained else "≈"
        print(f"  {tag} off[{ioff} n{noff}] on[{ion} n{non_}] -{lost}+{gained} | {q[:54]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
