#!/usr/bin/env python3
"""Genera docs/assets/architecture-flow.png (diagramma flusso richiesta,
referenziato dal README pubblico via https://metnos.com/assets/).

SORGENTE del diagramma (2/7/2026): il PNG originale era senza sorgente —
rigenerato qui in PIL per (a) fissare il glossario §11 («learned plan», non
«learned skill»: skill = bundle di executor, MAI il piano L1) e (b) rendere
il diagramma editabile in futuro. Palette campionata dall'originale.

Uso: python3 scripts/gen_architecture_flow.py   (scrive il PNG e basta)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "docs/assets/architecture-flow.png"

W, H = 870, 626
BG = (255, 255, 255)
SLATE = (51, 65, 85)          # frecce + bordi neutri
INK = (15, 23, 42)            # bordo box bianchi
GREEN_EDGE = (36, 130, 67)    # ritorni hit → answer
L0 = (180, 83, 9)
L1 = (21, 128, 61)
L2 = (107, 122, 46)
L3 = (30, 64, 175)
MUTED = (148, 163, 184)

_DEJA = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_DEJA.format("-Bold" if bold else ""), size)


def _rounded(d: ImageDraw.ImageDraw, box, fill=None, outline=None, width=2,
             radius=10):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                        width=width)


def _ctext(d: ImageDraw.ImageDraw, cx, cy, text, font, fill):
    d.text((cx, cy), text, font=font, fill=fill, anchor="mm")


def _arrow_down(d: ImageDraw.ImageDraw, x, y0, y1, color=SLATE, width=2):
    d.line([(x, y0), (x, y1 - 7)], fill=color, width=width)
    d.polygon([(x - 5, y1 - 8), (x + 5, y1 - 8), (x, y1)], fill=color)


def _arrow_right(d: ImageDraw.ImageDraw, y, x0, x1, color=GREEN_EDGE, width=2):
    d.line([(x0, y), (x1 - 7, y)], fill=color, width=width)
    d.polygon([(x1 - 8, y - 5), (x1 - 8, y + 5), (x1, y)], fill=color)


def _arrow_left(d: ImageDraw.ImageDraw, y, x0, x1, color=GREEN_EDGE, width=2):
    d.line([(x0, y), (x1 + 7, y)], fill=color, width=width)
    d.polygon([(x1 + 8, y - 5), (x1 + 8, y + 5), (x1, y)], fill=color)


def main() -> None:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    f11 = _font(11)
    f11i = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 11)
    f12 = _font(12)
    f12b = _font(12, bold=True)
    f13b = _font(13, bold=True)

    CX = 396                      # asse verticale del flusso
    RX = 700                      # binario dei ritorni hit → answer

    # ── User request ──
    _rounded(d, (260, 27, 532, 59), outline=INK, width=2)
    _ctext(d, CX, 43, "User request", f12b, INK)
    _arrow_down(d, CX, 59, 79)

    # ── intent_extractor ──
    _rounded(d, (260, 79, 532, 111), outline=INK, width=2)
    _ctext(d, CX, 95, "intent_extractor · verb + object", f12, INK)
    _arrow_down(d, CX, 111, 131)

    # ── L0 ──
    _rounded(d, (260, 131, 532, 181), fill=L0)
    _ctext(d, CX, 147, "L0 · Fastpath", f13b, BG)
    _ctext(d, CX, 166, "self-learned shortcut (hash + cosine)", f11, BG)
    _ctext(d, CX + 15, 190, "miss", f11i, MUTED)
    _arrow_down(d, CX, 181, 203)

    # ── L1 (§11: autopath = learned PLAN; «skill» = bundle, mai il piano) ──
    _rounded(d, (260, 203, 532, 253), fill=L1)
    _ctext(d, CX, 219, "L1 · Autopath", f13b, BG)
    _ctext(d, CX, 238, "learned plan (semantic + intent match)", f11, BG)
    _ctext(d, CX + 15, 262, "miss", f11i, MUTED)
    _arrow_down(d, CX, 253, 275)

    # ── L2 ──
    _rounded(d, (260, 275, 532, 318), fill=L2)
    _ctext(d, CX, 289, "L2 · Validator", f13b, BG)
    _ctext(d, CX, 306, "plan check", f11, BG)
    _arrow_down(d, CX, 318, 340)

    # ── L3 Engine ──
    _rounded(d, (172, 340, 668, 510), fill=L3)
    _ctext(d, 420, 361, "L3 · Engine", f13b, BG)
    inner = [
        ("Proposer", "1 LLM call", 184),
        ("Executor", "deterministic", 307),
        ("Recovery", "4 error classes", 430),
        ("Terminator", "honest limit", 553),
    ]
    for title, sub, x0 in inner:
        _rounded(d, (x0, 383, x0 + 108, 428), fill=BG, radius=6)
        _ctext(d, x0 + 54, 399, title, f12b, L3)
        _ctext(d, x0 + 54, 415, sub, f11, SLATE)
    for i in range(3):
        x = 292 + i * 123
        d.line([(x + 2, 405), (x + 21, 405)], fill=BG, width=2)
        d.polygon([(x + 15, 401), (x + 15, 409), (x + 21, 405)], fill=BG)
    _ctext(d, 420, 452, "propose · execute · recover · admit the limit",
           f11, BG)
    _ctext(d, 420, 475, "selector: simple | metis | frontier", f11, BG)
    _arrow_down(d, CX, 510, 550)

    # ── Answer ──
    _rounded(d, (260, 550, 532, 592), outline=GREEN_EDGE, width=2)
    _ctext(d, CX, 571, "Answer to the user", f12b, INK)

    # ── Ritorni hit → answer (binario destro) ──
    _ctext(d, 640, 142, "hit → answer", f11, GREEN_EDGE)
    _arrow_right(d, 156, 532, RX - 1)          # da L0
    _arrow_right(d, 228, 532, RX - 1)          # da L1
    _arrow_right(d, 425, 668, RX - 1)          # da L3
    d.line([(RX, 156), (RX, 571)], fill=GREEN_EDGE, width=2)
    _arrow_left(d, 571, RX, 532)               # verso Answer

    im.save(OUT)
    print(f"scritto {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
