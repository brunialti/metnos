#!/usr/bin/env python3
"""Offline V24 graph-grounding and catalog-projection research harness.

This file never calls an LLM, a network endpoint, or a production service.  It
converts already captured V23/V21 frames into the proposed V24 contract, checks
the contract, projects only through frozen language-neutral catalog metadata,
and runs mutation/cross-validation suites.

The runtime gate deliberately fails closed: the catalog audit found zero
production-reviewed intent contracts.  ``mode=research`` exists only so the
frozen prototype can be compared offline; it is not a fallback policy.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import glob
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import jsonschema


ROOT = Path("/opt/metnos")
RULES_PATH = Path("/tmp/metnos_v24_rules_frozen.json")
SCHEMA_PATH = Path("/tmp/metnos_v24_contract.schema.json")
LOCK_PATH = Path("/tmp/metnos_v24_freeze.lock.json")
RESULTS_PATH = Path("/tmp/metnos_v24_results.json")
MUTATIONS_PATH = Path("/tmp/metnos_v24_mutation_results.json")

V23_GLOB = "/tmp/metnos_v23_full_*"
V21_PATH = Path(
    "/tmp/metnos_request_analysis_adversarial_v1_v21_independent_root.json"
)
CATALOG_MIGRATION_PATH = Path(
    "/tmp/metnos_catalog_structured_intent_contract_migration.json"
)

SCHEMA_VERSION = "metnos.request-analysis/2.4"
ROLE_VALUES = ["request", "forbid", "condition", "description", "quote"]
RESOURCE_SCOPES = [
    "single_known",
    "collection_by_criterion",
    "whole_domain",
    "held_result",
    "new_resource",
    "not_applicable",
]
PATIENT_IMPLICIT_BASES = [
    "predicate_semantics",
    "deictic_context",
    "ambient_state",
]
EVIDENCE_REFS = [
    "predicate",
    "patient",
    "carrier",
    "source",
    "sink",
    "attribute",
]
CARRIER_KINDS = ["source_container", "transport_channel", "representation"]
CARRIER_VALUE_TYPES = [
    "uri",
    "filesystem_path",
    "message_store",
    "message_channel",
    "raster_image",
    "document",
    "text_representation",
    "opaque_identifier",
]
DESTINATION_ROLES = [
    "artifact_domain",
    "destination_locator",
    "external_store",
    "recipient_channel",
]


def _load_vocab() -> tuple[list[str], list[str], list[str]]:
    """Read the technical ontology; never inspect localized source strings."""
    import sys

    sys.path.insert(0, str(ROOT / "runtime"))
    from vocab import ACTIONS, OBJECTS, QUALIFIERS  # type: ignore

    return list(ACTIONS), list(OBJECTS), list(QUALIFIERS)


ACTIONS, OBJECTS, QUALIFIERS = _load_vocab()


def load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text())


def _none_variant() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"kind": {"const": "none"}},
        "required": ["kind"],
    }


def _span_properties() -> dict[str, Any]:
    return {
        "start_token_id": {"type": "integer", "minimum": 1},
        "end_token_id": {"type": "integer", "minimum": 1},
    }


def build_schema(rules: dict[str, Any]) -> dict[str, Any]:
    """Build the strict JSON Schema from technical vocab and manifest keys."""
    patient = {
        "oneOf": [
            _none_variant(),
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "explicit"},
                    **_span_properties(),
                    "object": {"type": "string", "enum": OBJECTS},
                },
                "required": [
                    "kind",
                    "start_token_id",
                    "end_token_id",
                    "object",
                ],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "implicit"},
                    "object": {"type": "string", "enum": OBJECTS},
                    "basis": {
                        "type": "string",
                        "enum": PATIENT_IMPLICIT_BASES,
                    },
                },
                "required": ["kind", "object", "basis"],
            },
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "anaphoric"},
                    **_span_properties(),
                },
                "required": ["kind", "start_token_id", "end_token_id"],
            },
        ]
    }

    carrier_variants = [_none_variant()]
    for kind in CARRIER_KINDS:
        carrier_variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": kind},
                    **_span_properties(),
                    "object": {"type": "string", "enum": OBJECTS},
                    "value_type": {
                        "type": "string",
                        "enum": CARRIER_VALUE_TYPES,
                    },
                },
                "required": [
                    "kind",
                    "start_token_id",
                    "end_token_id",
                    "object",
                    "value_type",
                ],
            }
        )
    carrier = {"oneOf": carrier_variants}

    sink_variants = [_none_variant()]
    for kind in ["primary_output", "secondary_persistence"]:
        sink_variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": kind},
                    **_span_properties(),
                    "object": {"type": "string", "enum": OBJECTS},
                    "destination_role": {
                        "type": "string",
                        "enum": DESTINATION_ROLES,
                    },
                },
                "required": [
                    "kind",
                    "start_token_id",
                    "end_token_id",
                    "object",
                    "destination_role",
                ],
            }
        )
    sink = {"oneOf": sink_variants}

    route = {
        "oneOf": [
            _none_variant(),
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": "candidate"},
                    "action": {"type": "string", "enum": ACTIONS},
                    "object": {"type": "string", "enum": OBJECTS},
                    "qualifier": {
                        "type": "string",
                        "enum": ["none", *QUALIFIERS],
                    },
                    "resolution": {
                        "type": "string",
                        "enum": ["direct", "generalized", "technical_override"],
                    },
                },
                "required": [
                    "kind",
                    "action",
                    "object",
                    "qualifier",
                    "resolution",
                ],
            },
        ]
    }

    attribute_keys = sorted(
        {item["claim_key"] for item in rules.get("attribute_bindings", [])}
    )
    attribute_claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "catalog_attribute"},
            "key": {"type": "string", "enum": attribute_keys},
            **_span_properties(),
        },
        "required": ["kind", "key", "start_token_id", "end_token_id"],
    }

    node = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "predicate_id": {"type": "integer", "minimum": 1},
            "predicate_anchor_token_id": {"type": "integer", "minimum": 1},
            "role": {"type": "string", "enum": ROLE_VALUES},
            "lemma": {"type": "string", "minLength": 1, "maxLength": 80},
            "semantic_gloss_en": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
            },
            "semantic_action": {
                "type": "string",
                "enum": ["none", *ACTIONS],
            },
            "resource_scope": {
                "type": "string",
                "enum": RESOURCE_SCOPES,
            },
            "input_from_predicate_id": {"type": "integer", "minimum": 0},
            "patient": patient,
            "carrier": carrier,
            "sink": sink,
            "route": route,
            "attribute_claims": {
                "type": "array",
                "items": attribute_claim,
                "uniqueItems": True,
            },
            "evidence_refs": {
                "type": "array",
                "items": {"type": "string", "enum": EVIDENCE_REFS},
                "uniqueItems": True,
                "minItems": 1,
            },
        },
        "required": [
            "predicate_id",
            "predicate_anchor_token_id",
            "role",
            "lemma",
            "semantic_gloss_en",
            "semantic_action",
            "resource_scope",
            "input_from_predicate_id",
            "patient",
            "carrier",
            "sink",
            "route",
            "attribute_claims",
            "evidence_refs",
        ],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://metnos.invalid/schema/request-analysis-v24.json",
        "title": "Metnos language-neutral grounded request graph V24",
        "description": (
            "One node per source predicate. Lemma/gloss are audit-only; "
            "catalog projection may consume only typed route, scope, graph, "
            "patient, carrier, sink, and attribute claims."
        ),
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": node,
            },
        },
        "required": ["schema_version", "nodes"],
    }


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    )


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def emit_schema() -> dict[str, Any]:
    schema = build_schema(load_rules())
    SCHEMA_PATH.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return schema


def freeze() -> dict[str, Any]:
    emit_schema()
    lock = {
        "freeze_version": "metnos.v24-offline-freeze/1.0",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rules_path": str(RULES_PATH),
        "rules_sha256": sha256_path(RULES_PATH),
        "schema_path": str(SCHEMA_PATH),
        "schema_sha256": sha256_path(SCHEMA_PATH),
        "projector_path": str(Path(__file__).resolve()),
        "projector_sha256": sha256_path(Path(__file__).resolve()),
        "cross_validation_started": False,
    }
    LOCK_PATH.write_text(
        json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return lock


def verify_freeze(*, mark_started: bool = False) -> dict[str, Any]:
    if not LOCK_PATH.exists():
        raise RuntimeError("freeze lock missing; run --freeze before cross-validation")
    lock = json.loads(LOCK_PATH.read_text())
    checks = {
        "rules_sha256": sha256_path(RULES_PATH),
        "schema_sha256": sha256_path(SCHEMA_PATH),
        "projector_sha256": sha256_path(Path(__file__).resolve()),
    }
    mismatches = {
        key: {"locked": lock.get(key), "actual": value}
        for key, value in checks.items()
        if lock.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"frozen artifact changed: {mismatches}")
    if mark_started and not lock.get("cross_validation_started"):
        lock["cross_validation_started"] = True
        lock["cross_validation_started_at"] = dt.datetime.now(
            dt.timezone.utc
        ).isoformat()
        LOCK_PATH.write_text(
            json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    return lock


def _span_ok(value: dict[str, Any], token_count: int) -> bool:
    start = value.get("start_token_id")
    end = value.get("end_token_id")
    return (
        isinstance(start, int)
        and isinstance(end, int)
        and 1 <= start <= end <= token_count
    )


URI_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?:#]|$))",
    re.IGNORECASE,
)
PATH_RE = re.compile(r"(?:^[/\\]|[/\\]|^[.]{1,2}$|^[.]{1,2}[/\\])")
LITERAL_ANCHOR_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|@|\d|^(?:[.~]?/|[A-Za-z]:\\|\\\\))",
    re.IGNORECASE,
)


def _span_text(value: dict[str, Any], tokens: list[str]) -> str:
    if not _span_ok(value, len(tokens)):
        return ""
    return " ".join(
        tokens[value["start_token_id"] - 1 : value["end_token_id"]]
    )


def expected_evidence_refs(node: dict[str, Any]) -> set[str]:
    refs = {"predicate"}
    if node.get("patient", {}).get("kind") != "none":
        refs.add("patient")
    if node.get("carrier", {}).get("kind") != "none":
        refs.add("carrier")
    if node.get("input_from_predicate_id", 0):
        refs.add("source")
    if node.get("sink", {}).get("kind") != "none":
        refs.add("sink")
    if node.get("attribute_claims"):
        refs.add("attribute")
    return refs


def validate_v24(
    frame: dict[str, Any], tokens: list[str], rules: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Validate shape, graph invariants, typed spans, and evidence coverage."""
    rules = rules or load_rules()
    schema = build_schema(rules)
    errors: list[dict[str, Any]] = []
    validator = jsonschema.Draft202012Validator(schema)
    for issue in sorted(validator.iter_errors(frame), key=lambda e: list(e.path)):
        path = "/".join(str(part) for part in issue.absolute_path)
        errors.append(
            {
                "code": "schema",
                "path": path,
                "message": issue.message,
                "retryable": True,
            }
        )
    if errors:
        return {"valid": False, "errors": errors}

    nodes = frame["nodes"]
    token_count = len(tokens)
    last_anchor = 0
    compatibility = rules.get("carrier_value_types", {})
    for index, node in enumerate(nodes, 1):
        prefix = f"nodes/{index - 1}"
        if node["predicate_id"] != index:
            errors.append(
                {
                    "code": "predicate_identity",
                    "path": f"{prefix}/predicate_id",
                    "message": "predicate_id must be dense and source ordered",
                    "retryable": True,
                }
            )
        anchor = node["predicate_anchor_token_id"]
        if not 1 <= anchor <= token_count or anchor <= last_anchor:
            errors.append(
                {
                    "code": "predicate_anchor",
                    "path": f"{prefix}/predicate_anchor_token_id",
                    "message": "anchor must be in range and strictly increasing",
                    "retryable": True,
                }
            )
        elif LITERAL_ANCHOR_RE.search(tokens[anchor - 1]):
            errors.append(
                {
                    "code": "literal_anchor",
                    "path": f"{prefix}/predicate_anchor_token_id",
                    "message": "literal tokens cannot anchor predicates",
                    "retryable": True,
                }
            )
        last_anchor = max(last_anchor, anchor)

        source = node["input_from_predicate_id"]
        if source >= index:
            errors.append(
                {
                    "code": "source_edge",
                    "path": f"{prefix}/input_from_predicate_id",
                    "message": "source edge must point to an earlier predicate",
                    "retryable": True,
                }
            )

        route_kind = node["route"]["kind"]
        if node["role"] == "request" and route_kind != "candidate":
            errors.append(
                {
                    "code": "request_without_route",
                    "path": f"{prefix}/route",
                    "message": "request predicates require an executable candidate",
                    "retryable": True,
                }
            )
        if node["role"] != "request" and route_kind != "none":
            errors.append(
                {
                    "code": "nonrequest_executable",
                    "path": f"{prefix}/route",
                    "message": "non-request roles must be non-executable",
                    "retryable": True,
                }
            )

        patient = node["patient"]
        if patient["kind"] == "explicit" and not _span_ok(patient, token_count):
            errors.append(
                {
                    "code": "patient_span",
                    "path": f"{prefix}/patient",
                    "message": "explicit patient requires a non-zero in-range span",
                    "retryable": True,
                }
            )
        if patient["kind"] == "anaphoric":
            if not _span_ok(patient, token_count):
                errors.append(
                    {
                        "code": "patient_span",
                        "path": f"{prefix}/patient",
                        "message": "anaphoric patient requires source evidence span",
                        "retryable": True,
                    }
                )
            if not source:
                errors.append(
                    {
                        "code": "anaphor_without_source",
                        "path": f"{prefix}/patient",
                        "message": "anaphoric patient requires an earlier source edge",
                        "retryable": True,
                    }
                )

        carrier = node["carrier"]
        if carrier["kind"] != "none":
            if not _span_ok(carrier, token_count):
                errors.append(
                    {
                        "code": "carrier_span",
                        "path": f"{prefix}/carrier",
                        "message": "active carrier requires a non-zero in-range span",
                        "retryable": True,
                    }
                )
            allowed_types = (
                compatibility.get(carrier["kind"], {}).get(carrier["object"])
                or [
                    rules.get("default_carrier_value_type", {}).get(
                        carrier["kind"], "opaque_identifier"
                    )
                ]
            )
            if carrier["value_type"] not in allowed_types:
                errors.append(
                    {
                        "code": "carrier_type",
                        "path": f"{prefix}/carrier/value_type",
                        "message": (
                            f"{carrier['value_type']} incompatible with "
                            f"{carrier['kind']}/{carrier['object']}"
                        ),
                        "retryable": True,
                    }
                )
            carrier_text = _span_text(carrier, tokens)
            if carrier["value_type"] == "uri" and not URI_RE.search(carrier_text):
                errors.append(
                    {
                        "code": "carrier_uri_evidence",
                        "path": f"{prefix}/carrier",
                        "message": "URI carrier span has no URI-shaped source evidence",
                        "retryable": True,
                    }
                )
            if (
                carrier["value_type"] == "filesystem_path"
                and not PATH_RE.search(carrier_text)
            ):
                errors.append(
                    {
                        "code": "carrier_path_evidence",
                        "path": f"{prefix}/carrier",
                        "message": "path carrier span has no path-shaped source evidence",
                        "retryable": True,
                    }
                )

        sink = node["sink"]
        if sink["kind"] != "none" and not _span_ok(sink, token_count):
            errors.append(
                {
                    "code": "sink_span",
                    "path": f"{prefix}/sink",
                    "message": "active sink requires a non-zero in-range span",
                    "retryable": True,
                }
            )

        for claim_index, claim in enumerate(node["attribute_claims"]):
            if not _span_ok(claim, token_count):
                errors.append(
                    {
                        "code": "attribute_span",
                        "path": f"{prefix}/attribute_claims/{claim_index}",
                        "message": "attribute claim requires source evidence",
                        "retryable": True,
                    }
                )

        actual_refs = set(node["evidence_refs"])
        required_refs = expected_evidence_refs(node)
        if actual_refs != required_refs:
            errors.append(
                {
                    "code": "evidence_coverage",
                    "path": f"{prefix}/evidence_refs",
                    "message": (
                        f"evidence refs {sorted(actual_refs)} do not exactly cover "
                        f"active facets {sorted(required_refs)}"
                    ),
                    "retryable": True,
                }
            )

    return {"valid": not errors, "errors": errors}


