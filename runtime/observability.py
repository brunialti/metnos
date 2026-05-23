"""runtime.observability — dashboard statica di osservabilita' di Metnos.

Legge le sorgenti dati del runtime (mnestoma, pairings, turns, vaglio,
scheduler, test) e produce un singolo HTML autocontenuto in
`workspace/dashboard/index.html`. Niente server live, niente WebSocket:
si rigenera on demand. Aprire con browser locale.

Uso:
    python3 -m observability render            # genera in workspace/dashboard/
    python3 -m observability render --out X    # path custom

Coerente col principio self-host: nessuna libreria esterna, niente
JavaScript. Solo HTML + CSS inline.
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from logging_setup import get_logger
log = get_logger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

# ADR 0148 rename-resilient
import config as _C  # noqa: E402
DEFAULT_OUT = _C.PATH_WORKSPACE / "dashboard" / "index.html"
TURNS_DIR = _C.PATH_TURNS
VAGLIO_DIR = _C.PATH_USER_DATA / "vaglio"
SCHEDULER_DB = _C.PATH_USER_STATE / "scheduler_v2.sqlite"
TESTS_DB = _C.PATH_RUNTIME / "testing" / "tests.db"


def _h(s) -> str:
    """HTML escape sicuro."""
    return html.escape(str(s), quote=True)


# --- raccolta dati ---------------------------------------------------------

def _collect_mnestoma() -> dict:
    try:
        from mnestoma import Mnestoma
        m = Mnestoma()
        try:
            return {
                "stats": m.stats(),
                "top_active": [
                    {"src": x.src_executor, "dst": x.dst_executor,
                     "weight": x.weight, "uses": x.uses, "state": x.state}
                    for x in m.top_active(limit=15, state="active")
                ],
                "top_proto": [
                    {"src": x.src_executor, "dst": x.dst_executor,
                     "weight": x.weight, "uses": x.uses,
                     "summary": (x.desired_sig or {}).get("summary", "") if x.desired_sig else ""}
                    for x in m.top_active(limit=10, state="proto")
                ],
                "audit": m.audit_recent(limit=20),
            }
        finally:
            m.close()
    except Exception as e:
        return {"error": str(e)}


def _collect_pairings() -> dict:
    try:
        import pairing
        active = [
            {"channel": p.channel, "sender": p.sender_id, "level": p.autonomy_level,
             "paired_at": p.paired_at, "last_seen": p.last_seen, "paired_by": p.paired_by}
            for p in pairing.list_pairings(include_revoked=False)
        ]
        revoked = [
            {"channel": p.channel, "sender": p.sender_id, "level": p.autonomy_level,
             "revoked_at": p.revoked_at}
            for p in pairing.list_pairings(include_revoked=True)
            if p.revoked_at
        ]
        return {"active": active, "revoked": revoked}
    except Exception as e:
        return {"error": str(e)}


def _collect_recent_turns(limit: int = 15) -> list[dict]:
    if not TURNS_DIR.exists():
        return []
    files = sorted(TURNS_DIR.glob("*.jsonl"), reverse=True)
    rows: list[dict] = []
    for f in files[:5]:  # ultimi 5 giorni al massimo
        try:
            for line in reversed(f.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception as _e:  # silent swallow (auto-fixed)
                    log.warning("silent exception in %s: %s", __name__, _e)
                if len(rows) >= limit:
                    return rows[:limit]
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    return rows[:limit]


def _collect_vaglio_decisions(limit: int = 20) -> list[dict]:
    if not VAGLIO_DIR.exists():
        return []
    files = sorted(VAGLIO_DIR.glob("*.jsonl"), reverse=True)
    rows: list[dict] = []
    for f in files[:3]:
        try:
            for line in reversed(f.read_text(encoding="utf-8").splitlines()):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception as _e:  # silent swallow (auto-fixed)
                    log.warning("silent exception in %s: %s", __name__, _e)
                if len(rows) >= limit:
                    return rows[:limit]
        except Exception as _e:  # silent swallow (auto-fixed)
            log.warning("silent exception in %s: %s", __name__, _e)
    return rows[:limit]


def _collect_scheduler() -> list[dict]:
    if not SCHEDULER_DB.exists():
        return []
    try:
        c = sqlite3.connect(str(SCHEDULER_DB))
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT name, trigger AS schedule, last_run_at, last_status "
            "FROM schedule_entries WHERE enabled=1 ORDER BY name"
        ).fetchall()
        out = [dict(r) for r in rows]
        c.close()
        return out
    except Exception:
        return []


def _collect_tests() -> dict:
    if not TESTS_DB.exists():
        return {"error": "tests DB assente"}
    try:
        c = sqlite3.connect(str(TESTS_DB))
        c.row_factory = sqlite3.Row
        modules = c.execute("SELECT COUNT(*) AS n FROM modules").fetchone()["n"]
        cases = c.execute("SELECT COUNT(*) AS n FROM test_cases WHERE enabled = 1").fetchone()["n"]
        last = c.execute(
            """SELECT last_status, COUNT(*) AS n FROM test_cases
               WHERE enabled = 1 GROUP BY last_status"""
        ).fetchall()
        per_module = c.execute(
            """SELECT m.name, COUNT(*) AS n FROM test_cases tc
               JOIN modules m ON m.id = tc.module_id
               WHERE tc.enabled = 1 GROUP BY m.name ORDER BY n DESC LIMIT 10"""
        ).fetchall()
        c.close()
        return {
            "modules": modules,
            "cases": cases,
            "by_status": {r["last_status"] or "(never run)": r["n"] for r in last},
            "top_modules": [dict(r) for r in per_module],
        }
    except Exception as e:
        return {"error": str(e)}


# --- rendering -------------------------------------------------------------

_CSS = """
:root {
    --navy: #1A477A; --navy-light: #2B6CB0; --sage: #548235;
    --bronze: #A0522D; --plum: #6B21A8;
    --bg: #FAFBFC; --text: #1a1a1a;
    --border: #d0d7de; --code-bg: #f6f8fa;
    --muted: #64748B;
    --green: #14532d; --green-bg: #dcfce7;
    --red: #991b1b; --red-bg: #fee2e2;
    --amber: #92400e; --amber-bg: #fef3c7;
}
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', Calibri, -apple-system, sans-serif; font-size: 11pt;
       line-height: 1.55; color: var(--text); background: var(--bg);
       max-width: 1200px; margin: 0 auto; padding: 24px 30px; }
