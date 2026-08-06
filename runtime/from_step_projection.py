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
CONTEXT_ERRORS_KEY = "_from_step_context_errors"


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


def _is_provided(value: Any) -> bool:
    """Return whether an argument carries a concrete, non-empty value."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _effective_arg_value(args: dict, properties: dict, arg_name: str):
    """Read an explicit argument, falling back to its manifest default."""
    if arg_name in args and args.get(arg_name) is not None:
        return args.get(arg_name)
    spec = properties.get(arg_name)
    return spec.get("default") if isinstance(spec, dict) else None


def _context_requirement_applies(requirement: Any, args: dict,
                                 properties: dict) -> bool:
    """Evaluate a manifest-declared source-context requirement.

    ``true`` keeps the historical unconditional meaning.  A mapping makes the
    requirement backend-aware without naming a backend in runtime code, for
    example ``{arg = "client", values = ["metnos"]}``.
    """
    if requirement is True:
        return True
    if not isinstance(requirement, dict):
        return False
    selector = requirement.get("arg")
    values = requirement.get("values")
    if not isinstance(selector, str) or not selector.strip():
        return False
    actual = _effective_arg_value(args, properties, selector)
    if isinstance(values, list):
        return actual in values
    if requirement.get("nonempty") is True:
        return _is_provided(actual)
    return False


def required_source_context_fields(
        args: dict, consumer_schema: dict | None, *,
        allow_deferred_from_step: bool = False) -> list[str]:
    """Return source-context fields missing from a direct payload call.

    A manifest can mark scalar properties with ``from_entries_required``.
    The value may be ``true`` or a data-driven condition.  When identifiers
    are supplied directly, every applicable field must be explicit; when a
    planner still carries ``from_step``, pre-execution validation may defer the
    check until projection has inspected the producer entries.
    """
    if not isinstance(args, dict):
        return []
    properties = _properties(consumer_schema)
    if not properties:
        return []

    source_groups = [
        group for group in (
            consumer_schema.get("requires_one_of", [])
            if isinstance(consumer_schema, dict) else [])
        if isinstance(group, list) and "from_step" in group
    ]
    deferred = _is_provided(args.get("from_step"))
    if allow_deferred_from_step and deferred:
        return []

    if source_groups:
        direct_payload = any(
            _is_provided(args.get(name))
            for group in source_groups for name in group
            if name != "from_step"
        )
    else:
        direct_payload = any(
            _is_array_property(spec)
            and isinstance(spec.get("from_entries_key"), str)
            and _is_provided(args.get(name))
            for name, spec in properties.items()
            if isinstance(spec, dict)
        )
    if not direct_payload:
        return []

    missing: list[str] = []
    for arg_name, spec in properties.items():
        if not (_is_scalar_property(spec)
                and isinstance(spec.get("from_entries_key"), str)):
            continue
        if not _context_requirement_applies(
                spec.get("from_entries_required"), args, properties):
            continue
        if not _is_provided(args.get(arg_name)):
            missing.append(arg_name)
    return sorted(missing)


def carries_upstream_payload(args: dict | None) -> bool:
    """Return whether these arguments already carry a producer's list payload.

    ``from_step`` before resolution and ``entries`` after it are the two forms
    of the same fact: this step reads what an earlier one produced.
    """
    if not isinstance(args, dict):
        return False
    step = args.get("from_step")
    if isinstance(step, int) and not isinstance(step, bool) and step >= 1:
        return True
    return _is_provided(args.get("entries"))


def projection_can_fill(consumer_schema: dict | None, arg_names) -> bool:
    """Return whether projection could fill at least one of these arguments.

    The vector payload always lands in an array property, so a disjunction that
    contains one is satisfiable by piping alone and must not be rejected before
    the producer has run.  A scalar-only disjunction is not: scalar context is
    projected only when every source entry agrees, and its absence is reported
    by :func:`required_source_context_fields` with a message of its own.
    """
    properties = _properties(consumer_schema)
    if not properties:
        return False
    return any(_is_array_property(properties.get(name)) for name in arg_names)


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
    context_errors: list[dict[str, str]] = []
    for arg_name, spec in properties.items():
        if arg_name in out or not _is_scalar_property(spec):
            continue
        source_key = spec.get("from_entries_key")
        if not isinstance(source_key, str) or not source_key:
            continue
        homogeneous, value = _uniform_scalar(entries, source_key)
        if homogeneous:
            out[arg_name] = value
        elif _context_requirement_applies(
                spec.get("from_entries_required"), out, properties):
            context_errors.append({
                "arg": arg_name,
                "source_key": source_key,
                "reason": "mixed_or_missing",
            })

    if context_errors:
        out[CONTEXT_ERRORS_KEY] = context_errors

    return out, used_vector_arg
