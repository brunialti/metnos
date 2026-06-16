#!/usr/bin/env python3
"""issues.py — visore LOCALE dei trattamenti issue.

Use case «github issue management» — NON è parte di Metnos, è un suo uso. Vive
sotto internal/ → ESCLUSO dallo snapshot pubblico (export-public.sh). Read-only
sullo store `github_issue_qa` (nessuna scrittura, nessuna chiamata di rete).

Ciclo di vita (macchina a stati issue_qa):  new → prepared → approved → posted
Bucket:
  pending    status 'new'                  (non-answer: nessuna bozza ancora)
  staging    status 'prepared' | 'approved'(bozza pronta, NON pubblicata)
  published  status 'posted'
  history    tutti

Uso:
  internal/tools/issues.py                  # riassunto: conteggi + i 3 bucket
  internal/tools/issues.py pending
  internal/tools/issues.py staging
  internal/tools/issues.py published
  internal/tools/issues.py history
  internal/tools/issues.py --repo owner/name --limit 50
  internal/tools/issues.py staging --full   # stampa anche il testo bozza
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# Root auto-derivata (§7.11 rename-resilient): internal/tools/issues.py
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))

import github_issue_qa_store as store  # noqa: E402

PENDING = ("new",)
STAGING = ("prepared", "approved")
PUBLISHED = ("posted",)
BUCKETS = {
    "pending": PENDING,
    "staging": STAGING,
    "published": PUBLISHED,
    "history": PENDING + STAGING + PUBLISHED,
}


def _default_repo() -> str | None:
    """env METNOS_GITHUB_REPO → cred github.repo → None (tutti i repo)."""
    env = os.environ.get("METNOS_GITHUB_REPO")
    if env:
        return env.strip()
    try:
        import credentials
        d = credentials.load("github") or {}
        return (d.get("repo") or "").strip() or None
    except Exception:
        return None


def _fmt_ts(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _trunc(s, n) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _print_table(recs: list[dict]) -> None:
    if not recs:
        print("  (nessuna)")
        return
    hdr = f"  {'#':>5}  {'STATUS':<9}  {'CLASS':<12}  {'DR':<2} {'AC':<2}  {'POSTED':<10}  TITLE"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in sorted(recs, key=lambda x: x.get("issue_number", 0), reverse=True):
        dr = "y" if (r.get("draft_reply") or "").strip() else "-"
        ac = "y" if (r.get("accepted_reply") or "").strip() else "-"
        print(f"  {r.get('issue_number', '?'):>5}  "
              f"{_trunc(r.get('status'), 9):<9}  "
              f"{_trunc(r.get('classification'), 12):<12}  "
              f"{dr:<2} {ac:<2}  {_fmt_ts(r.get('posted_at')):<10}  "
              f"{_trunc(r.get('title'), 56)}")


def _print_full(recs: list[dict]) -> None:
    for r in sorted(recs, key=lambda x: x.get("issue_number", 0), reverse=True):
        print(f"\n  ── #{r.get('issue_number')} [{r.get('status')}] "
              f"{_trunc(r.get('title'), 70)}")
        if r.get("classification"):
            print(f"     class: {r['classification']}")
        if (r.get("draft_reply") or "").strip():
            print(f"     draft:    {_trunc(r['draft_reply'], 300)}")
        if (r.get("accepted_reply") or "").strip():
            print(f"     accepted: {_trunc(r['accepted_reply'], 300)}")
        if r.get("posted_at"):
            print(f"     posted:   {_fmt_ts(r['posted_at'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="visore locale issue (read-only)")
    ap.add_argument("bucket", nargs="?", default="summary",
                    choices=["summary", "pending", "staging", "published",
                             "history"],
                    help="bucket da mostrare (default: summary)")
    ap.add_argument("--repo", default=None, help="owner/name (default: cred)")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--full", action="store_true",
                    help="mostra anche il testo bozza/accepted")
    args = ap.parse_args()

    repo = args.repo or _default_repo()
    recs = store.list_records(repo=repo, limit=args.limit)
    scope = f"repo={repo}" if repo else "tutti i repo"
    by = {b: [r for r in recs if r.get("status") in BUCKETS[b]]
          for b in ("pending", "staging", "published")}

    if args.bucket == "summary":
        print(f"\nIssue ({scope}) — totale {len(recs)}: "
              f"pending {len(by['pending'])} · staging {len(by['staging'])} · "
              f"published {len(by['published'])}\n")
        for b in ("pending", "staging", "published"):
            print(f"▸ {b.upper()}  ({len(by[b])})")
            _print_table(by[b])
            print()
        return 0

    sel = [r for r in recs if r.get("status") in BUCKETS[args.bucket]]
    print(f"\n▸ {args.bucket.upper()}  ({scope}) — {len(sel)}\n")
    (_print_full if args.full else _print_table)(sel)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
