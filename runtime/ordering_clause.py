"""runtime/ordering_clause.py — clausola «ordina/raggruppa per X» onorata
END-TO-END, deterministica e generale (§7.9 codice>LLM, §7.3 classe).

Bug live 12/6/2026 (turni T38/T39): «controlla le mailbox ... ORDINATE PER
MAILBOX» produceva lo STESSO piano della query base (read→describe) — nessun
layer (proposer/autopath/fastpath) traduceva la clausola di ordinamento in
uno step, e describe_entries raggruppava per tema A PRESCINDERE.

Soluzione di classe, tre pezzi in QUESTO modulo (nessun hardcoding di
dominio: la chiave è un parametro, vale per mail/file/eventi/contatti/...):

  1. detect(query)            — parser DETERMINISTICO (regex chiuse IT+EN)
                                della clausola: mode sort|group, key_text,
                                desc. Nessun LLM (§7.9).
  2. resolve_field(key, entries) — risoluzione chiave-utente → campo reale
                                delle entries (match esatto > famiglie di
                                sinonimi chiuse > substring). Condivisa da
                                sort_entries (executor) e describe_entries.
  3. apply_to_framework(...)  — normalizzazione del PIANO (qualunque layer
                                l'abbia prodotto: fastpath/autopath/engine/
                                recovery): inietta `sort_entries(by=key)`
                                prima del presenter terminale e passa
                                `group_by=key` a describe_entries, che vi
                                adegua l'output. Idempotente.

Confine verbi §2.2: la clausola di presentazione è SEMPRE `sort` (riordino
in memoria del risultato) — MAI `order` (riordino PERSISTENTE del corpus).
Il «raggruppamento» richiesto è sort per la chiave + sezioni nell'output
(describe group_by): group_entries resta il merge/dedup di N liste.

Lo step iniettato porta il marker arg `_ordering_clause: true`: i piani
così normalizzati sono query-specific (executor.is_query_specific) →
servibili solo via hash 0a; una query SIMILE senza clausola non eredita
mai l'ordinamento via cosine 0b.
"""
from __future__ import annotations

import re
from typing import Optional

# ── 1. Detection deterministica (regex chiuse IT+EN) ─────────────────────

_KEY_TOKEN = r"[a-zA-Zà-ùÀ-Ù0-9_]+"
_KEY_CAPTURE = rf"(?P<key>{_KEY_TOKEN}(?:\s+{_KEY_TOKEN}){{0,2}})"

# Gap lessicale ammesso fra verbo e preposizione: «ordina I FILE per
# dimensione» (max 4 token, non-greedy: l'adiacenza vince).
_GAP = rf"(?:\s+{_KEY_TOKEN}){{0,4}}?"

# Trigger chiusi. NB: participi/imperativi coperti per suffisso
# (ordinat[aeio], raggruppat[aeio]); «divis[ei]» solo plurale (il singolare
# «diviso per» è aritmetica). Estendere SOLO con forme della stessa classe.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("group", re.compile(
        rf"\b(?:raggruppat[aeio]|raggruppa(?:re|ndo|le|li)?"
        rf"|suddivis[aeio]|suddividi(?:le|li)?|divis[ei])"
        rf"{_GAP}\s+per\s+{_KEY_CAPTURE}", re.IGNORECASE)),
    ("group", re.compile(
        rf"\bgroup(?:ed)?{_GAP}\s+by\s+{_KEY_CAPTURE}", re.IGNORECASE)),
    ("sort", re.compile(
        rf"\b(?:(?:ri)?ordinat[aeio]|(?:ri)?ordina(?:re|ndo|le|li)?)"
        rf"{_GAP}\s+per\s+{_KEY_CAPTURE}", re.IGNORECASE)),
    ("sort", re.compile(
        rf"\bin\s+ordine\s+di\s+{_KEY_CAPTURE}", re.IGNORECASE)),
    ("sort", re.compile(
        rf"\b(?:sort(?:ed)?|order(?:ed)?|arranged?){_GAP}\s+by\s+"
        rf"{_KEY_CAPTURE}", re.IGNORECASE)),
)

