"""Declarative contracts for the private image-question durable preset.

This module contains domain data only: output schemas, registered workload
contracts and a candidate plan.  It never starts a worker, opens a database,
reads a source path or calls a model.  The generic engine remains responsible
for materialisation, leases, retries and idempotency.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .compiler import (
    ApprovedOutputSchema,
    CompositeRunnerResolver,
    OutputSchemaRegistry,
    RegisteredWorkloadResolver,
    VerifiedCatalogResolver,
)
from .schema import MAX_SNAPSHOT_JSON_BYTES, digest_json


PRESET_ID = "images.questions.v1"
SEMANTIC_SCHEMA_VERSION = "metnos.images.questions.semantic/1"


def _resources(**selected: int) -> dict[str, int]:
    values = {
        "cpu": 0,
        "local_io": 0,
        "network_io": 0,
        "llm": 0,
        "vlm": 0,
        "device": 0,
    }
    values.update(selected)
    return values


def _retry(*, attempts: int = 3) -> dict[str, Any]:
    return {
        "max_attempts": attempts,
        "base_delay_ms": 1_000 if attempts > 1 else 0,
        "max_delay_ms": 30_000 if attempts > 1 else 0,
        "retryable_error_classes": ["executor_transient"] if attempts > 1 else [],
    }


def _reference(name: str) -> dict[str, str]:
    return {"schema_version": "metnos.output-schema-ref/1", "name": name}


def _entries_schema(
    name: str,
    entry: Mapping[str, Any],
    *,
    required_entry_fields: tuple[str, ...],
) -> ApprovedOutputSchema:
    return ApprovedOutputSchema.create(
        name,
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "entries": {
                    "type": "array",
                    "maxItems": 1_000_000,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": dict(entry),
                        "required": list(required_entry_fields),
                    },
                },
            },
            "required": ["entries"],
        },
    )


def output_schemas() -> OutputSchemaRegistry:
    """Return the closed output-schema registry needed by this preset."""
    source_id = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,160}$"}
    digest = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"}
    key = {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"}
    schemas = (
        ApprovedOutputSchema.create(
            "metnos.inventory-seal/1",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "digest": digest,
                    "sources": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["digest", "sources"],
            },
        ),
        ApprovedOutputSchema.create(
            "metnos.durable.ocr-source/1",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ok": {"type": "boolean"},
                    "ok_count": {"type": "integer", "minimum": 0},
                    "fail_count": {"type": "integer", "minimum": 0},
                    "source_id": source_id,
                    "entries": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "source_id": source_id,
                                "content": {"type": "string", "maxLength": 500_000},
                                "char_count": {"type": "integer", "minimum": 0},
                                "lang": {"type": "string", "maxLength": 64},
                            },
                            "required": ["source_id", "content", "char_count", "lang"],
                        },
                    },
                    "failed": {"type": "array", "maxItems": 100, "items": {"type": "object"}},
                },
                "required": ["ok", "ok_count", "fail_count", "source_id", "entries", "failed"],
            },
        ),
        _entries_schema(
            "metnos.images.question-occurrences/1",
            {
                "question_occurrence_id": key,
                "canonical_question_key": key,
                "source_id": source_id,
                "coordinate_locale": {"type": "string", "maxLength": 256},
                "original_text": {"type": "string", "maxLength": 20_000},
                "normalized_text": {"type": "string", "maxLength": 20_000},
                "normalized_text_hash": digest,
                "semantic_schema_version": {"const": SEMANTIC_SCHEMA_VERSION},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            required_entry_fields=(
                "question_occurrence_id", "canonical_question_key", "source_id",
                "coordinate_locale", "original_text", "normalized_text",
                "normalized_text_hash", "semantic_schema_version", "confidence",
            ),
        ),
        _entries_schema(
            "metnos.images.canonical-questions/1",
            {
                "canonical_question_key": key,
                "normalized_text": {"type": "string", "maxLength": 20_000},
                "semantic_schema_version": {"const": SEMANTIC_SCHEMA_VERSION},
                "occurrence_ids": {
                    "type": "array", "minItems": 1, "maxItems": 1_000_000,
                    "items": key, "uniqueItems": True,
                },
            },
            required_entry_fields=(
                "canonical_question_key", "normalized_text", "semantic_schema_version",
                "occurrence_ids",
            ),
        ),
        _entries_schema(
            "metnos.images.answers/1",
            {
                "canonical_question_key": key,
                "status": {"enum": ["answered", "unresolved"]},
                "answer": {"type": "string", "maxLength": 100_000},
                "reason": {"type": "string", "maxLength": 2_000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            required_entry_fields=(
                "canonical_question_key", "status", "answer", "reason", "confidence",
            ),
        ),
        _entries_schema(
            "metnos.images.answer-validation/1",
            {
                "canonical_question_key": key,
                "valid": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 2_000},
            },
            required_entry_fields=("canonical_question_key", "valid", "reason"),
        ),
        _entries_schema(
            "metnos.images.reduction/1",
            {
                "kind": {"enum": ["notes", "formulae"]},
                "markdown": {"type": "string", "maxLength": 500_000},
            },
            required_entry_fields=("kind", "markdown"),
        ),
        _entries_schema(
            "metnos.images.assembly/1",
            {
                "kind": {"enum": ["solutions", "coverage"]},
                "markdown": {"type": "string", "maxLength": 1_000_000},
            },
            required_entry_fields=("kind", "markdown"),
        ),
        _entries_schema(
            "metnos.images.validation/1",
            {
                "valid": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 2_000},
            },
            required_entry_fields=("valid", "reason"),
        ),
        _entries_schema(
            "metnos.internal-artifacts/1",
            {
                "logical_name": {"type": "string", "maxLength": 64},
                "artifact_id": {"type": "string", "maxLength": 128},
                "digest": digest,
            },
            required_entry_fields=("logical_name", "artifact_id", "digest"),
        ),
    )
    return OutputSchemaRegistry(schemas)


_WORKLOAD_SPECS: Mapping[str, Mapping[str, Any]] = {
    "durable.images.extract_questions": {
        "output": "metnos.images.question-occurrences/1",
        "inputs": {"ocr_entries": "array", "source": "object"},
        "required": ("ocr_entries", "source"),
        "prompt": "Extract every visible question. Return bounded JSON only.",
    },
    "durable.images.deduplicate": {
        "output": "metnos.images.canonical-questions/1",
        "inputs": {"occurrences": "array"},
        "required": ("occurrences",),
        "prompt": "Group only semantically equivalent questions and preserve every occurrence.",
    },
    "durable.images.answer": {
        "output": "metnos.images.answers/1",
        "inputs": {"question": "object"},
        "required": ("question",),
        "prompt": "Answer the canonical question or declare it unresolved. Return JSON only.",
    },
    "durable.images.validate": {
        "output": "metnos.images.answer-validation/1",
        "inputs": {"answer": "object"},
        "required": ("answer",),
        "prompt": "Check the answer structure and evidence. Return JSON only.",
    },
    "durable.images.reduce_notes": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": "Produce concise Markdown notes from the answers. Return JSON only.",
    },
    "durable.images.reduce_formulae": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": "Produce a Markdown formula sheet from the answers. Return JSON only.",
    },
    "durable.images.assemble": {
        "output": "metnos.images.assembly/1",
        "inputs": {
            "answers": "array", "validation": "array", "notes": "array", "formulae": "array",
        },
        "required": ("answers", "validation", "notes", "formulae"),
        "prompt": "Assemble ordered solutions and coverage Markdown. Return JSON only.",
    },
}


def workload_prompt_digests() -> dict[str, str]:
    return {
        name: digest_json(
            "durable-image-preset-prompt",
            {"name": name, "prompt": spec["prompt"]},
            max_bytes=MAX_SNAPSHOT_JSON_BYTES,
        )
        for name, spec in _WORKLOAD_SPECS.items()
    }


def runner_resolver(
    *,
    catalog_loader: Callable[..., Any] | None = None,
    binding_resolver: Callable[..., Mapping[str, Any]] | None = None,
) -> CompositeRunnerResolver:
    """Create the closed resolver used when admitting this private preset."""
    return CompositeRunnerResolver(
        executors=VerifiedCatalogResolver(
            catalog_loader=catalog_loader,
            durable_effects={"read_files_ocr": ("pure",)},
            durable_output_schemas={"read_files_ocr": ("metnos.durable.ocr-source/1",)},
        ),
        workloads=RegisteredWorkloadResolver(
            output_schemas={name: (str(spec["output"]),) for name, spec in _WORKLOAD_SPECS.items()},
            input_names={name: tuple(spec["inputs"]) for name, spec in _WORKLOAD_SPECS.items()},
            input_types={name: dict(spec["inputs"]) for name, spec in _WORKLOAD_SPECS.items()},
            required_inputs={name: tuple(spec["required"]) for name, spec in _WORKLOAD_SPECS.items()},
            prompt_digests=workload_prompt_digests(),
            binding_resolver=binding_resolver,
        ),
    )


def image_questions_plan() -> dict[str, Any]:
    """Return a source-agnostic private candidate plan for image questions."""
    return {
        "schema_version": "metnos.durable-plan/1",
        "plan_id": PRESET_ID,
        "objective_redacted": "Analizzare immagini sigillate e produrre soluzioni verificabili.",
        "inventory": {
            "mode": "sealed", "dynamic": False, "max_sources": 1_000_000,
            "max_total_bytes": 1_099_511_627_776, "max_depth": 64,
            "symlink_policy": "ignore", "unstable_policy": "reject",
            "missing_policy": "needs_attention",
        },
        "terminal_criteria": {
            "require_inventory_sealed": True, "require_usage_complete": True,
            "reject_unaccepted_truncation": True,
        },
        "error_policy": {"mode": "strict", "allowed_error_classes": []},
        "budgets": {
            "max_units": 4_000_013, "max_attempts_per_unit": 3,
            "max_wall_time_s": 2_592_000, "max_bytes_read": 1_099_511_627_776,
            "max_bytes_written": 1_073_741_824, "max_tokens": 25_000_000,
            "max_cost_micros": 0, "max_artifacts": 3, "max_concurrency": 1,
        },
        "stages": [
            {
                "key": "inventory", "type": "inventory", "depends_on": [],
                "runner": {"kind": "internal", "name": "sealed_inventory"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"inventory": {"ref": "revision.inventory"}},
                "output_schema": _reference("metnos.inventory-seal/1"), "retry": _retry(attempts=1),
                "timeout_s": 60, "invalidation_keys": ["source.digest"],
                "resources": _resources(local_io=1), "required": True,
            },
            {
                "key": "ocr", "type": "map", "depends_on": ["inventory"],
                "runner": {"kind": "executor", "name": "read_files_ocr"},
                "effect_profile": "pure", "cardinality": {"mode": "per_source", "max_units": 1_000_000},
                "input_bindings": {
                    "paths": {"ref": "source.path"}, "source": {"ref": "source.record"},
                },
                "output_schema": _reference("metnos.durable.ocr-source/1"), "retry": _retry(),
                "timeout_s": 1_800,
                "invalidation_keys": ["source.digest", "runner.contract_digest", "semantic_args.digest"],
                "resources": _resources(cpu=1, local_io=1, vlm=1, device=1), "required": True,
            },
            {
                "key": "questions", "type": "map", "depends_on": ["ocr"],
                "runner": {"kind": "workload", "name": "durable.images.extract_questions"},
                "effect_profile": "pure", "cardinality": {"mode": "per_source", "max_units": 1_000_000},
                "input_bindings": {
                    "ocr_entries": {"ref": "dependency.entries", "stage": "ocr"},
                    "source": {"ref": "source.record"},
                },
                "output_schema": _reference("metnos.images.question-occurrences/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "deduplicate", "type": "reduce", "depends_on": ["questions"],
                "runner": {"kind": "workload", "name": "durable.images.deduplicate"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"occurrences": {"ref": "dependency.entries", "stage": "questions"}},
                "output_schema": _reference("metnos.images.canonical-questions/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "solutions", "type": "map", "depends_on": ["deduplicate"],
                "runner": {"kind": "workload", "name": "durable.images.answer"},
                "effect_profile": "pure",
                "cardinality": {"mode": "per_dependency", "max_units": 1_000_000, "entry_identity_field": "canonical_question_key"},
                "input_bindings": {"question": {"ref": "dependency.entries", "stage": "deduplicate"}},
                "output_schema": _reference("metnos.images.answers/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "answer_validation", "type": "map", "depends_on": ["solutions"],
                "runner": {"kind": "workload", "name": "durable.images.validate"},
                "effect_profile": "pure",
                "cardinality": {"mode": "per_dependency", "max_units": 1_000_000, "entry_identity_field": "canonical_question_key"},
                "input_bindings": {"answer": {"ref": "dependency.entries", "stage": "solutions"}},
                "output_schema": _reference("metnos.images.answer-validation/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "notes", "type": "reduce", "depends_on": ["solutions"],
                "runner": {"kind": "workload", "name": "durable.images.reduce_notes"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"answers": {"ref": "dependency.entries", "stage": "solutions"}},
                "output_schema": _reference("metnos.images.reduction/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "formulae", "type": "reduce", "depends_on": ["solutions"],
                "runner": {"kind": "workload", "name": "durable.images.reduce_formulae"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"answers": {"ref": "dependency.entries", "stage": "solutions"}},
                "output_schema": _reference("metnos.images.reduction/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "assemble", "type": "reduce", "depends_on": ["solutions", "answer_validation", "notes", "formulae"],
                "runner": {"kind": "workload", "name": "durable.images.assemble"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 4},
                "input_bindings": {
                    "answers": {"ref": "dependency.entries", "stage": "solutions"},
                    "validation": {"ref": "dependency.entries", "stage": "answer_validation"},
                    "notes": {"ref": "dependency.entries", "stage": "notes"},
                    "formulae": {"ref": "dependency.entries", "stage": "formulae"},
                },
                "output_schema": _reference("metnos.images.assembly/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "validate", "type": "validate", "depends_on": ["assemble"],
                "runner": {"kind": "internal", "name": "schema_and_coverage_validator"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"assembled": {"ref": "dependency.result", "stage": "assemble"}},
                "output_schema": _reference("metnos.images.validation/1"), "retry": _retry(attempts=1),
                "timeout_s": 300, "invalidation_keys": ["dependencies.digest"],
                "resources": _resources(cpu=1), "required": True,
            },
            {
                "key": "publish", "type": "publish", "depends_on": ["validate"],
                "runner": {"kind": "internal", "name": "artifact_store_publish"},
                "effect_profile": "idempotent", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {"validated": {"ref": "dependency.result", "stage": "validate"}},
                "output_schema": _reference("metnos.internal-artifacts/1"), "retry": _retry(),
                "timeout_s": 300, "invalidation_keys": ["dependencies.digest"],
                "resources": _resources(local_io=1), "required": True,
            },
        ],
        "required_artifacts": [
            {"name": "solutions_markdown", "mime_type": "text/markdown", "schema_version": "metnos.images.assembly/1", "publication": "internal_store"},
            {"name": "notes_markdown", "mime_type": "text/markdown", "schema_version": "metnos.images.reduction/1", "publication": "internal_store"},
            {"name": "formulae_markdown", "mime_type": "text/markdown", "schema_version": "metnos.images.reduction/1", "publication": "internal_store"},
        ],
    }


__all__ = [
    "PRESET_ID",
    "SEMANTIC_SCHEMA_VERSION",
    "image_questions_plan",
    "output_schemas",
    "runner_resolver",
    "workload_prompt_digests",
]
