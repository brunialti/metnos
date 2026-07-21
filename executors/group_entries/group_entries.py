#!/usr/bin/env python3
"""group_entries — merge entries da N step diversi in una lista unica.

Tipico in pipeline split read_html / read_pdf:
    1. find_urls -> entries con content_type misto
    2. filter_entries (text/html) -> read_urls_html
    3. filter_entries (application/pdf) -> read_urls_pdf
    4. group_entries(from_steps=[2,3]) -> entries unica deduplicata per url

Args:
    from_steps: list[int]   N step di provenienza, runtime espande
    entries_lists: list[list[dict]]  alternativa diretta
    dedup_key: str = "url"  campo per deduplicazione (None = no dedup)

Output: entries=[merged...], total_in:int, dedupes:int.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


def invoke(args: dict) -> dict:
    # Il runtime espande from_steps[i] in args["entries_lists"][i]
    # SOLO se la convenzione e' supportata. Per ora supportiamo
    # `entries_lists` come argomento esplicito (lista di liste di dict),
    # `from_steps` come informazione di provenienza che il runtime
    # serializza preventivamente (estensione futura del runtime).
    entries_lists = args.get("entries_lists")
    if entries_lists is None:
        # Fallback: se passato `entries` (lista piatta), trattiamo come 1 lista.
        entries = args.get("entries")
        if isinstance(entries, list):
            entries_lists = [entries]
        else:
            entries_lists = []

    if not isinstance(entries_lists, list):
        return {
            "ok": False,
            "error_class": "invalid_input",
            "error_code": "entries_lists_not_list",
            "error": _msg("ERR_ARG_NOT_LIST_OF", arg="entries_lists", of="lists"),
        }

    dedup_key = args.get("dedup_key", "url")
    if dedup_key in ("", None, 0):
        dedup_keys = None
    elif isinstance(dedup_key, str):
        dedup_keys = [dedup_key]
    elif (isinstance(dedup_key, list) and dedup_key
          and all(isinstance(field, str) and field for field in dedup_key)):
        dedup_keys = list(dedup_key)
    else:
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "dedup_key_invalid",
                "error": "dedup_key must be a field name or list of names"}
    cross_domain_key = args.get("cross_domain_key")
    domain_field = args.get("domain_field") or "dominio"
    cross_match_fields = args.get("cross_match_fields") or []
    missing_conflict_fields = args.get("missing_conflict_fields") or []
    missing_value_label = str(args.get("missing_value_label") or "missing")
    required_fields_by_domain = args.get("required_fields_by_domain") or {}
    unmatched_conflict_key = args.get("unmatched_conflict_key")
    unmatched_conflict_fields = args.get("unmatched_conflict_fields") or []
    match_field = args.get("match_field")
    match_labels = args.get("match_labels") or {}
    match_state_field = args.get("match_state_field") or "stato"
    cancellation_states = args.get("cancellation_states") or []
    reconcile_within_domains = args.get("reconcile_within_domains") or []
    anchor_field = args.get("anchor_field")
    anchor_equal_fields = args.get("anchor_equal_fields") or []
    anchor_match_fields = args.get("anchor_match_fields") or []
    anchor_within_domains = args.get("anchor_within_domains") or []
    drop_unmatched_domains = args.get("drop_unmatched_domains") or []
    coalesce_source_facts = bool(args.get("coalesce_source_facts", False))
    source_field = args.get("source_field") or "origine"
    type_field = args.get("type_field") or "tipo"
    subject_types = args.get("subject_types") or [
        "progetto", "project", "impegno", "commitment"]
    coalesce_fields = args.get("coalesce_fields") or [
        "organizzazione", "importo", "scadenza", "decisione", "stato",
        "responsabile",
    ]
    if cross_domain_key in (None, "", []):
        cross_domain_keys = None
    elif isinstance(cross_domain_key, str):
        cross_domain_keys = [cross_domain_key]
    elif (isinstance(cross_domain_key, list) and cross_domain_key
          and all(isinstance(field, str) and field
                  for field in cross_domain_key)):
        cross_domain_keys = list(cross_domain_key)
    else:
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "cross_domain_key_invalid",
                "error": ("cross_domain_key must be a field name or "
                          "non-empty list of field names")}
    if unmatched_conflict_key in (None, "", []):
        unmatched_conflict_keys = None
    elif isinstance(unmatched_conflict_key, str):
        unmatched_conflict_keys = [unmatched_conflict_key]
    elif (isinstance(unmatched_conflict_key, list)
          and unmatched_conflict_key
          and all(isinstance(field, str) and field
                  for field in unmatched_conflict_key)):
        unmatched_conflict_keys = list(unmatched_conflict_key)
    else:
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "unmatched_conflict_key_invalid",
                "error": ("unmatched_conflict_key must be a field name or "
                          "non-empty list of field names")}
    if not isinstance(domain_field, str) or not domain_field:
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "domain_field_invalid",
                "error": "domain_field must be a field name"}
    if not (isinstance(cross_match_fields, list)
            and all(isinstance(field, str) and field
                    for field in cross_match_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "cross_match_fields_invalid",
                "error": "cross_match_fields must be a list of field names"}
    if not (isinstance(missing_conflict_fields, list)
            and all(isinstance(field, str) and field
                    for field in missing_conflict_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "missing_conflict_fields_invalid",
                "error": ("missing_conflict_fields must be a list of field "
                          "names")}
    if not (isinstance(required_fields_by_domain, dict)
            and all(isinstance(domain, str) and domain.strip()
                    and isinstance(fields, list)
                    and all(isinstance(field, str) and field
                            for field in fields)
                    for domain, fields in required_fields_by_domain.items())):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "required_fields_by_domain_invalid",
                "error": ("required_fields_by_domain must map domain names "
                          "to lists of field names")}
    if not (isinstance(unmatched_conflict_fields, list)
            and all(isinstance(field, str) and field
                    for field in unmatched_conflict_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "unmatched_conflict_fields_invalid",
                "error": ("unmatched_conflict_fields must be a list of field "
                          "names")}
    if match_field is not None and not (
            isinstance(match_field, str) and match_field.strip()):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "match_field_invalid",
                "error": "match_field must be a non-empty field name"}
    if not (isinstance(match_labels, dict)
            and all(isinstance(key, str) and isinstance(value, str)
                    and key and value for key, value in match_labels.items())):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "match_labels_invalid",
                "error": "match_labels must be an object of string labels"}
    if not (isinstance(match_state_field, str) and match_state_field
            and isinstance(cancellation_states, list)
            and all(isinstance(value, str) and value.strip()
                    for value in cancellation_states)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "match_state_invalid",
                "error": ("match_state_field must be a field name and "
                          "cancellation_states a list of strings")}
    if not (isinstance(reconcile_within_domains, list)
            and all(isinstance(domain, str) and domain.strip()
                    for domain in reconcile_within_domains)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "reconcile_within_domains_invalid",
                "error": "reconcile_within_domains must be a list of domains"}
    if anchor_field is not None and not (
            isinstance(anchor_field, str) and anchor_field.strip()):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "anchor_field_invalid",
                "error": "anchor_field must be a non-empty field name"}
    if not (isinstance(anchor_equal_fields, list)
            and all(isinstance(field, str) and field
                    for field in anchor_equal_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "anchor_equal_fields_invalid",
                "error": "anchor_equal_fields must be a list of field names"}
    if not (isinstance(anchor_match_fields, list)
            and all(isinstance(field, str) and field
                    for field in anchor_match_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "anchor_match_fields_invalid",
                "error": "anchor_match_fields must be a list of field names"}
    if not (isinstance(anchor_within_domains, list)
            and all(isinstance(domain, str) and domain.strip()
                    for domain in anchor_within_domains)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "anchor_within_domains_invalid",
                "error": "anchor_within_domains must be a list of domains"}
    if anchor_field and (not anchor_equal_fields or not anchor_match_fields):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "anchor_policy_incomplete",
                "error": ("anchor_field requires non-empty "
                          "anchor_equal_fields and anchor_match_fields")}
    if not (isinstance(drop_unmatched_domains, list)
            and all(isinstance(domain, str) and domain.strip()
                    for domain in drop_unmatched_domains)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "drop_unmatched_domains_invalid",
                "error": "drop_unmatched_domains must be a list of domains"}
    if (not isinstance(source_field, str) or not source_field
            or not isinstance(type_field, str) or not type_field):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "coalesce_field_invalid",
                "error": "source_field and type_field must be field names"}
    if not (isinstance(subject_types, list) and subject_types
            and all(isinstance(value, str) and value.strip()
                    for value in subject_types)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "subject_types_invalid",
                "error": "subject_types must be a non-empty list of strings"}
    if not (isinstance(coalesce_fields, list)
            and all(isinstance(field, str) and field
                    for field in coalesce_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "coalesce_fields_invalid",
                "error": "coalesce_fields must be a list of field names"}
    reconcile_within_domains = {
        domain.strip().casefold() for domain in reconcile_within_domains}
    anchor_within_domains = {
        domain.strip().casefold() for domain in anchor_within_domains}
    drop_unmatched_domains = {
        domain.strip().casefold() for domain in drop_unmatched_domains}
    required_fields_by_domain = {
        domain.strip().casefold(): list(fields)
        for domain, fields in required_fields_by_domain.items()
    }
    merge_fields = args.get("merge_fields") or []
    conflict_fields = args.get("conflict_fields") or []
    conflict_field = args.get("conflict_field") or "conflitto"
    if not (isinstance(merge_fields, list)
            and all(isinstance(field, str) and field for field in merge_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "merge_fields_not_list",
                "error": _msg("ERR_ARG_NOT_LIST_OF", arg="merge_fields",
                              of="strings")}
    if not (isinstance(conflict_fields, list)
            and all(isinstance(field, str) and field
                    for field in conflict_fields)):
        return {"ok": False, "error_class": "invalid_input",
                "error_code": "conflict_fields_not_list",
                "error": _msg("ERR_ARG_NOT_LIST_OF", arg="conflict_fields",
                              of="strings")}

    def _values(value) -> list:
        raw = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in raw
                if item not in (None, "", []) and str(item).strip()]

    def _conflict_values(value) -> list[str]:
        """Return the stable, individually sortable conflict details.

        ``conflict_field`` is serialized as ``"; ".join(details)`` below.
        Reading it back through ``_values`` would turn all prior details into
        one item on a third merge, undercounting severity and weakening
        duplicate detection.  Keep this parser private because the technical
        ``_conflict_count`` field is runtime metadata, not user-authored data.
        """
        raw = value if isinstance(value, list) else [value]
        details: list[str] = []
        for item in raw:
            if item in (None, "", []):
                continue
            details.extend(
                detail.strip() for detail in str(item).split("; ")
                if detail.strip())
        return details

    def _merge_value(left, right):
        values = []
        seen_values = set()
        for value in [*_values(left), *_values(right)]:
            folded = value.casefold()
            if folded not in seen_values:
                seen_values.add(folded)
                values.append(value)
        return "; ".join(values)

    def _key_part(value):
        if value in (None, "", []):
            return None
        if isinstance(value, str):
            return value.strip().casefold() or None
        try:
            hash(value)
        except TypeError:
            return None
        return value

    def _lookup_key(item: dict, fields: list[str] | None):
        if not fields:
            return None
        values = tuple(_key_part(item.get(field)) for field in fields)
        return None if any(value is None for value in values) else values

    def _domains(item: dict) -> set[str]:
        value = item.get(domain_field)
        raw = value if isinstance(value, list) else [value]
        domains = set()
        for part in raw:
            if part in (None, "", []):
                continue
            domains.update(
                value.strip().casefold()
                for value in str(part).split(";") if value.strip())
        return domains

    def _cross_match_score(existing: dict, item: dict) -> int:
        # Prefer the same occurrence/value when several records share the
        # same entity. A unique cross-domain candidate may still be merged so
        # differing dates or states can be surfaced as the requested conflict.
        score = 0
        for index, field in enumerate(cross_match_fields):
            weight = 1 << max(0, len(cross_match_fields) - index - 1)
            left, right = _key_part(existing.get(field)), _key_part(item.get(field))
            if left is not None and left == right:
                score += weight
        return score

    def _text_key(value) -> str:
        normalized = unicodedata.normalize(
            "NFKD", str(value or "").casefold())
        normalized = "".join(
            char for char in normalized
            if not unicodedata.combining(char))
        return " ".join(re.findall(r"[a-z0-9@.+_-]+", normalized))

    def _anchors(item: dict) -> set[str]:
        if not anchor_field:
            return set()
        return {key for value in _values(item.get(anchor_field))
                if (key := _text_key(value))}

    def _field_similarity(left, right) -> int:
        """Bounded lexical evidence score (0..4), never a merge decision.

        Exact observed values are strongest; token containment handles stable
        aliases such as "Policlinico Gemelli" vs "Policlinico Agostino
        Gemelli".  The caller still requires shared declared anchors, exact
        occurrence fields and a unique best candidate.
        """
        left_values = {_text_key(value) for value in _values(left)}
        right_values = {_text_key(value) for value in _values(right)}
        left_values.discard("")
        right_values.discard("")
        if not left_values or not right_values:
            return 0
        if left_values & right_values:
            return 4
        left_tokens = {token for value in left_values for token in value.split()}
        right_tokens = {token for value in right_values for token in value.split()}
        common = left_tokens & right_tokens
        if not common:
            return 0
        if left_tokens <= right_tokens or right_tokens <= left_tokens:
            return 3
        overlap = len(common) / max(1, min(len(left_tokens), len(right_tokens)))
        return 2 if overlap >= 0.5 else 1

    def _anchor_match_score(existing: dict, item: dict) -> int:
        score = 0
        field_count = len(anchor_match_fields)
        for index, field in enumerate(anchor_match_fields):
            weight = 1 << max(0, field_count - index - 1)
            score += weight * _field_similarity(
                existing.get(field), item.get(field))
        return score

    def _anchor_candidate(existing: dict, item: dict,
                          existing_domains: set[str],
                          item_domains: set[str]) -> bool:
        if not anchor_field or not existing_domains or not item_domains:
            return False
        shared_domain = existing_domains & item_domains
        if (shared_domain
                and not bool(shared_domain & anchor_within_domains)):
            return False
        if not (_anchors(existing) & _anchors(item)):
            return False
        return all(
            (left := _key_part(existing.get(field))) is not None
            and left == _key_part(item.get(field))
            for field in anchor_equal_fields
        )

    subject_type_set = {
        value.strip().casefold() for value in subject_types if value.strip()}
    coalesced_source_facts = 0

    def _coalesce_unique_subject_facts(items: list) -> list:
        """Attach unambiguous source-level facts to one declared subject.

        This is deliberately opt-in and fail-closed.  A source containing zero
        or several subject rows is untouched; a field with zero or several
        distinct candidate values is untouched.  Standalone fact rows remain
        in the result, so coalescence cannot erase observed information.
        """
        nonlocal coalesced_source_facts
        copied = [dict(item) if isinstance(item, dict) else item
                  for item in items]
        by_source: dict[str, list[int]] = {}
        for position, item in enumerate(copied):
            if not isinstance(item, dict):
                continue
            source = item.get(source_field)
            if source in (None, "", []):
                continue
            by_source.setdefault(str(source), []).append(position)
        for positions in by_source.values():
            subjects = [
                position for position in positions
                if str(copied[position].get(type_field) or "").strip().casefold()
                in subject_type_set
            ]
            if len(subjects) != 1:
                continue
            subject = copied[subjects[0]]
            for field in coalesce_fields:
                if subject.get(field) not in (None, "", []):
                    continue
                values: list[str] = []
                seen_values: set[str] = set()
                for position in positions:
                    if position == subjects[0]:
                        continue
                    for value in _values(copied[position].get(field)):
                        folded = value.casefold()
                        if folded not in seen_values:
                            seen_values.add(folded)
                            values.append(value)
                if len(values) == 1:
                    subject[field] = values[0]
                    coalesced_source_facts += 1
        return copied

    merged: list[dict] = []
    seen: dict[object, int] = {}
    cross_seen: dict[object, list[int]] = {}
    domains_by_index: dict[int, set[str]] = {}
    total_in = 0
    dedupes = 0
    conflict_count = 0
    anchor_reconciliations = 0
    failed: list[dict] = []
    for index, sub in enumerate(entries_lists):
        if not isinstance(sub, list):
            failed.append({
                "index": index,
                "error_class": "invalid_input",
                "error_code": "entry_list_not_list",
                "error": _msg("ERR_ARG_NOT_LIST_OF", arg=f"entries_lists[{index}]", of="dicts"),
            })
            continue
        if coalesce_source_facts:
            sub = _coalesce_unique_subject_facts(sub)
        for item in sub:
            total_in += 1
            if not isinstance(item, dict):
                # tieni elementi non-dict come stringa
                merged.append({"value": item}); continue
            if dedup_keys is None:
                merged.append(item)
                continue
            lookup_key = _lookup_key(item, dedup_keys)
            cross_key = _lookup_key(item, cross_domain_keys)
            item_domains = _domains(item)
            # A partial exact key is not sufficient for same-domain
            # deduplication, but a complete, explicitly configured
            # cross-domain key can still reconcile the record.  This matters
            # for honest comparisons such as an email with a time and a
            # calendar event where the time is absent: the absence must be
            # surfaced as a conflict instead of silently producing two rows.
            duplicate_index = (seen.get(lookup_key)
                               if lookup_key is not None else None)
            if (duplicate_index is None and cross_key is not None
                    and item_domains):
                candidates = [
                    candidate for candidate in cross_seen.get(cross_key, [])
                    if (domains_by_index.get(candidate, set()).isdisjoint(
                            item_domains)
                        or bool(domains_by_index.get(candidate, set())
                                & item_domains
                                & reconcile_within_domains))
                ]
                if len(candidates) == 1:
                    duplicate_index = candidates[0]
                elif len(candidates) > 1:
                    scored = [
                        (_cross_match_score(merged[candidate], item), candidate)
                        for candidate in candidates
                    ]
                    best = max(score for score, _candidate in scored)
                    best_candidates = [candidate for score, candidate in scored
                                       if score == best]
                    if best > 0 and len(best_candidates) == 1:
                        duplicate_index = best_candidates[0]
            anchor_matched = False
            if duplicate_index is None and anchor_field and item_domains:
                anchor_candidates = [
                    candidate for candidate, existing in enumerate(merged)
                    if isinstance(existing, dict)
                    and _anchor_candidate(
                        existing, item,
                        domains_by_index.get(candidate, set())
                        or _domains(existing),
                        item_domains)
                ]
                scored = [
                    (_anchor_match_score(merged[candidate], item), candidate)
                    for candidate in anchor_candidates
                ]
                if scored:
                    best = max(score for score, _candidate in scored)
                    best_candidates = [
                        candidate for score, candidate in scored
                        if score == best
                    ]
                    # Shared scope anchors and occurrence fields only define
                    # candidates.  At least one independent matching signal
                    # and one unique best score are required to join them.
                    if best > 0 and len(best_candidates) == 1:
                        duplicate_index = best_candidates[0]
                        anchor_matched = True
            if duplicate_index is not None:
                dedupes += 1
                if anchor_matched:
                    anchor_reconciliations += 1
                existing = merged[duplicate_index]
                for field in merge_fields:
                    merged_value = _merge_value(
                        existing.get(field), item.get(field))
                    if merged_value:
                        existing[field] = merged_value
                # Private runtime evidence stays lossless across a chain of
                # joins.  Keep lists (rather than a rendered semicolon string)
                # so later candidates can still compare individual facts.
                if anchor_field:
                    evidence_fields = [
                        anchor_field,
                        *[field for field in anchor_match_fields
                          if field.startswith("_")],
                    ]
                    for field in dict.fromkeys(evidence_fields):
                        values = []
                        seen_evidence = set()
                        for value in [*_values(existing.get(field)),
                                      *_values(item.get(field))]:
                            key = _text_key(value)
                            if key and key not in seen_evidence:
                                seen_evidence.add(key)
                                values.append(value)
                        if values:
                            existing[field] = values
                conflicts = _conflict_values(existing.get(conflict_field))
                for field in conflict_fields:
                    left = _values(existing.get(field))
                    right = _values(item.get(field))
                    if not left or not right:
                        continue
                    if {value.casefold() for value in left} == {
                            value.casefold() for value in right}:
                        continue
                    detail = f"{field}: {left[0]} ↔ {right[0]}"
                    if detail.casefold() not in {
                            value.casefold() for value in conflicts}:
                        conflicts.append(detail)
                        conflict_count += 1
                for field in missing_conflict_fields:
                    left = _values(existing.get(field))
                    right = _values(item.get(field))
                    if bool(left) == bool(right):
                        continue
                    left_value = left[0] if left else f"[{missing_value_label}]"
                    right_value = right[0] if right else f"[{missing_value_label}]"
                    detail = f"{field}: {left_value} ↔ {right_value}"
                    if detail.casefold() not in {
                            value.casefold() for value in conflicts}:
                        conflicts.append(detail)
                        conflict_count += 1
                if conflicts:
                    existing[conflict_field] = "; ".join(conflicts)
                    # Runtime-only ranking signal: more independently
                    # contradictory fields means higher deterministic
                    # severity.  The leading underscore keeps it out of
                    # automatic report/spreadsheet schemas while allowing a
                    # later sort_entries step to consume it explicitly.
                    existing["_conflict_count"] = len(conflicts)
                if lookup_key is not None:
                    seen.setdefault(lookup_key, duplicate_index)
                domains_by_index.setdefault(duplicate_index, set()).update(
                    item_domains)
                continue
            if lookup_key is not None:
                seen[lookup_key] = len(merged)
            if cross_key is not None:
                cross_seen.setdefault(cross_key, []).append(len(merged))
            domains_by_index[len(merged)] = set(item_domains)
            merged.append(item)

    def _append_conflict(item: dict, detail: str) -> bool:
        """Append one stable conflict detail and its ranking signal."""
        nonlocal conflict_count
        conflicts = _conflict_values(item.get(conflict_field))
        if detail.casefold() in {value.casefold() for value in conflicts}:
            return False
        conflicts.append(detail)
        item[conflict_field] = "; ".join(conflicts)
        item["_conflict_count"] = len(conflicts)
        conflict_count += 1
        return True

    # Opt-in completeness checks apply also to rows that never found a
    # counterpart.  A calendar-only all-day item can therefore be reported as
    # "time missing" without inventing an email/calendar relationship.
    if required_fields_by_domain:
        for position, item in enumerate(merged):
            if not isinstance(item, dict):
                continue
            item_domains = domains_by_index.get(position, set()) or _domains(item)
            required = {
                field for domain, fields in required_fields_by_domain.items()
                if domain in item_domains for field in fields
            }
            for field in sorted(required):
                if not _values(item.get(field)):
                    _append_conflict(
                        item, f"{field}: [{missing_value_label}]")

    # A different date must not make two appointments collapse.  When exactly
    # one email-only and one calendar-only row share the stronger declared
    # identity key, annotate their differing fields while keeping both rows.
    # Ambiguous 1:N/N:M buckets are deliberately untouched: choosing a pair
    # there would manufacture a relationship from weak evidence.
    if unmatched_conflict_keys and unmatched_conflict_fields:
        buckets: dict[object, list[int]] = {}
        for position, item in enumerate(merged):
            if not isinstance(item, dict):
                continue
            item_domains = domains_by_index.get(position, set()) or _domains(item)
            if item_domains not in ({"email"}, {"calendar"}):
                continue
            related_key = _lookup_key(item, unmatched_conflict_keys)
            if related_key is not None:
                buckets.setdefault(related_key, []).append(position)
        for positions in buckets.values():
            email_rows = [position for position in positions
                          if (domains_by_index.get(position, set())
                              or _domains(merged[position])) == {"email"}]
            calendar_rows = [position for position in positions
                             if (domains_by_index.get(position, set())
                                 or _domains(merged[position])) == {"calendar"}]
            if len(email_rows) != 1 or len(calendar_rows) != 1:
                continue
            left = merged[email_rows[0]]
            right = merged[calendar_rows[0]]
            for field in unmatched_conflict_fields:
                left_values = _values(left.get(field))
                right_values = _values(right.get(field))
                if not left_values or not right_values:
                    continue
                if {value.casefold() for value in left_values} == {
                        value.casefold() for value in right_values}:
                    continue
                detail = (f"{field}: {left_values[0]} ↔ {right_values[0]} "
                          "(possibile divergenza, non unificati)")
                _append_conflict(left, detail)
                _append_conflict(right, detail)

    match_counts: dict[str, int] = {}
    if match_field:
        labels = {
            "exact": "exact_match",
            "probable": "probable_match",
            "email_only": "email_only",
            "calendar_only": "calendar_only",
            "cancelled": "cancellation_without_event",
            "unmatched": "unmatched",
            **match_labels,
        }
        cancellation_needles = {
            value.strip().casefold() for value in cancellation_states}
        for position, item in enumerate(merged):
            if not isinstance(item, dict):
                continue
            item_domains = (domains_by_index.get(position, set())
                            or _domains(item))
            if {"email", "calendar"} <= item_domains:
                label_key = ("probable" if item.get(conflict_field)
                             else "exact")
            elif item_domains == {"email"}:
                states = " ".join(_values(item.get(match_state_field))).casefold()
                cancelled = any(value in states
                                for value in cancellation_needles)
                label_key = "cancelled" if cancelled else "email_only"
            elif item_domains == {"calendar"}:
                label_key = "calendar_only"
            else:
                label_key = "unmatched"
            label = labels[label_key]
            item[match_field] = label
            match_counts[label] = match_counts.get(label, 0) + 1

    dropped_unmatched = 0
    if drop_unmatched_domains:
        kept = []
        for item in merged:
            item_domains = _domains(item) if isinstance(item, dict) else set()
            if item_domains and item_domains <= drop_unmatched_domains:
                dropped_unmatched += 1
                continue
            kept.append(item)
        merged = kept

    partial = bool(failed) and bool(merged)
    return {
        "ok": not failed or partial,
        "ok_count": len(merged),
        "fail_count": len(failed),
        "entries": merged,
        "total_in": total_in,
        "dedupes": dedupes,
        "conflicts": conflict_count,
        "dropped_unmatched": dropped_unmatched,
        "coalesced_source_facts": coalesced_source_facts,
        "anchor_reconciliations": anchor_reconciliations,
        "match_counts": match_counts,
        "partial": partial,
        "failed": failed,
    }


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
