"""Non-semantic normalization and immutable continuation construction."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from intent_shadow_io import canonical_json_bytes, freeze_json, thaw_json
from intent_shadow_registry import CONTRACT_VERSION, registry_payload_sha256
from intent_shadow_types import (
    BarrierRegion,
    Continuation,
    DataEdge,
    FrozenObject,
    Node,
    NormalizationResult,
    Operation,
    OperationGraph,
    OutcomeCase,
    ProjectionResult,
    RootDocument,
    SystemControl,
    Unrepresentable,
    ValidationIssue,
    ValidationResult,
)
from intent_shadow_validate import validate_model_document, validate_normalized_document


class NormalizationError(ValueError):
    pass


def _edges(
    raw_edges: list[dict[str, Any]],
    *,
    destination_ports: list[str],
    operations: dict[int, dict[str, Any]],
) -> tuple[DataEdge, ...]:
    result: list[DataEdge] = []
    for edge in raw_edges:
        source = edge["from"]
        source_ports = operations[source]["output_ports"]
        output = edge.get("output", source_ports[0])
        input_port = edge.get("input", destination_ports[0])
        result.append(DataEdge(source, output, input_port))
    return tuple(result)


def _body(
    raw_body: list[dict[str, Any]],
    *,
    registry: dict[str, Any],
    operations: dict[int, dict[str, Any]],
    next_ordinal: list[int],
    path: tuple[str | int, ...],
) -> tuple[Node, ...]:
    result: list[Node] = []
    for index, raw_node in enumerate(raw_body):
        node_path = path + (index,)
        if raw_node["kind"] == "operation":
            metadata = registry["operations"][raw_node["route"]]
            edges = _edges(
                raw_node.get("data_from", []),
                destination_ports=metadata["input_ports"],
                operations=operations,
            )
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            operation = Operation(node_path, ordinal, raw_node["route"], edges)
            operations[ordinal] = metadata
            result.append(operation)
            continue

        metadata = registry["barriers"][raw_node["barrier"]]
        barrier_edges = _edges(
            raw_node.get("data_from", []),
            destination_ports=metadata["input_ports"],
            operations=operations,
        )
        emitted = {case["outcome"]: case["body"] for case in raw_node["cases"]}
        cases: list[OutcomeCase] = []
        for outcome in metadata["outcomes"]:
            case_body = _body(
                emitted[outcome],
                registry=registry,
                operations=operations,
                next_ordinal=next_ordinal,
                path=node_path + ("cases", outcome, "body"),
            ) if outcome in emitted else ()
            cases.append(OutcomeCase(outcome, case_body))
        result.append(
            BarrierRegion(node_path, raw_node["barrier"], barrier_edges, tuple(cases))
        )
    return tuple(result)


def normalized_edge_json(edge: DataEdge) -> dict[str, Any]:
    return {
        "from": edge.source_ordinal,
        "output": edge.output_port,
        "input": edge.input_port,
    }


def normalized_node_json(node: Node) -> dict[str, Any]:
    if isinstance(node, Operation):
        return {
            "kind": "operation",
            "node_path": list(node.node_path),
            "ordinal": node.ordinal,
            "route": node.route,
            "data_from": [normalized_edge_json(edge) for edge in node.data_from],
        }
    return {
        "kind": "barrier",
        "node_path": list(node.node_path),
        "barrier": node.barrier,
        "data_from": [normalized_edge_json(edge) for edge in node.data_from],
        "cases": [
            {
                "outcome": case.outcome,
                "body": [normalized_node_json(item) for item in case.body],
            }
            for case in node.cases
        ],
    }


def normalized_document_json(document: RootDocument) -> dict[str, Any]:
    if isinstance(document, OperationGraph):
        return {
            "kind": "operation_graph",
            "body": [normalized_node_json(node) for node in document.body],
        }
    if isinstance(document, SystemControl):
        result: dict[str, Any] = {"kind": "system_control", "control": document.control}
        if document.inputs is not None:
            result["inputs"] = thaw_json(document.inputs)
        return result
    if isinstance(document, Unrepresentable):
        return {"kind": "unrepresentable", "reason": document.reason}
    raise TypeError("unknown root document")


def semantic_node_json(node: Node) -> dict[str, Any]:
    if isinstance(node, Operation):
        return {
            "kind": "operation",
            "route": node.route,
            "data_from": [normalized_edge_json(edge) for edge in node.data_from],
        }
    return {
        "kind": "barrier",
        "barrier": node.barrier,
        "data_from": [normalized_edge_json(edge) for edge in node.data_from],
        "cases": [
            {
                "outcome": case.outcome,
                "body": [semantic_node_json(item) for item in case.body],
            }
            for case in node.cases
        ],
    }


def semantic_document_json(document: RootDocument) -> dict[str, Any]:
    if isinstance(document, OperationGraph):
        return {
            "kind": "operation_graph",
            "body": [semantic_node_json(node) for node in document.body],
        }
    return normalized_document_json(document)


def _continuation_payload(
    *,
    registry_sha256: str,
    root_document_sha256: str,
    barrier_path: tuple[str | int, ...],
    outcome_ref: str,
    typed_body: tuple[Node, ...],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "registry_sha256": registry_sha256,
        "root_document_sha256": root_document_sha256,
        "barrier_path": list(barrier_path),
        "outcome_ref": outcome_ref,
        "typed_body": [normalized_node_json(node) for node in typed_body],
    }


def continuation_json(continuation: Continuation) -> dict[str, Any]:
    payload = _continuation_payload(
        registry_sha256=continuation.registry_sha256,
        root_document_sha256=continuation.root_document_sha256,
        barrier_path=continuation.barrier_path,
        outcome_ref=continuation.outcome_ref,
        typed_body=continuation.typed_body,
    )
    payload["continuation_sha256"] = continuation.continuation_sha256
    return payload


def _collect_continuations(
    body: tuple[Node, ...],
    *,
    registry_sha256: str,
    root_document_sha256: str,
    target: list[Continuation],
) -> None:
    for node in body:
        if not isinstance(node, BarrierRegion):
            continue
        for case in node.cases:
            if case.body:
                payload = _continuation_payload(
                    registry_sha256=registry_sha256,
                    root_document_sha256=root_document_sha256,
                    barrier_path=node.node_path,
                    outcome_ref=case.outcome,
                    typed_body=case.body,
                )
                target.append(
                    Continuation(
                        contract_version=CONTRACT_VERSION,
                        registry_sha256=registry_sha256,
                        root_document_sha256=root_document_sha256,
                        barrier_path=node.node_path,
                        outcome_ref=case.outcome,
                        typed_body=case.body,
                        continuation_sha256=sha256(canonical_json_bytes(payload)).hexdigest(),
                    )
                )
            _collect_continuations(
                case.body,
                registry_sha256=registry_sha256,
                root_document_sha256=root_document_sha256,
                target=target,
            )


def normalize_document(document: dict[str, Any], registry: dict[str, Any]) -> NormalizationResult:
    validation = validate_model_document(document, registry)
    if not validation.valid:
        codes = ",".join(issue.code for issue in validation.issues)
        raise NormalizationError(f"model document is invalid: {codes}")
    kind = document["kind"]
    if kind == "operation_graph":
        root: RootDocument = OperationGraph(
            _body(
                document["body"],
                registry=registry,
                operations={},
                next_ordinal=[0],
                path=("body",),
            )
        )
    elif kind == "system_control":
        frozen_inputs = freeze_json(document["inputs"]) if "inputs" in document else None
        if frozen_inputs is not None and not isinstance(frozen_inputs, FrozenObject):
            raise NormalizationError("system-control inputs are not an object")
        root = SystemControl(document["control"], frozen_inputs)
    else:
        root = Unrepresentable(document["reason"])
    normalized_validation = validate_normalized_document(root, registry)
    if not normalized_validation.valid:
        codes = ",".join(issue.code for issue in normalized_validation.issues)
        raise NormalizationError(f"normalizer violated typed invariants: {codes}")
    normalized_bytes = canonical_json_bytes(normalized_document_json(root))
    root_sha = sha256(normalized_bytes).hexdigest()
    continuations: list[Continuation] = []
    if isinstance(root, OperationGraph):
        _collect_continuations(
            root.body,
            registry_sha256=registry_payload_sha256(registry),
            root_document_sha256=root_sha,
            target=continuations,
        )
    return NormalizationResult(root, normalized_bytes, root_sha, tuple(continuations))


def project_normalized(result: NormalizationResult) -> ProjectionResult:
    semantic_bytes = canonical_json_bytes(semantic_document_json(result.document))
    return ProjectionResult(
        document=result.document,
        semantic_bytes=semantic_bytes,
        semantic_sha256=sha256(semantic_bytes).hexdigest(),
    )


def _barriers(body: tuple[Node, ...]) -> list[BarrierRegion]:
    result: list[BarrierRegion] = []
    for node in body:
        if isinstance(node, BarrierRegion):
            result.append(node)
            for case in node.cases:
                result.extend(_barriers(case.body))
    return result


def verify_continuation(
    continuation: Continuation,
    root: RootDocument,
    registry_sha256: str,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    root_bytes = canonical_json_bytes(normalized_document_json(root))
    root_sha = sha256(root_bytes).hexdigest()
    if continuation.contract_version != CONTRACT_VERSION:
        issues.append(ValidationIssue("CONTINUATION_CONTRACT", "$continuation", "version mismatch"))
    if continuation.registry_sha256 != registry_sha256:
        issues.append(ValidationIssue("CONTINUATION_REGISTRY", "$continuation", "registry mismatch"))
    if continuation.root_document_sha256 != root_sha:
        issues.append(ValidationIssue("CONTINUATION_ROOT", "$continuation", "root hash mismatch"))
    matching: list[BarrierRegion] = []
    if isinstance(root, OperationGraph):
        matching = [node for node in _barriers(root.body) if node.node_path == continuation.barrier_path]
    if len(matching) != 1:
        issues.append(ValidationIssue("CONTINUATION_PATH", "$continuation", "barrier path mismatch"))
    else:
        cases = [case for case in matching[0].cases if case.outcome == continuation.outcome_ref]
        if len(cases) != 1:
            issues.append(ValidationIssue("CONTINUATION_OUTCOME", "$continuation", "unknown outcome"))
        elif cases[0].body != continuation.typed_body or not continuation.typed_body:
            issues.append(ValidationIssue("CONTINUATION_BODY", "$continuation", "body mismatch or empty"))
    payload = _continuation_payload(
        registry_sha256=continuation.registry_sha256,
        root_document_sha256=continuation.root_document_sha256,
        barrier_path=continuation.barrier_path,
        outcome_ref=continuation.outcome_ref,
        typed_body=continuation.typed_body,
    )
    expected_hash = sha256(canonical_json_bytes(payload)).hexdigest()
    if continuation.continuation_sha256 != expected_hash:
        issues.append(ValidationIssue("CONTINUATION_SHA256", "$continuation", "hash mismatch"))
    return ValidationResult(not issues, tuple(issues))

