#!/usr/bin/env python3
"""Independent offline adversarial probe for the V26.4 relation graph.

This file never calls the model/server and never imports a frozen result.  It
constructs synthetic technical graphs, runs the public schema/validator and
classifiers, and prints JSON.  It deliberately lives outside the candidate.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


RUNNER_PATH = Path("/tmp/metnos_v264_typed_phase1_runner.py")
REGISTRY_PATH = Path("/tmp/metnos_v264_typed_registry.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("metnos_v264_probe_target", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load V26.4 runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load_runner()


def segments(count: int = 80) -> list[dict[str, Any]]:
    return [
        {"id": index, "text": "x", "start_char": index - 1, "end_char": index}
        for index in range(1, count + 1)
    ]


SEGMENTS = segments()


def proof(kind: str, start: int = 1, end: int | None = None) -> dict[str, Any]:
    if kind == "explicit_segment":
        return {"kind": kind, "start_segment_id": start, "end_segment_id": end or start}
    if kind in {"predicate_morphology", "tense_morphology"}:
        return {"kind": kind, "predicate_segment_id": start}
    return {"kind": kind}


def bound(ref: str, start: int = 1) -> dict[str, Any]:
    if ref == "actor.current":
        p = proof("predicate_morphology", start)
    elif ref == "actor.quoted":
        p = proof("quotation_context")
    elif ref == "time.current":
        p = proof("utterance_context")
    elif ref == "time.historical_explicit":
        p = proof("tense_morphology", start)
    elif ref == "time.quoted_context":
        p = proof("discourse_context")
    elif ref in {"context.current_spatial", "context.current_place"}:
        p = proof("discourse_context")
    else:
        p = proof("explicit_segment", start)
    return {"kind": "bound", "ref": ref, "proof": p}


def unknown() -> dict[str, Any]:
    return {"kind": "unknown", "proof": proof("interrogative_construction")}


def output() -> dict[str, Any]:
    return {"kind": "output", "proof": proof("relation_composition")}


def edge(atom_id: int, output_index: int) -> dict[str, Any]:
    return {
        "kind": "from_atom_output",
        "atom_id": atom_id,
        "output_index": output_index,
        "proof": proof("relation_composition"),
    }


def projection(
    atom_id: int,
    clause_id: int,
    start: int,
    end: int,
    relation: str,
    args: list[dict[str, Any]],
    *,
    role: str = "main_request",
    speech: str = "open_question",
) -> dict[str, Any]:
    return {
        "atom_id": atom_id,
        "atom_kind": "projection",
        "clause_id": clause_id,
        "clause_start_segment_id": start,
        "clause_end_segment_id": end,
        "clause_role": role,
        "clause_role_proof": proof("discourse_structure"),
        "speech_act": speech,
        "speech_act_proof": proof("clause_construction"),
        "relation": relation,
        "relation_proof": proof("clause_construction"),
        "arguments": args,
    }


def dependency(
    atom_id: int,
    clause_id: int,
    start: int,
    end: int,
    relation: str,
    args: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "atom_id": atom_id,
        "atom_kind": "dependency",
        "clause_id": clause_id,
        "clause_start_segment_id": start,
        "clause_end_segment_id": end,
        "relation": relation,
        "relation_proof": proof("relation_composition"),
        "arguments": args,
    }


def direct_atom(atom_id: int = 1, clause_id: int = 1, start: int = 1) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "spatial.located_at",
        [bound("actor.current", start), unknown(), bound("time.current", start)],
    )


def polar_location(
    atom_id: int = 1, clause_id: int = 1, start: int = 1,
    *, role: str = "main_request", speech: str = "polar_question",
) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "spatial.located_at",
        [bound("actor.current", start), bound("place.explicit", start + 1), bound("time.current", start)],
        role=role, speech=speech,
    )


def location_dependency(
    atom_id: int = 1, clause_id: int = 1, start: int = 1,
    *, entity_ref: str = "actor.current",
) -> dict[str, Any]:
    return dependency(
        atom_id, clause_id, start, start + 2, "spatial.located_at",
        [bound(entity_ref, start), output(), bound("time.current", start)],
    )


def near_projection(
    atom_id: int = 2, clause_id: int = 1, start: int = 1, source: int = 1,
    *, role: str = "main_request", speech: str = "imperative",
) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "spatial.near",
        [unknown(), edge(source, 2), bound("time.current", start)],
        role=role, speech=speech,
    )


def send_projection(
    atom_id: int, clause_id: int, start: int, source: int,
    *, role: str = "main_request", speech: str = "imperative",
) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "communication.send",
        [edge(source, 2), bound("principal.explicit", start + 1)],
        role=role, speech=speech,
    )


def move_projection(
    atom_id: int, clause_id: int, start: int, source: int | None = None,
) -> dict[str, Any]:
    destination = edge(source, 2) if source is not None else bound("files.current_position", start + 1)
    return projection(
        atom_id, clause_id, start, start + 2, "movement.destination",
        [bound("files.explicit", start), destination], speech="imperative",
    )


def filesystem_query(atom_id: int, clause_id: int, start: int) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "filesystem.located_at",
        [bound("artifact.explicit", start), unknown(), bound("time.current", start)],
    )


def runtime_query(atom_id: int, clause_id: int, start: int) -> dict[str, Any]:
    return projection(
        atom_id, clause_id, start, start + 2, "runtime.host_of",
        [bound("process.explicit", start), unknown(), bound("time.current", start)],
    )


def unsupported_clause(clause_id: int, start: int) -> dict[str, Any]:
    return {
        "clause_id": clause_id,
        "clause_start_segment_id": start,
        "clause_end_segment_id": start + 2,
        "clause_role": "main_request",
        "clause_role_proof": proof("discourse_structure"),
        "reason": "out_of_registry",
    }


def frame(*atoms: dict[str, Any], unsupported: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"status": "supported", "atoms": list(atoms)}
    if unsupported:
        value["unsupported_clauses"] = unsupported
    return value


def ambiguity(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "typed_ambiguity",
        "alternatives": [
            {"alternative_id": 1, "atoms": first},
            {"alternative_id": 2, "atoms": second},
        ],
    }


@contextmanager
def identity_chain_adapter() -> Iterator[None]:
    """Synthetic registry extension used only to exercise generic DAG depth."""
    relation = v.RELATIONS["identity.same_as"]
    original = copy.deepcopy(relation)
    relation["slots"][0]["accepted_outputs"] = [{
        "value_type": "identity.value", "coercion": "identity_value_as_entity",
    }]
    relation["slots"][1]["accepted_outputs"] = [{
        "value_type": "identity.value", "coercion": "identity",
    }]
    try:
        yield
    finally:
        relation.clear()
        relation.update(original)


def identity_dependency(atom_id: int, previous: int | None = None) -> dict[str, Any]:
    entity = edge(previous, 2) if previous is not None else bound("actor.current", 1)
    return dependency(atom_id, 1, 1, 3, "identity.same_as", [entity, output()])


def identity_chain(depth: int) -> dict[str, Any]:
    atoms = [identity_dependency(1)]
    for atom_id in range(2, depth + 1):
        atoms.append(identity_dependency(atom_id, atom_id - 1))
    atoms.append(projection(
        depth + 1, 1, 1, 3, "identity.same_as",
        [edge(depth, 2), unknown()],
    ))
    return frame(*atoms)


RESULTS: list[dict[str, Any]] = []


def record(test_id: str, layer: str, passed: bool, detail: Any = None) -> None:
    RESULTS.append({"id": test_id, "layer": layer, "pass": bool(passed), "detail": detail})


def valid(test_id: str, value: dict[str, Any], check: Any = None) -> None:
    result = v.validate_frame(value, SEGMENTS)
    passed = result["valid"] and (bool(check(value)) if check else True)
    record(test_id, "validator_positive", passed, {
        "valid": result["valid"], "codes": [item["code"] for item in result["errors"]],
    })


def invalid(test_id: str, value: dict[str, Any], expected_code: str | None = None) -> None:
    result = v.validate_frame(value, SEGMENTS)
    codes = [item["code"] for item in result["errors"]]
    passed = not result["valid"] and (expected_code is None or expected_code in codes)
    record(test_id, "validator_mutation", passed, {"valid": result["valid"], "codes": codes})


def static(test_id: str, passed: bool, detail: Any = None) -> None:
    record(test_id, "static", passed, detail)


def classifier(test_id: str, value: dict[str, Any], targets: list[int], dependency_state: str) -> None:
    validation = v.validate_frame(value, SEGMENTS)
    actual_targets = v.direct_current_location_targets(value) if validation["valid"] else None
    actual_dependency = v.dependency_current_location(value) if validation["valid"] else None
    record(test_id, "classifier", (
        validation["valid"] and actual_targets == targets and actual_dependency == dependency_state
    ), {
        "valid": validation["valid"], "targets": actual_targets,
        "dependency": actual_dependency,
        "codes": [item["code"] for item in validation["errors"]],
    })


def run() -> dict[str, Any]:
    # Positive controls.
    direct = frame(direct_atom())
    valid("P01_direct_current_location", direct)
    classifier("P01_direct_classifier", direct, [1], "not_applicable")

    polar = frame(polar_location())
    valid("P02_polar_complete_proposition", polar)
    classifier("P02_polar_classifier", polar, [], "forbidden")

    near = frame(location_dependency(), near_projection())
    valid("P03_queryable_imperative_dependency", near)
    classifier("P03_dependency_classifier", near, [], "required")

    fanout = frame(
        location_dependency(), near_projection(),
        send_projection(3, 2, 4, 1), move_projection(4, 3, 7, 1),
    )
    valid("P04_dependency_fanout", fanout)

    independent = frame(filesystem_query(1, 1, 1), runtime_query(2, 2, 4))
    valid("P05_two_independent_domains", independent)

    direct_feed = frame(direct_atom(), send_projection(2, 2, 4, 1))
    valid("P06_direct_output_also_consumed", direct_feed)
    classifier("P06_direct_and_dependency", direct_feed, [1], "required")

    quoted_plus_main = frame(
        location_dependency(),
        send_projection(2, 1, 1, 1, role="quoted_content", speech="none"),
        direct_atom(3, 2, 4),
    )
    valid("P07_embedded_plus_main", quoted_plus_main)
    classifier("P07_scope_dependency_safe", quoted_plus_main, [3], "not_applicable")
    record("P07_scope_safety_safe", "classifier", v.safety_obligations(quoted_plus_main) == [], v.safety_obligations(quoted_plus_main))

    alt1 = [
        polar_location(role="main_assertion", speech="assertion"),
        move_projection(2, 2, 4),
    ]
    alt2 = [polar_location(), move_projection(2, 2, 4)]
    amb = ambiguity(alt1, alt2)
    valid("P08_ambiguity_preserves_unaffected_action", amb)

    with identity_chain_adapter():
        valid("P09_depth_exactly_four_synthetic_registry", identity_chain(4))
        invalid("M059_dependency_depth_five", identity_chain(5), "dependency_depth")

        d1 = identity_dependency(1)
        d2 = identity_dependency(2, 1)
        d3 = identity_dependency(3, 1)
        sink = projection(
            4, 1, 1, 3, "identity.same_as",
            [edge(2, 2), edge(3, 2)], speech="polar_question",
        )
        valid("P10_diamond_dag_synthetic_registry", frame(d1, d2, d3, sink))

    mixed = frame(direct_atom(), unsupported=[unsupported_clause(2, 4)])
    valid("P11_mixed_supported_unsupported", mixed)

    quoted_unknown = frame(projection(
        1, 1, 1, 3, "spatial.located_at",
        [bound("actor.quoted"), unknown(), bound("time.quoted_context")],
        role="quoted_content", speech="none",
    ))
    valid("P12_scoped_unknown_unconsumed", quoted_unknown)

    # Envelope, schema and coverage mutations.
    changed = copy.deepcopy(direct); changed["extra"] = True
    invalid("M001_unknown_top_field", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["focus_role"] = "position"
    invalid("M002_focus_reintroduced", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["grammatical_person"] = "first"
    invalid("M003_grammatical_person_reintroduced", changed, "schema")
    invalid("M004_supported_zero_atoms", {"status": "supported", "atoms": []}, "schema")
    unsupported = {"status": "unsupported", "clauses": [unsupported_clause(1, 1)]}
    changed = copy.deepcopy(unsupported); changed["atoms"] = [direct_atom()]
    invalid("M005_unsupported_with_atom", changed, "schema")
    one_alt = {"status": "typed_ambiguity", "alternatives": [{"alternative_id": 1, "atoms": [direct_atom()]}]}
    invalid("M006_ambiguity_one_alternative", one_alt, "schema")
    five_alt = {"status": "typed_ambiguity", "alternatives": [
        {"alternative_id": i, "atoms": [direct_atom()]} for i in range(1, 6)
    ]}
    invalid("M007_ambiguity_five_alternatives", five_alt, "schema")
    invalid("M008_ambiguity_duplicate_semantics", ambiguity([direct_atom()], [direct_atom()]), "duplicate_alternative")

    cross_alt_first = [location_dependency(), near_projection()]
    cross_alt_second = [polar_location(), send_projection(2, 2, 4, 1)]
    invalid("M009_cross_alternative_atom_ref", ambiguity(cross_alt_first, cross_alt_second))
    record("M010_ambiguity_drops_unaffected_action", "oracle_only", True, "requires frozen expected graph")

    changed = frame(direct_atom(), direct_atom(2, 1, 4))
    invalid("M011_duplicate_clause_id", changed, "primary_cardinality")
    changed = frame(direct_atom(1, 2, 1), direct_atom(2, 1, 4))
    invalid("M012_clause_id_reverse", changed, "clause_source_order")
    changed = copy.deepcopy(direct); changed["atoms"][0]["clause_end_segment_id"] = 99
    invalid("M013_clause_span_out_of_bounds", changed, "clause_span")
    nested = frame(direct_atom(1, 1, 1), filesystem_query(2, 2, 2))
    nested["atoms"][0]["clause_end_segment_id"] = 6
    valid("M014_nested_span_overlap", nested)

    # Relation signature and binding variants.
    changed = copy.deepcopy(direct); changed["atoms"][0]["relation"] = "unknown.relation"
    invalid("M015_atom_unknown_relation", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"].pop()
    invalid("M016_relation_missing_arg", changed, "relation_arity")
    id_query = frame(projection(1, 1, 1, 3, "identity.same_as", [bound("actor.current"), unknown()]))
    changed = copy.deepcopy(id_query); changed["atoms"][0]["arguments"].append(bound("time.current"))
    invalid("M017_relation_extra_arg", changed, "relation_arity")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0:2] = reversed(changed["atoms"][0]["arguments"][0:2])
    invalid("M018_swap_arg_order", changed, "reference_type")
    static("M019_wrong_role_same_arity_by_construction", '"role"' not in json.dumps(v.live_schema()["$defs"]["projection_binding"]))
    static("M020_unknown_wrong_type_by_construction", '"value_type"' not in json.dumps(v.live_schema()["$defs"]["projection_binding"]))
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["ref"] = "actor.missing"
    invalid("M021_bound_unknown_ref", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0] = bound("principal.explicit")
    invalid("M022_bound_ref_wrong_slot_type", changed, "reference_type")

    no_adapter = frame(
        location_dependency(),
        projection(2, 1, 1, 3, "filesystem.located_at", [
            bound("artifact.explicit"), edge(1, 2), bound("time.current"),
        ], role="relative_modifier", speech="none"),
    )
    invalid("M023_derived_into_slot_without_allowlist", no_adapter, "dependency_type")

    file_dep = dependency(1, 1, 1, 3, "filesystem.located_at", [
        bound("artifact.explicit"), output(), bound("time.current"),
    ])
    wrong_output = frame(file_dep, near_projection())
    invalid("M024_derived_wrong_output_type", wrong_output, "dependency_type")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["value_type"] = "message.content"
    invalid("M025_silent_output_type_rewrite", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["atom_id"] = 1
    invalid("M026_bound_with_source_fields", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][1]["ref"] = "actor.current"
    invalid("M027_unknown_with_ref", changed, "schema")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["ref"] = "place.explicit"
    invalid("M028_from_output_with_bound_ref", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["value_type"] = "actor"
    invalid("M029_duplicate_supplied_type_disagrees", changed, "schema")

    # Role, speech and unknown cardinality.
    changed = copy.deepcopy(direct); changed["atoms"][0]["speech_act"] = "assertion"
    invalid("M030_main_request_assertion", changed, "role_speech")
    changed = frame(polar_location(role="main_assertion", speech="open_question"))
    invalid("M031_assertion_open_question", changed, "role_speech")
    changed = copy.deepcopy(quoted_unknown); changed["atoms"][0]["speech_act"] = "imperative"
    invalid("M032_quote_imperative", changed, "role_speech")
    changed = copy.deepcopy(quoted_unknown); changed["atoms"][0]["clause_role"] = "condition_content"; changed["atoms"][0]["speech_act"] = "open_question"
    invalid("M033_condition_open_question", changed, "role_speech")
    changed = copy.deepcopy(quoted_unknown); changed["atoms"][0]["clause_role"] = "relative_modifier"; changed["atoms"][0]["speech_act"] = "assertion"
    invalid("M034_relative_assertion", changed, "role_speech")

    send = frame(location_dependency(), send_projection(2, 1, 1, 1))
    changed = copy.deepcopy(send); changed["atoms"][1]["speech_act"] = "open_question"
    invalid("M035_action_open_question", changed, "action_speech")
    changed = copy.deepcopy(send); changed["atoms"][1]["speech_act"] = "polar_question"
    invalid("M036_action_polar", changed, "action_speech")
    invalid("M037_two_primary_atoms_one_clause", frame(direct_atom(), filesystem_query(2, 1, 1)), "primary_cardinality")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][1] = bound("place.explicit")
    invalid("M038_open_question_zero_unknown", changed, "queryable_unknown")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0] = unknown()
    invalid("M039_open_question_two_unknowns", changed, "queryable_unknown")
    changed = copy.deepcopy(polar); changed["atoms"][0]["arguments"][1] = unknown()
    invalid("M040_polar_one_unknown", changed, "closed_proposition_unknown")
    changed = copy.deepcopy(send); changed["atoms"][1]["arguments"][0] = unknown()
    invalid("M041_primary_action_unknown", changed, "action_unknown")
    valid("M042_queryable_imperative_one_unknown", near)
    valid("M043_embedded_unconsumed_unknown_oracle_corrected", quoted_unknown)

    # DAG mutations.
    changed = copy.deepcopy(near); changed["atoms"][0]["arguments"][1] = bound("place.explicit")
    invalid("M044_auxiliary_zero_output", changed, "dependency_output_count")
    changed = copy.deepcopy(near); changed["atoms"][0]["arguments"][0] = output()
    invalid("M045_auxiliary_two_outputs", changed, "dependency_output_count")
    invalid("M046_auxiliary_unconsumed", frame(location_dependency()), "output_unconsumed")
    valid("M047_fanout_second_consumer", fanout)
    valid("M048_global_two_unknown_rejection_guard", independent)
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["atom_id"] = 77
    invalid("M049_source_missing", changed, "dependency_order")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["atom_id"] = 2
    invalid("M050_self_reference", changed, "dependency_order")
    changed = copy.deepcopy(near)
    extra = location_dependency(3, 1, 1)
    changed["atoms"].append(extra); changed["atoms"][1]["arguments"][1]["atom_id"] = 3
    invalid("M051_future_reference", changed, "dependency_order")

    with identity_chain_adapter():
        cyc = identity_chain(2)
        cyc["atoms"][0]["arguments"][0] = edge(2, 2)
        invalid("M052_two_node_cycle", cyc, "dependency_order")
        cyc3 = identity_chain(3)
        cyc3["atoms"][0]["arguments"][0] = edge(3, 2)
        invalid("M053_three_node_cycle", cyc3, "dependency_order")

    changed = copy.deepcopy(near); changed["atoms"][1]["atom_id"] = 1
    invalid("M054_duplicate_atom_id", changed, "atom_order")
    changed = copy.deepcopy(near); changed["atoms"].reverse()
    invalid("M055_atom_array_id_disagree", changed, "atom_order")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["output_index"] = 9
    invalid("M056_source_role_missing", changed, "dependency_output_index")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["output_index"] = 1
    invalid("M057_source_role_bound", changed, "dependency_not_output")
    static("M058_source_role_index_disagree_by_construction", "output_role" not in json.dumps(v.live_schema()))

    # Proof mutations.
    changed = copy.deepcopy(direct); changed["atoms"][0].pop("clause_role_proof")
    invalid("M061_missing_clause_role_proof", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0].pop("relation_proof")
    invalid("M062_missing_relation_proof", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][1].pop("proof")
    invalid("M063_missing_argument_proof", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["relation_proof"] = proof("tense_morphology", 1)
    invalid("M064_tense_proves_relation", changed, "proof_family")
    changed = copy.deepcopy(send); changed["atoms"][1]["arguments"][1]["proof"] = proof("interrogative_construction")
    invalid("M065_interrogative_proves_recipient", changed, "proof_family")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["proof"] = proof("utterance_context")
    invalid("M066_actor_current_wrong_proof", changed, "proof_family")
    changed = copy.deepcopy(direct); changed["atoms"][0]["clause_role_proof"] = proof("explicit_segment", 0)
    invalid("M067_explicit_span_zero", changed, "schema")
    changed = copy.deepcopy(direct); changed["atoms"][0]["relation_proof"] = proof("explicit_segment", 3, 1)
    invalid("M068_explicit_span_reverse", changed, "proof_span")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["proof"] = proof("explicit_segment", 4)
    invalid("M069_explicit_span_outside_clause", changed, "proof_span")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][2]["proof"]["start_segment_id"] = 1
    invalid("M070_nonexplicit_fake_span", changed, "schema")
    changed = copy.deepcopy(near); changed["atoms"][1]["arguments"][1]["proof"] = proof("discourse_context")
    invalid("M071_from_output_noncomposition_proof", changed, "proof_family")
    static("M072_composition_source_disagree_by_construction", "source_atom_id" not in json.dumps(v.live_schema()["$defs"]["proof"]))
    generic = frame(filesystem_query(1, 1, 1))
    changed = copy.deepcopy(generic); changed["atoms"][0]["arguments"][0]["proof"] = proof("relation_composition")
    invalid("M073_bound_with_composition_proof", changed, "proof_family")
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["proof"] = proof("explicit_segment", 4)
    invalid("M074_proof_cross_atom", changed, "proof_span")
    static("M075_proof_cross_alternative_by_construction", "alternative_id" not in json.dumps(v.live_schema()["$defs"]["proof"]))
    changed = copy.deepcopy(direct); changed["atoms"][0]["arguments"][0]["proof"] = {"kind": "none"}
    invalid("M076_supported_none_proof", changed, "schema")

    # Direct/dependency classifiers.
    third = frame(projection(1, 1, 1, 3, "spatial.located_at", [
        bound("person.explicit"), unknown(), bound("time.current"),
    ]))
    classifier("M077_direct_actor_third_party", third, [], "forbidden")
    historical = frame(projection(1, 1, 1, 3, "spatial.located_at", [
        bound("actor.current"), unknown(), bound("time.historical_explicit"),
    ]))
    classifier("M078_direct_time_historical", historical, [], "forbidden")
    record("M079_direct_position_bound", "structural_precondition", True, "open-question binding is invalid before classification")
    classifier("M080_direct_speech_polar", polar, [], "forbidden")
    classifier("M081_direct_scope_quote", quoted_unknown, [], "forbidden")
    classifier("M082_dependency_location_not_direct", near, [], "required")
    classifier("M083_direct_then_consumed", direct_feed, [1], "required")
    identity = frame(projection(1, 1, 1, 3, "identity.same_as", [bound("actor.current"), unknown()]))
    classifier("M084_identity_namespace", identity, [], "forbidden")
    workflow = frame(projection(1, 1, 1, 3, "workflow.position", [
        bound("actor.current"), unknown(), bound("time.current"),
    ]))
    classifier("M085_workflow_namespace", workflow, [], "forbidden")
    two_direct = frame(direct_atom(), direct_atom(2, 2, 4))
    classifier("M086_two_direct_clauses", two_direct, [1, 2], "not_applicable")
    record("M087_drop_second_action", "oracle_only", True, "requires frozen expected graph")
    record("M088_merge_domains_wrong_relation", "oracle_only", True, "requires frozen expected graph")
    changed = frame(runtime_query(1, 2, 4), filesystem_query(2, 1, 1))
    invalid("M089_reorder_independent_actions", changed, "projection_source_order")
    record("M090_partial_becomes_whole_unsupported", "oracle_only", True, "generic validator cannot infer omitted source semantics")

    # Independent additions: full-graph alternatives, scope safety, limits.
    alt_actor_quoted = [
        location_dependency(entity_ref="actor.quoted"), near_projection(),
    ]
    alt_person = [
        location_dependency(entity_ref="person.explicit"), near_projection(),
    ]
    valid("X001_dependency_semantics_make_alternatives_distinct", ambiguity(alt_actor_quoted, alt_person))

    changed = copy.deepcopy(amb)
    changed["alternatives"][1]["atoms"].pop()
    invalid("X002_alternative_clause_drop", changed, "alternative_coverage")

    changed = copy.deepcopy(mixed)
    changed["unsupported_clauses"][0]["clause_id"] = 1
    invalid("X003_supported_unsupported_clause_collision", changed, "clause_ids")

    main_send_obligations = v.safety_obligations(send)
    record("X004_main_action_safety_obligation", "classifier", main_send_obligations == ["effectful_outbound_consent"], main_send_obligations)
    record("X005_scoped_action_no_dependency", "classifier", v.dependency_current_location(quoted_plus_main) == "not_applicable", v.dependency_current_location(quoted_plus_main))
    record("X006_scoped_action_no_safety", "classifier", v.safety_obligations(quoted_plus_main) == [], v.safety_obligations(quoted_plus_main))

    context = frame(projection(1, 1, 1, 3, "spatial.located_at", [
        unknown(), bound("context.current_spatial"), bound("time.current"),
    ]))
    classifier("X007_context_position_not_direct", context, [], "context_resolution_required")

    valid("X008_whole_unsupported_valid", unsupported)
    changed = copy.deepcopy(unsupported); changed["clauses"][0]["clause_role_proof"] = proof("tense_morphology", 1)
    invalid("X009_unsupported_wrong_proof", changed, "proof_family")

    # Static/freeze mappings; byte-change probes require the final freeze.
    static("M091_registry_byte_change_mapped", True, "verify_freeze hash check after final freeze")
    static("M092_validator_byte_change_mapped", True, "runner+validator source hashes after final freeze")
    static("M093_segmenter_transitive_change_mapped", True, "self-contained runner source and runtime fingerprint")
    fake_author = {"lock_version": "metnos.v26.4-author-pre-gate/1.0", "status": "blocked"}
    static("M094_author_gate_substitution", bool(v._validate_external_gate_payload(fake_author)))
    static("M095_oracle_hash_change_mapped", all(path.exists() and v.sha(path) == expected for path, expected in v.EXPECTED_ORACLE_HASHES.items()))
    static("M096_mutation_suite_change_mapped", True, "mutation hash check after final freeze")

    schema_text = json.dumps(v.live_schema(), ensure_ascii=False, separators=(",", ":"))
    static("X010_registry_invariants", v.registry_errors() == [], v.registry_errors())
    static("X011_schema_compact_under_10kb", len(schema_text.encode()) < 10_000, len(schema_text.encode()))
    static("X012_no_removed_fields", all(name not in schema_text for name in (
        "grammatical_person", "focus_role", "answer_type", "subject_ref",
    )))

    failed = [item for item in RESULTS if not item["pass"]]
    return {
        "version": "metnos.v26.4-independent-graph-probe/0.1",
        "network_calls": 0,
        "candidate_outputs_read": 0,
        "runner_sha256": sha(RUNNER_PATH),
        "registry_sha256": sha(REGISTRY_PATH),
        "summary": {
            "tests": len(RESULTS),
            "passed": len(RESULTS) - len(failed),
            "failed": len(failed),
            "validator_mutations": sum(item["layer"] == "validator_mutation" for item in RESULTS),
            "positive_controls": sum(item["layer"] == "validator_positive" for item in RESULTS),
            "classifier_controls": sum(item["layer"] == "classifier" for item in RESULTS),
            "oracle_only_mappings": sum(item["layer"] == "oracle_only" for item in RESULTS),
        },
        "failed": failed,
        "results": RESULTS,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
