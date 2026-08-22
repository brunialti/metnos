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
from datetime import datetime, timezone
from typing import Any

from .compiler import (
    ApprovedOutputSchema,
    CompositeRunnerResolver,
    OutputSchemaRegistry,
    RegisteredWorkloadResolver,
    VerifiedCatalogResolver,
    core_output_schemas,
)
from .reduction import hierarchical_node_bound
from .schema import (
    MAX_RESULT_JSON_BYTES,
    MAX_SNAPSHOT_JSON_BYTES,
    canonical_json,
    digest_json,
)


PRESET_ID = "images.questions.v1"
SEMANTIC_SCHEMA_VERSION = "metnos.images.questions.semantic/1"
_MAX_SOURCE_UNITS = 1_000_000
_MAX_REDUCTION_UNITS = hierarchical_node_bound(_MAX_SOURCE_UNITS)
_MAX_PLAN_UNITS = 8_000_032
_REDUCTION_FAN_IN = 32
_REDUCTION_INPUT_BYTES = 8_388_608


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


def _hierarchical_cardinality(input_name: str) -> dict[str, int | str]:
    return {
        "mode": "singleton",
        "max_units": _MAX_REDUCTION_UNITS,
        "fan_in": _REDUCTION_FAN_IN,
        "reduction_input": input_name,
        "max_input_bytes": _REDUCTION_INPUT_BYTES,
    }


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
        *core_output_schemas().schemas,
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
                "occurrence_count": {"type": "integer", "minimum": 1},
                "occurrence_multiset_hash": digest,
            },
            required_entry_fields=(
                "canonical_question_key", "normalized_text", "semantic_schema_version",
                "occurrence_count", "occurrence_multiset_hash",
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
                "status": {"enum": ["answered", "unresolved"]},
                "answer": {"type": "string", "maxLength": 100_000},
                "answer_reason": {"type": "string", "maxLength": 2_000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "valid": {"type": "boolean"},
                "validation_reason": {"type": "string", "maxLength": 2_000},
            },
            required_entry_fields=(
                "canonical_question_key", "status", "answer",
                "answer_reason", "confidence", "valid", "validation_reason",
            ),
        ),
        _entries_schema(
            "metnos.images.reduction/1",
            {
                "kind": {"enum": ["solutions", "notes", "formulae"]},
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
    )
    return OutputSchemaRegistry(schemas)


_WORKLOAD_SPECS: Mapping[str, Mapping[str, Any]] = {
    "durable.images.extract_questions": {
        "output": "metnos.images.question-occurrences/1",
        "inputs": {"ocr_entries": "array", "source": "object"},
        "required": ("ocr_entries", "source"),
        "prompt": (
            "Extract every question or problem that requires an answer. Join "
            "a related instruction to its question; do not emit a standalone "
            "instruction as another question. Return exactly one JSON object "
            "with this shape: "
            '{"questions":[{"text":"...","coordinate_locale":"...",'
            '"confidence":0.0}]}. Use an empty questions array when none exist.'
        ),
    },
    "durable.images.deduplicate": {
        "output": "metnos.images.canonical-questions/1",
        "inputs": {"occurrences": "array"},
        "required": ("occurrences",),
        "prompt": (
            "Optionally propose semantic duplicates. Never merge below "
            "confidence 0.98. Return exactly one JSON object with this shape: "
            '{"merge_groups":[{"keys":["...","..."],"confidence":0.98}]}.'
        ),
    },
    "durable.images.answer": {
        "output": "metnos.images.answers/1",
        "inputs": {"question": "object"},
        "required": ("question",),
        "prompt": (
            "Answer the canonical question. Return exactly one JSON object "
            "with this shape: "
            '{"status":"answered","answer":"...","reason":"...",'
            '"confidence":0.0}. Use status "unresolved" when a reliable '
            "answer is not possible."
        ),
    },
    "durable.images.validate": {
        "output": "metnos.images.answer-validation/1",
        "inputs": {"answer": "object"},
        "required": ("answer",),
        "prompt": (
            "Verify the supplied answer. Return exactly one JSON object with "
            'this shape: {"valid":true,"reason":"..."}.'
        ),
    },
    "durable.images.reduce_notes": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": (
            "Prepare concise Markdown notes. Return exactly one JSON object "
            'with this shape: {"markdown":"..."}.'
        ),
    },
    "durable.images.reduce_solutions": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": (
            "Prepare a verified Markdown solution document. Return exactly "
            'one JSON object with this shape: {"markdown":"..."}.'
        ),
    },
    "durable.images.reduce_formulae": {
        "output": "metnos.images.reduction/1",
        "inputs": {"answers": "array"},
        "required": ("answers",),
        "prompt": (
            "Prepare a Markdown formula sheet. Return exactly one JSON object "
            'with this shape: {"markdown":"..."}.'
        ),
    },
    "durable.images.assemble": {
        "output": "metnos.images.assembly/1",
        "inputs": {"solutions": "array", "notes": "array", "formulae": "array"},
        "required": ("solutions", "notes", "formulae"),
        "prompt": (
            "Return exactly one JSON object containing all three artifacts: "
            '{"artifacts":[{"logical_name":"solutions_markdown",'
            '"markdown":"..."},{"logical_name":"notes_markdown",'
            '"markdown":"..."},{"logical_name":"formulae_markdown",'
            '"markdown":"..."}]}.'
        ),
    },
}


