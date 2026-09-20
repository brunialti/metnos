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

import detection_lexicon_seed_parsers as _parser_lex

# ── 1. Detection deterministica (grammatica + lessico registrato) ──────

_KEY_TOKEN = r"\w+"
_KEY_CAPTURE = rf"(?P<key>{_KEY_TOKEN}(?:\s+{_KEY_TOKEN}){{0,2}})"

# Gap lessicale ammesso fra verbo e preposizione: «ordina I FILE per
# dimensione» (max 4 token, non-greedy: l'adiacenza vince).
_GAP = rf"(?:\s+{_KEY_TOKEN}){{0,4}}?"

def _phrase_alt(forms) -> str:
    """Escaped longest-first alternation for registered literal forms."""
    return "|".join(
        re.escape(str(form)).replace(r"\ ", r"\s+")
        for form in sorted(set(forms or ()), key=lambda item: (-len(item), item))
        if str(form).strip()
    )


def _clean_key(raw: str, lexicon: dict[str, object]) -> str:
    """Pulisce la chiave catturata: scarta articoli in testa, taglia al
    primo stop-token, max 2 token."""
    articles = {str(item).casefold() for item in
                lexicon["parser.ordering.article"]}
    stops = {str(item).casefold() for item in
             lexicon["parser.ordering.key_stop"]}
    tokens = (raw or "").casefold().split()
    while tokens and tokens[0] in articles:
        tokens = tokens[1:]
    out: list[str] = []
    for t in tokens:
        if t in stops:
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
    lexicon = _parser_lex.load_family("ordering")
    if lexicon is None:
        return None
    best = None  # (pos, mode, key)
    modes = lexicon["parser.ordering.mode_verb"]
    for mode, verbs in modes.items():
        verb_alt = _phrase_alt(verbs)
        connector_alt = _phrase_alt(
            lexicon.get(f"parser.ordering.{mode}_connector", ()))
        if not verb_alt or not connector_alt:
            continue
        rx = re.compile(
            rf"(?<!\w)(?:{verb_alt})(?!\w){_GAP}\s+"
            rf"(?:{connector_alt})\s+{_KEY_CAPTURE}",
            re.IGNORECASE | re.UNICODE,
        )
        for match in rx.finditer(query):
            key = _clean_key(match.group("key"), lexicon)
            if key and (best is None or match.start() < best[0]):
                best = (match.start(), mode, key)
    prefix_alt = _phrase_alt(lexicon["parser.ordering.sort_prefix"])
    if prefix_alt:
        prefix_rx = re.compile(
            rf"(?<!\w)(?:{prefix_alt})(?!\w)\s+{_KEY_CAPTURE}",
            re.IGNORECASE | re.UNICODE,
        )
        for match in prefix_rx.finditer(query):
            key = _clean_key(match.group("key"), lexicon)
            if key and (best is None or match.start() < best[0]):
                best = (match.start(), "sort", key)
    if best is None:
        return None
    desc_alt = _phrase_alt(lexicon["parser.ordering.descending"])
    descending = bool(desc_alt and re.search(
        rf"(?<!\w)(?:{desc_alt})(?!\w)", query,
        re.IGNORECASE | re.UNICODE,
    ))
    return {"mode": best[1], "key_text": best[2], "desc": descending}


# ── 2. Risoluzione chiave-utente → campo reale delle entries ─────────────

# I nomi reali dei campi sono invarianti tecniche; solo le alias utente sono
# risorse linguistiche in ``parser.ordering.field_alias``.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "domain": ("domain", "dominio", "hostname", "host"),
    "account": ("account", "mailbox", "folder", "account_email"),
    "sender": ("from", "sender", "from_email", "author"),
    "recipient": ("to", "recipient"),
    "date": ("date", "mtime", "modified_at", "created_at", "timestamp",
             "start", "ts", "time"),
    "size": ("size", "bytes", "size_bytes", "total_bytes", "file_size"),
    "subject": ("subject", "title", "name"),
    "name": ("name", "title", "basename", "filename", "path"),
    "type": ("kind", "type", "content_type", "format", "ext",
             "extension", "mimetype"),
    "folder": ("folder", "dir", "parent", "directory"),
    "status": ("status", "state"),
    "author": ("author", "from", "sender", "user"),
    "category": ("category", "class", "label", "importance"),
}


