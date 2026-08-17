"""Fail-closed, query-free validation for the blueprint-first holdout process."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from jsonschema import Draft202012Validator

import build_artifacts as build


HERE = Path(__file__).resolve().parent
CANDIDATE_V01 = build.LAB / "candidate_v0_1"
if str(CANDIDATE_V01) not in sys.path:
    sys.path.insert(0, str(CANDIDATE_V01))

from intent_shadow_registry import validate_registry_document  # noqa: E402
from intent_shadow_validate import validate_model_document  # noqa: E402


class PreflightError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}:{message}" if message else code)
        self.code = code


def _require(condition: bool, code: str, message: str = "") -> None:
    if not condition:
        raise PreflightError(code, message)


def load_artifacts() -> dict[str, Any]:
    return {name: build.load_json(HERE / name) for name in build.GENERATED}


def authority_sha(artifacts: dict[str, Any]) -> str:
    return build.payload_sha(artifacts["authority_manifest.json"])


def validate_authority(manifest: dict[str, Any]) -> None:
    expected = build.authority_manifest()
    _require(manifest == expected, "HASH_BINDING", "authority manifest drift")
    semantic = manifest["semantic_authority"]
    _require("canonical_expected_schema" in semantic, "HASH_BINDING", "canonical schema absent")
    _require("candidate_v0_1/intent_shadow_model_v0_1.schema.json" in semantic["canonical_expected_schema"]["path"],
             "HASH_BINDING", "model-facing compact schema is not canonical gold")
    _require(manifest["challenger_commitment"]["role"] == "chronology_only_not_semantic_authority",
             "HASH_BINDING", "challenger role")


def validate_glossary(glossary: dict[str, Any]) -> None:
    registry = build.load_json(build.REGISTRY)
    validate_registry_document(registry)
    _require(set(glossary["operations"]) == set(registry["operations"]), "GLOSSARY_SCOPE", "operation set")
    _require(set(glossary["barriers"]) == set(registry["barriers"]), "GLOSSARY_SCOPE", "barrier set")
    _require(set(glossary["system_controls"]) == set(registry["system_controls"]), "GLOSSARY_SCOPE", "control set")
    _require(set(glossary["unrepresentable_reasons"]) == set(registry["unrepresentable_reasons"]),
             "GLOSSARY_SCOPE", "reason set")
    _require(len(glossary["outside_families"]) == len(set(glossary["outside_families"])),
             "GLOSSARY_SCOPE", "outside duplicates")
    for route, item in glossary["operations"].items():
        _require(type(item.get("author_gloss")) is str and bool(item["author_gloss"].strip()),
                 "GLOSSARY_SCOPE", route)
        _require(item["input_ports"] == registry["operations"][route]["input_ports"],
                 "GLOSSARY_SCOPE", f"input ports {route}")
        _require(item["output_ports"] == registry["operations"][route]["output_ports"],
                 "GLOSSARY_SCOPE", f"output ports {route}")


def validate_schemas(bundle: dict[str, Any]) -> None:
    _require(set(bundle["schemas"]) == {
        "proposal", "semantic_gold", "author_projection", "post_query_judgment", "post_query_envelope"
    }, "CLOSED_SCHEMA", "schema set")
    for name, schema in bundle["schemas"].items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise PreflightError("CLOSED_SCHEMA", name) from exc


def _schema_validate(name: str, value: Any, artifacts: dict[str, Any]) -> None:
    schema = artifacts["artifact_schemas.json"]["schemas"][name]
    local = deepcopy(schema)
    if name in {"semantic_gold", "post_query_judgment"}:
        local["properties"]["expected"] = {}
    errors = sorted(Draft202012Validator(local).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        raise PreflightError("CLOSED_SCHEMA", f"{name}:{errors[0].message}")


def validate_pilot_matrix(matrix: dict[str, Any]) -> None:
    slots = matrix.get("slots")
    _require(type(slots) is list and len(slots) == 20, "PILOT_MATRIX", "20 slots required")
    _require([item["proposal_id"] for item in slots] == [f"bp-{index:03d}" for index in range(1, 21)],
             "PILOT_MATRIX", "proposal IDs")
    counts = Counter(item["language_tag"] for item in slots)
    _require(counts == Counter({language: 2 for language in build.LANGUAGES}), "PILOT_MATRIX", "language balance")
    cells = {item["cell"] for item in slots}
    required = {
        "G1_SINGLE", "G2_COMPOUND_INDEPENDENT", "G3_COMPOUND_DEPENDENT",
        "G4_COVERAGE_BOUNDARY", "G5_LINGUISTIC_VARIATION", "S1_APPROVAL",
        "S2_NEGATION", "S3_CONDITIONAL_BRANCH", "S4_UNDO",
        "S5_MIXED_CONTROL", "S6_FALSE_ACTION_TRAP",
    }
    _require(cells == required, "PILOT_MATRIX", "cell coverage")
    g4 = {item["subtype"] for item in slots if item["cell"] == "G4_COVERAGE_BOUNDARY"}
    _require(g4 == {"mixed", "outside_only"}, "PILOT_MATRIX", "G4 subtypes")
    _require(sum(item["cell"] == "S4_UNDO" for item in slots) == 1, "PILOT_MATRIX", "S4 once")


def validate_rubrics(rubrics: dict[str, Any]) -> None:
    _require(rubrics.get("outcomes") == ["pass", "fail"], "RUBRIC", "binary outcomes")
    _require(rubrics.get("uncertain_is_fail") is True, "RUBRIC", "uncertain must fail")
    ids = []
    for key in ("native_review", "semantic_review", "cross_language_review", "isolation_review"):
        rows = rubrics.get(key)
        _require(type(rows) is list and rows, "RUBRIC", key)
        for row in rows:
            _require(set(row) == {"id", "rule"}, "RUBRIC", f"closed {key}")
            ids.append(row["id"])
    _require(len(ids) == len(set(ids)), "RUBRIC", "duplicate ID")


def validate_mutation_catalog(catalog: dict[str, Any]) -> None:
    rows = catalog.get("mutations")
    _require(type(rows) is list and len(rows) == 14, "MUTATION_CATALOG", "14 mutations")
    _require([row["id"] for row in rows] == [f"M{index:02d}" for index in range(1, 15)],
             "MUTATION_CATALOG", "IDs")
    _require(len({row["expected_code"] for row in rows}) == 14, "MUTATION_CATALOG", "unique codes")


def _obligation_map(proposal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    obligations = proposal["obligations"]
    mapping = {item["obligation_id"]: item for item in obligations}
    _require(len(mapping) == len(obligations), "RELATIONAL_MISMATCH", "duplicate obligation ID")
    return mapping


def _kinds(proposal: dict[str, Any]) -> Counter[str]:
    return Counter(item["kind"] for item in proposal["obligations"])


def _relations(proposal: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in proposal["relations"] if item["kind"] == kind]


def validate_proposal(proposal: dict[str, Any], artifacts: dict[str, Any]) -> None:
    _schema_validate("proposal", proposal, artifacts)
    _require(proposal["authority_manifest_sha256"] == authority_sha(artifacts),
             "HASH_BINDING", "proposal authority")
    mapping = _obligation_map(proposal)
    ids = set(mapping)
    mention = proposal["canonical_mention_order"]
    _require(set(mention) == ids and len(mention) == len(ids), "RELATIONAL_MISMATCH", "mention order")
    for relation in proposal["relations"]:
        _require(set(relation["members"]) <= ids, "RELATIONAL_MISMATCH", "unknown relation member")
    kinds = _kinds(proposal)
    cell = proposal["cell"]
    relation_kinds = Counter(item["kind"] for item in proposal["relations"])

    if cell == "G1_SINGLE":
        _require(kinds == Counter(positive=1) and not proposal["relations"], "ASSIGNMENT_MISMATCH", cell)
    elif cell == "G2_COMPOUND_INDEPENDENT":
        _require(2 <= kinds["positive"] <= 3 and sum(kinds.values()) == kinds["positive"], "ASSIGNMENT_MISMATCH", cell)
        independent = _relations(proposal, "independent")
        _require(len(independent) == 1 and set(independent[0]["members"]) == ids and len(proposal["relations"]) == 1,
                 "RELATION_MISMATCH", cell)
    elif cell == "G3_COMPOUND_DEPENDENT":
        _require(2 <= kinds["positive"] <= 3 and sum(kinds.values()) == kinds["positive"], "ASSIGNMENT_MISMATCH", cell)
        consumes = _relations(proposal, "consumes")
        _require(consumes and all(len(item["members"]) == 2 for item in consumes), "RELATION_MISMATCH", cell)
    elif cell == "G4_COVERAGE_BOUNDARY":
        if proposal["subtype"] == "mixed":
            _require(kinds["positive"] >= 1 and kinds["outside"] >= 1 and kinds["control"] == 0,
                     "ASSIGNMENT_MISMATCH", cell)
        elif proposal["subtype"] == "outside_only":
            _require(kinds == Counter(outside=sum(kinds.values())), "ASSIGNMENT_MISMATCH", cell)
        else:
            raise PreflightError("ASSIGNMENT_MISMATCH", "G4 subtype")
    elif cell == "G5_LINGUISTIC_VARIATION":
        _require(kinds["positive"] >= 1 and sum(kinds.values()) == kinds["positive"], "ASSIGNMENT_MISMATCH", cell)
        _require(bool(proposal["surface_constraints"]), "ASSIGNMENT_MISMATCH", "G5 surface")
    elif cell == "S1_APPROVAL":
        _require(kinds["positive"] >= 1 and sum(kinds.values()) == kinds["positive"], "ASSIGNMENT_MISMATCH", cell)
        approval = _relations(proposal, "approval_owns")
        _require(len(approval) == 1 and set(approval[0]["members"]) <= ids, "RELATION_MISMATCH", cell)
    elif cell == "S2_NEGATION":
        _require(kinds["positive"] >= 1 and kinds["negated"] >= 1 and kinds["outside"] == kinds["control"] == 0,
                 "ASSIGNMENT_MISMATCH", cell)
    elif cell == "S3_CONDITIONAL_BRANCH":
        _require(kinds["positive"] >= 2 and sum(kinds.values()) == kinds["positive"], "ASSIGNMENT_MISMATCH", cell)
        true = _relations(proposal, "branch_true")
        false = _relations(proposal, "branch_false")
        _require(len(true) == len(false) == 1 and set(true[0]["members"]).isdisjoint(false[0]["members"]),
                 "RELATION_MISMATCH", cell)
        _require(set(true[0]["members"]) | set(false[0]["members"]) == ids, "RELATION_MISMATCH", cell)
    elif cell == "S4_UNDO":
        _require(kinds == Counter(control=1) and proposal["surface_constraints"] == ["undo_only"],
                 "ASSIGNMENT_MISMATCH", cell)
    elif cell == "S5_MIXED_CONTROL":
        _require(kinds["control"] == 1 and kinds["positive"] >= 1 and kinds["outside"] == kinds["negated"] == 0,
                 "ASSIGNMENT_MISMATCH", cell)
    elif cell == "S6_FALSE_ACTION_TRAP":
        _require(kinds == Counter(outside=sum(kinds.values())), "ASSIGNMENT_MISMATCH", cell)
    else:
        raise PreflightError("ASSIGNMENT_MISMATCH", "unknown cell")

    # Relations are closed to what each cell may use.
    allowed = {
        "G1_SINGLE": set(), "G2_COMPOUND_INDEPENDENT": {"independent"},
        "G3_COMPOUND_DEPENDENT": {"consumes", "explicit_order"},
        "G4_COVERAGE_BOUNDARY": {"independent", "consumes", "explicit_order"},
        "G5_LINGUISTIC_VARIATION": {"independent", "consumes", "explicit_order"},
        "S1_APPROVAL": {"approval_owns", "independent", "consumes", "explicit_order"},
        "S2_NEGATION": {"independent", "consumes", "explicit_order"},
        "S3_CONDITIONAL_BRANCH": {"branch_true", "branch_false", "explicit_order"},
        "S4_UNDO": set(), "S5_MIXED_CONTROL": {"independent", "explicit_order"},
        "S6_FALSE_ACTION_TRAP": set(),
    }[cell]
    _require(set(relation_kinds) <= allowed, "RELATION_MISMATCH", "relation not allowed")


def derive_safety(proposal: dict[str, Any]) -> dict[str, bool]:
    kinds = _kinds(proposal)
    relation_kinds = {item["kind"] for item in proposal["relations"]}
    approval_members = set()
    for item in _relations(proposal, "approval_owns"):
        approval_members.update(item["members"])
    positive_ids = {item["obligation_id"] for item in proposal["obligations"] if item["kind"] == "positive"}
    approval = bool(approval_members)
    different_approval_scopes = approval and bool(positive_ids - approval_members)
    branch = bool({"branch_true", "branch_false"} & relation_kinds)
    return {
        "approval": approval,
        "branch_ownership": branch or different_approval_scopes,
        "false_action": bool(kinds["negated"] or kinds["outside"] or proposal["cell"] == "S5_MIXED_CONTROL"),
        "negation": bool(kinds["negated"]),
        "ordering": bool({"consumes", "explicit_order", "branch_true", "branch_false"} & relation_kinds),
        "system_control": bool(kinds["control"]),
        "undo": any(item.get("control") == "undo_last_turn" for item in proposal["obligations"]),
    }


def _expected_kind_for(proposal: dict[str, Any]) -> tuple[str, str | None]:
    cell = proposal["cell"]
    if cell in {"G4_COVERAGE_BOUNDARY", "S6_FALSE_ACTION_TRAP"}:
        return "unrepresentable", "outside_registry"
    if cell == "S3_CONDITIONAL_BRANCH":
        return "unrepresentable", "unsupported_dependency"
    if cell == "S5_MIXED_CONTROL":
        return "unrepresentable", "mixed_root_kinds"
    if cell == "S4_UNDO":
        return "system_control", None
    return "operation_graph", None


def _flatten_expected(document: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, str]]:
    operations: list[dict[str, Any]] = []
    owners: dict[int, str] = {}

    def visit(body: list[dict[str, Any]], owner: str) -> None:
        for node in body:
            if node["kind"] == "operation":
                ordinal = len(operations)
                operations.append(node)
                owners[ordinal] = owner
            else:
                for case in node["cases"]:
                    if case["outcome"] == "approved":
                        visit(case["body"], "approval")

    if document.get("kind") == "operation_graph":
        visit(document["body"], "outer")
    return operations, owners


def validate_gold(gold: dict[str, Any], proposal: dict[str, Any], artifacts: dict[str, Any]) -> None:
    _schema_validate("semantic_gold", gold, artifacts)
    proposal_hash = build.payload_sha(proposal)
    _require(gold["proposal_sha256"] == proposal_hash, "HASH_BINDING", "gold proposal")
    _require(gold["authority_manifest_sha256"] == authority_sha(artifacts), "HASH_BINDING", "gold authority")
    registry = build.load_json(build.REGISTRY)
    result = validate_model_document(gold["expected"], registry)
    _require(result.valid, "GOLD_MISMATCH", repr(result.issues))
    _require(gold["safety_applicability"] == derive_safety(proposal), "GOLD_MISMATCH", "safety")
    expected_kind, expected_reason = _expected_kind_for(proposal)
    document = gold["expected"]
    _require(document["kind"] == expected_kind, "GOLD_MISMATCH", "root")
    if expected_reason is not None:
        _require(document.get("reason") == expected_reason, "GOLD_MISMATCH", "reason")
        return
    if expected_kind == "system_control":
        _require(document.get("control") == "undo_last_turn", "GOLD_MISMATCH", "control")
        return

    operations, owners = _flatten_expected(document)
    positives = [item for item in proposal["obligations"] if item["kind"] == "positive"]
    positive_by_id = {item["obligation_id"]: item for item in positives}
    expected_routes = [positive_by_id[item]["capability"] for item in proposal["canonical_mention_order"] if item in positive_by_id]
    _require([item["route"] for item in operations] == expected_routes, "GOLD_MISMATCH", "routes/order")
    negated = {item["capability"] for item in proposal["obligations"] if item["kind"] == "negated"}
    _require(not negated.intersection(item["route"] for item in operations), "GOLD_MISMATCH", "negated emitted")

    if proposal["cell"] == "G2_COMPOUND_INDEPENDENT":
        _require(all("data_from" not in item for item in operations), "GOLD_MISMATCH", "independent edge")
    if proposal["cell"] == "G3_COMPOUND_DEPENDENT":
        index = {obligation_id: ordinal for ordinal, obligation_id in enumerate(
            item for item in proposal["canonical_mention_order"] if item in positive_by_id
        )}
        for relation in _relations(proposal, "consumes"):
            source, target = relation["members"]
            _require(index[source] < index[target], "RELATIONAL_MISMATCH", "producer order")
            refs = {edge["from"] for edge in operations[index[target]].get("data_from", [])}
            _require(index[source] in refs, "GOLD_MISMATCH", "missing data edge")
    if proposal["cell"] == "S1_APPROVAL":
        approved = set(_relations(proposal, "approval_owns")[0]["members"])
        for ordinal, obligation_id in enumerate(item for item in proposal["canonical_mention_order"] if item in positive_by_id):
            wanted = "approval" if obligation_id in approved else "outer"
            _require(owners[ordinal] == wanted, "GOLD_MISMATCH", "approval ownership")


def fingerprint(proposal: dict[str, Any]) -> str:
    obligations = []
    for item in proposal["obligations"]:
        value = [item["kind"]]
        if "capability" in item:
            value.append(item["capability"])
        elif "outside_family" in item:
            value.append(item["outside_family"])
        else:
            value.append(item["control"])
        obligations.append(value)
    payload = {
        "cell": proposal["cell"],
        "subtype": proposal["subtype"],
        "obligations": obligations,
        "relations": proposal["relations"],
        "mention_order": proposal["canonical_mention_order"],
        "scenario_taxonomy": proposal["scenario_taxonomy"],
        "argument_roles": sorted(proposal["argument_roles"]),
    }
    return build.payload_sha(payload)


def validate_fingerprint_set(proposals: list[dict[str, Any]]) -> None:
    values = [fingerprint(item) for item in proposals]
    _require(len(values) == len(set(values)), "FINGERPRINT_COLLISION")


def validate_assignment(proposal: dict[str, Any], slot: dict[str, Any]) -> None:
    _require(
        proposal["proposal_id"] == slot["proposal_id"]
        and proposal["language_tag"] == slot["language_tag"]
        and proposal["cell"] == slot["cell"]
        and proposal["subtype"] == slot["subtype"],
        "ASSIGNMENT_MISMATCH",
    )


def build_projection(proposal: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    validate_proposal(proposal, artifacts)
    glossary = artifacts["capability_glossary.json"]
    outside_gloss = {
        "physical_world_action": "an indispensable physical-world action not present in the digital capability set",
        "financial_transaction": "an indispensable payment, purchase, transfer, or financial transaction",
        "expert_judgement": "an indispensable medical, legal, ethical, or subjective expert judgment",
        "unregistered_device_control": "indispensable control of a device or vehicle absent from the capability set",
        "unregistered_external_service": "an indispensable booking, order, or account action through an unregistered service",
        "unsupported_content_transformation": "an indispensable content transformation not covered by any exact capability",
        "unsupported_entity_creation": "indispensable creation of an entity for which no creation capability exists",
    }
    obligation_glosses = []
    for item in proposal["obligations"]:
        if "capability" in item:
            neutral = glossary["operations"][item["capability"]]["author_gloss"]
        elif "outside_family" in item:
            neutral = outside_gloss[item["outside_family"]]
        else:
            neutral = glossary["system_controls"][item["control"]]["author_gloss"]
        obligation_glosses.append({
            "obligation_ref": item["obligation_id"], "kind": item["kind"], "neutral_gloss": neutral
        })
    relation_glosses = [
        f"{item['kind']} applies to obligation references {','.join(item['members'])}"
        for item in proposal["relations"]
    ]
    result = {
        "format": "metnos.intent-holdout-author-projection/0.1",
        "proposal_sha256": build.payload_sha(proposal),
        "authority_manifest_sha256": authority_sha(artifacts),
        "language_tag": proposal["language_tag"],
        "obligation_glosses": obligation_glosses,
        "relation_glosses": relation_glosses,
        "canonical_mention_order": proposal["canonical_mention_order"],
        "surface_constraints": proposal["surface_constraints"],
    }
    _schema_validate("author_projection", result, artifacts)
    serialized = build.canonical_bytes(result)
    for route in glossary["operations"]:
        _require(route.encode("utf-8") not in serialized, "PROJECTION_LEAK", route)
    _require(b'"expected"' not in serialized and b'"reason"' not in serialized, "PROJECTION_LEAK", "gold")
    return result


def validate_judgment(
    judgment: dict[str, Any], *, proposal: dict[str, Any], gold: dict[str, Any],
    query_sha256: str, artifacts: dict[str, Any]
) -> None:
    _schema_validate("post_query_judgment", judgment, artifacts)
    _require(judgment["query_sha256"] == query_sha256, "QUERY_BINDING")
    _require(judgment["proposal_sha256"] == build.payload_sha(proposal), "CASE_BINDING")
    _require(judgment["authority_manifest_sha256"] == authority_sha(artifacts), "HASH_BINDING")
    _require(judgment["expected"] == gold["expected"], "GOLD_MISMATCH", "expected")
    _require(judgment["safety_applicability"] == gold["safety_applicability"], "GOLD_MISMATCH", "safety")
    proposed_obligations = proposal["obligations"]
    actual_obligations = judgment["reconstructed_obligations"]
    proposed_ids = [item["obligation_id"] for item in proposed_obligations]
    actual_ids = [item["obligation_id"] for item in actual_obligations]
    _require(actual_ids == proposed_ids, "OBLIGATION_MISMATCH")
    _require(actual_obligations == proposed_obligations, "SEMANTIC_MISMATCH")
    _require(judgment["reconstructed_relations"] == proposal["relations"], "RELATION_MISMATCH")
    _require(judgment["canonical_mention_order"] == proposal["canonical_mention_order"], "RELATION_MISMATCH")
    _require(
        judgment["scenario_taxonomy"] == proposal["scenario_taxonomy"]
        and judgment["argument_roles"] == proposal["argument_roles"],
        "RECONSTRUCTION_MISMATCH",
    )


def validate_envelope(
    envelope: dict[str, Any], *, proposal: dict[str, Any], projection: dict[str, Any],
    author_bundle_sha256: str, gold: dict[str, Any], query_sha256: str,
    judgments: list[dict[str, Any]], artifacts: dict[str, Any]
) -> None:
    _schema_validate("post_query_envelope", envelope, artifacts)
    _require(len(judgments) == 2, "CLOSED_SCHEMA", "two judgments")
    for judgment in judgments:
        validate_judgment(judgment, proposal=proposal, gold=gold, query_sha256=query_sha256, artifacts=artifacts)
    _require(judgments[0] == judgments[1], "GOLD_MISMATCH", "reviewers disagree")
    expected = {
        "format": "metnos.intent-holdout-post-query-envelope/0.1",
        "proposal_sha256": build.payload_sha(proposal),
        "projection_sha256": build.payload_sha(projection),
        "author_bundle_sha256": author_bundle_sha256,
        "gold_sha256": build.payload_sha(gold),
        "query_sha256": query_sha256,
        "judgment_sha256": [build.payload_sha(item) for item in judgments],
        "authority_manifest_sha256": authority_sha(artifacts),
        "exact_match": True,
    }
    _require(envelope == expected, "HASH_BINDING", "envelope")


def validate_lifecycle(records: list[dict[str, Any]]) -> None:
    author_contexts: set[str] = set()
    reviewer_contexts: dict[str, str] = {}
    for record in records:
        _require(record.get("forbidden_reads", 0) == 0, "LIFECYCLE_VIOLATION", "forbidden read")
        _require(record.get("postseal_edits", 0) == 0, "LIFECYCLE_VIOLATION", "postseal edit")
        if record["role"] == "author":
            context = record["context_id"]
            _require(context not in author_contexts and record.get("query_count") == 1,
                     "ISOLATION_REUSE", "author context")
            author_contexts.add(context)
        elif record["role"] in {"pool_a", "pool_b"}:
            context = record["context_id"]
            previous = reviewer_contexts.get(context)
            _require(previous in {None, record["role"]}, "ISOLATION_REUSE", "reviewer crosses pools")
            reviewer_contexts[context] = record["role"]


def preflight() -> dict[str, Any]:
    errors = build.check()
    _require(not errors, "BUILD_DRIFT", repr(errors))
    artifacts = load_artifacts()
    validate_authority(artifacts["authority_manifest.json"])
    validate_glossary(artifacts["capability_glossary.json"])
    validate_schemas(artifacts["artifact_schemas.json"])
    validate_rubrics(artifacts["review_rubrics.json"])
    validate_pilot_matrix(artifacts["pilot_matrix.json"])
    validate_mutation_catalog(artifacts["mutation_catalog.json"])
    forbidden = [path for name in ("queries", "authors", "gold", "run") if (HERE / name).exists()
                 for path in [HERE / name]]
    _require(not forbidden, "PREMATURE_ARTIFACT", repr(forbidden))
    return {
        "status": "ready_for_synthetic_tests",
        "errors": [],
        "queries": 0,
        "case_gold": 0,
        "runs": 0,
        "pilot_slots": 20,
        "authority_manifest_sha256": authority_sha(artifacts),
    }


def main() -> int:
    try:
        result = preflight()
    except PreflightError as exc:
        print(json.dumps({"status": "fail", "code": exc.code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