# Articoli/possessivi da scartare in testa alla chiave catturata.
_ARTICLES = frozenset({
    "il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una",
    "mio", "mia", "miei", "mie", "loro", "the", "a", "an", "my", "their",
})

# Stop-token: la chiave si interrompe qui (preposizioni, congiunzioni,
# cortesia). «ordina per favore» → chiave vuota → nessuna clausola.
_KEY_STOP = frozenset({
    "di", "del", "della", "dei", "delle", "da", "in", "con", "su", "per",
    "tra", "fra", "e", "ed", "o", "od", "and", "or", "poi", "che", "quindi",
    "favore", "cortesia", "piacere", "me", "esempio", "first", "prima",
    "crescente", "decrescente", "ascendente", "discendente",
    "ascending", "descending", "asc", "desc",
})

_DESC_RE = re.compile(
    r"\b(?:decrescent\w*|discendent\w*|descending|desc|invers[aoie]"
    r"|dal\s+pi[uù]\s+(?:recente|grande|nuovo)"
    r"|pi[uù]\s+(?:recenti|grandi|nuovi|nuove)\s+prima"
    r"|newest\s+first|largest\s+first|biggest\s+first|reverse[d]?)\b",
    re.IGNORECASE)


def _clean_key(raw: str) -> str:
    """Pulisce la chiave catturata: scarta articoli in testa, taglia al
    primo stop-token, max 2 token."""
    tokens = (raw or "").lower().split()
    while tokens and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    out: list[str] = []
    for t in tokens:
        if t in _KEY_STOP:
            break
        out.append(t)
        if len(out) >= 2:
            break
    return " ".join(out)


def detect(query: str) -> Optional[dict]:
    """Clausola di ordinamento/raggruppamento nella query, o None.

    Ritorna {"mode": "sort"|"group", "key_text": str, "desc": bool}.
    Deterministico §7.9: regex chiuse, nessun LLM. Se più clausole, vince
    la prima per posizione nella query.
    """
    if not query:
        return None
    best = None  # (pos, mode, key)
    for mode, rx in _PATTERNS:
        m = rx.search(query)
        if not m:
            continue
        key = _clean_key(m.group("key"))
        if not key:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), mode, key)
    if best is None:
        return None
    return {"mode": best[1], "key_text": best[2],
            "desc": bool(_DESC_RE.search(query))}


# ── 2. Risoluzione chiave-utente → campo reale delle entries ─────────────

# Famiglie chiuse §7.3: termine-utente (IT+EN) → candidati campo in ordine
# di preferenza. Estendere SOLO con campi documentati da executor reali.
_FIELD_FAMILIES: tuple[tuple[frozenset, tuple[str, ...]], ...] = (
    (frozenset({"mailbox", "mailboxes", "casella", "caselle", "account",
                "accounts", "cassetta", "mail"}),
     ("account", "mailbox", "folder", "account_email")),
    (frozenset({"mittente", "mittenti", "sender", "senders", "from", "da"}),
     ("from", "sender", "from_email", "author")),
    (frozenset({"destinatario", "destinatari", "recipient", "recipients",
                "to"}),
     ("to", "recipient")),
    (frozenset({"data", "date", "giorno", "day", "ora", "orario", "time",
                "quando"}),
     ("date", "mtime", "modified_at", "created_at", "timestamp", "start",
      "ts", "time")),
    (frozenset({"dimensione", "dimensioni", "size", "grandezza", "peso"}),
     ("size", "bytes", "size_bytes", "total_bytes", "file_size")),
    (frozenset({"oggetto", "subject", "titolo", "title"}),
     ("subject", "title", "name")),
    (frozenset({"nome", "name", "filename"}),
     ("name", "title", "basename", "filename", "path")),
    (frozenset({"tipo", "type", "formato", "format", "estensione",
                "extension"}),
     ("kind", "type", "content_type", "format", "ext", "extension",
      "mimetype")),
    (frozenset({"cartella", "cartelle", "folder", "directory"}),
     ("folder", "dir", "parent", "directory")),
    (frozenset({"stato", "state", "status"}),
     ("status", "state")),
    (frozenset({"autore", "autori", "author", "authors"}),
     ("author", "from", "sender", "user")),
    (frozenset({"categoria", "categorie", "category", "classe", "class",
                "label", "etichetta", "importanza", "importance"}),
     ("category", "class", "label", "importance")),
)


