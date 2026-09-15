"""args_extractor — estrazione deterministica di args tipati dalla query.

`regex_extract` (deterministico, §7.9): estrae token tipati comuni dalla
query con regex chiusa (PATH, URL, INT, EMAIL, FILE_EXT_GLOB,
DATE/TIME_WINDOW). V1.5 19/5 v5: home → ~, uppercase ext "PDF" → *.pdf,
keywords IT/EN oggi/today/ieri/etc.

Caller vivo: agent_runtime (strip degli args query-derived prima della
registrazione in canonical_query_log — single source of truth per gli
args ri-derivabili).

NB (11/6/2026): rimossi `extract_args`/`_llm_extract_args` (hybrid V1.5
con memoization + LLM fallback, ADR 0149): il loro unico caller era il
matcher L1 `canonical_matcher`, ritirato perche' ridondante con la cache
query→piano di Engine v2 (engine/fastpath L0).

Esposto:
    regex_extract(query, schema) -> dict
"""
from __future__ import annotations

import logging
import re
from typing import Optional

_LOG = logging.getLogger(__name__)


# Tipi standard placeholder estraibili via regex chiusa.
# Conservativi (§7.9): catch false positivi piuttosto che inventare.

# PATH: assoluto (/foo/bar), ~/.foo, ./, ../ + WINDOWS assoluto (C:\… o UNC
# \\srv\share — §2.4: una query può nominare un path del PC remoto, turn
# 8b675402: senza, l'extractor perdeva «C:\Windows\…» e il resolver cadeva su
# un default appreso).
_PATH_RE = re.compile(
    r"(?:^|\s)((?:~|\.{1,2})?/(?:[\w.\-]+/?)+|~/[\w.\-/]*"
    # Windows: i segmenti INTERMEDI (chiusi da \) ammettono lo spazio
    # («Program Files\»); il segmento FINALE no — altrimenti la regex
    # mangerebbe il resto della frase («…\etc sul pc-example e metti…»).
    r"|[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[\w.\-]*"
    r"|\\\\[\w.\-]+\\(?:[^\\/:*?\"<>|\r\n]+\\)*[\w.\-]*)"
)

# URL: http(s)://...
_URL_RE = re.compile(r"https?://\S+")

# INT: numero standalone (no parte di parola)

# EMAIL: standard RFC-light
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

def _localized_mapping(concept: str) -> dict[str, list[str]]:
    import detection_lexicon as _detlex

    return {
        str(canonical): [str(form) for form in forms if str(form).strip()]
        for canonical, forms in _detlex.mapping(concept).items()
        if isinstance(forms, list)
    }


def _localized_forms(concept: str) -> tuple[str, ...]:
    import detection_lexicon as _detlex

    return tuple(str(form) for form in _detlex.forms(concept) if str(form).strip())


def _phrase_occurs(query: str, phrase: str) -> bool:
    return bool(re.search(
        r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", query,
        flags=re.IGNORECASE | re.UNICODE,
    ))

# Pattern file con extension (*.ext, .ext)
_FILE_EXT_RE = re.compile(r"\*?\.(?P<ext>[a-zA-Z0-9]{1,5})\b")

