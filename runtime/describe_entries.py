"""runtime.describe_entries — builtin LLM-augmented summariser.

Pattern terza categoria di executor (28/4/2026 sera, ratificato in
`feedback_llm_augmented_executors`): vive nel runtime, no manifest su
disco, no subprocess. Wrapper sottile sopra `runtime.llm_helpers.call_llm`:
seleziona un prompt template in base allo style, lascia tutto il
trasporto all'helper.

Style preset:
- 'by_importance' — segnale vs rumore, raggruppa per affinita'. Chiude
  con un'affermazione, MAI con una domanda all'utente (no-forced-response:
  non esiste dialogo pendente che accolga la risposta). Default per
  liste eterogenee (mail, file, eventi).
- 'by_relevance'  — risposta a una richiesta utente (`context`
  obbligatorio): cosa risponde alla domanda, cosa no.
- 'compact'       — una riga per entry (fallback enumerativo).

Override esplicito: passare `prompt_override` come system prompt
completo (lo style viene ignorato).
"""
from __future__ import annotations

import json
import os
import re

from llm_helpers import call_llm
import prompt_loader
from config import DEFAULT_LANG
from messages import get as _msg

# Lista degli style preset disponibili. I prompt sono persistiti in
# `runtime/prompts/<lang>/describe_entries_<style>.j2` (ADR 0092 Phase 2)
# e caricati via `prompt_loader.get(role, lang, **vars)`.
STYLES = ("by_importance", "by_relevance", "compact")

# Cap DINAMICO verso il prompt LLM (12/6/2026, sostituisce il fisso
# _DESCRIBE_CAP=20). Il vincolo reale e' la DIMENSIONE serializzata del
# bundle — context bloat + il modello generalizza/si perde su bundle
# grossi — NON il numero di entries. Un cap a conteggio fisso tagliava
# per un solo elemento di troppo (es. 21 mail corte -> "1 fuori"),
# rischiando di perdere roba importante per nulla: 21 mail ~7 KB stanno
# larghe, il problema vero era il turn 98 KB con 100 entries scene-rumore
# (find_images_indices, diagnosi 8/5). Soluzione di classe (§7.3): pack
# greedy fino a un budget di caratteri (proxy dei token), con un tetto di
# sicurezza sul conteggio per evitare flood di entries minuscole. Sotto
# budget: nessun troncamento. Le entries oltre il cap NON sono dimenticate
# (`item_count` = totale + `truncated*` field §2.7). Override via env.
# Budget alzato 24K->48K (12/6/2026, Roberto): ~60 mail reali (~571-800 B/cad)
# stanno nel budget prima di troncarne una — il describe deterministico regge
# ~12-16K token, ben sotto il ctx 131072. Tunabile via env.
_DESCRIBE_MAX_CHARS = int(os.environ.get("METNOS_DESCRIBE_MAX_CHARS", "48000"))
_DESCRIBE_HARD_MAX = int(os.environ.get("METNOS_DESCRIBE_HARD_MAX", "200"))

# Map-reduce OVER-BUDGET (22/6/2026, Roberto «robusto, universale, efficiente»):
# quando il bundle sfora il budget, invece di troncare e DIMENTICARE la coda
# (prima N in ordine d'arrivo = arbitrario), si fa map-reduce GENERALE (§7.3,
# vale per mail/file/issue/processi):
#   MAP   — una passata `fast` PER ELEMENTO: resume breve + punteggio di
#           salienza (0-100). Una entry alla volta non sfora MAI il budget
#           (elimina il problema alla radice — Roberto), output corto, N volte.
#   REDUCE— una `middle` sintetizza i digest (piccoli → stanno nel budget),
#           ordinati per salienza; se i digest stessi sforano (N enorme),
#           ricorsione GERARCHICA (riassunto-di-riassunti) fino a convergenza.
# Copre TUTTE le entries (niente droppato §2.8). Scatta SOLO over-budget: il
# caso comune (sotto budget) resta la singola chiamata di prima, invariato.
# Determinismo §11: il path map-reduce e' N+1 chiamate HTTP fast/middle
# (efficiente, no processo monouso ×N) → NON byte-riproducibile, dichiarato
# onestamente `meta.deterministic=False` (come il fallback HTTP §11).
_DESCRIBE_MAPREDUCE = os.environ.get("METNOS_DESCRIBE_MAPREDUCE", "1").strip() != "0"
_MR_MAX_DEPTH = int(os.environ.get("METNOS_DESCRIBE_MR_DEPTH", "3"))
# Campi-identita' da preservare nei digest (per la sintesi REDUCE + link
# section ADR 0119). Dominio-agnostici: mail (subject/from), file (path/name),
# issue/url (url/title), eventi (when/date).
_DIGEST_ID_FIELDS = ("subject", "from", "sender", "title", "name",
                     "url", "path", "date", "when", "account")