def _entry_keys(entries: list) -> dict[str, str]:
    """Unione dei campi top-level delle entries (esclusi i tecnici `_*`).
    Ritorna map lowercase→nome reale (primo visto, ordine stabile)."""
    keys: dict[str, str] = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        for k in e.keys():
            if isinstance(k, str) and not k.startswith("_"):
                keys.setdefault(k.lower(), k)
    return keys


def resolve_field(key_text: str, entries: list) -> Optional[str]:
    """Risolve il termine-utente (es. «mailbox», «mittente», «size») nel
    campo REALE presente nelle entries. Catena deterministica:
    match esatto > famiglia di sinonimi > substring (len>=3). None se
    nessun campo plausibile (es. «tema»: concetto, non campo)."""
    kt = (key_text or "").strip().lower()
    if not kt:
        return None
    keys = _entry_keys(entries)
    if not keys:
        return None
    # 1. match esatto (case-insensitive), anche multi-parola → underscore
    for cand in (kt, kt.replace(" ", "_")):
        if cand in keys:
            return keys[cand]
    # 2. famiglie di sinonimi (qualunque token della chiave)
    tokens = set(kt.split()) | {kt}
    for synonyms, candidates in _FIELD_FAMILIES:
        if tokens & synonyms:
            for cand in candidates:
                if cand in keys:
                    return keys[cand]
    # 3. substring conservativa (token>=3 char): «dimension» ↔ «dimensione»
    for tok in sorted(tokens, key=len, reverse=True):
        if len(tok) < 3:
            continue
        for lk, real in keys.items():
            if len(lk) >= 3 and (tok in lk or lk in tok):
                return real
    return None


# ── 3. Normalizzazione del Framework (qualunque layer) ───────────────────

# Marker arg dello step iniettato: (a) idempotenza/diagnosi; (b) il piano
# diventa query-specific (executor.is_query_specific) → 0a-only.
ORDERING_MARKER = "_ordering_clause"

# Presenter terminali: non sono produttori di lista.
_PRESENTER_TOOLS = frozenset({"describe_entries"})

# Consumer di entries che dopo l'iniezione devono leggere dallo step sort.
_ENTRIES_CONSUMER_TOOLS = frozenset({
    "describe_entries", "classify_entries", "extract_entries",
    "filter_entries", "compute_entries", "compare_entries",
})

_STEPREF_SUB_RE = re.compile(r"(\$\{step|\{\{step)(\d+)(\.)")
_STEPSREF_SUB_RE = re.compile(r"(\$\{steps\.)(\d+)(\.)")


def _shift_text_refs(text: str, q: int) -> str:
    """Rinumera i riferimenti `${stepN.x}`/`{{stepN.x}}` (1-based) e
    `${steps.M.x}` (0-based) con N>=q → N+1 (uno step inserito alla
    posizione 1-based q)."""
    if not isinstance(text, str) or "step" not in text:
        return text

    def _bump1(m):
        n = int(m.group(2))
        return f"{m.group(1)}{n + 1 if n >= q else n}{m.group(3)}"

    def _bump0(m):
        n = int(m.group(2))  # 0-based: lo step 1-based è n+1
        return f"{m.group(1)}{n + 1 if (n + 1) >= q else n}{m.group(3)}"

    return _STEPSREF_SUB_RE.sub(_bump0, _STEPREF_SUB_RE.sub(_bump1, text))


