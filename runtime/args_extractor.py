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
from datetime import datetime, timedelta, timezone
from typing import Optional

_LOG = logging.getLogger(__name__)


# Tipi standard placeholder estraibili via regex chiusa.
# Conservativi (§7.9): catch false positivi piuttosto che inventare.

# PATH: assoluto (/foo/bar), ~/.foo, ./, ../
_PATH_RE = re.compile(
    r"(?:^|\s)((?:~|\.{1,2})?/(?:[\w.\-]+/?)+|~/[\w.\-/]*)"
)

# URL: http(s)://...
_URL_RE = re.compile(r"https?://\S+")

# INT: numero standalone (no parte di parola)
_INT_RE = re.compile(r"(?:^|\s)(\d+)(?:\s|$|[^\w.])")

# EMAIL: standard RFC-light
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# DATE keywords IT/EN. Mappa a offset (giorni) dalla data corrente.
# V1.5 19/5/2026: estesa con varianti comuni IT/EN per copertura corpus
# query reali ("dopodomani", "next week", "this week", ...).
_DATE_KEYWORDS_OFFSET = {
    # IT
    "oggi": 0,
    "ieri": -1,
    "domani": +1,
    "dopodomani": +2,
    "altroieri": -2,
    # EN
    "today": 0,
    "yesterday": -1,
    "tomorrow": +1,
    "day after tomorrow": +2,
    "day before yesterday": -2,
}

# Window keywords → time_window canonical (lascia all'executor il parsing).
_TIME_WINDOW_KEYWORDS = {
    # IT
    "questa settimana": "this-week",
    "settimana scorsa": "last-week",
    "settimana prossima": "next-week",
    "questo mese": "this-month",
    "ultimi 7 giorni": "last-7d",
    "ultime 24 ore": "last-24h",
    "ultime ore": "last-24h",
    # EN
    "this week": "this-week",
    "last week": "last-week",
    "next week": "next-week",
    "this month": "this-month",
    "last 7 days": "last-7d",
    "last 24 hours": "last-24h",
    "last hours": "last-24h",
}

# Pattern file con extension (*.ext, .ext)
_FILE_EXT_RE = re.compile(r"\*?\.(?P<ext>[a-zA-Z0-9]{1,5})\b")

# Home keyword IT/EN. "home" non e' un path: e' un'abbreviazione per ~/.
# Detection: "home" come parola standalone o "home/" prefisso.
_HOME_KEYWORDS_RE = re.compile(
    r"(?:^|\s)(?:home|the\s+home|la\s+home|nella\s+home|in\s+home)\b",
    re.IGNORECASE,
)