def _prompt_language(value: object) -> str:
    language = str(value or "").strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,3}", language):
        raise ValueError("prompt language is invalid")
    return language


def _workload_prompt(name: str, language: str) -> str:
    spec = _WORKLOAD_SPECS[name]
    return (
        f"{spec['prompt']}\n"
        "Keep source text unchanged. Write generated explanations and documents "
        f"in language {language}."
    )


def workload_prompt_digests(language: str) -> dict[str, str]:
    language = _prompt_language(language)
    return {
        name: digest_json(
            "durable-image-preset-prompt",
            {"name": name, "prompt": _workload_prompt(name, language)},
            max_bytes=MAX_SNAPSHOT_JSON_BYTES,
        )
        for name in _WORKLOAD_SPECS
    }


def registered_runner_bindings() -> tuple[tuple[str, str], ...]:
    """Return this package's closed contribution to the generic LRE registry."""

    return tuple(sorted(
        (("executor", "read_files_ocr"), *(('workload', name) for name in _WORKLOAD_SPECS)),
        key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
    ))


def registered_output_schema_names(
    schemas: OutputSchemaRegistry | None = None,
) -> tuple[str, ...]:
    """Return domain schemas only; generic schemas remain owned by LRE."""

    core_names = frozenset(core_output_schemas().names)
    selected = schemas or output_schemas()
    return tuple(name for name in selected.names if name not in core_names)


