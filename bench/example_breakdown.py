#!/usr/bin/env python3
"""example_breakdown.py — PNG di una query complessa scomposta in executor,
colorati per DOMINIO. Per il post (mostra quanti domini+azioni in una frase).

Dati statici (un esempio REALE catturato, non inventato): query → step con
dominio. Render Pillow, stile-casa.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

_DIR = Path(__file__).resolve().parents[1] / "internal/reports/scaling_results"

# Palette per dominio (distinti, leggibili).
DOM_COL = {
    "files":   (84, 130, 53),    # verde
    "mail":    (43, 108, 176),   # blu
    "store":   (160, 82, 45),    # bronzo
    "calendar":(107, 33, 168),   # viola
    "photo":   (193, 122, 38),   # ambra
    "web":     (32, 128, 141),   # teal
}
INK=(26,26,26); MUTE=(110,118,128); BG=(255,255,255); CARD=(247,249,251)

# Esempio: 1 frase, 6 domini, 8 azioni (struttura reale prodotta dall'engine).
QUERY = ("« Trova i file di log piu' vecchi di una settimana e comprimili, "
         "controlla la posta non letta di oggi, cerca tra le spese quelle sopra "
         "i 100 euro, guarda che impegni ho domani, trova le foto di ieri e "
         "cerca online le novita' »")
STEPS = [
    ("find_files",          "files",    "trova i log"),
    ("compress_files",      "files",    "li comprime in zip"),
    ("read_messages",       "mail",     "posta non letta · oggi"),
    ("find_entries",        "store",    "spese > 100€"),
    ("find_events",         "calendar", "impegni · domani"),
    ("find_images_indices", "photo",    "foto · ieri"),
    ("find_urls",           "web",      "ricerca online"),
]
DOM_LABEL = {"files":"FILE","mail":"MAIL","store":"DATABASE","calendar":"CALENDARIO","photo":"FOTO","web":"WEB"}


def _font(sz, bold=False):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")
    try: return ImageFont.truetype(p, sz)
    except Exception: return ImageFont.load_default()


def main(lang="it"):
    W = 980
    pad = 36
    row_h = 56
    H = 250 + len(STEPS)*row_h + 70
    img = Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(img)
    f_t=_font(26,True); f_q=_font(17); f_s=_font(18,True); f_d=_font(12,True); f_n=_font(14)
    title = "Una frase → 6 domini, 7 esecutori" if lang=="it" else "One sentence → 6 domains, 7 executors"
    d.text((pad,28), title, font=f_t, fill=(26,71,122))
    # query box
    d.rectangle([pad,76,W-pad,76+96], fill=CARD, outline=(208,215,222))
    # wrap query
    import textwrap
    qx,qy=pad+14,90
    for line in textwrap.wrap(QUERY, 92):
        d.text((qx,qy), line, font=f_q, fill=INK); qy+=24
    # steps
    y0=200
    d.text((pad, y0-26), "L'engine la scompone in:" if lang=="it" else "The engine breaks it into:", font=f_n, fill=MUTE)
    for i,(tool,dom,desc) in enumerate(STEPS):
        y=y0+i*row_h
        col=DOM_COL[dom]
        # numero
        d.ellipse([pad,y+8,pad+30,y+38], fill=col)
        d.text((pad+9,y+13), str(i+1), font=f_d, fill=(255,255,255))
        # dominio chip
        chip=DOM_LABEL[dom]
        cw=d.textbbox((0,0),chip,font=f_d); cwid=cw[2]-cw[0]+18
        d.rounded_rectangle([pad+44,y+11,pad+44+cwid,y+35], 6, fill=col)
        d.text((pad+53,y+15), chip, font=f_d, fill=(255,255,255))
        # tool name
        d.text((pad+44+cwid+16, y+8), tool, font=f_s, fill=INK)
        # desc
        d.text((pad+44+cwid+16, y+31), desc, font=f_n, fill=MUTE)
    # footer
    note = ("Stesso verbo «trova» su 4 oggetti diversi (file, spese, foto, web): "
            "qui nasce la difficolta'." if lang=="it" else
            "Same verb \"find\" over 4 different objects (files, expenses, photos, web): "
            "this is where it gets hard.")
    d.text((pad, H-50), note, font=f_n, fill=(160,82,45))
    out=_DIR/f"example_breakdown_{lang}.png"
    img.save(out,"PNG"); print(f"{out.name} ({W}x{H})")


if __name__=="__main__":
    main("it"); main("en")
