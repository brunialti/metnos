"""Helper deterministici per il rendering dei messaggi finali.

Modulo channel-agnostic (markdown semantico). I channel adapter
(`channels/daemon.py`, `http_routes_agent.py`) traducono il markdown
nel formato del canale (Telegram HTML subset, HTTP full HTML, voce =
plain stripped).

Regole guida (ADR 0095):
- KV (chiave/valore singolo): `**label**: value [unit]`. Niente slash
  ambigui per gruppi correlati: usare `format_kv_group`.
- Liste con record omogenei a >=3 attributi comparabili → `format_table`.
  Liste a <3 attributi o testo libero → `format_list` (bullet).
- Output >5 righe: aprire con `format_tldr` (1 riga riassunto).
- Sezioni multiple: separare con `format_separator()` (HR markdown).
- Cap-expand / offerta utente → blocco proprio con titolo dedicato,
  staccato dal contenuto.

Niente HTML hardcoded qui. Niente LLM call (§7.9 determinismo > LLM).
"""
from __future__ import annotations

from typing import Iterable, Sequence


def _strip(s: object) -> str:
    return str(s).strip() if s is not None else ""


def format_kv(label: str, value: object, unit: str | None = None) -> str:
    """Singolo `**label**: value [unit]`. Una riga, no newline finale.

    >>> format_kv("RAM", "38.4", "%")
    '**RAM**: 38.4%'
    >>> format_kv("Path", "/tmp/x.txt")
    '**Path**: /tmp/x.txt'
    """
    label_s = _strip(label)
    value_s = _strip(value)
    if unit:
        unit_s = _strip(unit)
        # No spazio prima per simboli (%, $); spazio per parole (GB, sec).
        if unit_s in ("%",):
            return f"**{label_s}**: {value_s}{unit_s}"
        return f"**{label_s}**: {value_s} {unit_s}"
    return f"**{label_s}**: {value_s}"


def format_kv_group(title: str | None,
                    pairs: Sequence[tuple[str, object, str | None]]) -> str:
    """Gruppo di KV correlati. `pairs` = sequenza di `(label, value, unit_or_None)`.
    Restituisce blocco con eventuale titolo + bullet per ogni pair.

    Esempio Load average:
        format_kv_group("Carico", [("1m", 0.98, None), ("5m", 0.94, None),
                                    ("15m", 0.47, None)])
        →
        **Carico**
          • 1m: 0.98
          • 5m: 0.94
          • 15m: 0.47
    """
    lines: list[str] = []
    if title:
        lines.append(f"**{_strip(title)}**")
    for p in pairs:
        if not isinstance(p, (tuple, list)) or len(p) < 2:
            continue
        label = p[0]
        value = p[1]
        unit = p[2] if len(p) >= 3 else None
        unit_s = _strip(unit) if unit else ""
        if unit_s == "%":
            lines.append(f"  • {_strip(label)}: {_strip(value)}{unit_s}")
        elif unit_s:
            lines.append(f"  • {_strip(label)}: {_strip(value)} {unit_s}")
        else:
            lines.append(f"  • {_strip(label)}: {_strip(value)}")
    return "\n".join(lines)


def format_list(title: str | None, items: Iterable[object],
                bullet: str = "•", cap: int | None = None) -> str:
    """Lista bullet semplice. `cap` = limite con notice di troncamento.
    Restituisce stringa multi-riga.

    >>> format_list("File trovati", ["a.py", "b.py"])
    '**File trovati**\\n  • a.py\\n  • b.py'
    """
    items_list = list(items)
    total = len(items_list)
    if cap is not None and cap > 0:
        items_list = items_list[:cap]
    lines: list[str] = []
    if title:
        lines.append(f"**{_strip(title)}**")
    for it in items_list:
        lines.append(f"  {bullet} {_strip(it)}")
    if cap is not None and cap < total:
        lines.append(f"  …(altri {total - cap} omessi)")
    return "\n".join(lines)