def runner_resolver(
    *,
    catalog_loader: Callable[..., Any] | None = None,
    binding_resolver: Callable[..., Mapping[str, Any]] | None = None,
    executor_model_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    executor_prompt_digests: Mapping[str, str] | None = None,
    language: str | None = None,
) -> CompositeRunnerResolver:
    """Create the closed resolver used when admitting this private preset."""
    import config

    prompt_language = _prompt_language(language or config.DEFAULT_LANG)
    if executor_model_bindings is None or executor_prompt_digests is None:
        import prompt_loader
        import vlm_client

        default_model_bindings = {
            "read_files_ocr": vlm_client.model_binding_facts(max_tokens=1_024),
        }
        default_prompt_digests = {
            "read_files_ocr": prompt_loader.prompt_identity(
                "agentic_ocr_extract", prompt_language,
            ).digest,
        }
        if executor_model_bindings is None:
            executor_model_bindings = default_model_bindings
        if executor_prompt_digests is None:
            executor_prompt_digests = default_prompt_digests
    return CompositeRunnerResolver(
        executors=VerifiedCatalogResolver(
            catalog_loader=catalog_loader,
            durable_effects={"read_files_ocr": ("pure",)},
            durable_output_schemas={"read_files_ocr": ("metnos.durable.ocr-source/1",)},
            model_bindings=executor_model_bindings,
            prompt_digests=executor_prompt_digests,
            prompt_languages={
                name: prompt_language for name in executor_model_bindings
            },
        ),
        workloads=RegisteredWorkloadResolver(
            output_schemas={name: (str(spec["output"]),) for name, spec in _WORKLOAD_SPECS.items()},
            input_names={name: tuple(spec["inputs"]) for name, spec in _WORKLOAD_SPECS.items()},
            input_types={name: dict(spec["inputs"]) for name, spec in _WORKLOAD_SPECS.items()},
            required_inputs={name: tuple(spec["required"]) for name, spec in _WORKLOAD_SPECS.items()},
            prompt_digests=workload_prompt_digests(prompt_language),
            prompt_language=prompt_language,
            max_input_tokens={
                name: MAX_RESULT_JSON_BYTES + 65_536
                for name in _WORKLOAD_SPECS
            },
            max_output_tokens={name: 4_096 for name in _WORKLOAD_SPECS},
            max_calls_per_attempt={name: 1 for name in _WORKLOAD_SPECS},
            hierarchical_reducers=(
                "durable.images.deduplicate",
                "durable.images.reduce_solutions",
                "durable.images.reduce_notes",
                "durable.images.reduce_formulae",
            ),
            binding_resolver=binding_resolver,
        ),
    )


