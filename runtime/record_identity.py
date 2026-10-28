# SPDX-License-Identifier: MIT
"""Identity requires an owned, scoped fact or an observed view relationship.

An unscoped literal own code may join the single record whose scoped fact
carries that same code, when no field disagrees.

Observations of different sources are also linked inductively (Roberto,
19/10/2026): every shared field compatible at its own precision, enough
information-bearing agreement, and a unique mutual best match. A source
never contributes two records to one entity. The richest values are kept.

Lexical token helpers are evidence candidates only. Equal amounts, dates,
text or unqualified codes never establish identity. Preserve contradictory
observations and all witnesses when compatible records are enriched.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date, datetime, time
from functools import lru_cache
from itertools import combinations

# Numbers joined by separators are values (dates, amounts, times), not keys.
_VALUE = re.compile(r"\d+(?:[.,:/\-]\d+)+")
_TERM = re.compile(r"\d+(?:[.,:/\-]\d+)+|[^\W_]+")
_TOKEN = re.compile(r"[^\W_]+(?:[-_/][^\W_]+)*")
_EMPTY = (None, "", [])


def _public(record: dict) -> dict:
    return {key: value for key, value in record.items()
            if isinstance(key, str) and not key.startswith("_")}


def _compact_date(token: str) -> bool:
    if len(token) != 8 or not token.isdecimal():
        return False
    for year, month, day in ((token[:4], token[4:6], token[6:]),
                             (token[4:], token[2:4], token[:2])):
        try:
            date(int(year), int(month), int(day))
            return True
        except ValueError:
            pass
    return False


def _identifier_matches(value: str):
    """Mixed codes may contain separators; numeric values remain values."""
    values = list(_VALUE.finditer(value))
    for match in _TOKEN.finditer(value):
        raw = match.group()
        token = re.sub(r"[-_/]", "", raw)
        # Preserve observed group boundaries: INV-20-005 and INV-200-05
        # are distinct. A compact code is not an inferred alias either.
        code = "-".join(re.split(r"[-_/]", raw)).casefold()
        digits = sum(char.isdecimal() for char in token)
        if not digits or _compact_date(token):
            continue
        if token.isdecimal() and raw != token:
            continue
        # Do not treat a fragment of an amount or timestamp as a code.
        if any(number.start() < match.end() and number.end() > match.start()
               and (number.start() < match.start() or number.end() > match.end())
               for number in values):
            continue
        if digits < len(token):
            # A time alone is written with its colons: a letter and digits
            # ("T2030000009") is a code, not a compact ISO time (21/10/2026).
            for parser in (datetime.fromisoformat, time.fromisoformat if ":" in raw else None):
                try:
                    if parser is not None:
                        parser(raw)
                        break
                except ValueError:
                    pass
            else:
                if len(token) >= 5:
                    yield match, code
        elif digits >= 6:
            yield match, code


def _value_evidence(value) -> tuple[set[str], set[str]]:
    if not isinstance(value, str):
        return {repr(value)}, set()
    matches = list(_identifier_matches(value))
    # Different formatting of the same code must also agree as field evidence.
    for match, code in reversed(matches):
        value = value[:match.start()] + code + value[match.end():]
    return ({term.casefold() for term in _TERM.findall(value)},
            {code for _match, code in matches})


def _terms(value) -> set[str]:
    return _value_evidence(value)[0]


def _evidence(record: dict) -> dict:
    """Parse each public field once per merge invocation; no retained state."""
    return {field: (*_value_evidence(value), value not in _EMPTY)
            for field, value in _public(record).items()}


def _conflicting_evidence(first: dict, second: dict) -> bool:
    for field in first.keys() & second.keys():
        left, right = first[field][1], second[field][1]
        if left and right and not (left <= right or right <= left):
            return True
    return False


def _agreement_evidence(first: dict, second: dict) -> tuple[int, int]:
    """Common nonempty fields where one value's terms contain the other's,
    and those where neither does."""
    agree = contradict = 0
    for field in first.keys() & second.keys():
        if not first[field][2] or not second[field][2]:
            continue
        ta, tb = first[field][0], second[field][0]
        if ta <= tb or tb <= ta:
            agree += 1
        else:
            contradict += 1
    return agree, contradict


def _suspected_evidence(first: dict, second: dict,
                        first_ids: set[str], second_ids: set[str]) -> bool:
    equal = sum(1 for field in first.keys() & second.keys()
                if first[field][2] and first[field][0] == second[field][0])
    return (equal >= 2 and _agreement_evidence(first, second)[1] == 0
            and not first_ids & second_ids)


def valid_reference(value) -> bool:
    """Opaque internal references, never redaction placeholders or prose."""
    return (isinstance(value, str) and 0 < len(value) <= 256
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is not None)


def _valid_scope(scope) -> bool:
    return (isinstance(scope, dict)
            and set(scope) == {"version", "entity", "namespace"}
            and type(scope["version"]) is int and scope["version"] == 1
            and valid_reference(scope["entity"])
            and valid_reference(scope["namespace"]))


def _literal_quote_span(quote: str, source: str):
    """Allow whitespace layout only; retain the exact contiguous source span."""
    if quote in source:
        return quote
    parts = quote.split()
    if not parts:
        return None
    match = re.search(r"\s+".join(map(re.escape, parts)), source)
    return match.group(0) if match else None


def admit_identity_facts(record: dict, facts, *, source_text: str,
                         source_ref: str, scope=None) -> dict:
    """Admit literal annotations; ownership remains a semantic claim.

    Scope is supplied by the qualified producer, never inferred from fields
    or accepted from the model. Invalid annotations leave public data intact.
    """
    result = deepcopy(record)
    for key in ("_identity_facts", "_record_facts", "_record_scope",
                "_record_source", "_record_provenance", "_record_conflicts"):
        result.pop(key, None)
    if not valid_reference(source_ref):
        return result
    result["_record_source"] = source_ref
    valid_scope = deepcopy(scope) if _valid_scope(scope) else None
    if valid_scope:
        result["_record_scope"] = valid_scope
    if not isinstance(source_text, str) or not isinstance(facts, list) or len(facts) > 64:
        return result
    admitted = []
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) != {"field", "value", "role", "owner", "quote"}:
            continue
        field, value, quote = fact["field"], fact["value"], fact["quote"]
        if (not isinstance(field, str) or field.startswith("_")
                or not isinstance(result.get(field), str)
                or not isinstance(value, str) or not value.strip() or len(value) > 512
                or not isinstance(quote, str) or len(quote) > 2048
                or value not in result[field] or value not in quote
                or (fact["role"] == "identifier" and value != result[field])
                or fact["role"] not in ("identifier", "attribute")
                or fact["owner"] not in ("self", "context", "reference", "unknown")):
            continue
        span = _literal_quote_span(quote, source_text)
        if span is None or value not in span:
            continue
        witness = {**deepcopy(fact), "quote": span, "source": source_ref}
        if valid_scope:
            witness["scope"] = deepcopy(valid_scope)
        if witness not in admitted:
            admitted.append(witness)
    if admitted:
        result["_record_facts"] = admitted
    return result


def _code_shape(value: str) -> str:
    """Letters, digits and separators of a code, not its characters."""
    return re.sub(r"[^\W\d_]", "a", re.sub(r"\d", "9", value))


def complete_identifier_facts(records: list[dict], source_text: str) -> list[dict]:
    """A field one source labels as its records' own identifier labels it in
    every record of that source.

    The model annotates each record; on a long list it may leave later rows
    without the annotation (real runs Fastweb3/4, 21/10/2026: the same list,
    numbers annotated for four of nine rows once and for all nine the next
    time, and the rows left bare were then rejected). A record of the same
    source without any identifier fact of its own receives the fact a sibling
    carries for that field when its value is a code of the same shape,
    written in the source after the same words as the sibling's quote, and
    not another record's value. Annotated records are never changed.
    """
    references = []
    for record in records:
        for fact in record.get("_record_facts") or ():
            if (isinstance(fact, dict) and fact.get("role") == "identifier"
                    and fact.get("owner") == "self" and isinstance(fact.get("value"), str)
                    and isinstance(fact.get("quote"), str) and fact["value"] in fact["quote"]
                    and fact.get("value") == record.get(fact.get("field"))):
                references.append(fact)
    if not references or not isinstance(source_text, str):
        return records
    taken = {(fact["field"], fact["value"]) for fact in references}
    completed = []
    for record in records:
        facts = [f for f in record.get("_record_facts") or () if isinstance(f, dict)]
        if any(f.get("role") == "identifier" for f in facts):
            completed.append(record)
            continue
        for reference in references:
            field, value = reference["field"], record.get(reference["field"])
            # The model's own word on this field, whatever it is, stands.
            before = reference["quote"][:reference["quote"].index(reference["value"])]
            # The words before the value are the evidence; without them only
            # a code of the very same shape qualifies (INV-999 → INV-1000,
            # "2026/000123", five digits: universality review 21/10/2026).
            if (any(f.get("field") == field for f in facts)
                    or not isinstance(value, str) or not value.strip()
                    or (field, value) in taken or not any(ch.isdecimal() for ch in value)
                    or (not before.strip() and (
                        _code_shape(value) != _code_shape(reference["value"])
                        or not any(match.group() == value
                                   for match, _code in _identifier_matches(value))))):
                continue
            quote = _literal_quote_span(before + value, source_text)
            if quote is None:
                continue
            record = {**deepcopy(record), "_record_facts": [*deepcopy(facts), {
                **deepcopy(reference), "value": value, "quote": quote}]}
            taken.add((field, value))
            break
        completed.append(record)
    return completed


def complete_identifier_facts_across(records: list[dict], texts: dict) -> list[dict]:
    """A field the extraction labels as records' own identifier labels it in
    records of other sources too (real run fw9, Fastweb 2026, 22/10/2026: the
    numbers of four rows annotated, six rows of another page left bare and then
    rejected as unidentified). A record without any identifier fact receives
    one, without producer scope, when its value in that field is a code of the
    very same shape as an annotated value, no other record holds it, and its
    own source shows it literally. Annotated records are never changed."""
    shapes = set()
    taken = set()
    for record in records:
        for fact in record.get("_record_facts") or ():
            if (isinstance(fact, dict) and fact.get("role") == "identifier"
                    and fact.get("owner") == "self" and isinstance(fact.get("field"), str)
                    and isinstance(fact.get("value"), str)
                    and fact["value"] == record.get(fact["field"])
                    and any(match.group() == fact["value"]
                            for match, _code in _identifier_matches(fact["value"]))):
                shapes.add((fact["field"], _code_shape(fact["value"])))
                taken.add((fact["field"], fact["value"]))
    if not shapes:
        return records
    completed = []
    for record in records:
        facts = [f for f in record.get("_record_facts") or () if isinstance(f, dict)]
        source = record.get("_record_source")
        text = texts.get(source) if valid_reference(source) else None
        if any(f.get("role") == "identifier" for f in facts) or not isinstance(text, str):
            completed.append(record)
            continue
        for field, shape in sorted(shapes):
            value = record.get(field)
            if (not isinstance(value, str) or not value.strip() or (field, value) in taken
                    or any(f.get("field") == field for f in facts)
                    or _code_shape(value) != shape
                    or not any(match.group() == value for match, _code in _identifier_matches(value))
                    or _literal_quote_span(value, text) is None):
                continue
            record = {**deepcopy(record), "_record_facts": [*deepcopy(facts), {
                "field": field, "value": value, "role": "identifier", "owner": "self",
                "quote": _literal_quote_span(value, text), "source": source}]}
            taken.add((field, value))
            break
        completed.append(record)
    return completed


def _qualified_identifier_facts(record: dict):
    """Owned identifiers supported by a source and producer-qualified scope."""
    facts = record.get("_record_facts")
    if not isinstance(facts, list):
        return
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        scope, field, value = fact.get("scope"), fact.get("field"), fact.get("value")
        if (fact.get("owner") == "self" and fact.get("role") == "identifier"
                and _valid_scope(scope) and valid_reference(fact.get("source"))
                and isinstance(field, str) and not field.startswith("_")
                and isinstance(record.get(field), str)
                and isinstance(value, str) and value and value == record[field]):
            yield fact


def qualified_identifiers(record: dict) -> set[tuple[str, str, str]]:
    """Exact identifier values and their producer-qualified namespaces."""
    return {(fact["scope"]["entity"], fact["scope"]["namespace"], fact["value"])
            for fact in _qualified_identifier_facts(record)}


def conflicting_self_identifier_fields(records: list[dict]) -> set[str]:
    """Different owned IDs in the same field/scope contradict a view link.

    Other fields, namespaces and contextual identifiers are not aliases or
    evidence of disagreement about the entity's own identifier.
    """
    values = {}
    for record in records:
        for fact in _qualified_identifier_facts(record):
            scope = fact["scope"]
            key = (scope["entity"], scope["namespace"], fact["field"])
            values.setdefault(key, set()).add(fact["value"])
    return {field for (_entity, _namespace, field), identifiers in values.items()
            if len(identifiers) > 1}


def conflicting_fields(first: dict, second: dict) -> set[str]:
    """Fields whose two values say different things, by the one comparison
    of every merge (`_comparison`): a month and a day of it, or one amount
    written two ways, are the same value (real run a7, ENI 22/10/2026: twelve
    rows declared invalid for a month beside its day). Every observation still
    stays in the record's provenance."""
    left, right = _public(first), _public(second)
    return {field for field in left.keys() & right.keys()
            if _comparison(left[field], right[field])[0] == "conflict"}


