#!/usr/bin/env python3
"""Frozen offline V24.1 delta: sink ownership and grounded support/query claims.

V24.1 imports the frozen V24 implementation but does not mutate it.  It adds
two source-grounded claim types and a manifest-driven interpretation of primary
sinks.  No query, lemma, gloss, language list, or gold id is read by projection.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import glob
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

sys.path.insert(0, "/tmp")
import metnos_v24_offline as v24  # noqa: E402


VERSION = "metnos.request-analysis/2.4.1"
RULES_PATH = Path("/tmp/metnos_v241_rules_frozen.json")
SCHEMA_PATH = Path("/tmp/metnos_v241_contract.schema.json")
CODE_PATH = Path(__file__).resolve()
LOCK_PATH = Path("/tmp/metnos_v241_freeze.lock.json")
RESULTS_PATH = Path("/tmp/metnos_v241_results.json")
MUTATIONS_PATH = Path("/tmp/metnos_v241_mutation_results.json")

V23LITE_GLOB = "/tmp/metnos_v23lite_full_*"
V23_GLOB = "/tmp/metnos_v23_full_*"
V21_PATH = v24.V21_PATH
BASE_EVIDENCE = list(v24.EVIDENCE_REFS)
V241_EVIDENCE = [*BASE_EVIDENCE, "question", "argument_relation"]


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_sha256(pattern: str) -> str:
    digest = hashlib.sha256()
    for name in sorted(glob.glob(pattern)):
        path = Path(name)
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_delta() -> dict[str, Any]:
    delta = json.loads(RULES_PATH.read_text())
    base = delta["base_contract"]
    path = Path(base["path"])
    actual = sha256_path(path)
    if actual != base["sha256"]:
        raise RuntimeError(
            f"V24 base rules changed: locked={base['sha256']} actual={actual}"
        )
    return delta


def build_schema() -> dict[str, Any]:
    delta = load_delta()
    schema = copy.deepcopy(v24.build_schema(v24.load_rules()))
    schema["$id"] = "https://metnos.invalid/schema/request-analysis-v241.json"
    schema["title"] = "Metnos grounded request graph V24.1"
    schema["properties"]["schema_version"] = {"const": VERSION}
    node = schema["properties"]["nodes"]["items"]

    question_keys = sorted(delta.get("question_bindings", {}))
    node["properties"]["question_binding"] = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"kind": {"const": "none"}},
                "required": ["kind"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "catalog_query"},
                    "binding_id": {"type": "string", "enum": question_keys},
                    "start_token_id": {"type": "integer", "minimum": 1},
                    "end_token_id": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "kind",
                    "binding_id",
                    "start_token_id",
                    "end_token_id",
                ],
            },
        ]
    }

    relation_keys = sorted(delta.get("argument_bindings", {}))
    node["properties"]["argument_relations"] = {
        "type": "array",
        "uniqueItems": True,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kind": {"const": "support_argument"},
                "binding_id": {"type": "string", "enum": relation_keys},
                "target_predicate_id": {"type": "integer", "minimum": 1},
                "support_object": {
                    "type": "string",
                    "enum": v24.OBJECTS,
                },
                "start_token_id": {"type": "integer", "minimum": 1},
                "end_token_id": {"type": "integer", "minimum": 1},
            },
            "required": [
                "kind",
                "binding_id",
                "target_predicate_id",
                "support_object",
                "start_token_id",
                "end_token_id",
            ],
        },
    }
    node["required"].extend(["question_binding", "argument_relations"])
    node["properties"]["evidence_refs"]["items"]["enum"] = V241_EVIDENCE
    return schema


def emit_schema() -> dict[str, Any]:
    schema = build_schema()
    SCHEMA_PATH.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return schema


def freeze() -> dict[str, Any]:
    emit_schema()
    lock = {
        "freeze_version": "metnos.v24.1-offline-freeze/1.0",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rules_sha256": sha256_path(RULES_PATH),
        "schema_sha256": sha256_path(SCHEMA_PATH),
        "projector_sha256": sha256_path(CODE_PATH),
        "base_rules_sha256": sha256_path(v24.RULES_PATH),
        "base_schema_sha256": sha256_path(v24.SCHEMA_PATH),
        "base_projector_sha256": sha256_path(Path(v24.__file__).resolve()),
        "v23lite_dataset_sha256": dataset_sha256(V23LITE_GLOB),
        "v23_full_dataset_sha256": dataset_sha256(V23_GLOB),
        "v21_adversarial_sha256": sha256_path(V21_PATH),
        "v24_on_v23lite_baseline_sha256": sha256_path(
            Path("/tmp/metnos_v24_on_v23lite_results.json")
        ),
        "v24_results_baseline_sha256": sha256_path(v24.RESULTS_PATH),
        "cross_validation_started": False,
    }
    LOCK_PATH.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return lock


def verify_freeze(*, mark_started: bool = False) -> dict[str, Any]:
    if not LOCK_PATH.exists():
        raise RuntimeError("V24.1 freeze lock missing")
    lock = json.loads(LOCK_PATH.read_text())
    actual = {
        "rules_sha256": sha256_path(RULES_PATH),
        "schema_sha256": sha256_path(SCHEMA_PATH),
        "projector_sha256": sha256_path(CODE_PATH),
        "base_rules_sha256": sha256_path(v24.RULES_PATH),
        "base_schema_sha256": sha256_path(v24.SCHEMA_PATH),
        "base_projector_sha256": sha256_path(Path(v24.__file__).resolve()),
        "v23lite_dataset_sha256": dataset_sha256(V23LITE_GLOB),
        "v23_full_dataset_sha256": dataset_sha256(V23_GLOB),
        "v21_adversarial_sha256": sha256_path(V21_PATH),
        "v24_on_v23lite_baseline_sha256": sha256_path(
            Path("/tmp/metnos_v24_on_v23lite_results.json")
        ),
        "v24_results_baseline_sha256": sha256_path(v24.RESULTS_PATH),
    }
    changed = {
        key: {"locked": lock.get(key), "actual": value}
        for key, value in actual.items()
        if lock.get(key) != value
    }
    if changed:
        raise RuntimeError(f"frozen artifact changed: {changed}")
    if mark_started and not lock.get("cross_validation_started"):
        lock["cross_validation_started"] = True
        lock["cross_validation_started_at"] = dt.datetime.now(
            dt.timezone.utc
        ).isoformat()
        LOCK_PATH.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    return lock


def _route_string(route: dict[str, Any]) -> str:
    if route.get("kind") == "none":
        return "none/none"
    obj = route["object"]
    qualifier = route.get("qualifier")
    if qualifier not in (None, "none"):
        obj = f"{obj}_{qualifier}"
    return f"{route['action']}/{obj}"


def _same_span(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("start_token_id") == right.get("start_token_id")
        and left.get("end_token_id") == right.get("end_token_id")
    )


def expected_evidence(node: dict[str, Any]) -> set[str]:
    refs = set(v24.expected_evidence_refs(node))
    if node.get("question_binding", {}).get("kind") != "none":
        refs.add("question")
    if node.get("argument_relations"):
        refs.add("argument_relation")
    return refs


def _ordered_evidence(refs: set[str]) -> list[str]:
    return sorted(refs, key=V241_EVIDENCE.index)


def to_v24(frame: dict[str, Any]) -> dict[str, Any]:
    base = copy.deepcopy(frame)
    base["schema_version"] = v24.SCHEMA_VERSION
    for node in base.get("nodes", []):
        node.pop("question_binding", None)
        node.pop("argument_relations", None)
        node["evidence_refs"] = _ordered_evidence(
            set(node.get("evidence_refs", [])).intersection(BASE_EVIDENCE)
        )
    return base


def validate_v241(
    frame: dict[str, Any], tokens: list[str], delta: dict[str, Any] | None = None
) -> dict[str, Any]:
    delta = delta or load_delta()
    errors: list[dict[str, Any]] = []
    validator = jsonschema.Draft202012Validator(build_schema())
    for issue in sorted(validator.iter_errors(frame), key=lambda e: list(e.path)):
        errors.append(
            {
                "code": "schema",
                "path": "/".join(str(p) for p in issue.absolute_path),
                "message": issue.message,
                "retryable": True,
            }
        )
    if errors:
        return {"valid": False, "errors": errors}

    base_validation = v24.validate_v24(to_v24(frame), tokens, v24.load_rules())
    errors.extend(base_validation["errors"])
    nodes = frame["nodes"]
    for index, node in enumerate(nodes, 1):
        prefix = f"nodes/{index - 1}"
        question = node["question_binding"]
        if question["kind"] != "none" and not v24._span_ok(question, len(tokens)):
            errors.append(
                {
                    "code": "question_span",
                    "path": f"{prefix}/question_binding",
                    "message": "question binding requires a source span",
                    "retryable": True,
                }
            )
        for rel_index, relation in enumerate(node["argument_relations"]):
            binding = delta.get("argument_bindings", {}).get(
                relation["binding_id"]
            )
            target_id = relation["target_predicate_id"]
            if target_id >= index:
                errors.append(
                    {
                        "code": "argument_target_edge",
                        "path": f"{prefix}/argument_relations/{rel_index}",
                        "message": "argument support must target an earlier predicate",
                        "retryable": True,
                    }
                )
                continue
            target = nodes[target_id - 1]
            target_route = _route_string(target["route"])
            if target_route not in binding.get("target_routes", []):
                errors.append(
                    {
                        "code": "argument_target_contract",
                        "path": f"{prefix}/argument_relations/{rel_index}",
                        "message": f"{target_route} does not accept {relation['binding_id']}",
                        "retryable": True,
                    }
                )
            if relation["support_object"] not in binding.get(
                "support_objects", []
            ):
                errors.append(
                    {
                        "code": "argument_support_domain",
                        "path": f"{prefix}/argument_relations/{rel_index}",
                        "message": "support domain incompatible with argument contract",
                        "retryable": True,
                    }
                )
            if not v24._span_ok(relation, len(tokens)):
                errors.append(
                    {
                        "code": "argument_relation_span",
                        "path": f"{prefix}/argument_relations/{rel_index}",
                        "message": "support relation requires source evidence",
                        "retryable": True,
                    }
                )
        actual = set(node["evidence_refs"])
        required = expected_evidence(node)
        if actual != required:
            errors.append(
                {
                    "code": "evidence_coverage_v241",
                    "path": f"{prefix}/evidence_refs",
                    "message": f"actual={sorted(actual)} required={sorted(required)}",
                    "retryable": True,
                }
            )
    return {"valid": not errors, "errors": errors}


def _infer_legacy_relation(
    node: dict[str, Any], nodes: list[dict[str, Any]], delta: dict[str, Any]
) -> list[dict[str, Any]]:
    """Comparison-only inference from a complete typed role collision."""
    inferred: list[dict[str, Any]] = []
    for binding_id, binding in delta.get("argument_bindings", {}).items():
        rule = binding.get("legacy_inference") or {}
        if _route_string(node["route"]) not in rule.get("candidate_routes", []):
            continue
        if node.get("resource_scope") not in rule.get("resource_scopes", []):
            continue
        source_id = node.get("input_from_predicate_id", 0)
        if rule.get("require_target_source_edge") and not source_id:
            continue
        if source_id < 1 or source_id >= node["predicate_id"]:
            continue
        target_route = _route_string(nodes[source_id - 1]["route"])
        if target_route not in binding.get("target_routes", []):
            continue
        patient = node.get("patient", {})
        sink = node.get("sink", {})
        if rule.get("require_explicit_patient") and patient.get("kind") != "explicit":
            continue
        if rule.get("require_primary_sink_same_span_and_domain"):
            if sink.get("kind") != "primary_output":
                continue
            if patient.get("object") != sink.get("object"):
                continue
            if not _same_span(patient, sink):
                continue
        if patient.get("object") not in binding.get("support_objects", []):
            continue
        inferred.append(
            {
                "kind": "support_argument",
                "binding_id": binding_id,
                "target_predicate_id": source_id,
                "support_object": patient["object"],
                "start_token_id": patient["start_token_id"],
                "end_token_id": patient["end_token_id"],
            }
        )
    return inferred


def upgrade_v24_frame(
    base: dict[str, Any], *, infer_legacy_relations: bool = False
) -> dict[str, Any]:
    delta = load_delta()
    frame = copy.deepcopy(base)
    frame["schema_version"] = VERSION
    nodes = frame.get("nodes", [])
    for node in nodes:
        node["question_binding"] = {"kind": "none"}
        node["argument_relations"] = []
    if infer_legacy_relations:
        for node in nodes:
            node["argument_relations"] = _infer_legacy_relation(
                node, nodes, delta
            )
    for node in nodes:
        node["evidence_refs"] = _ordered_evidence(expected_evidence(node))
    return frame


def migrate_v23(
    frame: dict[str, Any], tokens: list[str], *, infer_legacy_relations: bool
) -> dict[str, Any]:
    return upgrade_v24_frame(
        v24.migrate_v23(frame, tokens, v24.load_rules()),
        infer_legacy_relations=infer_legacy_relations,
    )


def migrate_v21(
    record: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    base, errors = v24.migrate_v21(record, v24.load_rules())
    if base is None:
        return None, errors
    return upgrade_v24_frame(base), []


def _candidate_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "candidate",
        "action": route["action"],
        "object": route["object"],
        "qualifier": route.get("qualifier", "none"),
        "resolution": "technical_override",
    }


def project_v241(
    frame: dict[str, Any],
    tokens: list[str],
    *,
    mode: str = "research",
    delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delta = delta or load_delta()
    validation = validate_v241(frame, tokens, delta)
    if not validation["valid"]:
        return {
            "status": "fail_closed",
            "reason": "invalid_grounding_v241",
            "signature": None,
            "trace": [],
            "validation": validation,
        }
    prepared = copy.deepcopy(frame)
    pretrace: dict[int, list[dict[str, Any]]] = {}
    for node in prepared["nodes"]:
        pid = node["predicate_id"]
        rules: list[dict[str, Any]] = []
        before = _route_string(node["route"])

        question = node["question_binding"]
        if question["kind"] != "none":
            binding = delta["question_bindings"][question["binding_id"]]
            node["route"] = _candidate_route(binding["route"])
            rules.append(
                {
                    "rule_id": f"v241.question.{question['binding_id']}",
                    "before": before,
                    "after": _route_string(node["route"]),
                    "evidence": ["question.binding_id", "question.span", "catalog.query_binding"],
                }
            )

        for relation in node["argument_relations"]:
            binding = delta["argument_bindings"][relation["binding_id"]]
            rel_before = _route_string(node["route"])
            node["route"] = _candidate_route(binding["acquisition_route"])
            rules.append(
                {
                    "rule_id": f"v241.argument.{relation['binding_id']}",
                    "before": rel_before,
                    "after": _route_string(node["route"]),
                    "evidence": [
                        "argument_relation.binding_id",
                        "argument_relation.target_predicate_id",
                        "argument_relation.span",
                        "catalog.argument_binding",
                    ],
                }
            )

        sink = node["sink"]
        route_key = _route_string(node["route"])
        sink_contract = delta.get("primary_sink_contracts", {}).get(route_key)
        if sink.get("kind") == "primary_output" and sink_contract:
            policy = sink_contract.get("explicit_sink_policy")
            if policy in {
                "secondary_persistence",
                "secondary_persistence_unless_explicit_consumer",
            } and sink.get("object") == sink_contract.get("sink_domain"):
                node["sink"]["kind"] = "secondary_persistence"
                node["sink"]["destination_role"] = "external_store"
                rules.append(
                    {
                        "rule_id": "v241.catalog.primary_sink_ownership",
                        "before": "primary_output",
                        "after": "secondary_persistence",
                        "evidence": [
                            "sink.span",
                            "sink.object",
                            "catalog.primary_artifact_durability",
                            "catalog.materialization_route",
                        ],
                    }
                )
        if rules:
            pretrace[pid] = rules

    base = to_v24(prepared)
    projected = v24.project_v24(
        base, tokens, mode=mode, rules=v24.load_rules()
    )
    projected["validation_v241"] = validation
    if projected.get("trace"):
        for item in projected["trace"]:
            pid = item.get("predicate_id")
            if pid in pretrace:
                item["rules"] = [*pretrace[pid], *item.get("rules", [])]
    elif pretrace:
        projected["preprojection_trace"] = pretrace
    return projected


def _base_v241(**kwargs: Any) -> dict[str, Any]:
    return upgrade_v24_frame(v24._base_frame(**kwargs))


def _set_evidence(node: dict[str, Any]) -> None:
    node["evidence_refs"] = _ordered_evidence(expected_evidence(node))


def run_mutations() -> dict[str, Any]:
    delta = load_delta()
    tests: list[dict[str, Any]] = []

    def projection_test(
        test_id: str,
        frame: dict[str, Any],
        tokens: list[str],
        expected: Any,
    ) -> None:
        result = project_v241(frame, tokens, mode="research", delta=delta)
        tests.append(
            {
                "id": test_id,
                "kind": "projection",
                "pass": result.get("signature") == expected,
                "expected": expected,
                "actual": result.get("signature"),
                "status": result.get("status"),
            }
        )

    extract = _base_v241(
        action="extract",
        obj="entries",
        scope="held_result",
        sink={
            "kind": "primary_output",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "entries",
            "destination_role": "external_store",
        },
    )
    _set_evidence(extract["nodes"][0])
    projection_test(
        "sink.ephemeral_extract_becomes_secondary",
        extract,
        ["extract", "amounts", "store"],
        ["extract/entries", "write/entries"],
    )

    compress = _base_v241(
        action="compress",
        obj="files",
        scope="held_result",
        sink={
            "kind": "primary_output",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "files",
            "destination_role": "artifact_domain",
        },
    )
    _set_evidence(compress["nodes"][0])
    projection_test(
        "sink.persistent_compress_remains_primary",
        compress,
        ["compress", "files", "archive"],
        "compress/files",
    )

    read_file = _base_v241(
        action="read",
        obj="files",
        scope="single_known",
        sink={
            "kind": "primary_output",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "files",
            "destination_role": "destination_locator",
        },
    )
    _set_evidence(read_file["nodes"][0])
    projection_test(
        "sink.read_file_destination_remains_owned",
        read_file,
        ["read", "file", "/tmp/out"],
        "read/files",
    )

    question = _base_v241(action="get", obj="persons", scope="single_known")
    question["nodes"][0]["question_binding"] = {
        "kind": "catalog_query",
        "binding_id": "runtime.current_location",
        "start_token_id": 2,
        "end_token_id": 2,
    }
    _set_evidence(question["nodes"][0])
    projection_test(
        "question.current_location_projects_places",
        question,
        ["tell", "location"],
        "get/places",
    )

    # Two-node graph: an existing file supports an earlier create/events node.
    relation = _base_v241(action="create", obj="events", scope="new_resource")
    first = relation["nodes"][0]
    first["patient"] = {"kind": "none"}
    _set_evidence(first)
    relation["nodes"].append(
        {
            "predicate_id": 2,
            "predicate_anchor_token_id": 3,
            "role": "request",
            "lemma": "support",
            "semantic_gloss_en": "support",
            "semantic_action": "write",
            "resource_scope": "single_known",
            "input_from_predicate_id": 1,
            "patient": {
                "kind": "explicit",
                "start_token_id": 4,
                "end_token_id": 4,
                "object": "files",
            },
            "carrier": {"kind": "none"},
            "sink": {
                "kind": "primary_output",
                "start_token_id": 4,
                "end_token_id": 4,
                "object": "files",
                "destination_role": "artifact_domain",
            },
            "route": {
                "kind": "candidate",
                "action": "write",
                "object": "files",
                "qualifier": "none",
                "resolution": "direct",
            },
            "attribute_claims": [],
            "question_binding": {"kind": "none"},
            "argument_relations": [
                {
                    "kind": "support_argument",
                    "binding_id": "calendar_event_attachment",
                    "target_predicate_id": 1,
                    "support_object": "files",
                    "start_token_id": 4,
                    "end_token_id": 4,
                }
            ],
            "evidence_refs": [],
        }
    )
    _set_evidence(relation["nodes"][1])
    projection_test(
        "argument.explicit_attachment_acquires_file",
        relation,
        ["create", "event", "support", "image.png"],
        ["create/events", "get/files"],
    )

    # The legacy adapter may infer exactly the same claim from the complete
    # typed collision, without reading the source phrase.
    legacy = to_v24(relation)
    legacy["nodes"][1]["route"] = {
        "kind": "candidate",
        "action": "write",
        "object": "files",
        "qualifier": "none",
        "resolution": "direct",
    }
    inferred = upgrade_v24_frame(legacy, infer_legacy_relations=True)
    projection_test(
        "argument.legacy_structural_inference",
        inferred,
        ["create", "event", "support", "image.png"],
        ["create/events", "get/files"],
    )

    # Guard: remove the source edge; no relation may be inferred.
    no_source = copy.deepcopy(legacy)
    no_source["nodes"][1]["input_from_predicate_id"] = 0
    no_source["nodes"][1]["evidence_refs"] = ["predicate", "patient", "sink"]
    guarded = upgrade_v24_frame(no_source, infer_legacy_relations=True)
    tests.append(
        {
            "id": "argument.no_source_no_inference",
            "kind": "inference_guard",
            "pass": guarded["nodes"][1]["argument_relations"] == [],
            "actual": guarded["nodes"][1]["argument_relations"],
        }
    )

    # Evidence mutation: active question without its evidence ref.
    missing_evidence = copy.deepcopy(question)
    missing_evidence["nodes"][0]["evidence_refs"].remove("question")
    validation = validate_v241(missing_evidence, ["tell", "location"], delta)
    tests.append(
        {
            "id": "validator.question_evidence_required",
            "kind": "validator",
            "pass": (not validation["valid"])
            and "evidence_coverage_v241"
            in {e["code"] for e in validation["errors"]},
            "actual_codes": sorted({e["code"] for e in validation["errors"]}),
        }
    )

    bad_target = copy.deepcopy(relation)
    bad_target["nodes"][1]["argument_relations"][0]["target_predicate_id"] = 2
    validation = validate_v241(
        bad_target, ["create", "event", "support", "image.png"], delta
    )
    tests.append(
        {
            "id": "validator.argument_forward_target_rejected",
            "kind": "validator",
            "pass": (not validation["valid"])
            and "argument_target_edge"
            in {e["code"] for e in validation["errors"]},
            "actual_codes": sorted({e["code"] for e in validation["errors"]}),
        }
    )

    runtime = project_v241(
        _base_v241(), ["read", "file"], mode="runtime", delta=delta
    )
    tests.append(
        {
            "id": "runtime.unreviewed_v241_fails_closed",
            "kind": "runtime_gate",
            "pass": runtime["status"] == "fail_closed",
            "actual": {"status": runtime["status"], "reason": runtime["reason"]},
        }
    )

    result = {
        "suite_version": "metnos.v24.1-mutations/1.0",
        "summary": {
            "tests": len(tests),
            "passed": sum(t["pass"] for t in tests),
            "failed": sum(not t["pass"] for t in tests),
            "by_kind": {
                kind: {
                    "tests": sum(t["kind"] == kind for t in tests),
                    "passed": sum(
                        t["kind"] == kind and t["pass"] for t in tests
                    ),
                }
                for kind in sorted({t["kind"] for t in tests})
            },
        },
        "tests": tests,
    }
    MUTATIONS_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return result


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _signature_value(values: list[str]) -> Any:
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def raw_signature(record: dict[str, Any]) -> Any:
    return v24.raw_v23_signature(record)


def _summarize(rows: list[dict[str, Any]], baseline_key: str) -> dict[str, Any]:
    return {
        "records": len(rows),
        "valid": sum(r["valid"] for r in rows),
        "fail_closed": sum(r["status"] == "fail_closed" for r in rows),
        "baseline_exact": sum(r[baseline_key] == r["expected"] for r in rows),
        "v241_exact": sum(r["v241"] == r["expected"] for r in rows),
        "improvements": sum(
            r[baseline_key] != r["expected"] and r["v241"] == r["expected"]
            for r in rows
        ),
        "regressions": sum(
            r[baseline_key] == r["expected"] and r["v241"] != r["expected"]
            for r in rows
        ),
    }


def cross_validate() -> dict[str, Any]:
    lock = verify_freeze(mark_started=True)
    delta = load_delta()
    mutations = run_mutations()
    if mutations["summary"]["failed"]:
        raise RuntimeError("V24.1 mutation suite failed")

    v24lite_baseline = json.loads(
        Path("/tmp/metnos_v24_on_v23lite_results.json").read_text()
    )["records"]
    lite_rows: list[dict[str, Any]] = []
    lite_source = [
        row
        for path in sorted(glob.glob(V23LITE_GLOB))
        for row in json.loads(Path(path).read_text())
    ]
    if len(lite_source) != len(v24lite_baseline):
        raise RuntimeError("V23lite/V24 baseline alignment mismatch")
    for index, (record, baseline) in enumerate(
        zip(lite_source, v24lite_baseline), 1
    ):
        if record["query"] != baseline["query"]:
            raise RuntimeError(f"V23lite baseline query mismatch at {index}")
        tokens = record["meta"]["tokens"]
        frame = migrate_v23(
            record["frame"], tokens, infer_legacy_relations=True
        )
        validation = validate_v241(frame, tokens, delta)
        projection = project_v241(frame, tokens, mode="research", delta=delta)
        lite_rows.append(
            {
                "index": index,
                "query": record["query"],
                "expected": record["expected"],
                "raw": raw_signature(record),
                "v24": baseline["v24"],
                "v241": projection.get("signature"),
                "valid": validation["valid"],
                "status": projection["status"],
                "inferred_relations": sum(
                    len(n["argument_relations"]) for n in frame["nodes"]
                ),
                "rules": [
                    rule["rule_id"]
                    for trace in projection.get("trace", [])
                    for rule in trace.get("rules", [])
                    if rule["rule_id"].startswith("v241.")
                ],
            }
        )

    v24_full_baseline = json.loads(v24.RESULTS_PATH.read_text())["v23"][
        "records"
    ]
    full_source = [
        row
        for path in sorted(glob.glob(V23_GLOB))
        for row in json.loads(Path(path).read_text())
    ]
    full_rows: list[dict[str, Any]] = []
    for index, (record, baseline) in enumerate(
        zip(full_source, v24_full_baseline), 1
    ):
        tokens = record["meta"]["tokens"]
        frame = migrate_v23(
            record["frame"], tokens, infer_legacy_relations=True
        )
        validation = validate_v241(frame, tokens, delta)
        projection = project_v241(frame, tokens, mode="research", delta=delta)
        full_rows.append(
            {
                "index": index,
                "query": record["query"],
                "expected": record["expected"],
                "v24": baseline["v24_research"],
                "v241": projection.get("signature"),
                "valid": validation["valid"],
                "status": projection["status"],
                "rules": [
                    rule["rule_id"]
                    for trace in projection.get("trace", [])
                    for rule in trace.get("rules", [])
                    if rule["rule_id"].startswith("v241.")
                ],
            }
        )

    adversarial = json.loads(V21_PATH.read_text())
    v21_rows: list[dict[str, Any]] = []
    for record in adversarial["records"]:
        expected = record["expected"]["requested_signature"]
        frame, conversion_errors = migrate_v21(record)
        tokens = record["transport_meta"]["tokens"]
        if frame is None:
            validation = {"valid": False, "errors": conversion_errors}
            projection = {"status": "fail_closed", "signature": None}
        else:
            validation = validate_v241(frame, tokens, delta)
            projection = project_v241(frame, tokens, mode="research", delta=delta)
        v21_rows.append(
            {
                "id": record["id"],
                "expected": expected,
                "v24": (
                    json.loads(v24.RESULTS_PATH.read_text())["v21_adversarial"]
                    ["records"]
                ),
                "v241": _as_list(projection.get("signature")),
                "valid": validation["valid"],
                "status": projection["status"],
            }
        )
    # Replace the bulky temporary V24 value with aligned baseline signatures.
    base_v21 = json.loads(v24.RESULTS_PATH.read_text())["v21_adversarial"][
        "records"
    ]
    for row, baseline in zip(v21_rows, base_v21):
        row["v24"] = baseline["v24_projection_only"]

    result = {
        "suite_version": "metnos.v24.1-cross-validation/1.0",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "freeze": lock,
        "catalog_gate": {
            "runtime_cutover_eligible": False,
            "reason": "prototype manifest metadata are not production-reviewed",
        },
        "mutations": mutations["summary"],
        "v23lite": {
            "summary": _summarize(lite_rows, "v24"),
            "remaining": [r for r in lite_rows if r["v241"] != r["expected"]],
            "improvements": [
                r
                for r in lite_rows
                if r["v24"] != r["expected"] and r["v241"] == r["expected"]
            ],
            "regressions": [
                r
                for r in lite_rows
                if r["v24"] == r["expected"] and r["v241"] != r["expected"]
            ],
            "records": lite_rows,
        },
        "v23_full": {
            "summary": _summarize(full_rows, "v24"),
            "improvements": [
                r
                for r in full_rows
                if r["v24"] != r["expected"] and r["v241"] == r["expected"]
            ],
            "regressions": [
                r
                for r in full_rows
                if r["v24"] == r["expected"] and r["v241"] != r["expected"]
            ],
        },
        "v21_adversarial": {
            "summary": _summarize(v21_rows, "v24"),
            "records": v21_rows,
        },
    }
    RESULTS_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-schema", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--mutations", action="store_true")
    parser.add_argument("--cross-validate", action="store_true")
    args = parser.parse_args()
    if args.emit_schema:
        emit_schema()
        print(json.dumps({"schema": str(SCHEMA_PATH)}))
    if args.freeze:
        print(json.dumps(freeze(), sort_keys=True))
    if args.mutations:
        if not SCHEMA_PATH.exists():
            emit_schema()
        print(json.dumps(run_mutations()["summary"], sort_keys=True))
    if args.cross_validate:
        result = cross_validate()
        print(
            json.dumps(
                {
                    "v23lite": result["v23lite"]["summary"],
                    "v23_full": result["v23_full"]["summary"],
                    "v21": result["v21_adversarial"]["summary"],
                    "mutations": result["mutations"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
