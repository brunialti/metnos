#!/usr/bin/env python3
"""Deterministic repair of anchor collisions, before validation.

Two prompt formulations failed to teach the model that coordinated operations
need distinct anchors (39 -> 37 and 39 -> 36): "crea un riepilogo E un foglio"
and "conta i file E le directory" give one verb token to two operations, and the
model will not give up the token of the verb. It is right not to: the grammar
offers no second predicate token.

So the model labels and the code cuts. The anchor is declared in the prompt to
be "only a stable graph identifier"; in v23lite it is not an edge key either --
the schema drops `input_from_predicate_anchor_id` and carries edges by
`predicate_id`. Where the grammar does not supply a distinct token, one is
derived deterministically from a span the model has already committed to.

The repair only fires on a collision, so it is inert on every frame that already
satisfies the contract, and it never invents a token outside the sentence.
"""
from __future__ import annotations

import re

LITERAL = re.compile(r"https?://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\)")


def _first_legal(tokens: list[str], lower: int, preferred: int | None) -> int | None:
    """First token id strictly above `lower` that may carry an anchor.

    `preferred` is tried first when it is above the bound: it is the record's own
    patient, i.e. the span the model already assigned to this operation.
    """
    candidates = []
    if isinstance(preferred, int) and preferred > lower:
        candidates.append(preferred)
    candidates.extend(range(lower + 1, len(tokens) + 1))
    for candidate in candidates:
        if 1 <= candidate <= len(tokens) and not LITERAL.search(tokens[candidate - 1]):
            return candidate
    return None


def repair(frame: dict, tokens: list[str]) -> list[tuple[int, int, int]]:
    """Give every record a distinct increasing anchor. Returns the changes made."""
    heads = frame.get("semantic_heads")
    predicates = frame.get("predicates")
    if not isinstance(heads, list) or not isinstance(predicates, list):
        return []
    changes = []
    last = 0
    for head, predicate in zip(heads, predicates):
        if not isinstance(head, dict) or not isinstance(predicate, dict):
            return changes
        anchor = head.get("predicate_anchor_token_id")
        if not isinstance(anchor, int):
            return changes
        if anchor > last:
            last = anchor
            continue
        moved = _first_legal(tokens, last, head.get("patient_start_token_id"))
        if moved is None:
            return changes
        changes.append((head.get("predicate_id"), anchor, moved))
        head["predicate_anchor_token_id"] = moved
        predicate["predicate_anchor_token_id"] = moved
        last = moved
    return changes


def install(module) -> bool:
    """Repair the frame right before the frozen validator sees it."""
    original = module.validate_frame_v23

    def validating(frame, tokens, *args, **kwargs):
        repair(frame, tokens)
        return original(frame, tokens, *args, **kwargs)

    module.validate_frame_v23 = validating
    return True
