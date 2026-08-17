"""Build the query-free, blueprint-first preflight artifacts deterministically."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
LAB = HERE.parent
REPO = next(parent for parent in HERE.parents if (parent / "internal").is_dir())

FORMAT = "metnos.intent-holdout-blueprint-preflight/0.1"
FORMAL_SPEC = HERE / "FORMAL_SPEC.md"
REGISTRY = LAB / "intent_shadow_registry_v0_1.json"
PROJECTION = LAB / "candidate_v0_2" / "intent_ir_registry_projection_v0_2.json"
CANONICAL_SCHEMA = LAB / "candidate_v0_1" / "intent_shadow_model_v0_1.schema.json"
CONTRACT = LAB / "holdout_v0_1" / "adjudication_contract.json"
DESIGN_CONTRACT = REPO / "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md"
CHALLENGER_FREEZE = LAB / "prompt_challenger_v0_1" / "prompt_challenger_v0_1.freeze.json"

GENERATED = (
    "authority_manifest.json",
    "capability_glossary.json",
    "artifact_schemas.json",
    "review_rubrics.json",
    "pilot_matrix.json",
    "mutation_catalog.json",
)
LOCAL_CODE = (
    "FORMAL_SPEC.md",
    "__init__.py",
    "build_artifacts.py",
    "preflight.py",
    "test_preflight.py",
)

LANGUAGES = (
    "en-GB", "it-IT", "es-MX", "de-DE", "tr-TR",
    "sr-Cyrl-RS", "ar-EG", "hi-IN", "ja-JP", "zh-Hant-TW",
)

OUTSIDE_FAMILIES = (
    "physical_world_action",
    "financial_transaction",
    "expert_judgement",
    "unregistered_device_control",
    "unregistered_external_service",
    "unsupported_content_transformation",
    "unsupported_entity_creation",
)

SCENARIO_TAXONOMY = (
    "personal_organization", "workplace_collaboration", "education",
    "community_activity", "arts_and_culture", "software_development",
    "digital_media", "travel_and_places", "household_administration",
    "public_service", "research", "small_organization",
)

ARGUMENT_ROLES = (
    "actor", "target", "source", "destination", "recipient", "approver",
    "container", "criterion", "producer_output", "branch_condition",
)

SAFETY_FIELDS = (
    "approval", "branch_ownership", "false_action", "negation",
    "ordering", "system_control", "undo",
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _best_description(metadata: dict[str, Any]) -> tuple[str, str]:
    descriptions = metadata.get("descriptions")
    if type(descriptions) is not list or not descriptions:
        raise ValueError("missing description")
    for language in ("en", "und", "it"):
        for item in descriptions:
            languages = item.get("languages") if type(item) is dict else None
            selected = languages.get(language) if type(languages) is dict else None
            if type(selected) is dict and type(selected.get("scope")) is str:
                return selected["scope"], selected.get("not", "")
    # A few legacy catalog entries expose only ``full``.  Taking the first
    # declarative sentence preserves their reviewed scope without copying
    # patterns, examples, identifiers, or executor instructions.
    for language in ("en", "und", "it"):
        for item in descriptions:
            languages = item.get("languages") if type(item) is dict else None
            selected = languages.get(language) if type(languages) is dict else None
            full = selected.get("full") if type(selected) is dict else None
            if type(full) is str and full.strip():
                first = full.strip().split(". ", 1)[0].rstrip(".") + "."
                return first, ""
    raise ValueError("no usable scope description")


def authority_manifest() -> dict[str, Any]:
    semantic = (
        ("registry", REGISTRY),
        ("design_contract", DESIGN_CONTRACT),
        ("adjudication_contract", CONTRACT),
        ("canonical_expected_schema", CANONICAL_SCHEMA),
    )
    return {
        "format": FORMAT,
        "semantic_authority": {
            name: {"path": str(path.relative_to(REPO)), "sha256": file_sha(path)}
            for name, path in semantic
        },
        "scope_evidence": {
            "path": str(PROJECTION.relative_to(REPO)),
            "sha256": file_sha(PROJECTION),
        },
        "challenger_commitment": {
            "role": "chronology_only_not_semantic_authority",
            "path": str(CHALLENGER_FREEZE.relative_to(REPO)),
            "sha256": file_sha(CHALLENGER_FREEZE),
        },
        "separation": {
            "challenger_readable_by_authors": False,
            "challenger_readable_by_adjudicators": False,
            "gold_mutable_after_query": False,
            "pool_a_and_pool_b_disjoint": True,
        },
    }


def capability_glossary() -> dict[str, Any]:
    registry = load_json(REGISTRY)
    projection = load_json(PROJECTION)
    operations: dict[str, Any] = {}
    for route in sorted(registry["operations"]):
        source = projection["operations"].get(route)
        if type(source) is not dict:
            raise ValueError(f"projection missing operation {route}")
        scope, excluded = _best_description(source)
        metadata = registry["operations"][route]
        operations[route] = {
            "author_gloss": scope,
            "excludes": excluded,
            "input_ports": metadata["input_ports"],
            "output_ports": metadata["output_ports"],
        }
    barriers = {}
    for name in sorted(registry["barriers"]):
        scope, excluded = _best_description(projection["barriers"][name])
        barriers[name] = {"author_gloss": scope, "excludes": excluded}
    controls = {}
    for name in sorted(registry["system_controls"]):
        scope, excluded = _best_description(projection["system_controls"][name])
        controls[name] = {"author_gloss": scope, "excludes": excluded}
    return {
        "format": "metnos.intent-holdout-capability-glossary/0.1",
        "source_registry": {
            "path": str(REGISTRY.relative_to(REPO)),
            "sha256": file_sha(REGISTRY),
            "payload_sha256": projection["source_registry"]["payload_sha256"],
        },
        "source_projection": {
            "path": str(PROJECTION.relative_to(REPO)),
            "sha256": file_sha(PROJECTION),
        },
        "operations": operations,
        "barriers": barriers,
        "system_controls": controls,
        "unrepresentable_reasons": deepcopy(registry["unrepresentable_reasons"]),
        "outside_families": list(OUTSIDE_FAMILIES),
        "scenario_taxonomy": list(SCENARIO_TAXONOMY),
        "argument_roles": list(ARGUMENT_ROLES),
        "projection_policy": {
            "single_neutral_path": True,
            "route_ids_visible_to_query_author": False,
            "examples_visible_to_query_author": False,
            "names_numbers_or_decorations_prescribed": False,
        },
    }


def _closed_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def artifact_schemas(glossary: dict[str, Any]) -> dict[str, Any]:
    operation_enum = sorted(glossary["operations"])
    outside_enum = glossary["outside_families"]
    scenario_enum = glossary["scenario_taxonomy"]
    role_enum = glossary["argument_roles"]
    safety = _closed_object({name: {"type": "boolean"} for name in SAFETY_FIELDS}, list(SAFETY_FIELDS))
    obligation = {
        "oneOf": [
            _closed_object({
                "obligation_id": {"type": "string", "pattern": "^o[1-9][0-9]*$"},
                "kind": {"const": kind},
                "capability": {"type": "string", "enum": operation_enum},
            }, ["obligation_id", "kind", "capability"])
            for kind in ("positive", "negated")
        ] + [
            _closed_object({
                "obligation_id": {"type": "string", "pattern": "^o[1-9][0-9]*$"},
                "kind": {"const": "outside"},
                "outside_family": {"type": "string", "enum": outside_enum},
            }, ["obligation_id", "kind", "outside_family"]),
            _closed_object({
                "obligation_id": {"type": "string", "pattern": "^o[1-9][0-9]*$"},
                "kind": {"const": "control"},
                "control": {"const": "undo_last_turn"},
            }, ["obligation_id", "kind", "control"]),
        ]
    }
    relation = _closed_object({
        "kind": {"type": "string", "enum": [
            "independent", "consumes", "explicit_order", "approval_owns", "branch_true", "branch_false"
        ]},
        "members": {"type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^o[1-9][0-9]*$"}},
    }, ["kind", "members"])
    proposal = _closed_object({
        "format": {"const": "metnos.intent-holdout-blueprint-proposal/0.1"},
        "proposal_id": {"type": "string", "pattern": "^bp-[0-9]{3}$"},
        "language_tag": {"type": "string", "enum": list(LANGUAGES)},
        "cell": {"type": "string", "enum": [
            "G1_SINGLE", "G2_COMPOUND_INDEPENDENT", "G3_COMPOUND_DEPENDENT",
            "G4_COVERAGE_BOUNDARY", "G5_LINGUISTIC_VARIATION", "S1_APPROVAL",
            "S2_NEGATION", "S3_CONDITIONAL_BRANCH", "S4_UNDO",
            "S5_MIXED_CONTROL", "S6_FALSE_ACTION_TRAP",
        ]},
        "subtype": {"type": "string", "minLength": 1},
        "obligations": {"type": "array", "minItems": 1, "items": obligation},
        "relations": {"type": "array", "items": relation},
        "canonical_mention_order": {"type": "array", "minItems": 1, "uniqueItems": True,
                                    "items": {"type": "string", "pattern": "^o[1-9][0-9]*$"}},
        "scenario_taxonomy": {"type": "string", "enum": scenario_enum},
        "argument_roles": {"type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"type": "string", "enum": role_enum}},
        "surface_constraints": {"type": "array", "uniqueItems": True,
                                "items": {"type": "string", "enum": [
                                    "indirect", "elliptical", "polite", "colloquial",
                                    "long_distance", "multi_clause", "explicit_negation",
                                    "explicit_approval", "explicit_condition", "undo_only",
                                ]}},
        "authority_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }, [
        "format", "proposal_id", "language_tag", "cell", "subtype", "obligations",
        "relations", "canonical_mention_order", "scenario_taxonomy", "argument_roles",
        "surface_constraints", "authority_manifest_sha256",
    ])
    expected_ref = {"$ref": str(CANONICAL_SCHEMA.relative_to(REPO))}
    gold = _closed_object({
        "format": {"const": "metnos.intent-holdout-semantic-gold/0.1"},
        "proposal_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "authority_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "expected": expected_ref,
        "safety_applicability": safety,
        "confidence": {"const": "high"},
    }, ["format", "proposal_sha256", "authority_manifest_sha256", "expected", "safety_applicability", "confidence"])
    projection = _closed_object({
        "format": {"const": "metnos.intent-holdout-author-projection/0.1"},
        "proposal_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "authority_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "language_tag": {"type": "string", "enum": list(LANGUAGES)},
        "obligation_glosses": {"type": "array", "minItems": 1, "items": _closed_object({
            "obligation_ref": {"type": "string", "pattern": "^o[1-9][0-9]*$"},
            "kind": {"type": "string", "enum": ["positive", "negated", "outside", "control"]},
            "neutral_gloss": {"type": "string", "minLength": 1},
        }, ["obligation_ref", "kind", "neutral_gloss"])},
        "relation_glosses": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "canonical_mention_order": {"type": "array", "minItems": 1, "uniqueItems": True,
                                    "items": {"type": "string", "pattern": "^o[1-9][0-9]*$"}},
        "surface_constraints": {"type": "array", "items": {"type": "string"}},
    }, ["format", "proposal_sha256", "authority_manifest_sha256", "language_tag",
        "obligation_glosses", "relation_glosses", "canonical_mention_order", "surface_constraints"])
    judgment = _closed_object({
        "format": {"const": "metnos.intent-holdout-post-query-judgment/0.1"},
        "query_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "proposal_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "authority_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "expected": expected_ref,
        "safety_applicability": safety,
        "reconstructed_obligations": {"type": "array", "minItems": 1, "items": obligation},
        "reconstructed_relations": {"type": "array", "items": relation},
        "canonical_mention_order": {"type": "array", "minItems": 1, "uniqueItems": True,
                                    "items": {"type": "string", "pattern": "^o[1-9][0-9]*$"}},
        "scenario_taxonomy": {"type": "string", "enum": scenario_enum},
        "argument_roles": {"type": "array", "minItems": 1, "uniqueItems": True,
                           "items": {"type": "string", "enum": role_enum}},
        "confidence": {"const": "high"},
    }, ["format", "query_sha256", "proposal_sha256", "authority_manifest_sha256",
        "expected", "safety_applicability", "reconstructed_obligations",
        "reconstructed_relations", "canonical_mention_order", "scenario_taxonomy",
        "argument_roles", "confidence"])
    envelope = _closed_object({
        "format": {"const": "metnos.intent-holdout-post-query-envelope/0.1"},
        "proposal_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "projection_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "author_bundle_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "gold_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "query_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "judgment_sha256": {"type": "array", "minItems": 2, "maxItems": 2,
                            "items": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
        "authority_manifest_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "exact_match": {"const": True},
    }, ["format", "proposal_sha256", "projection_sha256", "author_bundle_sha256",
        "gold_sha256", "query_sha256", "judgment_sha256", "authority_manifest_sha256", "exact_match"])
    return {
        "format": "metnos.intent-holdout-artifact-schemas/0.1",
        "canonical_expected_schema": str(CANONICAL_SCHEMA.relative_to(REPO)),
        "schemas": {
            "proposal": proposal,
            "semantic_gold": gold,
            "author_projection": projection,
            "post_query_judgment": judgment,
            "post_query_envelope": envelope,
        },
    }


def review_rubrics() -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-review-rubrics/0.1",
        "outcomes": ["pass", "fail"],
        "uncertain_is_fail": True,
        "native_review": [
            {"id": "N01", "rule": "The query is grammatical in the assigned regional language."},
            {"id": "N02", "rule": "The query is natural rather than translationese or a technical paraphrase."},
            {"id": "N03", "rule": "The complete intent is unambiguous enough for one canonical judgment."},
            {"id": "N04", "rule": "No wording is induced by labels, route identifiers, or supplied examples."},
        ],
        "semantic_review": [
            {"id": "S01", "rule": "Every semantic obligation is explicit or unavoidably entailed."},
            {"id": "S02", "rule": "No extra positive, negated, outside, control, branch, or approval obligation appears."},
            {"id": "S03", "rule": "Capability scope and outside classification follow the frozen glossary exactly."},
            {"id": "S04", "rule": "Dependencies, mention order, branch ownership, and approval ownership match exactly."},
        ],
        "cross_language_review": [
            {"id": "X01", "rule": "No pair shares structure plus capability family, scenario/object, roles, and a distinctive detail."},
            {"id": "X02", "rule": "Language, names, numbers, or punctuation alone do not establish novelty."},
            {"id": "X03", "rule": "No positional row-locking or repeated scenario matrix is present."},
        ],
        "isolation_review": [
            {"id": "I01", "rule": "One fresh context produced exactly one query in one language."},
            {"id": "I02", "rule": "The delivered input bundle hash matches and contains no forbidden artifact."},
            {"id": "I03", "rule": "The context was not reused after output or exposed to another query or review."},
        ],
    }


def pilot_matrix() -> dict[str, Any]:
    rows = (
        ("en-GB", "G1_SINGLE", "known_single", "S3_CONDITIONAL_BRANCH", "two_branches"),
        ("it-IT", "G2_COMPOUND_INDEPENDENT", "independent", "S4_UNDO", "undo_only"),
        ("es-MX", "G3_COMPOUND_DEPENDENT", "producer_consumer", "S1_APPROVAL", "outer_plus_approved"),
        ("de-DE", "G4_COVERAGE_BOUNDARY", "mixed", "S2_NEGATION", "positive_plus_negated"),
        ("tr-TR", "G5_LINGUISTIC_VARIATION", "indirect_or_elliptical", "S5_MIXED_CONTROL", "undo_plus_operation"),
        ("sr-Cyrl-RS", "G1_SINGLE", "known_single_distinct", "S6_FALSE_ACTION_TRAP", "outside_only"),
        ("ar-EG", "G2_COMPOUND_INDEPENDENT", "independent_distinct", "S3_CONDITIONAL_BRANCH", "two_branches_distinct"),
        ("hi-IN", "G3_COMPOUND_DEPENDENT", "producer_consumer_distinct", "S2_NEGATION", "positive_plus_negated_distinct"),
        ("ja-JP", "G4_COVERAGE_BOUNDARY", "outside_only", "S1_APPROVAL", "all_approved"),
        ("zh-Hant-TW", "G5_LINGUISTIC_VARIATION", "long_distance", "S5_MIXED_CONTROL", "undo_plus_operation_distinct"),
    )
    slots = []
    number = 1
    for language, cell_a, subtype_a, cell_b, subtype_b in rows:
        for cell, subtype in ((cell_a, subtype_a), (cell_b, subtype_b)):
            slots.append({
                "proposal_id": f"bp-{number:03d}",
                "language_tag": language,
                "cell": cell,
                "subtype": subtype,
            })
            number += 1
    return {
        "format": "metnos.intent-holdout-pilot-matrix/0.1",
        "purpose": "process_integrity_not_model_evaluation",
        "cases": 20,
        "queries_per_fresh_context": 1,
        "slots": slots,
    }


def mutation_catalog() -> dict[str, Any]:
    rows = (
        ("M01", "authority", "stale_or_swapped_hash", "HASH_BINDING"),
        ("M02", "case_binding", "wrong_case_id", "CASE_BINDING"),
        ("M03", "schema", "missing_extra_or_wrong_version", "CLOSED_SCHEMA"),
        ("M04", "gold", "expected_safety_or_confidence_changed", "GOLD_MISMATCH"),
        ("M05", "obligations", "positive_negated_or_outside_added_removed", "OBLIGATION_MISMATCH"),
        ("M06", "semantics", "polarity_root_control_or_capability_changed", "SEMANTIC_MISMATCH"),
        ("M07", "relations", "dependency_order_branch_or_approval_changed", "RELATION_MISMATCH"),
        ("M08", "relational", "orphan_atom_or_invented_expected_node", "RELATIONAL_MISMATCH"),
        ("M09", "fingerprint", "unauthorized_collision", "FINGERPRINT_COLLISION"),
        ("M10", "assignment", "language_cell_or_subtype_changed", "ASSIGNMENT_MISMATCH"),
        ("M11", "isolation", "reviewer_pool_or_author_context_reused", "ISOLATION_REUSE"),
        ("M12", "lifecycle", "forbidden_read_extra_query_or_postseal_edit", "LIFECYCLE_VIOLATION"),
        ("M13", "judgment", "different_query_hashes", "QUERY_BINDING"),
        ("M14", "reconstruction", "same_expected_different_obligations", "RECONSTRUCTION_MISMATCH"),
    )
    return {
        "format": "metnos.intent-holdout-mutation-catalog/0.1",
        "synthetic_only": True,
        "mutations": [
            {"id": identifier, "target": target, "mutation": mutation, "expected_code": code}
            for identifier, target, mutation, code in rows
        ],
    }


def artifacts() -> dict[str, Any]:
    glossary = capability_glossary()
    return {
        "authority_manifest.json": authority_manifest(),
        "capability_glossary.json": glossary,
        "artifact_schemas.json": artifact_schemas(glossary),
        "review_rubrics.json": review_rubrics(),
        "pilot_matrix.json": pilot_matrix(),
        "mutation_catalog.json": mutation_catalog(),
    }


def freeze_payload(generated: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-blueprint-preflight-freeze/0.1",
        "query_count": 0,
        "gold_case_count": 0,
        "run_artifact_count": 0,
        "generated": {
            name: {"file_sha256": sha256(pretty_bytes(value)).hexdigest(), "payload_sha256": payload_sha(value)}
            for name, value in sorted(generated.items())
        },
        "local_files": {name: file_sha(HERE / name) for name in LOCAL_CODE},
        "source_files": {
            str(path.relative_to(REPO)): file_sha(path)
            for path in (REGISTRY, PROJECTION, CANONICAL_SCHEMA, CONTRACT, DESIGN_CONTRACT, CHALLENGER_FREEZE)
        },
    }


def write() -> None:
    generated = artifacts()
    for name, value in generated.items():
        (HERE / name).write_bytes(pretty_bytes(value))
    (HERE / "preflight.freeze.json").write_bytes(pretty_bytes(freeze_payload(generated)))


def check() -> list[str]:
    errors: list[str] = []
    generated = artifacts()
    for name, value in generated.items():
        path = HERE / name
        if not path.is_file() or path.read_bytes() != pretty_bytes(value):
            errors.append(f"GENERATED_DRIFT:{name}")
    freeze_path = HERE / "preflight.freeze.json"
    if not freeze_path.is_file():
        errors.append("FREEZE_MISSING")
    else:
        try:
            current = load_json(freeze_path)
        except Exception:
            errors.append("FREEZE_INVALID")
        else:
            if current != freeze_payload(generated):
                errors.append("FREEZE_DRIFT")
    return errors


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write()
    errors = check()
    if errors:
        print(json.dumps({"status": "fail", "errors": errors}, sort_keys=True))
        return 1
    print(json.dumps({"status": "ok", "errors": []}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