# Tilde standalone "~" (senza /) come abbreviazione di home dir. Patch
# 25/5/2026: query "directory in ~" non veniva catturata da _PATH_RE.
_TILDE_STANDALONE_RE = re.compile(r"(?:^|\s)~(?:\s|$|[^\w./~])")
_HOME_PATH_RE = re.compile(
    r"(?:^|\s)(?:home|~)/(?P<rest>[\w.\-/]+)",
    re.IGNORECASE,
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
    for m in _HOME_PATH_RE.finditer(query):
        rest = m.group("rest").strip()
        if rest:
            _add(f"~/{rest}")
    # 2. PATH_RE generico (assoluto, ./, ../, ~/). Filtra URL.
    for m in _PATH_RE.finditer(query):
        p = m.group(1).strip()
        if p and not any(p in u for u in urls):
            _add(p)
    # 3. "home" standalone (senza /) → "~/" se non gia' coperto.
    if _HOME_KEYWORDS_RE.search(query) and not any(
            p.startswith("~") for p in out):
        _add("~/")
    # 4. "~" standalone (senza /) → "~/" se non gia' coperto.
    if _TILDE_STANDALONE_RE.search(query) and not any(
            p.startswith("~") for p in out):
        _add("~/")
    return out


def _extract_urls(query: str) -> list[str]:
    return _URL_RE.findall(query)


def _extract_ints(query: str) -> list[int]:
    return [int(m) for m in _INT_RE.findall(query)]


def _extract_emails(query: str) -> list[str]:
    return _EMAIL_RE.findall(query)


def _extract_file_ext_glob(query: str) -> Optional[str]:
    """Da 'trova file PDF' o 'i .tmp' → '*.pdf' / '*.tmp'.

    V1.5 19/5 v5: supporta esplicitamente "file PDF" / "files PDF" /
    "file di tipo PDF" senza punto. Caso live: il PLANNER spesso vede
    l'utente scrivere "file PDF" o "documenti PDF" senza glob.
    """
    m = _FILE_EXT_RE.search(query)
    if m:
        return f"*.{m.group('ext').lower()}"
    # "file PDF" / "files PDF" / "file di tipo PDF" / "documenti PDF"
    for kw in ("file", "files", "documento", "documenti", "document",
               "documents"):
        m = re.search(rf"\b{kw}\s+(?:di\s+tipo\s+|of\s+type\s+)?([A-Za-z]{{2,5}})\b",
                       query, re.IGNORECASE)
        if m and m.group(1).lower() not in ("di", "of", "tipo", "type",
                                              "the", "a", "una", "un"):
            return f"*.{m.group(1).lower()}"
    return None


def _extract_date_keyword(query: str) -> Optional[str]:
    """Estrae data ISO YYYY-MM-DD da keyword IT/EN.

    V1.5 19/5 v5. Esempi:
      "che eventi ho oggi" → 2026-05-19
      "i file di ieri"      → 2026-05-18
      "appuntamento domani" → 2026-05-20
    """
    q = query.lower()
    for kw, offset in _DATE_KEYWORDS_OFFSET.items():
        # Match parola intera (no "today" dentro a "today's" sufficienti).
        pattern = rf"\b{re.escape(kw)}\b"
        if re.search(pattern, q):
            dt = datetime.now(timezone.utc) + timedelta(days=offset)
            return dt.strftime("%Y-%m-%d")
    return None


def _extract_time_window(query: str) -> Optional[str]:
    """Estrae time_window canonical da keyword multi-parola IT/EN.

    V1.5 19/5 v5. Output formati supportati dall'executor (es. find_files,
    read_messages, ...): `last-Nh`, `last-Nd`, `this-week`, `last-week`,
    `next-week`, `this-month`, `today` (passa attraverso _extract_date_keyword).
    """
    q = query.lower()
    # Multi-word patterns prima (piu' specifici).
    for kw, canon in _TIME_WINDOW_KEYWORDS.items():
        if kw in q:
            return canon
    # Numero + unita': "ultimi 7 giorni" / "last 30 days".
    m = re.search(
        r"\b(?:ultim[oeai]|last)\s+(\d+)\s+(giorni?|days?|or[ae]|hours?)\b",
        q,
    )
    if m:
        n = m.group(1)
        unit = m.group(2)[0].lower()  # g/d/o/h
        if unit in ("g", "d"):
            return f"last-{n}d"
        return f"last-{n}h"
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


def regex_extract(query: str, schema: dict | None) -> dict:
    """Args extraction deterministica via regex. Ritorna dict (anche vuoto
    se nulla estratto). Solo i tipi standard (path/url/int/email/glob/date).

    Schema args (manifest [args.properties]) usato per filtrare quali
    estrazioni applicare:
      - args con name='paths' o 'path' → _extract_paths
      - 'url'/'urls' → _extract_urls
      - 'pattern' → _extract_file_ext_glob
      - 'max_*'/'top'/'limit' → _extract_ints (first)
      - 'to'/'recipient' → _extract_emails (first)
      - 'date'/'when' → _extract_date_keyword (V1.5 19/5 v5)
      - 'time_window'/'window'/'since' → _extract_time_window (V1.5 19/5 v5)

    Se `schema` e' None, ritorna dict vuoto (modo conservativo).
    """
    if not isinstance(schema, dict) or not query:
        return {}
    props = (schema.get("properties") or schema)
    out: dict = {}
    if not isinstance(props, dict):
        return {}
    for arg_name, _arg_spec in props.items():
        lname = arg_name.lower()
        # Pluralizzazione GUIDATA DALLO SCHEMA, non da suffissi lessicali
        # (lang-independent, universale): `type=array` -> lista,
        # `type=string` -> primo elemento. Il NOME dell'arg porta solo la
        # semantica (path/url/email/glob/date/time_window) che e' il
        # vocabolario chiuso §2.2 condiviso IT+EN.
        _spec = _arg_spec if isinstance(_arg_spec, dict) else {}
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
        if lname in ("path", "paths", "base_path", "src", "dst"):
            _emit(_extract_paths(query))
        elif lname in ("url", "urls", "src_url"):
            _emit(_extract_urls(query))
        elif lname in ("pattern", "patterns", "glob"):
            g = _extract_file_ext_glob(query)
            if g:
                _emit([g])
        elif lname in ("to", "recipient_id", "recipients", "email",
                       "to_user", "to_users"):
            _emit(_extract_emails(query))
        elif lname in ("repo", "repository"):
            r = _extract_repo_slug(query)
            if r:
                out[arg_name] = r
        elif lname in ("max_results", "max_total", "top", "limit", "n", "count"):
            ints = _extract_ints(query)
            if ints:
                # Heuristic: il numero piu' piccolo plausibile come cap.
                out[arg_name] = ints[0]
        elif lname in ("date", "day", "when", "on_date"):
            d = _extract_date_keyword(query)
            if d:
                out[arg_name] = d
        elif lname in ("time_window", "window", "since", "range"):
            w = _extract_time_window(query) or _extract_date_keyword(query)
            if w:
                out[arg_name] = w
    return out