def _carrier_value_type(
    carrier: dict[str, Any], tokens: list[str], rules: dict[str, Any]
) -> str:
    kind = carrier.get("kind")
    obj = carrier.get("object")
    span = _span_text(carrier, tokens)
    allowed = rules.get("carrier_value_types", {}).get(kind, {}).get(obj, [])
    if "uri" in allowed and URI_RE.search(span):
        return "uri"
    if "filesystem_path" in allowed and PATH_RE.search(span):
        return "filesystem_path"
    preferred = {
        "messages": "message_store" if kind == "source_container" else "message_channel",
        "images": "raster_image",
        "texts": "text_representation",
    }.get(obj)
    if preferred in allowed:
        return str(preferred)
    if "opaque_identifier" in allowed:
        return "opaque_identifier"
    if allowed:
        return str(allowed[0])
    return rules.get("default_carrier_value_type", {}).get(
        kind, "opaque_identifier"
    )


def _v24_evidence_refs(node: dict[str, Any]) -> list[str]:
    return sorted(expected_evidence_refs(node), key=EVIDENCE_REFS.index)


def migrate_v23(
    frame: dict[str, Any], tokens: list[str], rules: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Losslessly tag V23's ambiguous zero patient; never repair semantics."""
    rules = rules or load_rules()
    heads = frame.get("semantic_heads") or []
    predicates = frame.get("predicates") or []
    nodes: list[dict[str, Any]] = []
    for index, (head, predicate) in enumerate(zip(heads, predicates), 1):
        patient_start = head.get("patient_start_token_id", 0)
        patient_end = head.get("patient_end_token_id", 0)
        patient_object = head.get("patient_object", "none")
        if (patient_start or patient_end) and patient_object != "none":
            patient = {
                "kind": "explicit",
                "start_token_id": patient_start,
                "end_token_id": patient_end,
                "object": patient_object,
            }
        elif patient_start or patient_end:
            patient = {
                "kind": "anaphoric",
                "start_token_id": patient_start,
                "end_token_id": patient_end,
            }
        elif patient_object != "none":
            patient = {
                "kind": "implicit",
                "object": patient_object,
                "basis": "predicate_semantics",
            }
        else:
            patient = {"kind": "none"}

        old_carrier = head.get("carrier") or {"kind": "none"}
        if old_carrier.get("kind") == "none":
            carrier = {"kind": "none"}
        else:
            carrier = {
                "kind": old_carrier.get("kind"),
                "start_token_id": old_carrier.get("start_token_id"),
                "end_token_id": old_carrier.get("end_token_id"),
                "object": old_carrier.get("object"),
            }
            carrier["value_type"] = _carrier_value_type(carrier, tokens, rules)

        old_sink = head.get("sink") or {"kind": "none"}
        if old_sink.get("kind") == "none":
            sink = {"kind": "none"}
        else:
            destination_role = (
                "destination_locator"
                if old_sink.get("object") == "dirs"
                else "artifact_domain"
            )
            sink = {
                "kind": old_sink.get("kind"),
                "start_token_id": old_sink.get("start_token_id"),
                "end_token_id": old_sink.get("end_token_id"),
                "object": old_sink.get("object"),
                "destination_role": destination_role,
            }

        if predicate.get("role") == "request":
            route = {
                "kind": "candidate",
                "action": predicate.get("verb"),
                "object": predicate.get("object"),
                "qualifier": predicate.get("object_qualifier", "none"),
                "resolution": predicate.get("verb_resolution", "direct"),
            }
        else:
            route = {"kind": "none"}

        node = {
            "predicate_id": head.get("predicate_id", index),
            "predicate_anchor_token_id": head.get("predicate_anchor_token_id"),
            "role": head.get("role"),
            "lemma": head.get("lemma"),
            "semantic_gloss_en": head.get("semantic_gloss_en"),
            "semantic_action": head.get("semantic_action", "none"),
            "resource_scope": head.get("resource_scope"),
            "input_from_predicate_id": head.get("source_predicate_id", 0),
            "patient": patient,
            "carrier": carrier,
            "sink": sink,
            "route": route,
            "attribute_claims": [],
            "evidence_refs": [],
        }
        node["evidence_refs"] = _v24_evidence_refs(node)
        nodes.append(node)
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes}


