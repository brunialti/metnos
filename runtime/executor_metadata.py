"""Canonical catalog metadata shared by loaders, diagnostics and UI.

These helpers classify declared facts only.  They never certify behavioral
conformance and never infer authority from an executor name.
"""
from __future__ import annotations

from executor_standard import STANDARD_ID


SOURCE_KINDS = frozenset({"handcrafted", "synthesized", "imported", "builtin"})
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
