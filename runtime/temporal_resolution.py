"""Shared temporal arguments, a bounded local fallback and ordinary forms.

Arithmetic never belongs to a model. A short registered workload translates
unfamiliar phrases into the canonical grammar. Unresolved meanings use the
ordinary selection form, with absolute values that cannot drift while waiting.
"""
from __future__ import annotations

from datetime import datetime
import functools
import json
import re

import detection_lexicon_seed_parsers as lexicons
from time_window_parser import _normalize_llm_spec, resolve_time_bounds, temporal_now
from time_window_resolver import parse_query_time_window, temporal_mentions

_MAX_QUERY = 2048
_GRAMMAR = r'''
root ::= "{" ws "\"status\"" ws ":" ws status ws "," ws "\"expression\"" ws ":" ws expr ws "," ws "\"alternatives\"" ws ":" ws "[" ws (expr (ws "," ws expr){0,2})? ws "]" ws "}"
status ::= "\"resolved\"" | "\"ambiguous\"" | "\"unrecognized\"" | "\"none\""
expr ::= "\"" [a-zA-Z0-9@+:/._ -]{0,256} "\""
ws ::= [ \t\n]*
'''


def _time_signal(query):
    family = lexicons.load_family("time_resolver")
    if family is None:
        return False
    forms = [form for concept in ("parser.time.unit", "parser.time.relative_day",
                                  "parser.time.weekday", "parser.time.calendar_period",
                                  "parser.time.month")
             for values in family[concept].values() for form in values]
    return any(re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)", query, re.I)
               for form in forms) or bool(re.search(r"\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}", query))


def _complex_query(query):
    if re.search(r"\d{1,2}:\d{2}", query) or len(temporal_mentions(query)) > 1:
        return True
    family = lexicons.load_family("time_resolver")
    if family is None:
        return False
    numbers = [form for values in family["parser.time.number"].values() for form in values]
    units = [form for values in family["parser.time.unit"].values() for form in values]
    quantity = (r"(?<!\w)(?:\d+(?:\.\d+)?|" + "|".join(map(re.escape, numbers))
                + r")\s*(?:" + "|".join(map(re.escape, units)) + r")(?!\w)")
    return len(list(re.finditer(quantity, query, re.I))) > 1


@functools.lru_cache(maxsize=128)
def _interpret(query, field, language, reference_date, zone):
    """One local short call, cached per reference day; exceptions are not cached."""
    if not isinstance(query, str) or not query or len(query) > _MAX_QUERY:
        raise ValueError("temporal_query_out_of_bounds")
    import prompt_loader
    from llm_router import LLMRouter
    from llm_workloads import tier_for
    provider = LLMRouter().provider(tier_for("temporal.interpret"))
    if getattr(provider, "mode", "") != "local":
        raise ValueError("local_model_required")
    answer = provider.chat(
        prompt_loader.get("temporal_interpret", language),
        json.dumps({"query": query, "field": field, "reference_date": reference_date,
                    "timezone": zone}, ensure_ascii=False),
        max_tokens=240, request_timeout_s=6, grammar=_GRAMMAR)
    result = json.loads(str(getattr(answer, "text", "") or ""))
    if (not isinstance(result, dict) or set(result) != {"status", "expression", "alternatives"}
            or result["status"] not in {"resolved", "ambiguous", "unrecognized", "none"}
            or not isinstance(result["expression"], str)
            or not isinstance(result["alternatives"], list) or len(result["alternatives"]) > 3):
        raise ValueError("invalid_temporal_interpretation")
    anchor = temporal_now(datetime.fromisoformat(reference_date), tz=zone)
    grounded = set(re.findall(r"\d{4}-\d{2}-\d{2}", query))
    for mention in temporal_mentions(query):
        if re.fullmatch(r"(?:\d{4}|date)-\d{2}-\d{2}", mention):
            grounded.add(resolve_time_bounds(mention, anchor)[0].date().isoformat())
    values = [result["expression"]] if result["status"] == "resolved" else result["alternatives"]
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("invalid_temporal_expression")
        if set(re.findall(r"\d{4}-\d{2}-\d{2}", value)) - grounded:
            raise ValueError("ungrounded_absolute_date")
        if any(bound is None for bound in resolve_time_bounds(value, anchor)):
            raise ValueError("unbounded_temporal_interpretation")
    return result


def _weekday_choices(spec, now):
    match = re.fullmatch(r"(weekday-[0-6])(@\d{2}:\d{2}(?::\d{2})?)?", spec)
    if match:
        directions = ["last", "next"]
        if int(match[1][-1]) == now.weekday():
            directions.insert(0, "this")
        return [f"{match[1]}-{direction}{match[2] or ''}" for direction in directions]
    return []


def _render(expression, kind, now, *, freeze=False):
    start, end = resolve_time_bounds(expression, now)
    if start is None or end is None:
        if kind == "window":
            return "all"
        raise ValueError("temporal_field_requires_a_date")
    if kind == "date":
        if start.date() != end.date():
            raise ValueError("interval_not_date")
        return start.date().isoformat()
    if kind == "date-time":
        if start.timestamp() != end.timestamp():
            raise ValueError("date_time_requires_an_instant")
        return start.isoformat()
    if freeze:
        return start.isoformat() if start == end else f"{start.isoformat()}/{end.isoformat()}"
    return expression


