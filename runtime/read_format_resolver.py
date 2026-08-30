"""Canonicalizza la lettura di collezioni documentali locali.

I piani LLM/cachati tendono a usare ``parse=json`` come placeholder anche per
PDF/DOCX/XLSX/CSV. Questo resolver sceglie il parser misto dichiarato dal
manifest usando prima la shape materializzata dei path e, sui piani ancora
astratti, i formati espliciti della query. Nessun I/O e nessun LLM.
"""
from __future__ import annotations

from pathlib import PurePath

import detection_lexicon as _detlex
import detection_lexicon_seed_resolvers as _resolver_seed


_AUTO_SUFFIXES = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md",
    ".html", ".htm", ".xml",
})
def _paths(args: dict) -> list[str]:
    values = args.get("paths")
    if isinstance(values, list):
        return [value for value in values if isinstance(value, str)]
    entries = args.get("entries")
    if isinstance(entries, list):
        return [entry.get("path") for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("path"), str)]
    path = args.get("path")
    return [path] if isinstance(path, str) else []


def resolve_read_format(tool: str, args: dict, query: str, *,
                        args_schema=None) -> dict:
    if tool != "read_files" or not isinstance(args, dict):
        return args
    if str(args.get("client") or "local") != "local":
        return args
    props = (args_schema or {}).get("properties") \
        if isinstance(args_schema, dict) else None
    parse_schema = props.get("parse") if isinstance(props, dict) else None
    if not isinstance(parse_schema, dict) or "auto" not in (parse_schema.get("enum") or []):
        return args

    paths = _paths(args)
    materialized_mixed = any(
        PurePath(path.replace("\\", "/")).suffix.casefold() in _AUTO_SUFFIXES
        and PurePath(path.replace("\\", "/")).suffix.casefold() != ".json"
        for path in paths)
    _resolver_seed.ensure_registered()
    explicit_document_formats = _detlex.match(
        "resolver.read_document_format", query or "",
    )
    if not (materialized_mixed or explicit_document_formats):
        return args

    out = dict(args)
    out["parse"] = "auto"
    if _detlex.match("resolver.read_deduplicate", query or ""):
        out["deduplicate_content"] = True
    return out if out != args else args
