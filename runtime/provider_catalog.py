# SPDX-License-Identifier: MIT
"""The reads a provider's API offers, taken from its own published description.

A tool Metnos builds by itself reads a provider through the service
(`provider_reads`, ADR 0241). Left to its memory, the model that writes the
cases invents the shape of an answer and the one that writes the code
remembers another (measured 13/11/2026: a sound reader of birthdays refused
three times because the cases said ``{"name": ...}`` where the API answers
``names[].displayName``). So the model only CHOOSES, from a numbered list,
the reads the tool needs and the fields it uses; the address, the parameters
and the shape of the answer are copied here from the provider's discovery
documents, the same ones its client library ships.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The APIs behind the readable bases of the Google binding.
_GOOGLE_APIS = (("people", "v1"), ("gmail", "v1"), ("calendar", "v3"),
                ("drive", "v3"), ("sheets", "v4"), ("docs", "v1"))
# Path parameters with one admitted value: the account that gave the consent.
_FIXED_PATH_VALUES = {"userId": "me", "peopleId": "me"}
_SCALARS = ("string", "integer", "number", "boolean")
_FIELD_LINES = 60        # lines of an answer's shape shown for one read
_TEXT_LIMIT = 160        # characters of a description kept


@dataclass(frozen=True)
class Read:
    """One GET of a provider API."""
    id: str                               # "people.people.connections.list"
    url: str                              # address, {name} where the caller fills a value
    description: str
    path_parameters: tuple[tuple[str, str], ...]          # (name, description)
    parameters: tuple[tuple[str, str, bool, str], ...]    # (name, type, required, description)
    response: str                         # name of the answer's schema, "" when none
    api: str                              # "people.v1"


def _documents_dir() -> Path:
    import googleapiclient

    return Path(googleapiclient.__file__).parent / "discovery_cache" / "documents"


@lru_cache(maxsize=None)
def _document(api: str) -> dict:
    return json.loads((_documents_dir() / f"{api}.json").read_text(encoding="utf-8"))


# Words a description may open with that say how to use the field, not what it is.
_USAGE_MARKS = ("Required.", "Optional.", "Output only.")


def _first_sentence(text: object) -> str:
    text = " ".join(str(text or "").split())
    while text.startswith(_USAGE_MARKS):
        text = text.split(".", 1)[1].strip()
    cut = text.find(". ")
    text = text[:cut + 1] if cut > 0 else text
    return text if len(text) <= _TEXT_LIMIT else text[:_TEXT_LIMIT - 1].rstrip() + "…"


def _usage(spec: dict) -> str:
    return " ".join(str(spec.get("description") or "").split())


def _methods(resources: dict):
    for resource in (resources or {}).values():
        yield from (resource.get("methods") or {}).values()
        yield from _methods(resource.get("resources"))


def _read(api: str, document: dict, method: dict) -> Read:
    template = str(method.get("flatPath") or method.get("path") or "")
    for name, value in _FIXED_PATH_VALUES.items():
        template = template.replace("{" + name + "}", value)
    declared = method.get("parameters") or {}
    in_path = tuple(sorted(
        name for name in declared if "{" + name + "}" in template
        or ("{" + name + "Id}" in template)))
    # flatPath renames a reserved expansion ({+resourceName} -> {peopleId}):
    # whatever is still in braces is what the caller fills.
    placeholders = tuple(part.split("}")[0] for part in template.split("{")[1:])
    path_parameters = tuple(
        (name, _first_sentence((declared.get(name) or {}).get("description")))
        for name in placeholders)
    parameters = tuple(
        (name, str(spec.get("type") or "string"),
         bool(spec.get("required")) or _usage(spec).startswith("Required."),
         _first_sentence(spec.get("description")))
        for name, spec in sorted(declared.items())
        # Plain parameters only: a dotted name is a field of a nested request
        # message, an older way to say what a plain parameter already says.
        if spec.get("location") == "query" and name not in in_path and "." not in name
        and not spec.get("deprecated") and "deprecated" not in _usage(spec).lower())
    return Read(
        id=str(method["id"]),
        url=str(document["rootUrl"]) + str(document.get("servicePath") or "") + template,
        description=_first_sentence(method.get("description")),
        path_parameters=path_parameters, parameters=parameters,
        response=str((method.get("response") or {}).get("$ref") or ""), api=api)


@lru_cache(maxsize=None)
def reads(binding: str) -> tuple[Read, ...]:
    """Every read the service would accept for this provider, by identifier.

    Only GETs that answer JSON and whose address lies under a base the
    provider's own program accepts (`provider_reads.readable_bases`)."""
    import provider_reads

    if binding != "google-workspace":
        return ()
    bases = provider_reads.readable_bases(binding)
    found = []
    for name, version in _GOOGLE_APIS:
        api = f"{name}.{version}"
        try:
            document = _document(api)
        except (OSError, ValueError):
            continue
        for method in _methods(document.get("resources")):
            if method.get("httpMethod") != "GET" or not method.get("response"):
                continue
            read = _read(api, document, method)
            if provider_reads.under_a_base(read.url.split("{", 1)[0], bases):
                found.append(read)
    return tuple(sorted(found, key=lambda read: read.id))


