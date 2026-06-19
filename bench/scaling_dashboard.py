#!/usr/bin/env python3
"""scaling_dashboard.py — dashboard HTTP del scaling bench (Roberto 18/6).

5 grafici, auto-refresh ~60s, legge SCALING_LIVE (scrittura incrementale del
bench). Render server-side (SVG + tabella), self-contained: niente JS/CDN.

Grafici: (1) heatmap accuracy a×d; (2) %errori/n-domini; (3) %errori/n-azioni;
(4) %fuori-ordine/n-domini; (5) %fuori-ordine/n-azioni.

Run: SCALING_LIVE=/tmp/scaling_live.json SCALING_PORT=8901 python3 bench/scaling_dashboard.py
"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import defaultdict

LIVE = os.environ.get("SCALING_LIVE", "/tmp/scaling_live.json")
PORT = int(os.environ.get("SCALING_PORT", "8901"))


def load():
    try:
        with open(LIVE) as f:
            return json.load(f)
    except Exception:
        return {"engine": "?", "iter": 0, "done": 0, "total": 0, "ts": "-", "rows": []}


def classify(r):
    # AUTORITATIVO: l'esito e' quello calcolato dal bench (coerente con la fase
    # struct/args) — MAI ricalcolato dai campi strutturali (era la causa del
    # «100% ma non verde»: la dashboard rivalutava in modo diverso dal bench).
    o = r.get("outcome")
    if o:
        return o
    # Compat dati vecchi (senza 'outcome'): rispetta comunque r['ok'] come
    # verita' del bench; la sotto-classe e' solo decorativa.
    if r.get("ok"):
        return "ok"
    if r.get("flaky"):
        return "flaky"
    if r.get("anyo", 0) < r.get("gold_hard", 0):
        return "error"
    return "reorder"


def heat_color(acc, n):
    # Coerenza numero↔colore: 4/4 (acc=100) e' SEMPRE verde pieno; 0/4 rosso
    # pieno. Nessun caso «100% ma non verde».
    if n == 0:
        return "#1b2430", "#5a6b7a"        # da-fare
    if acc >= 100:
        return "rgb(34,160,75)", "#fff"    # tutto ok → verde pieno
    if acc <= 0:
        return "rgb(200,55,55)", "#fff"    # tutto fallito → rosso pieno
    r = int(40 + 200 * (1 - acc / 100))
    g = int(40 + 170 * (acc / 100))
    return f"rgb({r},{g},55)", "#fff"


def svg_bars(title, labels, values, color):
    W, H, pad = 380, 200, 34
    n = max(1, len(labels))
    bw = (W - 2 * pad) / n
    parts = [f'<text x="{W/2:.0f}" y="16" font-size="13" fill="#cfe" '
             f'text-anchor="middle">{title}</text>']
    for gl in (0, 25, 50, 75, 100):  # gridlines
        y = H - pad - (H - 2 * pad - 14) * gl / 100
        parts.append(f'<line x1="{pad}" y1="{y:.0f}" x2="{W-pad}" y2="{y:.0f}" '
                     f'stroke="#2a3340"/>')
        parts.append(f'<text x="{pad-4}" y="{y+3:.0f}" font-size="9" fill="#678" '
                     f'text-anchor="end">{gl}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = pad + i * bw + 5
        h = (H - 2 * pad - 14) * (v / 100.0)
        y = H - pad - h
        parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw-10:.0f}" '
                     f'height="{h:.0f}" fill="{color}" rx="2"/>')
        parts.append(f'<text x="{x+(bw-10)/2:.0f}" y="{H-pad+14:.0f}" font-size="11" '
                     f'fill="#9ab" text-anchor="middle">{lab}</text>')
        if v > 0:
            parts.append(f'<text x="{x+(bw-10)/2:.0f}" y="{y-3:.0f}" font-size="10" '
                         f'fill="#cfe" text-anchor="middle">{v:.0f}</text>')
    return (f'<div class="card"><svg width="{W}" height="{H}">'
            + "".join(parts) + "</svg></div>")


def render():
    d = load()
    rows = d.get("rows", [])
    # heatmap accuracy + count per (a,d)
    cells = defaultdict(lambda: [0, 0])
    by_d_err = defaultdict(lambda: [0, 0])   # [n_err, n_done]
    by_a_err = defaultdict(lambda: [0, 0])
    by_d_reo = defaultdict(lambda: [0, 0])
    by_a_reo = defaultdict(lambda: [0, 0])
    for r in rows:
        c = classify(r)
        cells[(r["a"], r["d"])][1] += 1
        cells[(r["a"], r["d"])][0] += int(c == "ok")
        for tab, key in ((by_d_err, r["d"]), (by_a_err, r["a"])):
            tab[key][1] += 1
            tab[key][0] += int(c == "error")
        for tab, key in ((by_d_reo, r["d"]), (by_a_reo, r["a"])):
            tab[key][1] += 1
            tab[key][0] += int(c == "reorder")
    amax = max([r["a"] for r in rows] + [8])
    dmax = max([r["d"] for r in rows] + [7])

    # heatmap table. Una cella e' "IN CORSO" (grigio, niente esito) SOLO finche'
    # il grid non l'ha completata. Il segnale robusto: l'ULTIMA cella scritta e'
    # in corso (il grid procede in ordine a→d); tutte le precedenti sono finite,
    # qualunque sia il loro n reale (alcune (a,d) generano <per_cell query
    # distinte: 2/3 e' un ESITO legittimo, non un parziale). Cosi' niente
    # parziale travestito da regressione, e niente falso "in corso" sulle celle
    # con poche query.
    rows_seen = d.get("rows", [])
    last_cell = (rows_seen[-1]["a"], rows_seen[-1]["d"]) if rows_seen else None
    running = d.get("done", 0) < d.get("total", 0)
    th = "".join(f"<th>d{x}</th>" for x in range(1, dmax + 1))
    trs = []
    for a in range(2, amax + 1):
        tds = [f"<th>a{a}</th>"]
        for dd in range(1, dmax + 1):
            ok, n = cells.get((a, dd), [0, 0])
            in_progress = running and last_cell == (a, dd)
            if in_progress:
                tds.append(f'<td style="background:#1b2430;color:#7a8aa0">…'
                           f'<br><span class="n">{ok}/{n}</span></td>')
                continue
            acc = 100 * ok / n if n else 0
            bg, fg = heat_color(acc, n)
            txt = f"{int(acc)}%" if n else "·"
            tds.append(f'<td style="background:{bg};color:{fg}">{txt}'
                       f'<br><span class="n">{ok}/{n}</span></td>' if n
                       else f'<td style="background:{bg};color:{fg}">·</td>')
        trs.append("<tr>" + "".join(tds) + "</tr>")
    heatmap = (f'<div class="card"><h3>Heatmap accuracy — righe #azioni, colonne '
               f'#domini</h3><table class="hm"><tr><th></th>{th}</tr>'
               + "".join(trs) + "</table></div>")

    def pct(tab, keys):
        return [round(100 * tab[k][0] / tab[k][1], 1) if tab[k][1] else 0 for k in keys]

    dks = list(range(1, dmax + 1))
    aks = list(range(2, amax + 1))
    c2 = svg_bars("% ERRORI per #DOMINI", [f"d{x}" for x in dks], pct(by_d_err, dks), "#e0556a")
    c3 = svg_bars("% ERRORI per #AZIONI", [f"a{x}" for x in aks], pct(by_a_err, aks), "#e0556a")
    c4 = svg_bars("% FUORI-ORDINE per #DOMINI", [f"d{x}" for x in dks], pct(by_d_reo, dks), "#d6a23a")
    c5 = svg_bars("% FUORI-ORDINE per #AZIONI", [f"a{x}" for x in aks], pct(by_a_reo, aks), "#d6a23a")

    n_err = sum(1 for r in rows if classify(r) == "error")
    n_reo = sum(1 for r in rows if classify(r) == "reorder")
    n_ok = sum(1 for r in rows if classify(r) == "ok")
    head = (f'<div class="hdr"><b>SCALING — engine {d.get("engine")} · iter '
            f'{d.get("iter")}</b> · {d.get("done")}/{d.get("total")} query · '
            f'<span style="color:#6e6">ok {n_ok}</span> · '
            f'<span style="color:#e66">err {n_err}</span> · '
            f'<span style="color:#ec3">reorder {n_reo}</span> · agg {d.get("ts")}</div>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30"><title>Scaling dashboard</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font:13px/1.4 system-ui,sans-serif;margin:0;padding:14px}}
.hdr{{font-size:15px;margin-bottom:12px}}
.grid{{display:flex;flex-wrap:wrap;gap:14px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px}}
.card h3{{margin:0 0 6px;font-size:12px;color:#8b98a5;font-weight:600}}
table.hm{{border-collapse:collapse}}
table.hm td,table.hm th{{width:60px;height:42px;text-align:center;font-size:12px;
  border:1px solid #0d1117}}
table.hm .n{{font-size:9px;opacity:.7}}
</style></head><body>{head}<div class="grid">{heatmap}{c2}{c3}{c4}{c5}</div></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/dashboard"):
            body = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/data":
            body = json.dumps(load()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"scaling dashboard on http://0.0.0.0:{PORT}/  (live={LIVE})")
    srv.serve_forever()
