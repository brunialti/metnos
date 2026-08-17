"""Build the complete query-free v0.2 preflight package."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from engine import digest, digest_bytes, file_digest, load_json


HERE = Path(__file__).resolve().parent
LAB = HERE.parent
REPO = next(parent for parent in HERE.parents if (parent / "internal").is_dir())

REGISTRY = LAB / "intent_shadow_registry_v0_1.json"
PROJECTION_SOURCE = LAB / "candidate_v0_2/intent_ir_registry_projection_v0_2.json"
CANONICAL_SCHEMA = LAB / "candidate_v0_1/intent_shadow_model_v0_1.schema.json"
DESIGN = REPO / "internal/design/contratto_ombra_prototipo_intento_12_8_2026.md"
OLD_CONTRACT = LAB / "holdout_v0_1/adjudication_contract.json"
CHALLENGER = LAB / "prompt_challenger_v0_1/prompt_challenger_v0_1.freeze.json"

GENERATED = ("authority.json", "glossary.json", "rubrics.json", "pilot_matrix.json", "mutation_catalog.json")
LOCAL = ("__init__.py", "FORMAL_SPEC.md", "model.py", "engine.py", "build.py", "preflight.py", "test_engine.py", "test_mutations.py")
SOURCES = (REGISTRY, PROJECTION_SOURCE, CANONICAL_SCHEMA, DESIGN, OLD_CONTRACT, CHALLENGER)

LANGUAGES = ("en-GB", "it-IT", "es-MX", "de-DE", "tr-TR", "sr-Cyrl-RS", "ar-EG", "hi-IN", "ja-JP", "zh-Hant-TW")
OUTSIDE = {
    "physical_world_action": "perform an indispensable physical-world action absent from the digital capability set",
    "financial_transaction": "perform an indispensable purchase, payment, transfer, or financial transaction",
    "expert_judgement": "provide an indispensable medical, legal, ethical, or subjective expert judgment",
    "unregistered_device_control": "control a device or vehicle absent from the capability set",
    "unregistered_external_service": "book, order, or modify an account through an unregistered service",
    "unsupported_content_transformation": "perform a content transformation absent from the exact capability set",
    "unsupported_entity_creation": "create an entity for which no creation capability exists",
}

# The registry projection is the authority.  Most executor descriptions already
# provide a concise English scope.  These entries cover only legacy descriptions
# that have no English scope, plus three scopes containing executor syntax.  They
# describe capabilities, never benchmark cases.
SCOPE_GLOSS_BY_EXECUTOR = {
    "change_pulls_github": "merge or otherwise operate on an existing GitHub pull request.",
    "create_images_indices": "index photos in a selected local collection, incrementally or by rebuilding the index.",
    "create_issues_github": "create a new issue in a GitHub repository.",
    "create_tasks": "schedule a recurring task, reminder, or timer.",
    "create_tasks_github": "start a GitHub Actions workflow.",
    "delete_issues_github": "close or delete an existing GitHub issue.",
    "delete_messages_github": "delete a comment from a GitHub issue or pull request.",
    "delete_tasks": "delete a scheduled recurring task, reminder, or timer.",
    "filter_lists": "compare, intersect, subtract, or deduplicate two lists using selected matching fields.",
    "find_issues_github": "list issues, bug reports, or tickets in a GitHub repository.",
    "find_pulls_github": "list pull requests in a GitHub repository.",
    "list_skills": "list available capability modules and whether each is enabled.",
    "list_tasks": "list scheduled recurring tasks, reminders, and timers.",
    "login_session": "log in through a previously registered website session.",
    "read_files_csv": "read local CSV files.",
    "read_files_xlsx": "read local Excel spreadsheet files.",
    "read_issues_github": "read one GitHub issue, including its content and comments.",
    "read_pulls_github": "read one GitHub pull request, including its content and diff.",
    "read_tasks": "read the details of one recurring task, reminder, or timer.",
    "read_tasks_github": "list GitHub Actions workflow runs for a repository.",
    "read_tasks_history": "read execution history for recurring tasks, reminders, or timers.",
    "reply_messages": "reply within an existing Gmail message thread.",
    "send_messages_github": "post comments on GitHub issues or pull requests.",
    "set_issues_github": "change the state, labels, or assignees of an existing GitHub issue.",
    "set_pulls_github": "change the state, title, or reviewers of an existing GitHub pull request.",
    "set_skills": "enable or disable a capability module.",
    "set_tasks": "pause, resume, or immediately trigger an existing recurring task.",
    "write_files": "persist records or text to local files.",
}


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def _description_gloss(description: dict[str, Any]) -> str:
    executor = description["executor"]
    if executor in SCOPE_GLOSS_BY_EXECUTOR:
        return SCOPE_GLOSS_BY_EXECUTOR[executor]
    scope = description.get("languages", {}).get("en", {}).get("scope")
    if type(scope) is not str or not scope.strip():
        raise ValueError(f"missing neutral scope for {executor}")
    return scope.strip()


def _author_gloss(metadata: dict[str, Any]) -> str:
    scopes: list[str] = []
    for description in metadata["descriptions"]:
        scope = _description_gloss(description)
        if scope not in scopes:
            scopes.append(scope)
    if not scopes:
        raise ValueError("operation has no semantic scope")
    return " ".join(scopes)


def authority() -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-authority/0.2",
        "precedence": ["registry", "canonical_schema", "normative_projection", "design_provenance"],
        "semantic": {
            "registry": {"path": str(REGISTRY.relative_to(REPO)), "sha256": file_digest(REGISTRY)},
            "canonical_schema": {"path": str(CANONICAL_SCHEMA.relative_to(REPO)), "sha256": file_digest(CANONICAL_SCHEMA)},
            "design_provenance": {"path": str(DESIGN.relative_to(REPO)), "sha256": file_digest(DESIGN)},
        },
        "normative_projection": {
            "confidence": "high_only",
            "arbitration": "forbidden",
            "independent_operation_order": "query_mention_order_without_dependency",
            "complete_request": "indispensable_outside_clause_means_outside_registry",
            "mixed_root": "system_control_plus_operation_means_mixed_root_kinds",
            "conditional_branch": "two_unrepresentable_branches_mean_unsupported_dependency",
            "approval": "barrier_owns_exactly_marked_positive_operations",
            "negation": "negated_operations_are_not_emitted",
            "safety": {
                "approval": "approval relation exists",
                "branch_ownership": "conditional branch relation exists",
                "false_action": "negated or outside obligation exists",
                "negation": "negated obligation exists",
                "ordering": "consumes, explicit-order, or explicitly ordered conditional relation exists",
                "system_control": "control obligation exists",
                "undo": "undo_last_turn obligation exists",
            },
            "supersedes_old_contract_fields": ["agreement.confidence_may_differ", "agreement.disagreement_action"],
            "provenance": {"path": str(OLD_CONTRACT.relative_to(REPO)), "sha256": file_digest(OLD_CONTRACT)},
        },
        "scope_evidence": {"path": str(PROJECTION_SOURCE.relative_to(REPO)), "sha256": file_digest(PROJECTION_SOURCE)},
        "challenger_commitment": {
            "role": "chronology_only_not_semantic_authority",
            "path": str(CHALLENGER.relative_to(REPO)), "sha256": file_digest(CHALLENGER),
        },
    }


def glossary() -> dict[str, Any]:
    registry = load_json(REGISTRY)
    projection = load_json(PROJECTION_SOURCE)
    if set(projection["operations"]) != set(registry["operations"]):
        raise ValueError("registry/projection route mismatch")
    operations = {route: {"author_gloss": _author_gloss(projection["operations"][route]),
                          "input_ports": metadata["input_ports"], "output_ports": metadata["output_ports"]}
                  for route, metadata in sorted(registry["operations"].items())}
    return {
        "format": "metnos.intent-holdout-neutral-glossary/0.2",
        "source_registry_sha256": file_digest(REGISTRY),
        "operations": operations,
        "outside_families": deepcopy(OUTSIDE),
        "system_controls": {"undo_last_turn": {"author_gloss": "undo the immediately previous assistant turn."}},
        "barriers": {"get/approval": {"author_gloss": "request approval for the owned digital actions."}},
        "languages": list(LANGUAGES),
        "same_neutral_projection_for_every_language": True,
    }


def rubrics() -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-rubrics/0.2", "uncertain_is_fail": True,
        "native": ["grammatical regional language", "natural non-translationese wording", "one unambiguous complete intent"],
        "semantic": ["all and only obligations present", "scope exact", "relations exact", "expected and safety exact"],
        "cross_language": ["no translation or template matrix", "no novelty by names numbers language or punctuation", "no row locking"],
        "isolation": ["globally unique context and reviewer", "exact input bundle", "one query per author", "zero forbidden read network GPU or postseal edit"],
    }


def pilot_matrix() -> dict[str, Any]:
    data = (
        ("en-GB", "G1_SINGLE", "known_single", "S3_CONDITIONAL_BRANCH", "two_branches"),
        ("it-IT", "G2_COMPOUND_INDEPENDENT", "independent", "S4_UNDO", "undo_only"),
        ("es-MX", "G3_COMPOUND_DEPENDENT", "producer_consumer", "S1_APPROVAL", "outer_plus_approved"),
        ("de-DE", "G4_COVERAGE_BOUNDARY", "mixed", "S2_NEGATION", "positive_plus_negated"),
        ("tr-TR", "G5_LINGUISTIC_VARIATION", "indirect", "S5_MIXED_CONTROL", "undo_plus_operation"),
        ("sr-Cyrl-RS", "G1_SINGLE", "known_single", "S6_FALSE_ACTION_TRAP", "outside_only"),
        ("ar-EG", "G2_COMPOUND_INDEPENDENT", "independent", "S3_CONDITIONAL_BRANCH", "two_branches"),
        ("hi-IN", "G3_COMPOUND_DEPENDENT", "producer_consumer", "S2_NEGATION", "positive_plus_negated"),
        ("ja-JP", "G4_COVERAGE_BOUNDARY", "outside_only", "S1_APPROVAL", "all_approved"),
        ("zh-Hant-TW", "G5_LINGUISTIC_VARIATION", "long_distance", "S5_MIXED_CONTROL", "undo_plus_operation"),
    )
    slots = []
    index = 1
    for language, ca, sa, cb, sb in data:
        for cell, subtype in ((ca, sa), (cb, sb)):
            slots.append({"proposal_id": f"bp-{index:03d}", "language_tag": language, "cell": cell, "subtype": subtype})
            index += 1
    return {"format": "metnos.intent-holdout-pilot-matrix/0.2", "cases": 20, "query_per_context": 1, "slots": slots}


def mutation_catalog() -> dict[str, Any]:
    mutations = (
        ("M01", "HASH_BINDING", "source_or_case_hash_stale_or_swapped"),
        ("M02", "CLOSED_SCHEMA", "missing_extra_wrong_type_or_version"),
        ("M03", "CAPABILITY_SCOPE", "unknown_route_port_control_or_outside_family"),
        ("M04", "SUBTYPE_MISMATCH", "subtype_or_surface_contract_changed"),
        ("M05", "CELL_MISMATCH", "obligation_cardinality_or_kind_changed"),
        ("M06", "RELATION_MISMATCH", "dependency_order_branch_or_approval_changed"),
        ("M07", "GOLD_MISMATCH", "expected_safety_confidence_or_root_changed"),
        ("M08", "PROJECTION_MISMATCH", "projection_not_exactly_reconstructed"),
        ("M09", "PROJECTION_LEAK", "technical_or_gold_content_exposed"),
        ("M10", "BUNDLE_MISMATCH", "author_bundle_not_exactly_reconstructed"),
        ("M11", "FINGERPRINT_COLLISION", "semantic_collision_or_cosmetic_novelty"),
        ("M12", "POOL_IDENTITY", "duplicate_or_cross_pool_reviewer"),
        ("M13", "RECONSTRUCTION_MISMATCH", "post_query_obligations_or_expected_differ"),
        ("M14", "LIFECYCLE_VIOLATION", "forbidden_read_network_gpu_extra_query_or_edit"),
        ("M15", "ISOLATION_REUSE", "context_or_reviewer_reused"),
        ("M16", "ENVELOPE_MISMATCH", "byte_hash_or_rubric_lifecycle_binding_changed"),
        ("M17", "PREMATURE_ARTIFACT", "query_gold_run_or_cache_present"),
        ("M18", "BUILD_DRIFT", "closure_or_generated_artifact_changed"),
    )
    return {"format": "metnos.intent-holdout-mutations/0.2", "synthetic_only": True,
            "mutations": [{"id": i, "expected_code": c, "description": d} for i, c, d in mutations]}


def generated() -> dict[str, Any]:
    return {"authority.json": authority(), "glossary.json": glossary(), "rubrics.json": rubrics(),
            "pilot_matrix.json": pilot_matrix(), "mutation_catalog.json": mutation_catalog()}


def freeze_payload(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "metnos.intent-holdout-process-freeze/0.2",
        "state": "preflight_only_no_queries_no_case_gold_no_run",
        "generated": {name: {"file_sha256": digest_bytes(pretty(value)), "payload_sha256": digest(value)}
                      for name, value in sorted(values.items())},
        "local_files": {name: file_digest(HERE / name) for name in LOCAL},
        "source_files": {str(path.relative_to(REPO)): file_digest(path) for path in SOURCES},
        "environment": {"python": ".".join(map(str, sys.version_info[:3])),
                        "json_canonical": "utf8_sorted_keys_no_nan_compact"},
    }


def write() -> None:
    values = generated()
    for name, value in values.items():
        (HERE / name).write_bytes(pretty(value))
    (HERE / "process.freeze.json").write_bytes(pretty(freeze_payload(values)))


def check() -> list[str]:
    errors = []
    values = generated()
    for name, value in values.items():
        if not (HERE / name).is_file() or (HERE / name).read_bytes() != pretty(value): errors.append(f"GENERATED:{name}")
    if not (HERE / "process.freeze.json").is_file(): errors.append("FREEZE:MISSING")
    else:
        try: current = load_json(HERE / "process.freeze.json")
        except Exception: errors.append("FREEZE:INVALID")
        else:
            if current != freeze_payload(values): errors.append("FREEZE:DRIFT")
    return errors


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--write", action="store_true"); args = parser.parse_args()
    if args.write: write()
    problems = check(); print(json.dumps({"status": "ok" if not problems else "fail", "errors": problems}, sort_keys=True))
    raise SystemExit(bool(problems))
