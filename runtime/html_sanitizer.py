"""runtime.html_sanitizer — Markdown → safe HTML, channel-agnostic.

Usato da ogni canale che invia messaggi formattati (Telegram, futuro HTTP/web).
Garanzie:
- Ogni `<`, `>`, `&` letterale viene escapato a entita' PRIMA di iniettare
  qualunque tag. Niente HTML injection involontario dal contenuto utente o
  dal final answer dell'LLM.
- Il sottoinsieme di tag emessi e' deliberatamente piccolo: `<b>`, `<i>`,
  `<code>`, `<pre>`, `<a href=...>`. Compatibile con la whitelist di
  Telegram (parse_mode=HTML) e generico per qualunque view HTML downstream.
- Funzione pura: nessun side-effect, nessun network, nessun IO.

Pipeline tipica di un canale:
    safe = to_safe_html(answer_md)        # entita' + Markdown→tags
    chunks = chunk_html(safe, max_len)    # chunking specifico del canale
    for c in chunks: channel.send(c, parse_mode="HTML")
"""
from __future__ import annotations

import html as _html
import re


_LATEX_REPLACEMENTS = (
    (r"\$\\rightarrow\$", "→"),
    (r"\$\\Rightarrow\$", "⇒"),
    (r"\$\\leftarrow\$",  "←"),
    (r"\$\\Leftarrow\$",  "⇐"),
    (r"\$\\leftrightarrow\$", "↔"),
    (r"\$\\to\$",         "→"),
    (r"\$\\mapsto\$",     "↦"),
    (r"\$\\times\$",      "×"),
    (r"\$\\cdot\$",       "·"),
    (r"\$\\pm\$",         "±"),
    (r"\$\\leq\$",        "≤"),
    (r"\$\\geq\$",        "≥"),
    (r"\$\\neq\$",        "≠"),
    (r"\$\\approx\$",     "≈"),
    (r"\$\\infty\$",      "∞"),
    (r"\$\\alpha\$",      "α"),
    (r"\$\\beta\$",       "β"),
    (r"\$\\gamma\$",      "γ"),
    (r"\$\\delta\$",      "δ"),
)
_LATEX_INLINE_RE = re.compile(r"(?<!\\)\$([^$\n]+?)\$")
_LATEX_DISPLAY_RE = re.compile(r"\$\$([^$]+?)\$\$", flags=re.DOTALL)

