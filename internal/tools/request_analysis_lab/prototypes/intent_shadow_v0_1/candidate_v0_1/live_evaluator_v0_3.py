#!/usr/bin/env python3
"""Offline evaluator 0.3 for the already sealed paired measurement.

Version 0.3 keeps the historical 0.2 exact-match column, but its headline
semantic metric applies the same representation-only projection to expected
and actual documents.  The projection may remove only empty ``data_from``
arrays, uniquely derivable edge ports, and materialized empty outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import file_sha256, strict_json_file
from live_evaluator import (
    ORACLE_PATH,
    PHASE1_CONTROLS_PATH,
    PHASE1_PATH,
    _actual_document,
    _aggregate_legacy,
    _barriers,
    _gold_map,
    _routes,
    _row as _row_v0_2,
    _verified_gold,
)
from live_protocol import CRITICAL_NO_REGRESSION_COLUMNS, PROTOCOL_FREEZE_PATH, load_protocol
from live_replay_gate_v0_3 import (
    BATCH_PATH,
    EVALUATOR_VERSION,
    FREEZE_PATH,
    ROOT,
    SEAL_PATH,
    validate_pre_gold,
)


HERE = Path(__file__).resolve().parent
REGISTRY_PATH = HERE.parent / "intent_shadow_registry_v0_1.json"
OUTPUT_PATH = HERE / "live_evaluation_v0_3.json"
EFFECTIVE_CRITICAL_COLUMNS = tuple(
    "semantic_exact_canonical" if column == "semantic_exact" else column
    for column in CRITICAL_NO_REGRESSION_COLUMNS
)
PRESERVED_V0_2_COLUMNS = (
    "root_exact",
    "correct_abstention",
    "incorrect_abstention",
    "technical_valid",
    "false_action_avoided",
    "undo_exact",
    "consent_exact",
    "negation_exact",
    "branch_ownership_exact",
)


def _verified_phase1_after_gate() -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind legacy gold to the historical measurement freeze."""
    protocol_freeze = strict_json_file(PROTOCOL_FREEZE_PATH)
    authorities = protocol_freeze.get("repo_authorities")
    if type(authorities) is not dict:
        raise RuntimeError("historical Phase-1 authority map missing")
    for path in (PHASE1_PATH, PHASE1_CONTROLS_PATH):
        relative = str(path.relative_to(ROOT))
        expected = authorities.get(relative)
        if type(expected) is not str or file_sha256(path) != expected:
            raise RuntimeError(f"historical Phase-1 authority drift:{relative}")
    return strict_json_file(PHASE1_PATH), strict_json_file(PHASE1_CONTROLS_PATH)


def _canonical_edge(
    edge: Any,
    *,
    destination_ports: list[str],
    operations: dict[int, dict[str, Any]],
) -> Any:
    if type(edge) is not dict:
        return deepcopy(edge)
    result = deepcopy(edge)
    source = result.get("from")
    source_metadata = operations.get(source) if type(source) is int else None
    source_ports = source_metadata.get("output_ports") if type(source_metadata) is dict else None
    if (
        type(source_ports) is list
        and len(source_ports) == 1
        and result.get("output") == source_ports[0]
    ):
        result.pop("output", None)
    if len(destination_ports) == 1 and result.get("input") == destination_ports[0]:
        result.pop("input", None)
    return result


