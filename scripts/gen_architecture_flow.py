#!/usr/bin/env python3
"""Generate the public request-flow diagram used by README.md."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "docs/assets/architecture-flow.png"

W, H = 1000, 650
BG = (250, 251, 252)
WHITE = (255, 255, 255)
INK = (24, 50, 74)
MUTED = (93, 109, 125)
LINE = (113, 132, 150)
BLUE = (26, 71, 122)
BLUE_LIGHT = (220, 236, 247)
GREEN = (65, 107, 75)
GREEN_LIGHT = (228, 241, 231)
AMBER = (154, 100, 24)
AMBER_LIGHT = (255, 243, 215)
RED = (151, 62, 50)
RED_LIGHT = (250, 231, 227)

_DEJA = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_DEJA.format("-Bold" if bold else ""), size)


def _box(draw: ImageDraw.ImageDraw, bounds, fill, outline, title, detail,
         title_color=INK) -> None:
    draw.rounded_rectangle(bounds, radius=13, fill=fill, outline=outline, width=2)
    x0, y0, x1, y1 = bounds
    cx = (x0 + x1) // 2
    draw.text((cx, y0 + 20), title, font=_font(14, True), fill=title_color,
              anchor="mm")
    draw.text((cx, y0 + 43), detail, font=_font(11), fill=MUTED,
              anchor="mm")


def _arrow(draw: ImageDraw.ImageDraw, points, color=LINE, width=2) -> None:
    draw.line(points, fill=color, width=width, joint="curve")
    x0, y0 = points[-2]
    x1, y1 = points[-1]
    if abs(x1 - x0) >= abs(y1 - y0):
        direction = 1 if x1 > x0 else -1
        head = [(x1, y1), (x1 - 8 * direction, y1 - 5),
                (x1 - 8 * direction, y1 + 5)]
    else:
        direction = 1 if y1 > y0 else -1
        head = [(x1, y1), (x1 - 5, y1 - 8 * direction),
                (x1 + 5, y1 - 8 * direction)]
    draw.polygon(head, fill=color)


def _label(draw: ImageDraw.ImageDraw, xy, text, color=MUTED) -> None:
    draw.text(xy, text, font=_font(10), fill=color, anchor="mm")


def main() -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.text((500, 24), "How a Metnos request is executed",
              font=_font(19, True), fill=INK, anchor="mm")

    _box(draw, (350, 48, 650, 105), WHITE, INK,
         "Natural-language request", "channel, user, and mandate")
    _arrow(draw, [(500, 105), (500, 130)])
    _box(draw, (320, 130, 680, 190), BLUE_LIGHT, BLUE,
         "Intent and routing pool", "canonical verb/object + relevant executors",
         BLUE)

    _arrow(draw, [(500, 190), (205, 190), (205, 220)])
    _box(draw, (70, 220, 340, 280), AMBER_LIGHT, AMBER,
         "L0 · Fastpath", "same request, validated cached plan", AMBER)
    _arrow(draw, [(205, 280), (205, 315)])
    _label(draw, (226, 298), "miss")
    _box(draw, (70, 315, 340, 375), GREEN_LIGHT, GREEN,
         "L1 · Autopath", "learned plan for a request family", GREEN)

    _arrow(draw, [(340, 250), (390, 250), (390, 430)])
    _label(draw, (365, 238), "hit")
    _arrow(draw, [(340, 355), (410, 355), (410, 430)])
    _label(draw, (376, 367), "hit")

    _arrow(draw, [(340, 330), (365, 330), (365, 205),
                  (535, 205), (535, 220)])
    _label(draw, (352, 316), "miss")
    _box(draw, (400, 220, 670, 280), BLUE_LIGHT, BLUE,
         "Proposer", "local LLM drafts a typed plan", BLUE)
    _arrow(draw, [(535, 280), (535, 315)])
    _box(draw, (400, 315, 670, 375), WHITE, BLUE,
         "Guards + Validator", "schema, authority, consistency, consent", BLUE)
    _arrow(draw, [(535, 375), (535, 410)])

    _box(draw, (330, 410, 700, 485), GREEN_LIGHT, GREEN,
         "Executor boundary", "direct or intelligent · same contract · verified outcome",
         GREEN)
    _arrow(draw, [(515, 485), (515, 545)])
    _label(draw, (548, 510), "success")

    _arrow(draw, [(700, 448), (770, 448)])
    _label(draw, (735, 436), "failure", RED)
    _box(draw, (770, 410, 955, 475), RED_LIGHT, RED,
         "Recovery", "classify and retry safely", RED)
    _arrow(draw, [(862, 410), (862, 250), (670, 250)], RED)
    _label(draw, (823, 238), "recoverable", RED)
    _arrow(draw, [(862, 475), (862, 515)], RED)
    _box(draw, (770, 515, 955, 580), WHITE, RED,
         "Terminator", "state the limit honestly", RED)
    _arrow(draw, [(770, 548), (700, 548)], RED)

    _box(draw, (330, 545, 700, 615), WHITE, GREEN,
         "Answer to the user", "result, evidence, partial status, or explicit failure",
         GREEN)

    draw.text((70, 620),
              "Transport, provider, and internal intelligence stay behind the executor contract.",
              font=_font(10), fill=MUTED)

    image.save(OUT)
    print(f"wrote {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
