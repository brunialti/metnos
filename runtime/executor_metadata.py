"""Canonical catalog metadata shared by loaders, diagnostics and UI.

These helpers classify declared facts only.  They never certify behavioral
conformance and never infer authority from an executor name.
"""
from __future__ import annotations

from executor_standard import STANDARD_ID


SOURCE_KINDS = frozenset({"handcrafted", "synthesized", "imported", "builtin"})
MEMBERSHIP_KINDS = frozenset({"builtin", "third-party"})
INTELLIGENCE_KINDS = frozenset({"deterministic", "llm", "agentic"})
TRANSPORT_KINDS = frozenset({
    "local-subprocess", "local-or-remote", "remote-device", "in-process",
})
STANDARD_STATES = frozenset({"candidate", "legacy", "declared", "invalid"})
_CANDIDATE_LIFECYCLES = frozenset({"proposed", "synthesized"})

# Execution policy is deliberately conservative.  Missing metadata never
# grants concurrency: old and newly-loaded executors remain byte-for-byte on
# the historical sequential path until a signed manifest opts in and carries
# the equivalence admission marker required by the standard.
DEFAULT_EXECUTION_POLICY = {
    "effect": "unknown",
    "parallelism_class": 0,
    "resource_class": "default",
    "concurrency_key": "none",
    "equivalence_gate": "unverified",
}


def standard_state(declaration: object, lifecycle: object = "active") -> str:
    """Return lifecycle/declaration state without claiming semantic proof."""
    if str(lifecycle or "active") in _CANDIDATE_LIFECYCLES:
        return "candidate"
    if declaration is None or declaration == "":
        return "legacy"
    if declaration == STANDARD_ID:
        return "declared"
    return "invalid"


def source_kind(manifest: dict | None = None, *, synthesized: bool = False,
                builtin: bool = False) -> str:
    """Classify origin from explicit provenance and loader context."""
    if builtin:
        return "builtin"
    data = manifest if isinstance(manifest, dict) else {}
    declared_origin = data.get("origin")
    if declared_origin in SOURCE_KINDS:
        return str(declared_origin)
    provenance = data.get("provenance") or {}
    # Third-party imports remain distinguishable from Metnos-owned executors.
    # First-party GitHub executors declare ``origin = "handcrafted"`` and do
    # not carry import provenance, even when installed below user data.
    if isinstance(provenance, dict) and provenance.get("imported_from"):
        return "imported"
    if synthesized or str(data.get("lifecycle") or "") in _CANDIDATE_LIFECYCLES:
        return "synthesized"
    return "handcrafted"


def membership_kind(manifest: dict | None = None, *, builtin: bool = False) -> str:
    """Classify product ownership independently from path/origin/transport.

    Imported provenance is the explicit third-party boundary. Metnos-owned
    executors, including GitHub materialized in user-data, remain builtin.
    """
    if builtin:
        return "builtin"
    data = manifest if isinstance(manifest, dict) else {}
    provenance = data.get("provenance") or {}
    if isinstance(provenance, dict) and provenance.get("imported_from"):
        return "third-party"
    return "builtin"


def intelligence_kind(manifest: dict | None = None) -> str:
    """Return the executor's declared internal reasoning mode.

    Agentic execution is never inferred: it is a behavioral claim and must be
    explicit.  Existing manifests with an LLM capability retain an honest
    compatibility default; everything else fails closed to deterministic.
    """
    data = manifest if isinstance(manifest, dict) else {}
    declared = data.get("intelligence")
    if declared in INTELLIGENCE_KINDS:
        return str(declared)
    capabilities = data.get("capabilities") or []
    if any(
        isinstance(capability, dict)
        and str(capability.get("name") or "").startswith("llm:")
        for capability in capabilities
    ):
        return "llm"
    return "deterministic"


def transport_kind(manifest: dict | None = None, *, in_process: bool = False) -> str:
    """Classify the declared execution route; actual target is per invocation."""
    if in_process:
        return "in-process"
    data = manifest if isinstance(manifest, dict) else {}
    placement = data.get("placement") or {}
    if isinstance(placement, dict):
        if placement.get("scope") == "device":
            return "remote-device"
        if placement.get("scope") == "any" and placement.get("device_ok") is True:
            return "local-or-remote"
    return "local-subprocess"


def output_schema(manifest: dict | None = None) -> str:
    data = manifest if isinstance(manifest, dict) else {}
    output = data.get("output") or {}
    if not isinstance(output, dict):
        return ""
    value = output.get("schema_inline")
    return value.strip() if isinstance(value, str) else ""


_RECORD_KEYS = ("entries", "results")


