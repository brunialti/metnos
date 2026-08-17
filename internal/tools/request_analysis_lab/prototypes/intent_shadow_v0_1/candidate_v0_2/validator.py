"""Fail-closed validator for the compact, model-facing IR."""
from __future__ import annotations

import math
from typing import Any

from .registry_projection import validate_projection
from .types import (
    IRBarrier,
    IRDocument,
    IROperation,
    IROperationGraph,
    IRStep,
    IRSystemControl,
    IRUnrepresentable,
    Issue,
    ValidationResult,
)


MAX_DEPTH = 24
MAX_STEPS = 256


def _issue(issues: list[Issue], code: str, path: str, message: str) -> None:
    issues.append(Issue(code, path, message))


def _reject_nonfinite(value: Any, path: str, issues: list[Issue]) -> None:
    if type(value) is float and not math.isfinite(value):
        _issue(issues, "D02_NONFINITE", path, "non-finite JSON number")
    elif type(value) is list:
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{path}[{index}]", issues)
    elif type(value) is dict:
        for key, item in value.items():
            _reject_nonfinite(item, f"{path}.{key}", issues)


def _closed(
    value: Any,
    required: set[str],
    optional: set[str],
    path: str,
    issues: list[Issue],
) -> bool:
    if type(value) is not dict:
        _issue(issues, "D03_OBJECT_TYPE", path, "exact JSON object required")
        return False
    actual = set(value)
    missing = sorted(required - actual)
    extra = sorted(actual - required - optional)
    if missing or extra:
        _issue(issues, "D03_CLOSED_OBJECT", path, f"missing={missing!r},extra={extra!r}")
        return False
    return True


def _steps(
    value: Any,
    *,
    registry: dict[str, Any],
    path: str,
    depth: int,
    count: list[int],
    visible: set[int],
    next_ordinal: list[int],
    issues: list[Issue],
) -> tuple[IRStep, ...]:
    if depth > MAX_DEPTH:
        _issue(issues, "LIMIT_DEPTH", path, "maximum nesting exceeded")
        return ()
    if type(value) is not list or not value:
        _issue(issues, "D03_STEPS_TYPE", path, "nonempty exact array required")
        return ()
    result: list[IRStep] = []
    current_visible = set(visible)
    for index, node in enumerate(value):
        node_path = f"{path}[{index}]"
        count[0] += 1
        if count[0] > MAX_STEPS:
            _issue(issues, "LIMIT_STEPS", node_path, "maximum node count exceeded")
            continue
        if type(node) is not dict:
            _issue(issues, "D03_STEP_TYPE", node_path, "exact JSON object required")
            continue
        if "route" in node:
            if not _closed(node, {"route"}, {"from"}, node_path, issues):
                continue
            route = node.get("route")
            if type(route) is not str or route not in registry["operations"]:
                _issue(issues, "ROUTE_AUTHORITY", f"{node_path}.route", "route outside registry")
                continue
            sources: tuple[int, ...] = ()
            if "from" in node:
                raw_sources = node["from"]
                if type(raw_sources) is not list or not raw_sources:
                    _issue(issues, "D03_FROM_TYPE", f"{node_path}.from", "nonempty exact array required")
                    continue
                if any(type(item) is not int or item < 0 for item in raw_sources):
                    _issue(issues, "D03_FROM_ITEM", f"{node_path}.from", "nonnegative exact integers required")
                    continue
                if len(raw_sources) != len(set(raw_sources)):
                    _issue(issues, "D04_FROM_UNIQUE", f"{node_path}.from", "duplicate authority source")
                    continue
                # Source order is not semantic.  Canonicalize the unique set so
                # equivalent IR documents compile to the same bytes and hash.
                sources = tuple(sorted(raw_sources))
                ordinal = next_ordinal[0]
                for source in sources:
                    if source not in current_visible:
                        code = "FROM_SELF_OR_FORWARD" if source >= ordinal else "FROM_OUT_OF_SCOPE"
                        _issue(
                            issues,
                            code,
                            f"{node_path}.from",
                            "source is not a dominating prior operation",
                        )
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            current_visible.add(ordinal)
            result.append(IROperation(route, sources))
            continue
        if "barrier" in node:
            if not _closed(node, {"barrier", "body"}, set(), node_path, issues):
                continue
            barrier = node.get("barrier")
            if type(barrier) is not str or barrier not in registry["barriers"]:
                _issue(issues, "BARRIER_AUTHORITY", f"{node_path}.barrier", "barrier outside registry")
                continue
            body = _steps(
                node.get("body"),
                registry=registry,
                path=f"{node_path}.body",
                depth=depth + 1,
                count=count,
                visible=set(current_visible),
                next_ordinal=next_ordinal,
                issues=issues,
            )
            if body:
                result.append(IRBarrier(barrier, body))
            continue
        _issue(issues, "STEP_DISCRIMINANT", node_path, "route or barrier required")
    return tuple(result)


def validate_ir(value: Any, registry: dict[str, Any]) -> ValidationResult:
    validate_projection(registry)
    issues: list[Issue] = []
    if type(value) is not dict:
        return ValidationResult(False, None, (Issue("D03_ROOT_TYPE", "$", "exact JSON object required"),))
    _reject_nonfinite(value, "$", issues)
    kind = value.get("kind")
    document: IRDocument | None = None
    if kind == "operation_graph":
        if _closed(value, {"kind", "steps"}, set(), "$", issues):
            steps = _steps(
                value["steps"],
                registry=registry,
                path="$.steps",
                depth=0,
                count=[0],
                visible=set(),
                next_ordinal=[0],
                issues=issues,
            )
            if steps:
                document = IROperationGraph(steps)
    elif kind == "system_control":
        if _closed(value, {"kind", "control"}, set(), "$", issues):
            control = value.get("control")
            if type(control) is not str or control not in registry["system_controls"]:
                _issue(issues, "CONTROL_AUTHORITY", "$.control", "control outside registry")
            else:
                document = IRSystemControl(control)
    elif kind == "unrepresentable":
        if _closed(value, {"kind", "reason"}, set(), "$", issues):
            reason = value.get("reason")
            if type(reason) is not str or reason not in registry["unrepresentable_reasons"]:
                _issue(issues, "REASON_AUTHORITY", "$.reason", "reason outside registry")
            else:
                document = IRUnrepresentable(reason)
    else:
        _issue(issues, "ROOT_KIND", "$.kind", "unknown exclusive root")
    if issues:
        document = None
    return ValidationResult(not issues, document, tuple(issues))
