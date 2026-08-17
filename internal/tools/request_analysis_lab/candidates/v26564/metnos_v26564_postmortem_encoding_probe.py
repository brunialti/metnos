#!/usr/bin/env python3
"""Offline post-mortem probe for the V26.5.6.4 live K1/34 run.

Pure analysis. No network, no model, no rerun, no gold mutation. It reads the
frozen artifacts read-only and answers four questions mechanically:

  A. Is the GOLD answer representable under the V26.5.6 compact encoding?
  B. Can a frame satisfy the frozen JSON Schema and still be rejected by the
     frozen adapter?  (schema/decoder gap -> unenforceable by constrained
     decoding)
  C. Does the proposed minimal successor turn that gap into a *structural*
     schema constraint, without surface mapping or language-specific lists?
  D. Do the live counters agree with the frozen batch?

Exit code 0 iff every assertion holds.

Run:
  /usr/bin/python3 -I -B \
    internal/tools/request_analysis_lab/candidates/v26564/\
metnos_v26564_postmortem_encoding_probe.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve(strict=True).parent
CANDIDATES = HERE.parent

SCHEMA_PATH = CANDIDATES / "v265" / "metnos_v265_compact.schema.json"
ADAPTER_PATH = CANDIDATES / "v265" / "metnos_v265_compact_adapter.py"
FIXTURE_PATH = CANDIDATES / "v2641" / "metnos_v2641_typed_phase1_controls_frozen.json"
BATCH_PATH = HERE / "metnos_v26564_live_k1_34.json"

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def load_adapter():
    spec = importlib.util.spec_from_file_location("_v265_adapter", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- frame constructors over the frozen compact schema ----------------------

DISCOURSE = {"kind": "discourse_structure"}
COMPOSITION = {"kind": "relation_composition"}
UNKNOWN = {"kind": "unknown", "proof": DISCOURSE}
OUTPUT = {"kind": "output", "proof": COMPOSITION}

CLAUSE_FIELDS = ("clause_start_segment_id", "clause_end_segment_id", "atom_kind")


def segment_proof(start: int, end: int) -> dict:
    return {"kind": "explicit_segment", "start_segment_id": start, "end_segment_id": end}


def bound(ref: str) -> dict:
    return {"kind": "bound", "ref": ref, "proof": DISCOURSE}


def prior(ordinal: int) -> dict:
    return {"kind": "from_prior_atom", "source_ordinal": ordinal, "proof": COMPOSITION}


def projection(relation: str, start: int, end: int, arguments: list) -> dict:
    return {
        "atom_kind": "projection",
        "clause_start_segment_id": start,
        "clause_end_segment_id": end,
        "relation": relation,
        "relation_proof": segment_proof(start, end),
        "arguments": arguments,
        "clause_role": "main_request",
        "clause_role_proof": DISCOURSE,
        "speech_act": "open_question",
        "speech_act_proof": segment_proof(start, end),
    }


def dependency(relation: str, start: int, end: int, arguments: list) -> dict:
    return {
        "atom_kind": "dependency",
        "clause_start_segment_id": start,
        "clause_end_segment_id": end,
        "relation": relation,
        "relation_proof": COMPOSITION,
        "arguments": arguments,
    }


def unsupported_clause(start: int, end: int) -> dict:
    return {
        "clause_start_segment_id": start,
        "clause_end_segment_id": end,
        "clause_role": "main_request",
        "clause_role_proof": DISCOURSE,
        "reason": "out_of_registry",
    }


# --- minimal successor: clause identity moves into the document structure ---
#
# One variable changes. Atoms stop being a flat array whose clause identity is
# reconstructed from an inferred source span; they become clauses that OWN
# exactly one projection plus their dependencies. Clause identity is the array
# position, so no numeric label is emitted and compactness is preserved. The
# span degrades to pure evidence and stops being a primary key.


def successor_schema(compact: dict) -> dict:
    """Derive the successor schema from the frozen compact one.

    The frozen $defs (relations, refs, proofs, bindings) are reused verbatim so
    the comparison isolates exactly one variable: where clause identity lives.
    """
    defs = copy.deepcopy(compact["$defs"])
    projection_body = copy.deepcopy(defs["projection_atom"])
    dependency_body = copy.deepcopy(defs["dependency_atom"])
    for body in (projection_body, dependency_body):
        for field in CLAUSE_FIELDS:
            body["properties"].pop(field, None)
            if field in body["required"]:
                body["required"].remove(field)
    defs["successor_projection"] = projection_body
    defs["successor_dependency"] = dependency_body
    defs["successor_clause"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "clause_start_segment_id": {"type": "integer", "minimum": 1},
            "clause_end_segment_id": {"type": "integer", "minimum": 1},
            # exactly one projection per clause -- structural, not prose
            "projection": {"$ref": "#/$defs/successor_projection"},
            "dependencies": {
                "type": "array",
                "minItems": 1,
                "maxItems": 15,
                "items": {"$ref": "#/$defs/successor_dependency"},
            },
        },
        "required": [
            "clause_start_segment_id",
            "clause_end_segment_id",
            "projection",
        ],
    }
    return {
        "$schema": compact["$schema"],
        "$defs": defs,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"const": "supported"},
            "clauses": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"$ref": "#/$defs/successor_clause"},
            },
            "unsupported_clauses": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"$ref": "#/$defs/unsupported_clause"},
            },
        },
        "required": ["status", "clauses"],
    }


def strip_clause_fields(atom: dict) -> dict:
    return {key: value for key, value in atom.items() if key not in CLAUSE_FIELDS}


def main() -> int:
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    adapter = load_adapter()
    validator = jsonschema.Draft202012Validator(schema)

    def schema_valid(frame: dict) -> bool:
        return not list(validator.iter_errors(frame))

    def adapter_verdict(frame: dict):
        try:
            adapter.expand_frame(copy.deepcopy(frame))
        except adapter.UnsafeCompactGraph as error:
            return str(error)
        return None

    print("A. GOLD representability under the frozen compact encoding")
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    counts = [
        len(case["canonical_expectation"].get("projections", []))
        for case in fixture["cases"]
    ]
    statuses = [case["canonical_expectation"]["status"] for case in fixture["cases"]]
    check(
        "gold never expects more than one projection per graph",
        max(counts) <= 1,
        f"max={max(counts)} supported={statuses.count('supported')} "
        f"typed_ambiguity={statuses.count('typed_ambiguity')}",
    )
    gold_shape = {
        "status": "supported",
        "atoms": [
            projection(
                "spatial.located_at", 1, 3,
                [bound("actor.current"), UNKNOWN, bound("time.current")],
            )
        ],
    }
    check("gold-shaped frame is schema-valid", schema_valid(gold_shape))
    check(
        "gold-shaped frame is accepted by the adapter",
        adapter_verdict(gold_shape) is None,
    )

    print("\nB. schema/decoder gap on the frozen compact encoding")
    hedge = {
        "status": "supported",
        "atoms": [
            projection("spatial.located_at", 1, 3,
                       [bound("actor.current"), UNKNOWN, bound("time.current")]),
            projection("workflow.position", 1, 3,
                       [bound("actor.current"), UNKNOWN, bound("time.current")]),
        ],
    }
    mixed = {
        "status": "supported",
        "atoms": [
            projection("spatial.located_at", 1, 3,
                       [bound("actor.current"), UNKNOWN, bound("time.current")])
        ],
        "unsupported_clauses": [unsupported_clause(1, 3)],
    }
    narrow = {
        "status": "supported",
        "atoms": [
            dependency("spatial.located_at", 4, 5,
                       [bound("actor.current"), OUTPUT, bound("time.current")]),
            projection("spatial.near", 1, 5,
                       [UNKNOWN, prior(1), bound("time.current")]),
        ],
    }
    forced = copy.deepcopy(narrow)
    forced["atoms"][0]["clause_start_segment_id"] = 1
    forced["atoms"][0]["clause_end_segment_id"] = 5

    span_code = "two semantic clauses claim the same source span"
    dep_code = "dependency has no projection with the same clause span"
    for label, frame, expected in (
        ("two projections on one span", hedge, span_code),
        ("projection + unsupported clause on one span", mixed, span_code),
        ("dependency grounded narrower than its projection", narrow, dep_code),
    ):
        verdict = adapter_verdict(frame)
        check(f"{label}: satisfies the frozen schema", schema_valid(frame))
        check(
            f"{label}: rejected by the adapter as '{expected}'",
            verdict == expected, f"got {verdict!r}",
        )
    check(
        "widening the dependency span to equal the projection span is accepted",
        schema_valid(forced) and adapter_verdict(forced) is None,
        "the adapter demands exact tuple equality, not containment",
    )

    print("\nC. minimal successor: clause identity as structure, not as key")
    succ = successor_schema(schema)
    jsonschema.Draft202012Validator.check_schema(succ)
    succ_validator = jsonschema.Draft202012Validator(succ)

    def succ_valid(frame: dict) -> bool:
        return not list(succ_validator.iter_errors(frame))

    def clause(start, end, proj, deps=None):
        out = {
            "clause_start_segment_id": start,
            "clause_end_segment_id": end,
            "projection": strip_clause_fields(proj),
        }
        if deps:
            out["dependencies"] = [strip_clause_fields(item) for item in deps]
        return out

    succ_gold = {
        "status": "supported",
        "clauses": [clause(1, 3, gold_shape["atoms"][0])],
    }
    check("successor represents the gold shape", succ_valid(succ_gold))

    succ_hedge = copy.deepcopy(succ_gold)
    succ_hedge["clauses"][0]["projection2"] = succ_hedge["clauses"][0]["projection"]
    check(
        "successor makes a second projection in one clause SCHEMA-INVALID",
        not succ_valid(succ_hedge),
        "the 32-case failure becomes unrepresentable, not merely forbidden",
    )

    succ_dep = {
        "status": "supported",
        "clauses": [clause(1, 5, narrow["atoms"][1], [narrow["atoms"][0]])],
    }
    check(
        "successor represents a dependency with no span-equality demand",
        succ_valid(succ_dep),
        "the dependency belongs to its clause structurally; span stays evidence",
    )

    print("\nD. live batch cross-check (read-only)")
    batch = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    codes: dict[str, int] = {}
    stages: dict[str, int] = {}
    for record in batch["records"]:
        validation = record["result"]["validation"]
        key = "|".join(validation["codes"])
        codes[key] = codes.get(key, 0) + 1
        stages[validation["stage"]] = stages.get(validation["stage"], 0) + 1
    check("32 cases failed on the span-collision code", codes.get(span_code) == 32, str(codes))
    check("1 case failed on the dependency-span code", codes.get(dep_code) == 1)
    check("1 case reached the validator", stages.get("validator") == 1, str(stages))
    check(
        "0 cases were valid",
        batch["summary"]["inference_counters"]["valid_cases"] == 0,
    )
    check(
        "33 cases were censored before any semantic validation",
        stages.get("adapter") == 33,
        "the adapter aborts the pipeline, so validator-level quality is unmeasured",
    )

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} assertion(s): {FAILURES}")
        return 1
    print("ALL ASSERTIONS HOLD -- no network, no model, no gold mutation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