# Nome-linguaggio/formato → estensione glob. L'utente dice «file python», non
# «file .py»: il nome del linguaggio (6+ lettere, fuori dal range estensione)
# va tradotto nell'estensione canonica. Mappa GENERALE (§7.3), non per-query.
# Chiave = parola intera in minuscolo; valore = estensione senza punto.
_LANG_EXT_MAP = {
    "python": "py", "javascript": "js", "typescript": "ts", "markdown": "md",
    "golang": "go", "rust": "rs", "ruby": "rb", "java": "java", "kotlin": "kt",
    "swift": "swift", "shell": "sh", "bash": "sh", "powershell": "ps1",
    "yaml": "yaml", "json": "json", "toml": "toml", "html": "html", "css": "css",
    "csharp": "cs", "cpp": "cpp", "header": "h", "perl": "pl", "php": "php",
    "scala": "scala", "elixir": "ex", "haskell": "hs", "lua": "lua", "sql": "sql",
    "text": "txt", "csv": "csv", "xml": "xml",
}
# Ordinato per lunghezza decrescente: «javascript» prima di «java» (evita che
# «file javascript» matchi «java»). Confine di parola su entrambi i lati.
_LANG_EXT_RE = re.compile(
    r"\b(" + "|".join(sorted(_LANG_EXT_MAP, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Estensioni/formati riconosciuti dopo «file …» (whitelist, NON blacklist: cosi'
# «file ci sono» non genera *.ci). Include i target di _LANG_EXT_MAP + i formati
# di documento/dato/media comuni. Tutto minuscolo, senza punto.
_KNOWN_EXTENSIONS = set(_LANG_EXT_MAP.values()) | {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "rtf",
    "txt", "md", "csv", "tsv", "json", "yaml", "yml", "toml", "xml", "ini",
    "log", "conf", "cfg", "env", "lock",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tiff", "ico",
    "mp3", "wav", "flac", "ogg", "mp4", "mov", "avi", "mkv", "webm",
    "zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar",
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "rb", "java", "kt", "c", "h",
    "cpp", "hpp", "cs", "php", "pl", "lua", "sh", "bash", "ps1", "sql", "r",
    "html", "htm", "css", "scss", "vue", "swift", "scala", "ex", "exs", "hs",
}

# Tilde standalone "~" (senza /) come abbreviazione di home dir. Patch
# 25/5/2026: query "directory in ~" non veniva catturata da _PATH_RE.
_TILDE_STANDALONE_RE = re.compile(r"(?:^|\s)~(?:\s|$|[^\w./~])")


def _home_path_pattern() -> re.Pattern:
    markers = sorted(
        set(_localized_forms("args.home_marker")), key=len, reverse=True,
    )
    alternatives = [re.escape(marker) for marker in markers]
    alternatives.append(re.escape("~"))
    return re.compile(
        r"(?:^|\s)(?:" + "|".join(alternatives)
        + r")/(?P<rest>[\w.\-/]+)",
        re.IGNORECASE | re.UNICODE,
    )


def _extract_paths(query: str) -> list[str]:
    """Estrae path-like da query. Filtra URL (che hanno '/' ma non sono path).

    V1.5 19/5 v5: aggiunto support per "home/foo" → "~/foo" e "home" standalone
    → "~/". Razionale: utente scrive "trova file in home/Documenti" senza
    espandere ~/ → il fast-path catturerebbe `home/Documenti` come path
    relativo invece di assoluto.
    """
    urls = set(_URL_RE.findall(query))
    out: list[str] = []
    # Dedup robusta: tracciamo SIA il path canonical (~/foo) SIA il path
    # raw (/foo, home/foo) per evitare duplicati cross-pattern.
    _seen_canon: set[str] = set()

    def _add(p: str) -> None:
        p = p.strip()
        if not p:
            return
        # Normalizza per dedup: drop leading "home/" -> "~/", trim trailing /
        canon = p
        if canon.lower().startswith("home/"):
            canon = "~/" + canon[5:]
        canon = canon.rstrip("/")
        if canon and canon not in _seen_canon:
            _seen_canon.add(canon)
            out.append(p if p.startswith(("~", "/", ".")) else canon)

    # 1. "home/<rest>" → "~/<rest>".
    for m in _home_path_pattern().finditer(query):
        rest = m.group("rest").strip()
        if rest:
            _add(f"~/{rest}")
    # 2. PATH_RE generico (assoluto, ./, ../, ~/). Filtra URL.
    for m in _PATH_RE.finditer(query):
        p = m.group(1).strip()
        if p and not any(p in u for u in urls):
            _add(p)
    # 3. "home" standalone (senza /) → "~/" se non gia' coperto.
    import detection_lexicon as _detlex
    if _detlex.match("args.home_marker", query) and not any(
            p.startswith("~") for p in out):
        _add("~/")
    # 4. "~" standalone (senza /) → "~/" se non gia' coperto.
    if _TILDE_STANDALONE_RE.search(query) and not any(
            p.startswith("~") for p in out):
        _add("~/")
    return out


def _extract_urls(query: str) -> list[str]:
    return _URL_RE.findall(query)


def _extract_count(query: str) -> Optional[int]:
    """CAP/conteggio ESPLICITO §7.9 (E.2, 2/7/2026): numero adiacente a un
    sostantivo contabile («100 foto», «10 mail») o preceduto da un prefisso
    di cap («prime 5», «top 3»). MAI il primo intero qualsiasi della query:
    «foto del 2020» è un anno, «da 50 euro» un prezzo — l'euristica ints[0]
    iniettava misroute su max_results. Pattern i18n da detection_lexicon
    (`count.cap_pattern`, capture group = il numero); lexicon assente →
    None (conservativo: meglio nessuna iniezione che una sbagliata)."""
    try:
        import detection_lexicon as dl
        m = dl.search("count.cap_pattern", query)
        if m:
            for g in m.groups():
                if g and g.isdigit():
                    return int(g)
    except Exception:
        pass
    return None


def _extract_emails(query: str) -> list[str]:
    return _EMAIL_RE.findall(query)


def _extract_file_ext_glob(query: str) -> Optional[str]:
    """Da 'trova file PDF' o 'i .tmp' → '*.pdf' / '*.tmp'.

    V1.5 19/5 v5: supporta esplicitamente "file PDF" / "files PDF" /
    "file di tipo PDF" senza punto. Caso live: il PLANNER spesso vede
    l'utente scrivere "file PDF" o "documenti PDF" senza glob.
    v6 25/6: nome-linguaggio ("file python" → *.py) via _LANG_EXT_MAP, perche'
    «python»/«javascript» eccedono il range estensione e darebbero *.python.
    """
    m = _FILE_EXT_RE.search(query)
    if m:
        return f"*.{m.group('ext').lower()}"
    # Nome di linguaggio/formato esteso ("python", "javascript", ...) → estensione
    # canonica. Precede il fallback generico "{2,5} lettere" perche' quei nomi
    # sono piu' lunghi e non finirebbero mai per essere catturati come estensione.
    try:
        import detection_lexicon as _detlex
        file_nouns = _detlex.native_ready_forms(
            "args.file_noun",
            require_manual=True,
            include_reviewed_baselines=True,
        )
    except Exception:
        file_nouns = []
    has_file_noun = any(
        _phrase_occurs(query, noun) for noun in file_nouns
    )
    ml = _LANG_EXT_RE.search(query) if has_file_noun else None
    if ml:
        return f"*.{_LANG_EXT_MAP[ml.group(1).lower()]}"
    # The noun/linker clause can inject a glob into mutating schemas.  It is
    # therefore served only as one complete, manually reviewed native regex
    # family; missing/pending locale coverage means no inferred glob.
    try:
        clause_patterns = _detlex.native_ready_patterns(
            "args.file_extension_clause",
            require_manual=True,
            include_reviewed_baselines=True,
        )
    except Exception:
        clause_patterns = []
    for pattern in clause_patterns:
        m = pattern.search(query)
        if m:
            cand = m.group(1).lower()
            if cand in _KNOWN_EXTENSIONS:
                return f"*.{cand}"
    return None


def _extract_file_kind_globs(query: str) -> list[str]:
    """Expand broad, translated file kinds into technical glob lists.

    Surface forms come from ``detection_lexicon`` and extension sets from
    ``file_kinds``. A form immediately used as a container name is ignored:
    ``folder Images`` identifies the search root, while ``image files`` or
    ``duplicate images in folder Images`` identify the requested file kind.
    """
    if not query:
        return []
    try:
        import detection_lexicon as dl
        from file_kinds import globs_for_kinds
    except ImportError:
        return []
    mapping = dl.mapping("files.kind") or {}
    container_forms = dl.forms("files.container_marker") or []
    low = query.casefold()
    selected: list[str] = []

    def _is_container_name(start: int) -> bool:
        prefix = low[:start].rstrip()
        return any(re.search(
            r"\b" + re.escape(str(form).casefold()) + r"\s*$", prefix)
            for form in container_forms if str(form).strip()
        )

    for kind, forms in mapping.items():
        matched = False
        for form in sorted(
                (str(value).casefold() for value in (forms or []) if value),
                key=len, reverse=True):
            for occurrence in re.finditer(
                    r"\b" + re.escape(form) + r"\b", low):
                if not _is_container_name(occurrence.start()):
                    matched = True
                    break
            if matched:
                break
        if matched:
            selected.append(str(kind))
    return globs_for_kinds(selected)


def _extract_date_keyword(query: str) -> Optional[str]:
    """Project a single calendar day using the shared clock and timezone.

    Legacy language registrations remain valid aliases, not a second clock
    or arithmetic implementation. A range never silently becomes one date.
    """
    from time_window_parser import resolve_time_bounds
    from time_window_resolver import parse_query_time_window, temporal_mentions

    if len(temporal_mentions(query)) > 1:
        return None
    expression = parse_query_time_window(query)
    q = query.lower()
    candidates = (
        (form, canonical)
        for canonical, forms in _localized_mapping("args.date_offset").items()
        for form in forms
    )
    if expression is None:
        for form, canonical in sorted(candidates, key=lambda item: -len(item[0])):
            if _phrase_occurs(q, form):
                offset = int(canonical)
                expression = "today" if offset == 0 else f"today{offset:+d}d"
                break
    if expression is None:
        return None
    try:
        start, end = resolve_time_bounds(expression)
        return (start.date().isoformat()
                if start is not None and end is not None and start.date() == end.date() else None)
    except (ValueError, TypeError, OverflowError):
        return None


def _extract_time_window(query: str) -> Optional[str]:
    """Use the central grammar, retaining registered legacy language aliases."""
    from time_window_resolver import parse_query_time_window
    canonical = parse_query_time_window(query)
    if canonical is not None:
        return canonical
    # Preserve externally registered legacy aliases after the shared grammar.
    q = query.lower()
    # Multi-word patterns prima (piu' specifici).
    candidates = (
        (form, canonical)
        for canonical, forms in _localized_mapping("args.time_window").items()
        for form in forms
    )
    for form, canonical in sorted(candidates, key=lambda item: -len(item[0])):
        if _phrase_occurs(q, form):
            return canonical
    # Numero + unita': "ultimi 7 giorni" / "last 30 days".
    prefixes = sorted(
        set(_localized_forms("args.relative_window_prefix")),
        key=len, reverse=True,
    )
    units = _localized_mapping("args.relative_window_unit")
    if prefixes and units:
        unit_owner = {
            form.casefold(): canonical
            for canonical, forms in units.items() for form in forms
        }
        unit_forms = sorted(unit_owner, key=len, reverse=True)
        pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(map(re.escape, prefixes))
            + r")\s+(\d+)\s+(" + "|".join(map(re.escape, unit_forms))
            + r")(?!\w)",
            re.IGNORECASE | re.UNICODE,
        )
        match = pattern.search(q)
        if match:
            return f"last-{match.group(1)}{unit_owner[match.group(2).casefold()]}"
    return None


# Slug 'owner/name' (es. repo GitHub) inline nella query. Lookaround esclude
# path (/a/b) e URL (host/owner/name): un solo '/', non preceduto/seguito da \w o /.
_REPO_SLUG_RE = re.compile(
    r"(?<![\w/])([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*)(?![\w/])")
_PLACEHOLDER_OWNERS = {"owner", "org", "organization", "user", "username",
                       "example", "your-org", "your-username", "you"}


def _extract_repo_slug(query: str) -> Optional[str]:
    """Primo 'owner/name' plausibile nella query (no path/URL/placeholder)."""
    for m in _REPO_SLUG_RE.finditer(query or ""):
        s = m.group(1)
        if s.count("/") != 1:
            continue
        if s.split("/", 1)[0].strip().lower() in _PLACEHOLDER_OWNERS:
            continue
        return s
    return None


# ── Nomi-arg riconosciuti dall'estrattore, raggruppati per semantica ─────────
# FONTE UNICA di «quali nomi sono clause-derivabili». I rami di regex_extract
# sotto E arg_provenance._CLAUSE_DERIVABLE_NAMES derivano ENTRAMBI da qui — niente
# più copia a mano (drift misurato 6/7: `recipient` vs `to_user/to_users`). Se
# l'extractor impara un nome nuovo, aggiungilo al gruppo giusto: provenienza e
# derivazione restano allineate per costruzione (§7.2 DRY, §7.3 sistemico).
_PATH_NAMES = frozenset({"path", "paths", "base_path", "src", "dst"})
_URL_NAMES = frozenset({"url", "urls", "src_url"})
_GLOB_NAMES = frozenset({"pattern", "patterns", "glob"})
_EMAIL_NAMES = frozenset({"to", "recipient_id", "recipients", "email",
                          "to_user", "to_users"})
_REPO_NAMES = frozenset({"repo", "repository"})
_COUNT_NAMES = frozenset({"max_results", "max_total", "top", "limit", "n", "count"})
_DATE_NAMES = frozenset({"date", "day", "when", "on_date"})
# A lower bound is not an alias for an entire period. Inferring both from one
# phrase can freeze a stale window and manufacture a contradictory interval.
# Explicit since/before arguments remain owned by their existing consumers.
_WINDOW_NAMES = frozenset({"time_window", "window", "range"})

#: Unione esportata: tutti i nomi-arg che regex_extract sa estrarre dal testo.
#: arg_provenance la importa come SoT per la classe `clause` (name-derivable).
CLAUSE_DERIVABLE_NAMES: frozenset = (
    _PATH_NAMES | _URL_NAMES | _GLOB_NAMES | _EMAIL_NAMES
    | _REPO_NAMES | _COUNT_NAMES | _DATE_NAMES | _WINDOW_NAMES)


def regex_extract(query: str, schema: dict | None) -> dict:
    """Extract only declared, name-typed arguments using deterministic rules.

    The schema selects path, URL, glob, explicit count, email, repository,
    date and whole-period extractors. Whole-period aliases are time_window,
    window and range; a mere temporal mention does not imply a since/before
    endpoint. Existing explicit endpoints are never modified here. Array
    cardinality comes from the schema, not the spelling of an argument name.

    Return an empty mapping when the schema or query supplies no evidence.
    """
    if not isinstance(schema, dict) or not query:
        return {}
    props = (schema.get("properties") or schema)
    out: dict = {}
    if not isinstance(props, dict):
        return {}
    # Boolean intent belongs to the semantic planner, not token extraction.
    # Description words (including negated behaviour or a path component)
    # cannot establish that the user requested a non-default mode.
    for arg_name, _arg_spec in props.items():
        lname = arg_name.lower()
        # Pluralizzazione GUIDATA DALLO SCHEMA, non da suffissi lessicali
        # (lang-independent, universale): `type=array` -> lista,
        # `type=string` -> primo elemento. Il NOME dell'arg porta solo la
        # semantica (path/url/email/glob/date/time_window) che e' il
        # vocabolario chiuso §2.2 condiviso IT+EN.
        _spec = _arg_spec if isinstance(_arg_spec, dict) else {}
        # Arg RUNTIME-ONLY (`runtime_resolved`): il RUNTIME lo possiede/risolve,
        # NON si estrae dalla query (arg_provenance). Estrarlo = leak. Bug live
        # 8/7: `copy` di move_files (runtime-only) veniva fabbricato a True dal
        # match description~verbo ("spostare" nella desc ~ "sposta" nella query)
        # → "sposta X in Y" diventava una COPIA (l'originale restava) e l'undo
        # collideva ("dst already exists"). Skip: il flag booleano seguente non
        # deve mai attivarsi su un runtime-only.
        if _spec.get("runtime_resolved"):
            continue
        _t = _spec.get("type")
        _is_plural = (_t == "array"
                       or (isinstance(_t, list) and "array" in _t))

        def _emit(value_list: list) -> None:
            if not value_list:
                return
            out[arg_name] = list(value_list) if _is_plural else value_list[0]

        # Mapping NOME -> ESTRATTORE. Il nome porta semantica
        # (paths/path/base_path/src/dst tutti sono "path"). Niente
        # ipotesi sul plurale dal nome — quello arriva dallo schema.
        if lname in _PATH_NAMES:
            _emit(_extract_paths(query))
        elif lname in _URL_NAMES:
            _emit(_extract_urls(query))
        elif lname in _GLOB_NAMES:
            g = _extract_file_ext_glob(query)
            if g:
                _emit([g])
            elif _spec.get("semantic_type") == "file_globs":
                _emit(_extract_file_kind_globs(query))
        elif lname in _EMAIL_NAMES:
            _emit(_extract_emails(query))
        elif lname in _REPO_NAMES:
            r = _extract_repo_slug(query)
            if r:
                out[arg_name] = r
        elif lname in _COUNT_NAMES:
            n = _extract_count(query)
            if n is not None:
                out[arg_name] = n
        elif lname in _DATE_NAMES:
            d = _extract_date_keyword(query)
            if d:
                out[arg_name] = d
        elif lname in _WINDOW_NAMES:
            w = _extract_time_window(query) or _extract_date_keyword(query)
            if w:
                out[arg_name] = w
    return out
