#!/usr/bin/env python3
"""The role contract belongs in code, not in a sentence.

Three wordings of the same rule have now been measured on 120 held-out queries:

    A  no contract sentence at all                       112
    B  contract, non-request branch stated first         110
    D  contract, request branch stated first, positive   108

Every wording loses. The reason is visible in the failure reasons: the sentence
does what it was written for -- `nonrequest_executable` goes from 3 to 0 -- and
then overshoots, `incomplete_request` goes from 1 to 5. Pushing the model toward
`none` on non-request records makes it reach for `none` on request records too,
and reordering the branches does not separate them.

The validator's rule is total and needs no judgement (lines 1301-1305): a record
whose role is not `request` MUST carry verb `none` and object `none`. That is a
normalization, so §7.9 applies -- deterministic code beats an LLM instruction
when both can do the job. This repair sets those two fields on non-request
records and touches nothing else. It cannot produce the overshoot, because it
never speaks to the model at all.

It does NOT invent a route for a request record that has none: an
`incomplete_request` stays a failure, honestly. Same discipline as
`riparo_ancore.py`.

Read-only on production: wraps a module attribute of the frozen bench.
"""
from __future__ import annotations


def install(module) -> bool:
    """Normalize non-request records before the frame is validated."""
    originale = module.validate_frame_v23

    def normalizza(frame: dict) -> int:
        toccati = 0
        for record in (frame.get("predicates") or []):
            if record.get("role") == "request":
                continue
            if record.get("verb") != "none" or record.get("object") != "none":
                record["verb"] = "none"
                record["object"] = "none"
                toccati += 1
        return toccati

    def validate(frame, *args, **kwargs):
        if isinstance(frame, dict):
            normalizza(frame)
        return originale(frame, *args, **kwargs)

    module.validate_frame_v23 = validate
    return True