def _clean_json_object(
    value: object,
    *,
    list_key: str | None = None,
) -> Mapping[str, Any]:
    """Decode one model response without weakening its typed contract.

    Small local models sometimes return the requested array as the JSON root,
    even when the prompt asks for an object containing that array. Registered
    adapters may name the one admissible wrapper key; item validation remains
    unchanged and every other root shape still fails closed.
    """

    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        raise ValueError("workload response is not an object")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if len(lines) > 2 and lines[-1].startswith("```") else lines[1:])
    parsed = json.loads(text)
    if isinstance(parsed, Mapping):
        return parsed
    if list_key is not None and isinstance(parsed, list):
        return {list_key: parsed}
    raise ValueError("workload response is not an object")


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
        invoke: Callable[[str, str, Mapping[str, Any], object], object] | None = None,
    ) -> None:
        self._invoke = invoke or self._router_invoke

    @staticmethod
    def _router_invoke(
        name: str,
        prompt: str,
        args: Mapping[str, Any],
        context: object,
    ) -> object:
        from llm_router import LLMRouter
        from llm_workloads import tier_for

        deadline_at = getattr(context, "deadline_at", None)
        if not isinstance(deadline_at, str) or not deadline_at.endswith("Z"):
            raise TimeoutError("durable model invocation has no deadline")
        deadline = datetime.fromisoformat(deadline_at[:-1] + "+00:00")
        remaining = (
            deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)
        ).total_seconds()
        if remaining <= 0:
            raise TimeoutError("durable model invocation deadline is exhausted")
        result = LLMRouter().provider(tier_for(name)).chat(
            prompt,
            canonical_json(args, max_bytes=MAX_RESULT_JSON_BYTES),
            max_tokens=4_096,
            request_timeout_s=remaining,
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
            list_key = {
                "durable.images.extract_questions": "questions",
                "durable.images.deduplicate": "merge_groups",
                "durable.images.assemble": "artifacts",
            }.get(name)
            raw = _clean_json_object(self._invoke(
                name,
                _workload_prompt(
                    name,
                    _prompt_language(getattr(_context, "language", None)),
                ),
                args,
                _context,
            ), list_key=list_key)
            if name == "durable.images.extract_questions":
                return self._extract_questions(raw, args)
            if name == "durable.images.deduplicate":
                return self._deduplicate(raw, args)
            if name == "durable.images.answer":
                return self._answer(raw, args)
            if name == "durable.images.validate":
                return self._validate(raw, args)
            if name in {
                "durable.images.reduce_solutions",
                "durable.images.reduce_notes",
                "durable.images.reduce_formulae",
            }:
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
            coordinate = item.get("coordinate_locale") or "whole_image"
            confidence = item.get("confidence", 1.0)
            if not isinstance(coordinate, str) or not coordinate:
                raise ValueError("question coordinate is invalid")
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ValueError("question confidence is invalid")
            normalized_hash = digest_json(
                "durable-question-normalized-text", normalized_text,
                max_bytes=MAX_SNAPSHOT_JSON_BYTES,
            )
            canonical_key = digest_json(
                "durable-canonical-question",
                {
                    "normalized_text_hash": normalized_hash,
                    "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                },
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
        grouped: dict[str, dict[str, object]] = {}
        for occurrence in _entry_list(args, key="occurrences"):
            canonical_key = occurrence.get("canonical_question_key")
            if not isinstance(canonical_key, str):
                raise ValueError("canonical key is invalid")
            normalized_text = occurrence.get("normalized_text")
            if not isinstance(normalized_text, str):
                raise ValueError("normalized question is invalid")
            occurrence_id = occurrence.get("question_occurrence_id")
            if isinstance(occurrence_id, str):
                count = 1
                accumulator = int(occurrence_id.removeprefix("sha256:"), 16)
            else:
                count = occurrence.get("occurrence_count")
                multiset_hash = occurrence.get("occurrence_multiset_hash")
                if (
                    not isinstance(count, int)
                    or isinstance(count, bool)
                    or count < 1
                    or not isinstance(multiset_hash, str)
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", multiset_hash)
                ):
                    raise ValueError("occurrence summary is invalid")
                accumulator = int(multiset_hash.removeprefix("sha256:"), 16)
            group = grouped.setdefault(canonical_key, {
                "normalized_text": normalized_text,
                "count": 0,
                "accumulator": 0,
            })
            if group["normalized_text"] != normalized_text:
                raise ValueError("canonical question text conflicts")
            group["count"] = int(group["count"]) + count
            group["accumulator"] = (
                int(group["accumulator"]) + accumulator
            ) % (1 << 256)

        merge_groups = raw.get("merge_groups", [])
        if not isinstance(merge_groups, list):
            raise ValueError("merge groups are invalid")
        for proposed in merge_groups:
            confidence = proposed.get("confidence") if isinstance(proposed, Mapping) else None
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0.98 <= confidence <= 1
            ):
                continue
            keys = proposed.get("keys")
            if not isinstance(keys, list) or len(keys) < 2 or any(
                not isinstance(key, str) or key not in grouped for key in keys
            ):
                continue
            target = min(keys, key=str.encode)
            for key in sorted(set(keys) - {target}, key=str.encode):
                merged = grouped.pop(key)
                grouped[target]["count"] = (
                    int(grouped[target]["count"]) + int(merged["count"])
                )
                grouped[target]["accumulator"] = (
                    int(grouped[target]["accumulator"])
                    + int(merged["accumulator"])
                ) % (1 << 256)

        entries: list[dict[str, object]] = []
        for canonical_key in sorted(grouped, key=str.encode):
            group = grouped[canonical_key]
            entries.append({
                "canonical_question_key": canonical_key,
                "normalized_text": str(group["normalized_text"]),
                "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
                "occurrence_count": int(group["count"]),
                "occurrence_multiset_hash": (
                    "sha256:" + format(int(group["accumulator"]), "064x")
                ),
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
            or not 0 <= confidence <= 1
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
            "status": answer["status"],
            "answer": answer["answer"],
            "answer_reason": answer["reason"],
            "confidence": answer["confidence"],
            "valid": valid,
            "validation_reason": reason,
        }]}

    @staticmethod
    def _reduce(raw: Mapping[str, Any], name: str) -> dict[str, object]:
        markdown = raw.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError("reduction markdown is invalid")
        return {"entries": [{
            "kind": (
                "solutions" if name.endswith("reduce_solutions")
                else "notes" if name.endswith("reduce_notes")
                else "formulae"
            ),
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
        if (
            len(artifacts) != len(expected)
            or len(by_name) != len(artifacts)
            or set(by_name) != set(expected)
        ):
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
            "mode": "sealed", "dynamic": False, "max_sources": _MAX_SOURCE_UNITS,
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
            "max_units": _MAX_PLAN_UNITS, "max_attempts_per_unit": 3,
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
                "effect_profile": "pure", "cardinality": {"mode": "per_source", "max_units": _MAX_SOURCE_UNITS},
                "input_bindings": {
                    "paths": {"ref": "source.path"}, "source": {"ref": "source.record"},
                },
                "output_schema": _reference("metnos.durable.ocr-source/1"), "retry": _retry(),
                "timeout_s": 1_800,
                "invalidation_keys": [
                    "source.digest", "runner.contract_digest",
                    "semantic_args.digest", "model_binding.digest", "prompt.digest",
                ],
                "resources": _resources(cpu=1, local_io=1, vlm=1, device=1), "required": True,
            },
            {
                "key": "questions", "type": "map", "depends_on": ["ocr"],
                "runner": {"kind": "workload", "name": "durable.images.extract_questions"},
                "effect_profile": "pure", "cardinality": {"mode": "per_source", "max_units": _MAX_SOURCE_UNITS},
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
                "effect_profile": "pure",
                "cardinality": _hierarchical_cardinality("occurrences"),
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
                "cardinality": {"mode": "per_dependency", "max_units": _MAX_SOURCE_UNITS, "entry_identity_field": "canonical_question_key"},
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
                "cardinality": {"mode": "per_dependency", "max_units": _MAX_SOURCE_UNITS, "entry_identity_field": "canonical_question_key"},
                "input_bindings": {"answer": {"ref": "dependency.entries", "stage": "solutions"}},
                "output_schema": _reference("metnos.images.answer-validation/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "solutions_document", "type": "reduce",
                "depends_on": ["answer_validation"],
                "runner": {"kind": "workload", "name": "durable.images.reduce_solutions"},
                "effect_profile": "pure",
                "cardinality": _hierarchical_cardinality("answers"),
                "input_bindings": {"answers": {"ref": "dependency.entries", "stage": "answer_validation"}},
                "output_schema": _reference("metnos.images.reduction/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "notes", "type": "reduce", "depends_on": ["answer_validation"],
                "runner": {"kind": "workload", "name": "durable.images.reduce_notes"},
                "effect_profile": "pure", "cardinality": _hierarchical_cardinality("answers"),
                "input_bindings": {"answers": {"ref": "dependency.entries", "stage": "answer_validation"}},
                "output_schema": _reference("metnos.images.reduction/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "formulae", "type": "reduce", "depends_on": ["answer_validation"],
                "runner": {"kind": "workload", "name": "durable.images.reduce_formulae"},
                "effect_profile": "pure", "cardinality": _hierarchical_cardinality("answers"),
                "input_bindings": {"answers": {"ref": "dependency.entries", "stage": "answer_validation"}},
                "output_schema": _reference("metnos.images.reduction/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order", "reduction.fan_in"],
                "resources": _resources(llm=1), "required": True,
            },
            {
                "key": "assemble", "type": "reduce",
                "depends_on": ["solutions_document", "notes", "formulae"],
                "runner": {"kind": "workload", "name": "durable.images.assemble"},
                "effect_profile": "pure", "cardinality": {"mode": "singleton", "max_units": 1},
                "input_bindings": {
                    "solutions": {"ref": "dependency.entries", "stage": "solutions_document"},
                    "notes": {"ref": "dependency.entries", "stage": "notes"},
                    "formulae": {"ref": "dependency.entries", "stage": "formulae"},
                },
                "output_schema": _reference("metnos.images.assembly/1"), "retry": _retry(),
                "timeout_s": 300,
                "invalidation_keys": ["dependencies.digest", "model_binding.digest", "prompt.digest", "reduction.order"],
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
    "registered_output_schema_names",
    "runner_resolver",
    "registered_runner_bindings",
    "workload_prompt_digests",
]
