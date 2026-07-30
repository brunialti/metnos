#!/usr/bin/env python3
"""Generate the bilingual introduction to the Metnos web interface.

The page is a MAP, not an inventory.  Field lists, controls, procedures, and
stop conditions of each administration page live once in the surfaces registry
(`runtime/ui_surfaces.py`) and reach the reader through the Tutor, which serves
them with registry authority.  Republishing them here would duplicate the same
evidence at a lower authority and make both copies compete for the same
retrieval slot, so this document carries only what the registry cannot state:
the orientation prose, the organizing rule, and the page names it derives so
the map cannot go stale.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
OUTPUTS = {
    "it": ROOT / "docs" / "it" / "interface.html",
    "en": ROOT / "docs" / "en" / "interface.html",
}

if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from ui_surfaces import settings_navigation  # noqa: E402


TEXT = {
    "it": {
        "title": "L'interfaccia di Metnos",
        "description": (
            "Com'è fatta l'interfaccia di Metnos: due canali di conversazione "
            "e le pagine di amministrazione dentro Settings."),
        "nav_home": "Metnos",
        "nav_manual": "Guida all'architettura",
        "nav_domains": "Riferimento dei domini",
        "nav_tutor": "Come funziona il Tutor",
        "eyebrow": "Guida introduttiva · mappa derivata dal registro delle superfici",
        "lead": "Due canali, una sola istanza e le pagine che la governano",
        "intro": (
            "Metnos si usa parlandogli. L'interfaccia esiste per due cose che "
            "la conversazione da sola non fa bene: mostrarti lo stato del "
            "sistema e farti intervenire su ciò che lo governa. Questa pagina "
            "ti dice com'è organizzata, così non devi cercarla a tentoni."),
        "count_pages": "pagine di amministrazione",
        "count_sections": "sezioni di Settings",
        "contract": (
            "La prosa di questa pagina è curata; l'elenco delle pagine, i loro "
            "percorsi e gli indirizzi derivano dal registro delle superfici "
            "del runtime, quindi restano allineati al "
            "prodotto. Qui trovi la mappa, non l'inventario: i campi visibili, "
            "i comandi e le procedure di ogni pagina vivono una volta sola nel "
            "registro; il Tutor li descrive su richiesta."),
        "jump_channels": "Due canali, una stessa identità",
        "jump_settings": "Com'è organizzato Settings",
        "jump_access": "Chi può vedere che cosa",
        "jump_ask": "Se non trovi una pagina",
        "channels_title": "Due canali, una stessa identità",
        "channels_body": (
            "Puoi parlare con Metnos dalla chat web dell'istanza o da "
            "Telegram. Ritrovi lo stesso sistema, la stessa identità e le "
            "stesse autorizzazioni, ma ciascun canale conserva la propria "
            "conversazione e la propria cronologia. Le pagine di "
            "amministrazione esistono soltanto nella chat "
            "web. Se stai scrivendo da Telegram e una risposta ti indica un "
            "percorso di Settings, quel percorso va aperto nella chat web, non "
            "dentro Telegram. Nel canale web, se apri la chat da un secondo "
            "dispositivo mentre il primo è attivo, Metnos offre tre scelte: "
            "«Annulla» non cambia nulla e lascia il nuovo browser in sola "
            "lettura; «Rendi attiva questa sessione» usa la conversazione già "
            "presente nel nuovo browser; «Continua la sessione precedente» "
            "trasferisce al nuovo browser la conversazione del primo "
            "dispositivo. Le due cronologie non vengono fuse e il browser "
            "revocato diventa di sola lettura. Queste sessioni e le relative "
            "cronologie sono indipendenti per ciascun utente: un conflitto "
            "nella sessione del proprietario non coinvolge un ospite, e "
            "viceversa."),
        "settings_title": "Com'è organizzato Settings",
        "settings_body": (
            "Settings è la parte amministrativa della chat web. Si apre su una "
            "panoramica dello stato e si dirama in quattro sezioni. Ogni pagina "
            "si raggiunge come «Settings &gt; Sezione &gt; Pagina» e risponde a "
            "un indirizzo che comincia per <code>/admin</code>. Le etichette "
            "sono disponibili in italiano e inglese; in una lingua non ancora "
            "tradotta l'interfaccia mostra la versione inglese. Il Tutor cita "
            "il percorso nella stessa "
            "forma in cui compare nell'interfaccia."),
        "root_label": "Panoramica",
        "map_title": "Mappa dell'interfaccia di Metnos",
        "map_desc": (
            "Dai due canali di conversazione a Settings, e da Settings alle "
            "quattro sezioni con le pagine di amministrazione, ognuna con il "
            "proprio indirizzo."),
        "map_conversation": "stessa identità · cronologie separate",
        "map_chip_web": "chat web",
        "map_chip_telegram": "Telegram",
        "map_only_web": "Settings si apre solo qui",
        "map_root_note": "panoramica dello stato",
        "map_caption": (
            "Mappa di riferimento: percorsi e indirizzi sono quelli del "
            "registro delle superfici. Che cosa mostra ogni pagina, e che cosa "
            "puoi farci, lo descrive il Tutor usando l'istanza che hai davanti."),
        "access_title": "Chi può vedere che cosa",
        "access_body": (
            "Le pagine sotto <code>/admin</code> richiedono il ruolo di "
            "amministratore dell'istanza: un ospite invitato non le apre. "
            "Sapere che una pagina esiste e che cosa contiene è un'altra cosa "
            "dall'aprirla, e le due autorizzazioni sono distinte: il Tutor "
            "spiega una pagina a chi è ammesso a conoscerla, anche quando "
            "l'accesso resta riservato all'amministratore."),
        "ask_title": "Se non trovi una pagina",
        "ask_body": (
            "Non serve memorizzare questa mappa. Chiedi a Metnos con una "
            "richiesta come quella di questo esempio: «Mostrami quale "
            "embedder è configurato in Settings &gt; Sistema &gt; Modelli e "
            "guidami per raggiungere la pagina dalla chat web». Il Tutor "
            "risponde sia dalla chat "
            "web sia da Telegram, ma il percorso indicato si apre sempre "
            "nella chat web. Quando chiedi dove si trova qualcosa, o che "
            "cosa contiene una pagina, "
            "risponde con il percorso, l'indirizzo e i contenuti attestati "
            "dall'istanza che hai davanti, non da una documentazione generica. "
            "Se la pagina non esiste nella tua installazione te lo dice, "
            "invece di inventarla."),
        "footer": (
            "Pagina generata da <code>scripts/generate_ui_reference.py</code> "
            "a partire dal registro delle superfici del runtime. I dettagli "
            "correnti di ogni pagina appartengono all'istanza: chiedili al Tutor."),
    },
    "en": {
        "title": "The Metnos interface",
        "description": (
            "How the Metnos interface is arranged: two conversation "
            "channels, and the administration pages inside Settings."),
        "nav_home": "Metnos",
        "nav_manual": "Architecture guide",
        "nav_domains": "Domain reference",
        "nav_tutor": "How the Tutor works",
        "eyebrow": "Introductory guide · map derived from the surfaces registry",
        "lead": "Two channels, one instance, and the pages that govern it",
        "intro": (
            "You use Metnos by talking to it. The interface exists for the two "
            "things a conversation alone does poorly: showing you the state of "
            "the system and letting you act on what governs it. This page "
            "tells you how it is arranged, so you do not have to hunt for it."),
        "count_pages": "administration pages",
        "count_sections": "Settings sections",
        "contract": (
            "The prose on this page is curated; the list of pages, their "
            "navigation paths, and their routes derive from the runtime's "
            "surfaces registry, so they cannot age separately from the "
            "product. This is the map, not the inventory: the visible fields, "
            "controls, and procedures of each page live once in the registry, "
            "and the Tutor recounts them if you ask."),
        "jump_channels": "Two channels, one identity",
        "jump_settings": "How Settings is arranged",
        "jump_access": "Who may see what",
        "jump_ask": "When you cannot find a page",
        "channels_title": "Two channels, one identity",
        "channels_body": (
            "You can talk to Metnos from the instance's web chat or from "
            "Telegram. You reach the same system with the same identity and "
            "authority, but each channel retains its own conversation and "
            "history. Administration pages exist in the web chat only. If you are "
            "writing from Telegram and an answer points you to a Settings "
            "path, open that path in the web chat, not inside Telegram. On "
            "the web channel, if you open the chat on a second device while "
            "the first one is active, Metnos offers three choices: Cancel "
            "changes nothing and leaves the new browser read-only; Make this "
            "session active uses the conversation already present in the new "
            "browser; Continue the previous session transfers the first "
            "device's conversation to the new browser. The two histories are "
            "not merged, and the revoked browser becomes read-only. These "
            "sessions and their histories are independent for each user: an "
            "owner session conflict does not involve a guest, and vice versa."),
        "settings_title": "How Settings is arranged",
        "settings_body": (
            "Settings is the administrative part of the web chat. It opens on "
            "a status overview and branches into four sections. Every page is "
            "reached as “Settings &gt; Section &gt; Page” and answers at an "
            "address starting with <code>/admin</code>. Labels are available "
            "in Italian and English; in a language not yet translated, the "
            "interface shows the English version. The Tutor quotes the path "
            "exactly as it appears in the "
            "interface."),
        "root_label": "Overview",
        "map_title": "Map of the Metnos interface",
        "map_desc": (
            "From the two conversation channels to Settings, and from Settings "
            "to the four sections with their administration pages, each with "
            "its own address."),
        "map_conversation": "same identity · separate histories",
        "map_chip_web": "web chat",
        "map_chip_telegram": "Telegram",
        "map_only_web": "Settings opens here only",
        "map_root_note": "status overview",
        "map_caption": (
            "Reference map: paths and addresses are the ones held by the "
            "surfaces registry. What each page shows, and what you can do "
            "there, the Tutor recounts on the instance in front of you."),
        "access_title": "Who may see what",
        "access_body": (
            "The pages under <code>/admin</code> require the instance "
            "administrator role: an invited guest cannot open them. Knowing "
            "that a page exists and what it contains is a different matter "
            "from opening it, and the two permissions are distinct: the Tutor "
            "explains a page to whoever is allowed to know it, even when "
            "access stays reserved to the administrator."),
        "ask_title": "When you cannot find a page",
        "ask_body": (
            "You do not need to memorize this map. Ask Metnos with a request "
            "like this example: “Show me which embedder is configured under "
            "Settings &gt; System &gt; Models and guide me there from the web "
            "chat.” The Tutor "
            "answers in both the web chat and Telegram, but the path it gives "
            "you always opens in the web chat. When you ask where something "
            "is or what a page contains, it answers with the path, "
            "the address, and the contents attested by the instance in front "
            "of you, not by generic documentation. If the page does not exist "
            "in your installation it says so, instead of inventing it."),
        "footer": (
            "Page generated by <code>scripts/generate_ui_reference.py</code> "
            "from the runtime's surfaces registry. The live detail of each "
            "page belongs to the instance: ask the Tutor."),
    },
}

_STYLE = (
    ":root{--ink:#25231f;--muted:#69645b;--paper:#fcfaf6;--warm:#f3eee4;"
    "--line:#ded5c5;--navy:#173f6b;--blue:#2b6cb0;--green:#477342;"
    "--bronze:#9a512f;--white:#fff}"
    "*{box-sizing:border-box}html{scroll-behavior:smooth}"
    "body{margin:0;background:var(--paper);color:var(--ink);"
    "font:16px/1.62 Inter,'Segoe UI',system-ui,sans-serif}a{color:var(--navy)}"
    ".shell{max-width:1180px;margin:auto;padding:0 28px}"
    "nav{display:flex;gap:18px;flex-wrap:wrap;padding:20px 0;"
    "border-bottom:1px solid var(--line);font-size:.92rem}"
    "nav a{text-decoration:none;font-weight:650}"
    ".hero{padding:74px 0 42px;display:grid;"
    "grid-template-columns:minmax(0,1.5fr) minmax(260px,.7fr);gap:46px;"
    "align-items:end}"
    ".eyebrow{text-transform:uppercase;letter-spacing:.12em;"
    "color:var(--bronze);font-size:.76rem;font-weight:800}"
    "h1{font-family:Georgia,serif;color:var(--navy);"
    "font-size:clamp(2.45rem,6vw,5rem);line-height:1.02;margin:.25em 0}"
    ".lead{font:1.35rem/1.5 Georgia,serif;color:var(--navy);max-width:720px}"
    ".hero-note{background:var(--navy);color:white;padding:26px;"
    "border-radius:4px;box-shadow:12px 12px 0 var(--warm)}"
    ".hero-note strong{display:block;font:2.2rem Georgia,serif}"
    ".hero-note span{color:#dce8f6}"
    ".contract{border-left:5px solid var(--green);background:#eef5eb;"
    "padding:20px 24px;margin:0 0 34px}"
    ".jump{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 62px}"
    ".jump a{border:1px solid var(--line);background:white;padding:7px 12px;"
    "border-radius:999px;text-decoration:none;font-size:.86rem}"
    "section{margin:0 0 62px;scroll-margin-top:20px}"
    ".section-title{display:flex;gap:18px;align-items:center;"
    "border-bottom:2px solid var(--navy);margin-bottom:22px;"
    "padding-bottom:12px}"
    ".section-title>span{font:2.4rem Georgia,serif;color:var(--bronze)}"
    ".section-title h2{margin:0;color:var(--navy);font:1.8rem Georgia,serif}"
    ".prose{max-width:820px}"
    ".group{margin:26px 0 0}"
    ".group h3{font:1.25rem Georgia,serif;color:var(--navy);margin:0 0 12px;"
    "padding-bottom:6px;border-bottom:1px solid var(--line)}"
    ".pages{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));"
    "gap:14px;margin:0;padding:0;list-style:none}"
    ".pages li{background:var(--white);border:1px solid var(--line);"
    "border-left:4px solid var(--navy);padding:16px 18px}"
    ".pages strong{display:block;color:var(--navy);font:1.05rem Georgia,serif}"
    ".pages .where{display:block;font-size:.78rem;color:var(--bronze);"
    "font-weight:700;margin:4px 0 8px}"
    ".pages code{font-size:.75rem;background:var(--warm);padding:3px 6px;"
    "border-radius:3px}"
    ".pages p{margin:6px 0 0;font-size:.92rem;color:var(--muted)}"
    "figure{margin:8px 0 34px}"
    ".map{display:block;width:100%;height:auto;max-width:100%}"
    ".map .shadow{fill:rgba(53,42,25,.07)}"
    ".map .band{fill:none;stroke:var(--line);stroke-dasharray:5 4}"
    ".map .band-label{fill:var(--bronze);font:800 10px Inter,'Segoe UI',"
    "sans-serif;text-anchor:middle;text-transform:uppercase;"
    "letter-spacing:.14em}"
    ".map .chip-live{fill:var(--navy)}"
    ".map .chip-alt{fill:var(--white);stroke:var(--line)}"
    ".map .chip-label{fill:var(--navy);font:600 13px Inter,'Segoe UI',"
    "sans-serif;text-anchor:middle}"
    ".map .chip-label-live{fill:#fff}"
    ".map .wire{stroke:var(--line);stroke-width:2;fill:none;"
    "stroke-linecap:round}"
    ".map .wire-strong{stroke:var(--blue)}"
    ".map .wire-note{fill:var(--blue);font:600 10.5px Inter,'Segoe UI',"
    "sans-serif}"
    ".map .joint{fill:var(--line)}"
    ".map .root{fill:var(--navy)}"
    ".map .root-label{fill:#fff;font:700 17px Georgia,serif;"
    "text-anchor:middle}"
    ".map .root-route{fill:#c9dcf1;font:10.5px ui-monospace,monospace;"
    "text-anchor:middle}"
    ".map .head{fill:var(--warm);stroke:var(--line)}"
    ".map .head-index{fill:var(--bronze);font:700 13px Georgia,serif}"
    ".map .head-label{fill:var(--navy);font:800 11px Inter,'Segoe UI',"
    "sans-serif;text-transform:uppercase;letter-spacing:.09em}"
    ".map .page{fill:var(--white);stroke:var(--line)}"
    ".map .edge{fill:var(--navy)}"
    ".map .page-label{fill:var(--navy);font:700 14px Georgia,serif}"
    ".map .page-route{fill:var(--muted);font:10.5px ui-monospace,monospace}"
    "figcaption{margin-top:12px;font-size:.85rem;color:var(--muted);"
    "max-width:820px}"
    "footer{border-top:1px solid var(--line);padding:28px 0 48px;"
    "color:var(--muted);font-size:.86rem}"
    "@media(max-width:800px){.hero{grid-template-columns:1fr;padding-top:45px}"
    ".pages{grid-template-columns:1fr}.shell{padding:0 16px}}"
)


# Geometria della mappa: interi fissi, nessuna misura del testo, così due
# esecuzioni producono gli stessi byte e il `--check` resta significativo.
_PAD = 18
_COL_W = 190
_COL_GAP = 18
_CHANNEL_H = 78
_CHIP_W = 152
_CHIP_H = 34
_CHIP_GAP = 16
_ROOT_W = 306
_ROOT_H = 62
_HEAD_H = 32
_ROW_H = 62
_ROW_GAP = 12


def _map_svg(lang: str) -> str:
    """Mappa a colpo d'occhio: dai due canali a Settings, sezione per sezione.

    Il segmentatore del Tutor considera solo blocchi testuali (h1-h3, p, li,
    pre, tr), quindi questa figura serve il lettore umano senza aggiungere
    evidenza al corpus.
    """

    text = TEXT[lang]
    navigation = settings_navigation(lang)
    groups = [g for g in navigation if g["items"] and g["label"]]
    root = next(s for g in navigation if not g["label"] for s in g["items"])

    columns = len(groups)
    body_w = columns * _COL_W + (columns - 1) * _COL_GAP
    width = body_w + 2 * _PAD
    rows = max(len(g["items"]) for g in groups)
    centre = _PAD + body_w // 2

    channel_w = 2 * _CHIP_W + _CHIP_GAP + 48
    channel_x = centre - channel_w // 2
    root_y = _PAD + _CHANNEL_H + 46
    bus_y = root_y + _ROOT_H + 28
    head_y = bus_y + 24
    first_row_y = head_y + _HEAD_H + 18
    height = first_row_y + rows * (_ROW_H + _ROW_GAP) - _ROW_GAP + _PAD

    def col_x(index: int) -> int:
        return _PAD + index * (_COL_W + _COL_GAP)

    out = [
        f'<svg class="map" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="map-title map-desc" '
        'xmlns="http://www.w3.org/2000/svg">',
        f'<title id="map-title">{html.escape(text["map_title"])}</title>',
        f'<desc id="map-desc">{html.escape(text["map_desc"])}</desc>',
    ]

    # Banda dei canali: la stessa identità, con cronologie distinte.
    chip_y = _PAD + 30
    left_x = centre - _CHIP_W - _CHIP_GAP // 2
    right_x = centre + _CHIP_GAP // 2
    out.append(
        f'<rect class="band" x="{channel_x}" y="{_PAD}" width="{channel_w}" '
        f'height="{_CHANNEL_H}" rx="6"/>'
        f'<text class="band-label" x="{centre}" y="{_PAD + 20}">'
        f'{html.escape(text["map_conversation"])}</text>'
        f'<rect class="chip chip-live" x="{left_x}" y="{chip_y}" '
        f'width="{_CHIP_W}" height="{_CHIP_H}" rx="17"/>'
        f'<text class="chip-label chip-label-live" x="{left_x + _CHIP_W // 2}" '
        f'y="{chip_y + 22}">{html.escape(text["map_chip_web"])}</text>'
        f'<rect class="chip chip-alt" x="{right_x}" y="{chip_y}" '
        f'width="{_CHIP_W}" height="{_CHIP_H}" rx="17"/>'
        f'<text class="chip-label" x="{right_x + _CHIP_W // 2}" '
        f'y="{chip_y + 22}">{html.escape(text["map_chip_telegram"])}</text>')

    # Discesa annotata: a Settings si arriva solo dalla chat web. La linea
    # scende diritta dal chip al bordo della scheda, che è più larga dei due
    # chip: nessun gomito da leggere.
    entry_x = left_x + _CHIP_W // 2
    out.append(
        f'<path class="wire wire-strong" d="M{entry_x} {chip_y + _CHIP_H}'
        f'V{root_y}"/>'
        f'<text class="wire-note" x="{entry_x + 12}" '
        f'y="{root_y - 14}">{html.escape(text["map_only_web"])}</text>')

    out.append(
        f'<rect class="shadow" x="{centre - _ROOT_W // 2 + 3}" y="{root_y + 4}" '
        f'width="{_ROOT_W}" height="{_ROOT_H}" rx="5"/>'
        f'<rect class="root" x="{centre - _ROOT_W // 2}" y="{root_y}" '
        f'width="{_ROOT_W}" height="{_ROOT_H}" rx="5"/>'
        f'<text class="root-label" x="{centre}" y="{root_y + 26}">'
        f'{html.escape(root.label(lang))}</text>'
        f'<text class="root-route" x="{centre}" y="{root_y + 46}">'
        f'{html.escape(root.route)} · {html.escape(text["map_root_note"])}'
        '</text>')

    first_mid = col_x(0) + _COL_W // 2
    last_mid = col_x(columns - 1) + _COL_W // 2
    out.append(
        f'<path class="wire" d="M{centre} {root_y + _ROOT_H}V{bus_y}"/>'
        f'<path class="wire" d="M{first_mid} {bus_y}H{last_mid}"/>')

    for index, group in enumerate(groups):
        x = col_x(index)
        mid = x + _COL_W // 2
        out.append(
            f'<path class="wire" d="M{mid} {bus_y}V{head_y}"/>'
            f'<circle class="joint" cx="{mid}" cy="{bus_y}" r="3.5"/>'
            f'<rect class="head" x="{x}" y="{head_y}" width="{_COL_W}" '
            f'height="{_HEAD_H}" rx="16"/>'
            f'<text class="head-index" x="{x + 16}" y="{head_y + 22}">'
            f'{index + 1:02d}</text>'
            f'<text class="head-label" x="{x + 40}" y="{head_y + 21}">'
            f'{html.escape(group["label"])}</text>')
        for row, surface in enumerate(group["items"]):
            y = first_row_y + row * (_ROW_H + _ROW_GAP)
            out.append(
                f'<rect class="shadow" x="{x + 2}" y="{y + 3}" '
                f'width="{_COL_W}" height="{_ROW_H}" rx="4"/>'
                f'<rect class="page" x="{x}" y="{y}" width="{_COL_W}" '
                f'height="{_ROW_H}" rx="4"/>'
                f'<rect class="edge" x="{x}" y="{y}" width="4" '
                f'height="{_ROW_H}"/>'
                f'<text class="page-label" x="{x + 16}" y="{y + 25}">'
                f'{html.escape(surface.label(lang))}</text>'
                f'<text class="page-route" x="{x + 16}" y="{y + 45}">'
                f'{html.escape(surface.route)}</text>')
    out.append("</svg>")
    return "".join(out)


def _page_item(surface, lang: str) -> str:
    """One map entry: name, where it is, its address, one line of purpose."""

    return (
        "<li>"
        f"<strong>{html.escape(surface.label(lang))}</strong>"
        f'<span class="where">{html.escape(surface.breadcrumb(lang))}'
        f" · <code>{html.escape(surface.route)}</code></span>"
        f"<p>{html.escape(surface.summary(lang))}</p>"
        "</li>"
    )


def _groups(lang: str) -> tuple[str, int]:
    """Render the Settings map; return the markup and the page count."""

    text = TEXT[lang]
    blocks: list[str] = []
    pages = 0
    for group in settings_navigation(lang):
        if not group["items"]:
            continue
        label = group["label"] or text["root_label"]
        items = "".join(_page_item(s, lang) for s in group["items"])
        pages += len(group["items"])
        blocks.append(
            f'<div class="group"><h3>{html.escape(label)}</h3>'
            f'<ul class="pages">{items}</ul></div>')
    return "".join(blocks), pages


def render(lang: str) -> str:
    text = TEXT[lang]
    other = "en" if lang == "it" else "it"
    groups, pages = _groups(lang)
    figure = (
        f'<figure>{_map_svg(lang)}'
        f'<figcaption>{html.escape(text["map_caption"])}</figcaption></figure>')
    sections = len([g for g in settings_navigation(lang)
                    if g["items"] and g["label"]])
    return f'''<!DOCTYPE html>
<!-- Generated by scripts/generate_ui_reference.py; edit the generator, not this file. -->
<html lang="{lang}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Metnos — {html.escape(text["title"])}</title>
<meta name="description" content="{html.escape(text["description"], quote=True)}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://metnos.com/{lang}/interface">
<link rel="alternate" hreflang="it" href="https://metnos.com/it/interface">
<link rel="alternate" hreflang="en" href="https://metnos.com/en/interface">
<link rel="alternate" hreflang="x-default" href="https://metnos.com/en/interface">
<link rel="stylesheet" href="/assets/metnos.css">
<style>
{_STYLE}
</style></head><body>
<div class="shell">
<nav><a href="index.html">← {text["nav_home"]}</a><a href="domains.html">{text["nav_domains"]}</a><a href="architecture/index.html">{text["nav_manual"]}</a><a href="architecture/tutor.html">{text["nav_tutor"]}</a><a href="/{other}/interface.html" hreflang="{other}">{other.upper()}</a></nav>
<header class="hero"><div><div class="eyebrow">{text["eyebrow"]}</div><h1>{text["lead"]}</h1><p class="lead">{text["intro"]}</p></div><div class="hero-note"><strong>{pages}</strong><span>{text["count_pages"]}</span><strong>{sections}</strong><span>{text["count_sections"]}</span></div></header>
<p class="contract">{text["contract"]}</p>
<div class="jump"><a href="#channels">{html.escape(text["jump_channels"])}</a><a href="#settings">{html.escape(text["jump_settings"])}</a><a href="#access">{html.escape(text["jump_access"])}</a><a href="#ask">{html.escape(text["jump_ask"])}</a></div>

<section id="channels">
  <div class="section-title"><span>01</span><h2>{html.escape(text["channels_title"])}</h2></div>
  <p class="prose">{text["channels_body"]}</p>
</section>

<section id="settings">
  <div class="section-title"><span>02</span><h2>{html.escape(text["settings_title"])}</h2></div>
  <p class="prose">{text["settings_body"]}</p>
  {figure}
  {groups}
</section>

<section id="access">
  <div class="section-title"><span>03</span><h2>{html.escape(text["access_title"])}</h2></div>
  <p class="prose">{text["access_body"]}</p>
</section>

<section id="ask">
  <div class="section-title"><span>04</span><h2>{html.escape(text["ask_title"])}</h2></div>
  <p class="prose">{text["ask_body"]}</p>
</section>

<footer>{text["footer"]}</footer>
</div></body></html>
'''


def write_reference(*, check: bool = False) -> bool:
    changed = False
    for lang, output in OUTPUTS.items():
        content = render(lang)
        current = output.read_text(encoding="utf-8") if output.is_file() else ""
        if content == current:
            continue
        changed = True
        if not check:
            output.write_text(content, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = write_reference(check=args.check)
    if args.check and changed:
        print("ui reference docs are stale", file=sys.stderr)
        return 1
    if not args.check:
        _groups_markup, pages = _groups("it")
        print(f"generated {pages} interface pages in 2 locales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
