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


def site_collection_years(steps, extract_pos: int, query: str) -> tuple[int, ...]:
    """Bind explicit years to the Sites collection actually read.

    A date of a carrier (for example the year of source messages) must not
    become a filter on dates of newly extracted entities. This narrow bridge
    requires the concrete extract -> read_sites -> goal chain and the user's
    clause for that same goal. A quantified phrase or an exact goal-token
    match binds the clause. It does not reconstruct a period removed from
    the goal or choose among competing clauses.
    """
    from compound_decomposer import split_query_chunks
    from playwright_sidecar.action_resolver import (
        goal_scope_qualifier, goal_tokens,
        is_goal_navigation_request,
    )
    from time_window_resolver import absolute_years

    def source(position):
        if not isinstance(position, int) or not 1 <= position <= len(steps):
            return None
        ref = (getattr(steps[position - 1], "args", None) or {}).get("from_step")
        if isinstance(ref, str) and ref.isdigit():
            ref = int(ref)
        return ref if isinstance(ref, int) and 1 <= ref < position else None

    if (not isinstance(extract_pos, int)
            or not 1 <= extract_pos <= len(steps)
            or getattr(steps[extract_pos - 1], "tool", "") != "extract_entries"):
        return ()
    read_pos = source(extract_pos)
    if read_pos is None or getattr(steps[read_pos - 1], "tool", "") != "read_sites":
        return ()
    act_pos = source(read_pos)
    if act_pos is None or getattr(steps[act_pos - 1], "tool", "") != "act_sites":
        return ()
    act = getattr(steps[act_pos - 1], "args", None) or {}
    action = str(act.get("action") or "")
    if act.get("value_ref") or not is_goal_navigation_request(action):
        return ()
    target = set(goal_tokens(action))
    clauses = {clause for clause in split_query_chunks(query or "")
               if goal_scope_qualifier(clause, action)
               or target and set(goal_tokens(clause)) == target}
    if len(clauses) != 1:
        return ()
    years = absolute_years(next(iter(clauses)))
    if not years or not {str(year) for year in years} <= target:
        return ()
    return years


def period_where_regex(years: tuple[int, ...]) -> str:
    """One existing predicate for observed ISO dates or a year-only field."""
    return "^(?:" + "|".join(str(year) for year in years) + ")(?:-|$)"


def resolve_period_filter_field(args: dict, source_result: dict,
                                years: tuple[int, ...]) -> dict:
    """Bind the deferred predicate against the complete extracted schema.

    The caller supplies the actual from_step result and the years bound to its
    Sites goal. Neither row values nor a container label select the field.
    An unresolved predicate retains its missing where_field, so filter_entries
    returns its ordinary hard error instead of guessing or passing all rows.
    """
    if (not isinstance(args, dict) or not years
            or args.get("where_field") is not None
            or args.get("where_regex") != period_where_regex(years)
            or not isinstance(source_result, dict)):
        return args
    fields = source_result.get("fields")
    if not isinstance(fields, list):
        return args
    import detection_lexicon_seed_parsers as parser_lex
    import detection_lexicon_seed_reconciliation as reconciliation_lex

    temporal = parser_lex.load_family("time_resolver")
    reconciliation = reconciliation_lex.load()
    if temporal is None or reconciliation is None:
        return args
    year_forms = {form.casefold() for form in
                  temporal["parser.time.singular_unit"]["y"]}
    candidates = set()
    for field in fields:
        if not isinstance(field, str) or not field or field.startswith("_"):
            continue
        forms = {field.casefold(), *re.split(r"[_\W]+", field.casefold())}
        # The date-normalisation gate also accepts document identities.
        # Binding uses the distinct registered date/year vocabulary.
        if (forms & year_forms or any(
                reconciliation.canonical_field(form) in {
                    "deadline", "normalized_date", "normalized_time"}
                for form in forms)
                or reconciliation.matches_gate("period_date_field", field.casefold())
                or reconciliation.matches_gate("datetime_field", field.casefold())):
            candidates.add(field)
    if len(candidates) != 1:
        return args
    out = dict(args)
    out["where_field"] = candidates.pop()
    return out


def _singular(value: str) -> str:
    folded = value.casefold()
    return folded[:-1] if folded.endswith("s") else folded


