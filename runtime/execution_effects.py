"""Resolve signed argument-dependent effects before executor invocation."""
from __future__ import annotations


def validate_effects(manifest: dict) -> list[str]:
    execution = manifest.get("execution") or {}
    rules = execution.get("effects")
    if rules is None:
        return []
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        return ["execution.effects must contain 1..32 rules"]
    if execution.get("effect") not in {
            "unknown", "mutating", "reversible", "create_only"}:
        return ["conditional effects require a conservative mutating fallback"]
    properties = (manifest.get("args") or {}).get("properties") or {}
    seen = set()
    discriminator = None
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {
                "argument", "equals", "effect"}:
            return ["effect rules require exactly argument, equals, effect"]
        argument, value = rule["argument"], rule["equals"]
        if not isinstance(argument, str) or not isinstance(value, str):
            return ["effect predicates require string argument and value"]
        spec = properties.get(argument) or {}
        if (not isinstance(spec, dict) or spec.get("type") != "string"
                or value not in (spec.get("enum") or [])):
            return ["effect predicates must reference a declared string enum"]
        if rule["effect"] != "read_only":
            return ["conditional effects may only refine to read_only"]
        if (argument, value) in seen or discriminator not in {None, argument}:
            return ["effect predicates must be unique and use one discriminator"]
        discriminator = argument
        seen.add((argument, value))
    if execution.get("parallelism_class", 0) != 0:
        return ["conditional effects require serial execution"]
    return []


def resolve_execution_effect(executor, args: dict) -> str | None:
    """Return the authenticated contract effect for these final arguments."""
    if executor is None or not getattr(
            executor, "execution_policy_declared", False):
        return None
    policy = getattr(executor, "execution_policy", {}) or {}
    fallback = policy.get("effect", "unknown")
    for rule in policy.get("effects", ()):
        if args.get(rule["argument"]) == rule["equals"]:
            return rule["effect"]
    return fallback