def retain_conflicts(record: dict, fields: set[str]) -> dict:
    result = deepcopy(record)
    result["_record_conflicts"] = sorted(set(result.get("_record_conflicts", [])) | fields)
    return result


def enrich_observations(records: list[dict], *, preferred: int = 0) -> dict:
    """Caller proves the relationship and compatibility; retain each witness.

    A field whose values all agree takes the most informative one (the day
    rather than its month); a disagreeing field keeps the preferred value.
    """
    result = deepcopy(records[preferred])
    facts, provenance = [], []
    for record in records:
        for field, value in _public(record).items():
            if field not in result or result[field] in _EMPTY:
                result[field] = deepcopy(value)
        # Flatten previous enrichments without recursively duplicating ledgers.
        witnesses = record.get("_record_provenance")
        if not isinstance(witnesses, list) or not witnesses:
            witnesses = [{key: deepcopy(value) for key, value in record.items()
                          if key != "_record_provenance"}]
        for witness in witnesses:
            if witness not in provenance:
                provenance.append(deepcopy(witness))
        for fact in record.get("_record_facts", []):
            if fact not in facts:
                facts.append(deepcopy(fact))
    for field in _public(result):
        values = [_public(r).get(field) for r in records if _public(r).get(field) not in _EMPTY]
        if len(set(map(str, values))) > 1 and not any(
                _comparison(a, b)[0] == "conflict" for a, b in combinations(values, 2)):
            result[field] = deepcopy(_most_informative(values))
    result["_record_provenance"] = provenance
    if facts:
        result["_record_facts"] = facts
    return result