def format_table(headers: Sequence[str], rows: Sequence[Sequence[object]],
                 title: str | None = None,
                 align: Sequence[str] | None = None) -> str:
    """Tabella markdown. `align` = lista di 'left'/'right'/'center' per colonna.

    Esempio:
        format_table(["Processo", "CPU%", "MEM%"],
                     [["python", "100.0", "0.0"], ["llama-server", "3.8", "11.1"]],
                     align=["left", "right", "right"])
    """
    if not headers:
        return ""
    n_cols = len(headers)
    align_chars: list[str] = []
    for i in range(n_cols):
        a = align[i] if align and i < len(align) else "left"
        if a == "right":
            align_chars.append("---:")
        elif a == "center":
            align_chars.append(":---:")
        else:
            align_chars.append("---")
    lines: list[str] = []
    if title:
        lines.append(f"**{_strip(title)}**")
        lines.append("")
    lines.append("| " + " | ".join(_strip(h) for h in headers) + " |")
    lines.append("| " + " | ".join(align_chars) + " |")
    for r in rows:
        cells = [_strip(c) for c in r]
        # Pad/truncate alle colonne attese
        while len(cells) < n_cols:
            cells.append("")
        cells = cells[:n_cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_section(title: str, body: str) -> str:
    """Sezione con titolo (heading h3 markdown) e corpo."""
    title_s = _strip(title)
    body_s = _strip(body)
    if not body_s:
        return f"### {title_s}"
    return f"### {title_s}\n\n{body_s}"


def format_tldr(line: str) -> str:
    """Riga di apertura per output >5 righe. Italico + prefisso localizzato.

    Il prefisso e' fetched da `messages.MSG_TLDR_PREFIX` (i18n compliance,
    ADR 0104). DEFAULT_LANG via env METNOS_LANG.
    """
    # Import lazy per evitare import-time cycle (messages → i18n → sqlite).
    from messages import get as _msg
    prefix = _msg("MSG_TLDR_PREFIX")
    return f"_{prefix}: {_strip(line)}_"


def format_separator() -> str:
    """HR markdown standalone. Channel adapter pu? collassarlo a `\\n` se
    non supportato (Telegram non supporta HR nativo)."""
    return "\n---\n"


def format_offer(title: str, body: str) -> str:
    """Blocco visivamente separato per cap-expand / offerte utente.
    Compone separator + sezione titolata.
    """
    return format_separator() + format_section(title, body)


_COOKIE_BANNER_MARKERS = (
    "questo sito utilizza cookie",
    "this site uses cookies",
    "we use cookies",
    "uso dei cookie",
    "accetta i cookie",
    "accept cookies",
    "cookie tecnici",
    "cookie policy",
    "proseguendo nella navigazione",
    "by continuing to browse",
    "informativa sulla privacy",
    "privacy policy",
)


def _is_cookie_banner(snippet: str) -> bool:
    """Heuristic: true se lo snippet e' un banner cookie/privacy
    (markers comuni IT+EN). Usato per droppare snippet inutili da
    `format_search_results`."""
    if not snippet:
        return False
    s = snippet.lower()
    return any(m in s for m in _COOKIE_BANNER_MARKERS)


def _sanitize_title_for_md_link(text: str) -> str:
    """Sostituisce `[...]` dentro un titolo con `(...)` per evitare che
    il parser markdown spezzi il link `[title](url)`. L'escape `\\[`/`\\]`
    NON funziona col parser di `to_safe_html_full` (10/5/2026): il link
    non viene generato del tutto. Sostituire i bracket e' la soluzione
    pulita. Esempio: `Scuola Primaria [663 KB]` -> `Scuola Primaria (663 KB)`."""
    if not isinstance(text, str):
        return ""
    return text.replace("[", "(").replace("]", ")")


def format_search_results(
    entries,
    *,
    query: str = "",
    discovered_documents=(),
    max_show: int = 20,
    snippet_max: int = 140,
) -> str:
    """Markdown deterministico per risultati di ricerca web (find_urls).

    Stile lista numerata cliccabile (link Markdown), con score badge se
    presente, snippet 1-riga sotto (solo se NON e' un cookie banner).
    Sezione separata per documenti scoperti (PDF/docx/etc), dedupli-
    cati per URL.

    Output user-facing: NON e' un riassunto LLM, e' la lista grezza
    formattata. Il chat lo rende cliccabile via `to_safe_html_full`
    (anchor whitelist gia' attivo).

    Localizzato via i18n (MSG_SEARCH_RESULTS_HEADER /
    MSG_SEARCH_RESULTS_NONE / MSG_SEARCH_DOCS_HEADER).
    """
    from messages import get as _msg
    entries = list(entries or [])
    docs_in = list(discovered_documents or [])
    n = len(entries)
    n_docs = len(docs_in)
    if n == 0 and n_docs == 0:
        return _msg("MSG_SEARCH_RESULTS_NONE", query=query or "")

    lines: list[str] = []
    if n > 0:
        header = _msg("MSG_SEARCH_RESULTS_HEADER",
                       n=min(n, max_show), query=query or "")
        # Compatto: niente blank line dopo header, niente blank line tra entries.
        # snippet (se presente) viene appesa con `\n  ` (newline + indent 2 sp)
        # per stare nello stesso paragrafo del titolo nel markdown renderer.
        lines.append(f"**{header}**")
    counter = 0
    for e in entries[:max_show]:
        url = _strip(e.get("url") if isinstance(e, dict) else "")
        if not url:
            continue
        counter += 1
        title = _strip(e.get("title")) or url
        title_safe = _sanitize_title_for_md_link(title)
        score = e.get("score")
        score_part = ""
        if isinstance(score, (int, float)) and score > 0:
            score_part = f" — score {float(score):.2f}"
        # 11/5/2026: drop snippet dal listato compatto. Snippet
        # inconsistenti (alcune entries lo hanno, altre no; cookie banner,
        # ripetizioni del titolo) generavano line-height irregolare e
        # confusione. Una riga per entry, click sul link per il contenuto.
        lines.append(f"**{counter}.** [{title_safe}]({url}){score_part}")
    if n > max_show:
        lines.append(_msg("MSG_SEARCH_RESULTS_MORE",
                           hidden=n - max_show))

    docs_seen = set()
    docs: list[dict] = []
    for d in (discovered_documents or []):
        if not isinstance(d, dict):
            continue
        url = _strip(d.get("url"))
        if not url or url in docs_seen:
            continue
        docs_seen.add(url)
        docs.append(d)
    if docs:
        # Singolo blank line SOLO fra le due sezioni (risultati vs documenti).
        if lines:
            lines.append("")
        lines.append(f"**{_msg('MSG_SEARCH_DOCS_HEADER', n=len(docs))}**")
        for j, d in enumerate(docs[:max_show], start=1):
            url = _strip(d.get("url"))
            anchor = _strip(d.get("anchor_text")) or _strip(d.get("title")) or url
            anchor_safe = _sanitize_title_for_md_link(anchor)
            ext = _strip(d.get("ext"))
            ext_part = f" ({ext})" if ext else ""
            lines.append(f"**{j}.** [{anchor_safe}]({url}){ext_part}")

    return "\n".join(lines)


__all__ = [
    "format_kv",
    "format_kv_group",
    "format_list",
    "format_table",
    "format_section",
    "format_tldr",
    "format_separator",
    "format_offer",
    "format_search_results",
]