def _entry_keys(entries: list) -> dict[str, str]:
    """Unione dei campi top-level delle entries (esclusi i tecnici `_*`).
    Ritorna map lowercase→nome reale (primo visto, ordine stabile)."""
    keys: dict[str, str] = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        for k in e.keys():
            if isinstance(k, str) and not k.startswith("_"):
                keys.setdefault(k.casefold(), k)
    return keys


def resolve_field(key_text: str, entries: list) -> Optional[str]:
    """Risolve il termine-utente (es. «mailbox», «mittente», «size») nel
    campo REALE presente nelle entries. Catena deterministica:
    match esatto > famiglia di sinonimi > substring (len>=3). None se
    nessun campo plausibile (es. «tema»: concetto, non campo)."""
    kt = (key_text or "").strip().casefold()
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
    lexicon = _parser_lex.load_family("ordering")
    aliases = (lexicon or {}).get("parser.ordering.field_alias", {})
    for family, candidates in _FIELD_CANDIDATES.items():
        synonyms = {str(item).casefold()
                    for item in aliases.get(family, ())}
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
    "sort_entries", "group_entries", "create_files_spreadsheet",
    "write_files_spreadsheet", "write_files", "write_files_doc",
})

# Once durable output starts, ordering must already have happened.  Inserting
# a sort immediately before ``final_answer`` sorts nothing when report/file
# sinks precede it (live turn 94d748bc).  This boundary is effect-based and
# independent of the producer domain.
_OUTPUT_BOUNDARY_TOOLS = frozenset({
    "create_dirs", "write_files", "write_files_doc",
    "write_files_spreadsheet", "create_files_doc",
    "create_files_spreadsheet", "compress_files",
})
_KNOWN_ENTRY_TRANSFORMS = frozenset({
    "extract_entries", "filter_entries", "sort_entries", "group_entries",
    "classify_entries", "compute_entries", "compare_entries",
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
        elif any(tool in _OUTPUT_BOUNDARY_TOOLS for tool in tools):
            p = min(i for i, tool in enumerate(tools)
                    if tool in _OUTPUT_BOUNDARY_TOOLS)
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
        source_pos = None
        if p < len(new_steps):
            src = new_steps[p].args.get("from_step")
            if isinstance(src, int) and src < q:
                source_pos = src
        # The first output boundary is often ``create_dirs`` and therefore has
        # no data reference.  Look through all downstream sinks for their
        # common carrier; if still absent, use the latest known pure entries
        # transform before the boundary.
        if source_pos is None:
            source_pos = next((
                src for step in new_steps[p:]
                if isinstance((src := step.args.get("from_step")), int)
                and src < q
            ), None)
        if source_pos is None:
            source_pos = next((
                index + 1 for index in range(p - 1, -1, -1)
                if new_steps[index].tool in _KNOWN_ENTRY_TRANSFORMS
            ), None)
        if source_pos is not None:
            sort_args["from_step"] = source_pos
            guard_nonempty = False
        for s in new_steps[p:]:
            s.args = _shift_step_refs(s.args, q)
        final_message = _shift_text_refs(final_message, q)
        new_steps.insert(p, _SS(tool="sort_entries", args=sort_args,
                                if_prev_entries_nonempty=guard_nonempty))
        # Every downstream entries consumer that read the pre-sort carrier
        # now reads the injected sort.  Consumers with a distinct explicit
        # branch are left untouched.
        for downstream in new_steps[p + 1:]:
            if downstream.tool not in _ENTRIES_CONSUMER_TOOLS:
                continue
            current = downstream.args.get("from_step")
            if source_pos is not None and current == source_pos:
                downstream.args["from_step"] = q
            elif current is None and downstream.tool in {
                    "create_files_spreadsheet", "write_files_spreadsheet"}:
                downstream.args["from_step"] = q
            if isinstance(downstream.args.get("entries"), str):
                downstream.args.pop("entries")
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