def _unscoped_identifier_claims(record: dict) -> set[tuple[str, str]]:
    """Literal self identifiers admitted without a producer scope.

    Only lexical codes qualify: dates, amounts and other separated values
    stay values. Alone such a claim never identifies anything.
    """
    claims = set()
    for fact in record.get("_record_facts") or []:
        if not isinstance(fact, dict) or _valid_scope(fact.get("scope")):
            continue
        field, value = fact.get("field"), fact.get("value")
        if (fact.get("owner") == "self" and fact.get("role") == "identifier"
                and valid_reference(fact.get("source"))
                and isinstance(field, str) and not field.startswith("_")
                and isinstance(value, str) and value and record.get(field) == value
                and any(match.group() == value for match, _code in _identifier_matches(value))):
            claims.add((field, value))
    return claims


def _join_unscoped_claims(records: list[dict], kept: list[int], sources: list,
                          replacements: dict, removed: set, suspected: set) -> None:
    """An unscoped literal self identifier joins the unique scoped holder.

    The scoped record proves the value is an entity's own identifier in this
    collection; the other observation quotes that same value as its own.
    Several holders, another source missing, the same source or any unequal
    field leave both records intact (conflicts retained and suspected).
    """
    holders = {}
    for index in kept:
        record = replacements.get(index, records[index])
        for fact in _qualified_identifier_facts(record):
            holders.setdefault((fact["field"], fact["value"]), set()).add(index)
    for index in kept:
        if index in removed:
            continue
        record = replacements.get(index, records[index])
        if qualified_identifiers(record):
            continue
        targets = set().union(*(holders.get(claim, set())
                                for claim in _unscoped_identifier_claims(record)))
        if len(targets) != 1:
            continue
        target, = targets
        if (target in removed or sources[index] is None or sources[target] is None
                or sources[index] == sources[target]):
            continue
        holder = replacements.get(target, records[target])
        if _partitions_differ(holder, record):
            suspected.update((index, target))
            continue
        conflicts = conflicting_fields(holder, record)
        if conflicts:
            replacements[index] = retain_conflicts(record, conflicts)
            replacements[target] = retain_conflicts(holder, conflicts)
            suspected.update((index, target))
            continue
        replacements[target] = enrich_observations([holder, record], preferred=0)
        removed.add(index)


