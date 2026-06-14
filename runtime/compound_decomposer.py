"""compound_decomposer.py — Decomposer deterministico §7.9 per compound queries.

Universal § lingua-indipendente: usa vocab IT+EN già esistente in prefilter
(`_VERB_TO_CANONICAL`) e vocab (`canonical_object` + `_OBJECT_HINTS`).

Trasforma query come "trova X, mettili in Y e inviameli via Z" in framework
multi-step pronto per Praxis Noûs:

    [
        {"tool": "find_X", "args": {...}},
        {"tool": "create_Y", "args": {"from_step": 1, ...}},
        {"tool": "send_Z", "args": {"from_step": 2, ...}},
        {"tool": "final_answer", "args": {}},
    ]

Bypassa Mētis LLM quando decomposizione deterministica successo.
"""
from __future__ import annotations

import re
from typing import Optional

# Connettori sequenziali universal (IT + EN + simboli):
# - virgola, punto-virgola: separatori sintattici universal
# - "e", "and", "poi", "then": connettori temporali standard
# - "&" / "&&": symbol-only
_CONNECTOR_PATTERN = re.compile(
    r"\s*(?:,|;|\&\&?|\b(?:e|and|poi|then|after|finally|infine)\b)\s*",
    re.IGNORECASE,
)

# Verb categories from §2.2 vocab (canonical):
# - Producer (read_family): find/read/get/list — produce entries
# - Mutating: write/create/set/move/delete/send/share/compress/extract/change
# - Transformative: filter/sort/group/classify/describe/render/compute/compare
PRODUCER_VERBS = {"find", "read", "get", "list"}
MUTATING_VERBS = {"write", "create", "set", "move", "delete", "send",
                   "share", "compress", "extract", "change", "order"}
TRANSFORM_VERBS = {"filter", "sort", "group", "classify", "describe",
                    "render", "compute", "compare"}


def split_query_chunks(query: str) -> list[str]:
    """Split query su connettori sequenziali universali. Ritorna chunks
    non vuoti puliti."""
    if not query or not query.strip():
        return []
    parts = _CONNECTOR_PATTERN.split(query)
    return [p.strip() for p in parts if p.strip()]


def detect_chunk_action(chunk: str) -> Optional[tuple[str, str]]:
    """Detect (verb, object) canonical per un chunk di query.
    Ritorna None se nessun verbo canonico o object derivabile.

    Universal §7.9: usa vocab esistenti, no patterns hardcoded.
    """
    try:
        from prefilter import (
            tokenize,
            detect_canonical_verbs_all,
            _OBJECT_HINTS,
        )
        from vocab import canonical_object as _canon_obj
    except ImportError:
        return None

    tokens = tokenize(chunk)
    if not tokens:
        return None

    # 1. Detect verbo canonico (con clitic stripping incluso)
    verbs = detect_canonical_verbs_all(tokens)
    if not verbs:
        return None
    verb = verbs[0]

    # 2. Detect object canonico
    # Try _OBJECT_HINTS first (più ricco)
    detected_obj = None
    for obj, hints in _OBJECT_HINTS.items():
        for h in hints:
            h_tokens = set(h.lower().split())
            if h_tokens <= tokens or h in chunk.lower():
                detected_obj = obj
                break
        if detected_obj:
            break

    # Fallback: try canonical_object on each token
    if not detected_obj:
        for tok in tokens:
            obj = _canon_obj(tok)
            if obj:
                detected_obj = obj
                break

    if not detected_obj:
        return None

    return (verb, detected_obj)


def derive_tool_name(verb: str, obj: str, available_tools: set[str]) -> Optional[str]:
    """Derive canonical tool name `<verb>_<obj>` o variante presente nel catalog.
    Universal §7.9: cerca nel pool tool registrato, no inventato.
    Preferenza: forma plain canonical (no qualifier) over qualifier variants.
    """
    # 1. Exact match canonical (preferito)
    canonical = f"{verb}_{obj}"
    if canonical in available_tools:
        return canonical
    # 2. READ_FAMILY swap PRIMA dei qualifier variants (find_X canonical più
    # forte che find_X_indices/find_X_empty per query generiche).
    if verb in PRODUCER_VERBS:
        for alt_verb in PRODUCER_VERBS:
            alt = f"{alt_verb}_{obj}"
            if alt in available_tools:
                return alt
    # 3. TRANSFORM verb generic fallback: <verb>_entries opera su qualsiasi
    # entry list (universal §7.9 — describe_entries/classify_entries/...).
    if verb in TRANSFORM_VERBS:
        generic = f"{verb}_entries"
        if generic in available_tools:
            return generic
    # 4. Suffix variants (es. write_files_doc per write+files)
    prefix = f"{verb}_{obj}_"
    suffix_variants = sorted(t for t in available_tools if t.startswith(prefix))
    if suffix_variants:
        return suffix_variants[0]  # alphabetical first
    return None


