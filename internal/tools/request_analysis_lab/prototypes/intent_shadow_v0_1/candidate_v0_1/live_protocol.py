"""Closed, query-only protocol primitives for the single paired measurement.

This module never opens gold and never performs network I/O.  It derives the
316-request manifest from the two already-frozen query-only panels, validates
all protocol artifacts with exact JSON types, and exposes the one approved
technical limit profile.  Semantic extraction lives in the two arm adapters.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from intent_shadow_io import (
    StrictJsonError,
    TechnicalLimits,
    canonical_json_bytes,
    file_sha256,
    require_exact_keys,
    require_exact_type,
    strict_json_file,
)
from intent_shadow_runner import load_legacy_panel, load_query_suite


PROTOCOL_FORMAT = "metnos.intent-shadow-live-protocol/0.1"
SNAPSHOT_FORMAT = "metnos.intent-shadow-current-control-snapshot/0.1"
MANIFEST_FORMAT = "metnos.intent-shadow-live-request-manifest/0.1"
PROTOCOL_FREEZE_FORMAT = "metnos.intent-shadow-live-protocol-freeze/0.1"
AUTHORIZATION_FORMAT = "metnos.intent-shadow-live-authorization/0.1"
SEALED_BATCH_FORMAT = "metnos.intent-shadow-live-sealed-batch/0.1"
CREATED_DATE = "2026-08-12"

# This is the one canonical no-regression set approved in the frozen
# protocol.  Order is used only to keep reports deterministic; membership and
# uniqueness are the semantic invariant.
CRITICAL_NO_REGRESSION_COLUMNS = (
    "semantic_exact",
    "root_exact",
    "correct_abstention",
    "technical_valid",
    "false_action_avoided",
    "undo_exact",
    "consent_exact",
    "negation_exact",
    "branch_ownership_exact",
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
PROTOCOL_PATH = HERE / "live_measurement_protocol_v0_1.json"
CONTROL_SNAPSHOT_PATH = HERE / "control_current_snapshot_v0_1.json"
REQUEST_MANIFEST_PATH = HERE / "live_request_manifest_v0_1.json"
PROTOCOL_FREEZE_PATH = HERE / "live_protocol_v0_1.freeze.json"
AUTHORIZATION_PATH = HERE / "live_run_authorization_v0_1.json"
REGISTRY_PATH = HERE.parent / "intent_shadow_registry_v0_1.json"
QUERY_SUITE_PATH = HERE / "intent_shadow_query_suite_v0_1.json"
LEGACY_PANEL_PATH = HERE / "legacy_panel_v0_1.json"
MODEL_PROMPT_PATH = HERE / "intent_shadow_model_v0_1.prompt.txt"
MODEL_SCHEMA_PATH = HERE / "intent_shadow_model_v0_1.schema.json"

LIVE_LIMITS = TechnicalLimits(
    max_bytes=256 * 1024,
    max_depth=64,
    max_nodes=10_000,
    max_string_chars=64 * 1024,
    max_integer_digits=64,
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Exact source membership for the fresh current-control implementation.  The
# snapshot records hashes; preflight compares every byte before importing it.
CONTROL_REPO_SOURCES = frozenset(
    {
        "runtime/compound_decomposer.py",
        "runtime/config.py",
        "runtime/detection_lexicon.py",
        "runtime/detection_lexicon_seed.py",
        "runtime/hashutil.py",
        "runtime/i18n.py",
        "runtime/intent_extractor.py",
        "runtime/logging_setup.py",
        "runtime/prefilter.py",
        "runtime/prompt_loader.py",
        "runtime/prompts/en/intent_extractor_v4.j2",
        "runtime/prompts/it/intent_extractor_v4.j2",
        "runtime/tool_grammar.py",
        "runtime/vocab.py",
    }
)

BACKEND_ABSOLUTE_SOURCES = frozenset(
    {
        "/etc/systemd/system/llama-server.service",
        "/etc/systemd/system/llama-server.service.d/override.conf",
        "/home/roberto/.config/metnos/llm_tiers.toml",
        "/home/roberto/llama.cpp-0712/build/bin/llama-server",
        "/home/roberto/llamacpp.env",
        "/home/roberto/models/qwen36-35b-mtp/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "/opt/suprastructure/config/models.yaml",
        "/opt/suprastructure/models/manifest.yaml",
    }
)

IMMUTABLE_PROTOCOL_LOCAL_FILES = frozenset(
    {
        "build_live_protocol.py",
        "control_current_snapshot_v0_1.json",
        "live_arm_candidate.py",
        "live_arm_current.py",
        "live_evaluator.py",
        "live_measurement_protocol_v0_1.json",
        "live_protocol.py",
        "live_request_manifest_v0_1.json",
        "live_runner.py",
        "test_live_protocol.py",
        "test_live_protocol_mutations.py",
        "verify_live_protocol.py",
    }
)

MUTABLE_RUN_FILES = frozenset(
    {
        "live_run_consumption_v0_1.json",
        "live_run_journal_v0_1.jsonl",
        "live_run_checkpoint_v0_1.json",
        "live_run_partial_v0_1.json",
        "live_run_sealed_batch_v0_1.json",
        "live_run_sealed_batch_v0_1.freeze.json",
        "live_evaluation_v0_1.json",
    }
)


def payload_sha256(document: dict[str, Any], field: str) -> str:
    payload = deepcopy(document)
    payload.pop(field, None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _sha(value: Any, path: str) -> None:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise StrictJsonError("SCHEMA_SHA256", path, "lowercase SHA-256 required")


def _constant(value: Any, expected: Any, path: str) -> None:
    if not _exact_json_equal(value, expected):
        raise StrictJsonError("SCHEMA_CONSTANT", path, repr(expected))


def _exact_json_equal(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(
            _exact_json_equal(value[key], item) for key, item in expected.items()
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _exact_json_equal(left, right)
            for left, right in zip(value, expected, strict=True)
        )
    return value == expected


def _exact_unique_strings(values: Any, expected: Iterable[str], path: str) -> None:
    require_exact_type(values, list, path)
    if any(type(item) is not str for item in values):
        raise StrictJsonError("SCHEMA_LIST_TYPE", path, "exact strings required")
    expected_set = set(expected)
    if len(values) != len(set(values)):
        raise StrictJsonError("SCHEMA_LIST_DUPLICATE", path, "duplicate")
    if len(values) != len(expected_set) or set(values) != expected_set:
        raise StrictJsonError("SCHEMA_LIST_SET", path, "canonical set differs")


def detection_lexicon_behavior_sha256(
    path: Path = Path("/home/roberto/.local/share/metnos/detection.sqlite"),
) -> tuple[str, int]:
    """Hash only columns that can affect matching; timestamps are nonsemantic."""
    columns = (
        "concept", "lang", "kind", "match_mode", "payload",
        "needs_translation", "source_lang", "version_hash", "source_text_hash",
    )
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                "SELECT " + ",".join(columns)
                + " FROM detection_lexicon ORDER BY concept,lang"
            )
        ]
    finally:
        if "connection" in locals():
            connection.close()
    return sha256(canonical_json_bytes(rows)).hexdigest(), len(rows)


def all_query_cases() -> list[dict[str, Any]]:
    suite = load_query_suite(QUERY_SUITE_PATH)
    legacy = load_legacy_panel(LEGACY_PANEL_PATH)
    cases: list[dict[str, Any]] = []
    for panel_name in ("canonical_120", "typed_controls_4"):
        for case in suite["panels"][panel_name]:
            cases.append(
                {
                    "sample_index": len(cases),
                    "panel": panel_name,
                    "panel_ordinal": case["ordinal"],
                    "opaque_case_id": case["opaque_case_id"],
                    "language": "it",
                    "query": case["query"],
                    "query_sha256": case["query_sha256"],
                }
            )
    for case in legacy["cases"]:
        cases.append(
            {
                "sample_index": len(cases),
                "panel": "legacy_phase1_34",
                "panel_ordinal": case["ordinal"],
                "opaque_case_id": case["opaque_case_id"],
                "language": case["language"],
                "query": case["query"],
                "query_sha256": case["query_sha256"],
            }
        )
    if len(cases) != 158:
        raise RuntimeError("query-only workload is not exactly 158")
    return cases


def arm_order(sample_index: int) -> tuple[str, str]:
    if type(sample_index) is not int or sample_index < 0:
        raise TypeError("sample_index must be a nonnegative exact integer")
    return ("A", "B") if sample_index % 2 == 0 else ("B", "A")


def openai_request(system: str, query: str) -> dict[str, Any]:
    if type(system) is not str or type(query) is not str:
        raise TypeError("request strings must be exact strings")
    return {
        "model": "local",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ],
        "temperature": 0,
        "seed": 42,
        "max_tokens": 4000,
        "stream": False,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def load_control_snapshot(path: Path = CONTROL_SNAPSHOT_PATH) -> dict[str, Any]:
    value = strict_json_file(path, limits=LIVE_LIMITS)
    require_exact_keys(
        value,
        {
            "snapshot_format", "created_date", "arm_id", "provenance",
            "current_extractor", "repo_sources", "runtime_state",
            "backend_identity", "snapshot_payload_sha256",
        },
        set(),
        "$",
    )
    _constant(value["snapshot_format"], SNAPSHOT_FORMAT, "$.snapshot_format")
    _constant(value["created_date"], CREATED_DATE, "$.created_date")
    _constant(value["arm_id"], "A", "$.arm_id")
    require_exact_keys(
        value["provenance"],
        {
            "label", "fresh_control", "production_read_only",
            "historic_v23lite_repair", "snapshot_kind",
        },
        set(),
        "$.provenance",
    )
    _constant(value["provenance"], {
        "label": "current_metnos_intent_extractor_with_lab_adapter",
        "fresh_control": True,
        "production_read_only": True,
        "historic_v23lite_repair": False,
        "snapshot_kind": "hash_pinned_sources_plus_logical_detection_lexicon",
    }, "$.provenance")
    require_exact_keys(
        value["current_extractor"],
        {
            "entrypoint", "workload", "tier", "level", "scaffold",
            "prompt_role", "prompt_languages", "response_schema",
            "production_max_tokens", "measurement_profile_override",
            "adapter_delta",
        },
        set(),
        "$.current_extractor",
    )
    _constant(value["current_extractor"]["entrypoint"], "runtime.intent_extractor.extract_intent", "$.current_extractor.entrypoint")
    _constant(value["current_extractor"]["workload"], "intent.extract", "$.current_extractor.workload")
    _constant(value["current_extractor"]["tier"], "fast", "$.current_extractor.tier")
    _constant(value["current_extractor"]["level"], "micro", "$.current_extractor.level")
    _constant(value["current_extractor"]["scaffold"], False, "$.current_extractor.scaffold")
    _constant(value["current_extractor"]["prompt_role"], "intent_extractor_v4", "$.current_extractor.prompt_role")
    _exact_unique_strings(value["current_extractor"]["prompt_languages"], {"it", "en"}, "$.current_extractor.prompt_languages")
    _constant(value["current_extractor"]["response_schema"], "none_tolerant_parser_in_entrypoint", "$.current_extractor.response_schema")
    _constant(value["current_extractor"]["production_max_tokens"], 320, "$.current_extractor.production_max_tokens")
    _constant(value["current_extractor"]["measurement_profile_override"], 4000, "$.current_extractor.measurement_profile_override")
    _constant(
        value["current_extractor"]["adapter_delta"],
        "exactly_one_primary_model_response; deterministic bypasses still receive one paired request; optional open-source probe is disabled after the primary response",
        "$.current_extractor.adapter_delta",
    )
    require_exact_type(value["repo_sources"], dict, "$.repo_sources")
    if set(value["repo_sources"]) != set(CONTROL_REPO_SOURCES):
        raise StrictJsonError("SNAPSHOT_SOURCE_SET", "$.repo_sources", "source membership")
    for key, digest in value["repo_sources"].items():
        _sha(digest, f"$.repo_sources[{key!r}]")
    require_exact_keys(value["runtime_state"], {"detection_lexicon_path", "behavior_sha256", "row_count", "language_policy"}, set(), "$.runtime_state")
    _constant(value["runtime_state"]["detection_lexicon_path"], "/home/roberto/.local/share/metnos/detection.sqlite", "$.runtime_state.detection_lexicon_path")
    _sha(value["runtime_state"]["behavior_sha256"], "$.runtime_state.behavior_sha256")
    if type(value["runtime_state"]["row_count"]) is not int or value["runtime_state"]["row_count"] < 1:
        raise StrictJsonError("SNAPSHOT_ROW_COUNT", "$.runtime_state.row_count", "positive exact integer")
    _constant(value["runtime_state"]["language_policy"], "canonical_and_typed=it; legacy=case_language; missing prompt falls back to en exactly as current loader", "$.runtime_state.language_policy")
    require_exact_keys(
        value["backend_identity"],
        {
            "provider", "endpoint", "api_model_id", "physical_model_id",
            "weights_path", "weights_size_bytes", "weights_sha256",
            "source_revision", "backend", "backend_build", "backend_binary",
            "backend_binary_sha256", "absolute_sources",
        },
        set(),
        "$.backend_identity",
    )
    constants = {
        "provider": "llamacpp",
        "endpoint": "http://localhost:8080/v1/chat/completions",
        "api_model_id": "local",
        "physical_model_id": "qwen3.6-35b-a3b",
        "weights_path": "/home/roberto/models/qwen36-35b-mtp/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "weights_size_bytes": 22663387424,
        "weights_sha256": "0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b",
        "source_revision": "5bc3e238d916f48a861bac2f8a1990a0e9b7e98d",
        "backend": "llama.cpp Vulkan",
        "backend_build": "1422 (e3546c794)",
        "backend_binary": "/home/roberto/llama.cpp-0712/build/bin/llama-server",
        "backend_binary_sha256": "14f86e51ea42cc2362285d1139d475d58b985a968e78cfbfdbdf9d9e9ee87c78",
    }
    for key, expected in constants.items():
        _constant(value["backend_identity"][key], expected, f"$.backend_identity.{key}")
    require_exact_type(value["backend_identity"]["absolute_sources"], dict, "$.backend_identity.absolute_sources")
    if set(value["backend_identity"]["absolute_sources"]) != set(BACKEND_ABSOLUTE_SOURCES):
        raise StrictJsonError("SNAPSHOT_BACKEND_SET", "$.backend_identity.absolute_sources", "source membership")
    for key, digest in value["backend_identity"]["absolute_sources"].items():
        _sha(digest, f"$.backend_identity.absolute_sources[{key!r}]")
    _sha(value["snapshot_payload_sha256"], "$.snapshot_payload_sha256")
    if payload_sha256(value, "snapshot_payload_sha256") != value["snapshot_payload_sha256"]:
        raise StrictJsonError("SNAPSHOT_PAYLOAD", "$.snapshot_payload_sha256", "mismatch")
    return value


def verify_control_environment(snapshot: dict[str, Any]) -> None:
    for relative, expected in snapshot["repo_sources"].items():
        path = ROOT / relative
        if stable_file_sha256(path) != expected:
            raise RuntimeError(f"current-control source drift: {relative}")
    for absolute, expected in snapshot["backend_identity"]["absolute_sources"].items():
        path = Path(absolute)
        if not path.is_file() or stable_file_sha256(path) != expected:
            raise RuntimeError(f"backend authority drift: {absolute}")
    behavior_sha, count = detection_lexicon_behavior_sha256(
        Path(snapshot["runtime_state"]["detection_lexicon_path"])
    )
    if behavior_sha != snapshot["runtime_state"]["behavior_sha256"] or count != snapshot["runtime_state"]["row_count"]:
        raise RuntimeError("detection lexicon behavior drift")


@lru_cache(maxsize=256)
def _cached_file_sha256(
    path_text: str,
    device: int,
    inode: int,
    size: int,
    mtime_ns: int,
    ctime_ns: int,
) -> str:
    del device, inode, size, mtime_ns, ctime_ns
    return file_sha256(Path(path_text))


def stable_file_sha256(path: Path) -> str:
    """Cache a digest only while all identity/change metadata stays equal.

    The verifier, preflight and their in-process test harness share this
    function, so a large unchanged authority is read once per process.  Any
    inode, size, mtime or ctime change creates a new cache key, and a change
    while hashing fails closed.
    """
    before = path.stat()
    key = (
        str(path), before.st_dev, before.st_ino, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    digest = _cached_file_sha256(*key)
    after = path.stat()
    after_key = (
        str(path), after.st_dev, after.st_ino, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if key != after_key:
        raise RuntimeError(f"authority changed while hashing: {path}")
    return digest


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    value = strict_json_file(path, limits=LIVE_LIMITS)
    require_exact_keys(
        value,
        {
            "protocol_format", "protocol_version", "created_date", "status",
            "authorization", "arms", "backend", "generation_profile",
            "technical_limits", "schedule", "panels", "failure_policy",
            "evaluation", "bindings", "protocol_payload_sha256",
        },
        set(),
        "$",
    )
    _constant(value["protocol_format"], PROTOCOL_FORMAT, "$.protocol_format")
    _constant(value["protocol_version"], "intent-shadow-paired-gpu/0.1", "$.protocol_version")
    _constant(value["created_date"], CREATED_DATE, "$.created_date")
    _constant(value["status"], "prepared_not_authorized", "$.status")
    _constant(value["authorization"], {
        "owner": "Roberto",
        "decisions_approved": 7,
        "inference_executed": False,
        "network_preflight_allowed": False,
        "independent_audit_required": True,
        "root_final_authorization_required": True,
        "authorization_file": "live_run_authorization_v0_1.json",
    }, "$.authorization")
    _constant(value["arms"], {
        "A": "fresh current Metnos intent extractor plus frozen lab adapter",
        "B": "intent-shadow candidate 0.1 direct schema extractor",
    }, "$.arms")
    snapshot = load_control_snapshot()
    _constant(value["backend"], snapshot["backend_identity"], "$.backend")
    _constant(value["generation_profile"], {
        "temperature": 0,
        "seed": 42,
        "max_output_tokens": 4000,
        "timeout_seconds": 120,
        "thinking": False,
        "retry_count": 0,
        "same_for_both_arms": True,
    }, "$.generation_profile")
    _constant(value["technical_limits"], {
        "json_bytes": 262144,
        "depth": 64,
        "nodes": 10000,
        "string_chars": 65536,
        "integer_digits": 64,
        "classification": "technical_invalid",
        "never_unrepresentable": True,
    }, "$.technical_limits")
    _constant(value["schedule"], {
        "algorithm": "AB on even sample_index; BA on odd sample_index",
        "sample_index_range": [0, 157],
        "query_count": 158,
        "arm_count": 2,
        "request_count": 316,
        "paired_request_optimization": False,
    }, "$.schedule")
    _constant(value["panels"], {
        "canonical_120": {"queries": 120, "both_arms": True, "reported_separately": True},
        "typed_controls_4": {"queries": 4, "both_arms": True, "reported_separately": True},
        "legacy_phase1_34": {
            "queries": 34,
            "both_arms": True,
            "separate_phase1_oracle": True,
            "automatic_conversion": False,
            "cross_panel_compensation": False,
        },
    }, "$.panels")
    _constant(value["failure_policy"], {
        "consumed_at": "first accepted HTTP POST",
        "marker_written_before_first_socket_attempt": True,
        "ambiguous_first_transport_attempt_blocks_rerun": True,
        "retry": False,
        "transport_or_timeout_after_consumption": "stop_and_seal_partial",
        "invalid_json_or_semantics": "count_error_and_continue",
        "raw_response_capture": True,
        "append_only_journal": True,
        "atomic_checkpoints": True,
    }, "$.failure_policy")
    require_exact_keys(
        value["evaluation"],
        {
            "gold_access", "typed_special_gate", "critical_columns_no_regression",
            "canonical_improvement_gate", "inconclusive_delta", "legacy_report",
        },
        set(),
        "$.evaluation",
    )
    _constant(
        value["evaluation"]["gold_access"],
        "only_after_complete_316_record_batch_is_sealed",
        "$.evaluation.gold_access",
    )
    _constant(
        value["evaluation"]["typed_special_gate"],
        "candidate_B_exact_4_of_4",
        "$.evaluation.typed_special_gate",
    )
    _exact_unique_strings(
        value["evaluation"]["critical_columns_no_regression"],
        CRITICAL_NO_REGRESSION_COLUMNS,
        "$.evaluation.critical_columns_no_regression",
    )
    _constant(
        value["evaluation"]["canonical_improvement_gate"],
        "candidate_B_minus_control_A_at_least_3_exact_of_120",
        "$.evaluation.canonical_improvement_gate",
    )
    _constant(value["evaluation"]["inconclusive_delta"], [-2, 2], "$.evaluation.inconclusive_delta")
    _constant(
        value["evaluation"]["legacy_report"],
        "separate Phase-1 direct-binding panel; no compensation",
        "$.evaluation.legacy_report",
    )
    require_exact_type(value["bindings"], dict, "$.bindings")
    if set(value["bindings"]) != {
        "control_snapshot_file_sha256", "registry_file_sha256",
        "query_suite_file_sha256", "legacy_panel_file_sha256",
        "candidate_prompt_sha256", "candidate_schema_sha256",
    }:
        raise StrictJsonError("PROTOCOL_BINDINGS", "$.bindings", "closed binding set")
    for key, digest in value["bindings"].items():
        _sha(digest, f"$.bindings.{key}")
    _sha(value["protocol_payload_sha256"], "$.protocol_payload_sha256")
    if payload_sha256(value, "protocol_payload_sha256") != value["protocol_payload_sha256"]:
        raise StrictJsonError("PROTOCOL_PAYLOAD", "$.protocol_payload_sha256", "mismatch")
    return value


def load_request_manifest(path: Path = REQUEST_MANIFEST_PATH) -> dict[str, Any]:
    value = strict_json_file(path, limits=TechnicalLimits(
        max_bytes=8 * 1024 * 1024, max_depth=64, max_nodes=100_000,
        max_string_chars=128 * 1024, max_integer_digits=64,
    ))
    require_exact_keys(
        value,
        {
            "manifest_format", "created_date", "status", "gold_fields_present",
            "protocol_payload_sha256", "counts", "records", "manifest_payload_sha256",
        },
        set(),
        "$",
    )
    _constant(value["manifest_format"], MANIFEST_FORMAT, "$.manifest_format")
    _constant(value["created_date"], CREATED_DATE, "$.created_date")
    _constant(value["status"], "query_only_frozen", "$.status")
    _constant(value["gold_fields_present"], False, "$.gold_fields_present")
    _constant(value["counts"], {"queries": 158, "arms": 2, "requests": 316}, "$.counts")
    _sha(value["protocol_payload_sha256"], "$.protocol_payload_sha256")
    require_exact_type(value["records"], list, "$.records")
    if len(value["records"]) != 316:
        raise StrictJsonError("MANIFEST_COUNT", "$.records", "exactly 316")
    cases = all_query_cases()
    identities: set[tuple[int, str]] = set()
    for offset, record in enumerate(value["records"]):
        path0 = f"$.records[{offset}]"
        require_exact_keys(
            record,
            {
                "request_ordinal", "sample_index", "within_case_arm_ordinal",
                "panel", "panel_ordinal", "opaque_case_id", "language",
                "query", "query_sha256", "arm", "request", "request_sha256",
            },
            set(),
            path0,
        )
        if type(record["request_ordinal"]) is not int or record["request_ordinal"] != offset + 1:
            raise StrictJsonError("MANIFEST_ORDINAL", f"{path0}.request_ordinal", "sequence")
        if type(record["sample_index"]) is not int or not 0 <= record["sample_index"] < 158:
            raise StrictJsonError("MANIFEST_SAMPLE_INDEX", f"{path0}.sample_index", "range")
        case = cases[record["sample_index"]]
        expected_arm_order = arm_order(record["sample_index"])
        position = offset % 2
        expected = {
            "sample_index": case["sample_index"],
            "within_case_arm_ordinal": position + 1,
            "panel": case["panel"],
            "panel_ordinal": case["panel_ordinal"],
            "opaque_case_id": case["opaque_case_id"],
            "language": case["language"],
            "query": case["query"],
            "query_sha256": case["query_sha256"],
            "arm": expected_arm_order[position],
        }
        for key, expected_value in expected.items():
            _constant(record[key], expected_value, f"{path0}.{key}")
        if offset // 2 != record["sample_index"]:
            raise StrictJsonError("MANIFEST_PAIR", path0, "pair adjacency")
        _sha(record["request_sha256"], f"{path0}.request_sha256")
        if sha256(canonical_json_bytes(record["request"])).hexdigest() != record["request_sha256"]:
            raise StrictJsonError("MANIFEST_REQUEST_HASH", f"{path0}.request_sha256", "mismatch")
        if record["request"].get("messages", [{}, {}])[1].get("content") != case["query"]:
            raise StrictJsonError("MANIFEST_QUERY_ONLY", f"{path0}.request", "user message mismatch")
        identity = (record["sample_index"], record["arm"])
        if identity in identities:
            raise StrictJsonError("MANIFEST_DUPLICATE", path0, repr(identity))
        identities.add(identity)
    if identities != {(index, arm) for index in range(158) for arm in ("A", "B")}:
        raise StrictJsonError("MANIFEST_COVERAGE", "$.records", "pair coverage")
    _sha(value["manifest_payload_sha256"], "$.manifest_payload_sha256")
    if payload_sha256(value, "manifest_payload_sha256") != value["manifest_payload_sha256"]:
        raise StrictJsonError("MANIFEST_PAYLOAD", "$.manifest_payload_sha256", "mismatch")
    return value


def canonical_write_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2,
    ).encode("utf-8") + b"\n"
