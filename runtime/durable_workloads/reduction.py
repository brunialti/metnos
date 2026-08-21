"""Constant-space capacity arithmetic for hierarchical reductions."""

from __future__ import annotations

MAX_REDUCTION_INPUTS = 1_000_000


class ReductionPlanError(ValueError):
    """A reduction cannot be represented inside the v1 safety bounds."""


def hierarchical_node_bound(input_count: int) -> int:
    """Return the worst successful unit count for a bounded reduction.

    Payload-size grouping can reduce an admitted fan-in to two.  Using that
    worst converging case keeps the stage cap honest without inspecting or
    retaining the corpus.  A single input still needs one reducer invocation
    because the parent and reducer output contracts may differ.
    """

    if (
        isinstance(input_count, bool)
        or not isinstance(input_count, int)
        or not 0 <= input_count <= MAX_REDUCTION_INPUTS
    ):
        raise ReductionPlanError(
            "reduction input count exceeds the supported bounds"
        )
    remaining = max(1, input_count)
    total = 0
    while True:
        produced = (remaining + 1) // 2
        total += produced
        if produced == 1:
            return total
        remaining = produced