def by_id(binding: str, identifier: str) -> Read | None:
    return next((read for read in reads(binding) if read.id == identifier), None)


# ---- the shape of an answer ----------------------------------------------------

def _schema(api: str, spec: dict) -> dict:
    """``spec`` with a reference resolved once."""
    name = spec.get("$ref") if isinstance(spec, dict) else None
    return (_document(api).get("schemas") or {}).get(name, {}) if name else (spec or {})


def _record_list(read: Read, answer: dict) -> str | None:
    """The property of a page that holds its records, None when the answer is
    one resource. A page is what a list or a search answers, or what carries
    the token of a next page; its records are the list of objects with the
    richest item (a page may also carry small lists of settings)."""
    properties = answer.get("properties") or {}
    last = read.id.rsplit(".", 1)[-1].lower()
    if not (last.startswith(("list", "search")) or "nextPageToken" in properties):
        return None
    sizes = {name: len(_schema(read.api, spec["items"]).get("properties") or {})
             for name, spec in properties.items()
             if spec.get("type") == "array" and "$ref" in (spec.get("items") or {})}
    return max(sorted(sizes), key=sizes.get) if sizes else None


def record_fields(read: Read) -> tuple[tuple[str, str], ...]:
    """The fields of the record this read answers with, each with what it is.

    The record is the item of the answer's list of records when the answer
    is a page, the answer itself otherwise."""
    answer = _schema(read.api, {"$ref": read.response})
    main = _record_list(read, answer)
    record = _schema(read.api, answer["properties"][main]["items"]) if main else answer
    return tuple((name, _first_sentence(spec.get("description")))
                 for name, spec in sorted((record.get("properties") or {}).items())
                 if not spec.get("deprecated") and "deprecated" not in _usage(spec).lower()[:40])


def _shape(api: str, spec: dict, depth: int, keep: frozenset | None, lines: list,
           indent: str, seen: tuple) -> None:
    """Append the fields under ``spec``: plain values first, then the nested
    objects from the smallest, opened while lines remain."""
    schema = _schema(api, spec)
    nested = []
    for name, field in sorted((schema.get("properties") or {}).items()):
        if keep is not None and name not in keep:
            continue
        inner = field.get("items") if field.get("type") == "array" else field
        target = _schema(api, inner or {})
        many = "[]" if field.get("type") == "array" else ""
        kind = target.get("type") or (inner or {}).get("type") or "object"
        reference = (inner or {}).get("$ref")
        if kind == "object" and target.get("properties") and depth > 0 and reference not in seen:
            nested.append((len(target["properties"]), name, many, inner, reference))
        elif len(lines) < _FIELD_LINES:
            lines.append(f"{indent}{name}{many}: {kind if kind in _SCALARS else 'object'}")
    for size, name, many, inner, reference in sorted(nested, key=lambda item: item[:2]):
        if len(lines) + 1 + min(size, 3) > _FIELD_LINES:
            lines.append(f"{indent}{name}{many}: object")
            continue
        lines.append(f"{indent}{name}{many}:")
        _shape(api, inner, depth - 1, None, lines, indent + "  ",
               seen + ((reference,) if reference else ()))