# Testo DETERMINISTICO per costruzione (12/6/2026): stessa lista di entries
# -> testo IDENTICO byte-a-byte su run ripetuti. Il path HTTP del llama-server
# condiviso NON e' riproducibile (stato di processo, vedi llm_helpers blocco
# DETERMINISTICA); describe passa deterministic=True a call_llm, che genera
# via processo llama-completion monouso (stesso GGUF, stesso template,
# temp=0, seed §11). NIENTE cache/template del contenuto: la sintesi resta
# LLM piena sui dati correnti. Fallback HTTP onesto se il path manca
# (meta.deterministic=False). Opt-out: METNOS_DESCRIBE_DETERMINISTIC=0.
_DESCRIBE_DETERMINISTIC = (
    os.environ.get("METNOS_DESCRIBE_DETERMINISTIC", "1").strip() != "0"
)


def _pack_entries(entries: list) -> tuple[list, bool]:
    """Greedy: include entries in ordine finche' la dimensione serializzata
    sta nel budget caratteri e non si supera il tetto di sicurezza sul
    conteggio. Almeno 1 entry passa sempre (anche se da sola sfora). Ritorna
    (visible, truncated)."""
    visible: list = []
    total_chars = 0
    for e in entries:
        try:
            sz = len(json.dumps(e, ensure_ascii=False))
        except Exception:
            sz = len(str(e))
        if visible and (total_chars + sz > _DESCRIBE_MAX_CHARS
                        or len(visible) >= _DESCRIBE_HARD_MAX):
            break
        visible.append(e)
        total_chars += sz
    return visible, len(visible) < len(entries)


def _fallback_resume(entry) -> str:
    """Resume deterministico senza LLM (fallback se la MAP fallisce)."""
    if isinstance(entry, dict):
        for k in ("body_text", "content", "body", "text", "snippet",
                  "subject", "title", "name"):
            v = entry.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()[:300]
    return json.dumps(entry, ensure_ascii=False)[:300]


# Tetto input per il MAP: per giudicare salienza + 1 frase NON serve la mail
# intera. Tagliare i campi testuali lunghi a ~1500 char accelera il prompt
# processing senza intaccare il giudizio (subject/mittente + incipit bastano).
# NB (22/6): il batch del MAP e' stato PROVATO e SCARTATO — su GPU seriale il
# tempo e' legato ai token totali, non al numero di chiamate (§7.4): batch =
# stesso lavoro, +complessita'. Per-mail e' piu' semplice (§7.2) e pari-veloce.
_MAP_FIELD_CHARS = int(os.environ.get("METNOS_DESCRIBE_MAP_FIELD_CHARS", "1500"))
_MAP_LONG_FIELDS = ("body_text", "content", "body", "text", "snippet", "html")