def build_step_args(verb: str, obj: str, chunk: str,
                     prev_step_idx: Optional[int] = None,
                     tool_name: Optional[str] = None,
                     tool_schemas: Optional[dict] = None) -> dict:
    """Costruisci args di base per uno step.

    Universal §7.9 — pattern derivati da prefilter+vocab:
      - producer step 1: estrai possibili input (paths/urls/dates)
      - mutating/transform step N>1: usa from_step per piping

    Schema-aware (§7.9): se `tool_schemas` e' fornito, gli args di default
    (es. title/name su create/write) vengono POTATI a quelli realmente
    dichiarati dal tool — niente arg bogus che il tool ignorerebbe (e che
    falserebbero il confidence gate del decomposer).
    """
    args: dict = {}

    # Detect time_window dal chunk (universal IT+EN)
    # "prossimi 30 giorni" / "next 30 days" / "ultime 7 ore" / "last 7 hours"
    time_pattern = re.compile(
        r"(prossim[io]|next|ultim[io]|last|past)\s*(\d+)\s*(giorn[io]|days?|or[ae]|hours?|mes[ie]|months?|settiman[ae]|weeks?)",
        re.IGNORECASE,
    )
    tm = time_pattern.search(chunk)
    if tm and obj in ("events", "messages", "tasks", "files"):
        direction = tm.group(1).lower()
        n = int(tm.group(2))
        unit_raw = tm.group(3).lower()
        unit_map = {
            "giorn": "d", "day": "d",
            "or": "h", "hour": "h",
            "settiman": "d",  # weeks → days *7
            "week": "d",
            "mes": "d", "month": "d",  # months → days *30
        }
        unit = next((v for k, v in unit_map.items() if unit_raw.startswith(k)), "d")
        if "settiman" in unit_raw or unit_raw.startswith("week"):
            n *= 7
        if "mes" in unit_raw or unit_raw.startswith("month"):
            n *= 30
        prefix = "next" if direction.startswith(("pross", "next")) else "last"
        args["time_window"] = f"{prefix}-{n}{unit}"

    # from_step piping per step N>1 (mutating/transform consumer)
    if prev_step_idx is not None and verb in (MUTATING_VERBS | TRANSFORM_VERBS):
        args["from_step"] = prev_step_idx

    # Auto-fill default title/name args per create/write (universal §7.9).
    # Derivato da timestamp + object → "events_2026-05-27" ecc.
    if verb in ("create", "write"):
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M")
        default_name = f"{obj}_{ts}"
        args.setdefault("title", default_name)
        args.setdefault("name", default_name)

    # send_messages: structure messages list con destinatario + body con
    # link al risultato del step precedente. Universal §7.9: usa output
    # comuni dei producer (web_view_url, path, url, summary).
    if verb == "send" and obj == "messages" and prev_step_idx is not None:
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        args.pop("from_step", None)
        # Body con multiple placeholder fallback: web_view_url > url > path > summary
        body_template = (
            f"Generated: {ts}\n\n"
            f"Risultato: ${{step{prev_step_idx}.web_view_url}}\n"
            f"(alternative: ${{step{prev_step_idx}.url}} / "
            f"${{step{prev_step_idx}.path}})\n\n"
            f"Sintesi:\n${{step{prev_step_idx}.summary}}"
        )
        args["messages"] = [{
            "to": "${RUNTIME:actor_email}",
            "subject": f"Metnos auto-export {ts}",
            "body": body_template,
        }]

    # Potatura schema-aware §7.9: scarta gli args che il tool NON dichiara
    # (es. 'name' di default ma non previsto da create_files_spreadsheet).
    # Preserva piping (from_step/entries) e runtime args (_*).
    if tool_schemas and tool_name:
        sch = tool_schemas.get(tool_name)
        if isinstance(sch, dict) and sch.get("properties"):
            props = set(sch["properties"].keys())
            args = {k: v for k, v in args.items()
                    if k in props or k in ("from_step", "entries")
                    or k.startswith("_")}

    return args


