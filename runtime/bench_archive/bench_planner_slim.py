#!/usr/bin/env python3
"""bench_planner_slim — bench corpus pre/post slim (#H0a, 19/5/2026 v2).

Misura impatto delle 3 ottimizzazioni del giant prompt PLANNER (il modello locale
think=True):
  (b) tool schema slim al rendering — `tool_schema_slim.py`
  (c) section pruning per OBJECTS core — `prompt_loader.compose(sections=())`
  (d) fast-path get_location (out of scope qui: tagliato prima del PLANNER)

Quattro configurazioni × N query × N run:
  FULL+ALL    baseline pre-slim                (schema full + sections=None)
  SLIM+ALL    solo (b)                         (schema slim + sections=None)
  FULL+SMART  solo (c)                         (schema full + sections core/targeted)
  SLIM+SMART  full slim (entrambe)             (schema slim + sections core/targeted)

Phase A (default): solo misura DETERMINISTICA — char/tok del prompt+tools.
Phase B (--live): chiamata REALE al PLANNER LLM, misura latenza/accuracy.

Uso:
    python3 runtime/bench_planner_slim.py            # phase A only
    python3 runtime/bench_planner_slim.py --live     # phase A + B
    python3 runtime/bench_planner_slim.py --live --runs 2

Output: stdout summary + JSONL su ~/.local/share/metnos/bench_planner_slim_<ts>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

_RUNTIME = os.environ.get("METNOS_RUNTIME") or str(Path(__file__).resolve().parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

# Forziamo SLIM OFF di default per render: il bench applica slim
# *direttamente* sui dict tool (non via env), in modo che le due variant
# siano comparabili nella stessa sessione python.
os.environ.setdefault("METNOS_TOOL_SCHEMA_FULL", "1")

from loader import load_catalog, filter_for_visibility, VISIBILITY_COMPOSER
import prefilter
import prompt_loader
from vocab import (
    sections_for_object,
    object_is_core_only,
    render_actions_inline as _vocab_actions,
    render_objects_inline as _vocab_objects,
    render_qualifiers_inline as _vocab_qualifiers,
)
from tool_schema_slim import slim_description, slim_args_schema
from agent_runtime import (
    planner_facing_schema,
    _render_project_paths_block,
    _render_users_known_block,
    _render_now_vars,
    _render_telos_block,
)


# --- Corpus IT (mix core + domain) --------------------------------------

@dataclass
class CorpusEntry:
    q: str
    expected: str
    verb: str
    object: str
    is_core: bool


# Tutti `expected` esistono come manifest in `executors/`.
# Esteso 19/5/2026 da 14q a 25q per #H0c.2 bench ampio PLANNER split forced-on.
BENCH_CORPUS: list[CorpusEntry] = [
    # Core (sections=() = core-only) — 7
    CorpusEntry("trova i file .py più grandi nella home", "find_files", "find", "files", True),
    CorpusEntry("elenca le directory in /opt/metnos", "find_dirs", "find", "dirs", True),
    CorpusEntry("quante righe di codice ci sono in /opt/metnos/runtime", "compute_files_loc", "compute", "files", True),
    CorpusEntry("che pacchetti debian relativi a postgres sono installati", "find_packages", "find", "packages", True),
    CorpusEntry("media e mediana dei numeri 12 18 25 30 7 14", "describe_numbers", "describe", "numbers", True),
    CorpusEntry("filtra le righe con ERROR nel file /tmp/log.txt", "filter_texts_lines", "filter", "texts", True),
    CorpusEntry("che città è alle coordinate 41.9 12.5", "find_places", "find", "places", True),
    # Domain (sections targeted) — 7
    CorpusEntry("leggi le ultime 5 mail", "read_messages", "read", "messages", False),
    CorpusEntry("manda una mail a mario@example.com con oggetto Test", "send_messages", "send", "messages", False),
    CorpusEntry("che eventi ho domani in calendario", "read_events", "read", "events", False),
    CorpusEntry("crea un evento martedì alle 15 dentista", "create_events", "create", "events", False),
    CorpusEntry("cerca le ultime notizie su elezioni 2026", "find_urls", "find", "urls", False),
    CorpusEntry("trova foto del cane al mare nel 2024", "find_images_indices", "find", "images", False),
    CorpusEntry("che processi consumano più CPU", "get_processes", "get", "processes", False),
    # Extension 19/5/2026 (+11) — mix mutating + producer + multi-step
    CorpusEntry("sposta i file .pdf vecchi più di 6 mesi in /tmp/old", "move_files", "move", "files", True),
    CorpusEntry("cancella i file .tmp in /tmp", "delete_files", "delete", "files", True),
    CorpusEntry("comprimi la cartella /tmp/log in zip", "compress_dirs_zip", "compress", "dirs", True),
    CorpusEntry("che ora è a Tokyo", "get_now", "get", "numbers", True),
    CorpusEntry("dimmi la lista delle persone conosciute", "list_persons", "list", "persons", False),
    CorpusEntry("aggiungi promemoria fra 2 ore: chiamare dentista", "create_tasks", "create", "tasks", False),
    CorpusEntry("cosa ho fatto ieri", "read_tasks_history", "read", "tasks", False),
    CorpusEntry("riassumi la pagina https://it.wikipedia.org/wiki/Roma", "read_urls_html", "read", "urls", False),
    CorpusEntry("trova mail di posta indesiderata da ultimo mese", "find_messages", "find", "messages", False),
    CorpusEntry("rinomina file_old.txt in file_new.txt nella home", "move_files", "move", "files", True),
    CorpusEntry("salva nota: spesa supermercato 35€", "write_files_text", "write", "texts", False),
]


CONFIGS = [
    ("FULL+ALL",   False, "all"),
    ("SLIM+ALL",   True,  "all"),
    ("FULL+SMART", False, "smart"),
    ("SLIM+SMART", True,  "smart"),
]


# --- Rendering helpers ---------------------------------------------------

def render_tools(executors, *, slim: bool) -> list[dict]:
    """Replica di `agent_runtime.render_tools_for_provider` con slim
    controllato per parametro invece che via env (cosi' bench non sporca
    env globale e puo' alternare run-by-run)."""
    tools = []
    for ex in executors:
        desc = ex.description
        params = planner_facing_schema(ex.args_schema) or {"type": "object"}
        if slim:
            desc = slim_description(desc)
            params = slim_args_schema(params)
        tools.append({
            "type": "function",
            "function": {
                "name": ex.name,
                "description": desc,
                "parameters": params,
            },
        })
    return tools


def render_system(*, entry: CorpusEntry, kind: str) -> str:
    """Render del prompt PLANNER per la configurazione 'all' o 'smart'."""
    if kind == "all":
        sections = None
    else:  # smart
        targeted = sections_for_object(entry.object)
        if targeted:
            sections = list(targeted)
        elif object_is_core_only(entry.object):
            sections = []  # core-only (sections=())
        else:
            sections = None  # unknown → all (degrade)
    now_vars = _render_now_vars()
    return prompt_loader.compose(
        "planner", "it",
        sections=sections,
        vocab_actions=_vocab_actions(),
        vocab_objects=_vocab_objects(),
        vocab_qualifiers=_vocab_qualifiers(),
        project_paths=_render_project_paths_block(),
        users_known=_render_users_known_block(),
        telos_block=_render_telos_block("it"),
        **now_vars,
    )


def get_top_k(catalog, entry: CorpusEntry, k: int = 7):
    """Pool deterministico via prefilter.rank_with_intent (stesso path runtime)."""
    intent = {"verb": entry.verb, "object": entry.object}
    pool = prefilter.rank_with_intent(entry.q, catalog, intent, k=k)
    if not pool:
        # Fallback bag-of-words se rank_with_intent torna None
        pool = prefilter.rank(entry.q, catalog, k=k, min_score=0)
    # Garantisci che `expected` sia nel pool: se prefilter non l'ha pickato,
    # iniettalo (vogliamo misurare la decisione del LLM con pool realistico
    # ma equo). Nota: se manca completamente, accuracy sara' 0 by-design.
    names = {e.name for e in pool}
    if entry.expected not in names:
        ex = catalog.get(entry.expected)
        if ex is not None:
            pool = list(pool) + [ex]
    return list(pool)


# --- Phase A: deterministic size measurement -----------------------------

@dataclass
class SizeRow:
    q: str
    expected: str
    config: str
    pool_k: int
    sys_chars: int
    sys_tok: int
    tools_chars: int
    tools_tok: int
    total_chars: int
    total_tok: int


def measure_sizes(catalog) -> list[SizeRow]:
    rows: list[SizeRow] = []
    for entry in BENCH_CORPUS:
        pool = get_top_k(catalog, entry, k=7)
        for label, slim, kind in CONFIGS:
            sysprompt = render_system(entry=entry, kind=kind)
            tools = render_tools(pool, slim=slim)
            tools_str = json.dumps(tools, ensure_ascii=False)
            sys_chars = len(sysprompt)
            tools_chars = len(tools_str)
            rows.append(SizeRow(
                q=entry.q,
                expected=entry.expected,
                config=label,
                pool_k=len(pool),
                sys_chars=sys_chars,
                sys_tok=sys_chars // 4,
                tools_chars=tools_chars,
                tools_tok=tools_chars // 4,
                total_chars=sys_chars + tools_chars,
                total_tok=(sys_chars + tools_chars) // 4,
            ))
    return rows


def summarize_sizes(rows: list[SizeRow]) -> dict:
    """Aggrega per config: mediana e somma totale."""
    by_config: dict[str, list[SizeRow]] = {}
    for r in rows:
        by_config.setdefault(r.config, []).append(r)
    out = {}
    for cfg, rs in by_config.items():
        out[cfg] = {
            "n": len(rs),
            "sys_tok_median": int(statistics.median([r.sys_tok for r in rs])),
            "tools_tok_median": int(statistics.median([r.tools_tok for r in rs])),
            "total_tok_median": int(statistics.median([r.total_tok for r in rs])),
            "total_tok_sum": sum(r.total_tok for r in rs),
        }
    return out


# --- Phase B: live LLM call (latency + accuracy) -------------------------

@dataclass
class LiveRow:
    q: str
    expected: str
    config: str
    run: int
    in_tok: int
    out_tok: int
    latency_ms: int
    tool_picked: str
    correct: bool


def measure_live(catalog, runs: int) -> list[LiveRow]:
    from llm_provider import LlamaCppProvider
    provider = LlamaCppProvider()
    rows: list[LiveRow] = []
    total = len(BENCH_CORPUS) * len(CONFIGS) * runs
    done = 0
    for entry in BENCH_CORPUS:
        pool = get_top_k(catalog, entry, k=7)
        for label, slim, kind in CONFIGS:
            sysprompt = render_system(entry=entry, kind=kind)
            tools = render_tools(pool, slim=slim)
            for run in range(runs):
                done += 1
                print(f"[live {done:3d}/{total}] {entry.expected:24s} {label:11s} run={run}",
                      end=" ", flush=True)
                try:
                    res = provider.chat_with_tools(
                        sysprompt, entry.q, tools,
                        max_tokens=2048,
                        temperature=0,
                        think=True,
                        reasoning_budget=512,
                    )
                    pick = res.tool_calls[0].name if res.tool_calls else "<no_tool>"
                    correct = (pick == entry.expected)
                    rows.append(LiveRow(
                        q=entry.q, expected=entry.expected, config=label, run=run,
                        in_tok=res.in_tokens, out_tok=res.out_tokens,
                        latency_ms=res.latency_ms, tool_picked=pick,
                        correct=correct,
                    ))
                    print(f"in={res.in_tokens:5d} out={res.out_tokens:4d} "
                          f"lat={res.latency_ms:5d}ms picked={pick} "
                          f"{'✓' if correct else '✗'}")
                except Exception as e:
                    print(f"FAIL: {e}")
                    rows.append(LiveRow(
                        q=entry.q, expected=entry.expected, config=label, run=run,
                        in_tok=0, out_tok=0, latency_ms=0,
                        tool_picked="<error>", correct=False,
                    ))
    return rows


def summarize_live(rows: list[LiveRow]) -> dict:
    by_config: dict[str, list[LiveRow]] = {}
    for r in rows:
        by_config.setdefault(r.config, []).append(r)
    out = {}
    for cfg, rs in by_config.items():
        ok = [r for r in rs if r.tool_picked != "<error>"]
        if not ok:
            out[cfg] = {"n": 0, "accuracy": 0.0, "latency_ms_median": 0}
            continue
        out[cfg] = {
            "n": len(rs),
            "accuracy": sum(1 for r in rs if r.correct) / len(rs),
            "in_tok_median": int(statistics.median([r.in_tok for r in ok])),
            "out_tok_median": int(statistics.median([r.out_tok for r in ok])),
            "latency_ms_median": int(statistics.median([r.latency_ms for r in ok])),
            "latency_ms_p90": int(sorted([r.latency_ms for r in ok])[int(0.9 * (len(ok) - 1))]),
        }
    return out


# --- Main ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Bench planner slim (#H0a)")
    ap.add_argument("--live", action="store_true", help="Phase B: chiama LLM reale")
    ap.add_argument("--runs", type=int, default=1, help="Run per (q,config) in live")
    ap.add_argument("--k", type=int, default=7, help="Pool size top-K")
    ap.add_argument("--output-dir", default=str(Path.home() / ".local" / "share" / "metnos"))
    args = ap.parse_args()

    print(f"[bench_planner_slim] corpus={len(BENCH_CORPUS)} configs={len(CONFIGS)} k={args.k}")
    t0 = time.time()
    catalog = load_catalog()
    catalog = filter_for_visibility(catalog, VISIBILITY_COMPOSER)
    print(f"  catalog: {len(catalog)} executors  ({int((time.time()-t0)*1000)}ms)")

    # Phase A
    print("\n=== Phase A: deterministic sizes ===")
    size_rows = measure_sizes(catalog)
    size_summary = summarize_sizes(size_rows)
    print(f"{'config':12s} {'n':>3s} {'sys_med':>8s} {'tools_med':>10s} {'total_med':>10s} {'total_sum':>10s}")
    for cfg, s in size_summary.items():
        print(f"{cfg:12s} {s['n']:>3d} {s['sys_tok_median']:>8d} "
              f"{s['tools_tok_median']:>10d} {s['total_tok_median']:>10d} "
              f"{s['total_tok_sum']:>10d}")

    # Delta tabella
    base = size_summary["FULL+ALL"]
    print("\n--- delta vs FULL+ALL ---")
    for cfg, s in size_summary.items():
        if cfg == "FULL+ALL":
            continue
        d_total = s["total_tok_median"] - base["total_tok_median"]
        d_pct = 100.0 * d_total / max(1, base["total_tok_median"])
        print(f"  {cfg:12s}  total_tok Δ={d_total:+6d}  ({d_pct:+6.1f}%)")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    sizes_path = out_dir / f"bench_planner_slim_sizes_{ts}.jsonl"
    with open(sizes_path, "w") as f:
        for r in size_rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\nwrote {sizes_path}")

    # Phase B
    if args.live:
        print(f"\n=== Phase B: live LLM ({len(BENCH_CORPUS) * len(CONFIGS) * args.runs} call) ===")
        live_rows = measure_live(catalog, runs=args.runs)
        live_summary = summarize_live(live_rows)
        print()
        print(f"{'config':12s} {'n':>3s} {'acc':>6s} {'in_med':>8s} {'out_med':>8s} "
              f"{'lat_med':>9s} {'lat_p90':>9s}")
        for cfg, s in live_summary.items():
            if s["n"] == 0:
                print(f"{cfg:12s} {s['n']:>3d}   FAIL")
                continue
            print(f"{cfg:12s} {s['n']:>3d} {s['accuracy']:>6.2f} "
                  f"{s.get('in_tok_median',0):>8d} {s.get('out_tok_median',0):>8d} "
                  f"{s['latency_ms_median']:>8d}ms {s.get('latency_ms_p90',0):>8d}ms")
        live_path = out_dir / f"bench_planner_slim_live_{ts}.jsonl"
        with open(live_path, "w") as f:
            for r in live_rows:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        print(f"\nwrote {live_path}")


if __name__ == "__main__":
    main()