h1 { color: var(--navy); font-size: 20pt; border-bottom: 3px solid var(--navy);
     padding-bottom: 8px; margin: 0 0 6px; }
h2 { color: var(--navy-light); font-size: 14pt; margin-top: 24px;
     border-bottom: 1px solid var(--border); padding-bottom: 4px; }
h3 { color: var(--navy); font-size: 11pt; margin: 12px 0 4px; text-transform: uppercase;
     letter-spacing: 1px; font-size: 9.5pt; color: var(--muted); }
p, ul { margin: 6px 0; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 10pt; }
th { background: var(--navy); color: white; padding: 6px 8px; text-align: left; font-weight: 600; }
td { padding: 5px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:nth-child(even) td { background: #f0f4f8; }
code { font-family: Consolas, 'Courier New', monospace; font-size: 9.5pt;
       background: var(--code-bg); padding: 1px 4px; border-radius: 3px; }
.subtitle { color: var(--muted); font-size: 10pt; margin-bottom: 18px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
         gap: 10px; margin: 12px 0 16px; }
.card { background: white; border: 1px solid var(--border); border-radius: 6px;
        padding: 10px 14px; }
.card .label { font-size: 9pt; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
.card .value { font-size: 18pt; font-weight: 700; color: var(--navy); }
.pill { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 9pt;
        font-weight: 600; letter-spacing: 1px; }
.pill.ok { background: var(--green-bg); color: var(--green); }
.pill.warn { background: var(--amber-bg); color: var(--amber); }
.pill.err { background: var(--red-bg); color: var(--red); }
.pill.muted { background: var(--code-bg); color: var(--muted); }
.empty { color: var(--muted); font-style: italic; padding: 8px 0; }
.error { background: var(--red-bg); color: var(--red); padding: 8px 12px;
         border-left: 3px solid var(--red); border-radius: 0 4px 4px 0; }
.bar-wrap { background: var(--code-bg); border-radius: 4px; height: 14px;
            overflow: hidden; min-width: 60px; }
.bar { height: 100%; background: var(--sage); }
.muted { color: var(--muted); font-size: 9.5pt; }
"""


def _render_metric_card(label: str, value, sub: str = "") -> str:
    return (f'<div class="card"><div class="label">{_h(label)}</div>'
            f'<div class="value">{_h(value)}</div>'
            f'<div class="muted">{_h(sub)}</div></div>')


def _render_mnestoma(data: dict) -> str:
    if "error" in data:
        return f'<h2>Mnestoma</h2><div class="error">errore: {_h(data["error"])}</div>'
    s = data["stats"]
    cards = "".join([
        _render_metric_card("totale", s["total_mnests"]),
        _render_metric_card("active", s["active"]),
        _render_metric_card("proto", s["proto"]),
        _render_metric_card("decaying", s["decaying"]),
        _render_metric_card("eventi", s["events"]),
    ])
    out = [f'<h2>Mnestoma</h2><div class="cards">{cards}</div>']

    out.append('<h3>Top mnest attivi (per peso)</h3>')
    if not data["top_active"]:
        out.append('<div class="empty">Nessun mnest attivo registrato.</div>')
    else:
        rows = []
        for x in data["top_active"]:
            bar_pct = int(x["weight"] * 100)
            rows.append(
                f'<tr><td><code>{_h(x["src"])}</code> &rarr; <code>{_h(x["dst"])}</code></td>'
                f'<td><div class="bar-wrap"><div class="bar" style="width:{bar_pct}%"></div></div> {x["weight"]:.3f}</td>'
                f'<td>{x["uses"]}</td></tr>'
            )
        out.append(f'<table><thead><tr><th>Arco</th><th style="width:30%">Peso</th><th style="width:12%">Uses</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')

    if data["top_proto"]:
        out.append('<h3>Proto-mnest in attesa di sintesi</h3>')
        rows = []
        for x in data["top_proto"]:
            rows.append(
                f'<tr><td><code>{_h(x["src"])}</code> &rarr; <code>{_h(x["dst"])}</code></td>'
                f'<td>{x["weight"]:.3f}</td><td>{x["uses"]}</td>'
                f'<td class="muted">{_h(x["summary"][:80])}</td></tr>'
            )
        out.append(f'<table><thead><tr><th>Desideri</th><th>Peso</th><th>Uses</th><th>Contesto</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')

    if data.get("audit"):
        out.append('<h3>Eventi recenti (ultimi 20)</h3>')
        rows = []
        for ev in data["audit"]:
            edge = f'{ev.get("src_executor") or "?"}&rarr;{ev.get("dst_executor") or "?"}'
            delta = ev.get("delta") or 0
            rows.append(
                f'<tr><td class="muted">{_h(ev["ts"])}</td>'
                f'<td><span class="pill muted">{_h(ev["kind"])}</span></td>'
                f'<td>{delta:+.3f}</td>'
                f'<td><code>{edge}</code></td>'
                f'<td class="muted">{_h(ev.get("reason") or "")}</td></tr>'
            )
        out.append(f'<table><thead><tr><th>Quando</th><th>Tipo</th><th>Δ</th><th>Arco</th><th>Reason</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')

    return "\n".join(out)


def _render_pairings(data: dict) -> str:
    if "error" in data:
        return f'<h2>Pairings</h2><div class="error">errore: {_h(data["error"])}</div>'
    out = ['<h2>Pairings</h2>']
    if not data["active"]:
        out.append('<div class="empty">Nessun pairing attivo. Genera un codice con <code>python3 -m pairing generate Full</code>.</div>')
    else:
        rows = []
        for p in data["active"]:
            level_pill = {"Full": "ok", "Supervised": "warn", "ReadOnly": "muted"}.get(p["level"], "muted")
            rows.append(
                f'<tr><td><code>{_h(p["channel"])}</code></td>'
                f'<td><code>{_h(p["sender"])}</code></td>'
                f'<td><span class="pill {level_pill}">{_h(p["level"])}</span></td>'
                f'<td class="muted">{_h(p["paired_at"])}</td>'
                f'<td class="muted">{_h(p.get("paired_by") or "")}</td>'
                f'<td class="muted">{_h(p.get("last_seen") or "(mai)")}</td></tr>'
            )
        out.append(f'<table><thead><tr><th>Canale</th><th>Sender</th><th>Livello</th><th>Pairato</th><th>Da</th><th>Ultimo visto</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    if data.get("revoked"):
        out.append(f'<p class="muted">{len(data["revoked"])} pairing revocati nello storico.</p>')
    return "\n".join(out)


def _render_turns(turns: list[dict]) -> str:
    out = ['<h2>Turni recenti</h2>']
    if not turns:
        out.append('<div class="empty">Nessun turno registrato. Lancia un turno via <code>python3 -m channels.daemon</code> o CLI.</div>')
        return "\n".join(out)
    rows = []
    for t in turns:
        kind = t.get("final_kind") or "?"
        kind_pill = {"answer": "ok", "error": "err", "cap_same_executor": "warn",
                     "cap_steps": "warn"}.get(kind, "muted")
        steps = len(t.get("steps", []))
        msg = (t.get("final_message") or "")[:120]
        ts = datetime.fromtimestamp(t.get("ts_start") or 0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        rows.append(
            f'<tr><td class="muted">{_h(ts)}</td>'
            f'<td>{_h(t.get("user_query", "")[:60])}</td>'
            f'<td><span class="pill {kind_pill}">{_h(kind)}</span></td>'
            f'<td>{steps}</td>'
            f'<td class="muted">{_h(msg)}</td></tr>'
        )
    out.append(f'<table><thead><tr><th style="width:12%">Quando</th><th style="width:24%">Query</th><th style="width:12%">Esito</th><th style="width:6%">Steps</th><th>Risposta</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return "\n".join(out)


def _render_vaglio(decisions: list[dict]) -> str:
    out = ['<h2>Decisioni del Vaglio</h2>']
    if not decisions:
        out.append('<div class="empty">Nessuna decisione registrata.</div>')
        return "\n".join(out)
    rows = []
    for d in decisions:
        approved = d.get("approved")
        pill = "ok" if approved else "err"
        label = "approve" if approved else f'block:{d.get("blocked_by") or "?"}'
        score = d.get("score") or 0
        rows.append(
            f'<tr><td class="muted">{_h(d.get("ts", ""))}</td>'
            f'<td><code>{_h(d.get("executor", "?"))}</code></td>'
            f'<td>{score:.2f}</td>'
            f'<td><span class="pill {pill}">{_h(label)}</span></td>'
            f'<td class="muted">{_h(d.get("reason", "")[:120])}</td></tr>'
        )
    out.append(f'<table><thead><tr><th style="width:18%">Quando</th><th style="width:18%">Executor</th><th style="width:8%">Score</th><th style="width:14%">Decisione</th><th>Motivazione</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return "\n".join(out)


def _render_scheduler(tasks: list[dict]) -> str:
    out = ['<h2>Scheduler</h2>']
    if not tasks:
        out.append('<div class="empty">Scheduler v2 non attivo (nessun DB di stato). Riavvia <code>metnos-http.service</code>.</div>')
        return "\n".join(out)
    rows = []
    for t in tasks:
        status = t.get("last_status") or "(never run)"
        pill = {"ok": "ok", "error": "err"}.get(status, "muted")
        rows.append(
            f'<tr><td><code>{_h(t["name"])}</code></td>'
            f'<td><code>{_h(t["schedule"])}</code></td>'
            f'<td class="muted">{_h(t.get("last_run_at") or "(mai)")}</td>'
            f'<td><span class="pill {pill}">{_h(status)}</span></td></tr>'
        )
    out.append(f'<table><thead><tr><th>Task</th><th>Schedule</th><th>Ultima esecuzione</th><th>Esito</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return "\n".join(out)


def _render_tests(data: dict) -> str:
    if "error" in data:
        return f'<h2>Test framework</h2><div class="error">errore: {_h(data["error"])}</div>'
    cards = []
    cards.append(_render_metric_card("moduli", data["modules"]))
    cards.append(_render_metric_card("test cases", data["cases"]))
    for status, n in (data.get("by_status") or {}).items():
        cards.append(_render_metric_card(status, n))
    out = [f'<h2>Test framework</h2><div class="cards">{"".join(cards)}</div>']
    if data.get("top_modules"):
        out.append('<h3>Top moduli per casi</h3>')
        rows = []
        for r in data["top_modules"]:
            rows.append(f'<tr><td><code>{_h(r["name"])}</code></td><td>{r["n"]}</td></tr>')
        out.append(f'<table><thead><tr><th>Modulo</th><th>Cases</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return "\n".join(out)


def render_dashboard(out_path: Path | str = DEFAULT_OUT) -> Path:
    """Genera dashboard HTML statica. Ritorna il path scritto."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "mnestoma": _collect_mnestoma(),
        "pairings": _collect_pairings(),
        "turns": _collect_recent_turns(),
        "vaglio": _collect_vaglio_decisions(),
        "scheduler": _collect_scheduler(),
        "tests": _collect_tests(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }

    body = "\n".join([
        f'<h1>Metnos &mdash; dashboard</h1>',
        f'<div class="subtitle">Generata {data["generated_at"]} &middot; '
        f'rigenera con <code>python3 -m observability render</code></div>',
        _render_tests(data["tests"]),
        _render_mnestoma(data["mnestoma"]),
        _render_pairings(data["pairings"]),
        _render_turns(data["turns"]),
        _render_vaglio(data["vaglio"]),
        _render_scheduler(data["scheduler"]),
    ])

    html_doc = (
        f'<!DOCTYPE html>\n<html lang="it"><head><meta charset="UTF-8">'
        f'<title>Metnos dashboard</title>'
        f'<style>{_CSS}</style></head><body>{body}</body></html>'
    )
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


# --- CLI ------------------------------------------------------------------

def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Metnos observability dashboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_render = sub.add_parser("render", help="genera dashboard HTML")
    p_render.add_argument("--out", default=str(DEFAULT_OUT), help="file di output")
    args = ap.parse_args(argv)

    if args.cmd == "render":
        path = render_dashboard(args.out)
        print(f"dashboard generata: {path}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