_NUMBER = re.compile(r"[-+]?(?:\d{1,3}(?:[.\s\u00a0]\d{3})+|\d+)(?:[.,]\d+)?")


def _number(value: str):
    """The single number a value carries, separators normalised; else None.

    A short unit or currency may surround it; prose carrying a number is not
    a number.
    """
    if len(re.sub(r"[\W\d_]+", "", value)) > 3:
        return None
    found = _NUMBER.findall(value)
    if len(found) != 1:
        return None
    text = re.sub(r"[\s\u00a0]", "", found[0])
    if "," in text and "." in text:
        text = text.replace("." if text.rfind(",") > text.rfind(".") else ",", "")
    if text.count(",") == 1 and len(text.rsplit(",", 1)[1]) != 3:
        text = text.replace(",", ".")
    elif text.count(".") == 1 and len(text.rsplit(".", 1)[1]) == 3 and len(text.split(".")[0]) <= 3:
        text = text.replace(".", "")
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


@lru_cache(maxsize=4096)
def _period(value: str):
    try:
        from time_window_parser import observed_date_period
        return observed_date_period(value)
    except Exception:  # noqa: BLE001 -- an unreadable date is not evidence
        return None


def _comparison(left, right) -> tuple[str, float]:
    """('agree'|'conflict'|'neutral', weight) of two observed field values.

    Values agree when equal, when one date period contains the other, when
    they carry the same number, or when one value's terms contain the other's.
    Information-bearing values (with digits) weigh more than labels.
    """
    if left in _EMPTY or right in _EMPTY:
        return "neutral", 0.0
    a, b = " ".join(str(left).split()).casefold(), " ".join(str(right).split()).casefold()
    # Information grows with the digits a value carries: a lone "0" says
    # little, a date or an amount says much; labels weigh half.
    digits = min(sum(c.isdecimal() for c in a), sum(c.isdecimal() for c in b))
    weight = min(1.0, digits / 2) if digits else 0.5
    if a == b:
        return "agree", weight
    first, second = _period(a), _period(b)
    if first and second:
        inside = ((first[0] <= second[0] and second[1] <= first[1])
                  or (second[0] <= first[0] and first[1] <= second[1]))
        return ("agree", weight) if inside else ("conflict", 0.0)
    if bool(first) != bool(second) and re.search(r"\d", a) and re.search(r"\d", b):
        return "conflict", 0.0
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return ("agree", weight) if x == y else ("conflict", 0.0)
    ta, tb = _terms(a), _terms(b)
    if ta and tb and (ta <= tb or tb <= ta):
        return "agree", weight
    return "conflict", 0.0


