"""args_extractor — V2 hybrid args extraction per canonical_matcher.

Sblocca il fast-path introvertivo (ADR 0149) per executor con args
required: oggi `canonical_matcher.try_match` ritorna `args={}` e il
fallback cade al PLANNER. Con extraction args robusta, il fast-path
serve direttamente con args dedotti dalla query.

Architettura hybrid C (Roberto 19/5/2026 v4):
  1. **regex_extract** (deterministico, §7.9): estrae token tipati
     comuni dalla query con regex chiusa (PATH, URL, INT, EMAIL, DATE).
  2. **learned_from_log** (memoization): se la canonical_query_log
     ha `args_observed` per la stessa entry, riusa quei valori (zero LLM).
  3. **llm_fallback** (futuro, opt-in): chiamata LLM fast tier (~500 ms)
     per casi complessi non risolti da 1+2.

V1 implementato: 1 + 2. LLM fallback lasciato come hook per opt-in.

Determinismo §7.9: regex deterministica, niente LLM in critical path
del primo passaggio. LLM solo se 1+2 falliscono E flag opt-in attivo.

Esposto:
    extract_args(query, executor_name, schema, observed_args=None) -> dict | None
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
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

# DATE iso o "oggi/ieri/domani"
_DATE_KEYWORDS = {
    "oggi", "ieri", "domani", "today", "yesterday", "tomorrow",
}

# Pattern file con extension (*.ext, .ext)
_FILE_EXT_RE = re.compile(r"\*?\.(?P<ext>[a-zA-Z0-9]{1,5})\b")


def _extract_paths(query: str) -> list[str]:
    """Estrae path-like da query. Filtra URL (che hanno '/' ma non sono path)."""
    urls = set(_URL_RE.findall(query))
    out = []
    for m in _PATH_RE.finditer(query):
        p = m.group(1).strip()
        if p and not any(p in u for u in urls):
            out.append(p)
    return out


def _extract_urls(query: str) -> list[str]:
    return _URL_RE.findall(query)


def _extract_ints(query: str) -> list[int]:
    return [int(m) for m in _INT_RE.findall(query)]


def _extract_emails(query: str) -> list[str]:
    return _EMAIL_RE.findall(query)


def _extract_file_ext_glob(query: str) -> Optional[str]:
    """Da 'trova file PDF' o 'i .tmp' → '*.pdf' / '*.tmp'."""
    m = _FILE_EXT_RE.search(query)
    if m:
        return f"*.{m.group('ext').lower()}"
    # Anche "file PDF" senza punto esplicito → estensione capitalizzata
    m = re.search(r"\bfile[s]?\s+([A-Z]{2,5})\b", query)
    if m:
        return f"*.{m.group(1).lower()}"
    return None


def regex_extract(query: str, schema: dict | None) -> dict:
    """Args extraction deterministica via regex. Ritorna dict (anche vuoto
    se nulla estratto). Solo i tipi standard (path/url/int/email/glob).

    Schema args (manifest [args.properties]) usato per filtrare quali
    estrazioni applicare:
      - args con name='paths' o 'path' → _extract_paths
      - 'url'/'urls' → _extract_urls
      - 'pattern' → _extract_file_ext_glob
      - 'max_*'/'top'/'limit' → _extract_ints (first)
      - 'to'/'recipient' → _extract_emails (first)

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
        if lname in ("path", "paths", "base_path", "src", "dst"):
            paths = _extract_paths(query)
            if paths:
                # plural args ricevono lista, singular il primo.
                out[arg_name] = paths if lname.endswith("s") else paths[0]
        elif lname in ("url", "urls", "src_url"):
            urls = _extract_urls(query)
            if urls:
                out[arg_name] = urls if lname.endswith("s") else urls[0]
        elif lname in ("pattern", "patterns", "glob"):
            g = _extract_file_ext_glob(query)
            if g:
                out[arg_name] = g if not lname.endswith("s") else [g]
        elif lname in ("to", "recipient_id", "email"):
            mails = _extract_emails(query)
            if mails:
                out[arg_name] = mails[0] if not lname.endswith("s") else mails
        elif lname in ("max_results", "max_total", "top", "limit", "n", "count"):
            ints = _extract_ints(query)
            if ints:
                # Heuristic: il numero piu' piccolo plausibile come cap.
                out[arg_name] = ints[0]
    return out


def extract_args(
    query: str,
    executor_name: str,
    schema: dict | None,
    *,
    observed_args: dict | None = None,
    llm_fallback: bool = False,
) -> dict:
    """Args extraction hybrid V2 (ADR 0149 + 0150 19/5/2026 v4).

    Ordine (deterministic):
      1. `observed_args` (memoization da canonical_query_log): se presente
         e non vuoto, viene preferito (already-learned at first planner call).
      2. `regex_extract`: pattern deterministici PATH/URL/INT/EMAIL/GLOB.
      3. `llm_fallback`: HOOK (default False). Quando opt-in, chiama LLM
         fast tier per estrarre args missing rispetto allo schema required.
         Non implementato V1; ritorna (2) come-is.

    Determinismo §7.9: 1+2 zero-LLM. LLM solo se esplicitamente attivato.
    """
    args: dict = {}
    if isinstance(observed_args, dict) and observed_args:
        args.update(observed_args)
    extracted = regex_extract(query, schema)
    if extracted:
        for k, v in extracted.items():
            args.setdefault(k, v)
    if llm_fallback:
        # Hook futuro: chiamata LLM tier fast per args required missing.
        # Non implementato V1.
        pass
    return args
