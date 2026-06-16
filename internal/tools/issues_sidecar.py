#!/usr/bin/env python3
"""issues_sidecar.py — sidecar HTTP LOCALE per l'amministrazione degli issue.

Use case «github issue management» — NON è parte di Metnos, è un suo uso. Vive
sotto internal/ → ESCLUSO dallo snapshot pubblico (export-public.sh). Read-only
sullo store `github_issue_qa` (nessuna scrittura, nessuna chiamata di rete).
Bind su 0.0.0.0 (raggiungibile in LAN, es. http://192.168.1.33:8889/).

Ciclo di vita issue_qa:  new → prepared → approved → posted
  pending    'new'                    (non-answer: nessuna bozza)
  staging    'prepared' | 'approved'  (bozza pronta, non pubblicata)
  published  'posted'

Uso:
  internal/tools/issues_sidecar.py            # porta 8888 (o prima libera)
  internal/tools/issues_sidecar.py --port 9000
Apri http://127.0.0.1:8888/ — refresh automatico ogni 30s.
"""
from __future__ import annotations

import argparse
import html
import socket
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "runtime"))

from aiohttp import web  # noqa: E402
import github_issue_qa_store as store  # noqa: E402

PENDING = ("new",)
STAGING = ("prepared", "approved")
PUBLISHED = ("posted",)


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _rows_html(recs: list[dict]) -> str:
    if not recs:
        return "<tr><td colspan='7' class='empty'>nessuna</td></tr>"
    out = []
    for r in sorted(recs, key=lambda x: x.get("issue_number", 0), reverse=True):
        dr = "✓" if (r.get("draft_reply") or "").strip() else "—"
        ac = "✓" if (r.get("accepted_reply") or "").strip() else "—"
        out.append(
            "<tr>"
            f"<td class='num'>#{_esc(r.get('issue_number'))}</td>"
            f"<td><span class='st st-{_esc(r.get('status'))}'>{_esc(r.get('status'))}</span></td>"
            f"<td>{_esc(r.get('classification'))}</td>"
            f"<td class='c'>{dr}</td><td class='c'>{ac}</td>"
            f"<td>{_fmt_ts(r.get('posted_at'))}</td>"
            f"<td class='ti' title='{_esc(r.get('draft_reply') or r.get('accepted_reply'))}'>"
            f"{_esc(r.get('title')) or '<i>—</i>'}</td>"
            "</tr>")
    return "".join(out)


def _section(title: str, recs: list[dict]) -> str:
    return (
        f"<section><h2>{title} <span class='n'>{len(recs)}</span></h2>"
        "<table><thead><tr><th>#</th><th>stato</th><th>classe</th>"
        "<th>bozza</th><th>acc</th><th>posted</th><th>titolo</th></tr></thead>"
        f"<tbody>{_rows_html(recs)}</tbody></table></section>")


_CSS = """
body{font:14px/1.4 system-ui,sans-serif;margin:0;background:#14110e;color:#e8e2d8}
header{padding:14px 22px;background:#1d1812;border-bottom:1px solid #3a3127}
header h1{margin:0;font-size:17px;color:#c8a86a}
header .sub{color:#8a7f6f;font-size:12px;margin-top:3px}
main{padding:18px 22px;display:flex;flex-direction:column;gap:22px}
section h2{font-size:14px;margin:0 0 8px;color:#c8a86a;text-transform:uppercase;letter-spacing:.5px}
section h2 .n{background:#3a3127;color:#e8e2d8;border-radius:10px;padding:1px 8px;font-size:12px;margin-left:6px}
table{width:100%;border-collapse:collapse;background:#1d1812;border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #2a241c;font-size:13px}
th{color:#8a7f6f;font-weight:600;font-size:11px;text-transform:uppercase}
td.num{font-variant-numeric:tabular-nums;color:#c8a86a}
td.c{text-align:center}
td.ti{max-width:42ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.empty{color:#6a6055;font-style:italic;text-align:center}
.st{border-radius:4px;padding:1px 7px;font-size:11px;font-weight:600}
.st-new{background:#5a3a1a;color:#ffb86b}
.st-prepared{background:#3a4a5a;color:#8fc6ff}
.st-approved{background:#2a4a32;color:#8fe0a0}
.st-posted{background:#2a2a2a;color:#9a9a9a}
"""


async def index(request: web.Request) -> web.Response:
    recs = store.list_records(limit=1000)
    by = {
        "PENDING (non-answer)": [r for r in recs if r.get("status") in PENDING],
        "STAGING (non pubblicate)": [r for r in recs if r.get("status") in STAGING],
        "PUBLISHED / STORICO": [r for r in recs if r.get("status") in PUBLISHED],
    }
    repos = sorted({r.get("repo") for r in recs if r.get("repo")})
    body = "".join(_section(t, rs) for t, rs in by.items())
    page = (
        "<!doctype html><html lang='it'><head><meta charset='utf-8'>"
        "<meta http-equiv='refresh' content='30'>"
        "<title>Metnos · issue admin</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header><h1>Metnos · amministrazione issue</h1>"
        f"<div class='sub'>{_esc(', '.join(repos)) or 'nessun repo'} · "
        f"{len(recs)} record · sidecar locale read-only · refresh 30s</div></header>"
        f"<main>{body}</main></body></html>")
    return web.Response(text=page, content_type="text/html")


async def api_issues(request: web.Request) -> web.Response:
    return web.json_response(store.list_records(limit=1000))


def _pick_port(start: int, tries: int = 12) -> int | None:
    for p in range(start, start + tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="sidecar HTTP locale issue (read-only)")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--strict", action="store_true",
                    help="usa SOLO la porta richiesta (no fallback)")
    args = ap.parse_args()

    port = args.port if args.strict else _pick_port(args.port)
    if port is None:
        print(f"porta {args.port} occupata (nessuna libera in {args.port}-"
              f"{args.port + 11}). Usa --port N.", file=sys.stderr)
        return 1
    if port != args.port:
        print(f"porta {args.port} occupata → uso {port}")

    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/api/issues", api_issues)])
    print(f"issue admin sidecar → http://0.0.0.0:{port}/ (LAN) (Ctrl-C per uscire)")
    web.run_app(app, host="0.0.0.0", port=port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