# Schemi consentiti negli href dei link markdown. Tutto il resto
# (javascript:, data:, vbscript:, file:, ...) e' rifiutato: l'href viene
# scartato e si emette solo il testo. Funzione prevista di un sanitizer:
# bloccare gli URL attivi (XSS). I link relativi (`/`, `#`, `?`, `.`) e i
# frammenti sono consentiti perche' privi di schema attivo.
_SAFE_URL_SCHEMES = ("http", "https", "mailto", "tel")
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _safe_href(url: str) -> str | None:
    """Ritorna l'href se lo schema e' consentito, altrimenti None.

    - URL relativi/ancora (iniziano con `/`, `#`, `?`, `.`) → consentiti.
    - URL con schema in `_SAFE_URL_SCHEMES` → consentiti.
    - Qualunque altro schema (es. `javascript:`, `data:`) → None (drop href).

    Lo schema viene valutato sulla stringa con whitespace di controllo
    (tab/newline/NUL) rimossi: i browser ignorano questi byte quando
    risolvono `javascript:`, quindi `java\\tscript:` deve essere bloccato.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    # Rimuove i byte di controllo che i browser ignorano nella risoluzione
    # dello schema (evita bypass tipo `java&#9;script:`).
    probe = re.sub(r"[\x00-\x20]", "", raw)
    m = _SCHEME_RE.match(probe)
    if not m:
        # Nessuno schema → relativo o ancora: consentito.
        return raw
    scheme = m.group(1).lower()
    if scheme in _SAFE_URL_SCHEMES:
        return raw
    return None


def _strip_latex(s: str) -> str:
    """Mappa notazione LaTeX/MathJax tipica del planner LLM su Unicode.
    Frecce, operatori, lettere greche; poi rimuove i delimitatori `$..$`
    rimasti lasciando il contenuto in chiaro."""
    if not s:
        return ""
    for pattern, repl in _LATEX_REPLACEMENTS:
        s = re.sub(pattern, repl, s)
    s = _LATEX_DISPLAY_RE.sub(lambda m: m.group(1).strip(), s)
    s = _LATEX_INLINE_RE.sub(lambda m: m.group(1).strip(), s)
    return s


_MD_TABLE_BLOCK_RE = re.compile(
    r"(?ms)^(\s*\|[^\n]+\|\s*\n"
    r"\s*\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|\s*\n"
    r"(?:\s*\|[^\n]+\|\s*\n?)+)"
)


def _md_tables_to_pre(s: str) -> str:
    """Trasforma blocchi tabella markdown in `<pre>...</pre>` monospace.

    Telegram non rende `|col1|col2|` come tabella; HTTP chat rende
    cosi' come tabella ma anche il `<pre>` e' leggibile e mantiene
    allineamento. Channel-agnostic: scegliamo `<pre>` per portabilita'.

    Allineamento: parsato dalla riga separator (`---:` right, `:---:`
    center, `---` left). Padding con spazi a width-max per colonna.
    """
    def _repl(m: "re.Match") -> str:
        block = m.group(1)
        rows_raw = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(rows_raw) < 3:
            return block
        def _split_row(row: str) -> list[str]:
            r = row.strip()
            if r.startswith("|"):
                r = r[1:]
            if r.endswith("|"):
                r = r[:-1]
            return [c.strip() for c in r.split("|")]
        header = _split_row(rows_raw[0])
        sep = _split_row(rows_raw[1])
        body = [_split_row(r) for r in rows_raw[2:]]
        n = len(header)
        align: list[str] = []
        for cell in sep:
            c = cell.strip()
            if c.startswith(":") and c.endswith(":"):
                align.append("center")
            elif c.endswith(":"):
                align.append("right")
            else:
                align.append("left")
        all_rows = [header] + body
        widths = [0] * n
        for row in all_rows:
            for i in range(n):
                cell = row[i] if i < len(row) else ""
                if len(cell) > widths[i]:
                    widths[i] = len(cell)
        def _pad(cell: str, w: int, a: str) -> str:
            if a == "right":
                return cell.rjust(w)
            if a == "center":
                return cell.center(w)
            return cell.ljust(w)
        lines = []
        # header line
        lines.append(" | ".join(
            _pad(header[i] if i < len(header) else "", widths[i],
                 align[i] if i < len(align) else "left")
            for i in range(n)
        ))
        # separator (semplice riga di trattini per leggibilita')
        lines.append("-+-".join("-" * widths[i] for i in range(n)))
        # body rows
        for row in body:
            lines.append(" | ".join(
                _pad(row[i] if i < len(row) else "", widths[i],
                     align[i] if i < len(align) else "left")
                for i in range(n)
            ))
        rendered = "<pre>" + "\n".join(lines) + "</pre>"
        # Il matcher della tabella include il newline finale. Ripristinarne
        # uno mantiene separato il blocco Markdown successivo.
        return rendered + ("\n" if re.search(r"\n\s*\Z", block) else "")
    return _MD_TABLE_BLOCK_RE.sub(_repl, s)


def to_safe_html(md: str) -> str:
    """Converte Markdown comune in HTML safe.

    Sequenza:
    1) Strip LaTeX (`$..$`, `$$..$$`) — non rappresentabile.
    2) Escape HTML (`<`, `>`, `&` → entita').
    3) Inietta tag su pattern Markdown:
       - ```` ```code``` ```` → `<pre>...</pre>`
       - `` `code` `` → `<code>...</code>`
       - `**bold**` / `__bold__` → `<b>...</b>`
       - `[text](url)` → `<a href="url">text</a>`
       - `# Heading` → `<b>Heading</b>`
    Italic intenzionalmente non gestito (`*x*` ambiguo coi bullet).

    Output: stringa HTML safe, valida per parse_mode=HTML di Telegram e
    per qualunque view HTML che accetta b/i/code/pre/a.
    """
    if not md:
        return ""
    s = _strip_latex(md)
    s = _html.escape(s, quote=False)

    # Markdown tables → <pre> con padding monospace (Telegram non supporta
    # `<table>` nativo; HTTP chat rende il `<pre>` come block code OK).
    # Pattern: header riga `|...|` + separator `|---|---|` + 1+ righe `|...|`.
    s = _md_tables_to_pre(s)

    def _codeblock(m: re.Match) -> str:
        body = m.group(1).lstrip("\n").rstrip("\n")
        return f"<pre>{body}</pre>"
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", _codeblock, s, flags=re.DOTALL)

    s = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)

    s = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_\n]+?)__", r"<b>\1</b>", s)

    # Italic _text_ (single underscore). Boundary su word-char per non
    # rompere snake_case: il `_` deve avere non-word a sx e a dx.
    # Es. "vedi agent_runtime.py" NON matcha; "...risposta. _elapsed: 12s_"
    # SI matcha. Usato per metadata in coda al messaggio e enfasi leggera.
    s = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", s)

    def _link(m: re.Match) -> str:
        text, url = m.group(1), m.group(2)
        href = _safe_href(url)
        if href is None:
            # Schema non consentito (javascript:/data:/...): scarta l'href,
            # emette solo il testo (gia' html-escaped). No XSS.
            return text
        href = href.replace('"', "%22")
        return f'<a href="{href}">{text}</a>'
    s = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", _link, s)

    s = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", s)

    return s


# --- Full HTML rendering for browser channels (ADR 0110) ----------------------
#
# `to_safe_html_full` produce HTML completo per browser (HTTP chat). Estende
# `to_safe_html` (subset Telegram) con table/heading/list/blockquote/hr veri.
# La whitelist finale dei tag emessi e':
#   b, strong, i, em, u, code, pre, a,
#   h1, h2, h3, h4, h5, h6,
#   ul, ol, li, blockquote, hr,
#   table, thead, tbody, tr, th, td,
#   p, br
# Sicurezza: HTML escape iniziale come in to_safe_html → l'unico HTML
# emesso proviene dal nostro parser markdown (regex + minor state). Niente
# tag attivi (script/iframe/img/style/...). Determinismo §7.9: zero LLM.
#
# Per Telegram USA `to_safe_html()` (subset). Per HTTP browser USA questa.

_MD_TABLE_FULL_RE = _MD_TABLE_BLOCK_RE  # stesso pattern di rilevamento


def _md_tables_to_html(s: str) -> str:
    """Trasforma blocchi tabella markdown in `<table>` veri con allineamento.

    Allineamento da separator: `:---` left, `---:` right, `:---:` center.
    Allineamento applicato come attributo `style="text-align:..."` su
    ogni cella, leggibile da qualunque browser senza CSS extra.
    """
    def _repl(m: "re.Match") -> str:
        block = m.group(1)
        rows_raw = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(rows_raw) < 3:
            return block
        def _split_row(row: str) -> list[str]:
            r = row.strip()
            if r.startswith("|"):
                r = r[1:]
            if r.endswith("|"):
                r = r[:-1]
            return [c.strip() for c in r.split("|")]
        header = _split_row(rows_raw[0])
        sep = _split_row(rows_raw[1])
        body = [_split_row(r) for r in rows_raw[2:]]
        n = len(header)
        align: list[str] = []
        for cell in sep:
            c = cell.strip()
            if c.startswith(":") and c.endswith(":"):
                align.append("center")
            elif c.endswith(":"):
                align.append("right")
            else:
                align.append("left")
        def _attr(i: int) -> str:
            a = align[i] if i < len(align) else "left"
            if a == "left":
                return ""
            return f' style="text-align:{a}"'
        out = ["<table>", "<thead>", "<tr>"]
        for i in range(n):
            cell = header[i] if i < len(header) else ""
            out.append(f"<th{_attr(i)}>{cell}</th>")
        out.append("</tr>")
        out.append("</thead>")
        out.append("<tbody>")
        for row in body:
            out.append("<tr>")
            for i in range(n):
                cell = row[i] if i < len(row) else ""
                out.append(f"<td{_attr(i)}>{cell}</td>")
            out.append("</tr>")
        out.append("</tbody>")
        out.append("</table>")
        rendered = "".join(out)
        # Senza questo newline un elenco/link subito dopo la tabella viene
        # saldato al placeholder HTML e non attraversa il parser Markdown.
        return rendered + ("\n" if re.search(r"\n\s*\Z", block) else "")
    return _MD_TABLE_FULL_RE.sub(_repl, s)


def _apply_inline(s: str) -> str:
    """Inline markdown → HTML su una stringa SINGLE-LINE gia' html-escaped.

    Ordine: code-inline (protegge il contenuto da altri pattern) → bold →
    italic → link. Restituisce HTML inline.
    """
    # `code` inline: protegge da successive sostituzioni dentro il code
    # (poiche' usiamo il marker `<code>...</code>` e i regex successivi
    # non matcheranno backtick). Niente backtick annidati: `[^`\n]+?`.
    s = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", s)
    # bold **...** e __...__
    s = re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_\n]+?)__", r"<b>\1</b>", s)
    # italic _..._ con boundary su non-word (preserva snake_case)
    s = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", s)
    # link [text](url)
    def _link(m: "re.Match") -> str:
        text, url = m.group(1), m.group(2)
        href = _safe_href(url)
        if href is None:
            return text
        href = href.replace('"', "%22")
        return f'<a href="{href}">{text}</a>'
    s = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", _link, s)
    return s


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET_RE = re.compile(r"^(\s*)[*\-]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^(\s*)\d+\.\s+(.+)$")
# Blockquote: il regex matcha il marker DOPO l'escape HTML, quindi `>` e' &gt;
_BLOCKQUOTE_RE = re.compile(r"^&gt;\s+(.+)$")
_HR_RE = re.compile(r"^-{3,}$")
_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")
_CODEFENCE_RE = re.compile(r"^```([a-zA-Z0-9_-]*)\s*$")


def to_safe_html_full(md: str) -> str:
    """Markdown → HTML completo per browser. Estende `to_safe_html` con:

    - heading `# ## ###` → `<h1>` `<h2>` `<h3>` (livello = numero di `#`);
    - hr `---` su riga sola → `<hr>`;
    - blockquote `> ...` (consecutivi merged) → `<blockquote>...</blockquote>`;
    - bullet list `*` o `-` (consecutivi merged) → `<ul><li>...</li></ul>`;
    - numbered list `1. 2. ...` (consecutivi merged) → `<ol><li>...</li></ol>`;
    - table markdown → `<table><thead>...</thead><tbody>...</tbody></table>`;
    - code block triple backtick → `<pre><code>...</code></pre>`;
    - testo plain → `<p>...</p>` (paragrafi separati da blank line).

    Inline su righe non-codice:
    - `**bold**` / `__bold__` → `<b>...</b>`
    - `_italic_` (boundary word) → `<i>...</i>`
    - `` `code` `` → `<code>...</code>`
    - `[text](url)` → `<a href="url">text</a>`

    Sicurezza:
    - HTML escape iniziale di `<`, `>`, `&` (no injection da contenuto).
    - Whitelist tag deliberatamente piccola (vedi modulo).

    Per Telegram USA `to_safe_html()` (subset compatibile parse_mode=HTML).
    Per HTTP browser USA questa.
    """
    if not md:
        return ""
    s = _strip_latex(md)
    s = _html.escape(s, quote=False)

    # Tabelle PRIMA: il regex matcha blocchi multi-riga, va fatto sul
    # testo piatto prima dello split per linee.
    s = _md_tables_to_html(s)

    # Code block triple-backtick: parsing line-based per non rompere.
    # Lo facciamo via DOTALL regex e marker placeholder per estrarli e
    # reinserirli dopo il line-by-line.
    placeholders: list[str] = []
    def _stash_codeblock(m: "re.Match") -> str:
        body = m.group(1).lstrip("\n").rstrip("\n")
        placeholders.append(f"<pre><code>{body}</code></pre>")
        return f"\x00CODEBLOCK{len(placeholders)-1}\x00"
    s = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", _stash_codeblock, s, flags=re.DOTALL)

    # Stash anche i blocchi <table>...</table> gia' generati, cosi' il loop
    # line-based non li tocca (altrimenti li wrapperebbe in <p>).
    def _stash_table(m: "re.Match") -> str:
        placeholders.append(m.group(0))
        return f"\x00TABLE{len(placeholders)-1}\x00"
    s = re.sub(r"<table>.*?</table>", _stash_table, s, flags=re.DOTALL)

    # Line-based parser con state per liste/blockquote/paragrafi.
    lines = s.split("\n")
    out: list[str] = []

    # State: quale blocco contenitore e' aperto in this moment.
    # one of: None, "ul", "ol", "blockquote", "p"
    state: str | None = None
    para_buf: list[str] = []
    blockquote_buf: list[str] = []
    # Stack di indent per <ul>/<ol> nested. Ogni elemento e' lo spazio
    # iniziale del bullet che ha aperto quel livello. Profondita' visiva
    # = len(list_stack); convenzione 2 spazi per livello (markdown standard).
    list_stack: list[int] = []

    def _flush_para():
        nonlocal state, para_buf
        if para_buf:
            # Soft line break GFM-style: ogni `\n` interno al paragrafo
            # diventa `<br>` (non space-join). Aderisce all'aspettativa
            # utente che "riga1\nriga2" appaia su due righe distinte
            # anche su HTTP browser, senza richiedere `\n\n` esplicito.
            # `<br>` letterale e' sicuro perche' iniettato DOPO l'escape
            # iniziale e _apply_inline non lo tocca.
            text = "<br>".join(p.strip() for p in para_buf if p.strip())
            if text:
                out.append(f"<p>{_apply_inline(text)}</p>")
            para_buf = []
        state = None

    def _flush_blockquote():
        nonlocal state, blockquote_buf
        if blockquote_buf:
            text = " ".join(blockquote_buf).strip()
            if text:
                out.append(f"<blockquote>{_apply_inline(text)}</blockquote>")
            blockquote_buf = []
        state = None

    def _close_lists():
        """Chiude tutti i livelli `<ul>`/`<ol>` aperti."""
        while list_stack:
            list_stack.pop()
            out.append("</ul>" if state == "ul" else "</ol>")

    def _close_state():
        nonlocal state
        if state in ("ul", "ol"):
            _close_lists()
        elif state == "blockquote":
            _flush_blockquote()
        elif state == "p":
            _flush_para()
        state = None

    def _adjust_list_depth(indent: int, tag: str):
        """Adatta la profondita' `<ul>`/`<ol>` corrente all'indent del
        bullet corrente. Apre o chiude livelli in base al delta."""
        # Chiudi livelli con indent maggiore (sblocca a target).
        while list_stack and indent < list_stack[-1]:
            out.append(f"</{tag}>")
            list_stack.pop()
        # Apri nuovo livello se indent strictly maggiore dell'ultimo.
        if not list_stack or indent > list_stack[-1]:
            out.append(f"<{tag}>")
            list_stack.append(indent)

    for raw in lines:
        line = raw.rstrip()

        # Placeholder per codeblock/table: chiudi tutto e emit as-is.
        if line.startswith("\x00CODEBLOCK") or line.startswith("\x00TABLE"):
            _close_state()
            out.append(line)
            continue

        # Blank line → chiude paragrafo/blockquote, mantiene lista.
        if not line.strip():
            if state in ("p", "blockquote"):
                _close_state()
            elif state in ("ul", "ol"):
                # blank line in mezzo a lista → fine lista
                _close_state()
            continue

        # HR
        if _HR_RE.match(line.strip()):
            _close_state()
            out.append("<hr>")
            continue

        # Heading
        m = _HEADING_RE.match(line.strip())
        if m:
            _close_state()
            level = len(m.group(1))
            text = _apply_inline(m.group(2).strip())
            out.append(f"<h{level}>{text}</h{level}>")
            continue

        # Bullet list (con indentazione → nested <ul>)
        m = _BULLET_RE.match(line)
        if m:
            indent = len(m.group(1))
            text = m.group(2).strip()
            if state != "ul":
                _close_state()
                state = "ul"
            _adjust_list_depth(indent, "ul")
            out.append(f"<li>{_apply_inline(text)}</li>")
            continue

        # Numbered list (con indentazione → nested <ol>)
        m = _NUMBERED_RE.match(line)
        if m:
            indent = len(m.group(1))
            text = m.group(2).strip()
            if state != "ol":
                _close_state()
                state = "ol"
            _adjust_list_depth(indent, "ol")
            out.append(f"<li>{_apply_inline(text)}</li>")
            continue

        # Blockquote
        m = _BLOCKQUOTE_RE.match(line.strip())
        if m:
            if state != "blockquote":
                _close_state()
                state = "blockquote"
            blockquote_buf.append(m.group(1).strip())
            continue

        # Plain text → paragrafo
        if state != "p":
            _close_state()
            state = "p"
        para_buf.append(line.strip())

    _close_state()

    result = "\n".join(out)

    # Restore placeholders
    def _restore(m: "re.Match") -> str:
        idx = int(m.group(2))
        if 0 <= idx < len(placeholders):
            return placeholders[idx]
        return m.group(0)
    result = re.sub(r"\x00(CODEBLOCK|TABLE)(\d+)\x00", _restore, result)

    # I link della chat aprono una pagina esterna senza sostituire la chat
    # corrente. Oltre alla continuita' UX, evita che il ritorno col tasto Back
    # debba ricostruire stream SSE e storico della conversazione.
    result = re.sub(
        r'<a href="([^"]+)">',
        r'<a href="\1" target="_blank" rel="noopener noreferrer">',
        result,
    )

    return result


_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")


def strip_html(html_text: str) -> str:
    """Rimuove ogni tag HTML, restituendo testo plain. Le entita' restano
    (e.g. `&lt;` rimane `&lt;`); per de-escape applicare `html.unescape`."""
    if not html_text:
        return ""
    return _TAG_RE.sub("", html_text)


def to_plain_text(md: str) -> str:
    """Markdown → plain text (Markdown rimosso, entita' decodificate).
    Utile come fallback per canali che non supportano HTML."""
    return _html.unescape(strip_html(to_safe_html(md)))