def _contains(first, second) -> bool:
    return bool(first and second) and (
        (first[0] <= second[0] and second[1] <= first[1])
        or (second[0] <= first[0] and first[1] <= second[1]))


def _evidence_fields(record: dict) -> dict:
    """Public values that count as evidence of this record's identity.

    A date claimed as the record's identifier that repeats its own date is a
    misplaced date, not an identifier: it says nothing more about identity.
    """
    public = _public(record)
    claimed = {fact.get("field") for fact in record.get("_record_facts") or []
               if isinstance(fact, dict) and fact.get("role") == "identifier"}
    misplaced = set()
    for field in claimed:
        value = public.get(field)
        period = _period(str(value)) if value not in _EMPTY else None
        if period and any(other != field and value_other not in _EMPTY
                          and _contains(period, _period(str(value_other)))
                          for other, value_other in public.items()):
            misplaced.add(field)
    return {key: value for key, value in public.items() if key not in misplaced}


def _is_mention(record: dict) -> bool:
    """Its identifier claim is a misplaced value: a mention, not a record."""
    return set(_evidence_fields(record)) != set(_public(record))


def _partitions_differ(first: dict, second: dict) -> bool:
    """Read in two different record partitions (contracts, accounts...)."""
    a, b = first.get("_record_partition"), second.get("_record_partition")
    return bool(a and b and a != b)


def _link_score(first: dict, second: dict) -> float | None:
    """Agreement weight of two records, or None when any field conflicts."""
    if _partitions_differ(first, second):
        return None
    a, b = _evidence_fields(first), _evidence_fields(second)
    # A misplaced identifier claim is no evidence, and no counter-evidence.
    ids = [set() if _is_mention(r) else qualified_identifiers(r) for r in (first, second)]
    if ids[0] and ids[1] and not ids[0] & ids[1]:
        return None
    score = 0.0
    for field in a.keys() & b.keys():
        verdict, weight = _comparison(a[field], b[field])
        if verdict == "conflict":
            return None
        score += weight
    return score


