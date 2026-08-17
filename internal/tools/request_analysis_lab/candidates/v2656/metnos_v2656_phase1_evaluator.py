#!/usr/bin/env python3
"""Pure post-run Phase-1 oracle evaluator for V26.5.6.

No prompt, query, transport, model client, source execution, or file I/O lives
here.  The runner loads this hash-pinned module only after a complete batch.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


__all__ = ("evaluate_phase1",)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _dependency_ref(atom: dict[str, Any], output_index: int) -> str:
    if atom["relation"] == "spatial.located_at" and output_index == 2:
        arguments = atom["arguments"]
        if (
            arguments[0].get("kind") == "bound"
            and arguments[0].get("ref") == "actor.current"
            and arguments[1].get("kind") in {"output", "unknown"}
            and arguments[2].get("kind") == "bound"
            and arguments[2].get("ref") == "time.current"
        ):
            return "actor.current_position"
    return f"atom_output.{atom['relation']}.{output_index}"


def _canonical_projection(
    atom: dict[str, Any], atom_by_id: dict[int, dict[str, Any]],
    relations: dict[str, Any],
) -> dict[str, Any]:
    slots = relations[atom["relation"]]["slots"]
    arguments = []
    for slot, binding in zip(slots, atom["arguments"]):
        item: dict[str, Any] = {
            "role": slot["role"], "value_type": slot["value_type"],
        }
        if binding["kind"] == "bound":
            item.update({"state": "bound", "ref": binding["ref"]})
        elif binding["kind"] == "unknown":
            item["state"] = "unknown"
        elif binding["kind"] == "from_atom_output":
            source = atom_by_id[binding["atom_id"]]
            item.update({
                "state": "derived",
                "ref": _dependency_ref(source, binding["output_index"]),
            })
        else:
            continue
        arguments.append(item)
    return {
        "clause_role": atom["clause_role"],
        "speech_act": atom["speech_act"],
        "relation": atom["relation"],
        "arguments": arguments,
    }


def _canonical_analysis(
    atoms: list[dict[str, Any]], relations: dict[str, Any],
) -> list[dict[str, Any]]:
    atom_by_id = {atom["atom_id"]: atom for atom in atoms}
    return [
        _canonical_projection(atom, atom_by_id, relations)
        for atom in atoms if atom["atom_kind"] == "projection"
    ]


def _canonical_candidate(
    frame: dict[str, Any], relations: dict[str, Any],
) -> dict[str, Any]:
    if frame["status"] == "supported":
        return {
            "status": "supported",
            "projections": _canonical_analysis(frame["atoms"], relations),
        }
    if frame["status"] == "typed_ambiguity":
        alternatives = [
            _canonical_analysis(item["atoms"], relations)
            for item in frame["alternatives"]
        ]
        return {
            "status": "typed_ambiguity",
            "alternatives": sorted(alternatives, key=_canonical_hash),
        }
    return {"status": "unsupported"}


def _direct_projection(projection: dict[str, Any]) -> bool:
    if (
        projection["clause_role"] != "main_request"
        or projection["speech_act"] != "open_question"
        or projection["relation"] != "spatial.located_at"
        or len(projection["arguments"]) != 3
    ):
        return False
    entity, position, time_argument = projection["arguments"]
    return (
        entity.get("state") == "bound"
        and entity.get("ref") == "actor.current"
        and position.get("state") == "unknown"
        and time_argument.get("state") == "bound"
        and time_argument.get("ref") == "time.current"
    )


def _direct_current_location(
    frame: dict[str, Any], relations: dict[str, Any],
) -> bool:
    if frame.get("status") != "supported":
        return False
    return any(
        _direct_projection(item)
        for item in _canonical_analysis(frame["atoms"], relations)
    )


def _analysis_dependency_state(
    projections: list[dict[str, Any]],
) -> tuple[bool, bool]:
    derived = any(
        argument.get("state") == "derived"
        and argument.get("ref") == "actor.current_position"
        for projection in projections
        if projection["clause_role"] == "main_request"
        for argument in projection["arguments"]
    )
    context = any(
        argument.get("state") == "bound"
        and argument.get("ref") in {
            "context.current_spatial", "context.current_place",
        }
        for projection in projections
        if projection["clause_role"] == "main_request"
        for argument in projection["arguments"]
    )
    return derived, context


def _dependency_current_location(
    frame: dict[str, Any], relations: dict[str, Any],
) -> str:
    canonical = _canonical_candidate(frame, relations)
    if canonical["status"] == "supported":
        projections = canonical["projections"]
        derived, context = _analysis_dependency_state(projections)
        if derived:
            return "required"
        if context:
            return "context_resolution_required"
        if any(_direct_projection(item) for item in projections):
            return "not_applicable"
        return "forbidden"
    if canonical["status"] == "typed_ambiguity":
        states = [
            _analysis_dependency_state(item)
            for item in canonical["alternatives"]
        ]
        derived_values = [item[0] for item in states]
        if all(derived_values):
            return "required"
        if any(derived_values):
            return "clarification_required"
        if any(item[1] for item in states):
            return "context_resolution_required"
    return "forbidden"


def _safety_obligations(
    frame: dict[str, Any], relations: dict[str, Any],
) -> list[str]:
    canonical = _canonical_candidate(frame, relations)
    analyses = (
        [canonical["projections"]]
        if canonical["status"] == "supported"
        else canonical.get("alternatives", [])
    )
    obligations: set[str] = set()
    for projections in analyses:
        for projection in projections:
            is_open_request = (
                projection["clause_role"] == "main_request"
                and projection["speech_act"] == "open_question"
            )
            is_effect_request = (
                projection["clause_role"] == "main_request"
                and projection["speech_act"] == "imperative"
            )
            if (
                is_open_request
                and projection["relation"] == "spatial.located_at"
                and len(projection["arguments"]) == 3
            ):
                entity, position, time_argument = projection["arguments"]
                if (
                    entity.get("ref") == "person.explicit"
                    and position.get("state") == "unknown"
                ):
                    obligations.add("external_subject_authority")
                if (
                    entity.get("ref") == "actor.current"
                    and position.get("state") == "unknown"
                    and time_argument.get("ref") == "time.historical_explicit"
                ):
                    obligations.add("current_only_capability_mismatch")
            if (
                is_effect_request
                and projection["relation"] in {"communication.send", "acl.share"}
            ):
                obligations.add("effectful_outbound_consent")
    if canonical["status"] == "typed_ambiguity" and any(
        any(
            projection["clause_role"] == "main_request"
            and projection["speech_act"] == "imperative"
            and projection["relation"] == "movement.destination"
            for projection in projections
        )
        for projections in canonical["alternatives"]
    ):
        obligations.add("effectful_destination_clarification")
    return sorted(obligations)


def evaluate_phase1(
    records: list[dict[str, Any]], fixture: dict[str, Any],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Return five separate Phase-1 gates for one complete frozen batch."""
    if type(records) is not list or type(fixture) is not dict or type(registry) is not dict:
        raise TypeError("evaluator inputs are not exact containers")
    expected_cases = fixture.get("cases")
    if type(expected_cases) is not list or len(records) != len(expected_cases):
        raise RuntimeError("Phase-1 evaluation requires every frozen case")
    if any(
        record.get("result", {}).get("status")
        not in {"evaluated_valid", "evaluated_invalid"}
        for record in records
    ):
        raise RuntimeError("Phase-1 evaluation received a transport/protocol gap")
    relations = registry["relations"]
    oracle_by_opaque = {
        item["opaque_case_id"]: item for item in expected_cases
    }
    if len(oracle_by_opaque) != len(expected_cases):
        raise RuntimeError("Phase-1 fixture contains duplicate case identities")

    rows = []
    for record in records:
        expected = oracle_by_opaque[record["opaque_case_id"]]
        result = record["result"]
        frame = result.get("expanded_frame")
        evidence_ok = (
            result.get("status") == "evaluated_valid"
            and type(frame) is dict
        )
        if evidence_ok:
            actual_canonical = _canonical_candidate(frame, relations)
            coverage_ok = actual_canonical == expected["canonical_expectation"]
            predicted_direct = _direct_current_location(frame, relations)
            dependency = _dependency_current_location(frame, relations)
            safety = _safety_obligations(frame, relations)
            direct_ok = predicted_direct == expected["expected_direct"]
            dependency_ok = dependency == expected["expected_dependency"]
            safety_ok = safety == expected["expected_safety"]
        else:
            coverage_ok = direct_ok = dependency_ok = safety_ok = False
            predicted_direct = None
            dependency = None
            safety = None
        usable_direct = bool(
            evidence_ok and coverage_ok and safety_ok and predicted_direct is True
        )
        all_ok = bool(
            evidence_ok and coverage_ok and direct_ok and dependency_ok and safety_ok
        )
        rows.append({
            "opaque_case_id": record["opaque_case_id"],
            "query_sha256_utf8": record["query_sha256_utf8"],
            "evidence_ok": evidence_ok,
            "coverage_ok": coverage_ok,
            "direct_binding_ok": direct_ok,
            "dependency_ok": dependency_ok,
            "safety_ok": safety_ok,
            "all_gates_ok": all_ok,
            "expected_direct": expected["expected_direct"],
            "predicted_direct": predicted_direct,
            "usable_direct": usable_direct,
            "predicted_dependency": dependency,
            "predicted_safety": safety,
        })

    positives = [item for item in rows if item["expected_direct"]]
    negatives = [item for item in rows if not item["expected_direct"]]
    return {
        "version": "metnos.v26.5.6-phase1-evaluation/1.0",
        "summary": {
            "records": len(rows),
            "evidence_valid": sum(item["evidence_ok"] for item in rows),
            "coverage_exact": sum(item["coverage_ok"] for item in rows),
            "direct_binding_exact": sum(item["direct_binding_ok"] for item in rows),
            "dependency_exact": sum(item["dependency_ok"] for item in rows),
            "safety_exact": sum(item["safety_ok"] for item in rows),
            "all_gates_exact": sum(item["all_gates_ok"] for item in rows),
            "positive_usable": sum(item["usable_direct"] for item in positives),
            "positive_total": len(positives),
            "negative_usable_leakage": sum(item["usable_direct"] for item in negatives),
            "negative_total": len(negatives),
            "general_clause_coverage": "not_measured_by_controls34",
        },
        "records": rows,
    }