def migrate_v21(
    record: dict[str, Any], rules: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Projection-only adapter; V21 has no patient/carrier grounding."""
    rules = rules or load_rules()
    frame = record.get("frame") or {}
    heads = frame.get("semantic_heads") or []
    predicates = frame.get("predicates") or []
    tokens = (record.get("transport_meta") or {}).get("tokens") or []
    if len(heads) != len(predicates):
        return None, [{"code": "legacy_phase_alignment", "retryable": True}]
    anchor_to_id: dict[int, int] = {}
    nodes: list[dict[str, Any]] = []
    for index, (head, predicate) in enumerate(zip(heads, predicates), 1):
        anchor = head.get("predicate_anchor_token_id")
        anchor_to_id[anchor] = index
        sink_mode = predicate.get("sink_mode", "none")
        materialize_to = predicate.get("materialize_to", "none")
        sink_anchor = predicate.get("sink_anchor_token_id", 0)
        if sink_mode == "explicit_persistence" and (
            materialize_to == "none" or not sink_anchor
        ):
            return None, [
                {
                    "code": "legacy_sink_alignment",
                    "message": "active legacy sink lacks object or non-zero evidence",
                    "retryable": True,
                }
            ]
        sink: dict[str, Any]
        if sink_mode == "explicit_persistence":
            sink = {
                "kind": "secondary_persistence",
                "start_token_id": sink_anchor,
                "end_token_id": sink_anchor,
                "object": materialize_to,
                "destination_role": "external_store",
            }
        else:
            sink = {"kind": "none"}
        source_anchor = predicate.get("input_from_predicate_anchor_id", 0)
        source_id = anchor_to_id.get(source_anchor, 0)
        route = (
            {
                "kind": "candidate",
                "action": predicate.get("verb"),
                "object": predicate.get("object"),
                "qualifier": predicate.get("object_qualifier", "none"),
                "resolution": predicate.get("verb_resolution", "direct"),
            }
            if predicate.get("role") == "request"
            else {"kind": "none"}
        )
        node = {
            "predicate_id": index,
            "predicate_anchor_token_id": anchor,
            "role": head.get("role"),
            "lemma": head.get("lemma"),
            "semantic_gloss_en": head.get("semantic_gloss_en"),
            "semantic_action": head.get("semantic_action", "none"),
            "resource_scope": head.get("resource_scope"),
            "input_from_predicate_id": source_id,
            "patient": {"kind": "none"},
            "carrier": {"kind": "none"},
            "sink": sink,
            "route": route,
            "attribute_claims": [],
            "evidence_refs": [],
        }
        node["evidence_refs"] = _v24_evidence_refs(node)
        nodes.append(node)
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes}, []


def _rule_matches(node: dict[str, Any], match: dict[str, Any]) -> bool:
    route = node["route"]
    tests = {
        "candidate_actions": route.get("action"),
        "candidate_objects": route.get("object"),
        "semantic_actions": node.get("semantic_action"),
        "resource_scopes": node.get("resource_scope"),
        "patient_kinds": node.get("patient", {}).get("kind"),
        "carrier_kinds": node.get("carrier", {}).get("kind"),
        "carrier_objects": node.get("carrier", {}).get("object"),
        "attribute_keys": {
            claim.get("key") for claim in node.get("attribute_claims", [])
        },
    }
    for key, allowed in match.items():
        actual = tests.get(key)
        if isinstance(actual, set):
            if not actual.intersection(allowed):
                return False
        elif actual not in allowed:
            return False
    return True


def _known_routes() -> set[str]:
    routes: set[str] = set()
    if CATALOG_MIGRATION_PATH.exists():
        migration = json.loads(CATALOG_MIGRATION_PATH.read_text())
        for entry in migration.get("entries", []):
            route = (entry.get("candidate_contract") or {}).get("route") or {}
            if route.get("kind") != "canonical":
                continue
            action = route.get("action")
            obj = route.get("object")
            qualifier = route.get("qualifier")
            if action and obj:
                rendered = obj if not qualifier else f"{obj}_{qualifier}"
                routes.add(f"{action}/{rendered}")
    return routes


def _render_route(route: dict[str, Any]) -> str:
    obj = route["object"]
    if route.get("qualifier") not in (None, "none"):
        obj = f"{obj}_{route['qualifier']}"
    return f"{route['action']}/{obj}"


def _signature_value(values: list[str]) -> str | list[str] | None:
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def project_v24(
    frame: dict[str, Any],
    tokens: list[str],
    *,
    mode: str = "research",
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a validated graph; never inspect query, lemma, or gloss."""
    rules = rules or load_rules()
    validation = validate_v24(frame, tokens, rules)
    if not validation["valid"]:
        return {
            "status": "fail_closed",
            "reason": "invalid_grounding",
            "validation": validation,
            "signature": None,
            "trace": [],
        }
    runtime_gate = rules["runtime_gate"]
    if mode == "runtime" and rules.get("review_state") != runtime_gate.get(
        "required_review_state"
    ):
        return {
            "status": "fail_closed",
            "reason": "catalog_contract_unreviewed",
            "validation": validation,
            "signature": None,
            "trace": [],
        }
    if mode not in {"runtime", "research"}:
        raise ValueError(f"unsupported projection mode: {mode}")

    catalog_routes = _known_routes()
    overlay_routes = set(
        rules.get("research_route_policy", {}).get("overlay_known_routes", [])
    )
    universal_actions = set(
        rules.get("research_route_policy", {}).get("universal_actions", [])
    )
    values: list[str] = []
    trace: list[dict[str, Any]] = []
    requested_nodes = [n for n in frame["nodes"] if n["role"] == "request"]
    for request_index, node in enumerate(requested_nodes):
        candidate = node["route"]
        route = {
            "action": candidate["action"],
            "object": candidate["object"],
            "qualifier": candidate["qualifier"],
        }
        original = copy.deepcopy(route)
        applied: list[dict[str, Any]] = []

        for rule in rules.get("route_rules", []):
            if not _rule_matches(node, rule.get("match", {})):
                continue
            before = copy.deepcopy(route)
            route.update(rule.get("project", {}))
            applied.append(
                {
                    "rule_id": rule["rule_id"],
                    "before": before,
                    "after": copy.deepcopy(route),
                    "evidence": rule.get("evidence_basis", []),
                }
            )

        carrier = node["carrier"]
        if carrier["kind"] != "none":
            domain = rules.get("domain_semantics", {}).get(carrier["object"], {})
            authority_field = {
                "source_container": "source_container_authority",
                "transport_channel": "transport_authority",
                "representation": "representation_authority",
            }[carrier["kind"]]
            authority = domain.get(authority_field, "context")
            carrier_policy = rules.get("carrier_projection", {})
            use_carrier = False
            if (
                authority == "source_owner"
                and route["action"]
                in carrier_policy.get("source_owner_actions", [])
            ):
                use_carrier = True
            elif (
                authority == "locator"
                and route["action"]
                in carrier_policy.get("locator_content_actions", [])
                and node["patient"]["kind"]
                in carrier_policy.get("locator_overrides_only_patient_kinds", [])
            ):
                use_carrier = True
            elif (
                authority == "destination_channel"
                and route["action"]
                in carrier_policy.get("destination_channel_actions", [])
            ):
                use_carrier = True
            if use_carrier and route["object"] != carrier["object"]:
                before = copy.deepcopy(route)
                route["object"] = carrier["object"]
                route["qualifier"] = "none"
                applied.append(
                    {
                        "rule_id": "catalog.typed_carrier_authority",
                        "before": before,
                        "after": copy.deepcopy(route),
                        "evidence": [
                            "carrier.kind",
                            "carrier.object",
                            "carrier.value_type",
                            "patient.kind",
                            "catalog.domain_semantics",
                        ],
                    }
                )

        claim_keys = {c["key"] for c in node.get("attribute_claims", [])}
        for binding in rules.get("attribute_bindings", []):
            if binding["claim_key"] not in claim_keys:
                continue
            if node["patient"].get("object") not in binding.get(
                "accepted_patient_objects", []
            ):
                continue
            before = copy.deepcopy(route)
            route = copy.deepcopy(binding["route"])
            applied.append(
                {
                    "rule_id": f"catalog.attribute.{binding['binding_id']}",
                    "before": before,
                    "after": copy.deepcopy(route),
                    "evidence": [
                        "attribute_claim.key",
                        "attribute_claim.span",
                        "patient.object",
                        "catalog.argument_contract",
                    ],
                }
            )

        rendered = _render_route(route)
        original_rendered = _render_route(original)
        if rendered != original_rendered:
            route_known = (
                rendered in catalog_routes
                or rendered in overlay_routes
                or route["action"] in universal_actions
            )
            if not route_known:
                return {
                    "status": "fail_closed",
                    "reason": "corrected_route_not_in_catalog_contract",
                    "validation": validation,
                    "signature": None,
                    "trace": trace + applied,
                }
        values.append(rendered)
        trace.append(
            {
                "predicate_id": node["predicate_id"],
                "candidate": original_rendered,
                "projected": rendered,
                "rules": applied,
                "evidence_coverage": sorted(expected_evidence_refs(node)),
                "catalog_status": rules.get("review_state"),
            }
        )

        sink = node["sink"]
        if sink["kind"] != "secondary_persistence":
            continue
        sink_contract = rules.get("materialization", {}).get("sinks", {}).get(
            sink["object"]
        )
        if not sink_contract:
            return {
                "status": "fail_closed",
                "reason": "secondary_sink_not_in_catalog_contract",
                "validation": validation,
                "signature": None,
                "trace": trace,
            }
        materialization_actions = set(
            rules.get("materialization", {}).get("materialization_actions", [])
        )
        duplicate_consumer = any(
            later["route"]["kind"] == "candidate"
            and later["input_from_predicate_id"] == node["predicate_id"]
            and later["route"]["action"] in materialization_actions
            and later["route"]["object"] == sink["object"]
            for later in requested_nodes[request_index + 1 :]
        )
        sink_rendered = _render_route(sink_contract["route"])
        owns_same_domain = (
            route["action"] in materialization_actions
            and route["object"] == sink["object"]
        )
        if not duplicate_consumer and not owns_same_domain:
            values.append(sink_rendered)
            trace.append(
                {
                    "predicate_id": node["predicate_id"],
                    "candidate": None,
                    "projected": sink_rendered,
                    "rules": [
                        {
                            "rule_id": "catalog.secondary_persistence",
                            "evidence": [
                                "sink.kind",
                                "sink.object",
                                "sink.span",
                                "catalog.materialization_sink",
                            ],
                        }
                    ],
                    "evidence_coverage": ["sink"],
                    "catalog_status": sink_contract.get("review_state"),
                }
            )

    return {
        "status": "projected",
        "reason": "",
        "validation": validation,
        "signature": _signature_value(values),
        "trace": trace,
    }


def analyze_with_retry(
    call: Callable[[int, list[dict[str, Any]]], tuple[dict[str, Any], list[str]]],
    *,
    mode: str = "research",
    max_attempts: int = 2,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One constrained retry, then an explicit non-executable failure."""
    rules = rules or load_rules()
    feedback: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        frame, tokens = call(attempt, feedback)
        validation = validate_v24(frame, tokens, rules)
        attempts.append(
            {
                "attempt": attempt,
                "valid": validation["valid"],
                "errors": validation["errors"],
            }
        )
        if validation["valid"]:
            projection = project_v24(frame, tokens, mode=mode, rules=rules)
            return {
                "status": projection["status"],
                "attempts": attempts,
                "projection": projection,
            }
        feedback = [
            {
                "code": error["code"],
                "path": error.get("path", ""),
                "message": error.get("message", ""),
            }
            for error in validation["errors"]
            if error.get("retryable")
        ]
    return {
        "status": "fail_closed",
        "reason": "grounding_invalid_after_retry",
        "attempts": attempts,
        "projection": None,
    }


def _base_frame(
    *,
    action: str = "read",
    obj: str = "files",
    scope: str = "single_known",
    patient: dict[str, Any] | None = None,
    carrier: dict[str, Any] | None = None,
    sink: dict[str, Any] | None = None,
    attributes: list[dict[str, Any]] | None = None,
    semantic_action: str | None = None,
    role: str = "request",
    source: int = 0,
) -> dict[str, Any]:
    node = {
        "predicate_id": 1,
        "predicate_anchor_token_id": 1,
        "role": role,
        "lemma": action,
        "semantic_gloss_en": action,
        "semantic_action": semantic_action or action,
        "resource_scope": scope,
        "input_from_predicate_id": source,
        "patient": patient or {
            "kind": "explicit",
            "start_token_id": 2,
            "end_token_id": 2,
            "object": obj,
        },
        "carrier": carrier or {"kind": "none"},
        "sink": sink or {"kind": "none"},
        "route": (
            {
                "kind": "candidate",
                "action": action,
                "object": obj,
                "qualifier": "none",
                "resolution": "direct",
            }
            if role == "request"
            else {"kind": "none"}
        ),
        "attribute_claims": attributes or [],
        "evidence_refs": [],
    }
    node["evidence_refs"] = _v24_evidence_refs(node)
    return {"schema_version": SCHEMA_VERSION, "nodes": [node]}


def run_mutations() -> dict[str, Any]:
    """Mutation and boundary tests independent of natural-language fixtures."""
    rules = load_rules()
    tests: list[dict[str, Any]] = []

    def structural(
        test_id: str,
        mutate: Callable[[dict[str, Any]], None],
        expected_valid: bool,
        expected_code: str | None = None,
        tokens: list[str] | None = None,
    ) -> None:
        frame = _base_frame()
        mutate(frame)
        result = validate_v24(frame, tokens or ["read", "file"], rules)
        codes = {e["code"] for e in result["errors"]}
        passed = result["valid"] == expected_valid and (
            expected_code is None or expected_code in codes
        )
        tests.append(
            {
                "id": test_id,
                "kind": "validator_mutation",
                "pass": passed,
                "expected_valid": expected_valid,
                "actual_valid": result["valid"],
                "expected_code": expected_code,
                "actual_codes": sorted(codes),
            }
        )

    structural("valid.base", lambda _: None, True)
    structural(
        "reject.patient_none_stale",
        lambda f: f["nodes"][0].update(
            {"patient": {"kind": "none", "object": "files"}}
        ),
        False,
        "schema",
    )
    structural(
        "reject.patient_explicit_zero",
        lambda f: f["nodes"][0].update(
            {
                "patient": {
                    "kind": "explicit",
                    "start_token_id": 0,
                    "end_token_id": 0,
                    "object": "files",
                }
            }
        ),
        False,
        "schema",
    )
    structural(
        "reject.patient_implicit_without_basis",
        lambda f: f["nodes"][0].update(
            {"patient": {"kind": "implicit", "object": "files"}}
        ),
        False,
        "schema",
    )
    structural(
        "reject.patient_anaphor_without_source",
        lambda f: f["nodes"][0].update(
            {
                "patient": {
                    "kind": "anaphoric",
                    "start_token_id": 2,
                    "end_token_id": 2,
                }
            }
        ),
        False,
        "anaphor_without_source",
    )
    structural(
        "reject.carrier_none_stale",
        lambda f: f["nodes"][0].update(
            {"carrier": {"kind": "none", "object": "urls"}}
        ),
        False,
        "schema",
    )
    structural(
        "reject.carrier_active_zero",
        lambda f: f["nodes"][0].update(
            {
                "carrier": {
                    "kind": "transport_channel",
                    "start_token_id": 0,
                    "end_token_id": 0,
                    "object": "urls",
                    "value_type": "uri",
                }
            }
        ),
        False,
        "schema",
    )
    structural(
        "reject.carrier_type_mismatch",
        lambda f: f["nodes"][0].update(
            {
                "carrier": {
                    "kind": "transport_channel",
                    "start_token_id": 2,
                    "end_token_id": 2,
                    "object": "urls",
                    "value_type": "filesystem_path",
                },
                "evidence_refs": ["predicate", "patient", "carrier"],
            }
        ),
        False,
        "carrier_type",
    )
    structural(
        "reject.sink_active_zero",
        lambda f: f["nodes"][0].update(
            {
                "sink": {
                    "kind": "primary_output",
                    "start_token_id": 0,
                    "end_token_id": 0,
                    "object": "files",
                    "destination_role": "artifact_domain",
                }
            }
        ),
        False,
        "schema",
    )
    structural(
        "reject.sink_none_stale",
        lambda f: f["nodes"][0].update(
            {"sink": {"kind": "none", "object": "files"}}
        ),
        False,
        "schema",
    )
    structural(
        "reject.forward_source_edge",
        lambda f: f["nodes"][0].update(
            {"input_from_predicate_id": 1, "evidence_refs": ["predicate", "patient", "source"]}
        ),
        False,
        "source_edge",
    )
    structural(
        "reject.nonrequest_executable",
        lambda f: f["nodes"][0].update({"role": "description"}),
        False,
        "nonrequest_executable",
    )
    structural(
        "reject.request_without_route",
        lambda f: f["nodes"][0].update({"route": {"kind": "none"}}),
        False,
        "request_without_route",
    )
    structural(
        "reject.evidence_coverage_missing_carrier",
        lambda f: f["nodes"][0].update(
            {
                "carrier": {
                    "kind": "source_container",
                    "start_token_id": 2,
                    "end_token_id": 2,
                    "object": "dirs",
                    "value_type": "opaque_identifier",
                }
            }
        ),
        False,
        "evidence_coverage",
    )
    structural(
        "reject.literal_predicate_anchor",
        lambda _: None,
        False,
        "literal_anchor",
        ["https://example.com", "file"],
    )

    def boundary(
        test_id: str,
        frame: dict[str, Any],
        tokens: list[str],
        expected: Any,
    ) -> None:
        projection = project_v24(frame, tokens, mode="research", rules=rules)
        tests.append(
            {
                "id": test_id,
                "kind": "projector_boundary",
                "pass": projection.get("signature") == expected,
                "expected": expected,
                "actual": projection.get("signature"),
                "status": projection.get("status"),
                "trace": projection.get("trace"),
            }
        )

    boundary(
        "project.open_to_read",
        _base_frame(action="open", obj="files"),
        ["open", "file"],
        "read/files",
    )
    url_frame = _base_frame(
        action="read",
        obj="files",
        carrier={
            "kind": "transport_channel",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "urls",
            "value_type": "uri",
        },
    )
    url_frame["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        url_frame["nodes"][0]
    )
    boundary(
        "project.url_carrier_owns_read",
        url_frame,
        ["read", "content", "https://example.com/a.json"],
        "read/urls",
    )
    dirs_explicit = _base_frame(
        action="list",
        obj="files",
        scope="collection_by_criterion",
        carrier={
            "kind": "source_container",
            "start_token_id": 4,
            "end_token_id": 4,
            "object": "dirs",
            "value_type": "filesystem_path",
        },
    )
    dirs_explicit["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        dirs_explicit["nodes"][0]
    )
    boundary(
        "project.explicit_files_not_replaced_by_directory",
        dirs_explicit,
        ["list", "files", "in", "/tmp"],
        "list/files",
    )
    dirs_implicit = _base_frame(
        action="list",
        obj="files",
        scope="collection_by_criterion",
        patient={
            "kind": "implicit",
            "object": "files",
            "basis": "predicate_semantics",
        },
        carrier={
            "kind": "source_container",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "dirs",
            "value_type": "filesystem_path",
        },
    )
    dirs_implicit["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        dirs_implicit["nodes"][0]
    )
    boundary(
        "project.implicit_directory_content_uses_container",
        dirs_implicit,
        ["list", "content", "/tmp"],
        "list/dirs",
    )
    boundary(
        "project.process_collection_snapshot",
        _base_frame(
            action="find",
            obj="processes",
            scope="collection_by_criterion",
        ),
        ["find", "processes"],
        "get/processes",
    )
    boundary(
        "project.process_single_existence",
        _base_frame(action="find", obj="processes", scope="single_known"),
        ["find", "process"],
        "find/processes",
    )
    messages_frame = _base_frame(
        action="find",
        obj="entries",
        scope="collection_by_criterion",
        carrier={
            "kind": "source_container",
            "start_token_id": 4,
            "end_token_id": 4,
            "object": "messages",
            "value_type": "message_store",
        },
    )
    messages_frame["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        messages_frame["nodes"][0]
    )
    boundary(
        "project.search_in_messages",
        messages_frame,
        ["find", "entries", "in", "mailbox"],
        "find/messages",
    )
    exif_frame = _base_frame(
        action="change",
        obj="images",
        scope="collection_by_criterion",
        attributes=[
            {
                "kind": "catalog_attribute",
                "key": "media_metadata",
                "start_token_id": 3,
                "end_token_id": 3,
            }
        ],
    )
    exif_frame["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        exif_frame["nodes"][0]
    )
    boundary(
        "project.media_metadata_via_get_files_fields",
        exif_frame,
        ["enrich", "images", "EXIF"],
        "get/files",
    )
    primary_sink = _base_frame(
        action="move",
        obj="files",
        sink={
            "kind": "primary_output",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "dirs",
            "destination_role": "destination_locator",
        },
    )
    primary_sink["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        primary_sink["nodes"][0]
    )
    boundary(
        "project.primary_sink_does_not_replace_patient_domain",
        primary_sink,
        ["move", "files", "/tmp"],
        "move/files",
    )
    secondary_sink = _base_frame(
        action="extract",
        obj="entries",
        scope="held_result",
        sink={
            "kind": "secondary_persistence",
            "start_token_id": 3,
            "end_token_id": 3,
            "object": "entries",
            "destination_role": "external_store",
        },
    )
    secondary_sink["nodes"][0]["evidence_refs"] = _v24_evidence_refs(
        secondary_sink["nodes"][0]
    )
    boundary(
        "project.secondary_persistence_adds_sink",
        secondary_sink,
        ["extract", "amounts", "store"],
        ["extract/entries", "write/entries"],
    )
    explicit_consumer = copy.deepcopy(secondary_sink)
    explicit_consumer["nodes"].append(
        {
            "predicate_id": 2,
            "predicate_anchor_token_id": 4,
            "role": "request",
            "lemma": "write",
            "semantic_gloss_en": "write",
            "semantic_action": "write",
            "resource_scope": "new_resource",
            "input_from_predicate_id": 1,
            "patient": {
                "kind": "implicit",
                "object": "entries",
                "basis": "deictic_context",
            },
            "carrier": {"kind": "none"},
            "sink": {"kind": "none"},
            "route": {
                "kind": "candidate",
                "action": "write",
                "object": "entries",
                "qualifier": "none",
                "resolution": "direct",
            },
            "attribute_claims": [],
            "evidence_refs": ["predicate", "patient", "source"],
        }
    )
    boundary(
        "project.explicit_consumer_suppresses_secondary_duplicate",
        explicit_consumer,
        ["extract", "amounts", "store", "write", "entries"],
        ["extract/entries", "write/entries"],
    )
    boundary(
        "project.send_route_owns_message_envelope",
        _base_frame(action="send", obj="files", scope="held_result"),
        ["send", "archive"],
        "send/messages",
    )

    invalid = _base_frame()
    invalid["nodes"][0]["patient"] = {
        "kind": "explicit",
        "start_token_id": 0,
        "end_token_id": 0,
        "object": "files",
    }
    valid = _base_frame()

    retry_success = analyze_with_retry(
        lambda attempt, _: (invalid if attempt == 1 else valid, ["read", "file"]),
        rules=rules,
    )
    tests.append(
        {
            "id": "retry.one_repair_then_project",
            "kind": "retry_policy",
            "pass": retry_success["status"] == "projected"
            and len(retry_success["attempts"]) == 2,
            "actual": retry_success,
        }
    )
    retry_failure = analyze_with_retry(
        lambda _attempt, _feedback: (invalid, ["read", "file"]), rules=rules
    )
    tests.append(
        {
            "id": "retry.two_invalid_fail_closed",
            "kind": "retry_policy",
            "pass": retry_failure["status"] == "fail_closed"
            and retry_failure.get("projection") is None,
            "actual": retry_failure,
        }
    )
    invalid_projection = project_v24(
        invalid, ["read", "file"], mode="research", rules=rules
    )
    tests.append(
        {
            "id": "projector.invalid_graph_never_projects",
            "kind": "runtime_gate",
            "pass": invalid_projection["status"] == "fail_closed"
            and invalid_projection["signature"] is None,
            "actual": invalid_projection,
        }
    )
    runtime_gate = project_v24(valid, ["read", "file"], mode="runtime", rules=rules)
    tests.append(
        {
            "id": "runtime.unreviewed_catalog_fails_closed",
            "kind": "runtime_gate",
            "pass": runtime_gate["status"] == "fail_closed"
            and runtime_gate["reason"] == "catalog_contract_unreviewed",
            "actual": runtime_gate,
        }
    )

    result = {
        "suite_version": "metnos.v24-mutations/1.0",
        "rules_sha256": sha256_path(RULES_PATH),
        "schema_sha256": sha256_path(SCHEMA_PATH) if SCHEMA_PATH.exists() else None,
        "summary": {
            "tests": len(tests),
            "passed": sum(t["pass"] for t in tests),
            "failed": sum(not t["pass"] for t in tests),
            "by_kind": {
                kind: {
                    "tests": sum(t["kind"] == kind for t in tests),
                    "passed": sum(t["kind"] == kind and t["pass"] for t in tests),
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


def raw_v23_signature(record: dict[str, Any]) -> Any:
    values = []
    for predicate in (record.get("frame") or {}).get("predicates", []):
        if predicate.get("role") != "request":
            continue
        obj = predicate.get("object")
        qualifier = predicate.get("object_qualifier")
        if qualifier not in (None, "none"):
            obj = f"{obj}_{qualifier}"
        values.append(f"{predicate.get('verb')}/{obj}")
    return _signature_value(values)


def raw_v21_signature(record: dict[str, Any]) -> list[str]:
    return _as_list(raw_v23_signature(record))


def _slice_flags_v23(frame: dict[str, Any]) -> list[str]:
    flags: set[str] = set()
    for head, predicate in zip(
        frame.get("semantic_heads") or [], frame.get("predicates") or []
    ):
        if (
            head.get("patient_start_token_id") == 0
            and head.get("patient_end_token_id") == 0
        ):
            flags.add("patient_span0")
        sink = head.get("sink") or {}
        if sink.get("kind") != "none" and (
            sink.get("start_token_id") == 0 or sink.get("end_token_id") == 0
        ):
            flags.add("active_sink_span0")
        carrier = head.get("carrier") or {}
        if carrier.get("object") == "urls":
            flags.add("url_carrier")
        if carrier.get("object") == "dirs":
            flags.add("directory_carrier")
        if carrier.get("object") == "messages":
            flags.add("message_carrier")
        if predicate.get("verb") == "open":
            flags.add("open_candidate")
        if predicate.get("object") == "processes":
            flags.add("process_domain")
        if predicate.get("verb") == "change" and predicate.get("object") in {
            "files",
            "images",
        }:
            flags.add("attribute_claim_required_but_absent")
    return sorted(flags)


def _clause_score(expected: Any, actual: Any) -> tuple[int, int]:
    exp = _as_list(expected)
    got = _as_list(actual)
    return sum(a == b for a, b in zip(exp, got)), len(exp)


def cross_validate() -> dict[str, Any]:
    lock = verify_freeze(mark_started=True)
    rules = load_rules()
    mutation = run_mutations()
    if mutation["summary"]["failed"]:
        raise RuntimeError("mutation suite failed; cross-validation aborted")

    v23_records: list[dict[str, Any]] = []
    for path in sorted(glob.glob(V23_GLOB)):
        rows = json.loads(Path(path).read_text())
        for row in rows:
            row = copy.deepcopy(row)
            row["_source"] = path
            v23_records.append(row)

    v23_results: list[dict[str, Any]] = []
    for index, record in enumerate(v23_records, 1):
        tokens = (record.get("meta") or {}).get("tokens") or []
        migrated = migrate_v23(record.get("frame") or {}, tokens, rules)
        validation = validate_v24(migrated, tokens, rules)
        projection = project_v24(migrated, tokens, mode="research", rules=rules)
        runtime_projection = project_v24(
            migrated, tokens, mode="runtime", rules=rules
        )
        expected = record.get("expected")
        raw = raw_v23_signature(record)
        folded = record.get("folded")
        v24 = projection.get("signature")
        matched, clauses = _clause_score(expected, v24)
        v23_results.append(
            {
                "index": index,
                "group": record.get("group"),
                "source": record["_source"],
                "query": record.get("query"),
                "expected": expected,
                "raw_v23": raw,
                "folded_v23": folded,
                "v24_research": v24,
                "v24_status": projection.get("status"),
                "runtime_status": runtime_projection.get("status"),
                "valid_v24": validation["valid"],
                "validation_codes": sorted(
                    {error["code"] for error in validation["errors"]}
                ),
                "exact_raw": raw == expected,
                "exact_folded": folded == expected,
                "exact_v24": v24 == expected,
                "clause_exact_v24": matched,
                "clause_total": clauses,
                "slice_flags": _slice_flags_v23(record.get("frame") or {}),
                "applied_rules": [
                    applied["rule_id"]
                    for trace in projection.get("trace", [])
                    for applied in trace.get("rules", [])
                ],
            }
        )

    def summarize_v23(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "records": len(rows),
            "v24_valid": sum(r["valid_v24"] for r in rows),
            "v24_fail_closed": sum(r["v24_status"] == "fail_closed" for r in rows),
            "runtime_fail_closed": sum(
                r["runtime_status"] == "fail_closed" for r in rows
            ),
            "exact": {
                "raw_v23": sum(r["exact_raw"] for r in rows),
                "folded_v23": sum(r["exact_folded"] for r in rows),
                "v24_research": sum(r["exact_v24"] for r in rows),
            },
            "clause_exact_v24": sum(r["clause_exact_v24"] for r in rows),
            "clause_total": sum(r["clause_total"] for r in rows),
            "delta_vs_raw": {
                "improvements": sum(
                    (not r["exact_raw"]) and r["exact_v24"] for r in rows
                ),
                "regressions": sum(
                    r["exact_raw"] and (not r["exact_v24"]) for r in rows
                ),
                "net": sum(r["exact_v24"] for r in rows)
                - sum(r["exact_raw"] for r in rows),
            },
            "delta_vs_folded": {
                "improvements": sum(
                    (not r["exact_folded"]) and r["exact_v24"] for r in rows
                ),
                "regressions": sum(
                    r["exact_folded"] and (not r["exact_v24"]) for r in rows
                ),
                "net": sum(r["exact_v24"] for r in rows)
                - sum(r["exact_folded"] for r in rows),
            },
        }

    slice_names = sorted(
        {flag for row in v23_results for flag in row["slice_flags"]}
    )
    slice_summary = {
        name: summarize_v23(
            [row for row in v23_results if name in row["slice_flags"]]
        )
        for name in slice_names
    }

    v21_data = json.loads(V21_PATH.read_text())
    v21_results: list[dict[str, Any]] = []
    for record in v21_data.get("records", []):
        tokens = (record.get("transport_meta") or {}).get("tokens") or []
        migrated, conversion_errors = migrate_v21(record, rules)
        if migrated is None:
            validation = {"valid": False, "errors": conversion_errors}
            projection = {
                "status": "fail_closed",
                "reason": "legacy_conversion_invalid",
                "signature": None,
                "trace": [],
            }
        else:
            validation = validate_v24(migrated, tokens, rules)
            projection = project_v24(migrated, tokens, mode="research", rules=rules)
        expected = record["expected"]["requested_signature"]
        raw = raw_v21_signature(record)
        compiler = _as_list((record.get("score") or {}).get("compiler_signature"))
        projected = _as_list(projection.get("signature"))
        v21_results.append(
            {
                "id": record.get("id"),
                "family": record.get("family"),
                "language": record.get("language"),
                "expected": expected,
                "raw_v21": raw,
                "compiler_v21": compiler,
                "v24_projection_only": projected,
                "valid_v24_adapter": validation["valid"],
                "v24_status": projection.get("status"),
                "exact_raw": raw == expected,
                "exact_compiler": compiler == expected,
                "exact_v24": projected == expected,
                "conversion_errors": [
                    error.get("code") for error in validation.get("errors", [])
                ],
                "applied_rules": [
                    applied["rule_id"]
                    for trace in projection.get("trace", [])
                    for applied in trace.get("rules", [])
                ],
            }
        )

    accepted_v21 = [r for r in v21_results if r["valid_v24_adapter"]]
    v21_summary = {
        "records": len(v21_results),
        "adapter_valid": len(accepted_v21),
        "adapter_fail_closed": sum(
            r["v24_status"] == "fail_closed" for r in v21_results
        ),
        "grounding_evaluable": 0,
        "grounding_note": (
            "V21 has no patient/carrier spans; only role/route/source/sink "
            "projection compatibility is measurable."
        ),
        "full_denominator_exact": {
            "raw_signature_including_invalid": sum(r["exact_raw"] for r in v21_results),
            "compiler_v21": sum(r["exact_compiler"] for r in v21_results),
            "v24_projection_only": sum(r["exact_v24"] for r in v21_results),
        },
        "accepted_denominator_exact": {
            "denominator": len(accepted_v21),
            "raw_v21": sum(r["exact_raw"] for r in accepted_v21),
            "compiler_v21": sum(r["exact_compiler"] for r in accepted_v21),
            "v24_projection_only": sum(r["exact_v24"] for r in accepted_v21),
        },
        "delta_vs_raw_on_accepted": {
            "improvements": sum(
                (not r["exact_raw"]) and r["exact_v24"] for r in accepted_v21
            ),
            "regressions": sum(
                r["exact_raw"] and (not r["exact_v24"]) for r in accepted_v21
            ),
        },
    }

    results = {
        "suite_version": "metnos.v24-offline-cross-validation/1.0",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "freeze": lock,
        "catalog_gate": {
            "prototype_review_state": rules.get("review_state"),
            "production_ready_manifest_contracts": 0,
            "runtime_cutover_eligible": False,
            "runtime_behavior": "fail_closed",
            "research_behavior": "frozen_shadow_projection",
        },
        "mutation_summary": mutation["summary"],
        "v23": {
            "summary": summarize_v23(v23_results),
            "slices": slice_summary,
            "improvements_vs_raw": [
                r for r in v23_results if (not r["exact_raw"]) and r["exact_v24"]
            ],
            "regressions_vs_raw": [
                r for r in v23_results if r["exact_raw"] and (not r["exact_v24"])
            ],
            "remaining_errors": [r for r in v23_results if not r["exact_v24"]],
            "records": v23_results,
        },
        "v21_adversarial": {
            "summary": v21_summary,
            "records": v21_results,
        },
    }
    RESULTS_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-schema", action="store_true")
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--mutations", action="store_true")
    parser.add_argument("--cross-validate", action="store_true")
    args = parser.parse_args()
    did_work = False
    if args.emit_schema:
        schema = emit_schema()
        print(json.dumps({"schema": str(SCHEMA_PATH), "nodes": schema["properties"]["nodes"]["minItems"]}))
        did_work = True
    if args.freeze:
        print(json.dumps(freeze(), ensure_ascii=False, sort_keys=True))
        did_work = True
    if args.mutations:
        if not SCHEMA_PATH.exists():
            emit_schema()
        result = run_mutations()
        print(json.dumps(result["summary"], sort_keys=True))
        did_work = True
    if args.cross_validate:
        result = cross_validate()
        print(
            json.dumps(
                {
                    "v23": result["v23"]["summary"],
                    "v21": result["v21_adversarial"]["summary"],
                    "mutations": result["mutation_summary"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        did_work = True
    if not did_work:
        parser.error("select at least one action")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
