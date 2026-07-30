"""telegram_format — Telegram-specific chunking sopra il sanitizer generico.

Telegram supporta solo un sottoinsieme di HTML in `parse_mode=HTML`:
  <b> <i> <u> <s> <code> <pre> <a href="..."> <tg-spoiler>

La conversione Markdown → safe HTML e' nel modulo channel-agnostic
`runtime.html_sanitizer.to_safe_html`. Questo file aggiunge SOLO il
chunking specifico del limite Telegram (4096 char) preservando i tag
aperti tramite riapertura in cima al chunk successivo.

Riferimento Telegram: https://core.telegram.org/bots/api#html-style
"""
from __future__ import annotations

import re

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).parent.parent))
from html_sanitizer import to_safe_html  # noqa: E402

PAIRED_TAGS = ("b", "i", "u", "s", "code", "pre", "tg-spoiler", "a")
# Tag full match con attributi (es. <a href="...">). Group 1 = "/" se chiusura,
# group 2 = nome tag, group 3 = attributi (incl. spazio iniziale o vuoto).
_TAG_FULL_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s[^>]*)?)>")
_TELEGRAM_LIMIT = 4096
# Default chunk_html: 4000 lascia ~96 char di margine per le sequenze di
# riapertura/chiusura tag aggiunte al boundary (caso peggiore: stack
# di 4-5 tag annidati piu' attributo `a href`). Telegram hard limit = 4096.
_DEFAULT_CHUNK = 4000

_MD_TABLE_RE = re.compile(
    r"(?ms)^(\s*\|[^\n]+\|\s*\n"
    r"\s*\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|\s*\n"
    r"(?:\s*\|[^\n]+\|\s*\n?)+)"
)


def _split_table_row(row: str) -> list[str]:
    row = row.strip().strip("|")
    return [cell.strip() for cell in row.split("|")]


def _compact_wide_tables(md: str) -> str:
    """Render wide Markdown tables as vertically wrapping Telegram cards.

    A six-column table may be valid HTML but is unreadable in Telegram's
    horizontally scrolling ``<pre>`` block.  Narrow tables retain the compact
    monospace rendering; tables with four or more columns, or very wide rows,
    become one labelled record per row while preserving every cell.
    """
    def _replace(match: re.Match) -> str:
        block = match.group(1)
        lines = [line.strip() for line in block.strip().splitlines()
                 if line.strip()]
        if len(lines) < 3:
            return block
        headers = _split_table_row(lines[0])
        rows = [_split_table_row(line) for line in lines[2:]]
        estimated_width = max((len(line) for line in lines), default=0)
        if len(headers) <= 3 and estimated_width <= 100:
            return block
        rendered: list[str] = []
        for index, row in enumerate(rows, start=1):
            rendered.append(f"**{index}.**")
            for column, header in enumerate(headers):
                value = row[column] if column < len(row) else ""
                rendered.append(f"• **{header or column + 1}:** {value}")
            rendered.append("")
        return "\n".join(rendered).rstrip() + (
            "\n" if block.endswith("\n") else "")
    return _MD_TABLE_RE.sub(_replace, md or "")


def _scan_open_tags(text: str) -> list[tuple[str, str]]:
    """Ritorna lo stack dei tag PAIRED_TAGS ancora aperti a fine `text`,
    come lista di tuple `(name, attrs)` per preservare gli attributi
    (es. `href` di `<a>`) alla riapertura nel chunk successivo."""
    stack: list[tuple[str, str]] = []
    for m in _TAG_FULL_RE.finditer(text):
        slash, name, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if name not in PAIRED_TAGS:
            continue
        if not slash:
            stack.append((name, attrs))
        else:
            if stack and stack[-1][0] == name:
                stack.pop()
    return stack


def _close_tags(tags: list[tuple[str, str]]) -> str:
    return "".join(f"</{t[0]}>" for t in reversed(tags))


def _open_tags(tags: list[tuple[str, str]]) -> str:
    return "".join(f"<{t[0]}{t[1]}>" for t in tags)


def chunk_html(text: str, max_len: int = _DEFAULT_CHUNK) -> list[str]:
    """Spezza un testo HTML in chunks ognuno <= max_len, ben formato.

    Strategia:
      - boundary preferiti: newline, poi spazio (entro `end - safety`).
      - se nessun boundary buono: hard split a `end`.
      - calcola lo stack di tag aperti nel chunk; chiude in coda (LIFO);
        riapre IDENTICI (con attributi originali) in cima al chunk
        successivo (FIFO).
      - tag void (`<br>`, `<hr>`, ...) non sono in PAIRED_TAGS → ignorati.
      - caso edge `<pre>` interno: il `<pre>` viene chiuso e riaperto
        come ogni altro paired tag; lo split puo' cadere DENTRO al
        contenuto del `<pre>`, ma il chunk resta valido.
    """
    if len(text) <= max_len:
        return [text]
    out: list[str] = []
    remaining = text
    safety = 96  # margine per close+reopen sequence
    while len(remaining) > max_len:
        end = max_len - safety
        if end <= 0:
            end = max_len
        # Preferisci newline; altrimenti spazio; altrimenti hard split.
        nl = remaining.rfind("\n", 0, end)
        if nl > 0:
            end = nl + 1
        else:
            sp = remaining.rfind(" ", 0, end)
            if sp > 0:
                end = sp + 1
        chunk = remaining[:end]
        opens = _scan_open_tags(chunk)
        if opens:
            chunk = chunk + _close_tags(opens)
        out.append(chunk)
        prefix = _open_tags(opens) if opens else ""
        remaining = prefix + remaining[end:]
    if remaining:
        out.append(remaining)
    return out


def format_for_telegram(md: str, max_len: int = _DEFAULT_CHUNK) -> list[str]:
    """One-stop: Markdown → safe HTML → chunks pronti per sendMessage.
    Il pass di sanitizzazione (escape entita' + Markdown→tag) e' delegato
    a `runtime.html_sanitizer.to_safe_html` per riuso cross-channel.
    Default `max_len=4000` (vs hard limit 4096) lascia margine per
    sequenza close/reopen aggiunte al boundary."""
    return chunk_html(to_safe_html(_compact_wide_tables(md)), max_len=max_len)