def _drop_inherent_kind(args: dict, entries: list, query: str) -> dict:
    """Remove a carrier kind only when query and records prove it redundant."""
    if args.get("kind") is None or any(
            isinstance(entry, dict) and "kind" in entry for entry in entries):
        return args
    try:
        from prefilter import tokenize
        from vocab import canonical_object
        query_objects = {canonical_object(token) for token in tokenize(query or "")}
        raw_kinds = args.get("kind")
        raw_kinds = raw_kinds if isinstance(raw_kinds, list) else [raw_kinds]
        kind_objects = {canonical_object(str(item)) for item in raw_kinds}
        if (kind_objects - {None}) & (query_objects - {None}):
            out = dict(args)
            out.pop("kind", None)
            return out
    except Exception:
        pass
    return args


def _resolve_collection_presence(args: dict, entries: list, query: str) -> dict:
    """Map ``type=x`` to a uniquely matching non-empty collection field.

    Planner shorthand such as ``type=email`` describes the user's semantic
    selector, while materialized records commonly expose ``emails=[...]``.
    Convert only when the selector is explicit in the query and exactly one
    public record field matches by singular/plural spelling.
    """
    if args.get("type") is None or args.get("name_regex") is not None:
        return args
    if args.get("where_field") is not None or any(
            args.get(key) is not None for key in (
                "where_value", "where_in", "where_not_in",
                "where_starts_with", "where_contains", "where_glob",
                "where_regex", "where_present")):
        return args
    raw_types = args.get("type")
    raw_types = raw_types if isinstance(raw_types, list) else [raw_types]
    selectors = {_singular(str(item)) for item in raw_types if item is not None}
    words = {_singular(word) for word in re.findall(r"[^\W_]+", query or "")}
    explicit = selectors & words
    if len(explicit) != 1:
        return args
    fields = {
        field for entry in entries if isinstance(entry, dict)
        for field in entry if isinstance(field, str) and not field.startswith("_")
        and _singular(field) in explicit
    }
    if len(fields) != 1:
        return args
    field = next(iter(fields))
    # Presence semantics are meaningful for collection/scalar fields alike;
    # the executor defines empty string/list/null uniformly as absent.
    out = dict(args)
    out.pop("type", None)
    out["where_field"] = field
    out["where_present"] = True
    return _drop_inherent_kind(out, entries, query)


def _matching_fields(entries: list, pattern: re.Pattern) -> list[str]:
    matched: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for field, value in entry.items():
            if not isinstance(field, str) or field.startswith("_"):
                continue
            if value is None or isinstance(value, dict):
                continue
            values = value if isinstance(value, (list, tuple, set)) else [value]
            if any(pattern.search(str(item)) for item in values
                   if item is not None):
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
    if tool != "filter_entries" or not isinstance(args, dict):
        return args
    entries = args.get("entries")
    if not isinstance(entries, list) or not entries:
        return args
    presence = _resolve_collection_presence(args, entries, query)
    if presence is not args:
        return presence
    raw_pattern = args.get("name_regex")
    if not isinstance(raw_pattern, str) or not raw_pattern.strip():
        return args
    if args.get("where_field") is not None or any(
            args.get(key) is not None for key in (
                "where_value", "where_in", "where_not_in",
                "where_starts_with", "where_contains", "where_glob",
                "where_regex")):
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
    has_named_entries = any(
        isinstance(entry, dict) and entry.get("name") not in (None, "")
        for entry in entries)
    name_matches = "name" in fields
    if has_named_entries and not name_matches:
        # ``name_regex`` remains a precise contract unless the query names the
        # one alternative field that the pattern actually matches.  This
        # repairs an email-address regex over contact records without guessing
        # from the values alone.
        words = {word.casefold() for word in re.findall(r"[^\W_]+", query or "")}
        stems = {word[:-1] if word.endswith("s") else word for word in words}
        explicit = [field for field in fields if (
            field.casefold() in words
            or (field.casefold()[:-1] if field.casefold().endswith("s")
                else field.casefold()) in stems
        )]
        if len(explicit) != 1:
            return args
        fields = explicit
    if len(fields) != 1:
        return args
    out = dict(args)
    out.pop("name_regex", None)
    out["where_field"] = fields[0]
    out["where_regex"] = raw_pattern
    # A planner can redundantly filter by the carrier kind already selected by
    # the producer (find_contacts -> kind=contact), even when public records do
    # not repeat a ``kind`` key.  Remove it only when the query contains that
    # same canonical object; unrelated kinds still fail closed downstream.
    return _drop_inherent_kind(out, entries, query)
