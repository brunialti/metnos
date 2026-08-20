"""Deterministic bounded reduction graphs for durable-plan v1."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .schema import MAX_PLAN_JSON_BYTES, canonical_json, digest_json


DEFAULT_FAN_IN = 32
MAX_REDUCTION_INPUTS = 1_000_000
MAX_REDUCTION_NODES = 1_100_000


class ReductionPlanError(ValueError):
    """A reduction cannot be represented inside the v1 safety bounds."""


@dataclass(frozen=True, slots=True)
class ReductionNode:
    """One stable node whose inputs are leaf keys or preceding node keys."""

    key: str
    level: int
    ordinal: int
    inputs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "level": self.level,
            "ordinal": self.ordinal,
            "inputs": list(self.inputs),
        }


@dataclass(frozen=True, slots=True)
class ReductionGraph:
    """Canonical fan-in tree; an empty input has no root."""

    fan_in: int
    leaves: tuple[str, ...]
    nodes: tuple[ReductionNode, ...]
    root_key: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "metnos.reduction-graph/1",
            "fan_in": self.fan_in,
            "leaves": list(self.leaves),
            "nodes": [node.as_dict() for node in self.nodes],
            "root_key": self.root_key,
        }

    @property
    def canonical_json(self) -> str:
        return canonical_json(self.as_dict(), max_bytes=MAX_PLAN_JSON_BYTES)

    @property
    def digest(self) -> str:
        return digest_json(
            "durable-reduction-graph",
            self.as_dict(),
            max_bytes=MAX_PLAN_JSON_BYTES,
        )


def _leaf_key(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ReductionPlanError("reduction leaf keys must be non-empty strings")
    return value


def build_reduction_graph(
    input_keys: Sequence[str],
    *,
    fan_in: int = DEFAULT_FAN_IN,
    max_inputs: int = MAX_REDUCTION_INPUTS,
    max_nodes: int = MAX_REDUCTION_NODES,
) -> ReductionGraph:
    """Build the same balanced graph regardless of caller or locale ordering."""

    if isinstance(fan_in, bool) or not isinstance(fan_in, int) or fan_in < 2:
        raise ReductionPlanError("reduction fan_in must be an integer of at least 2")
    if fan_in > 1024:
        raise ReductionPlanError("reduction fan_in exceeds the v1 limit")
    if (
        isinstance(max_inputs, bool)
        or not isinstance(max_inputs, int)
        or max_inputs < 0
        or isinstance(max_nodes, bool)
        or not isinstance(max_nodes, int)
        or max_nodes < 0
    ):
        raise ReductionPlanError("reduction limits must be non-negative integers")

    leaves = tuple(sorted((_leaf_key(item) for item in input_keys), key=str.encode))
    if len(leaves) != len(set(leaves)):
        raise ReductionPlanError("reduction input keys must be unique")
    if len(leaves) > max_inputs:
        raise ReductionPlanError("reduction input width exceeds the admitted maximum")
    if not leaves:
        return ReductionGraph(fan_in=fan_in, leaves=(), nodes=(), root_key=None)
    if len(leaves) == 1:
        return ReductionGraph(
            fan_in=fan_in,
            leaves=leaves,
            nodes=(),
            root_key=leaves[0],
        )

    nodes: list[ReductionNode] = []
    current = leaves
    level = 0
    while len(current) > 1:
        next_level: list[str] = []
        for ordinal, start in enumerate(range(0, len(current), fan_in)):
            inputs = tuple(current[start:start + fan_in])
            key = digest_json(
                "durable-reduction-node",
                {"fan_in": fan_in, "inputs": list(inputs), "level": level},
                max_bytes=MAX_PLAN_JSON_BYTES,
            )
            nodes.append(ReductionNode(key, level, ordinal, inputs))
            if len(nodes) > max_nodes:
                raise ReductionPlanError(
                    "reduction graph exceeds the admitted node maximum"
                )
            next_level.append(key)
        current = tuple(next_level)
        level += 1
    return ReductionGraph(
        fan_in=fan_in,
        leaves=leaves,
        nodes=tuple(nodes),
        root_key=current[0],
    )