def _richness(record: dict) -> tuple:
    values = _evidence_fields(record).values()
    return (sum(v not in _EMPTY for v in values), sum(len(str(v)) for v in values if v not in _EMPTY))


def _most_informative(values: list):
    """Among compatible values: the narrowest date, else the fullest text."""
    periods = [_period(str(v)) for v in values]
    if all(periods):
        return min(zip(periods, values), key=lambda item: item[0][1] - item[0][0])[1]
    return max(values, key=lambda v: len(str(v)))


def _joined_values(values: list) -> str:
    """Every different observed value once: compatible values collapse to the
    most informative one (a month and its day are one value)."""
    kept = []
    for value in values:
        if value in _EMPTY:
            continue
        for position, other in enumerate(kept):
            if _comparison(other, value)[0] != "conflict":
                kept[position] = _most_informative([other, value])
                break
        else:
            kept.append(value)
    return " / ".join(sorted(dict.fromkeys(str(value) for value in kept)))


def _merge_group(observations: list[dict]) -> dict:
    """The richest observation completed by the others, every witness kept.

    Each field keeps its most informative compatible value; values that are
    not evidence (a date claimed as identifier) are never chosen.
    """
    richest = max(range(len(observations)), key=lambda n: _richness(observations[n]))
    merged = enrich_observations(observations, preferred=richest)
    evidence = [_evidence_fields(o) for o in observations]
    for field in {f for o in observations for f in _public(o)}:
        values = [v.get(field) for v in evidence if v.get(field) not in _EMPTY]
        if values:
            merged[field] = _most_informative(values)
        elif field not in evidence[richest]:
            merged[field] = ""
    # Contradictions an earlier phase declared stay declared.
    conflicts = sorted({f for o in observations for f in o.get("_record_conflicts") or ()})
    if conflicts:
        merged["_record_conflicts"] = conflicts
    else:
        merged.pop("_record_conflicts", None)
    return merged


def _contained(inner: dict, outer: dict) -> bool:
    """Every evidence value of the inner record agrees with the outer one,
    with at least the weight of a date and a label."""
    mine, theirs = _evidence_fields(inner), _evidence_fields(outer)
    values = {k: v for k, v in mine.items() if v not in _EMPTY}
    verdicts = [_comparison(v, theirs.get(k)) for k, v in values.items()]
    return (bool(values) and all(verdict == "agree" for verdict, _w in verdicts)
            and sum(weight for _v, weight in verdicts) >= 1.5)


def _link_compatible(records: list[dict], kept: list[int], sources: list,
                     replacements: dict, removed: set, *, contain: bool = True) -> set[int]:
    """Link observations by mutual unique best compatibility across sources.

    Then a poorer observation contained in exactly one richer entity of
    other sources joins it; with several candidates it stays apart. That
    uniqueness holds only over complete observations (``contain``).
    """
    current = {i: replacements.get(i, records[i]) for i in kept if i not in removed}
    best = {}
    for i, j in combinations(current, 2):
        if sources[i] is None or sources[j] is None or sources[i] == sources[j]:
            continue
        score = _link_score(current[i], current[j])
        if score is None or score < 2.0:
            continue
        for x, y in ((i, j), (j, i)):
            best.setdefault((x, sources[y]), []).append((score, y))

    def choice(found):
        # The best partner must be unique; indistinguishable candidates (equal
        # public values) are one choice, and the first of them is taken.
        top = max(score for score, _j in found)
        tied = sorted(j for score, j in found if score == top)
        if any(_public(current[j]) != _public(current[tied[0]]) for j in tied):
            return None
        return top, tied[0]

    chosen = {key: choice(found) for key, found in best.items()}
    pairs = []
    for (i, _source), picked in chosen.items():
        if picked is None:
            continue
        score, j = picked
        if i < j and chosen.get((j, sources[i])) == (score, i):
            pairs.append((score, i, j))
    group = {i: {i} for i in current}
    owner = {i: i for i in current}

    def unite(gi, gj, *, mention=False) -> bool:
        # A source never gives two records to one entity; a mention of an
        # entity is not a second record of it.
        if gi == gj or (not mention and {sources[k] for k in group[gi]}
                        & {sources[k] for k in group[gj]}):
            return False
        if any(_link_score(current[x], current[y]) is None for x in group[gi] for y in group[gj]):
            return False
        group[gi] |= group.pop(gj)
        for k in group[gi]:
            owner[k] = gi
        return True

    for _score, i, j in sorted(pairs, reverse=True):
        unite(owner[i], owner[j])
    view = {g: _merge_group([current[k] for k in sorted(members)]) for g, members in group.items()}
    for g in sorted(view, key=lambda g: _richness(view[g])) if contain else ():
        if g not in group:
            continue
        mention = len(group[g]) == 1 and _is_mention(current[next(iter(group[g]))])
        hosts = [h for h in group if h != g and _contained(view[g], view[h])
                 and (mention or not {sources[k] for k in group[g]} & {sources[k] for k in group[h]})]
        if (len(hosts) == 1 and _richness(view[hosts[0]]) > _richness(view[g])
                and unite(hosts[0], g, mention=mention)):
            view[hosts[0]] = _merge_group([current[k] for k in sorted(group[hosts[0]])])
    linked = set()
    for members in group.values():
        if len(members) < 2:
            continue
        ordered = sorted(members)
        replacements[ordered[0]] = _merge_group([current[k] for k in ordered])
        removed.update(ordered[1:])
        linked.update(ordered)
    return linked