def _fields(props):
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        array = spec.get("type") == "array"
        item = spec.get("items", {}) if array else spec
        kind = item.get("format") if isinstance(item, dict) else None
        if kind in {"date", "date-time", "time-window"} or name in {"time_window", "time_windows"}:
            yield name, "window" if kind == "time-window" else kind or "window", array


def resolve_temporal_args(tool, args, query, args_schema=None):
    """Normalize declared temporal scalars/vectors; infer windows on reads only."""
    props = (args_schema or {}).get("properties", {})
    if not isinstance(args, dict) or not isinstance(props, dict):
        return args
    fields = list(_fields(props))
    if not fields:
        return args
    out = {key: value for key, value in args.items() if key != "_temporal_choices"}
    query = query if isinstance(query, str) else ""
    try:
        now = temporal_now(tz=args.get("timezone") if "timezone" in props else None)
    except (ValueError, KeyError, TypeError):
        out["_temporal_choices"] = {"timezone": [{"index": None, "options": []}]}
        return out
    safe_read = tool.split("_", 1)[0] in {"read", "find", "get", "list"}
    choices = {}
    for field, kind, array in fields:
        raw = out.get(field)
        infer = safe_read and field == "time_window" and not (args.get("since") or args.get("before"))
        if infer:
            raw = parse_query_time_window(query) or raw
        if raw is None and not (infer and _time_signal(query)):
            continue
        values = raw if isinstance(raw, list) else [raw]
        resolved = list(values)
        for index, value in enumerate(values):
            source = value if isinstance(value, str) else query if value is None else ""
            canonical = _normalize_llm_spec(source)
            options = []
            try:
                resolve_time_bounds(canonical, now)
                need_model = infer and _complex_query(query)
            except (ValueError, TypeError, OverflowError):
                options = _weekday_choices(canonical, now)
                need_model = not options
            if need_model:
                try:
                    import i18n
                    decision = _interpret(query if infer else source, field,
                                          i18n.current_lang(), now.date().isoformat(), str(now.tzinfo))
                    if decision["status"] == "none" and value is None:
                        continue
                    if decision["status"] != "resolved":
                        options = decision["alternatives"]
                        raise ValueError("temporal_meaning_unresolved")
                    canonical = decision["expression"]
                except Exception:
                    choices.setdefault(field, []).append({"index": index if array else None,
                                                          "options": options})
                    continue
            try:
                if options:
                    raise ValueError("temporal_meaning_ambiguous")
                resolved[index] = _render(canonical, kind, now)
            except (ValueError, TypeError, OverflowError):
                choices.setdefault(field, []).append({"index": index if array else None,
                                                      "options": options})
        if any(value is not None for value in resolved):
            out[field] = resolved if array else resolved[0]
    if choices:
        by_name = {name: kind for name, kind, _array in fields}
        for field, pending in choices.items():
            for item in pending:
                frozen = []
                for option in item["options"]:
                    try:
                        frozen.append(_render(option, by_name[field], now, freeze=True))
                    except (ValueError, TypeError, OverflowError):
                        continue
                item["options"] = list(dict.fromkeys(frozen))
        out["_temporal_choices"] = choices
    return out


def temporal_form_request(tool, args, schema):
    """Reuse typed dialogs and their existing vector-preserving resume merge."""
    choices = args.get("_temporal_choices") if isinstance(args, dict) else None
    props = (schema or {}).get("properties", {})
    if not isinstance(choices, dict) or not choices or not isinstance(props, dict):
        return None
    from messages import get as msg
    lexicons.register_temporal_messages()
    dialog, arrays = [], {}
    temporal_fields = {name: kind for name, kind, _array in _fields(props)}
    if "timezone" in props:
        temporal_fields["timezone"] = "timezone"
    for field, pending in choices.items():
        if field not in temporal_fields or not isinstance(pending, list):
            continue
        description = props[field].get("title")
        if isinstance(description, dict):
            import i18n
            description = description.get(i18n.current_lang())
        if not isinstance(description, str) or not description.strip():
            description = msg("MSG_TEMPORAL_FIELD_" + temporal_fields[field].upper().replace("-", "_"))
        for item in pending:
            if not isinstance(item, dict):
                continue
            index, options = item.get("index"), item.get("options", [])
            is_array = props[field].get("type") == "array"
            values = args.get(field)
            if is_array:
                if (type(index) is not int or not isinstance(values, list)
                        or not 0 <= index < len(values)):
                    continue
            elif index is not None:
                continue
            if not isinstance(options, list):
                options = []
            options = [value for value in options[:3] if isinstance(value, str) and len(value) <= 512]
            variable = field if index is None else f"temporal_{field}_{index}"
            labels = [{"label": value.replace("T", " "), "value": value} for value in options]
            dialog.append({"var": variable, "prompt": msg("MSG_TEMPORAL_CHOOSE", field=description),
                           "schema": {"kind": "choice", "choices": labels} if labels else {"kind": "text"}})
            if index is not None:
                slots = arrays.setdefault(field, list(args[field]))
                slots[index] = {"var": variable}
    if not dialog:
        return None
    callback = {"type": "resume_executor_with_values", "executor": tool,
                "args_base": {key: value for key, value in args.items() if key != "_temporal_choices"}}
    if arrays:
        callback["list_args"] = arrays
    return {"decision": "needs_inputs", "needs_inputs": {
        "title": msg("MSG_TEMPORAL_TITLE"), "dialog": dialog, "fmt": "auto",
        "on_complete": callback, "timeout_s": 3600,
    }}