def _shift_step_refs(value, q: int):
    """Rinumera ricorsivamente from_step/from_steps int e i placeholder
    stringa dentro args (dict/list/str)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "from_step" and isinstance(v, int):
                out[k] = v + 1 if v >= q else v
            elif k == "from_steps" and isinstance(v, list):
                out[k] = [(x + 1 if isinstance(x, int) and x >= q else x)
                          for x in v]
            else:
                out[k] = _shift_step_refs(v, q)
        return out
    if isinstance(value, list):
        return [_shift_step_refs(v, q) for v in value]
    if isinstance(value, str):
        return _shift_text_refs(value, q)
    return value


def apply_to_framework(framework, query: str, catalog_names=None):
    """Normalizza il piano perché la clausola «ordina/raggruppa per X»
    della query CORRENTE sia onorata. Deterministico §7.9, idempotente,
    universale per qualunque kind di entries (§7.3).

    - nessuna clausola → piano INVARIATO (stesso oggetto).
    - clausola → (a) inietta `sort_entries(by=key_text, desc)` prima del
      presenter terminale (ultimo describe_entries, altrimenti prima di
      final_answer), rinumerando i riferimenti agli step; (b) imposta
      `group_by=key_text` su ogni describe_entries (l'output riflette la
      chiave, vedi describe_entries._build_group_directive).
    - rispetta l'ordinamento ESPLICITO del piano (sort_entries/order_* già
      presenti → niente doppia iniezione, solo group_by).
    - `catalog_names` (set, opzionale): inietta solo se sort_entries è
      invocabile.

    Ritorna un NUOVO Framework se cambia qualcosa, altrimenti l'originale.
    """
    clause = detect(query)
    if not clause:
        return framework
    steps = list(getattr(framework, "steps", None) or [])
    if not steps:
        return framework
    tools = [(s.tool or "") for s in steps]
    has_producer = any(
        t and t != "final_answer"
        and t not in _PRESENTER_TOOLS and t != "sort_entries"
        for t in tools)
    if not has_producer:
        return framework
    has_explicit_order = any(
        t == "sort_entries" or t.startswith("order_") for t in tools)
    can_inject = (catalog_names is None) or ("sort_entries" in catalog_names)

    from engine.types import Framework as _FW, StepSpec as _SS

    new_steps = [
        _SS(tool=s.tool, args=dict(s.args or {}),
            if_prev_entries_nonempty=s.if_prev_entries_nonempty)
        for s in steps
    ]
    final_message = getattr(framework, "final_message", "") or ""
    changed = False

    if not has_explicit_order and can_inject:
        if "describe_entries" in tools:
            p = max(i for i, t in enumerate(tools) if t == "describe_entries")
        elif "final_answer" in tools:
            p = tools.index("final_answer")
        else:
            p = len(tools)
        q = p + 1  # numero 1-based dello step sort iniettato
        sort_args: dict = {
            "by": clause["key_text"],
            "desc": bool(clause["desc"]),
            ORDERING_MARKER: True,
        }
        # Sorgente: eredita il from_step del consumer a valle (se int);
        # altrimenti auto-wire dell'Executor (_ENTRIES_CONSUMERS) — in quel
        # caso lo step è condizionato a entries non vuote (l'auto-wire non
        # riempie su lista vuota → required `entries` mancante; con
        # from_step la lista vuota fluisce onesta, N=0 §2.1).
        guard_nonempty = True
        if p < len(new_steps):
            src = new_steps[p].args.get("from_step")
            if isinstance(src, int) and src < q:
                sort_args["from_step"] = src
                guard_nonempty = False
        for s in new_steps[p:]:
            s.args = _shift_step_refs(s.args, q)
        final_message = _shift_text_refs(final_message, q)
        new_steps.insert(p, _SS(tool="sort_entries", args=sort_args,
                                if_prev_entries_nonempty=guard_nonempty))
        # Il consumer subito a valle legge dallo step sort.
        if p + 1 < len(new_steps) \
                and new_steps[p + 1].tool in _ENTRIES_CONSUMER_TOOLS:
            nxt = new_steps[p + 1].args
            nxt["from_step"] = q
            if isinstance(nxt.get("entries"), str):
                nxt.pop("entries")  # placeholder anti-pattern §4.1
        changed = True

    for s in new_steps:
        if s.tool == "describe_entries" \
                and s.args.get("group_by") != clause["key_text"]:
            s.args["group_by"] = clause["key_text"]
            changed = True

    if not changed:
        return framework
    return _FW(
        steps=new_steps,
        fillers=framework.fillers,
        final_message=final_message,
        # Metadato runtime-owned: una normalizzazione deterministica non deve
        # trasformare una pipeline canonica lunga in un piano LLM ordinario.
        # Il campo resta volutamente fuori da to_dict/from_dict e dalle cache.
        runtime_step_cap=int(
            getattr(framework, "runtime_step_cap", 0) or 0),
    )