def _send_has_explicit_recipient(chunk: str) -> bool:
    """True se il chunk nomina un destinatario ESPLICITO per un 'send': una
    email (`x@y.z`) o `a/ad/to <NomeProprio>` (capitalizzato). I pronomi self
    (mandaMI / inviaMI / «a me») NON contano. Bias di sicurezza: in dubbio
    False → risposta in chat, niente email indesiderata (decisione 1/6, §10.2:
    "mandami il riassunto" = rispondi in chat, non spedire un'email a sé)."""
    import re as _re
    if _re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", chunk):
        return True
    # 'a'/'ad'/'to' + nome proprio CAPITALIZZATO (es. "manda a Roberto").
    # Minuscolo ("a casa", "a quella pagina") NON è un destinatario.
    if _re.search(r"\b(?:a|ad|to)\s+[A-ZÀ-Þ][\wÀ-ÿ'.\-]+", chunk):
        return True
    return False


def _step_schema_coherent(tool: str, args: dict,
                          tool_schemas: Optional[dict]) -> bool:
    """True se lo step e' COERENTE con lo schema del tool (decomposer confident).

    Deterministico §7.9. Senza schema (builtin/ignoto) → True (non giudica).
    Reietta (False) quando il decomposer euristico ha prodotto uno step rotto:
      - arg NON dichiarato nello schema (mapping sbagliato, es. write_files con
        `title`/`name` che non sono suoi argomenti);
      - required non soddisfatto e non coperto da piping from_step/entries.
    In quei casi `decompose_query` ritorna None e il runtime DEFERISCE al
    PLANNER LLM (che estrae gli args dal NL), invece di eseguire una pipeline
    malformata (§2.8). Generale: vale per QUALSIASI tool, non per casi cablati."""
    if not tool_schemas:
        return True
    sch = tool_schemas.get(tool)
    if not isinstance(sch, dict) or not sch.get("properties"):
        return True  # builtin o schema assente → non valutabile
    props = set((sch.get("properties") or {}).keys())
    required = set(sch.get("required") or [])
    piped = ("from_step" in args) or ("entries" in args)
    for k in args:
        if k in ("from_step", "entries") or k.startswith("_"):
            continue
        if k not in props:
            return False  # arg bogus → mapping non confidente
    for r in required:
        if r in args or piped:
            continue
        return False  # required mancante e non pipeable
    # `requires_one_of` (§7.3 universale): ogni gruppo richiede ALMENO un arg
    # non-vuoto. Se l'euristica non ha popolato nessun criterio di un gruppo
    # (es. find_images_indices senza query_text/name/... → "missing search
    # criterion"), lo step e' INCOMPLETO → defer al PLANNER LLM (che estrae il
    # criterio dal NL). Vale per QUALSIASI tool con requires_one_of, non cablato.
    def _provided(k: str) -> bool:
        if k in ("from_step", "entries"):
            return piped
        v = args.get(k)
        return v not in (None, "", [], {}, 0) and not (isinstance(v, str) and not v.strip())
    for group in (sch.get("requires_one_of") or []):
        if isinstance(group, list) and group and not any(_provided(k) for k in group):
            return False  # nessun criterio del gruppo → defer
    return True


