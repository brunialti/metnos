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

# A terminal producer reports the outcome of its whole branch, including
# upstream effects. Absence means unknown, never "nothing to do". The kernel
# projects this only after clean completion and agreement of every terminal
# unit; it never infers it from empty entries, skipped units or zero errors.
COMPLETION_OUTCOME_SCHEMA = {
    "type": "string", "enum": ["no_changes", "changes_applied"],
}


# The result of a Metnos tool invoked as it is (§2.1): a list of items done
# and, when some were lost, ``failed`` naming them.
EXECUTOR_RESULT_SCHEMA = "metnos.executor-result/1"

# What a tool names as the cause of a failure, its own or of one item
# (``error_class`` is language-neutral). Closed on purpose: the console shows
# only codes it has a text for, never a string chosen by a child process.
TOOL_CAUSES = frozenset({
    "not_found", "permission_denied", "io_error", "invalid_args",
    "invalid_input", "resource_unavailable", "dependency_unavailable",
    "unsupported", "timeout",
})
ITEM_FAILED = "item_failed"


def tool_cause(value: object) -> str | None:
    """The declared cause, when it is one the console can name."""
    return value if type(value) is str and value in TOOL_CAUSES else None


def item_failures_outcome(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Domain outcome of a tool that worked and lost some items.

    A tool answers ``ok: false`` both when it could not work and when it
    worked and some items failed. Only the first is a failed attempt: in the
    second the result stands and the lost items are counted, each under one
    cause (the same rule the chat applies, §2.8).

    Returns None when the tool declares an error of its own, names no failed
    item, or already reports its own domain outcome: none of these is ours to
    reinterpret.
    """
    if result.get("ok") is not False or "domain_outcome" in result:
        return None
    if any(result.get(key) not in (None, "")
           for key in ("error", "error_class", "error_code")):
        return None
    failed = result.get("failed")
    listed = ([item for item in failed if isinstance(item, Mapping)]
              if isinstance(failed, list) else [])
    total = result.get("fail_count")
    if type(total) is not int or total < len(listed):
        total = len(listed)
    if not 1 <= total <= MAX_DOMAIN_ERRORS:
        return None
    counts: dict[str, int] = {}
    for item in listed:
        code = tool_cause(item.get("error_class")) or ITEM_FAILED
        counts[code] = counts.get(code, 0) + 1
    if total > len(listed):
        # Items the tool counted without listing them: lost, cause not given.
        counts[ITEM_FAILED] = counts.get(ITEM_FAILED, 0) + total - len(listed)
    return {"version": 1, "error_counts": counts}


def completion_outcome(payload: Mapping[str, Any]) -> str | None:
    if "completion_outcome" not in payload:
        return None
    value = payload["completion_outcome"]
    if not isinstance(value, str) or value not in COMPLETION_OUTCOME_SCHEMA["enum"]:
        raise SchemaValidationError("completion_outcome is invalid")
    return value


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
    completion = completion_outcome(payload)
    detail = {}
    if outcome is not None and outcome["error_counts"]:
        detail["domain_outcome"] = outcome
    if completion is not None:
        detail["completion_outcome"] = completion
    if not detail:
        return None
    return canonical_json({
        "schema_version": "metnos.durable-unit-terminal/1",
        **detail,
    }, max_bytes=65536)