# Roberto, 20/10/2026: «se lo stesso codice compare su 3 o più fatture
# diverse fra loro, non è il numero fattura; quelle righe restano normali.
# Con 2 righe diverse resta "dati non validi"».
_SHARED_CODE_RECORDS = 3


def _conflicting_fields(first: dict, second: dict) -> int:
    a, b = _public(first), _public(second)
    return sum(_comparison(a[field], b[field])[0] == "conflict" for field in a.keys() & b.keys())


def _different_records(first: dict, second: dict) -> bool:
    """Two rows of one source that disagree are two records; readings from
    different sources are, only when they disagree on two fields or more:
    the same invoice read as issue date on one page and due date on another
    is still one invoice (universality review 21/10/2026)."""
    conflicts = _conflicting_fields(first, second)
    same_source = (first.get("_record_source") is not None
                   and first.get("_record_source") == second.get("_record_source"))
    return conflicts >= (1 if same_source else 2)


def _distinct_records(records: list[dict]) -> int:
    """How many different records these are: one reading of each counts."""
    representatives = []
    for record in records:
        if all(_different_records(record, other) for other in representatives):
            representatives.append(record)
    return len(representatives)


def _release_shared_codes(records: list[dict]) -> list[dict]:
    """A value claimed as the own identifier of three or more records that
    differ from each other is a code they share (a customer or supply
    number), not theirs: those claims become context and the rows stay
    ordinary rows, neither merged nor declared invalid by that value."""
    holders = {}
    for index, record in enumerate(records):
        if _is_mention(record):
            continue
        claims = ({(fact["field"], fact["value"]) for fact in _qualified_identifier_facts(record)}
                  | _unscoped_identifier_claims(record))
        for claim in claims:
            holders.setdefault(claim, []).append(index)
    shared = {claim for claim, members in holders.items()
              if len(members) >= _SHARED_CODE_RECORDS
              and _distinct_records([records[i] for i in members]) >= _SHARED_CODE_RECORDS}
    released = list(records)
    for claim in shared:
        for index in holders[claim]:
            record = released[index] if released[index] is not records[index] else deepcopy(records[index])
            record["_record_facts"] = [
                {**fact, "owner": "context"}
                if (isinstance(fact, dict) and fact.get("owner") == "self"
                    and fact.get("role") == "identifier"
                    and (fact.get("field"), fact.get("value")) == claim) else fact
                for fact in record.get("_record_facts") or []]
            # An ordinary row, never one "without identifier" to reject.
            record["_record_shared_code"] = True
            released[index] = record
    return released


