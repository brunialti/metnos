"""Query-blind structural and semantic validation for intent-shadow 0.1."""
from __future__ import annotations

from typing import Any

from intent_shadow_registry import validate_registry_document
from intent_shadow_types import (
    BarrierRegion,
    DataEdge,
    Operation,
    OperationGraph,
    RootDocument,
    SystemControl,
    Unrepresentable,
    ValidationIssue,
    ValidationResult,
)


def _issue(issues: list[ValidationIssue], code: str, path: str, message: str) -> None:
    issues.append(ValidationIssue(code, path, message))


def _closed(
    value: Any,
    required: set[str],
    optional: set[str],
    path: str,
    issues: list[ValidationIssue],
) -> bool:
    if type(value) is not dict:
        _issue(issues, "OBJECT_TYPE", path, "expected exact object")
        return False
    actual = set(value)
    missing = sorted(required - actual)
    extra = sorted(actual - required - optional)
    if missing or extra:
        _issue(issues, "CLOSED_OBJECT", path, f"missing={missing!r},extra={extra!r}")
        return False
    return True


def _edge_values(
    edges: Any,
    *,
    destination_ports: list[str],
    visible: set[int],
    operations: dict[int, dict[str, Any]],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if type(edges) is not list or not edges:
        _issue(issues, "DATA_EDGE_SHAPE", path, "expected nonempty exact array")
        return
    signatures: set[tuple[int, str, str]] = set()
    for index, edge in enumerate(edges):
        edge_path = f"{path}[{index}]"
        if not _closed(edge, {"from"}, {"output", "input"}, edge_path, issues):
            continue
        source = edge.get("from")
        if type(source) is not int:
            _issue(issues, "DATA_EDGE_SOURCE_TYPE", f"{edge_path}.from", "exact integer required")
            continue
        if source not in visible:
            _issue(
                issues,
                "DATA_EDGE_DOMINANCE",
                f"{edge_path}.from",
                "source is not a dominating prior operation",
            )
            continue
        source_ports = operations[source]["output_ports"]
        output = edge.get("output")
        if "output" not in edge:
            if len(source_ports) != 1:
                _issue(issues, "DATA_EDGE_OUTPUT_AMBIGUOUS", edge_path, "cannot derive output")
                continue
            output = source_ports[0]
        elif type(output) is not str or output not in source_ports:
            _issue(issues, "DATA_EDGE_OUTPUT_PORT", f"{edge_path}.output", "unregistered port")
            continue
        input_port = edge.get("input")
        if "input" not in edge:
            if len(destination_ports) != 1:
                _issue(issues, "DATA_EDGE_INPUT_AMBIGUOUS", edge_path, "cannot derive input")
                continue
            input_port = destination_ports[0]
        elif type(input_port) is not str or input_port not in destination_ports:
            _issue(issues, "DATA_EDGE_INPUT_PORT", f"{edge_path}.input", "unregistered port")
            continue
        signature = (source, output, input_port)
        if signature in signatures:
            _issue(issues, "DATA_EDGE_DUPLICATE", edge_path, "canonical edge duplicated")
        signatures.add(signature)


def _body(
    body: Any,
    *,
    visible: set[int],
    next_ordinal: list[int],
    operations: dict[int, dict[str, Any]],
    registry: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> set[int]:
    if type(body) is not list or not body:
        _issue(issues, "BODY_SHAPE", path, "expected nonempty exact array")
        return set(visible)
    current_visible = set(visible)
    for index, node in enumerate(body):
        node_path = f"{path}[{index}]"
        if type(node) is not dict:
            _issue(issues, "NODE_TYPE", node_path, "expected exact object")
            continue
        kind = node.get("kind")
        if kind == "operation":
            shape_ok = _closed(
                node, {"kind", "route"}, {"data_from"}, node_path, issues
            )
            route = node.get("route")
            metadata = registry["operations"].get(route) if type(route) is str else None
            if metadata is None:
                _issue(issues, "OPERATION_ROUTE", f"{node_path}.route", "unregistered route")
                metadata = {"input_ports": [], "output_ports": []}
            if shape_ok and "data_from" in node:
                _edge_values(
                    node["data_from"],
                    destination_ports=metadata["input_ports"],
                    visible=current_visible,
                    operations=operations,
                    path=f"{node_path}.data_from",
                    issues=issues,
                )
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            operations[ordinal] = metadata
            current_visible.add(ordinal)
        elif kind == "barrier":
            shape_ok = _closed(
                node,
                {"kind", "barrier", "cases"},
                {"data_from"},
                node_path,
                issues,
            )
            name = node.get("barrier")
            metadata = registry["barriers"].get(name) if type(name) is str else None
            if metadata is None:
                _issue(issues, "BARRIER_KEY", f"{node_path}.barrier", "unregistered barrier")
                metadata = {"input_ports": [], "outcomes": []}
            if shape_ok and "data_from" in node:
                _edge_values(
                    node["data_from"],
                    destination_ports=metadata["input_ports"],
                    visible=current_visible,
                    operations=operations,
                    path=f"{node_path}.data_from",
                    issues=issues,
                )
            cases = node.get("cases")
            if type(cases) is not list or not cases:
                _issue(issues, "BARRIER_CASES", f"{node_path}.cases", "nonempty array required")
                continue
            emitted: list[str] = []
            for case_index, case in enumerate(cases):
                case_path = f"{node_path}.cases[{case_index}]"
                if not _closed(case, {"outcome", "body"}, set(), case_path, issues):
                    continue
                outcome = case.get("outcome")
                if type(outcome) is not str:
                    _issue(issues, "BARRIER_OUTCOME_TYPE", f"{case_path}.outcome", "string required")
                else:
                    emitted.append(outcome)
                _body(
                    case.get("body"),
                    visible=set(current_visible),
                    next_ordinal=next_ordinal,
                    operations=operations,
                    registry=registry,
                    path=f"{case_path}.body",
                    issues=issues,
                )
            if len(emitted) != len(set(emitted)):
                _issue(issues, "BARRIER_OUTCOME_DUPLICATE", f"{node_path}.cases", "duplicate outcome")
            registered = metadata["outcomes"]
            if any(outcome not in registered for outcome in emitted):
                _issue(issues, "BARRIER_OUTCOME", f"{node_path}.cases", "unregistered outcome")
            expected_order = [outcome for outcome in registered if outcome in emitted]
            if emitted != expected_order:
                _issue(issues, "BARRIER_OUTCOME_ORDER", f"{node_path}.cases", "registry order required")
            # Branch-local ordinals are deliberately not added to current_visible.
        else:
            _issue(issues, "NODE_KIND", f"{node_path}.kind", "unknown discriminant")
    return current_visible


def validate_model_document(document: Any, registry: dict[str, Any]) -> ValidationResult:
    """Validate one model-facing document without receiving or reading a query."""
    validate_registry_document(registry)
    issues: list[ValidationIssue] = []
    if type(document) is not dict:
        _issue(issues, "ROOT_TYPE", "$", "expected exact object")
        return ValidationResult(False, tuple(issues))
    kind = document.get("kind")
    if kind == "operation_graph":
        if _closed(document, {"kind", "body"}, set(), "$", issues):
            _body(
                document["body"],
                visible=set(),
                next_ordinal=[0],
                operations={},
                registry=registry,
                path="$.body",
                issues=issues,
            )
    elif kind == "system_control":
        if _closed(document, {"kind", "control"}, {"inputs"}, "$", issues):
            control = document.get("control")
            metadata = registry["system_controls"].get(control) if type(control) is str else None
            if metadata is None:
                _issue(issues, "SYSTEM_CONTROL", "$.control", "unregistered control")
            else:
                allowed = metadata["model_facing_inputs"]
                if allowed:
                    inputs = document.get("inputs")
                    if type(inputs) is not dict or set(inputs) != set(allowed):
                        _issue(issues, "SYSTEM_CONTROL_INPUTS", "$.inputs", "exact registered keys required")
                elif "inputs" in document:
                    _issue(issues, "SYSTEM_CONTROL_INPUTS", "$.inputs", "control declares no model inputs")
    elif kind == "unrepresentable":
        if _closed(document, {"kind", "reason"}, set(), "$", issues):
            reason = document.get("reason")
            if type(reason) is not str or reason not in registry["unrepresentable_reasons"]:
                _issue(issues, "UNREPRESENTABLE_REASON", "$.reason", "unregistered reason")
    else:
        _issue(issues, "ROOT_KIND", "$.kind", "unknown exclusive root")
    return ValidationResult(not issues, tuple(issues))


def _validate_typed_edges(
    edges: tuple[DataEdge, ...],
    *,
    destination_ports: list[str],
    visible: set[int],
    operations: dict[int, dict[str, Any]],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    signatures: set[tuple[int, str, str]] = set()
    for index, edge in enumerate(edges):
        edge_path = f"{path}[{index}]"
        if edge.source_ordinal not in visible:
            _issue(issues, "NORMALIZED_EDGE_DOMINANCE", edge_path, "non-dominating source")
            continue
        source_ports = operations[edge.source_ordinal]["output_ports"]
        if edge.output_port not in source_ports or edge.input_port not in destination_ports:
            _issue(issues, "NORMALIZED_EDGE_PORT", edge_path, "port mismatch")
        signature = (edge.source_ordinal, edge.output_port, edge.input_port)
        if signature in signatures:
            _issue(issues, "NORMALIZED_EDGE_DUPLICATE", edge_path, "duplicate")
        signatures.add(signature)


def _validate_typed_body(
    body: tuple[Operation | BarrierRegion, ...],
    *,
    visible: set[int],
    expected_next: list[int],
    operations: dict[int, dict[str, Any]],
    registry: dict[str, Any],
    path: tuple[str | int, ...],
    issues: list[ValidationIssue],
    allow_empty: bool,
) -> set[int]:
    if not body and not allow_empty:
        _issue(issues, "NORMALIZED_BODY_EMPTY", repr(path), "nonempty body required")
    current = set(visible)
    for index, node in enumerate(body):
        expected_path = path + (index,)
        if node.node_path != expected_path:
            _issue(issues, "NORMALIZED_NODE_PATH", repr(expected_path), "path mismatch")
        if isinstance(node, Operation):
            metadata = registry["operations"].get(node.route)
            if metadata is None:
                _issue(issues, "NORMALIZED_ROUTE", repr(expected_path), "unregistered")
                metadata = {"input_ports": [], "output_ports": []}
            if node.ordinal != expected_next[0]:
                _issue(issues, "NORMALIZED_ORDINAL", repr(expected_path), "nonsequential")
            expected_next[0] += 1
            _validate_typed_edges(
                node.data_from,
                destination_ports=metadata["input_ports"],
                visible=current,
                operations=operations,
                path=repr(expected_path + ("data_from",)),
                issues=issues,
            )
            operations[node.ordinal] = metadata
            current.add(node.ordinal)
        else:
            metadata = registry["barriers"].get(node.barrier)
            if metadata is None:
                _issue(issues, "NORMALIZED_BARRIER", repr(expected_path), "unregistered")
                metadata = {"input_ports": [], "outcomes": []}
            _validate_typed_edges(
                node.data_from,
                destination_ports=metadata["input_ports"],
                visible=current,
                operations=operations,
                path=repr(expected_path + ("data_from",)),
                issues=issues,
            )
            if [case.outcome for case in node.cases] != metadata["outcomes"]:
                _issue(issues, "NORMALIZED_OUTCOMES", repr(expected_path), "not fully materialized")
            if not any(case.body for case in node.cases):
                _issue(issues, "NORMALIZED_BARRIER_EMPTY", repr(expected_path), "all outcomes empty")
            for case in node.cases:
                _validate_typed_body(
                    case.body,
                    visible=set(current),
                    expected_next=expected_next,
                    operations=operations,
                    registry=registry,
                    path=expected_path + ("cases", case.outcome, "body"),
                    issues=issues,
                    allow_empty=True,
                )
    return current


def validate_normalized_document(
    document: RootDocument,
    registry: dict[str, Any],
) -> ValidationResult:
    validate_registry_document(registry)
    issues: list[ValidationIssue] = []
    if isinstance(document, OperationGraph):
        _validate_typed_body(
            document.body,
            visible=set(),
            expected_next=[0],
            operations={},
            registry=registry,
            path=("body",),
            issues=issues,
            allow_empty=False,
        )
    elif isinstance(document, SystemControl):
        metadata = registry["system_controls"].get(document.control)
        if metadata is None:
            _issue(issues, "NORMALIZED_CONTROL", "$", "unregistered")
        elif metadata["model_facing_inputs"]:
            expected = tuple(metadata["model_facing_inputs"])
            actual = tuple(key for key, _ in document.inputs.items) if document.inputs else ()
            if actual != expected:
                _issue(issues, "NORMALIZED_CONTROL_INPUTS", "$", "mismatch")
        elif document.inputs is not None:
            _issue(issues, "NORMALIZED_CONTROL_INPUTS", "$", "unexpected")
    elif isinstance(document, Unrepresentable):
        if document.reason not in registry["unrepresentable_reasons"]:
            _issue(issues, "NORMALIZED_REASON", "$", "unregistered")
    else:
        _issue(issues, "NORMALIZED_ROOT", "$", "unknown typed root")
    return ValidationResult(not issues, tuple(issues))


def continuation_validation_result(valid: bool, code: str = "", message: str = "") -> ValidationResult:
    if valid:
        return ValidationResult(True, ())
    return ValidationResult(False, (ValidationIssue(code, "$continuation", message),))
