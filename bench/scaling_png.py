#!/usr/bin/env python3
"""scaling_png.py — render PNG di pubblicazione (Reddit + doc) dai risultati
dello scaling bench, via Pillow (no SVG rasterizer richiesto).

Heatmap accuratezza #azioni × #domini, stile pulito, leggibile a colpo d'occhio,
con titolo + sottotitolo esplicativi. Una immagine per fase (struttura / args).

Uso: python3 bench/scaling_png.py struct_iter5_final args_final
"""
import json, sys
from pathlib import Path
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont

_DIR = Path(__file__).resolve().parents[1] / "internal/reports/scaling_results"

NAVY = (26, 71, 122); SAGE = (84, 130, 53); BRONZE = (160, 82, 45)
INK = (26, 26, 26); MUTE = (107, 114, 128); LINE = (208, 215, 222)
BG = (255, 255, 255); GRID0 = (238, 241, 244)


def _font(sz, bold=False):
    cands = ([ "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
             ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for c in cands:
        try:
            return ImageFont.truetype(c, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def _heat(acc, n):
    if n == 0:
        return GRID0
    if acc >= 100:
        return SAGE
    if acc <= 0:
        return (176, 58, 46)
    if acc >= 50:
        t = (acc - 50) / 50.0
        return (int(214 + (84 - 214) * t), int(162 + (130 - 162) * t), int(58 + (53 - 58) * t))
    t = acc / 50.0
    return (int(176 + (214 - 176) * t), int(58 + (162 - 58) * t), int(46 + (58 - 46) * t))


def _cells(rows):
    c = defaultdict(lambda: [0, 0])
    for r in rows:
        c[(r["a"], r["d"])][1] += 1
        c[(r["a"], r["d"])][0] += int(r.get("ok"))
    return c


def _center(draw, x, y, text, font, fill):
    b = draw.textbbox((0, 0), text, font=font)
    draw.text((x - (b[2] - b[0]) / 2, y - (b[3] - b[1]) / 2), text, font=font, fill=fill)


def render(name, title, subtitle, caption):
    d = json.loads((_DIR / f"{name}.json").read_text())
    rows = d["rows"]
    cells = _cells(rows)
    amax = max(r["a"] for r in rows); dmax = max(r["d"] for r in rows)
    cw, chh = 78, 56
    ox, oy = 120, 130
    f_title = _font(30, True); f_sub = _font(16); f_ax = _font(16, True)
    f_lab = _font(15); f_cell = _font(18, True); f_small = _font(11); f_cap = _font(13)
    # larghezza = max(griglia, titolo, sottotitolo, didascalia) → niente clipping
    grid_w = ox + dmax * cw + 50
    probe = Image.new("RGB", (10, 10)); pdr = ImageDraw.Draw(probe)
    def _tw(txt, fnt):
        b = pdr.textbbox((0, 0), txt, font=fnt); return b[2] - b[0]
    text_w = max([_tw(title, f_title) + 80, _tw(subtitle, f_sub) + 80]
                 + [_tw(c, f_cap) + 80 for c in caption])
    W = max(grid_w, text_w)
    H = oy + (amax - 1) * chh + 140
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    dr.text((40, 30), title, font=f_title, fill=NAVY)
    dr.text((40, 72), subtitle, font=f_sub, fill=MUTE)

    # axis titles
    _center(dr, ox + dmax * cw / 2, oy - 34, "NUMERO DI DOMINI  (aree diverse: file, mail, foto, calendario, …)",
            f_ax, INK)
    # vertical (azioni) — draw rotated via temp image
    vt = Image.new("RGBA", (360, 24), (0, 0, 0, 0))
    vdr = ImageDraw.Draw(vt)
    vdr.text((0, 0), "NUMERO DI AZIONI  (passi nella richiesta)", font=f_ax, fill=INK)
    vt = vt.rotate(90, expand=True)
    img.paste(vt, (24, int(oy + (amax - 1) * chh / 2 - 180)), vt)

    for dd in range(1, dmax + 1):
        _center(dr, ox + (dd - 1) * cw + cw / 2, oy - 12, str(dd), f_lab, MUTE)
    for ai, a in enumerate(range(2, amax + 1)):
        y = oy + ai * chh
        _center(dr, ox - 18, y + chh / 2, str(a), f_lab, MUTE)
        for dd in range(1, dmax + 1):
            x = ox + (dd - 1) * cw
            ok, n = cells.get((a, dd), [0, 0])
            acc = 100 * ok / n if n else 0
            col = _heat(acc, n)
            dr.rectangle([x + 2, y + 2, x + cw - 3, y + chh - 3], fill=col)
            if n:
                tc = (255, 255, 255) if (acc >= 100 or acc < 42) else INK
                _center(dr, x + cw / 2, y + chh / 2 - 7, f"{int(acc)}%", f_cell, tc)
                _center(dr, x + cw / 2, y + chh / 2 + 12, f"{ok}/{n}", f_small, tc)

    # legend swatches
    ly = oy + (amax - 1) * chh + 28
    for i, (c, lab) in enumerate([(SAGE, "tutto corretto"),
                                  ((214, 162, 58), "parziale"),
                                  ((176, 58, 46), "fallito"),
                                  (GRID0, "non applicabile")]):
        lx = 40 + i * 175
        dr.rectangle([lx, ly, lx + 22, ly + 22], fill=c, outline=LINE)
        dr.text((lx + 30, ly + 3), lab, font=f_cap, fill=INK)
    # caption (wrapped)
    cy = ly + 44
    for line in caption:
        dr.text((40, cy), line, font=f_cap, fill=MUTE)
        cy += 19

    out = _DIR / f"{name}.png"
    img.save(out, "PNG")
    n = len(rows); ok = sum(int(r.get("ok")) for r in rows)
    print(f"{name}.png  ({W}x{H})  {ok}/{n} = {100*ok/n:.1f}%")
    return out


def render_marginals(name, title):
    """Due pannelli a barre affiancati: accuratezza per #azioni e per #domini —
    mostra a colpo d'occhio che il limite e' il #domini, non il #azioni."""
    d = json.loads((_DIR / f"{name}.json").read_text())
    rows = d["rows"]
    def agg(by):
        a = defaultdict(lambda: [0, 0])
        for r in rows:
            a[r[by]][1] += 1; a[r[by]][0] += int(r.get("ok"))
        return a
    panels = [("a", "per NUMERO DI AZIONI", agg("a")),
              ("d", "per NUMERO DI DOMINI", agg("d"))]
    pw, ph = 470, 300; gap = 30
    f_title = _font(26, True); f_pan = _font(16, True); f_lab = _font(14)
    f_val = _font(13, True); f_cap = _font(13)
    probe = Image.new("RGB", (10, 10)); pdr = ImageDraw.Draw(probe)
    tb = pdr.textbbox((0, 0), title, font=f_title)
    W = max(pw * 2 + gap + 60, (tb[2] - tb[0]) + 80); H = ph + 90
    img = Image.new("RGB", (W, H), BG); dr = ImageDraw.Draw(img)
    dr.text((40, 26), title, font=f_title, fill=NAVY)
    for pi, (by, ptitle, a) in enumerate(panels):
        px = 40 + pi * (pw + gap); py = 80
        _center_at = px + pw / 2
        b = dr.textbbox((0, 0), ptitle, font=f_pan)
        dr.text((_center_at - (b[2] - b[0]) / 2, py - 26), ptitle, font=f_pan, fill=INK)
        keys = sorted(a); plot_h = ph - 60; base = py + plot_h
        for gl in (0, 25, 50, 75, 100):
            gy = base - plot_h * gl / 100
            dr.line([(px + 36, gy), (px + pw - 16, gy)], fill=LINE)
            dr.text((px + 8, gy - 7), str(gl), font=_font(11), fill=MUTE)
        bw = (pw - 60) / max(1, len(keys))
        for i, k in enumerate(keys):
            ok, nn = a[k]; acc = 100 * ok / nn if nn else 0
            x = px + 40 + i * bw
            bh = plot_h * acc / 100
            col = SAGE if acc >= 95 else ((176, 58, 46) if acc < 75 else (214, 162, 58))
            dr.rectangle([x, base - bh, x + bw - 10, base], fill=col)
            _center(dr, x + (bw - 10) / 2, base - bh - 10, f"{int(acc)}%", f_val, INK)
            _center(dr, x + (bw - 10) / 2, base + 14, str(k), f_lab, MUTE)
    dr.text((40, H - 28),
            "Le azioni (profondita') scalano; i domini (ampiezza) sono il vero limite.",
            font=f_cap, fill=MUTE)
    out = _DIR / f"{name}_marginals.png"
    img.save(out, "PNG")
    print(f"{name}_marginals.png  ({W}x{H})")
    return out


SPECS = {
    "struct_iter5_final": (
        "Engine compound — robustezza STRUTTURALE",
        "Compone la pipeline corretta? (i passi giusti, nell'ordine giusto) — 117 richieste multi-dominio",
        ["Ogni cella = una difficolta' (N azioni combinate su M aree diverse), 4 richieste a caso.",
         "Verde = l'engine ha scelto tutti i tool giusti nell'ordine giusto. Fino a 6 azioni x 5 aree: 100%."]),
    "args_final": (
        "Engine compound — robustezza ARGOMENTI",
        "Riempie i parametri giusti? (date, percorsi, destinatari, filtri) — stesse 117 richieste",
        ["Seconda fase: data la struttura corretta, ogni passo riceve i suoi argomenti dedotti dal testo.",
         "Verde = tutti gli argomenti deducibili sono corretti (es. 'foto di ieri' -> data di ieri)."]),
}


def main():
    names = sys.argv[1:] or list(SPECS)
    for name in names:
        if not (_DIR / f"{name}.json").exists():
            print(f"skip: {name}.json manca"); continue
        t, s, cap = SPECS.get(name, (f"Scaling — {name}", "", []))
        render(name, t, s, cap)
        render_marginals(name, t + " — effetto di azioni vs domini")


if __name__ == "__main__":
    main()
