"""Allinea un filtro ``name_regex`` a record strutturati senza ``name``.

``extract_entries`` produce campi scelti dalla richiesta (per esempio
``destinazione``), mentre un planner puo' usare il filtro abbreviato
``name_regex``. Se i record non hanno ``name``, quel filtro eliminerebbe tutto.

La correzione e' data-driven e fail-closed. Se ogni record proviene da un
contenitore gia' selezionato dalla stessa regex, il predicato e' gia'
soddisfatto e non viene riapplicato ai figli. Altrimenti prova la regex sui
soli campi pubblici scalari e la converte in ``where_field``/``where_regex``
soltanto se esiste un unico campo compatibile. Nessun nome di dominio o lingua
e' hardcoded; zero o piu' candidati lasciano decidere al normale validator.
"""
from __future__ import annotations

import re


def _matching_fields(entries: list, pattern: re.Pattern) -> list[str]:
    matched: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for field, value in entry.items():
            if not isinstance(field, str) or field.startswith("_"):
                continue
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            if pattern.search(str(value)):
                matched.add(field)
    return sorted(matched)


def _all_source_scopes_match(entries: list, pattern: re.Pattern) -> bool:
    """True only when every child inherits a matching selected container."""
    if not entries:
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        label = entry.get("_source_scope_label")
        if not isinstance(label, str) or not label.strip() \
                or not pattern.search(label):
            return False
    return True


def resolve_filter_field(tool: str, args: dict, query: str) -> dict:
    """Converte il filtro-name solo quando il campo reale e' inequivocabile."""
    del query  # API uniforme; la risoluzione usa i dati, non parole NL
    if tool != "filter_entries" or not isinstance(args, dict):
        return args
    raw_pattern = args.get("name_regex")
    entries = args.get("entries")
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        return args
    if not isinstance(entries, list) or not entries:
        return args
    if args.get("where_field") is not None or any(
            args.get(key) is not None for key in (
                "where_value", "where_in", "where_not_in",
                "where_starts_with", "where_contains", "where_glob",
                "where_regex")):
        return args
    # `filter_entries.name_regex` ha gia' semantica precisa quando il carrier
    # espone `name`: non reinterpretarlo nemmeno se non produce match.
    if any(isinstance(entry, dict) and entry.get("name") not in (None, "")
           for entry in entries):
        return args
    try:
        pattern = re.compile(raw_pattern, re.IGNORECASE)
    except re.error:
        return args  # l'executor produrra' l'errore canonico di regex invalida
    if _all_source_scopes_match(entries, pattern):
        out = dict(args)
        out.pop("name_regex", None)
        return out
    fields = _matching_fields(entries, pattern)
    if len(fields) != 1:
        return args
    out = dict(args)
    out.pop("name_regex", None)
    out["where_field"] = fields[0]
    out["where_regex"] = raw_pattern
    return out
