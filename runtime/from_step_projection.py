# SPDX-License-Identifier: AGPL-3.0-only
"""Schema-driven projection of a producer's entries into consumer arguments.

``from_step`` carries two kinds of data across a pipeline:

* the vector payload (for example ``entries[*].uid`` -> ``message_ids``);
* homogeneous scalar context required to interpret that payload (for example
  the mailbox ``account`` and source ``folder`` shared by those UIDs).

Both mappings are declared by the consumer manifest through
``from_entries_key``.  Array properties receive the projected vector; scalar
properties receive a value only when every source entry declares the same
primitive value.  Heterogeneous or incomplete context is never guessed.

The module is deliberately independent from both runtime engines so the
legacy and v3 execution paths share exactly the same projection semantics.
"""
from __future__ import annotations

from typing import Any


_SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _properties(schema: dict | None) -> dict:
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties") or {}
    return properties if isinstance(properties, dict) else {}


def _is_array_property(spec: Any) -> bool:
    if not isinstance(spec, dict):
        return False
    declared = spec.get("type")
    if isinstance(declared, list):
        return "array" in declared
    return declared == "array"


def _is_scalar_property(spec: Any) -> bool:
    if not isinstance(spec, dict):
        return False
    declared = spec.get("type")
    if isinstance(declared, list):
        values = {value for value in declared if isinstance(value, str)}
        return bool(values) and values <= (_SCALAR_TYPES | {"null"})
    return declared in _SCALAR_TYPES


def consumer_match_arg(consumer_schema: dict | None,
                       entries: list) -> str | None:
    """Return the natural array argument for a list payload.

    Explicit ``from_entries_key`` declarations outrank the conventional
    plural-to-singular match. Required properties outrank optional ones, with
    a stable alphabetical tie-break.
    """
    if not isinstance(entries, list):
        return None
    properties = _properties(consumer_schema)
    if not properties:
        return None
    required = consumer_schema.get("required") or [] \
        if isinstance(consumer_schema, dict) else []
    if not isinstance(required, list):
        required = []

    if not entries:
        for arg_name in required:
            if arg_name in {"entries", "from_step"}:
                continue
            spec = properties.get(arg_name)
            if isinstance(spec, dict) and spec.get("type") \
                    and not _is_array_property(spec):
                continue
            return arg_name
        return None

    first = entries[0]
    if not isinstance(first, dict):
        return None
    sample_keys = set(first)
    candidates: list[tuple[int, str]] = []
    for arg_name, spec in properties.items():
        if arg_name in {"entries", "from_step"}:
            continue
        if isinstance(spec, dict) and spec.get("type") \
                and not _is_array_property(spec):
            continue
        source_key = spec.get("from_entries_key") \
            if isinstance(spec, dict) else None
        required_rank = 0 if arg_name in required else 1
        if isinstance(source_key, str) and source_key in sample_keys:
            candidates.append((required_rank - 1, arg_name))
            continue
        singular = (arg_name[:-1]
                    if arg_name.endswith("s") and len(arg_name) > 1
                    else arg_name)
        if singular in sample_keys:
            candidates.append((required_rank, arg_name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _uniform_scalar(entries: list, source_key: str):
    """Return ``(True, value)`` only for complete homogeneous context."""
    if not entries:
        return False, None
    values = []
    for entry in entries:
        if not isinstance(entry, dict) or source_key not in entry:
            return False, None
        value = entry.get(source_key)
        if value is None or not isinstance(value, (str, int, float, bool)):
            return False, None
        values.append(value)
    first = values[0]
    if not all(value == first and type(value) is type(first)
               for value in values[1:]):
        return False, None
    return True, first


def project_from_entries(args: dict, entries: list,
                         consumer_schema: dict | None) -> tuple[dict, str | None]:
    """Project payload and declared homogeneous context into consumer args.

    Returns ``(projected_args, vector_arg)``. ``vector_arg`` is ``None`` when
    the historical ``entries`` fallback was used.
    """
    out = dict(args)
    properties = _properties(consumer_schema)
    vector_arg = consumer_match_arg(consumer_schema, entries)
    used_vector_arg = None
    if vector_arg and vector_arg not in out:
        spec = properties.get(vector_arg) or {}
        source_key = spec.get("from_entries_key") \
            if isinstance(spec, dict) else None
        if not isinstance(source_key, str) or not source_key:
            source_key = (vector_arg[:-1]
                          if vector_arg.endswith("s") and len(vector_arg) > 1
                          else vector_arg)
        out[vector_arg] = [
            entry[source_key] for entry in entries
            if isinstance(entry, dict) and entry.get(source_key) is not None
        ]
        used_vector_arg = vector_arg
    else:
        out["entries"] = entries

    # Scalar context is strictly manifest-driven. A consumer opts in by
    # declaring from_entries_key on a primitive property. Explicit arguments
    # always win; mixed/missing producer values remain unresolved.
    for arg_name, spec in properties.items():
        if arg_name in out or not _is_scalar_property(spec):
            continue
        source_key = spec.get("from_entries_key")
        if not isinstance(source_key, str) or not source_key:
            continue
        homogeneous, value = _uniform_scalar(entries, source_key)
        if homogeneous:
            out[arg_name] = value

    return out, used_vector_arg
