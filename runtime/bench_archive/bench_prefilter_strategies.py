"""Bench comparativo per le strategy del prefilter modulare.

Genera corpus query+ground_truth dai turn log Metnos reali (~4086 turni
ultimi 30 giorni). Per ogni strategy: misura
  - Recall@5: il first_tool effettivo del turno e' nei top-5?
  - Recall@1: e' al primo posto?
  - Latency (mean, p50, p95)
  - Diversita': quanti tool diversi nei top-3 (entropy)

Output: report Markdown comparativo. Determinismo §7.9 puro (zero LLM
nelle strategy bench-mode; intent_extractor disabilitato per bench
deterministico).

Uso:
  python3 -m bench_prefilter_strategies                  # tutte le strategy
  python3 -m bench_prefilter_strategies --strategies a,b # subset
  python3 -m bench_prefilter_strategies --n 500          # cap query
  python3 -m bench_prefilter_strategies --output /tmp/x.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

# Path setup
_RUNTIME = Path(__file__).resolve().parent
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))


import config as _C  # §7.11
TURNS_DIR = _C.PATH_TURNS


def load_corpus(min_query_len: int = 6, max_n: int = 1000) -> list[dict]:
    """Estrae corpus (query, ground_truth_first_tool) dai turn log.

    Filtra:
    - turni con first_tool fra builtin/utili (no `final_answer`, no
      `request_disambiguation_from_user`, no `get_inputs`).
    - query di lunghezza minima (no "ok", "si").
    - dedup per query identica (mantieni prima occorrenza).

    Returns: lista `{query, first_tool, turn_id}`.
    """
    EXCLUDE_TOOLS = {
        "final_answer", "request_disambiguation_from_user",
        "get_inputs", "scratchpad_read", "scratchpad_write",
        "undo_last_turn", "request_new_executor",
    }
    seen_queries: set[str] = set()
    corpus: list[dict] = []
    files = sorted(TURNS_DIR.glob("*.jsonl"), reverse=True)
    for jl in files:
        for ln in jl.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                t = json.loads(ln)
            except json.JSONDecodeError:
                continue
            q = (t.get("user_query") or "").strip()
            if len(q) < min_query_len:
                continue
            q_norm = q.lower()
            if q_norm in seen_queries:
                continue
            steps = t.get("steps") or []
            if not steps:
                continue
            first = (steps[0].get("chosen_tool") or "").strip()
            if not first or first in EXCLUDE_TOOLS:
                continue
            # Skip turni che hanno fallito al primo step
            first_result = steps[0].get("result")
            if isinstance(first_result, dict) and first_result.get("ok") is False:
                continue
            seen_queries.add(q_norm)
            corpus.append({
                "query": q,
                "first_tool": first,
                "turn_id": t.get("turn_id", ""),
            })
            if len(corpus) >= max_n:
                return corpus
    return corpus


def _generate_distractor_tools(template_executors: list, n_distractors: int) -> list:
    """Genera n_distractors tool sintetici con nomi unique + affinity
    plausibili. Servono come 'rumore' nel pool per misurare la robustness
    delle strategy al crescere del catalog (scaling test).

    Approccio: combinazioni nuove di verb canonici §2.2 × oggetti ×
    provider qualifier ipotetici (proton, outlook, slack, ...). Cloning
    args_schema dai template per realismo. Description leggera.
    """
    import random
    random.seed(42)  # deterministico per bench
    if not template_executors:
        return []
    VERBS = ["find", "read", "write", "create", "delete", "send",
             "set", "list", "compute", "compare", "filter", "sort"]
    OBJECTS = ["files", "messages", "events", "contacts", "urls",
               "notes", "tasks", "logs", "records", "channels",
               "boards", "issues"]
    PROVIDERS = ["proton", "outlook", "slack", "notion", "linear",
                 "asana", "trello", "dropbox", "icloud", "todoist",
                 "monday", "airtable", "fastmail", "hey", "matrix"]
    seen_names = {getattr(e, "name", "") for e in template_executors}
    distractors = []
    attempts = 0
    while len(distractors) < n_distractors and attempts < n_distractors * 20:
        attempts += 1
        v = random.choice(VERBS)
        o = random.choice(OBJECTS)
        p = random.choice(PROVIDERS)
        name = f"{v}_{o}_{p}"
        if name in seen_names:
            continue
        seen_names.add(name)
        # Crea MockExecutor con attributi minimi che il prefilter usa.
        # Usiamo SimpleNamespace per evitare dipendenze pesanti.
        from types import SimpleNamespace
        template = template_executors[len(distractors) % len(template_executors)]
        mock = SimpleNamespace(
            name=name,
            description=f"Esecutore sintetico per {v} {o} via {p} (distractor bench).",
            affinity=[v, o, p, f"{v}_{o}", "distractor_synth"],
            args_schema=getattr(template, "args_schema", {"type": "object", "properties": {}}),
            timeout_s=getattr(template, "timeout_s", 30),
            capabilities=getattr(template, "capabilities", []),
            target_kind=getattr(template, "target_kind", ""),
            revertible=getattr(template, "revertible", False),
            critical=getattr(template, "critical", False),
        )
        distractors.append(mock)
    return distractors


def _estimate_tool_tokens(tool) -> int:
    """Approssima i token che un tool occupa nel prompt PLANNER.

    Rule of thumb: 1 token ≈ 4 char (inglese) / 3.5 char (italiano).
    Usiamo 4 per stima conservativa. Sommiamo:
      - name (1 riga)
      - description (description.it se multilang, altrimenti string)
      - args_schema (JSON dump)
    """
    import json as _json
    name = getattr(tool, "name", "") or ""
    desc = getattr(tool, "description", "") or ""
    if isinstance(desc, dict):
        # Multilang manifest
        desc = desc.get("it") or desc.get("en") or next(iter(desc.values()), "")
    args = getattr(tool, "args_schema", {}) or {}
    try:
        args_str = _json.dumps(args, ensure_ascii=False)
    except Exception:
        args_str = str(args)
    total_chars = len(name) + len(str(desc)) + len(args_str)
    return total_chars // 4  # 1 token ≈ 4 char


def run_bench(strategies: list[str], corpus: list[dict],
               catalog_executors: list, k_min: int = 5, k_max: int = 8,
               verbose: bool = False) -> dict:
    """Esegue il bench per ogni strategy. Ritorna metriche aggregate."""
    from prefilter_strategies import select_strategy
    # Pre-calcola token per tool del catalog (una volta sola)
    tool_tokens: dict[str, int] = {}
    for e in catalog_executors:
        n = getattr(e, "name", "")
        if n:
            tool_tokens[n] = _estimate_tool_tokens(e)
    results: dict[str, dict] = {}
    for sname in strategies:
        if verbose:
            print(f"\n=== {sname} ===", flush=True)
        try:
            strategy = select_strategy(sname)
        except Exception as ex:
            results[sname] = {"error": str(ex)}
            continue
        recalls_5: list[int] = []
        recalls_1: list[int] = []
        latencies: list[float] = []
        top3_diversity: Counter = Counter()
        rank_positions: list[int] = []
        n_cands: list[int] = []
        pool_tokens: list[int] = []
        for i, item in enumerate(corpus):
            t0 = time.perf_counter()
            try:
                candidates, info = strategy.rank(
                    item["query"], catalog_executors,
                    k_min=k_min, k_max=k_max,
                    llm_call=None, prefer_intent=False,
                )
            except Exception as ex:
                if verbose:
                    print(f"  [{i}] ERROR: {ex}")
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            names = [getattr(e, "name", "") for e in candidates]
            top3_diversity.update(names[:3])
            gt = item["first_tool"]
            in_top5 = gt in names[:5]
            recalls_5.append(1 if in_top5 else 0)
            recalls_1.append(1 if names[:1] == [gt] else 0)
            try:
                rank_positions.append(names.index(gt) + 1)
            except ValueError:
                pass
            n_cands.append(len(candidates))
            pool_tokens.append(sum(tool_tokens.get(n, 0) for n in names))
            if verbose and i % 50 == 0:
                print(f"  [{i}/{len(corpus)}] r@5={sum(recalls_5)/len(recalls_5):.3f}", flush=True)
        mean_pool_tokens = (
            statistics.mean(pool_tokens) if pool_tokens else 0
        )
        recall_5_val = sum(recalls_5) / len(recalls_5) if recalls_5 else 0
        results[sname] = {
            "n": len(latencies),
            "recall_5": recall_5_val,
            "recall_1": sum(recalls_1) / len(recalls_1) if recalls_1 else 0,
            "mean_ms": statistics.mean(latencies) if latencies else 0,
            "p50_ms": statistics.median(latencies) if latencies else 0,
            "p95_ms": (
                sorted(latencies)[int(len(latencies) * 0.95)]
                if latencies else 0
            ),
            "p99_ms": (
                sorted(latencies)[int(len(latencies) * 0.99)]
                if latencies else 0
            ),
            "mean_rank": (
                statistics.mean(rank_positions) if rank_positions else None
            ),
            "n_distinct_top3": len(top3_diversity),
            "mean_n_candidates": (
                statistics.mean(n_cands) if n_cands else 0
            ),
            "mean_pool_tokens": mean_pool_tokens,
            # Efficiency: recall per 1000 tokens del pool (higher = better)
            "recall_per_ktok": (
                recall_5_val / (mean_pool_tokens / 1000)
                if mean_pool_tokens > 0 else 0
            ),
        }
    return results


def render_report(results: dict, corpus: list[dict], k_max: int) -> str:
    """Render del report comparativo in Markdown."""
    n_queries = len(corpus)
    objects_count = Counter()
    verbs_count = Counter()
    for item in corpus:
        ft = item["first_tool"]
        parts = ft.split("_")
        verbs_count[parts[0]] += 1
        if len(parts) > 1:
            objects_count[parts[1]] += 1
    lines = [
        "# Bench prefilter strategies — confronto comparativo",
        "",
        f"**Generato**: {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Corpus**: {n_queries} query reali da turn log Metnos (deduplicate, filtrate)",
        f"**k_max**: {k_max} (top-K candidati per query)",
        f"**Modalita'**: prefer_intent=False (deterministico, no LLM)",
        "",
        "## Distribuzione corpus",
        "",
        "**Verbi top-10** (frequenza nei first_tool):",
        "",
    ]
    for v, c in verbs_count.most_common(10):
        lines.append(f"- `{v}`: {c} ({100*c/n_queries:.1f}%)")
    lines.extend([
        "",
        "**Oggetti top-10**:",
        "",
    ])
    for o, c in objects_count.most_common(10):
        lines.append(f"- `{o}`: {c} ({100*c/n_queries:.1f}%)")
    lines.extend([
        "",
        "## Risultati comparativi (ordinati per Recall@5)",
        "",
        "| Strategy | Recall@1 | Recall@5 | mean ms | p95 ms | n_cand | pool tok | r5/ktok |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    valid_results = {k: v for k, v in results.items() if "error" not in v}
    sorted_strategies = sorted(
        valid_results.keys(),
        key=lambda k: valid_results[k]["recall_5"],
        reverse=True,
    )
    for sname in sorted_strategies:
        r = valid_results[sname]
        lines.append(
            f"| `{sname}` | {r['recall_1']:.3f} | "
            f"**{r['recall_5']:.3f}** | {r['mean_ms']:.1f} | "
            f"{r['p95_ms']:.1f} | {r['mean_n_candidates']:.1f} | "
            f"{r['mean_pool_tokens']:.0f} | {r['recall_per_ktok']:.3f} |"
        )
    # Tabella alternativa: ordinata per efficienza (recall per ktok)
    lines.extend([
        "",
        "## Ranking per efficienza (Recall@5 per 1000 tokens del pool LLM)",
        "",
        "Metrica chiave per minimizzare il contesto LLM downstream (PLANNER).",
        "Un valore alto = piu' recall con meno tokens nel prompt.",
        "",
        "| Strategy | r5/ktok | Recall@5 | pool tok | risparmio vs token_flat |",
        "|---|---:|---:|---:|---:|",
    ])
    sorted_eff = sorted(
        valid_results.keys(),
        key=lambda k: valid_results[k]["recall_per_ktok"],
        reverse=True,
    )
    baseline = valid_results.get("token_flat", {})
    baseline_tokens = baseline.get("mean_pool_tokens", 1) or 1
    for sname in sorted_eff:
        r = valid_results[sname]
        savings = (
            100 * (baseline_tokens - r["mean_pool_tokens"]) / baseline_tokens
            if baseline_tokens > 0 else 0
        )
        savings_str = f"{savings:+.1f}%"
        lines.append(
            f"| `{sname}` | **{r['recall_per_ktok']:.3f}** | "
            f"{r['recall_5']:.3f} | {r['mean_pool_tokens']:.0f} | {savings_str} |"
        )
    # Errori
    error_strategies = {k: v for k, v in results.items() if "error" in v}
    if error_strategies:
        lines.extend(["", "## Strategy con errore", ""])
        for sname, err in error_strategies.items():
            lines.append(f"- `{sname}`: {err['error']}")
    lines.extend([
        "",
        "## Analisi",
        "",
        "**Metriche**:",
        "- **Recall@1**: il tool del ground truth e' al primo posto.",
        "- **Recall@5**: il tool del ground truth e' nei primi 5 candidati (target del prefilter).",
        "- **mean rank**: posizione media del ground truth nei candidati (lower = better, idealmente vicino a 1).",
        "- **distinct top3**: numero di tool diversi che compaiono fra i top-3 di tutte le query (diversita' del pool restituito).",
        "",
        "**Note**:",
        "- Ground truth = first_tool effettivamente chiamato dal PLANNER nei turn log. Approssimazione: non garantisce che sia il tool ottimale, ma misura coerenza con il comportamento attuale di produzione.",
        "- `prefer_intent=False` disattiva l'intent_extractor LLM: tutte le strategy lavorano deterministicamente. Differenze fra strategy sono dovute al solo algoritmo di matching/ranking.",
        "- Strategy che includono fallback al `legacy` mascherano debolezze proprie (recall pari al legacy quando fallback scatta).",
        "",
        "**Interpretazione raccomandata**:",
        "1. Se due strategy hanno Recall@5 entro 1%, prediligi quella con latenza minore.",
        "2. Una strategy puo' avere Recall@5 alto ma Recall@1 basso: utile come reducer di pool, da combinare con re-rank fine.",
        "3. Differenze di latenza sotto 5 ms sono trascurabili.",
        "4. `distinct top3` basso = pool meno diversificato (potenziale rischio di rigidita').",
        "",
    ])
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Bench prefilter strategies")
    ap.add_argument("--strategies", help="Lista CSV (default: tutte registrate)")
    ap.add_argument("--n", type=int, default=500, help="Max query corpus")
    ap.add_argument("--k-max", type=int, default=8)
    ap.add_argument("--output", default="/tmp/bench_prefilter_report.md")
    ap.add_argument("--pool-size", type=int, default=0,
                    help="Pad catalog con distractor sintetici fino a N tool "
                         "(scaling test). 0 = catalog reale.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("Caricamento catalog...", flush=True)
    from loader import load_catalog
    cat = load_catalog(verify=True, include_synth=True)
    executors = list(cat.executors.values())
    print(f"  {len(executors)} executor reali caricati", flush=True)
    if args.pool_size > len(executors):
        n_distractors = args.pool_size - len(executors)
        print(f"  Padding con {n_distractors} distractor sintetici "
              f"(pool target {args.pool_size})...", flush=True)
        distractors = _generate_distractor_tools(executors, n_distractors)
        executors = executors + distractors
        print(f"  {len(executors)} executor totali (reali + distractor)",
              flush=True)

    print("Estrazione corpus...", flush=True)
    corpus = load_corpus(max_n=args.n)
    print(f"  {len(corpus)} query estratte (max {args.n})", flush=True)

    if args.strategies:
        strategies = [s.strip() for s in args.strategies.split(",")]
    else:
        from prefilter_strategies import list_strategies
        # Skip alias "legacy" (= "token_flat")
        strategies = [s for s in list_strategies() if s != "legacy"]
    print(f"Strategy in bench: {strategies}", flush=True)

    results = run_bench(
        strategies, corpus, executors, k_max=args.k_max,
        verbose=args.verbose,
    )

    report = render_report(results, corpus, args.k_max)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"\nReport scritto in {args.output}")
    # Sintesi a console
    print("\n=== SINTESI ===")
    for sname, r in sorted(
        results.items(),
        key=lambda kv: kv[1].get("recall_5", 0) if "error" not in kv[1] else -1,
        reverse=True,
    ):
        if "error" in r:
            print(f"  {sname:24s} ERROR: {r['error']}")
        else:
            print(
                f"  {sname:24s} r@5={r['recall_5']:.3f} "
                f"r@1={r['recall_1']:.3f} "
                f"mean={r['mean_ms']:.1f}ms p95={r['p95_ms']:.1f}ms"
            )


if __name__ == "__main__":
    main()
