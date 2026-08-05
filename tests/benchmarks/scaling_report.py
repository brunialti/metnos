#!/usr/bin/env python3
"""scaling_report.py — genera SVG di pubblicazione dai risultati dello scaling
bench, per la documentazione architettura (robustezza engine v2).

Input: i JSON archiviati (internal/reports/scaling_results/*.json).
Output: SVG inline (stile-casa docs: navy/sage/bronze) — heatmap accuracy a×d +
barre effetto-marginale per #azioni e #domini. Self-contained, no JS/CDN.

Uso: python3 tests/benchmarks/scaling_report.py struct_iter5_final args_iter103_final
     → scrive internal/reports/scaling_results/<name>.svg + un fragment HTML
       riassuntivo da incollare nei doc.
"""
import json, sys
from pathlib import Path
from collections import defaultdict

_DIR = Path(__file__).resolve().parents[2] / "internal/reports/scaling_results"

# Palette stile-casa docs architettura (docs/it/architecture/*.html :root).
NAVY = "#1A477A"; SAGE = "#548235"; BRONZE = "#A0522D"; TEXT = "#1a1a1a"
BORDER = "#d0d7de"; MUTED = "#6b7280"


def _heat(acc, n):
    """Verde(sage)→giallo→rosso(bronze), pieno agli estremi (coerenza num↔colore)."""
    if n == 0:
        return "#eef1f4"
    if acc >= 100:
        return SAGE
    if acc <= 0:
        return "#b03a2e"
    # interp giallo→verde sopra 50, rosso→giallo sotto
    if acc >= 50:
        t = (acc - 50) / 50.0
        r = int(0xD6 + (0x54 - 0xD6) * t); g = int(0xA2 + (0x82 - 0xA2) * t); b = int(0x3A + (0x35 - 0x3A) * t)
    else:
        t = acc / 50.0
        r = int(0xB0 + (0xD6 - 0xB0) * t); g = int(0x3A + (0xA2 - 0x3A) * t); b = int(0x2E + (0x3A - 0x2E) * t)
    return f"rgb({r},{g},{b})"


def _cells(rows):
    c = defaultdict(lambda: [0, 0])
    for r in rows:
        c[(r["a"], r["d"])][1] += 1
        c[(r["a"], r["d"])][0] += int(r.get("ok"))
    return c


def heatmap_svg(rows, title, subtitle):
    cells = _cells(rows)
    amax = max(r["a"] for r in rows); dmax = max(r["d"] for r in rows)
    cw, ch, ox, oy = 52, 38, 70, 64
    W = ox + dmax * cw + 30
    H = oy + (amax - 1) * ch + 60
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">']
    p.append(f'<text x="20" y="26" font-size="16" font-weight="700" fill="{NAVY}">{title}</text>')
    p.append(f'<text x="20" y="46" font-size="12" fill="{MUTED}">{subtitle}</text>')
    # assi
    p.append(f'<text x="{ox+dmax*cw/2}" y="{oy-26}" font-size="12" font-weight="600" '
             f'fill="{TEXT}" text-anchor="middle">numero di DOMINI →</text>')
    p.append(f'<text x="22" y="{oy+(amax-1)*ch/2}" font-size="12" font-weight="600" '
             f'fill="{TEXT}" text-anchor="middle" transform="rotate(-90 22 {oy+(amax-1)*ch/2})">numero di AZIONI →</text>')
    for d in range(1, dmax + 1):
        p.append(f'<text x="{ox+(d-1)*cw+cw/2}" y="{oy-8}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{d}</text>')
    for ai, a in enumerate(range(2, amax + 1)):
        y = oy + ai * ch
        p.append(f'<text x="{ox-12}" y="{y+ch/2+4}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="end">{a}</text>')
        for d in range(1, dmax + 1):
            x = ox + (d - 1) * cw
            ok, n = cells.get((a, d), [0, 0])
            acc = 100 * ok / n if n else 0
            fill = _heat(acc, n)
            p.append(f'<rect x="{x}" y="{y}" width="{cw-3}" height="{ch-3}" rx="3" '
                     f'fill="{fill}" stroke="#fff" stroke-width="1"/>')
            if n:
                tcol = "#fff" if (acc >= 100 or acc < 40) else "#1a1a1a"
                p.append(f'<text x="{x+(cw-3)/2}" y="{y+ch/2-1}" font-size="11" '
                         f'font-weight="700" fill="{tcol}" text-anchor="middle">{int(acc)}%</text>')
                p.append(f'<text x="{x+(cw-3)/2}" y="{y+ch/2+11}" font-size="8" '
                         f'fill="{tcol}" text-anchor="middle" opacity="0.85">{ok}/{n}</text>')
    # legenda
    ly = oy + (amax - 1) * ch + 22
    p.append(f'<text x="20" y="{ly}" font-size="11" fill="{MUTED}">Copertura per cella '
             f'(verde=tutte le query corrette · rosso=fallite · grigio=non applicabile)</text>')
    p.append('</svg>')
    return "\n".join(p)