def _trim_for_map(entry):
    """Copia shallow dell'entry coi campi testuali lunghi troncati a
    _MAP_FIELD_CHARS — solo per il MAP, l'entry originale resta intatto."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    for k in _MAP_LONG_FIELDS:
        v = out.get(k)
        if isinstance(v, str) and len(v) > _MAP_FIELD_CHARS:
            out[k] = v[:_MAP_FIELD_CHARS]
    return out


def _parse_salience(text: str, entry) -> tuple[int, str]:
    """Estrae (salienza 0-100, resume) dall'output MAP. Robusto §2.4: se il
    formato non torna, salienza neutra 50 + resume = testo/fallback."""
    score = 50
    m = re.search(r'(?:SALIENZA|SALIENCE)\s*[:=]\s*(\d{1,3})', text, re.I)
    if m:
        score = max(0, min(100, int(m.group(1))))
    m2 = re.search(r'(?:RIASSUNTO|SUMMARY)\s*[:=]\s*(.+)', text, re.I | re.S)
    resume = (m2.group(1).strip() if m2 else (text or "").strip())
    if not resume:
        resume = _fallback_resume(entry)
    return score, resume[:600]


def _map_one(entry, map_prompt: str) -> tuple[int, str]:
    """MAP di UN elemento: chiamata `fast` HTTP (efficiente, no processo
    monouso), input troncato, output corto. Ritorna (salienza, resume).
    Fail-open §2.8."""
    try:
        text, _meta = call_llm([_trim_for_map(entry)], map_prompt, tier="fast",
                               max_tokens=140, deterministic=False,
                               max_query_chars=_MAP_FIELD_CHARS + 2048)
    except Exception:
        return 50, _fallback_resume(entry)
    return _parse_salience(text, entry)


def _describe_map_reduce(entries: list, *, style: str, context: str,
                         data_kind, fmt: str, group_by, max_tokens: int,
                         health_context, mr_depth: int) -> dict:
    """Over-budget describe via map-reduce (§7.3, vedi blocco costanti).
    MAP per-elemento (resume + salienza), copre TUTTE le entries, ordina per
    salienza; ricorsione gerarchica se i digest sforano. NON byte-determ."""
    map_prompt = prompt_loader.get("describe_map_salience", DEFAULT_LANG,
                                   context=context or "")
    digests: list = []
    for e in entries:
        score, resume = _map_one(e, map_prompt)
        d = {}
        if isinstance(e, dict):
            for k in _DIGEST_ID_FIELDS:
                if e.get(k) is not None:
                    d[k] = e[k]
        d["content"] = resume
        d["_salience"] = score
        digests.append(d)
    digests.sort(key=lambda x: x.get("_salience", 0), reverse=True)
    # REDUCE: describe normale sui digest (piccoli → singola chiamata; se
    # sforano ancora, ricorre map-reduce a mr_depth+1 = gerarchico). tier
    # `middle`, non deterministico (path efficiente).
    res = handle_describe_entries({
        "entries": digests, "style": style, "context": context,
        "data_kind": data_kind, "format": fmt, "group_by": group_by,
        "tier": "middle", "max_tokens": max_tokens,
        "health_context": health_context,
    }, _mr_depth=mr_depth + 1, _deterministic=False)
    if isinstance(res, dict) and res.get("ok"):
        # Coperte TUTTE: niente troncamento, item_count = totale reale.
        res["item_count"] = len(entries)
        for k in ("truncated", "truncated_what", "used", "available_total",
                  "cap_field", "cap_value"):
            res.pop(k, None)
        res["map_reduce"] = True
        res["mapped"] = len(entries)
        res["deterministic"] = False
    return res

# Direttive di formattazione applicate in append al prompt principale.
# Cosi' il chiamante puo' chiedere lo stesso riassunto in markdown
# (default Telegram), HTML, plain, o JSON strutturato — senza
# duplicare i prompt template.
FORMAT_DIRECTIVES = {
    "markdown": (
        "FORMATO OUTPUT: markdown leggero. Bullet list `* ` per "
        "elenchi, **grassetto** per evidenziare, niente tabelle "
        "complesse. Compatibile con Telegram MarkdownV2/HTML mixed."
    ),
    "html": (
        "FORMATO OUTPUT: HTML semplice supportato da Telegram Bot API: "
        "<b>grassetto</b>, <i>corsivo</i>, <code>monospace</code>, "
        "<a href=\"...\">link</a>. Niente <ul>/<li>: per elenchi usa "
        "righe separate da \\n con prefisso `• `. Niente tag esotici."
    ),
    "plain": (
        "FORMATO OUTPUT: testo piano. NIENTE markdown, NIENTE HTML, "
        "NIENTE simboli decorativi. Frasi pulite, paragrafi separati "
        "da una riga vuota se servono."
    ),
    "json": (
        "FORMATO OUTPUT: un singolo oggetto JSON valido con i campi "
        "{summary: string, highlights: [string], total: int, "
        "noise_filtered: int}. Niente prosa fuori dal JSON."
    ),
    "bullet_list": (
        "FORMATO OUTPUT: solo una bullet list (`* `), una riga per "
        "punto, senza prefazione ne' chiusa. Massimo 10 punti."
    ),
}


def _auto_tier(entries: list) -> str:
    """Sceglie il tier in base alla dimensione del bundle serializzato.
    Heuristica conservativa: testi corti reggono col tier fast,
    contenuti medi vanno a middle, bundle grossi a wise (per context
    + qualita' di sintesi su molti item)."""
    try:
        size = len(json.dumps(entries, ensure_ascii=False))
    except Exception:
        size = 0
    n = len(entries)
    if size < 5_000 and n <= 10:
        return "fast"
    if size < 30_000 and n <= 50:
        return "middle"
    return "wise"


_LINK_SECTION_TITLE = {
    "it": "Link diretti",
    "en": "Direct links",
}
_PATHS_SECTION_TITLE = {
    "it": "Path",
    "en": "Paths",
}
_MAX_LINKS_APPENDED = 10


def _maybe_append_link_section(text: str, entries: list,
                               fmt: str, kind: str) -> str:
    """Append elenco link/path al summary se le entries li hanno.

    Logica deterministica:
    - Solo per fmt in {markdown, html, plain} (non json/bullet_list).
    - Salta se le top-5 entries non hanno `url` o `path`.
    - Salta se il LLM ha gia' citato la maggior parte dei top URL/path
      nel summary (heuristic: 60%+ match).
    - Sanitize titoli per evitare break del markdown (`[`, `]`, `\\n`).
    """
    if not isinstance(text, str) or not entries:
        return text
    if fmt in ("json", "bullet_list"):
        return text

    top = [e for e in entries[:_MAX_LINKS_APPENDED]
           if isinstance(e, dict) and (e.get("url") or e.get("path"))]
    if not top:
        return text

    # Estrai URL gia' citati nel testo. Greedy fino a whitespace/angle/quote,
    # poi strip trailing punctuation (.,;:!?)).
    raw_urls = re.findall(r"https?://[^\s<>\"']+", text)
    cited_urls = {u.rstrip(".,;:!?)") for u in raw_urls}

    sample = top[:5]
    sample_urls = [e.get("url") for e in sample if e.get("url")]
    sample_paths = [e.get("path") for e in sample if e.get("path")]

    # Conteggio match
    n_url_cited = sum(1 for u in sample_urls if u in cited_urls)
    n_path_cited = sum(1 for p in sample_paths
                       if isinstance(p, str) and p in text)
    n_top = max(1, len(sample_urls) + len(sample_paths))
    coverage = (n_url_cited + n_path_cited) / n_top
    if coverage >= 0.6:
        return text  # LLM ha gia' citato abbastanza link

    lang = (DEFAULT_LANG or "it").split("-")[0].lower()
    has_urls = bool(sample_urls)
    title = (_LINK_SECTION_TITLE if has_urls else _PATHS_SECTION_TITLE).get(
        lang, _LINK_SECTION_TITLE["en"]
    )

    items: list[str] = []
    for e in top:
        u = e.get("url")
        p = e.get("path")
        label = (e.get("title") or e.get("name")
                 or (u or p or "")).replace("[", "(").replace("]", ")")
        label = label.replace("\n", " ").strip() or "(no title)"
        if u and isinstance(u, str) and u.startswith(("http://", "https://")):
            items.append(f"- [{label}]({u})")
        elif p and isinstance(p, str):
            items.append(f"- `{p}` — {label}")
    if not items:
        return text

    if fmt == "html":
        block_lines = [f"<p><b>{title}</b></p><ul>"]
        for e in top:
            u = e.get("url")
            p = e.get("path")
            label = (e.get("title") or e.get("name")
                     or (u or p or ""))
            label = (label.replace("&", "&amp;")
                          .replace("<", "&lt;")
                          .replace(">", "&gt;"))
            if u and isinstance(u, str) and u.startswith(("http://", "https://")):
                block_lines.append(f'<li><a href="{u}">{label}</a></li>')
            elif p and isinstance(p, str):
                block_lines.append(f"<li><code>{p}</code> — {label}</li>")
        block_lines.append("</ul>")
        block = "\n".join(block_lines)
    else:
        # markdown / plain
        block = f"**{title}**:\n" + "\n".join(items)

    return text.rstrip() + "\n\n" + block


# Direttiva di raggruppamento ESPLICITO (12/6/2026, clausola «ordina/
# raggruppa per X» — vedi runtime/ordering_clause.py): quando il chiamante
# passa `group_by`, la chiave richiesta dall'utente VINCE sul raggruppamento
# intrinseco per affinità/tema dei prompt by_importance/by_relevance.
# Deterministica §7.9: la STRUTTURA (campo risolto, sezioni, ordine, conteggi)
# è calcolata in codice e prescritta al LLM; al modello resta solo la sintesi
# del contenuto di ciascuna sezione. Soglia sezioni: pochi valori distinti →
# sezioni esplicite; molti (chiave quasi-unica, es. data) → presentazione
# nell'ordine dato citando la chiave.
_GROUP_SECTIONS_MAX = 12


def _build_group_directive(key_text: str, entries: list) -> str:
    """Direttiva prompt deterministica per `group_by`. Risolve la chiave
    utente nel campo reale (ordering_clause.resolve_field); se nessun campo
    plausibile (chiave concettuale, es. 'tema') prescrive il raggruppamento
    per quel concetto. IT/EN come _LINK_SECTION_TITLE (direttiva LLM-facing,
    non user-facing: fuori dal vincolo i18n DB §11)."""
    lang = (DEFAULT_LANG or "it").split("-")[0].lower()
    try:
        from ordering_clause import resolve_field
        fld = resolve_field(key_text, entries)
    except Exception:
        fld = None
    if fld is None:
        if lang == "it":
            return (
                f"RAGGRUPPAMENTO RICHIESTO DALL'UTENTE — vince su ogni "
                f"altra istruzione di raggruppamento (affinita'/tema).\n"
                f"DEVI: organizzare il riassunto in sezioni per "
                f"'{key_text}'.\n"
                f"NON DEVI: raggruppare per un criterio diverso da "
                f"'{key_text}'.")
        return (
            f"USER-REQUESTED GROUPING — overrides any other grouping "
            f"instruction (affinity/topic).\n"
            f"YOU MUST: organize the summary into sections by "
            f"'{key_text}'.\nYOU MUST NOT: group by any other criterion.")
    ordered_values: list[str] = []
    counts: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        v = e.get(fld)
        v = "?" if v in (None, "") else str(v)
        if v not in counts:
            ordered_values.append(v)
        counts[v] = counts.get(v, 0) + 1
    n_vals = len(ordered_values)
    if 2 <= n_vals <= _GROUP_SECTIONS_MAX and n_vals < len(entries):
        sections = ", ".join(f"'{v}' ({counts[v]})" for v in ordered_values)
        if lang == "it":
            return (
                f"RAGGRUPPAMENTO RICHIESTO DALL'UTENTE — vince su ogni "
                f"altra istruzione di raggruppamento (affinita'/tema).\n"
                f"DEVI: organizzare il riassunto in {n_vals} sezioni, una "
                f"per ciascun valore del campo '{fld}', in quest'ordine: "
                f"{sections}. Ogni sezione inizia con il valore in "
                f"grassetto.\n"
                f"NON DEVI: raggruppare per tema ne' mescolare nella stessa "
                f"sezione entries con valori diversi di '{fld}'.")
        return (
            f"USER-REQUESTED GROUPING — overrides any other grouping "
            f"instruction (affinity/topic).\n"
            f"YOU MUST: organize the summary into {n_vals} sections, one "
            f"per value of field '{fld}', in this order: {sections}. "
            f"Start each section with the value in bold.\n"
            f"YOU MUST NOT: group by topic or mix entries with different "
            f"'{fld}' values in the same section.")
    if lang == "it":
        return (
            f"ORDINAMENTO RICHIESTO DALL'UTENTE — vince su ogni altra "
            f"istruzione di raggruppamento.\n"
            f"DEVI: presentare le entries nell'ordine dato (sono gia' "
            f"ordinate per '{fld}'), citando il valore di '{fld}'.\n"
            f"NON DEVI: riordinarle ne' raggrupparle per tema.")
    return (
        f"USER-REQUESTED ORDERING — overrides any other grouping "
        f"instruction.\nYOU MUST: present the entries in the given order "
        f"(already sorted by '{fld}'), citing the '{fld}' value.\n"
        f"YOU MUST NOT: reorder them or group by topic.")


def _detect_kind(entries: list, hint: str | None) -> str:
    """Determina il `kind` semantico delle entries: hint esplicito >
    campo `kind` uniforme nelle entries > euristica > 'generic'."""
    if hint:
        return hint
    kinds = {e.get("kind") for e in entries if isinstance(e, dict)}
    kinds.discard(None)
    if len(kinds) == 1:
        return next(iter(kinds))
    if len(kinds) > 1:
        return "mixed"
    # Euristica leggera sui campi per inferire dominio
    first = entries[0] if entries else {}
    if isinstance(first, dict):
        if "from" in first and "subject" in first:
            return "email"
        if "url" in first and ("title" in first or "snippet" in first):
            return "web_result"
        if "path" in first:
            return "file"
    return "generic"


DESCRIBE_ENTRIES_TOOL = {
    "type": "function",
    "function": {
        "name": "describe_entries",
        "description": (
            "Riassume lista di entries via LLM interno. Args: from_step=N, "
            "style in {by_importance|by_relevance|compact}. Cap dinamico a "
            "dimensione del bundle (non a conteggio): le entries vengono "
            "inviate al prompt finche' stanno nel budget caratteri; sotto "
            "budget passano tutte. Se si supera, il summary cita il "
            "troncamento e l'output include `truncated=True`, "
            "`cap_field='describe_cap'`, `cap_value=<n inviate>`, "
            "`used=<n inviate>`, `available_total=<total>`.\n"
            "DEVI: chiamare describe_entries SOLO se l'utente chiede "
            "riassunto di una lista nel suo insieme.\n"
            "NON DEVI: chiamare describe_entries se l'utente cita campi "
            "specifici da elencare per entry. Vai a final_answer.\n"
            "OK: 'riassumi le mail di oggi'.\n"
            "OK: 'sintetizza i log degli ultimi 10 minuti'.\n"
            "OK: 'punti importanti dei 30 risultati di ricerca'.\n"
            "ERRORE: 'dimmi mittenti e dimensioni delle 5 mail piu' grandi'.\n"
            "ERRORE: 'mostra path e mtime dei file modificati oggi'.\n"
            "ERRORE: 'top-3 mail per size con sender'.\n"
            "Pattern (NON copiare letteralmente): per richieste con campi "
            "espliciti, leggi entries da scratchpad e formula final_answer."
        ),
        "parameters": {
            "type": "object",
            "required": ["from_step"],
            "properties": {
                "from_step": {
                    "type": "integer",
                    "description": "Numero dello step precedente (in questo "
                                   "turno) che ha prodotto la lista da "
                                   "riassumere. Es. se al passo 1 hai "
                                   "chiamato read_messages, qui passi "
                                   "from_step=1.",
                    "minimum": 1,
                },
                "style": {
                    "type": "string",
                    "description": "Preset di prompt: 'by_importance' (default), "
                                   "'by_relevance' (richiede context), 'compact'.",
                    "enum": ["by_importance", "by_relevance", "compact"],
                },
                "context": {
                    "type": "string",
                    "description": "Per style='by_relevance': la richiesta originale "
                                   "dell'utente, da usare come metro di pertinenza.",
                },
                "group_by": {
                    "type": "string",
                    "description": "Chiave di raggruppamento RICHIESTA "
                                   "dall'utente (es. 'mailbox', 'mittente', "
                                   "'size'): l'output viene organizzato in "
                                   "sezioni/ordine per quella chiave e VINCE "
                                   "sul raggruppamento intrinseco per tema. "
                                   "Risolta sul campo reale delle entries.",
                },
                "data_kind": {
                    "type": "string",
                    "description": "Tipo semantico delle entries (es. 'email', "
                                   "'web_result', 'log_line', 'file'). Se "
                                   "omesso, viene dedotto dai campi 'kind' "
                                   "delle entries o euristicamente. Sovrascrive "
                                   "il 'kind' per-entry nel prompt.",
                },
                "format": {
                    "type": "string",
                    "description": "Formato di output desiderato. 'markdown' "
                                   "(default, leggero, Telegram-friendly), "
                                   "'html' (Telegram parse_mode HTML), "
                                   "'plain' (no markup), 'bullet_list' (solo "
                                   "elenco puntato), 'json' (oggetto strutturato).",
                    "enum": ["markdown", "html", "plain", "bullet_list", "json"],
                },
                "tier": {
                    "type": "string",
                    "description": "Tier LLM da usare. Default 'auto': sceglie "
                                   "fast/middle/wise in base alla dimensione "
                                   "del bundle (entries corte → fast, medie → "
                                   "middle, lunghe o numerose → wise). "
                                   "Override esplicito solo se sai che serve.",
                    "enum": ["auto", "fast", "middle", "wise"],
                    "default": "auto",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Tetto per l'output del LLM. Default 600.",
                    "default": 600,
                },
            },
        },
    },
}


def _extract_header(entries):
    """Se entries[0] e' un descriptor `{_meta: True, ...}` lo estrai e
    ritorna (header_dict, rest_entries). Altrimenti (None, entries)."""
    if not entries:
        return None, entries
    head = entries[0]
    if isinstance(head, dict) and head.get("_meta"):
        return head, entries[1:]
    return None, entries


def handle_describe_entries(args, *, verbose: bool = False,
                             _mr_depth: int = 0,
                             _deterministic: bool | None = None) -> dict:
    entries = (args or {}).get("entries")
    if not isinstance(entries, list):
        return {"ok": False, "error": "missing or invalid 'entries' (must be a list)"}

    # Header opzionale come primo elemento: {_meta: True, kind, style,
    # context, max_tokens, prompt_override}. I valori dell'header NON
    # sovrascrivono args espliciti (i kwargs vincono).
    header, entries = _extract_header(entries)
    h = header or {}

    style = (args or {}).get("style") or h.get("style") or "by_importance"
    context = (args or {}).get("context") or h.get("context") or ""
    # Safety net (20/5 v6): style=by_relevance richiede `context` con la
    # query utente per fare un riassunto mirato. Se il PLANNER l'ha
    # dimenticato, ricadiamo deterministicamente a by_importance (segnale
    # vs rumore, non richiede context). Evita output del tipo "query
    # dell'utente vuota '', non posso rispondere".
    if style == "by_relevance" and not (context and context.strip()):
        style = "by_importance"
    data_kind = (args or {}).get("data_kind") or h.get("kind") or h.get("data_kind")
    # max_tokens adattivo per dimensione bundle (era 600 fisso → 400 → scala):
    # N=1-3 → 200, N=4-10 → 300, N>10 → 400. Riduce KV-cache allocation
    # llama-server proporzionalmente al target output reale (3-5 righe). Caller
    # puo' override esplicito.
    _explicit_max = (args or {}).get("max_tokens") or h.get("max_tokens")
    if _explicit_max is not None:
        max_tokens = int(_explicit_max)
    else:
        _n_ent = len(entries) if isinstance(entries, list) else 0
        if _n_ent <= 3:
            max_tokens = 200
        elif _n_ent <= 10:
            max_tokens = 300
        else:
            max_tokens = 400
    prompt_override = (args or {}).get("prompt_override") or h.get("prompt_override")
    group_by = (args or {}).get("group_by") or h.get("group_by") or ""
    fmt = (args or {}).get("format") or h.get("format") or "markdown"
    tier = (args or {}).get("tier") or h.get("tier") or "auto"
    # ADR 0111 (7/5/2026): Level 2 — describe_entries deve sapere se la
    # sorgente (`from_step`) aveva un blocco `health` (load/memoria/dischi/
    # servizi). Senza questa visibilita' il LLM dichiarerebbe "non
    # disponibile" su tutti i campi salute (vedono solo le entries =
    # processi). Il runtime inietta `health_context` (dict) come
    # informazione contestuale che viene PRE-pendata al prompt LLM con
    # istruzione esplicita di non re-discutere salute.
    health_context = (args or {}).get("health_context") or h.get("health_context")
    # tier 'auto' risolto DOPO il pack (dimensiona sul bundle realmente
    # inviato `visible_entries`, non sul totale pre-cap).

    # §2.4 robustezza NL→determinismo: l'LLM confonde gli enum e a volte mette
    # un valore di `format` (es. 'bullet_list') nello `style`. NON far crashare
    # il turno (regressione live: «quali sono i task» → list_tasks OK ma describe
    # rigettava 'bullet_list' e l'errore diventava la risposta). Degrade
    # deterministico: (1) se è un format valido messo nel posto sbagliato,
    # spostalo in `fmt`; (2) ricadi sempre sullo style di default. Mai hard-fail.
    _FORMATS = ("markdown", "html", "plain", "bullet_list", "json")
    if style not in STYLES and not prompt_override:
        if style in _FORMATS:
            fmt = style           # era un format messo nel posto sbagliato
        style = "by_importance"   # default robusto, mai crash

    if not entries:
        return {"ok": True, "summary": "", "item_count": 0, "style": style,
                "data_kind": data_kind or "generic",
                "in_tokens": 0, "out_tokens": 0, "latency_ms": 0}

    # ADR 0153 (19/5/2026 v6): content fetch on-demand. Se le entries
    # hanno SOLO url+title+snippet (tipicamente output di find_urls) e
    # nessun campo testuale (content/body/text), describe_entries NON
    # puo' sintetizzare contenuto reale — ricadrebbe in enumerazione di
    # metadata. Dichiara strutturalmente la mancanza con
    # `error_class=needs_content_fetch`; il runtime auto-injecta
    # `read_urls_html` sui top URL e ri-chiama describe_entries con
    # entries arricchite. Pattern install_on_demand (ADR 0143).
    # Campi testuali considerati "contenuto sufficiente" per la sintesi.
    # SOLO testo realmente sintetizzabile:
    # - content/body/text: convenzioni generali
    # - body_text: read_urls_html canonical HTML fetch
    # Snippet ESCLUSO: i find_urls snippets sono preview SEO 100-200 char,
    # non sintetizzabili a riassunto informativo. La presenza di soli
    # snippet trigger needs_content_fetch -> read_urls_html sui top URL.
    _CONTENT_FIELDS = ("content", "body", "text", "body_text")
    # Soglia minima di contenuto sintetizzabile (caratteri):
    # - snippet di search (100-200 char) → NON sufficiente
    # - paragrafo singolo (~300 char) → marginale
    # - 500 char ≈ ~80 parole / 4-5 frasi → contenuto reale.
    # Override via env per tuning durante bench, default conservativo.
    _CONTENT_MIN_CHARS = int(
        os.environ.get("METNOS_DESCRIBE_MIN_CHARS", "500")
    )
    def _has_content(e: dict) -> bool:
        for k in _CONTENT_FIELDS:
            v = e.get(k)
            if isinstance(v, str) and len(v.strip()) >= _CONTENT_MIN_CHARS:
                return True
        return False
    _has_textual_content = any(
        _has_content(e) for e in entries if isinstance(e, dict)
    )
    if _mr_depth == 0 and not _has_textual_content:
        _urls_for_fetch = [
            e["url"] for e in entries
            if isinstance(e, dict)
            and isinstance(e.get("url"), str)
            and e["url"].startswith(("http://", "https://"))
        ][:5]
        if _urls_for_fetch:
            return {
                "ok": False,
                "error_class": "needs_content_fetch",
                "needs_urls_html": _urls_for_fetch,
                "error": (
                    "describe_entries: le entries hanno solo metadata "
                    "(url/title/snippet), nessun contenuto testuale. "
                    "Il runtime interpone read_urls_html sui top URL "
                    "e ri-prova."
                ),
            }

    # Cap dinamico a budget di caratteri (§2.7, §7.3): mandiamo al prompt
    # solo le prime N entries che stanno nel budget e dichiariamo truncated
    # nel return value. Sotto budget: tutte le entries, nessun troncamento.
    total_entries = len(entries)
    visible_entries, truncated_describe = _pack_entries(entries)
    hidden_count = total_entries - len(visible_entries)

    # Over-budget → map-reduce (§7.3, copre TUTTE le entries invece di
    # troncare la coda). Scatta solo se abilitato e sotto il tetto di
    # ricorsione. Sotto budget (truncated_describe=False): path invariato.
    if (truncated_describe and _DESCRIBE_MAPREDUCE
            and _mr_depth < _MR_MAX_DEPTH):
        return _describe_map_reduce(
            entries, style=style, context=context, data_kind=data_kind,
            fmt=fmt, group_by=group_by, max_tokens=max_tokens,
            health_context=health_context, mr_depth=_mr_depth)

    if tier == "auto":
        tier = _auto_tier(visible_entries)

    kind = _detect_kind(visible_entries, data_kind)
    base_prompt = (prompt_override
                   if prompt_override
                   else prompt_loader.get(f"describe_entries_{style}",
                                          DEFAULT_LANG,
                                          n=len(visible_entries), context=context, kind=kind))
    fmt_directive = FORMAT_DIRECTIVES.get(fmt, "")
    # Level 2 (ADR 0111): pre-pend `health_context` quando presente. Il LLM
    # vede un blocco "STATO SERVER GIA' RIASSUNTO" con load/RAM/dischi/
    # servizi formattati e l'istruzione esplicita di non ripeterli ne'
    # dichiararli "non disponibili" — limitati a riassumere le entries
    # (processi) sotto.
    health_directive = ""
    if isinstance(health_context, dict) and health_context:
        try:
            from orchestration import _fmt_health_block  # ADR 0148: package-relative
            block = _fmt_health_block(health_context)
        except Exception:
            block = ""
        if block:
            health_directive = (
                "STATO SERVER GIA' RIASSUNTO (NON RIPETERE, NON DICHIARARE "
                "'NON DISPONIBILE'):\n"
                + block
                + "\n\nIl tuo compito: riassumi SOLO le entries (processi) "
                "sotto. Carico/RAM/Dischi/Servizi sono GIA' nel blocco "
                "sopra, non commentarli, non ripeterli."
            )
    prompt = base_prompt
    if health_directive:
        prompt = health_directive + "\n\n" + prompt
    if fmt_directive:
        prompt = prompt + "\n\n" + fmt_directive
    # Raggruppamento ESPLICITO richiesto dall'utente: appeso per ULTIMO,
    # vince sul raggruppamento intrinseco del prompt preset (affinità/tema).
    if group_by and visible_entries:
        group_directive = _build_group_directive(str(group_by),
                                                 visible_entries)
        if group_directive:
            prompt = prompt + "\n\n" + group_directive

    try:
        # max_query_chars: il budget di pack (_DESCRIBE_MAX_CHARS) deve
        # passare INTERO a call_llm — il default 12000 di _serialize_query
        # troncherebbe in silenzio il bundle a meta' JSON, smentendo i
        # conteggi visible/hidden dichiarati (§2.7/§2.8).
        _det = (_DESCRIBE_DETERMINISTIC if _deterministic is None
                else _deterministic)
        text, meta = call_llm(visible_entries, prompt, tier=tier,
                              max_tokens=max_tokens,
                              deterministic=_det,
                              max_query_chars=_DESCRIBE_MAX_CHARS + 2048)
    except Exception as e:
        return {"ok": False, "error_code": "ERR_EXT_SVC_UNAVAILABLE",
                "error": f"LLM call failed: {type(e).__name__}: {e}"}

    # ADR 0119 (9/5/2026): post-process append "Link diretti" se le entries
    # hanno `url` o `path` E il LLM non li ha gia' citati nel summary.
    # Generale (qualsiasi `kind` con campo url/path), deterministico (regex),
    # rispetta le regole di prompt che vietano elenco letterale (il LLM
    # produce sintesi pulita, il post-process aggiunge i link sotto).
    text = _maybe_append_link_section(text, visible_entries, fmt, kind)

    # Patch 3 (8/5/2026): se truncated, append nota localizzata al summary
    # cosi' l'utente vede subito il cap (UX onesto §2.8) e il PLANNER
    # puo' decidere se ritagliare/rilanciare.
    if truncated_describe:
        try:
            note = _msg("MSG_DESCRIBE_TRUNCATED",
                        visible=len(visible_entries),
                        hidden=hidden_count,
                        cap=len(visible_entries))
        except Exception:
            note = ""
        if text and note:
            text = text.rstrip() + "\n\n" + note
        elif note:
            text = note

    out = {
        "ok": True,
        "summary": text,
        "item_count": total_entries,
        "style": style,
        "data_kind": kind,
        "format": fmt,
        **meta,
    }
    if group_by:
        out["group_by"] = str(group_by)
    if truncated_describe:
        out.update({
            "truncated": True,
            "truncated_what": "describe",
            "used": len(visible_entries),
            "available_total": total_entries,
            "cap_field": "describe_cap",
            "cap_value": len(visible_entries),
        })
    return out


# --- API per chiamate da altri executor (Python diretto, no tool_call) -------

def describe(items, *, style: str | None = None, context: str = "",
             data_kind: str | None = None, max_tokens: int = 600,
             prompt_override: str | None = None) -> str:
    """Funzione di alto livello per altri executor che hanno bisogno di
    riassumere una lista. Ritorna SOLO la stringa di riassunto.

    DUE PATTERN equivalenti per il chiamante:

    1. Args espliciti:
        from describe_entries import describe
        summary = describe(results,
                           style="by_relevance",
                           context="le novita' su X",
                           data_kind="web_result")

    2. Lista con descriptor in testa (piu' ergonomico, una sola "cosa"
       da passare in giro fra executor):
        from describe_entries import describe
        bundle = [
            {"_meta": True, "kind": "web_result",
             "style": "by_relevance",
             "context": "le novita' su X"},
            *results,
        ]
        summary = describe(bundle)

    Solleva RuntimeError se la chiamata fallisce.
    """
    res = handle_describe_entries({
        "entries": items, "style": style, "context": context,
        "data_kind": data_kind, "max_tokens": max_tokens,
        "prompt_override": prompt_override,
    })
    if not res.get("ok"):
        raise RuntimeError(res.get("error", "describe failed"))
    return res["summary"]