def merge_observed_identifiers(records: list[dict], *, source_key: str = "_source_url",
                               complete: bool = True) -> tuple[list[dict], int, int]:
    """Merge unique scoped self-identifiers and compatible observations;
    incompatible witnesses stay in the provenance, their values declared."""
    records = _release_shared_codes(records)
    sources = [record.get(source_key) if isinstance(record.get(source_key), (str, int))
               and not isinstance(record.get(source_key), bool)
               and record.get(source_key) != "" else None for record in records]
    # A real self-identifier decides identity on its own, within one source
    # too; the other fields only confirm it. Where they contradict it, the
    # identifier or the data are wrong: one row shows every value, declared.
    identifiers = [_own_identifiers(record) for record in records]
    holders = {}
    for index, keys in enumerate(identifiers):
        # The same value in the same identifier field is the same identity
        # across producer scopes too: two detail views of one invoice, or a
        # list row quoting it (real run Enel 20/10: 8 rows for 4 invoices).
        # "Se esiste un id univoco, non possono esistere righe con lo stesso
        # id" (Roberto, 20/10); conflicting values are all shown (21/10).
        # Only a code joins across scopes: small numbers ("3", "0001") are
        # counted per account or list and repeat (universality review 21/10).
        fields = {("", fact["field"], fact["value"])
                  for fact in _qualified_identifier_facts(records[index])
                  if any(match.group() == fact["value"]
                         for match, _code in _identifier_matches(fact["value"]))} if keys else set()
        for key in keys | fields:
            holders.setdefault(key, []).append(index)
    parent = list(range(len(records)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for members in holders.values():
        for other in members[1:]:
            parent[root(other)] = root(members[0])
    groups = {}
    for index in range(len(records)):
        groups.setdefault(root(index), []).append(index)
    replacements, removed, suspected = {}, set(), set()
    for members in groups.values():
        if len(members) < 2:
            continue
        # The scoped holder of the identifier is the base of the merged row.
        members = sorted(members, key=lambda i: (not qualified_identifiers(records[i]), i))
        conflicts = {field for i, j in combinations(members, 2)
                     for field in _public(records[i]).keys() & _public(records[j]).keys()
                     if _comparison(_public(records[i])[field],
                                    _public(records[j])[field])[0] == "conflict"}
        # The same identifier in two partitions: the identifier or the data
        # are invalid; its own fields are declared.
        conflicts |= {fact.get("field") for i, j in combinations(members, 2)
                      if _partitions_differ(records[i], records[j])
                      for fact in records[i].get("_record_facts") or ()
                      if isinstance(fact, dict) and fact.get("role") == "identifier"
                      and fact.get("field") in _public(records[i])}
        if conflicts and any(_partitions_differ(records[i], records[j])
                             for i, j in combinations(members, 2)):
            # Two contracts cannot share an invoice: the identifier itself is
            # in doubt, each row stays with its partition, declared.
            for i in members:
                replacements[i] = retain_conflicts(records[i], conflicts)
            suspected.update(members)
            continue
        merged = _merge_group([records[i] for i in members])
        if conflicts:
            # One identifier, one row (Roberto, 20/10/2026): every different
            # value observed for a conflicting field is shown, compatible
            # ones once, and the row is declared invalid data.
            for field in conflicts:
                merged[field] = _joined_values([_public(records[i]).get(field) for i in members])
            merged["_record_conflicts"] = sorted(set(merged.get("_record_conflicts") or ()) | conflicts)
        replacements[members[0]] = merged
        removed.update(members[1:])
    kept = [i for i in range(len(records)) if i not in removed]
    _join_unscoped_claims(records, kept, sources, replacements, removed, suspected)
    kept = [i for i in range(len(records)) if i not in removed]
    suspected -= _link_compatible(records, kept, sources, replacements, removed,
                                  contain=complete)
    kept = [i for i in range(len(records)) if i not in removed]
    evidence = [_evidence(record) for record in records]
    for i, j in combinations(kept, 2):
        if (sources[i] is not None and sources[j] is not None and sources[i] != sources[j]
                and (identifiers[i] & identifiers[j] or _suspected_evidence(
                    evidence[i], evidence[j], identifiers[i], identifiers[j]))):
            suspected.update((i, j))
    return ([deepcopy(replacements.get(i, records[i])) for i in kept],
            len(removed), len(suspected - removed))


def claims_identity(record: dict) -> bool:
    return _claims_identity(record)


def unidentified_absorbed(records: list[dict]) -> int:
    """Distinct rows without an identifier merged into an identified record."""
    absorbed = set()
    for record in records:
        if not _claims_identity(record):
            continue
        for witness in record.get("_record_provenance") or ():
            if isinstance(witness, dict) and not _claims_identity(witness):
                absorbed.add(json.dumps(_public(witness), sort_keys=True, default=str))
    return len(absorbed)


def _own_identifiers(record: dict) -> set[tuple[str, str, str]]:
    """Identifiers the source labels as the record's own: producer-scoped,
    or a literal code without scope (keyed by its field). A misplaced value
    (a mention) has none."""
    if _is_mention(record):
        return set()
    return qualified_identifiers(record) | {
        ("", field, value) for field, value in _unscoped_identifier_claims(record)}


def _claims_identity(record: dict) -> bool:
    """It names itself by an identifier that is evidence, not a misplaced value."""
    return bool(_own_identifiers(record))

