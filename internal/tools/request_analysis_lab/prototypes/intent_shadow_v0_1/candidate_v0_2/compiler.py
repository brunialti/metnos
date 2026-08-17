"""Deterministic compiler from compact IR to an immutable typed graph."""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from .canonical import canonical_json_bytes
from .registry_projection import CONTRACT_VERSION, projection_sha256, validate_projection
from .types import (
    CompilationResult,
    CompiledBarrier,
    CompiledDocument,
    CompiledOperation,
    CompiledOperationGraph,
    CompiledOutcome,
    CompiledStep,
    CompiledSystemControl,
    CompiledUnrepresentable,
    Continuation,
    DataEdge,
    IRBarrier,
    IRDocument,
    IROperation,
    IROperationGraph,
    IRStep,
    IRSystemControl,
    IRUnrepresentable,
    Issue,
)
from .validator import validate_ir


def _edge_json(edge: DataEdge, *, semantic: bool) -> dict[str, Any]:
    if semantic:
        return {"from": edge.source_ordinal}
    return {
        "from": edge.source_ordinal,
        "output": edge.output_port,
        "input": edge.input_port,
    }


def compiled_step_json(step: CompiledStep, *, semantic: bool = False) -> dict[str, Any]:
    if isinstance(step, CompiledOperation):
        result: dict[str, Any] = {"kind": "operation"}
        if not semantic:
            result["node_path"] = list(step.node_path)
            result["ordinal"] = step.ordinal
        result["route"] = step.route
        if step.data_from:
            result["data_from"] = [_edge_json(edge, semantic=semantic) for edge in step.data_from]
        return result
    result = {"kind": "barrier"}
    if not semantic:
        result["node_path"] = list(step.node_path)
    result["barrier"] = step.barrier
    cases = []
    for outcome in step.outcomes:
        if semantic and not outcome.body:
            continue
        cases.append(
            {
                "outcome": outcome.outcome,
                "body": [compiled_step_json(item, semantic=semantic) for item in outcome.body],
            }
        )
    result["cases"] = cases
    return result


def compiled_document_json(document: CompiledDocument, *, semantic: bool = False) -> dict[str, Any]:
    if isinstance(document, CompiledOperationGraph):
        return {
            "kind": "operation_graph",
            "body": [compiled_step_json(step, semantic=semantic) for step in document.steps],
        }
    if isinstance(document, CompiledSystemControl):
        return {"kind": "system_control", "control": document.control}
    if isinstance(document, CompiledUnrepresentable):
        return {"kind": "unrepresentable", "reason": document.reason}
    raise TypeError("unknown compiled document")


def _compile_steps(
    steps: tuple[IRStep, ...],
    *,
    registry: dict[str, Any],
    visible: set[int],
    operations: dict[int, dict[str, Any]],
    next_ordinal: list[int],
    path: tuple[str | int, ...],
    issues: list[Issue],
) -> tuple[CompiledStep, ...]:
    result: list[CompiledStep] = []
    current_visible = set(visible)
    for index, step in enumerate(steps):
        node_path = path + (index,)
        if isinstance(step, IROperation):
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            metadata = registry["operations"][step.route]
            edges: list[DataEdge] = []
            if step.sources:
                input_ports = metadata["input_ports"]
                if len(input_ports) != 1:
                    issues.append(Issue("INPUT_PORT_AMBIGUOUS", repr(node_path), "cannot derive unique input port"))
                for source in step.sources:
                    if source not in current_visible:
                        code = "FROM_SELF_OR_FORWARD" if source >= ordinal else "FROM_OUT_OF_SCOPE"
                        issues.append(Issue(code, repr(node_path + ("from",)), "source is not a dominating prior operation"))
                        continue
                    source_ports = operations[source]["output_ports"]
                    if len(source_ports) != 1:
                        issues.append(Issue("OUTPUT_PORT_AMBIGUOUS", repr(node_path), "cannot derive unique output port"))
                        continue
                    if len(input_ports) == 1:
                        edges.append(DataEdge(source, source_ports[0], input_ports[0]))
            compiled = CompiledOperation(node_path, ordinal, step.route, tuple(edges))
            operations[ordinal] = metadata
            current_visible.add(ordinal)
            result.append(compiled)
            continue
        metadata = registry["barriers"][step.barrier]
        body_outcome = metadata["body_outcome"]
        branch = _compile_steps(
            step.body,
            registry=registry,
            visible=set(current_visible),
            operations=operations,
            next_ordinal=next_ordinal,
            path=node_path + ("outcomes", body_outcome, "body"),
            issues=issues,
        )
        outcomes = [CompiledOutcome(body_outcome, branch)]
        outcomes.extend(
            CompiledOutcome(name, ()) for name in metadata["empty_outcomes"]
        )
        result.append(CompiledBarrier(node_path, step.barrier, tuple(outcomes)))
        # Operations owned by the approved branch do not become visible outside.
    return tuple(result)