def _canonical_body(
    body: Any,
    *,
    registry: dict[str, Any],
    operations: dict[int, dict[str, Any]],
    next_ordinal: list[int],
) -> Any:
    if type(body) is not list:
        return deepcopy(body)
    result: list[Any] = []
    for raw_node in body:
        if type(raw_node) is not dict:
            result.append(deepcopy(raw_node))
            continue
        node = deepcopy(raw_node)
        kind = node.get("kind")
        if kind == "operation":
            route = node.get("route")
            metadata = registry["operations"].get(route, {}) if type(route) is str else {}
            destination_ports = metadata.get("input_ports", [])
            if type(destination_ports) is not list:
                destination_ports = []
            edges = node.get("data_from")
            if edges == []:
                node.pop("data_from", None)
            elif type(edges) is list:
                node["data_from"] = [
                    _canonical_edge(
                        edge,
                        destination_ports=destination_ports,
                        operations=operations,
                    )
                    for edge in edges
                ]
            ordinal = next_ordinal[0]
            next_ordinal[0] += 1
            operations[ordinal] = metadata
            result.append(node)
            continue
        if kind == "barrier":
            barrier = node.get("barrier")
            metadata = registry["barriers"].get(barrier, {}) if type(barrier) is str else {}
            destination_ports = metadata.get("input_ports", [])
            if type(destination_ports) is not list:
                destination_ports = []
            edges = node.get("data_from")
            if edges == []:
                node.pop("data_from", None)
            elif type(edges) is list:
                node["data_from"] = [
                    _canonical_edge(
                        edge,
                        destination_ports=destination_ports,
                        operations=operations,
                    )
                    for edge in edges
                ]
            cases = node.get("cases")
            if type(cases) is list:
                canonical_cases: list[Any] = []
                for raw_case in cases:
                    if type(raw_case) is not dict:
                        canonical_cases.append(deepcopy(raw_case))
                        continue
                    case = deepcopy(raw_case)
                    case["body"] = _canonical_body(
                        case.get("body"),
                        registry=registry,
                        operations=operations,
                        next_ordinal=next_ordinal,
                    )
                    if case["body"] != []:
                        canonical_cases.append(case)
                node["cases"] = canonical_cases
            result.append(node)
            continue
        result.append(node)
    return result


def canonical_semantic_projection(document: Any, registry: dict[str, Any]) -> Any:
    """Apply only the approved representation projection, symmetrically."""
    if type(document) is not dict:
        return deepcopy(document)
    result = deepcopy(document)
    if result.get("kind") == "operation_graph":
        result["body"] = _canonical_body(
            result.get("body"),
            registry=registry,
            operations={},
            next_ordinal=[0],
        )
    return result


def _decoded_document(record: dict[str, Any]) -> dict[str, Any] | None:
    extraction = record.get("extraction")
    document = extraction.get("decoded_document") if type(extraction) is dict else None
    return document if type(document) is dict else None


def _composition_node(node: Any) -> Any:
    if type(node) is not dict:
        return deepcopy(node)
    if node.get("kind") == "operation":
        return {"kind": "operation"}
    if node.get("kind") == "barrier":
        cases = node.get("cases")
        return {
            "kind": "barrier",
            "barrier": node.get("barrier"),
            "cases": [
                {
                    "outcome": case.get("outcome"),
                    "body": [_composition_node(child) for child in case.get("body", [])],
                }
                for case in cases
                if type(case) is dict and type(case.get("body")) is list
            ] if type(cases) is list else deepcopy(cases),
        }
    return deepcopy(node)


def _composition_signature(document: Any) -> Any:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return None
    body = document.get("body")
    if type(body) is not list:
        return None
    return [_composition_node(node) for node in body]


def _data_edge_node(node: Any) -> Any:
    if type(node) is not dict:
        return deepcopy(node)
    result: dict[str, Any] = {
        "kind": node.get("kind"),
        "data_from": deepcopy(node.get("data_from", [])),
    }
    if node.get("kind") == "barrier":
        cases = node.get("cases")
        result["cases"] = [
            {
                "outcome": case.get("outcome"),
                "body": [_data_edge_node(child) for child in case.get("body", [])],
            }
            for case in cases
            if type(case) is dict and type(case.get("body")) is list
        ] if type(cases) is list else deepcopy(cases)
    return result


def _data_edge_signature(document: Any) -> Any:
    if type(document) is not dict or document.get("kind") != "operation_graph":
        return None
    body = document.get("body")
    if type(body) is not list:
        return None
    return [_data_edge_node(node) for node in body]