def output_record(schema: str) -> tuple[str, tuple[str, ...]]:
    """The rows a tool returns, as its `[output]` schema declares them: the
    key that holds them (`entries` or `results`) and the field names of the
    record inside `Array<{...}>`.

    ("", ()) when the schema declares the rows without their fields
    (`Array<object>`): nothing is guessed. Nested records, comments and types
    are skipped; a `?` after a name only says the field may be absent.
    """
    if not isinstance(schema, str):
        return "", ()
    for key in _RECORD_KEYS:
        position = 0
        while True:
            position = schema.find(key, position)
            if position < 0:
                break
            before = schema[position - 1] if position else " "
            rest = schema[position + len(key):].lstrip(" ?")
            position += len(key)
            if before.isalnum() or before == "_" or not rest.startswith(":"):
                continue
            body = rest[1:].lstrip()
            opening = next((mark for mark in ("Array<{", "[{", "{")
                            if body.startswith(mark)), None)
            if opening is None:
                break
            names = _top_level_names(body[len(opening):])
            if names:
                return key, names
            break
    return "", ()


def _top_level_names(body: str) -> tuple[str, ...]:
    names: list[str] = []
    depth, start, index, quote = 0, 0, 0, ""
    pieces: list[str] = []
    while index < len(body):
        char = body[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "#":
            # A comment runs to the end of its line and declares nothing.
            end = body.find("\n", index)
            end = len(body) if end < 0 else end
            body = body[:index] + " " * (end - index) + body[end:]
        elif char in "{[<(":
            depth += 1
        elif char in "}]>)":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            pieces.append(body[start:index])
            start = index + 1
        index += 1
    pieces.append(body[start:index])
    for piece in pieces:
        name = piece.split(":", 1)[0].strip().rstrip("?").strip().strip("'\"")
        if name and name.replace("_", "").isalnum() and name not in names:
            names.append(name)
    return tuple(names)


def execution_policy(manifest: dict | None = None) -> dict:
    """Return the normalized, fail-closed executor execution policy.

    Validation belongs to :mod:`executor_standard`; this helper only supplies
    safe defaults to every loader/transport.  It intentionally ignores unknown
    values instead of turning malformed metadata into parallel authority.
    """
    data = manifest if isinstance(manifest, dict) else {}
    raw = data.get("execution") or {}
    if not isinstance(raw, dict):
        return dict(DEFAULT_EXECUTION_POLICY)

    policy = dict(DEFAULT_EXECUTION_POLICY)
    if raw.get("effect") in {
            "unknown", "read_only", "create_only", "reversible", "mutating",
            "interactive"}:
        policy["effect"] = raw["effect"]
    level = raw.get("parallelism_class")
    if isinstance(level, int) and not isinstance(level, bool) and 0 <= level <= 3:
        policy["parallelism_class"] = level
    if raw.get("resource_class") in {
            "default", "local_io", "network_io", "cpu", "llm", "browser", "device"}:
        policy["resource_class"] = raw["resource_class"]
    if raw.get("concurrency_key") in {
            "none", "device", "account", "browser_session", "path"}:
        policy["concurrency_key"] = raw["concurrency_key"]
    if raw.get("equivalence_gate") in {"unverified", "verified"}:
        policy["equivalence_gate"] = raw["equivalence_gate"]
    from execution_effects import validate_effects
    from frozen_plan_consent import normalized_policy as _frozen_policy
    effect_findings = validate_effects(data)
    if not effect_findings:
        rules = raw.get("effects")
        if isinstance(rules, list):
            policy["effects"] = tuple(dict(rule) for rule in rules)
    elif "effects" in raw:
        # A malformed refinement must invalidate the whole effect claim;
        # retaining a mutating/read-only fallback would let admission bypass
        # reinterpret a broken conditional contract as trusted authority.
        policy["effect"] = "unknown"
        policy["parallelism_class"] = 0
    frozen = _frozen_policy(data)
    if frozen is not None:
        policy["frozen_plan"] = frozen

    # Defence in depth: even if validation was bypassed, an incomplete opt-in
    # degrades to serial rather than acquiring parallel execution authority.
    if (policy["parallelism_class"] <= 0
            or policy["effect"] in {"unknown", "interactive"}
            or (policy["effect"] != "read_only"
                and policy["concurrency_key"] == "none")
            or policy["equivalence_gate"] != "verified"):
        policy["parallelism_class"] = 0
    return policy


def generated_execution_policy_toml() -> str:
    """Canonical policy fragment for every generated executor.

    Generators consume this fragment instead of duplicating policy literals.
    Changing the safe generated default is therefore a single-point runtime
    decision, while the validator remains the independent admission authority.
    """
    policy = DEFAULT_EXECUTION_POLICY
    return "\n".join((
        "[execution]",
        f'effect           = "{policy["effect"]}"',
        f'parallelism_class = {policy["parallelism_class"]}',
        f'resource_class   = "{policy["resource_class"]}"',
        f'concurrency_key  = "{policy["concurrency_key"]}"',
        f'equivalence_gate = "{policy["equivalence_gate"]}"',
    ))
