#!/usr/bin/env python3
"""Generate the V26.5 compact decoder schema from frozen V26.4.1 schema."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


PARENT = Path(
    "/opt/metnos/internal/tools/request_analysis_lab/candidates/v2641/"
    "metnos_v2641_typed_phase1.schema.json"
)
EXPECTED_PARENT_SHA256 = "06452e75f0a1ec7a42c9bf87dfae3da4e1528fc07b02d9276272a1e9ff0c7fec"
REMOVED_FIELDS = {"clause_id", "atom_id", "output_index"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(value: Any) -> Any:
    if isinstance(value, list):
        return [
            transform(item) for item in value
            if not (isinstance(item, str) and item in REMOVED_FIELDS)
        ]
    if not isinstance(value, dict):
        return value

    result = {key: transform(item) for key, item in value.items()}
    properties = result.get("properties")
    if isinstance(properties, dict):
        for field in REMOVED_FIELDS:
            properties.pop(field, None)
        kind = properties.get("kind")
        if isinstance(kind, dict) and kind.get("const") == "from_atom_output":
            kind["const"] = "from_prior_atom"
            properties["source_ordinal"] = {"minimum": 1, "type": "integer"}
            required = result.get("required", [])
            if "source_ordinal" not in required:
                result["required"] = [*required, "source_ordinal"]
    return result


def generate() -> dict[str, Any]:
    if sha(PARENT) != EXPECTED_PARENT_SHA256:
        raise RuntimeError("parent schema hash mismatch")
    schema = transform(copy.deepcopy(json.loads(PARENT.read_text())))
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    for forbidden in (*REMOVED_FIELDS, "from_atom_output"):
        if forbidden in encoded:
            raise RuntimeError(f"forbidden compact-schema token remains: {forbidden}")
    return schema


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2, sort_keys=True))
