#!/usr/bin/env python3
"""metnos-promotions — CLI per gestire promote synth daemon.

Usage:
    python3 -m admin.promotions_cli list [--state STATE] [--days N]
    python3 -m admin.promotions_cli show <proposal_id>
    python3 -m admin.promotions_cli rollback <proposal_id>

Stato `state`: promoted_grace | promoted_finalized | review_needed |
                rolled_back | archived.

Determinismo §7.9. ADR Promoter.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _format_iso_relative(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc,
        )
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - dt
    if delta.total_seconds() < 0:
        secs = -delta.total_seconds()
        if secs < 3600:
            return f"in {int(secs // 60)}m"
        if secs < 86400:
            return f"in {int(secs // 3600)}h"
        return f"in {int(secs // 86400)}g"
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)}m fa"
    if secs < 86400:
        return f"{int(secs // 3600)}h fa"
    return f"{int(secs // 86400)}g fa"


def cmd_list(args) -> int:
    from jobs.promoter_state import list_by_state
    from output_format import format_table

    if args.state:
        states = [args.state]
    else:
        states = ["promoted_grace", "promoted_finalized", "review_needed",
                  "rolled_back", "archived"]
    rows = list_by_state(states, limit=500)

    # Filtra per days su promoted_at o created_at.
    if args.days:
        import time as _t
        cutoff = _t.time() - args.days * 86400
        filtered = []
        for r in rows:
            anchor = r.get("promoted_at") or r.get("created_at") or ""
            try:
                dt = datetime.strptime(anchor, "%Y-%m-%dT%H:%M:%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                if dt.timestamp() < cutoff:
                    continue
            except ValueError:
                pass
            filtered.append(r)
        rows = filtered

    if not rows:
        print("(nessuna promozione corrispondente)")
        return 0
    table_rows = []
    for r in rows:
        table_rows.append([
            (r.get("proposal_id") or "?")[:40],
            r.get("name") or "?",
            r.get("state") or "?",
            _format_iso_relative(r.get("promoted_at")),
            _format_iso_relative(r.get("grace_until")),
        ])
    print(format_table(
        ["ID", "Name", "State", "Promoted", "Grace until"],
        table_rows, align=["left", "left", "left", "left", "left"],
    ))
    print(f"\nTotale: {len(rows)}")
    return 0


def cmd_show(args) -> int:
    from jobs.promoter_state import load_proposal_state
    row = load_proposal_state(args.id)
    if row is None:
        print(f"(non trovato: {args.id})", file=sys.stderr)
        return 1
    # Pretty print verdict se presente.
    verdict_raw = row.get("evaluator_verdict")
    if verdict_raw:
        try:
            row["evaluator_verdict"] = json.loads(verdict_raw)
        except (TypeError, ValueError):
            pass
    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_rollback(args) -> int:
    from jobs.promoter_rollback import rollback_promotion
    result = rollback_promotion(args.id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="metnos-promotions",
        description=__doc__.split("\n")[0],
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ls = sub.add_parser("list", help="Lista promozioni")
    p_ls.add_argument("--state",
                        choices=["promoted_grace", "promoted_finalized",
                                  "review_needed", "rolled_back", "archived"],
                        help="Filtra per stato")
    p_ls.add_argument("--days", type=int, default=30,
                        help="Finestra temporale in giorni (default 30)")

    p_show = sub.add_parser("show", help="Dettaglio promozione")
    p_show.add_argument("id", help="proposal_id")

    p_rb = sub.add_parser("rollback", help="Annulla una promozione")
    p_rb.add_argument("id", help="proposal_id")

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_argparser()
    args = ap.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "show": cmd_show,
        "rollback": cmd_rollback,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
