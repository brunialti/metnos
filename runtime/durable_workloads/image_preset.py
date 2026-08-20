"""Declarative contracts for the private image-question durable preset.

This module contains domain data only: output schemas, registered workload
contracts and a candidate plan.  It never starts a worker, opens a database,
reads a source path or calls a model.  The generic engine remains responsible
for materialisation, leases, retries and idempotency.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .compiler import (
    ApprovedOutputSchema,
    CompositeRunnerResolver,
    OutputSchemaRegistry,
    RegisteredWorkloadResolver,
    VerifiedCatalogResolver,
)
from .schema import MAX_RESULT_JSON_BYTES, MAX_SNAPSHOT_JSON_BYTES, canonical_json, digest_json


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
                "logical_name": {
                    "enum": ["solutions_markdown", "notes_markdown", "formulae_markdown"],
                },
                "mime_type": {"const": "text/markdown"},
                "schema_version": {
                    "enum": ["metnos.images.assembly/1", "metnos.images.reduction/1"],
                },
                "markdown": {"type": "string", "maxLength": 1_000_000},
            },
            required_entry_fields=("logical_name", "mime_type", "schema_version", "markdown"),
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
        "prompt": "Extract every visible question as questions[{text,coordinate_locale,confidence}]. Return JSON only.",
    },
    "durable.images.deduplicate": {
        "output": "metnos.images.canonical-questions/1",
        "inputs": {"occurrences": "array"},
        "required": ("occurrences",),
        "prompt": "Optionally propose semantic merge_groups[{keys,confidence}]. Never merge below confidence 0.98. Return JSON only.",
    },
    "durable.images.answer": {
        "output": "metnos.images.answers/1",
        "inputs": {"question": "object"},
        "required": ("question",),
        "prompt": "Return status, answer, reason and confidence for the canonical question. Return JSON only.",
    },
    "durable.images.validate": {
        "output": "metnos.images.answer-validation/1",
        "inputs": {"answer": "object"},
        "required": ("answer",),
        "prompt": "Return valid and reason for the supplied answer. Return JSON only.",
    },
    "durable.images.reduce_notes": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": "Return Markdown notes as markdown. Return JSON only.",
    },
    "durable.images.reduce_formulae": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": "Return a Markdown formula sheet as markdown. Return JSON only.",
    },
    "durable.images.assemble": {
        "output": "metnos.images.assembly/1",
        "inputs": {
            "answers": "array", "validation": "array", "notes": "array", "formulae": "array",
        },
        "required": ("answers", "validation", "notes", "formulae"),
        "prompt": "Return artifacts[{logical_name,markdown}] for solutions_markdown, notes_markdown and formulae_markdown. Return JSON only.",
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


def _clean_json_object(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        raise ValueError("workload response is not an object")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if len(lines) > 2 and lines[-1].startswith("```") else lines[1:])
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("workload response is not an object")
    return parsed


def _normalized_question(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("question text is invalid")
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    if not normalized:
        raise ValueError("question text is empty")
    return normalized


def _entry_list(value: object, *, key: str = "entries") -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("workload arguments are invalid")
    items = value.get(key)
    if not isinstance(items, list):
        raise ValueError("workload entries are invalid")
    if any(not isinstance(item, Mapping) for item in items):
        raise ValueError("workload entry is invalid")
    return list(items)


class ImagePresetWorkloadInvoker:
    """Normalise registered model output into deterministic preset contracts."""

    def __init__(
        self,
        invoke: Callable[[str, str, Mapping[str, Any]], object] | None = None,
    ) -> None:
        self._invoke = invoke or self._router_invoke

    @staticmethod
    def _router_invoke(name: str, prompt: str, args: Mapping[str, Any]) -> object:
        from llm_router import LLMRouter
        from llm_workloads import tier_for

        result = LLMRouter().provider(tier_for(name)).chat(
            prompt,
            canonical_json(args, max_bytes=MAX_RESULT_JSON_BYTES),
            max_tokens=4_096,
        )
        return getattr(result, "text", result)

    @staticmethod
    def _failure() -> dict[str, object]:
        return {"ok": False, "error_class": "contract_violation"}

    def __call__(
        self,
        name: str,
        args: Mapping[str, Any],
        _context: object,
    ) -> dict[str, object]:
        spec = _WORKLOAD_SPECS.get(name)
        if spec is None or not isinstance(args, Mapping):
            return self._failure()
        try:
            raw = _clean_json_object(self._invoke(name, str(spec["prompt"]), args))
            if name == "durable.images.extract_questions":
                return self._extract_questions(raw, args)
            if name == "durable.images.deduplicate":
                return self._deduplicate(raw, args)
            if name == "durable.images.answer":
                return self._answer(raw, args)
            if name == "durable.images.validate":
                return self._validate(raw, args)
            if name in {"durable.images.reduce_notes", "durable.images.reduce_formulae"}:
                return self._reduce(raw, name)
            if name == "durable.images.assemble":
                return self._assemble(raw)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return self._failure()
        return self._failure()

    @staticmethod
    def _extract_questions(raw: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, object]:
        source = args.get("source")
        if not isinstance(source, Mapping) or not isinstance(source.get("source_id"), str):
            raise ValueError("source identity is invalid")
        source_id = str(source["source_id"])
        questions = raw.get("questions", raw.get("entries"))
        if not isinstance(questions, list):
            raise ValueError("questions are invalid")
        entries: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in questions:
            if not isinstance(item, Mapping):
                raise ValueError("question is invalid")
            original_text = item.get("text")
            normalized_text = _normalized_question(original_text)
            coordinate = str(item.get("coordinate_locale") or "whole_image")
            confidence = item.get("confidence", 1.0)
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise ValueError("question confidence is invalid")
            normalized_hash = digest_json(
                "durable-question-normalized-text", normalized_text,
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            canonical_key = digest_json(
                "durable-canonical-question",
                {"normalized_text_hash": normalized_hash, "semantic_schema_version": SEMANTIC_SCHEMA_VERSION},
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            occurrence_id = digest_json(
                "durable-question-occurrence",
                {
                    "source_id": source_id,
                    "coordinate_locale": coordinate,
                    "normalized_text_hash": normalized_hash,
                },
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            if occurrence_id in seen:
                raise ValueError("duplicate occurrence")
            seen.add(occurrence_id)
            entries.append({
                "question_occurrence_id": occurrence_id,
                "canonical_question_key": canonical_key,
                "source_id": source_id,
                "coordinate_locale": coordinate,
                "original_text": original_text,
                "normalized_text": normalized_text,
                "normalized_text_hash": normalized_hash,
                "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                "confidence": float(confidence),
            })
        return {"entries": entries}

    @staticmethod
    def _deduplicate(raw: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, object]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for occurrence in _entry_list(args, key="occurrences"):
            canonical_key = occurrence.get("canonical_question_key")
            if not isinstance(canonical_key, str):
                raise ValueError("canonical key is invalid")
            grouped.setdefault(canonical_key, []).append(occurrence)

        merge_groups = raw.get("merge_groups", [])
        if not isinstance(merge_groups, list):
            raise ValueError("merge groups are invalid")
        for proposed in merge_groups:
            confidence = proposed.get("confidence") if isinstance(proposed, Mapping) else None
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or confidence < 0.98
            ):
                continue
            keys = proposed.get("keys")
            if not isinstance(keys, list) or len(keys) < 2 or any(
                not isinstance(key, str) or key not in grouped for key in keys
            ):
                continue
            target = min(keys, key=str.encode)
            for key in sorted(set(keys) - {target}, key=str.encode):
                grouped[target].extend(grouped.pop(key))

        entries: list[dict[str, object]] = []
        for canonical_key in sorted(grouped, key=str.encode):
            occurrences = grouped[canonical_key]
            first = min(occurrences, key=lambda item: str(item["question_occurrence_id"]).encode())
            occurrence_ids = sorted(
                {str(item["question_occurrence_id"]) for item in occurrences}, key=str.encode,
            )
            entries.append({
                "canonical_question_key": canonical_key,
                "normalized_text": str(first["normalized_text"]),
                "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                "occurrence_ids": occurrence_ids,
            })
        return {"entries": entries}

    @staticmethod
    def _answer(raw: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, object]:
        question = args.get("question")
        if not isinstance(question, Mapping) or not isinstance(question.get("canonical_question_key"), str):
            raise ValueError("question is invalid")
        status = raw.get("status")
        if status not in {"answered", "unresolved"}:
            raise ValueError("answer status is invalid")
        answer = raw.get("answer")
        reason = raw.get("reason")
        confidence = raw.get("confidence")
        if (
            not isinstance(answer, str)
            or not isinstance(reason, str)
            or not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
        ):
            raise ValueError("answer fields are invalid")
        return {"entries": [{
            "canonical_question_key": question["canonical_question_key"],
            "status": status,
            "answer": answer,
            "reason": reason,
            "confidence": float(confidence),
        }]}

    @staticmethod
    def _validate(raw: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, object]:
        answer = args.get("answer")
        if not isinstance(answer, Mapping) or not isinstance(answer.get("canonical_question_key"), str):
            raise ValueError("answer is invalid")
        valid = raw.get("valid")
        reason = raw.get("reason")
        if not isinstance(valid, bool) or not isinstance(reason, str):
            raise ValueError("validation fields are invalid")
        return {"entries": [{
            "canonical_question_key": answer["canonical_question_key"],
            "valid": valid,
            "reason": reason,
        }]}

    @staticmethod
    def _reduce(raw: Mapping[str, Any], name: str) -> dict[str, object]:
        markdown = raw.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError("reduction markdown is invalid")
        return {"entries": [{
            "kind": "notes" if name.endswith("reduce_notes") else "formulae",
            "markdown": markdown,
        }]}

    @staticmethod
    def _assemble(raw: Mapping[str, Any]) -> dict[str, object]:
        artifacts = raw.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("assembled artifacts are invalid")
        expected = {
            "solutions_markdown": "metnos.images.assembly/1",
            "notes_markdown": "metnos.images.reduction/1",
            "formulae_markdown": "metnos.images.reduction/1",
        }
        by_name = {
            item.get("logical_name"): item
            for item in artifacts
            if isinstance(item, Mapping) and isinstance(item.get("logical_name"), str)
        }
        if set(by_name) != set(expected):
            raise ValueError("assembled artifact names are invalid")
        entries: list[dict[str, str]] = []
        for name in ("solutions_markdown", "notes_markdown", "formulae_markdown"):
            markdown = by_name[name].get("markdown")
            if not isinstance(markdown, str) or not markdown.strip():
                raise ValueError("assembled markdown is invalid")
            entries.append({
                "logical_name": name,
                "mime_type": "text/markdown",
                "schema_version": expected[name],
                "markdown": markdown,
            })
        return {"entries": entries}


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
                "key": "publish", "type": "publish", "depends_on": ["assemble", "validate"],
                "runner": {"kind": "internal", "name": "artifact_store_publish"},
                "effect_profile": "idempotent", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {
                    "artifacts": {"ref": "dependency.entries", "stage": "assemble"},
                    "validation": {"ref": "dependency.entries", "stage": "validate"},
                },
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
    "ImagePresetWorkloadInvoker",
    "PRESET_ID",
    "SEMANTIC_SCHEMA_VERSION",
    "image_questions_plan",
    "output_schemas",
    "runner_resolver",
    "workload_prompt_digests",
]
