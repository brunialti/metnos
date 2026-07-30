#!/usr/bin/env python3
"""metnos-proposals — CLI per review batch del backlog di proposte.

Usage:
    python3 -m admin.proposals_cli summary                   # overview compatto
    python3 -m admin.proposals_cli list-synth [--state S]    # synt_proposals
    python3 -m admin.proposals_cli list-candidates [--kind K]
    python3 -m admin.proposals_cli show-synth <id>           # dettaglio
    python3 -m admin.proposals_cli cleanup [--dry-run]       # esegue ADR 0096

Stile output: deterministico (ADR 0095). Niente LLM.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as _C  # §7.11

SYNT_PROPOSALS_DIR = _C.PATH_USER_DATA / "synt_proposals"
INTROVERTIVA_DIR = _C.PATH_USER_DATA / "introvertiva"


def _read_json_safe(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _read_jsonl_safe(p: Path) -> list[dict]:
    out: list[dict] = []
    try:
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        pass
    return out


# ─── summary ──────────────────────────────────────────────────────────────


def cmd_summary(args) -> int:
    from output_format import format_kv, format_section, format_table

    print(format_section("Backlog proposte", ""))
    print()

    # synt_proposals
    synth_files = sorted(SYNT_PROPOSALS_DIR.glob("*.json")) \
        if SYNT_PROPOSALS_DIR.exists() else []
    by_state: Counter = Counter()
    by_name: Counter = Counter()
    for p in synth_files:
        if "_archived" in p.parts:
            continue
        d = _read_json_safe(p) or {}
        by_state[d.get("final_state", "?")] += 1
        by_name[d.get("name") or d.get("expected_name") or "?"] += 1
    print(format_section("synt_proposals/", ""))
    print(format_kv("Totale", len(synth_files) - sum(
        1 for p in synth_files if "_archived" in p.parts)))
    if by_state:
        rows = [[s, str(c)] for s, c in sorted(by_state.items(), key=lambda x: -x[1])]
        print(format_table(["Stato", "Count"], rows, align=["left", "right"]))
    print()
    if by_name:
        # Mostra solo gli executor con >1 proposal (potenzialmente review-worthy)
        dups = [(n, c) for n, c in by_name.items() if c > 1]
        if dups:
            dups.sort(key=lambda x: -x[1])
            rows = [[n, str(c)] for n, c in dups[:10]]
            print("**Executor con piu' di una proposal (top 10)**")
            print(format_table(["Executor", "N"], rows, align=["left", "right"]))
            print()

    # introvertiva
    cand_files = sorted(INTROVERTIVA_DIR.glob("candidates_*.jsonl")) \
        if INTROVERTIVA_DIR.exists() else []
    from proposals_cleanup import (_kind_from_filename, _candidate_signature)
    by_kind: Counter = Counter()
    sig_count: defaultdict[tuple, int] = defaultdict(int)
    sig_total_uses: defaultdict[tuple, int] = defaultdict(int)
    sig_label: dict[tuple, str] = {}
    for p in cand_files:
        if "_archived" in p.parts:
            continue
        kind_hint = _kind_from_filename(p)
        for r in _read_jsonl_safe(p):
            kind = r.get("kind") or kind_hint or "?"
            by_kind[kind] += 1
            sig = _candidate_signature(r, kind_from_file=kind_hint)
            sig_count[sig] += 1
            uses_val = (r.get("uses") or r.get("total_uses") or 0)
            sig_total_uses[sig] = max(sig_total_uses[sig], int(uses_val))
            # Etichetta umana per il record (per top signature display)
            if sig not in sig_label:
                sig_label[sig] = _label_for_signature(sig, r)
    print(format_section("introvertiva/ candidates", ""))
    print(format_kv("File", len([p for p in cand_files
                                   if "_archived" not in p.parts])))
    print(format_kv("Record totali", sum(by_kind.values())))
    print(format_kv("Signature uniche", len(sig_count)))
    if by_kind:
        rows = [[k, str(c)] for k, c in sorted(by_kind.items(), key=lambda x: -x[1])]
        print(format_table(["Kind", "Count"], rows, align=["left", "right"]))
    print()

    if sig_count:
        # Top signature per uses
        top_sigs = sorted(sig_count.items(),
                            key=lambda x: -sig_total_uses[x[0]])[:10]
        print("**Top signature per uses**")
        rows = [[
            sig[0], sig_label.get(sig, "-"),
            str(sig_total_uses[sig]), str(count),
        ] for sig, count in top_sigs]
        print(format_table(
            ["Kind", "Signature", "Uses", "Recurrences"],
            rows, align=["left", "left", "right", "right"],
        ))
    return 0


def _label_for_signature(sig: tuple, rec: dict) -> str:
    """Estrae una label umana da un signature schema-aware."""
    kind = sig[0] if sig else ""
    if kind in ("legacy_orphan", "dedupe"):
        src = rec.get("src_executor") or "-"
        dst = rec.get("dst_executor") or "-"
        return f"{src} → {dst}"
    if kind == "specialize":
        return rec.get("proposed_name") or rec.get("executor") or "-"
    if kind == "generalize":
        pat = rec.get("pattern")
        if isinstance(pat, list):
            return " → ".join(pat)
        return "-"
    return rec.get("proposed_name") or rec.get("executor") or "-"


# ─── list-synth ───────────────────────────────────────────────────────────


def cmd_list_synth(args) -> int:
    if not SYNT_PROPOSALS_DIR.exists():
        print("(nessuna directory synt_proposals)")
        return 0
    rows = []
    for p in sorted(SYNT_PROPOSALS_DIR.glob("*.json")):
        if "_archived" in p.parts:
            continue
        d = _read_json_safe(p) or {}
        if args.state and d.get("final_state") != args.state:
            continue
        ts = d.get("ts_start") or p.stat().st_mtime
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            dt = "?"
        rows.append([
            d.get("id", p.stem)[:40],
            d.get("name") or d.get("expected_name") or "?",
            d.get("final_state", "?"),
            dt,
        ])
    if not rows:
        print("(nessuna proposal corrispondente)")
        return 0
    from output_format import format_table
    print(format_table(["ID", "Name", "State", "Date"], rows,
                          align=["left", "left", "left", "left"]))
    print(f"\nTotale: {len(rows)}")
    return 0


# ─── list-candidates ──────────────────────────────────────────────────────


def cmd_list_candidates(args) -> int:
    if not INTROVERTIVA_DIR.exists():
        print("(nessuna directory introvertiva)")
        return 0
    rows = []
    for p in sorted(INTROVERTIVA_DIR.glob("candidates_*.jsonl")):
        if "_archived" in p.parts:
            continue
        for r in _read_jsonl_safe(p):
            kind = r.get("kind", "?")
            if args.kind and kind != args.kind:
                continue
            rows.append([
                kind,
                r.get("src_executor", "-"),
                r.get("dst_executor", "-"),
                r.get("proposed_name", "-"),
                str(r.get("uses") or 0),
                f"{r.get('weight') or 0:.3f}",
            ])
    if not rows:
        print("(nessun candidato corrispondente)")
        return 0
    from output_format import format_table
    print(format_table(
        ["Kind", "Src", "Dst", "Proposed", "Uses", "Weight"], rows,
        align=["left", "left", "left", "left", "right", "right"],
    ))
    print(f"\nTotale: {len(rows)}")
    return 0


# ─── show-synth ───────────────────────────────────────────────────────────


def cmd_show_synth(args) -> int:
    target = args.id
    for p in SYNT_PROPOSALS_DIR.glob("*.json"):
        if "_archived" in p.parts:
            continue
        d = _read_json_safe(p) or {}
        if d.get("id") == target or p.stem == target:
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return 0
    print(f"(non trovato: {target})", file=sys.stderr)
    return 1


# ─── cleanup ──────────────────────────────────────────────────────────────


def cmd_cleanup(args) -> int:
    from proposals_cleanup import run_cleanup
    rep = run_cleanup(dry_run=args.dry_run)
    rep.get("synth_proposals", {}).pop("archived_paths", None)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def _resolve_proposal_path(target: str) -> Path | None:
    """Risolve `target` come id/stem/path verso un file in synt_proposals/."""
    p = Path(target)
    if p.exists():
        return p
    if SYNT_PROPOSALS_DIR.exists():
        for cand in SYNT_PROPOSALS_DIR.glob("*.json"):
            if "_archived" in cand.parts:
                continue
            d = _read_json_safe(cand) or {}
            if d.get("id") == target or cand.stem == target:
                return cand
    return None


def cmd_evaluate(args) -> int:
    """Auto-evaluator di una proposta synth (ADR 0122)."""
    from proposal_evaluator import evaluate_proposal

    p = _resolve_proposal_path(args.id)
    if p is None:
        print(f"(non trovato: {args.id})", file=sys.stderr)
        return 1
    result = evaluate_proposal(p, audit=not args.no_audit)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_aggregate_eta(args) -> int:
    """Aggrega i turn JSONL ultimi N giorni in proposals_eta.sqlite (ADR 0122)."""
    import time as _time
    from proposals_eta_index import aggregate_from_jsonls
    since = _time.time() - args.days * 86400
    rep = aggregate_from_jsonls(since_ts=since)
    print(json.dumps(rep, indent=2))
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="metnos-proposals",
                                  description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary", help="Overview compatto del backlog")

    p_ls = sub.add_parser("list-synth", help="Lista proposal synth attive")
    p_ls.add_argument("--state",
                       choices=["synthesized", "abandoned", "failed"],
                       help="Filtra per final_state")

    p_lc = sub.add_parser("list-candidates", help="Lista candidati introvertiva")
    p_lc.add_argument("--kind",
                       choices=["legacy_orphan", "generalize", "specialize",
                                  "dedupe"],
                       help="Filtra per kind")

    p_show = sub.add_parser("show-synth", help="Dettaglio proposal synth")
    p_show.add_argument("id")

    p_cln = sub.add_parser("cleanup", help="Esegui ADR 0096 cleanup")
    p_cln.add_argument("--dry-run", action="store_true")

    p_ev = sub.add_parser("evaluate",
                            help="Auto-evaluator di una proposta synth (ADR 0122)")
    p_ev.add_argument("id", help="proposal id, stem, o path completo")
    p_ev.add_argument("--no-audit", action="store_true",
                       help="Non scrivere su synth_audit/proposal_evaluator.jsonl")

    p_eta = sub.add_parser("aggregate-eta",
                             help="Aggrega i turn JSONL ultimi N giorni nell'index ETA")
    p_eta.add_argument("--days", type=int, default=7)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_argparser()
    args = ap.parse_args(argv)
    handlers = {
        "summary": cmd_summary,
        "list-synth": cmd_list_synth,
        "list-candidates": cmd_list_candidates,
        "show-synth": cmd_show_synth,
        "cleanup": cmd_cleanup,
        "evaluate": cmd_evaluate,
        "aggregate-eta": cmd_aggregate_eta,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