def decompose_query(query: str, available_tools: set[str],
                    tool_schemas: Optional[dict] = None) -> Optional[list[dict]]:
    """Decompose query in framework multi-step. Universal §7.9.

    Ritorna lista di step `[{tool, args}, ...]` (senza final_answer).
    Ritorna None se decomposizione fallisce (caller può fallback a LLM Mētis).

    Requirements:
      - Query split in >=2 chunks su connettori standard
      - Almeno 2 chunks devono produrre (verb, object) → tool valido
      - Tutti i tool derivati devono essere nel catalog
      - `tool_schemas` (opzionale, {tool: args_schema}): se fornito, OGNI step
        deve essere schema-coerente (vedi `_step_schema_coherent`); altrimenti
        il decomposer DEFERISCE al PLANNER LLM (return None). Evita di
        short-circuitare con pipeline rotte su domini che richiedono estrazione
        args non banale (es. repo GitHub, path_template) — territorio LLM §7.9.

    Pronoun resolution universal: se chunk ha verbo ma no object → eredita
    object dal chunk precedente (es. "mandameli" / "cancellale" referenziano
    le entries del producer precedente).
    """
    chunks = split_query_chunks(query)
    if len(chunks) < 2:
        return None

    # Mapping format hints → object/qualifier per "metti in X" pattern.
    # Universal §7.9: file format → tool suffix.
    FORMAT_HINTS = {
        "spreadsheet": ("files", "spreadsheet"),
        "excel": ("files", "spreadsheet"),
        "xlsx": ("files", "xlsx"),
        "xls": ("files", "xlsx"),
        "csv": ("files", "csv"),
        "pdf": ("files", "pdf"),
        "doc": ("files", "doc"),
        "document": ("files", "doc"),
        "documento": ("files", "doc"),
        "json": ("files", "json"),
        "xml": ("files", "xml"),
        "html": ("files", "html"),
        "markdown": ("files", "md"),
        "md": ("files", "md"),
        "txt": ("files", "txt"),
        "text": ("files", "txt"),
    }

    def _detect_format_obj(chunk: str) -> Optional[tuple[str, str]]:
        """Detect (obj, qualifier) da format hint nel chunk.
        Universal §7.9: exact substring + Levenshtein <=2 (tolera typos).
        """
        cl = chunk.lower()
        # 1. Exact substring (fast path)
        for hint, (obj, qual) in FORMAT_HINTS.items():
            if hint in cl:
                return (obj, qual)
        # 2. Fuzzy match on tokens (typo-tolerant via difflib.SequenceMatcher)
        import re as _re_fm
        from difflib import SequenceMatcher
        tokens = _re_fm.findall(r"[a-zàèéìòù]{4,}", cl)
        for tok in tokens:
            for hint, (obj, qual) in FORMAT_HINTS.items():
                # Skip very different lengths
                if abs(len(tok) - len(hint)) > 3:
                    continue
                ratio = SequenceMatcher(None, tok, hint).ratio()
                if ratio >= 0.85:  # ~1-2 char differences for 10-char words
                    return (obj, qual)
        return None

    # Helper: build values 2D matrix da entries (universal §7.9).
    # Headers = chiavi top-level del primo entry (escluse meta/nested).
    def _build_values_from_entries_ref(prev_step: int) -> list[list]:
        """Return template references che ExecutorEngine risolverà a matrix."""
        # Costruiamo template che genera headers+rows dal step.entries.
        # NOTE: la conversione effettiva entries→matrix avviene a runtime
        # via _resolve_stepref + post-processing. Per ora ritorniamo un
        # placeholder structure che il resolver espanderà.
        return f"${{step{prev_step}.entries}}"  # placeholder, gestito post

    steps: list[dict] = []
    last_obj: Optional[str] = None
    # Tracking step indexes "user-intended" (escludendo auto-inject interni
    # come write_X_qualifier dopo create_X_qualifier). Il prev_idx per
    # send_messages deve riferirsi al CREATE step (con web_view_url), non
    # al WRITE auto-injected.
    user_step_idxs: list[int] = []
    for i, chunk in enumerate(chunks, 1):
        action = detect_chunk_action(chunk)
        verb_qualifier: Optional[str] = None
        if not action:
            # Verb-only fallback + format hint OR pronoun resolution
            try:
                from prefilter import tokenize, detect_canonical_verbs_all
                tokens = tokenize(chunk)
                verbs = detect_canonical_verbs_all(tokens)
            except ImportError:
                verbs = []
            if not verbs:
                continue
            fmt = _detect_format_obj(chunk)
            if fmt:
                obj, verb_qualifier = fmt
                action = (verbs[0], obj)
            else:
                # Verb-default object: send/share → messages, delete → use last_obj
                VERB_DEFAULT_OBJ = {
                    "send": "messages", "share": "files",
                    "create": "files", "write": "files",
                    "compress": "files", "extract": "files",
                }
                default_obj = VERB_DEFAULT_OBJ.get(verbs[0]) or last_obj
                if default_obj:
                    action = (verbs[0], default_obj)
                else:
                    continue
        verb, obj = action
        # §10.2 (decisione 1/6): "mandami/inviami il riassunto|risultato" SENZA
        # destinatario esplicito = risposta in CHAT (describe_entries), NON una
        # email a sé. Risolve il bug del send-body degenere (email vuota
        # "Risultato:/Sintesi:" inviata + falso ✓, §2.8). send_messages resta
        # SOLO con destinatario esplicito (email o "a <Nome>").
        if (verb == "send" and obj == "messages"
                and not _send_has_explicit_recipient(chunk)):
            verb = "describe"
            obj = last_obj or obj
            action = (verb, obj)
        # Apply qualifier preference (es. write_files_spreadsheet over write_files).
        # PRIORITA': create_X_qualifier > write_X_qualifier > verb_X_qualifier.
        # Razionale §7.9: "metti in spreadsheet" intent è CREARE nuovo, non
        # UPDATE existing (che richiede id non noto in compound query).
        tool_name = None
        if verb_qualifier:
            for alt_verb in ("create", "write"):
                alt = f"{alt_verb}_{obj}_{verb_qualifier}"
                if alt in available_tools:
                    tool_name = alt
                    break
            # Fallback: usa il verbo originale se nessuna alternativa create/write
            if not tool_name:
                qual_tool = f"{verb}_{obj}_{verb_qualifier}"
                if qual_tool in available_tools:
                    tool_name = qual_tool
        if not tool_name:
            tool_name = derive_tool_name(verb, obj, available_tools)
        if not tool_name:
            continue  # skip chunk, don't abort whole decomposition
        # prev_idx = ultimo step USER-INTENDED (skip auto-injects)
        prev_idx = user_step_idxs[-1] if user_step_idxs else None
        args = build_step_args(verb, obj, chunk, prev_idx,
                               tool_name=tool_name, tool_schemas=tool_schemas)
        # Confidence gate (§7.9/§2.8): se gli args euristici non sono coerenti
        # con lo schema del tool, il decomposer NON e' confidente → deferisce
        # al PLANNER LLM invece di eseguire una pipeline rotta.
        if not _step_schema_coherent(tool_name, args, tool_schemas):
            return None
        steps.append({"tool": tool_name, "args": args})
        user_step_idxs.append(len(steps))  # 1-indexed of THIS step
        # Universal §7.9: dopo create_X_qualifier che ritorna un blob vuoto
        # (sheet/doc/db senza contenuto), se step PRECEDENTE ha prodotto
        # entries → inject step write_X_qualifier che POPOLA con i dati.
        # Check tool_name (not verb) perché preferenza create>write può aver
        # mappato verb=write a tool_name=create_X.
        if (tool_name and tool_name.startswith("create_") and verb_qualifier
            and prev_idx is not None
            and verb_qualifier in ("spreadsheet", "doc", "csv", "xlsx", "json")):
            write_tool = f"write_{obj}_{verb_qualifier}"
            if write_tool in available_tools:
                create_step_idx = len(steps)  # 1-indexed
                write_args: dict = {}
                # Build write args based on qualifier-specific schema
                if verb_qualifier in ("spreadsheet", "xlsx"):
                    write_args = {
                        "spreadsheet_id": f"${{step{create_step_idx}.spreadsheet_id}}",
                        "range": "A1",  # default first sheet, no tab name
                        "values": f"${{step{prev_idx}.entries}}",  # 2D conversion at resolve
                        "mode": "overwrite",
                    }
                elif verb_qualifier == "doc":
                    write_args = {
                        "doc_id": f"${{step{create_step_idx}.doc_id}}",
                        "content": f"${{step{prev_idx}.summary}}",
                    }
                elif verb_qualifier in ("csv", "json"):
                    write_args = {
                        "path": f"${{step{create_step_idx}.path}}",
                        "data": f"${{step{prev_idx}.entries}}",
                    }
                if write_args:
                    steps.append({"tool": write_tool, "args": write_args})
        last_obj = obj

    if len(steps) < 2:
        return None  # not really compound

    return steps