def describe(read: Read, fields: tuple[str, ...] = ()) -> str:
    """One read as the writers of cases and code need it: where it goes, what
    it takes, the shape of what it answers. ``fields``: the record's fields
    the tool uses; all of them when empty."""
    lines = [f"GET {read.url}"]
    for name, text in read.path_parameters:
        lines.append(f"  in the address, {{{name}}}: {text}")
    for name, kind, required, text in read.parameters:
        lines.append(f"  parameter {name} ({kind}{', required' if required else ''}): {text}")
    lines.append("  answer (JSON):")
    answer = _schema(read.api, {"$ref": read.response})
    properties = answer.get("properties") or {}
    main = _record_list(read, answer)
    shape: list[str] = []
    keep = frozenset(fields) if fields else None
    if main:
        # A page: its own plain fields, then the record's chosen ones under the list.
        for name, spec in sorted(properties.items()):
            if name != main and (spec.get("type") or "") in _SCALARS:
                shape.append(f"    {name}: {spec['type']}")
        shape.append(f"    {main}[]:")
        _shape(read.api, properties[main]["items"], 2, keep, shape, "      ",
               (properties[main]["items"].get("$ref"),))
    else:
        _shape(read.api, {"$ref": read.response}, 2, keep, shape, "    ", (read.response,))
    return "\n".join(lines + shape)


# ---- checks on what a writer wrote ---------------------------------------------

def _segments(url: object) -> list[str]:
    return str(url or "").split("?", 1)[0].split("#", 1)[0].rstrip("/").split("/")


def matches(read: Read, url: object) -> bool:
    """Whether ``url``, parameters aside, is an address of this read."""
    wanted, given = _segments(read.url), _segments(url)
    return len(wanted) == len(given) and all(
        (part.startswith("{") and bool(got)) or part == got
        for part, got in zip(wanted, given))


def begins(read: Read, text: object) -> bool:
    """Whether ``text`` can be the beginning of an address of this read: an
    address may be put together in the code from a fixed start."""
    fixed = read.url.split("{", 1)[0]
    start = str(text or "").split("?", 1)[0]
    return matches(read, start) or fixed.startswith(start) or start.startswith(fixed)


def _shape_problem(api: str, spec: dict, value, where: str, seen: tuple) -> str | None:
    schema = _schema(api, spec)
    kind = schema.get("type") or spec.get("type")
    if kind == "array" or spec.get("type") == "array":
        if not isinstance(value, list):
            return f"`{where}` is a list in the provider's answer"
        inner = spec.get("items") or schema.get("items") or {}
        return next((problem for item in value[:20] for problem in
                     [_shape_problem(api, inner, item, where + "[]", seen)] if problem), None)
    properties = schema.get("properties")
    if not properties:
        return None          # a scalar, or an object the description leaves open
    if not isinstance(value, dict):
        return f"`{where}` is an object in the provider's answer"
    for name, inner_value in value.items():
        if name not in properties:
            known = ", ".join(sorted(properties)[:25])
            return (f"`{where}` has no field `{name}` in the provider's answer; "
                    f"its fields are: {known}")
        reference = properties[name].get("$ref") or (properties[name].get("items") or {}).get("$ref")
        if reference in seen:
            continue
        problem = _shape_problem(api, properties[name], inner_value,
                                 f"{where}.{name}" if where else name,
                                 seen + ((reference,) if reference else ()))
        if problem:
            return problem
    return None


def shape_problem(read: Read, response) -> str | None:
    """Why ``response`` cannot be an answer of this read, or None: a field
    the provider's answer does not have, a list where it has an object."""
    return _shape_problem(read.api, {"$ref": read.response}, response, "", (read.response,))
