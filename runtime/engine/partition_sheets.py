"""Keep complete partitioned records when the request leaves columns open.

A planner-selected projection is not a user-selected schema. With no schema
in the request, the existing automatic projection retains the folder and
all record fields, including both sides of comparisons. This uses declared
partition provenance, not producer names or filesystem inspection (ADR 0244).
"""
from __future__ import annotations

from dataclasses import replace
import re

import partitioned_runs
from from_step_projection import has_explicit_from_step_alternative

from .types import Framework


def _schema_left_open(query: str) -> bool:
    """Uncertain language or a possible column choice preserves the plan."""
    from compound_decomposer import _compound_lexicon, _raw_query_chunks, derive_sink_fields

    if not query or derive_sink_fields(query):
        return False
    lexicon = _compound_lexicon()
    if not lexicon or not all(lexicon.get("parser.compound." + name) for name in
                             ("schema_marker", "schema_restriction", "tabular_noun",
                              "with_connector")):
        return False

    def forms(concept):
        values = lexicon.get("parser.compound." + concept) or []
        return r"(?<!\w)(?:" + "|".join(re.escape(item) for item in values) + r")(?!\w)"

    # Conservatively keep even single-column and layout choices. A table
    # followed by "with" may name one column without a schema marker.
    if re.search(forms("schema_marker"), query, re.I):
        return False
    # A restriction in the table clause can select one field without naming
    # columns. Restrictions in a separate search clause do not select schema.
    for clause in _raw_query_chunks(query):
        if (re.search(forms("tabular_noun"), clause, re.I)
                and re.search(forms("schema_restriction"), clause, re.I)):
            return False
    return re.search(forms("tabular_noun") + r"[^.;\n]*" + forms("with_connector"),
                     query, re.I) is None


def complete_partition_sheets(framework: Framework, catalog, *, query: str = "") -> Framework:
    """Use automatic projection only on proved partitioned record lineage."""
    if not _schema_left_open(query):
        return framework
    by_name = {getattr(item, "name", None): item for item in catalog or []}
    steps = list(framework.steps)
    partitioned: set[int] = set()
    changed = False
    for index, step in enumerate(steps, 1):
        args = step.args if isinstance(step.args, dict) else {}
        tool = by_name.get(step.tool)
        schema = getattr(tool, "args_schema", None) or {}
        policy = partitioned_runs.policy_of(tool)
        if policy and (args.get("group_by") == "first_level"
                       or args.get(policy["list_arg"]) not in (None, [], "")):
            partitioned.add(index)
        source = args.get("from_step")
        # Explicit source alternatives override from_step: their provenance
        # is not proved by that argument, even with a string reference.
        if (type(source) is not int or source not in partitioned or source >= index
                or has_explicit_from_step_alternative(args, schema)):
            continue
        if str(step.tool or "").split("_")[1:] == ["entries"]:
            partitioned.add(index)
        properties = schema.get("properties", {})
        if not {"from_step", "columns", "column_specs"} <= set(properties):
            continue
        rewritten = {key: value for key, value in args.items()
                     if key not in ("columns", "column_specs")}
        if rewritten != args:
            steps[index - 1] = replace(step, args=rewritten)
            changed = True
    return replace(framework, steps=steps) if changed else framework
