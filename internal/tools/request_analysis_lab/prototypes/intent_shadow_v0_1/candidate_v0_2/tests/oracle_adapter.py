"""Test-only conversion from the frozen oracle shape to compact IR.

This module is intentionally below ``tests`` and is never imported by the API,
structured client, prompt builder or compiler.
"""
from __future__ import annotations

from typing import Any


class OracleConversionError(ValueError):
    pass


def _steps(body: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in body:
        if node.get("kind") == "operation":
            converted: dict[str, Any] = {"route": node["route"]}
            if node.get("data_from"):
                converted["from"] = [edge["from"] for edge in node["data_from"]]
            result.append(converted)
            continue
        if node.get("kind") != "barrier":
            raise OracleConversionError("unknown oracle node")
        cases = node.get("cases")
        if type(cases) is not list:
            raise OracleConversionError("barrier cases missing")
        approved = [case for case in cases if case.get("outcome") == "approved"]
        other_nonempty = [
            case for case in cases
            if case.get("outcome") != "approved" and case.get("body")
        ]
        if len(approved) != 1 or other_nonempty:
            raise OracleConversionError("multi-branch semantics are outside candidate 0.2")
        result.append({"barrier": node["barrier"], "body": _steps(approved[0]["body"])})
    return result


def oracle_expected_to_ir(expected: dict[str, Any]) -> dict[str, Any]:
    kind = expected.get("kind")
    if kind == "operation_graph":
        return {"kind": kind, "steps": _steps(expected["body"])}
    if kind == "system_control":
        return {"kind": kind, "control": expected["control"]}
    if kind == "unrepresentable":
        return {"kind": kind, "reason": expected["reason"]}
    raise OracleConversionError("unknown oracle root")