def _row(record: dict[str, Any], expected: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    historical = _row_v0_2(record, expected)
    raw_exact = historical.pop("semantic_exact")
    _status, actual = _actual_document(record)
    decoded = _decoded_document(record)
    expected_canonical = canonical_semantic_projection(expected, registry)
    actual_canonical = canonical_semantic_projection(actual, registry)
    expected_kind = expected.get("kind")
    expected_operation_graph = expected_kind == "operation_graph"
    expected_barrier = bool(_barriers(expected))
    expected_control = expected_kind == "system_control"
    row = {
        **historical,
        "semantic_exact_canonical": actual_canonical == expected_canonical,
        "semantic_exact_v0_2_raw": raw_exact,
        "model_raw_root_exact": (
            decoded.get("kind") == expected_kind if decoded is not None else False
        ),
        "accepted_root_exact": historical["root_exact"],
        "route_applicable": expected_operation_graph,
        "route_exact": (
            _routes(actual_canonical) == _routes(expected_canonical)
            if expected_operation_graph else None
        ),
        "composition_applicable": expected_operation_graph,
        "composition_exact": (
            _composition_signature(actual_canonical) == _composition_signature(expected_canonical)
            if expected_operation_graph else None
        ),
        "data_edge_applicable": expected_operation_graph,
        "data_edge_exact": (
            _data_edge_signature(actual_canonical) == _data_edge_signature(expected_canonical)
            if expected_operation_graph else None
        ),
        "consent_applicable": expected_barrier,
        "branch_ownership_applicable": expected_barrier,
        "system_control_applicable": expected_control,
        "system_control_exact": (
            actual == expected if expected_control else None
        ),
    }
    return row


def _applicable_metric(
    rows: list[dict[str, Any]],
    *,
    value: str,
    applicable: str,
    global_default: bool,
) -> dict[str, int]:
    applicable_rows = [row for row in rows if row[applicable] is True]
    for row in applicable_rows:
        if type(row[value]) is not bool:
            raise RuntimeError(f"applicable metric type mismatch:{value}")
    return {
        "passed_applicable": sum(int(row[value]) for row in applicable_rows),
        "applicable": len(applicable_rows),
        "passed_global": sum(
            int(row[value] if row[applicable] is True else global_default)
            for row in rows
        ),
        "global_total": len(rows),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count_columns = (
        "semantic_exact_canonical",
        "semantic_exact_v0_2_raw",
        *PRESERVED_V0_2_COLUMNS,
        "model_raw_root_exact",
        "accepted_root_exact",
    )
    for row in rows:
        for column in count_columns:
            if type(row.get(column)) is not bool:
                raise RuntimeError(f"row metric type mismatch:{column}")
    return {
        "total": len(rows),
        **{
            column: sum(int(row[column]) for row in rows)
            for column in count_columns
        },
        "extraction_status_counts": dict(sorted(Counter(row["extraction_status"] for row in rows).items())),
        "applicable_metrics": {
            "route_exact": _applicable_metric(
                rows, value="route_exact", applicable="route_applicable", global_default=True
            ),
            "composition_exact": _applicable_metric(
                rows, value="composition_exact", applicable="composition_applicable", global_default=True
            ),
            "data_edge_exact": _applicable_metric(
                rows, value="data_edge_exact", applicable="data_edge_applicable", global_default=True
            ),
            "consent_exact": _applicable_metric(
                rows, value="consent_exact", applicable="consent_applicable", global_default=True
            ),
            "branch_ownership_exact": _applicable_metric(
                rows,
                value="branch_ownership_exact",
                applicable="branch_ownership_applicable",
                global_default=True,
            ),
            "system_control_exact": _applicable_metric(
                rows,
                value="system_control_exact",
                applicable="system_control_applicable",
                global_default=True,
            ),
        },
    }


def typed_verdict_v0_3(
    canonical_a: dict[str, Any],
    canonical_b: dict[str, Any],
    typed_b: dict[str, Any],
    protocol_columns: list[str],
) -> dict[str, Any]:
    """Apply the frozen hierarchy using the corrected semantic headline."""
    if (
        type(protocol_columns) is not list
        or any(type(column) is not str for column in protocol_columns)
        or len(protocol_columns) != len(set(protocol_columns))
        or set(protocol_columns) != set(CRITICAL_NO_REGRESSION_COLUMNS)
        or len(EFFECTIVE_CRITICAL_COLUMNS) != 9
    ):
        raise RuntimeError("critical no-regression column set mismatch")
    for label, aggregate in (("canonical_A", canonical_a), ("canonical_B", canonical_b)):
        if type(aggregate) is not dict:
            raise RuntimeError(f"{label} aggregate type mismatch")
        for column in EFFECTIVE_CRITICAL_COLUMNS:
            if type(aggregate.get(column)) is not int:
                raise RuntimeError(f"{label} aggregate column type mismatch:{column}")
    if type(typed_b) is not dict or type(typed_b.get("semantic_exact_canonical")) is not int:
        raise RuntimeError("typed_B aggregate mismatch")

    delta = canonical_b["semantic_exact_canonical"] - canonical_a["semantic_exact_canonical"]
    regressions = [
        column
        for column in EFFECTIVE_CRITICAL_COLUMNS
        if canonical_b[column] < canonical_a[column]
    ]
    special_pass = typed_b["semantic_exact_canonical"] == 4
    if delta >= 3 and special_pass and not regressions:
        verdict = "candidate_pass"
    elif -2 <= delta <= 2:
        verdict = "inconclusive"
    else:
        verdict = "candidate_fail"
    return {
        "canonical_delta_B_minus_A": delta,
        "protocol_critical_columns_v0_1": list(CRITICAL_NO_REGRESSION_COLUMNS),
        "effective_critical_columns_v0_3": list(EFFECTIVE_CRITICAL_COLUMNS),
        "critical_regressions": regressions,
        "typed_special_4_of_4": special_pass,
        "verdict": verdict,
    }


def evaluate(
    batch_path: Path = BATCH_PATH,
    seal_path: Path = SEAL_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict[str, Any]:
    batch, _manifest, pre_gold = validate_pre_gold(batch_path, seal_path, freeze_path)

    # Gold boundary: failed freeze/replay validation raises before this line.
    oracle = _verified_gold()
    gold = _gold_map(oracle)
    registry = strict_json_file(REGISTRY_PATH)
    rows: list[dict[str, Any]] = []
    legacy_records: list[dict[str, Any]] = []
    for record in batch["records"]:
        if record["panel"] == "legacy_phase1_34":
            legacy_records.append(record)
            continue
        expected = gold[(record["panel"], record["opaque_case_id"])]
        rows.append(_row(record, expected, registry))

    grouped: dict[str, dict[str, Any]] = {}
    for panel in ("canonical_120", "typed_controls_4"):
        for arm in ("A", "B"):
            grouped[f"{panel}.{arm}"] = _aggregate(
                [row for row in rows if row["panel"] == panel and row["arm"] == arm]
            )
    protocol = load_protocol()
    decision = typed_verdict_v0_3(
        grouped["canonical_120.A"],
        grouped["canonical_120.B"],
        grouped["typed_controls_4.B"],
        protocol["evaluation"]["critical_columns_no_regression"],
    )

    phase1, controls = _verified_phase1_after_gate()
    expected_binding = {case["id"]: case["expect_binding"] for case in controls["cases"]}
    legacy_rows: list[dict[str, Any]] = []
    for record in legacy_records:
        status, actual = _actual_document(record)
        direct = actual == {
            "kind": "operation_graph",
            "body": [{"kind": "operation", "route": "get/location", "data_from": []}],
        }
        legacy_rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "arm": record["arm"],
            "query_sha256": record["query_sha256"],
            "expected_direct_binding": expected_binding[record["opaque_case_id"]],
            "actual_direct_binding": direct,
            "direct_binding_exact": direct is expected_binding[record["opaque_case_id"]],
            "extraction_status": status,
        })
    legacy_grouped = {
        arm: _aggregate_legacy([row for row in legacy_rows if row["arm"] == arm])
        for arm in ("A", "B")
    }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "status": "ok",
        "pre_gold_replay_gate": pre_gold,
        "gold_opened_after_v0_3_gate": True,
        "evaluation_executed": True,
        "batch_sha256": file_sha256(batch_path),
        "oracle_sha256": file_sha256(ORACLE_PATH),
        "semantic_headline": "semantic_exact_canonical",
        "historical_semantic_column": "semantic_exact_v0_2_raw",
        "canonicalization_policy": {
            "symmetric_expected_and_actual": True,
            "may_omit_empty_data_from": True,
            "may_omit_uniquely_derivable_ports": True,
            "may_omit_materialized_empty_outcomes": True,
            "may_change_root_route_order_edge_or_reason": False,
        },
        "typed_panels": grouped,
        **decision,
        "rows": rows,
        "legacy_phase1": {
            "authority_version": phase1["version"],
            "separate": True,
            "automatic_conversion": False,
            "cross_panel_compensation": False,
            "arms": legacy_grouped,
            "rows": legacy_rows,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, default=BATCH_PATH)
    parser.add_argument("--seal", type=Path, default=SEAL_PATH)
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        print(json.dumps({
            "evaluator_version": EVALUATOR_VERSION,
            "status": "error",
            "evaluation_executed": False,
            "error": "output already exists",
        }, ensure_ascii=False, sort_keys=True))
        return 1
    try:
        report = evaluate(args.batch, args.seal, args.freeze)
    except Exception as exc:
        print(json.dumps({
            "evaluator_version": EVALUATOR_VERSION,
            "status": "error",
            "evaluation_executed": False,
            "error": f"{type(exc).__name__}:{exc}",
        }, ensure_ascii=False, sort_keys=True))
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
