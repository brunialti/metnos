"""Dependency-free subprocess fixture for the F5 byte-stability gate."""

from __future__ import annotations

import json

from durable_workloads.compiler import ApprovedOutputSchema, OutputSchemaRegistry, compile_plan
from helpers import inventory, plan


class _NoExternalRunners:
    def resolve(self, _kind, _name):
        raise AssertionError("the inventory-only fixture has no external runner")


def process_bytes() -> str:
    schemas = OutputSchemaRegistry((
        ApprovedOutputSchema.create(
            "metnos.inventory-seal/1",
            {
                "type": "object",
                "properties": {
                    "digest": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["digest", "sources"],
                "additionalProperties": False,
            },
        ),
    ))
    compiled = compile_plan(
        plan(), inventory(), runners=_NoExternalRunners(), output_schemas=schemas,
    )
    return json.dumps(
        [compiled.canonical_plan_json, compiled.canonical_graph_json],
        ensure_ascii=False,
        separators=(",", ":"),
    )