def marginal_svg(rows, by, title):
    """Barre: accuracy media per valore di #azioni (by='a') o #domini (by='d')."""
    agg = defaultdict(lambda: [0, 0])
    for r in rows:
        agg[r[by]][1] += 1
        agg[r[by]][0] += int(r.get("ok"))
    keys = sorted(agg)
    W, H, ox, oy, bw = 460, 240, 56, 40, 44
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">']
    p.append(f'<text x="20" y="24" font-size="14" font-weight="700" fill="{NAVY}">{title}</text>')
    plot_h = H - oy - 40
    for gl in (0, 25, 50, 75, 100):
        gy = oy + plot_h * (1 - gl / 100)
        p.append(f'<line x1="{ox}" y1="{gy}" x2="{W-20}" y2="{gy}" stroke="{BORDER}"/>')
        p.append(f'<text x="{ox-6}" y="{gy+3}" font-size="9" fill="{MUTED}" text-anchor="end">{gl}</text>')
    for i, k in enumerate(keys):
        ok, n = agg[k]
        acc = 100 * ok / n if n else 0
        x = ox + 14 + i * bw
        bh = plot_h * acc / 100
        col = SAGE if acc >= 95 else (BRONZE if acc < 75 else "#d6a23a")
        p.append(f'<rect x="{x}" y="{oy+plot_h-bh}" width="{bw-12}" height="{bh}" rx="2" fill="{col}"/>')
        p.append(f'<text x="{x+(bw-12)/2}" y="{oy+plot_h-bh-4}" font-size="10" font-weight="600" '
                 f'fill="{TEXT}" text-anchor="middle">{int(acc)}%</text>')
        p.append(f'<text x="{x+(bw-12)/2}" y="{oy+plot_h+14}" font-size="10" fill="{MUTED}" '
                 f'text-anchor="middle">{k}</text>')
    lab = "numero di AZIONI" if by == "a" else "numero di DOMINI"
    p.append(f'<text x="{ox+(len(keys)*bw)/2}" y="{H-8}" font-size="11" font-weight="600" '
             f'fill="{TEXT}" text-anchor="middle">{lab}</text>')
    p.append('</svg>')
    return "\n".join(p)


def summary(rows):
    n = len(rows); ok = sum(int(r.get("ok")) for r in rows)
    cells = _cells(rows)
    green = sum(1 for v in cells.values() if v[0] == v[1])
    return {"n": n, "ok": ok, "pct": round(100 * ok / max(1, n), 1),
            "cells": len(cells), "green_cells": green,
            "amax": max(r["a"] for r in rows), "dmax": max(r["d"] for r in rows)}


def main():
    names = sys.argv[1:] or ["struct_iter5_final", "args_iter103_final"]
    for name in names:
        path = _DIR / f"{name}.json"
        if not path.exists():
            print(f"skip (manca): {name}.json"); continue
        d = json.loads(path.read_text())
        rows = d.get("rows", [])
        phase = "ARGS (riempimento argomenti)" if "args" in name else "STRUTTURA (copertura+ordine clausole)"
        s = summary(rows)
        title = f"Robustezza engine — fase {phase.split()[0]}"
        sub = (f"{s['ok']}/{s['n']} query corrette ({s['pct']}%) · {s['green_cells']}/{s['cells']} "
               f"celle (a,d) tutte verdi · griglia fino a {s['amax']} azioni × {s['dmax']} domini")
        hm = heatmap_svg(rows, title, sub)
        ma = marginal_svg(rows, "a", "Accuratezza per numero di AZIONI (media sui domini)")
        md = marginal_svg(rows, "d", "Accuratezza per numero di DOMINI (media sulle azioni)")
        (_DIR / f"{name}_heatmap.svg").write_text(hm)
        (_DIR / f"{name}_marg_actions.svg").write_text(ma)
        (_DIR / f"{name}_marg_domains.svg").write_text(md)
        print(f"{name}: {s['pct']}%  → 3 SVG scritti  ({phase})")


if __name__ == "__main__":
    main()
