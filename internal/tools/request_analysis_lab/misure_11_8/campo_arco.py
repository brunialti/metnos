#!/usr/bin/env python3
"""Arm F: the prompt describes an edge field that v23lite does not have.

Found by cross-reading the assembled prompt against the schema and validator.

  * The schema pops `input_from_predicate_anchor_id` for v23lite (line ~795).
    The field the validator actually reads is `input_from_predicate_id`
    (lines 1100, 1188, 1287, 1312, 1571), and that name appears NOWHERE in the
    prompt text.
  * The validator wants a RECORD number there: `source_predicate_id` must be an
    int with 0 <= id < index, and `input_from_predicate_id` must equal it
    (1284-1288). TAG lite states this correctly for the head's own field, line
    522: "source_predicate_id is 0 or the predicate_id of an EARLIER predicate".
  * BASE (592-593) and COMPOUND (243-246) instruct the opposite for the mirror
    field: fill it with a TOKEN id, "not a record ordinal", "Never put a record
    number there".

So the model is told to build the data-flow graph through a deleted field, with
a rule that inverts the one the validator enforces, while the surviving field is
never named. The observed failure signature matches: [18] and [31] both fail as
`predicate_N_source_edge`.

This is the same defect class as cycle 2, which paid +2 on the tuning set: the
prompt instructed exactly what the validator rejects. Content is not removed
here, it is redirected onto the field that exists.

Read-only on production: module attributes only, the frozen file is untouched.
"""
from __future__ import annotations

BASE_FANTASMA = ("Set `input_from_predicate_anchor_id` to an earlier predicate "
                 "anchor\nsupplying an elided patient, otherwise 0.")
BASE_REALE = ("Set `input_from_predicate_id` to the `predicate_id` of the "
              "earlier record\nsupplying an elided patient, otherwise 0.")

CMP_FANTASMA = """\
- `input_from_predicate_anchor_id` is a graph edge, not a record ordinal. Use 0
  when the predicate has an explicit patient or no data dependency. Otherwise
  use the `predicate_anchor_token_id` of the earlier predicate whose result
  supplies this predicate's elided patient. Never put a record number there.
"""
CMP_REALE = """\
- `input_from_predicate_id` is a graph edge. Use 0 when the predicate has an
  explicit patient or no data dependency. Otherwise use the `predicate_id` of
  the earlier record whose result supplies this predicate's elided patient. The
  head's `source_predicate_id` MUST carry that same value.
"""


def install(module) -> bool:
    """Redirect both mentions onto the field the validator reads."""
    if BASE_FANTASMA not in module.BASE_INSTRUCTION:
        raise AssertionError("ghost edge field not found in BASE_INSTRUCTION")
    if CMP_FANTASMA not in module.COMPOUND_REFINEMENTS:
        raise AssertionError("ghost edge bullet not found in COMPOUND")
    module.BASE_INSTRUCTION = module.BASE_INSTRUCTION.replace(
        BASE_FANTASMA, BASE_REALE)
    module.COMPOUND_REFINEMENTS = module.COMPOUND_REFINEMENTS.replace(
        CMP_FANTASMA, CMP_REALE)
    return True
