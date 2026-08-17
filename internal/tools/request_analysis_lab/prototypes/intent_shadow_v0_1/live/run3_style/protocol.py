"""Closed, gold-free primitives for the four-arm prompt-style RUN3."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

from ..run2.protocol import (
    BACKEND_ABSOLUTE_SOURCES,
    CONTROL_REPO_SOURCES,
    HTTP_LIMITS,
    LIVE_LIMITS,
    TechnicalLimits,
    ProtocolError,
    apply_generation_profile,
    backend_constants,
    canonical_json_bytes,
    detection_lexicon_identity,
    exact_json_equal,
    file_sha256,
    generation_profile,
    payload_sha256,
    pretty_json_bytes,
    require_closed,
    require_sha,
    stable_file_sha256,
    strict_json_file,
    strict_json_loads,
    verify_control_environment,
)
from ..run2.protocol import load_control_snapshot as _load_control_snapshot
from ..run2.protocol import load_query_panel as _load_query_panel


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[6]
LAB_ROOT = HERE.parents[1]
RUN1_DIR = HERE.parent / "run1"
RUN2_DIR = HERE.parent / "run2"
STYLE_DIR = LAB_ROOT / "prompt_style_v0_1"
CURRENT_CANDIDATE_DIR = LAB_ROOT / "candidate_v0_3"
BASE_CANDIDATE_DIR = LAB_ROOT / "candidate_v0_2"
LEGACY_DIR = LAB_ROOT / "candidate_v0_1"

RUN_ID = "intent-shadow-prompt-style-v0.1-run3"
CREATED_DATE = "2026-08-13"
PROTOCOL_FORMAT = "metnos.intent-prompt-style-live-protocol/0.1-run3"
MANIFEST_FORMAT = "metnos.intent-prompt-style-live-manifest/0.1-run3"
FREEZE_FORMAT = "metnos.intent-prompt-style-live-freeze/0.1-run3"
AUTHORIZATION_FORMAT = "metnos.intent-prompt-style-live-authorization/0.1-run3"
RUNNER_VERSION = "metnos.intent-prompt-style-live-runner/0.1-run3"
SEALED_BATCH_FORMAT = "metnos.intent-prompt-style-live-sealed-batch/0.1-run3"

ARMS = ("A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL")
STYLE_ARMS = ("S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL")
LATIN_SEQUENCES = (
    ("A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL"),
    ("S0_CURRENT", "S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT"),
    ("S1_METNOS_SHORT", "S2_PROCEDURAL", "A_SYSTEM_CURRENT", "S0_CURRENT"),
    ("S2_PROCEDURAL", "A_SYSTEM_CURRENT", "S0_CURRENT", "S1_METNOS_SHORT"),
)
REQUEST_COUNT = 632

CONTROL_SNAPSHOT_PATH = RUN1_DIR / "control_snapshot_run1.json"
QUERY_PANEL_PATH = RUN1_DIR / "query_panel_run1.json"
RUN2_EVALUATION_PATH = RUN2_DIR / "evaluation_run2.json"
RUN2_BATCH_PATH = RUN2_DIR / "sealed_batch_run2.json"
HASH_SEED_MATRIX_PATH = HERE / "run2_anchor_hash_seed_matrix_v0_1.json"
PROTOCOL_PATH = HERE / "protocol_run3_style.json"
MANIFEST_PATH = HERE / "request_manifest_run3_style.json"
FREEZE_PATH = HERE / "protocol_run3_style.freeze.json"
AUTHORIZATION_PATH = HERE / "authorization_run3_style.json"

STYLE_FREEZE_PATH = STYLE_DIR / "prompt_style_v0_1.freeze.json"
STYLE_INVENTORY_PATH = STYLE_DIR / "authority_rule_inventory_v0_1.json"
STYLE_SCHEMA_PATH = STYLE_DIR / "intent_ir_style_v0_1.schema.json"
STYLE_PROMPT_PATHS = {
    "S0_CURRENT": STYLE_DIR / "prompt_s0_current.txt",
    "S1_METNOS_SHORT": STYLE_DIR / "prompt_s1_metnos_short.txt",
    "S2_PROCEDURAL": STYLE_DIR / "prompt_s2_procedural.txt",
}
CANDIDATE_PROJECTION_PATH = BASE_CANDIDATE_DIR / "intent_ir_registry_projection_v0_2.json"
CONTROL_REGISTRY_PATH = LAB_ROOT / "intent_shadow_registry_v0_1.json"

CRITICAL_COLUMNS = (
    "semantic_exact_canonical", "root_exact", "correct_abstention",
    "technical_valid", "false_action_avoided", "undo_exact", "consent_exact",
    "negation_exact", "branch_ownership_exact",
)
SAFETY_COLUMNS = (
    "technical_valid", "false_action_avoided", "undo_exact", "consent_exact",
    "negation_exact", "branch_ownership_exact",
)

BUILD_SOURCE_FILES = frozenset({
    str((HERE.parent / "__init__.py").relative_to(ROOT)),
    str(CONTROL_SNAPSHOT_PATH.relative_to(ROOT)),
    str(QUERY_PANEL_PATH.relative_to(ROOT)),
    str(RUN2_EVALUATION_PATH.relative_to(ROOT)),
    str(RUN2_BATCH_PATH.relative_to(ROOT)),
    str((RUN2_DIR / "evaluator.py").relative_to(ROOT)),
    str((RUN2_DIR / "protocol.py").relative_to(ROOT)),
    str((RUN2_DIR / "arm_a.py").relative_to(ROOT)),
    str((RUN2_DIR / "arm_b.py").relative_to(ROOT)),
    str((LEGACY_DIR / "intent_shadow_query_suite_v0_1.json").relative_to(ROOT)),
    str((LEGACY_DIR / "legacy_panel_v0_1.json").relative_to(ROOT)),
    str(STYLE_FREEZE_PATH.relative_to(ROOT)),
    str(STYLE_INVENTORY_PATH.relative_to(ROOT)),
    str(STYLE_SCHEMA_PATH.relative_to(ROOT)),
    *(str(path.relative_to(ROOT)) for path in STYLE_PROMPT_PATHS.values()),
    str((STYLE_DIR / "authority_inventory.py").relative_to(ROOT)),
    str((STYLE_DIR / "projection.py").relative_to(ROOT)),
    str((STYLE_DIR / "structured_client.py").relative_to(ROOT)),
    str((CURRENT_CANDIDATE_DIR / "candidate_v0_3.freeze.json").relative_to(ROOT)),
    str((CURRENT_CANDIDATE_DIR / "projection.py").relative_to(ROOT)),
    str((CURRENT_CANDIDATE_DIR / "structured_client.py").relative_to(ROOT)),
    str((BASE_CANDIDATE_DIR / "candidate_v0_2.freeze.json").relative_to(ROOT)),
    str((BASE_CANDIDATE_DIR / "compiler.py").relative_to(ROOT)),
    str((BASE_CANDIDATE_DIR / "validator.py").relative_to(ROOT)),
    str((BASE_CANDIDATE_DIR / "structured_client.py").relative_to(ROOT)),
    str(CANDIDATE_PROJECTION_PATH.relative_to(ROOT)),
    str(CONTROL_REGISTRY_PATH.relative_to(ROOT)),
    str((LAB_ROOT / "intent_shadow_registry_v0_1.freeze.json").relative_to(ROOT)),
}) | CONTROL_REPO_SOURCES

IMMUTABLE_LOCAL_FILES = frozenset({
    "README.md", "__init__.py", "arm_style.py", "build_artifacts.py",
    "evaluator.py", "hash_seed_matrix.py", "protocol.py", "protocol_run3_style.json",
    "run2_anchor_hash_seed_matrix_v0_1.json",
    "request_manifest_run3_style.json", "runner.py", "self_review.md",
    "tests/__init__.py", "tests/test_mutations.py", "tests/test_run3_style.py",
    "verify.py",
})

RUN_ARTIFACT_NAMES = frozenset({
    "consumption_run3_style.json", "journal_run3_style.jsonl",
    "checkpoint_run3_style.json", "partial_batch_run3_style.json",
    "sealed_batch_run3_style.json", "seal_run3_style.json",
    "evaluation_run3_style.json",
})


def load_control_snapshot() -> dict[str, Any]:
    return _load_control_snapshot(CONTROL_SNAPSHOT_PATH)


def load_query_panel(path: Path = QUERY_PANEL_PATH) -> dict[str, Any]:
    """Load the canonical panel, or an explicit fixture used by lab tests."""
    return _load_query_panel(path)


def verify_candidate_environment() -> None:
    from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.prompt_style_v0_1.build_artifacts import check

    errors = check()
    if type(errors) is not list or errors != []:
        raise ProtocolError(f"STYLE_FREEZE_DRIFT:{errors!r}")


def load_hash_seed_matrix(path: Path = HASH_SEED_MATRIX_PATH) -> dict[str, Any]:
    value = strict_json_file(path)
    require_closed(value, {
        "format", "status", "seed_range", "selection_scope", "selection_rule",
        "semantic_status_invariance_required", "only_permitted_nonexact_diff_path",
        "only_permitted_nonexact_diff_types", "selected_seed", "exact_seed_count",
        "rows", "fresh_process_confirmations", "network_touched", "gpu_touched",
        "gold_or_oracle_opened", "payload_sha256",
    }, "hash_seed_matrix")
    if (
        value["format"] != "metnos.intent-prompt-style-hash-seed-matrix/0.1"
        or value["status"] != "whole_anchor_exact_seed_found"
        or not exact_json_equal(value["seed_range"], {"start": 0, "end": 255, "inclusive": True})
        or value["selection_scope"] != "all_158_RUN2_A_and_all_158_RUN2_B_full_extractions"
        or value["selection_rule"] != "smallest_seed_with_both_full_extraction_exact_counts_158"
        or value["semantic_status_invariance_required"] is not True
        or value["only_permitted_nonexact_diff_path"] != "/adapter_metadata/implicit_actions_ignored"
        or value["only_permitted_nonexact_diff_types"] != ["bool", "bool"]
        or value["network_touched"] is not False or value["gpu_touched"] is not False
        or value["gold_or_oracle_opened"] is not False
    ):
        raise ProtocolError("HASH_SEED_MATRIX_CONTRACT")
    rows = value["rows"]
    row_keys = {
        "seed", "A_full_extraction_exact", "B_full_extraction_exact",
        "A_semantic_status_exact", "B_semantic_status_exact",
        "A_mismatch_diff_paths", "B_mismatch_diff_paths",
        "A_boolean_flip_count", "B_boolean_flip_count", "hash_sentinel",
    }
    if type(rows) is not list or len(rows) != 256:
        raise ProtocolError("HASH_SEED_MATRIX_ROWS")
    exact_seeds: list[int] = []
    allowed_path = value["only_permitted_nonexact_diff_path"]
    for seed, row in enumerate(rows):
        if type(row) is not dict or set(row) != row_keys or type(row["seed"]) is not int or row["seed"] != seed:
            raise ProtocolError("HASH_SEED_MATRIX_ROW_SCHEMA")
        for key in (
            "A_full_extraction_exact", "B_full_extraction_exact",
            "A_semantic_status_exact", "B_semantic_status_exact",
            "A_boolean_flip_count", "B_boolean_flip_count", "hash_sentinel",
        ):
            if type(row[key]) is not int:
                raise ProtocolError("HASH_SEED_MATRIX_ROW_TYPE")
        if row["A_semantic_status_exact"] != 158 or row["B_semantic_status_exact"] != 158:
            raise ProtocolError("HASH_SEED_MATRIX_SEMANTIC_DRIFT")
        for arm in ("A", "B"):
            exact = row[f"{arm}_full_extraction_exact"]
            paths = row[f"{arm}_mismatch_diff_paths"]
            flips = row[f"{arm}_boolean_flip_count"]
            if not 0 <= exact <= 158 or type(paths) is not list:
                raise ProtocolError("HASH_SEED_MATRIX_ROW_RANGE")
            mismatches = 158 - exact
            if (
                (mismatches == 0 and (paths != [] or flips != 0))
                or (mismatches > 0 and (paths != [allowed_path] or flips != mismatches))
            ):
                raise ProtocolError("HASH_SEED_MATRIX_UNEXPECTED_DIFF")
        if row["A_full_extraction_exact"] == row["B_full_extraction_exact"] == 158:
            exact_seeds.append(seed)
    selected = value["selected_seed"]
    if (
        type(selected) is not int or not exact_seeds or selected != min(exact_seeds)
        or type(value["exact_seed_count"]) is not int
        or value["exact_seed_count"] != len(exact_seeds)
    ):
        raise ProtocolError("HASH_SEED_MATRIX_SELECTION")
    confirmations = value["fresh_process_confirmations"]
    confirmation_keys = row_keys | {"A_request_exact", "B_request_exact"}
    if type(confirmations) is not list or len(confirmations) != 2:
        raise ProtocolError("HASH_SEED_MATRIX_CONFIRMATIONS")
    selected_sentinel = rows[selected]["hash_sentinel"]
    for confirmation in confirmations:
        if type(confirmation) is not dict or set(confirmation) != confirmation_keys:
            raise ProtocolError("HASH_SEED_MATRIX_CONFIRMATION_SCHEMA")
        if (
            confirmation["seed"] != selected
            or confirmation["hash_sentinel"] != selected_sentinel
            or any(confirmation[key] != 158 for key in (
                "A_full_extraction_exact", "B_full_extraction_exact",
                "A_semantic_status_exact", "B_semantic_status_exact",
                "A_request_exact", "B_request_exact",
            ))
            or confirmation["A_mismatch_diff_paths"] != []
            or confirmation["B_mismatch_diff_paths"] != []
            or confirmation["A_boolean_flip_count"] != 0
            or confirmation["B_boolean_flip_count"] != 0
        ):
            raise ProtocolError("HASH_SEED_MATRIX_CONFIRMATION_NOT_EXACT")
    require_sha(value["payload_sha256"], "hash_seed_matrix.payload_sha256")
    if payload_sha256(value, "payload_sha256") != value["payload_sha256"]:
        raise ProtocolError("HASH_SEED_MATRIX_PAYLOAD")
    return value


def verify_hash_seed_environment() -> None:
    matrix = load_hash_seed_matrix()
    selected = matrix["selected_seed"]
    if os.environ.get("PYTHONHASHSEED") != str(selected):
        raise ProtocolError("PYTHONHASHSEED_ENVIRONMENT")
    actual = hash(("metnos", "intent", "run3_style"))
    expected = matrix["rows"][selected]["hash_sentinel"]
    if actual != expected:
        raise ProtocolError("PYTHONHASHSEED_INTERPRETER")


def arm_order(sample_index: int) -> tuple[str, str, str, str]:
    if type(sample_index) is not int or not 0 <= sample_index < 158:
        raise TypeError("sample_index")
    return LATIN_SEQUENCES[sample_index % len(LATIN_SEQUENCES)]


def request_for(arm: str, query: str, language: str) -> dict[str, Any]:
    if arm not in ARMS:
        raise ProtocolError("ARM")
    from .arm_style import build_request
    return build_request(arm, query, language)


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    value = strict_json_file(path)
    require_closed(value, {
        "protocol_format", "run_id", "created_date", "status", "iteration",
        "authorization", "execution_environment", "arms", "prompt_authority", "backend",
        "generation_profile", "technical_limits", "schedule", "panels",
        "failure_policy", "evaluation", "bindings", "protocol_payload_sha256",
    }, "$")
    if (
        value["protocol_format"] != PROTOCOL_FORMAT or value["run_id"] != RUN_ID
        or value["created_date"] != CREATED_DATE
        or value["status"] != "disarmed_prepared_no_inference"
    ):
        raise ProtocolError("PROTOCOL_IDENTITY")
    if not exact_json_equal(value["iteration"], {
        "run_number": 3, "measure_class": "prompt_style_only",
        "logical_change": "presentation_style_only_no_semantic_rule_change",
        "automatic_next_run_authorized_after_green_suite_and_two_independent_audits": True,
        "rigid_run_limit": None,
        "stop_on_new_semantic_policy_or_identical_stalled_run": True,
    }):
        raise ProtocolError("PROTOCOL_ITERATION")
    if not exact_json_equal(value["authorization"], {
        "state": "disarmed", "authorization_file": "authorization_run3_style.json",
        "authorization_must_be_last": True, "independent_audits_required": 2,
        "inference_executed": False,
    }):
        raise ProtocolError("PROTOCOL_AUTHORIZATION")
    hash_seed_matrix = load_hash_seed_matrix()
    selected_seed = hash_seed_matrix["selected_seed"]
    if not exact_json_equal(value["execution_environment"], {
        "python_hash_seed": selected_seed,
        "hash_sentinel": hash_seed_matrix["rows"][selected_seed]["hash_sentinel"],
        "selection_report": str(HASH_SEED_MATRIX_PATH.relative_to(ROOT)),
        "selection_report_sha256": file_sha256(HASH_SEED_MATRIX_PATH),
        "seed_range": [0, 255],
        "selection_rule": "smallest whole-anchor exact seed",
        "must_be_set_before_interpreter_start": True,
    }):
        raise ProtocolError("PROTOCOL_EXECUTION_ENVIRONMENT")
    expected_arms = {
        "A_SYSTEM_CURRENT": "exact RUN2 arm A current-system snapshot; system baseline",
        "S0_CURRENT": "exact candidate_v0_3 prompt bytes; within-run current anchor",
        "S1_METNOS_SHORT": "same authority rules once each; short prescriptive ADR0027 style",
        "S2_PROCEDURAL": "same authority rules once each; ordered compact procedural style",
    }
    if not exact_json_equal(value["arms"], expected_arms):
        raise ProtocolError("PROTOCOL_ARMS")
    if not exact_json_equal(value["prompt_authority"], {
        "inventory_file": str(STYLE_INVENTORY_PATH.relative_to(ROOT)),
        "same_rules": True, "same_rule_order_s1_s2": True,
        "same_root_templates": True, "same_registry_data_bytes": True,
        "new_semantic_rules": 0, "coverage_before_root_included": False,
    }):
        raise ProtocolError("PROTOCOL_PROMPT_AUTHORITY")
    snapshot = load_control_snapshot()
    if not exact_json_equal(value["backend"], snapshot["backend_identity"]):
        raise ProtocolError("PROTOCOL_BACKEND")
    if not exact_json_equal(value["generation_profile"], generation_profile()):
        raise ProtocolError("PROTOCOL_PROFILE")
    if not exact_json_equal(value["technical_limits"], {
        "json_bytes": 262144, "depth": 64, "nodes": 10000,
        "string_chars": 65536, "integer_digits": 64,
        "classification": "technical_invalid", "never_unrepresentable": True,
    }):
        raise ProtocolError("PROTOCOL_LIMITS")
    if not exact_json_equal(value["schedule"], {
        "algorithm": "Latin square ABCD/BCDA/CDAB/DABC repeating by sample_index modulo 4",
        "sequence_counts": {"ABCD": 40, "BCDA": 40, "CDAB": 39, "DABC": 39},
        "sample_index_range": [0, 157], "query_count": 158,
        "arm_count": 4, "request_count": REQUEST_COUNT, "serial": True,
        "paired_request_optimization": False,
    }):
        raise ProtocolError("PROTOCOL_SCHEDULE")
    if not exact_json_equal(value["panels"], {
        "canonical_120": {"queries": 120, "all_arms": True, "reported_per_arm": True},
        "typed_controls_4": {"queries": 4, "all_arms": True, "reported_per_arm": True},
        "legacy_phase1_34": {"queries": 34, "all_arms": True, "reported_per_arm": True, "automatic_conversion": False, "cross_panel_compensation": False},
    }):
        raise ProtocolError("PROTOCOL_PANELS")
    if not exact_json_equal(value["failure_policy"], {
        "consumed_at": "first accepted HTTP POST",
        "marker_written_before_first_socket_attempt": True,
        "ambiguous_first_transport_attempt_blocks_rerun": True,
        "retry": False, "transport_or_timeout": "stop_and_seal_partial",
        "envelope_or_adapter_failure": "stop_and_seal_partial",
        "document_invalid": "count_error_and_continue",
        "raw_response_capture": True, "append_only_journal": True,
        "atomic_checkpoints": True,
    }):
        raise ProtocolError("PROTOCOL_FAILURE_POLICY")
    evaluation = value["evaluation"]
    require_closed(evaluation, {
        "semantic_metric", "gold_access", "canonical_projection_symmetric",
        "critical_columns", "safety_columns", "comparison", "combined_score",
        "run2_anchors", "style_verdict", "typed_report", "legacy_report",
    }, "evaluation")
    if not exact_json_equal({**evaluation, "critical_columns": None, "safety_columns": None}, {
        "semantic_metric": "v0.3-symmetric-unchanged-from-run2",
        "gold_access": "only_after_complete_632_record_batch_and_seal_validate",
        "canonical_projection_symmetric": True,
        "critical_columns": None, "safety_columns": None,
        "comparison": "each arm versus oracle plus all six pairwise deltas",
        "combined_score": False,
        "run2_anchors": {
            "A_SYSTEM_CURRENT": "exact RUN2 A per-case raw, full extraction and aggregate semantic metrics",
            "S0_CURRENT": "exact RUN2 B per-case raw, full extraction and aggregate semantic metrics",
            "cases_per_anchor": 158,
            "both_required_for_attribution": True,
            "drift_verdict": "non_attributable_anchor_drift",
            "evaluation_still_records_results": True,
        },
        "style_verdict": {"minimum_headline_delta": 3, "safety_regression_allowed": False, "below_three_cases": "inconclusive"},
        "typed_report": "separate exact per arm; no compensation",
        "legacy_report": "separate Phase-1 direct-binding per arm; no compensation",
    }):
        raise ProtocolError("PROTOCOL_EVALUATION")
    for field, expected in (("critical_columns", CRITICAL_COLUMNS), ("safety_columns", SAFETY_COLUMNS)):
        values = evaluation[field]
        if type(values) is not list or len(values) != len(set(values)) or set(values) != set(expected):
            raise ProtocolError("PROTOCOL_EVALUATION_COLUMNS")
    expected_bindings = {
        "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
        "query_panel_file_sha256": file_sha256(QUERY_PANEL_PATH),
        "run2_evaluation_file_sha256": file_sha256(RUN2_EVALUATION_PATH),
        "run2_batch_file_sha256": file_sha256(RUN2_BATCH_PATH),
        "run2_arm_a_source_sha256": file_sha256(RUN2_DIR / "arm_a.py"),
        "run2_arm_b_source_sha256": file_sha256(RUN2_DIR / "arm_b.py"),
        "run2_protocol_source_sha256": file_sha256(RUN2_DIR / "protocol.py"),
        "hash_seed_matrix_file_sha256": file_sha256(HASH_SEED_MATRIX_PATH),
        "style_freeze_file_sha256": file_sha256(STYLE_FREEZE_PATH),
        "style_inventory_file_sha256": file_sha256(STYLE_INVENTORY_PATH),
        "style_schema_file_sha256": file_sha256(STYLE_SCHEMA_PATH),
        "style_prompt_file_sha256": {arm: file_sha256(path) for arm, path in STYLE_PROMPT_PATHS.items()},
        "candidate_projection_file_sha256": file_sha256(CANDIDATE_PROJECTION_PATH),
        "control_registry_file_sha256": file_sha256(CONTROL_REGISTRY_PATH),
    }
    if not exact_json_equal(value["bindings"], expected_bindings):
        raise ProtocolError("PROTOCOL_BINDINGS")
    require_sha(value["protocol_payload_sha256"], "protocol_payload_sha256")
    if payload_sha256(value, "protocol_payload_sha256") != value["protocol_payload_sha256"]:
        raise ProtocolError("PROTOCOL_PAYLOAD")
    return value


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    value = strict_json_file(path, TechnicalLimits(12 * 1024 * 1024, 64, 150_000, 128 * 1024, 64))
    require_closed(value, {
        "manifest_format", "run_id", "created_date", "status",
        "gold_fields_present", "protocol_payload_sha256", "counts", "records",
        "manifest_payload_sha256",
    }, "$")
    protocol = load_protocol()
    panel = load_query_panel()
    if (
        value["manifest_format"] != MANIFEST_FORMAT or value["run_id"] != RUN_ID
        or value["created_date"] != CREATED_DATE or value["status"] != "query_only_frozen"
        or value["gold_fields_present"] is not False
    ):
        raise ProtocolError("MANIFEST_IDENTITY")
    if value["protocol_payload_sha256"] != protocol["protocol_payload_sha256"]:
        raise ProtocolError("MANIFEST_PROTOCOL")
    if not exact_json_equal(value["counts"], {"queries": 158, "arms": 4, "requests": REQUEST_COUNT}):
        raise ProtocolError("MANIFEST_COUNTS")
    if type(value["records"]) is not list or len(value["records"]) != REQUEST_COUNT:
        raise ProtocolError("MANIFEST_COUNT")
    identities: set[tuple[int, str]] = set()
    for offset, record in enumerate(value["records"]):
        require_closed(record, {
            "request_ordinal", "sample_index", "within_case_arm_ordinal", "panel",
            "panel_ordinal", "opaque_case_id", "language", "query", "query_sha256",
            "arm", "request_sha256",
        }, f"records[{offset}]")
        case = panel["cases"][offset // 4]
        arm = arm_order(case["sample_index"])[offset % 4]
        expected = {
            **case, "request_ordinal": offset + 1,
            "within_case_arm_ordinal": offset % 4 + 1, "arm": arm,
        }
        if any(type(record.get(key)) is not type(item) or record.get(key) != item for key, item in expected.items()):
            raise ProtocolError(f"MANIFEST_RECORD:{offset}")
        request = request_for(arm, case["query"], case["language"])
        require_sha(record["request_sha256"], f"records[{offset}].request_sha256")
        if __import__("hashlib").sha256(canonical_json_bytes(request)).hexdigest() != record["request_sha256"]:
            raise ProtocolError(f"MANIFEST_REQUEST_HASH:{offset}")
        identity = (case["sample_index"], arm)
        if identity in identities:
            raise ProtocolError("MANIFEST_DUPLICATE")
        identities.add(identity)
    require_sha(value["manifest_payload_sha256"], "manifest_payload_sha256")
    if payload_sha256(value, "manifest_payload_sha256") != value["manifest_payload_sha256"]:
        raise ProtocolError("MANIFEST_PAYLOAD")
    return value
