"""Bounded, digest-bound domain errors in otherwise complete result receipts.

These facts never excuse missing sources, dependencies, artifacts or usage.
Only the unit originating an error reports it. Downstream reducers/publishers
may retain domain summaries, but must not re-emit the same domain_outcome.
This keeps errors observable as work progresses without multiplying counts.

Each original item contributes to exactly one primary error code. Counts refer
to items, not exceptions or attempts. Domain producers own item identity and
choose that primary code; the general-purpose kernel cannot infer either from
aggregate counts. A committed receipt is counted once, including after replay
or semantic reuse, and its facts remain available after workload completion.
"""

from collections.abc import Mapping
import re
from typing import Any

from .schema import SchemaValidationError, canonical_json


MAX_DOMAIN_ERRORS = 1_000_000
MAX_DOMAIN_ERROR_CODES = 20
_CODE = re.compile(r"[a-z][a-z0-9_.-]{2,95}")
DOMAIN_OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["version", "error_counts"],
    "properties": {
        "version": {"type": "integer", "const": 1},
        "error_counts": {
            "type": "object", "maxProperties": MAX_DOMAIN_ERROR_CODES,
            "propertyNames": {"pattern": "^[a-z][a-z0-9_.-]{2,95}$"},
            "additionalProperties": {
                "type": "integer", "minimum": 1, "maximum": MAX_DOMAIN_ERRORS,
            },
        },
    },
}


def domain_outcome(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate the reserved field; absence is not an error and means no facts."""
    if "domain_outcome" not in payload:
        return None
    value = payload["domain_outcome"]
    if (
        not isinstance(value, Mapping) or set(value) != {"version", "error_counts"}
        or type(value["version"]) is not int or value["version"] != 1
    ):
        raise SchemaValidationError("domain_outcome has an invalid version or shape")
    counts = value["error_counts"]
    if (
        not isinstance(counts, Mapping) or len(counts) > MAX_DOMAIN_ERROR_CODES
        or any(
            not isinstance(code, str) or _CODE.fullmatch(code) is None
            or type(count) is not int or not 1 <= count <= MAX_DOMAIN_ERRORS
            for code, count in counts.items()
        )
        or sum(counts.values()) > MAX_DOMAIN_ERRORS
    ):
        raise SchemaValidationError("domain_outcome has invalid error counts")
    return {"version": 1, "error_counts": dict(counts)}


def domain_terminal_detail(payload: Mapping[str, Any]) -> str | None:
    """One projection for both fresh commits and semantic result reuse."""
    outcome = domain_outcome(payload)
    if outcome is None or not outcome["error_counts"]:
        return None
    return canonical_json({
        "schema_version": "metnos.durable-unit-terminal/1",
        "domain_outcome": outcome,
    }, max_bytes=65536)
