"""Resumable photo indexing assembled from ordinary, bounded LRE units.

Discovery is itself a durable unit: submitting a large archive never hashes
the archive in the chat process. Its accepted, immutable groups feed typed
per-dependency maps. Only the final publication makes a generation searchable.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path
import re
import threading
from typing import Any

from .compiler import (
    ApprovedOutputSchema, CompositeRunnerResolver, OutputSchemaRegistry,
    RegisteredWorkloadResolver, VerifiedCatalogResolver,
)
from .direct_invocation import DirectInvocationUnsupported
from .models import ExecutionContext, RESOURCE_KEYS
from .runtime_bindings import RuntimeRegistration
from .schema import inventory_digest


PLAN_ID = "images.index.v1"
EXECUTOR = "create_images_indices"
FOLDER_WORKLOAD = "images.folder_classify"
DISCOVERY_SCHEMA = "metnos.images.index-discovery/3"
FOLDER_SCHEMA = "metnos.images.index-folders/1"
PART_SCHEMA = "metnos.images.index-part/3"
PUBLISHED_SCHEMA = "metnos.images.index-published/3"
_PART = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
_GENERATION = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PUBLIC_ARGS = frozenset({"base_path", "recursive", "force", "max_files", "dry_run"})


def describe_plan(plan: Mapping, phase: str | None) -> dict[str, Any]:
    """Expose only the common literal corpus path from an admitted index plan."""
    paths = []
    for stage in plan["stages"]:
        if stage["runner"] != {"kind": "executor", "name": EXECUTOR}:
            continue
        binding = stage["input_bindings"].get("base_path", {})
        value = binding.get("value")
        if binding.get("ref") != "literal" or not isinstance(value, str) or not value.startswith("/"):
            paths.append(None)
        else:
            paths.append(value)
    target = paths[0] if paths and paths[0] and all(value == paths[0] for value in paths) else None
    return {
        "kind": "image_indexing", "operation": EXECUTOR, "target_path": target,
        "phase": phase if phase in {"discover", "folders", "analyze", "merge", "publish"} else None,
    }


def normalize_request(executor: object, args: Mapping[str, Any], target_device=None) -> dict:
    """Freeze only the public operation, never caller-supplied worker phases."""
    if (
        getattr(executor, "name", None) != EXECUTOR
        or getattr(executor, "lre_plan", "") != PLAN_ID
        or getattr(executor, "lifecycle", "") != "active"
        or getattr(executor, "dormant", False)
        or not getattr(executor, "signed_by", "")
        or str(getattr(executor, "signed_by", "")).startswith("(")
        or target_device not in {None, "", "server"}
        or not isinstance(args, Mapping)
        or set(args) - _PUBLIC_ARGS
    ):
        raise DirectInvocationUnsupported("image indexing invocation is not admissible")
    raw = args.get("base_path")
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise DirectInvocationUnsupported("image indexing root is missing")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise DirectInvocationUnsupported("image indexing root must be absolute")
    root = root.resolve(strict=True)
    if not root.is_dir() or len(str(root).encode("utf-8")) > 32_768:
        raise DirectInvocationUnsupported("image indexing root is invalid")
    from index_schema import canonical_corpus_path
    # Freeze the logical workspace identity before crossing the sandbox.
    # A symlink mounted there as a directory must retain the same index key.
    request = {"base_path": canonical_corpus_path(root)}
    for key, default in (("recursive", True), ("force", False), ("dry_run", False)):
        value = args.get(key, default)
        if type(value) is not bool:
            raise DirectInvocationUnsupported("image indexing option must be boolean")
        request[key] = value
    max_files = args.get("max_files", 50_000)
    if type(max_files) is not int or not 1 <= max_files <= 1_000_000:
        raise DirectInvocationUnsupported("image indexing source limit is invalid")
    request["max_files"] = max_files
    return request


def _schema(name: str, entry: Mapping, *, extra: Mapping | None = None, required=()):
    return ApprovedOutputSchema.create(name, {
        "type": "object", "additionalProperties": True,
        "properties": {
            "ok": {"type": "boolean"},
            "entries": {"type": "array", "maxItems": 1_000_000, "items": entry},
            **dict(extra or {}),
        },
        "required": ["entries", *required],
    })


def output_schemas() -> OutputSchemaRegistry:
    from image_index_build import GROUP_SIZE
    from image_index_outcomes import DECODE_FAILURE_CODES
    from .domain_outcome import DOMAIN_OUTCOME_SCHEMA

    error_counts = {
        "type": "object", "additionalProperties": False,
        "properties": {code: {"type": "integer", "minimum": 1, "maximum": 1_000_000}
                       for code in sorted(DECODE_FAILURE_CODES)},
    }
    domain_outcome = {
        **DOMAIN_OUTCOME_SCHEMA,
        "properties": {**DOMAIN_OUTCOME_SCHEMA["properties"], "error_counts": error_counts},
    }
    # Diagnostic authority stays in the approved schema, not in the bridge
    # or a parallel runtime registry. Values are existing executor outcomes.
    error_code = {"type": "string", "enum": [
        "active_generation_changed", "analysis_checkpoint_invalid", "analysis_group_invalid", "args_not_object",
        "artifact_size_or_type", "artifact_too_large", "base_path_invalid",
        "base_path_missing", "coverage_mismatch", "directory_unavailable",
        "discovery_output_too_large", "duplicate_part", "duplicate_source_path",
        "entries_type_invalid", "entry_limit", "entry_path_invalid",
        "entry_source_mismatch", "entry_too_large", "expected_count_invalid",
        "face_embedding_unavailable", "face_model_unavailable",
        "folder_classification_invalid", "folder_contexts_invalid", "folder_label_invalid",
        "generation_context_mismatch", "generation_incomplete", "generation_invalid",
        "generation_receipt_conflict", "image_corpus_empty", "image_description_unavailable",
        "image_index_phase_failed", "image_decode_failed", "image_format_unreadable",
        "image_model_unavailable", "immutable_artifact_conflict",
        "incomplete_analysis", "invalid_indexing_failure", "inventory_limits_invalid", "max_files_invalid",
        "mixed_model_generations", "mixed_vector_dimensions", "model_dimension_mismatch",
        "model_metadata_invalid", "part_context_mismatch", "part_count_invalid",
        "part_count_mismatch", "part_digest_mismatch", "part_invalid", "part_kind_invalid",
        "phase_invalid", "previous_generation_invalid", "reduction_depth_invalid",
        "reduction_fanout_invalid", "requires_lre", "snapshot_path_invalid",
        "source_changed", "source_changed_since_discovery", "source_digest_invalid",
        "source_digest_mismatch", "source_metadata_invalid", "source_path_invalid",
        "source_record_invalid", "source_size_mismatch", "source_unreadable",
        "symlink_directory", "symlink_lookup", "symlink_reference", "symlink_vectors",
        "vectors_invalid",
    ]}
    part = {"type": "object", "additionalProperties": False,
            "properties": {"part": _PART}, "required": ["part"]}
    return OutputSchemaRegistry((
        _schema(DISCOVERY_SCHEMA, {
            "type": "object", "additionalProperties": False,
            "properties": {
                "part": _PART,
                "folder_labels": {"type": "array", "maxItems": GROUP_SIZE,
                                  "items": {"type": "string", "maxLength": 4096}},
            }, "required": ["part", "folder_labels"],
        }, extra={"source_count": {"type": "integer", "minimum": 1}, "error_code": error_code},
            required=("source_count",)),
        _schema(FOLDER_SCHEMA, {
            "type": "object", "additionalProperties": False,
            "properties": {
                "part": _PART,
                "folder_contexts": {"type": "object", "maxProperties": GROUP_SIZE,
                                    "additionalProperties": {"type": "string", "maxLength": 8192}},
            }, "required": ["part", "folder_contexts"],
        }),
        _schema(PART_SCHEMA, part, extra={"error_code": error_code, "domain_outcome": domain_outcome}),
        _schema(PUBLISHED_SCHEMA, part,
                extra={"n_entries_total": {"type": "integer", "minimum": 1}, "error_code": error_code,
                       "n_indexed": {"type": "integer", "minimum": 0},
                       "n_not_indexed": {"type": "integer", "minimum": 0},
                       "indexing_error_counts": error_counts},
                required=("n_entries_total", "n_indexed", "n_not_indexed", "indexing_error_counts")),
    ))


class FolderContextInvoker:
    """Classify repeated labels once per owner, language, prompt and model.

    Only completed values are cached. Each invocation still reports its real
    provider usage; a cache hit is a verified zero-call in-process scope.
    """

    def __init__(self, language: str, identity: str, *, binding_identity: Callable,
                 classifier: Callable | None = None):
        self.language = language
        self.identity = identity
        self.binding_identity = binding_identity
        self.classifier = classifier
        self._cache: OrderedDict[tuple[str, str, str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, name: str, args: Mapping, context: object) -> dict:
        from image_index_build import GROUP_SIZE, classify_folder_context

        if name != FOLDER_WORKLOAD or not isinstance(context, ExecutionContext):
            raise ValueError("image indexing workload identity is invalid")
        entries = args.get("entries")
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("folder classification needs one sealed group")
        group = entries[0]
        labels = group.get("folder_labels") if isinstance(group, Mapping) else None
        if (
            not isinstance(group, Mapping)
            or not isinstance(group.get("part"), str)
            or re.fullmatch(r"[a-f0-9]{64}", group["part"]) is None
            or not isinstance(labels, list) or len(labels) > GROUP_SIZE
            or any(not isinstance(label, str) or len(label) > 4096 for label in labels)
        ):
            raise ValueError("folder classification group is malformed")
        model_identity = self.binding_identity()
        if not isinstance(model_identity, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", model_identity) is None:
            raise ValueError("folder classification model identity is missing")
        contexts = {}
        for label in dict.fromkeys(labels):
            key = (context.owner_user_id, self.language, self.identity, model_identity, label)
            with self._lock:
                value = self._cache.get(key)
                if value is not None:
                    self._cache.move_to_end(key)
            if value is None:
                value = (self.classifier or classify_folder_context)(label, self.language)
                if not isinstance(value, str) or len(value) > 8192:
                    raise ValueError("folder classification result is malformed")
                with self._lock:
                    self._cache[key] = value
                    self._cache.move_to_end(key)
                    while len(self._cache) > 1024:
                        self._cache.popitem(last=False)
            contexts[label] = value
        return {"entries": [{"part": group["part"], "folder_contexts": contexts}]}


def registration(*, catalog_loader=None, binding_resolver=None, language=None,
                 vlm_binding=None, prompt_digests=None) -> RuntimeRegistration:
    import config
    import prompt_loader
    import vlm_client
    from image_index_build import GROUP_SIZE

    lang = language or config.DEFAULT_LANG
    vlm = dict(vlm_binding if vlm_binding is not None else vlm_client.model_binding_facts())
    vlm["max_calls_per_attempt"] = GROUP_SIZE
    identities = dict(prompt_digests or {})
    if EXECUTOR not in identities:
        identities[EXECUTOR] = prompt_loader.prompt_identity("image_index_describe", lang).digest
    if FOLDER_WORKLOAD not in identities:
        from .schema import digest_json, MAX_SNAPSHOT_JSON_BYTES
        identities[FOLDER_WORKLOAD] = digest_json("image-index-folder-prompts", {
            role: prompt_loader.prompt_identity(role, lang).digest
            for role in ("image_index_folder", "image_index_context")
        }, max_bytes=MAX_SNAPSHOT_JSON_BYTES)
    schemas = output_schemas()
    runners = CompositeRunnerResolver(
        executors=VerifiedCatalogResolver(
            catalog_loader=catalog_loader,
            durable_effects={EXECUTOR: ("idempotent",)},
            durable_output_schemas={EXECUTOR: (DISCOVERY_SCHEMA, PART_SCHEMA, PUBLISHED_SCHEMA)},
            model_bindings={EXECUTOR: vlm}, prompt_digests={EXECUTOR: identities[EXECUTOR]},
            prompt_languages={EXECUTOR: lang}, hierarchical_reducers=(EXECUTOR,),
        ),
        workloads=RegisteredWorkloadResolver(
            output_schemas={FOLDER_WORKLOAD: (FOLDER_SCHEMA,)},
            input_names={FOLDER_WORKLOAD: ("entries",)},
            input_types={FOLDER_WORKLOAD: {"entries": "array"}},
            required_inputs={FOLDER_WORKLOAD: ("entries",)},
            prompt_digests={FOLDER_WORKLOAD: identities[FOLDER_WORKLOAD]},
            prompt_language=lang, max_input_tokens={FOLDER_WORKLOAD: 8192},
            max_output_tokens={FOLDER_WORKLOAD: 512},
            max_calls_per_attempt={FOLDER_WORKLOAD: GROUP_SIZE},
            binding_resolver=binding_resolver,
        ),
    )
    return RuntimeRegistration(
        name=PLAN_ID, runner_bindings=(("executor", EXECUTOR), ("workload", FOLDER_WORKLOAD)),
        runners=runners, output_schemas=schemas, output_schema_names=tuple(sorted(schemas.names)),
        workload_invoker=FolderContextInvoker(
            lang, identities[FOLDER_WORKLOAD],
            binding_identity=lambda: runners.resolve("workload", FOLDER_WORKLOAD).model_binding_digest,
        ),
    )


def build_candidate(request: Mapping, generation: str, runners, *, max_concurrency: int):
    from image_index_build import GROUP_SIZE, MAX_SOURCE_BYTES, MAX_SOURCE_DEPTH

    if not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None:
        raise ValueError("image indexing generation is invalid")
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 256:
        raise ValueError("image indexing concurrency is invalid")
    groups = max(1, (request["max_files"] + GROUP_SIZE - 1) // GROUP_SIZE)
    # A bounded reduction tree needs fewer than twice its leaf count.
    reductions = max(1, groups * 2)
    literal = lambda value: {"ref": "literal", "value": value}
    common = {"base_path": literal(request["base_path"]), "generation": literal(generation)}

    def stage(key, kind, runner, dependencies, schema, bindings, cardinality, **resources):
        # One signed executor has one frozen model contract. Keep its model
        # slot even for zero-call phases; only a distinct attested contract
        # could safely relax that resource boundary.
        if runner == EXECUTOR:
            resources["vlm"] = 1
        return {
            "key": key, "type": kind, "depends_on": dependencies,
            "runner": {"kind": "workload" if runner == FOLDER_WORKLOAD else "executor", "name": runner},
            "effect_profile": "pure" if runner == FOLDER_WORKLOAD else "idempotent",
            "cardinality": cardinality, "input_bindings": bindings,
            "output_schema": {"schema_version": "metnos.output-schema-ref/1", "name": schema},
            "retry": {"max_attempts": 3, "base_delay_ms": 1000, "max_delay_ms": 60_000,
                      "retryable_error_classes": ["executor_transient"]},
            "timeout_s": 3600 if key in {"discover", "publish"} else 1800,
            "invalidation_keys": ["dependencies.digest", "runner.contract_digest",
                                  "semantic_args.digest", "model_binding.digest", "prompt.digest"],
            "resources": {name: int(resources.get(name, 0)) for name in RESOURCE_KEYS},
            "required": True,
        }

    singleton = {"mode": "singleton", "max_units": 1}
    maps = {"mode": "per_dependency", "max_units": groups, "entry_identity_field": "part"}
    stages = [{
        "key": "inventory", "type": "inventory", "depends_on": [],
        "runner": {"kind": "internal", "name": "sealed_inventory"},
        "effect_profile": "pure", "cardinality": singleton,
        "input_bindings": {"inventory": {"ref": "revision.inventory"}},
        "output_schema": {"schema_version": "metnos.output-schema-ref/1", "name": "metnos.inventory-seal/1"},
        "retry": {"max_attempts": 1, "base_delay_ms": 0, "max_delay_ms": 0, "retryable_error_classes": []},
        "timeout_s": 60, "invalidation_keys": ["source.digest"],
        "resources": {name: int(name == "local_io") for name in RESOURCE_KEYS}, "required": True,
    }]
    stages.append(stage("discover", "validate", EXECUTOR, ["inventory"], DISCOVERY_SCHEMA, {
        **common, "phase": literal("discover"), "device_id": literal("server"),
        "recursive": literal(request["recursive"]), "max_files": literal(request["max_files"]),
        "max_total_bytes": literal(MAX_SOURCE_BYTES), "max_depth": literal(MAX_SOURCE_DEPTH),
    }, singleton, cpu=1, local_io=1))
    stages.append(stage("folders", "map", FOLDER_WORKLOAD, ["discover"], FOLDER_SCHEMA, {
        "entries": {"ref": "dependency.entries", "stage": "discover"},
    }, maps, llm=1))
    stages.append(stage("analyze", "map", EXECUTOR, ["folders"], PART_SCHEMA, {
        **common, "phase": literal("analyze"), "force": literal(request["force"]),
        "entries": {"ref": "dependency.entries", "stage": "folders"},
    }, maps, cpu=1, local_io=1, vlm=1))
    merge = stage("merge", "reduce", EXECUTOR, ["analyze"], PART_SCHEMA, {
        **common, "phase": literal("merge"),
        "entries": {"ref": "dependency.entries", "stage": "analyze"},
    }, {"mode": "singleton", "max_units": reductions, "fan_in": GROUP_SIZE,
        "reduction_input": "entries", "max_input_bytes": 262_144}, local_io=1)
    merge["invalidation_keys"] += ["reduction.order", "reduction.fan_in"]
    stages.append(merge)
    stages.append(stage("publish", "publish", EXECUTOR, ["merge", "discover"], PUBLISHED_SCHEMA, {
        **common, "phase": literal("publish"),
        "entries": {"ref": "dependency.entries", "stage": "merge"},
        "expected_count": {"ref": "dependency.result", "stage": "discover", "field": "source_count"},
    }, singleton, cpu=1, local_io=1))
    max_tokens = 0
    for item in stages[1:]:
        contract = runners.resolve(item["runner"]["kind"], item["runner"]["name"])
        max_tokens += item["cardinality"]["max_units"] * item["retry"]["max_attempts"] * (
            contract.model_max_calls * (contract.model_max_input_tokens + contract.model_max_output_tokens)
        )
    candidate = {
        "schema_version": "metnos.durable-plan/1", "plan_id": PLAN_ID,
        "objective_redacted": "Prepare a searchable photo index from authorized local files.",
        "inventory": {"mode": "sealed", "dynamic": False, "max_sources": 0,
                      "max_total_bytes": 0, "max_depth": 0, "symlink_policy": "ignore",
                      "unstable_policy": "reject", "missing_policy": "needs_attention"},
        "terminal_criteria": {"require_inventory_sealed": True, "require_usage_complete": True,
                              "reject_unaccepted_truncation": True},
        "error_policy": {"mode": "strict", "allowed_error_classes": []},
        "budgets": {
            "max_units": sum(item["cardinality"]["max_units"] for item in stages),
            "max_attempts_per_unit": 3, "max_wall_time_s": 2_592_000,
            "max_bytes_read": MAX_SOURCE_BYTES * 4, "max_bytes_written": MAX_SOURCE_BYTES,
            "max_tokens": max_tokens, "max_cost_micros": 0, "max_artifacts": 0,
            "max_concurrency": max_concurrency,
        }, "stages": stages, "required_artifacts": [],
    }
    inventory = {"schema_version": "metnos.durable-inventory/1", "sealed": True,
                 "sources": [], "digest": inventory_digest([])}
    return candidate, inventory


__all__ = ["PLAN_ID", "EXECUTOR", "registration", "normalize_request", "build_candidate"]