def _compile_typed(document: IRDocument, registry: dict[str, Any], issues: list[Issue]) -> CompiledDocument:
    if isinstance(document, IROperationGraph):
        return CompiledOperationGraph(
            _compile_steps(
                document.steps,
                registry=registry,
                visible=set(),
                operations={},
                next_ordinal=[0],
                path=("steps",),
                issues=issues,
            )
        )
    if isinstance(document, IRSystemControl):
        return CompiledSystemControl(document.control)
    if isinstance(document, IRUnrepresentable):
        return CompiledUnrepresentable(document.reason)
    raise TypeError("unknown IR document")


def _continuation_payload(
    *,
    registry_sha256: str,
    root_sha256: str,
    barrier_path: tuple[str | int, ...],
    outcome: str,
    body: tuple[CompiledStep, ...],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "registry_sha256": registry_sha256,
        "root_document_sha256": root_sha256,
        "barrier_path": list(barrier_path),
        "outcome": outcome,
        "body": [compiled_step_json(item) for item in body],
    }


def _collect_continuations(
    steps: tuple[CompiledStep, ...],
    *,
    registry_sha256: str,
    root_sha256: str,
    target: list[Continuation],
) -> None:
    for step in steps:
        if not isinstance(step, CompiledBarrier):
            continue
        for outcome in step.outcomes:
            if outcome.body:
                payload = _continuation_payload(
                    registry_sha256=registry_sha256,
                    root_sha256=root_sha256,
                    barrier_path=step.node_path,
                    outcome=outcome.outcome,
                    body=outcome.body,
                )
                target.append(
                    Continuation(
                        CONTRACT_VERSION,
                        registry_sha256,
                        root_sha256,
                        step.node_path,
                        outcome.outcome,
                        outcome.body,
                        sha256(canonical_json_bytes(payload)).hexdigest(),
                    )
                )
            _collect_continuations(
                outcome.body,
                registry_sha256=registry_sha256,
                root_sha256=root_sha256,
                target=target,
            )


def compile_document(value: Any, registry: dict[str, Any]) -> CompilationResult:
    validate_projection(registry)
    validation = validate_ir(value, registry)
    if not validation.valid or validation.document is None:
        return CompilationResult(False, None, None, (), validation.issues)
    issues: list[Issue] = []
    compiled = _compile_typed(validation.document, registry, issues)
    if issues:
        return CompilationResult(False, None, None, (), tuple(issues))
    document_sha = sha256(canonical_json_bytes(compiled_document_json(compiled))).hexdigest()
    continuations: list[Continuation] = []
    if isinstance(compiled, CompiledOperationGraph):
        _collect_continuations(
            compiled.steps,
            registry_sha256=projection_sha256(registry),
            root_sha256=document_sha,
            target=continuations,
        )
    return CompilationResult(True, compiled, document_sha, tuple(continuations), ())


def semantic_projection(result: CompilationResult) -> dict[str, Any]:
    if not result.valid or result.document is None:
        raise ValueError("cannot project an invalid compilation")
    return compiled_document_json(result.document, semantic=True)


def continuation_json(continuation: Continuation) -> dict[str, Any]:
    payload = _continuation_payload(
        registry_sha256=continuation.registry_sha256,
        root_sha256=continuation.root_document_sha256,
        barrier_path=continuation.barrier_path,
        outcome=continuation.outcome,
        body=continuation.body,
    )
    payload["continuation_sha256"] = continuation.continuation_sha256
    return payload


def verify_continuation(
    continuation: Continuation,
    result: CompilationResult,
    registry: dict[str, Any],
) -> bool:
    if not result.valid or result.document is None or result.document_sha256 is None:
        return False
    if continuation.contract_version != CONTRACT_VERSION:
        return False
    if continuation.registry_sha256 != projection_sha256(registry):
        return False
    if continuation.root_document_sha256 != result.document_sha256:
        return False
    payload = _continuation_payload(
        registry_sha256=continuation.registry_sha256,
        root_sha256=continuation.root_document_sha256,
        barrier_path=continuation.barrier_path,
        outcome=continuation.outcome,
        body=continuation.body,
    )
    if continuation.continuation_sha256 != sha256(canonical_json_bytes(payload)).hexdigest():
        return False
    return continuation in result.continuations
