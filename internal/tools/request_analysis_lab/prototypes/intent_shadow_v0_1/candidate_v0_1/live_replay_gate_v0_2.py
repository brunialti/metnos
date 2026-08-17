#!/usr/bin/env python3
"""Pre-gold replay gate 0.2 for the completed paired measurement.

This module is laboratory-only and performs no network or model call.  It
verifies the frozen measurement chain, reconstructs model content from the
saved HTTP bytes, and replays all 316 adapter calls before any evaluator may
open gold.

Roberto's approved exception is deliberately narrow: at zero-based record
index 80 only, saved and replayed extraction may differ at the single pointer
``adapter_metadata.implicit_actions_ignored`` when both values are exact JSON
booleans.  The field remains visible in the report.  Every other value and
type must be byte-equivalent canonical JSON.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from intent_shadow_io import (
    canonical_json_bytes,
    file_sha256,
    require_exact_keys,
    strict_json_file,
    strict_json_loads,
)
from live_protocol import (
    AUTHORIZATION_PATH,
    PROTOCOL_FREEZE_PATH,
    PROTOCOL_PATH,
    REQUEST_MANIFEST_PATH,
    SEALED_BATCH_FORMAT,
    load_protocol,
    load_request_manifest,
    payload_sha256,
)


REPLAY_GATE_VERSION = "metnos.intent-shadow-live-replay-gate/0.2"
FREEZE_FORMAT = "metnos.intent-shadow-live-replay-freeze/0.2"
CREATED_DATE = "2026-08-13"
ALLOWED_POINTER = "adapter_metadata.implicit_actions_ignored"
ALLOWED_RECORD_INDEX = 80
ALLOWED_RECORD_IDENTITY = {
    "request_ordinal": 81,
    "sample_index": 40,
    "panel": "canonical_120",
    "panel_ordinal": 41,
    "opaque_case_id": "frozen_sample.040",
    "query_sha256": "a28c35924994e5735fe8463e81184f178a24615fa2d1f5b3db64aa3d35b8cd2b",
    "arm": "A",
    "request_sha256": "d4849e0d55076286d9e922ca8e4cb19dcaf696a2bcd4378e7a1fdf79fe33c105",
}
ALLOWED_REASON = "approved_boolean_diagnostic_variance_at_fixed_record"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
BATCH_PATH = HERE / "live_run_sealed_batch_v0_1.json"
SEAL_PATH = HERE / "live_run_sealed_batch_v0_1.freeze.json"
JOURNAL_PATH = HERE / "live_run_journal_v0_1.jsonl"
MARKER_PATH = HERE / "live_run_consumption_v0_1.json"
CHECKPOINT_PATH = HERE / "live_run_checkpoint_v0_1.json"
FREEZE_PATH = HERE / "live_replay_v0_2.freeze.json"

# Exact, pre-gold membership.  Oracle and Phase-1 gold artifacts are
# intentionally absent.  Documentation is also absent so it can record the
# resulting digest without creating a documentation/freeze cycle.
FROZEN_SOURCE_FILES = (
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/candidate_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/control_current_snapshot_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_extract.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_io.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_normalize.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_projection.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_registry.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_types.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/intent_shadow_validate.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_arm_candidate.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_arm_current.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluator.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluator_v0_2.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_measurement_protocol_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_protocol.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_protocol_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_request_manifest_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_authorization_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_checkpoint_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_consumption_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_journal_v0_1.jsonl",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_sealed_batch_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_run_sealed_batch_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_runner.py",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.freeze.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/intent_shadow_registry_v0_1.json",
    "internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/reviews/functional_b/live_protocol_preflight_review_cycle2.md",
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
)


def _exact_json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json_bytes(left) == canonical_json_bytes(right)
    except (TypeError, ValueError):
        return False


def _expected_freeze_policy() -> dict[str, Any]:
    return {
        "json_pointer": ALLOWED_POINTER,
        "record_index_zero_based": ALLOWED_RECORD_INDEX,
        "record_identity": dict(ALLOWED_RECORD_IDENTITY),
        "field_required_in_saved_and_replay": True,
        "exact_boolean_type_required": True,
        "both_boolean_directions_allowed": True,
        "all_other_fields_and_types_exact": True,
        "report_saved_and_replay_values": True,
        "reason": ALLOWED_REASON,
    }


def verify_v0_2_freeze(path: Path = FREEZE_PATH) -> dict[str, Any]:
    freeze = strict_json_file(path)
    require_exact_keys(
        freeze,
        {
            "freeze_format", "replay_gate_version", "evaluator_version",
            "created_date", "algorithm", "status", "oracle_opened",
            "evaluation_executed", "counts", "allowed_difference",
            "source_files", "self_files", "lock_payload_sha256",
        },
        set(),
        "$",
    )
    expected_constants = {
        "freeze_format": FREEZE_FORMAT,
        "replay_gate_version": REPLAY_GATE_VERSION,
        "evaluator_version": "metnos.intent-shadow-live-evaluator/0.2",
        "created_date": CREATED_DATE,
        "algorithm": "sha256",
        "status": "pre_gold_gate_only_evaluation_not_run",
        "oracle_opened": False,
        "evaluation_executed": False,
        "counts": {
            "records": 316,
            "canonical_records": 240,
            "typed_control_records": 8,
            "legacy_records": 68,
        },
        "allowed_difference": _expected_freeze_policy(),
    }
    for key, expected in expected_constants.items():
        if not _exact_json_equal(freeze.get(key), expected):
            raise RuntimeError(f"v0.2 freeze constant mismatch:{key}")
    if type(freeze["source_files"]) is not dict or set(freeze["source_files"]) != set(FROZEN_SOURCE_FILES):
        raise RuntimeError("v0.2 freeze source membership mismatch")
    for relative in FROZEN_SOURCE_FILES:
        digest = freeze["source_files"].get(relative)
        if type(digest) is not str or len(digest) != 64:
            raise RuntimeError(f"v0.2 freeze invalid digest:{relative}")
        source = ROOT / relative
        if not source.is_file() or file_sha256(source) != digest:
            raise RuntimeError(f"v0.2 freeze source drift:{relative}")
    expected_self_files = {
        "live_replay_gate_v0_2.py": file_sha256(Path(__file__).resolve()),
        "build_live_replay_v0_2_freeze.py": file_sha256(HERE / "build_live_replay_v0_2_freeze.py"),
        "test_live_replay_v0_2.py": file_sha256(HERE / "test_live_replay_v0_2.py"),
    }
    if not _exact_json_equal(freeze["self_files"], expected_self_files):
        raise RuntimeError("v0.2 freeze self-file drift")
    lock = freeze["lock_payload_sha256"]
    if type(lock) is not str or lock != payload_sha256(freeze, "lock_payload_sha256"):
        raise RuntimeError("v0.2 freeze lock mismatch")
    return freeze


def compare_saved_and_replay(
    saved: Any,
    replay: Any,
    *,
    record_index: int,
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    """Compare one extraction with the one approved, visible exception."""
    if type(saved) is not dict or type(replay) is not dict:
        raise RuntimeError(f"saved/replay extraction type mismatch at {record_index}")
    if _exact_json_equal(saved, replay):
        return None
    saved_metadata = saved.get("adapter_metadata")
    replay_metadata = replay.get("adapter_metadata")
    if type(saved_metadata) is not dict or type(replay_metadata) is not dict:
        raise RuntimeError(f"adapter metadata missing at {record_index}")
    field = "implicit_actions_ignored"
    if field not in saved_metadata or field not in replay_metadata:
        raise RuntimeError(f"approved diagnostic field missing at {record_index}")
    saved_value = saved_metadata[field]
    replay_value = replay_metadata[field]
    if type(saved_value) is not bool or type(replay_value) is not bool:
        raise RuntimeError(f"approved diagnostic field is not boolean at {record_index}")

    normalized_saved = deepcopy(saved)
    normalized_replay = deepcopy(replay)
    normalized_saved["adapter_metadata"][field] = False
    normalized_replay["adapter_metadata"][field] = False
    if not _exact_json_equal(normalized_saved, normalized_replay):
        raise RuntimeError(f"unexpected saved/replay difference at {record_index}")
    if saved_value == replay_value:
        return None
    if record_index != ALLOWED_RECORD_INDEX or not _exact_json_equal(identity, ALLOWED_RECORD_IDENTITY):
        raise RuntimeError(f"diagnostic mismatch at unauthorized record index {record_index}")
    return {
        "record_index_zero_based": record_index,
        **dict(ALLOWED_RECORD_IDENTITY),
        "json_pointer": ALLOWED_POINTER,
        "saved": saved_value,
        "replay": replay_value,
        "reason": ALLOWED_REASON,
    }


def _validate_historical_protocol_freeze() -> dict[str, Any]:
    freeze = strict_json_file(PROTOCOL_FREEZE_PATH)
    if (
        type(freeze) is not dict
        or freeze.get("freeze_format") != "metnos.intent-shadow-live-protocol-freeze/0.1"
        or freeze.get("algorithm") != "sha256"
        or freeze.get("lock_payload_sha256") != payload_sha256(freeze, "lock_payload_sha256")
    ):
        raise RuntimeError("historical protocol freeze invalid")
    protocol = load_protocol(PROTOCOL_PATH)
    manifest = load_request_manifest(REQUEST_MANIFEST_PATH)
    if freeze.get("protocol_payload_sha256") != protocol["protocol_payload_sha256"]:
        raise RuntimeError("historical protocol freeze protocol binding mismatch")
    if freeze.get("manifest_payload_sha256") != manifest["manifest_payload_sha256"]:
        raise RuntimeError("historical protocol freeze manifest binding mismatch")
    return freeze


def _validate_closed_measurement() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    protocol_freeze = _validate_historical_protocol_freeze()
    manifest = load_request_manifest(REQUEST_MANIFEST_PATH)
    batch = strict_json_file(BATCH_PATH)
    seal = strict_json_file(SEAL_PATH)
    marker = strict_json_file(MARKER_PATH)
    checkpoint = strict_json_file(CHECKPOINT_PATH)

    from live_runner import RUNNER_VERSION, _content_from_http, validate_authorization

    validate_authorization(AUTHORIZATION_PATH)
    authorization_sha256 = file_sha256(AUTHORIZATION_PATH)
    batch_keys = {
        "batch_format", "runner_version", "state", "gold_opened",
        "authorization_sha256", "protocol_file_sha256",
        "protocol_freeze_file_sha256", "manifest_file_sha256",
        "manifest_payload_sha256", "record_count", "accepted_http_posts",
        "stopped_reason", "records",
    }
    require_exact_keys(batch, batch_keys, set(), "$.batch")
    expected_batch_header = {
        "batch_format": SEALED_BATCH_FORMAT,
        "runner_version": RUNNER_VERSION,
        "state": "complete",
        "gold_opened": False,
        "authorization_sha256": authorization_sha256,
        "protocol_file_sha256": file_sha256(PROTOCOL_PATH),
        "protocol_freeze_file_sha256": file_sha256(PROTOCOL_FREEZE_PATH),
        "manifest_file_sha256": file_sha256(REQUEST_MANIFEST_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": 316,
        "accepted_http_posts": 316,
        "stopped_reason": None,
    }
    for key, expected in expected_batch_header.items():
        if not _exact_json_equal(batch.get(key), expected):
            raise RuntimeError(f"sealed batch header mismatch:{key}")
    if type(batch["records"]) is not list or len(batch["records"]) != 316:
        raise RuntimeError("sealed batch record count mismatch")

    require_exact_keys(
        seal,
        {
            "freeze_format", "algorithm", "batch_sha256", "journal_sha256",
            "consumption_marker_sha256", "authorization_sha256",
            "record_count", "accepted_http_posts",
        },
        set(),
        "$.seal",
    )
    expected_seal = {
        "freeze_format": "metnos.intent-shadow-live-sealed-batch-freeze/0.1",
        "algorithm": "sha256",
        "batch_sha256": file_sha256(BATCH_PATH),
        "journal_sha256": file_sha256(JOURNAL_PATH),
        "consumption_marker_sha256": file_sha256(MARKER_PATH),
        "authorization_sha256": authorization_sha256,
        "record_count": 316,
        "accepted_http_posts": 316,
    }
    if not _exact_json_equal(seal, expected_seal):
        raise RuntimeError("sealed batch freeze binding mismatch")

    expected_marker = {
        "runner_version": RUNNER_VERSION,
        "state": "measurement_consumed_first_post_accepted",
        "authorization_sha256": authorization_sha256,
        "protocol_freeze_sha256": file_sha256(PROTOCOL_FREEZE_PATH),
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "accepted_http_posts": 316,
        "first_request_ordinal": 1,
    }
    if not _exact_json_equal(marker, expected_marker):
        raise RuntimeError("consumption marker mismatch")
    expected_checkpoint = {
        "runner_version": RUNNER_VERSION,
        "state": "in_progress",
        "authorization_sha256": authorization_sha256,
        "manifest_payload_sha256": manifest["manifest_payload_sha256"],
        "record_count": 316,
        "accepted_http_posts": 316,
        "last_request_ordinal": 316,
        "journal_sha256": file_sha256(JOURNAL_PATH),
    }
    if not _exact_json_equal(checkpoint, expected_checkpoint):
        raise RuntimeError("final checkpoint mismatch")

    journal_lines = JOURNAL_PATH.read_bytes().splitlines()
    if len(journal_lines) != 316:
        raise RuntimeError("journal line count mismatch")
    mismatches: list[dict[str, Any]] = []
    record_keys = {
        "request_ordinal", "sample_index", "panel", "panel_ordinal",
        "opaque_case_id", "query_sha256", "arm", "request_sha256",
        "http_accepted", "http_status", "elapsed_ms", "transport_error",
        "raw_http_response", "model_content_b64", "model_content_sha256",
        "extraction",
    }
    for index, (record, public, journal_raw) in enumerate(
        zip(batch["records"], manifest["records"], journal_lines, strict=True)
    ):
        require_exact_keys(record, record_keys, set(), f"$.records[{index}]")
        journal_record = strict_json_loads(journal_raw)
        if not _exact_json_equal(record, journal_record):
            raise RuntimeError(f"journal record mismatch at {index}")
        exact_bindings = {
            "request_ordinal": public["request_ordinal"],
            "sample_index": public["sample_index"],
            "panel": public["panel"],
            "panel_ordinal": public["panel_ordinal"],
            "opaque_case_id": public["opaque_case_id"],
            "query_sha256": public["query_sha256"],
            "arm": public["arm"],
            "request_sha256": public["request_sha256"],
            "http_accepted": True,
            "http_status": 200,
            "transport_error": None,
        }
        for key, expected in exact_bindings.items():
            if not _exact_json_equal(record.get(key), expected):
                raise RuntimeError(f"record binding mismatch at {index}:{key}")
        if type(record["elapsed_ms"]) is not int or record["elapsed_ms"] < 0:
            raise RuntimeError(f"record elapsed type/value mismatch at {index}")
        raw = record["raw_http_response"]
        require_exact_keys(raw, {"body_b64", "body_sha256", "headers"}, set(), f"$.records[{index}].raw")
        if type(raw["body_b64"]) is not str or type(raw["body_sha256"]) is not str:
            raise RuntimeError(f"raw response type mismatch at {index}")
        if type(raw["headers"]) is not list or any(
            type(pair) is not list
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            for pair in raw["headers"]
        ):
            raise RuntimeError(f"raw response headers mismatch at {index}")
        try:
            raw_bytes = base64.b64decode(raw["body_b64"], validate=True)
        except Exception as exc:
            raise RuntimeError(f"raw response base64 mismatch at {index}") from exc
        if sha256(raw_bytes).hexdigest() != raw["body_sha256"]:
            raise RuntimeError(f"raw response hash mismatch at {index}")
        content, wrapper_error = _content_from_http(raw_bytes)
        if content is None or wrapper_error is not None:
            raise RuntimeError(f"raw response content reconstruction failed at {index}")
        if type(record["model_content_b64"]) is not str or type(record["model_content_sha256"]) is not str:
            raise RuntimeError(f"saved model content type mismatch at {index}")
        try:
            saved_content = base64.b64decode(record["model_content_b64"], validate=True)
        except Exception as exc:
            raise RuntimeError(f"saved model content base64 mismatch at {index}") from exc
        if saved_content != content or sha256(content).hexdigest() != record["model_content_sha256"]:
            raise RuntimeError(f"saved model content/raw mismatch at {index}")
        if type(record["extraction"]) is not dict:
            raise RuntimeError(f"saved extraction type mismatch at {index}")
        if record["arm"] == "A":
            from live_arm_current import extract_response

            replay = extract_response(content, query=public["query"], language=public["language"])
        else:
            from live_arm_candidate import extract_response

            replay = extract_response(content)
        identity = {key: record[key] for key in ALLOWED_RECORD_IDENTITY}
        mismatch = compare_saved_and_replay(
            record["extraction"], replay, record_index=index, identity=identity
        )
        if mismatch is not None:
            mismatches.append(mismatch)
    if len(mismatches) > 1:
        raise RuntimeError("more than one approved diagnostic mismatch")
    if protocol_freeze.get("status") != "prepared_not_authorized_no_inference":
        raise RuntimeError("historical protocol freeze status mismatch")
    return batch, manifest, mismatches


def validate_pre_gold(
    batch_path: Path = BATCH_PATH,
    seal_path: Path = SEAL_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return validated batch/manifest and a gold-free report."""
    if batch_path.resolve() != BATCH_PATH.resolve() or seal_path.resolve() != SEAL_PATH.resolve():
        raise RuntimeError("v0.2 gate accepts only the pinned completed measurement paths")
    verify_v0_2_freeze(freeze_path)
    batch, manifest, mismatches = _validate_closed_measurement()
    report = {
        "replay_gate_version": REPLAY_GATE_VERSION,
        "status": "pass",
        "oracle_opened": False,
        "evaluation_executed": False,
        "records_verified": 316,
        "raw_http_responses_verified": 316,
        "journal_records_verified": 316,
        "saved_extractions_replayed": 316,
        "allowed_mismatch_count": len(mismatches),
        "unexpected_mismatch_count": 0,
        "allowed_mismatches": mismatches,
        "panels": {
            "canonical_120": {"queries": 120, "records": 240, "separate": True},
            "typed_controls_4": {"queries": 4, "records": 8, "separate": True},
            "legacy_phase1_34": {"queries": 34, "records": 68, "separate": True},
        },
        "integrity": {
            "v0_2_freeze": True,
            "historical_protocol_freeze": True,
            "sealed_batch": True,
            "sealed_batch_freeze": True,
            "journal": True,
            "consumption_marker": True,
            "checkpoint": True,
            "raw_http_bytes": True,
        },
        "batch_sha256": file_sha256(BATCH_PATH),
        "seal_sha256": file_sha256(SEAL_PATH),
        "journal_sha256": file_sha256(JOURNAL_PATH),
        "v0_2_freeze_sha256": file_sha256(freeze_path),
    }
    return batch, manifest, report


def run_pre_gold_gate(
    batch_path: Path = BATCH_PATH,
    seal_path: Path = SEAL_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict[str, Any]:
    return validate_pre_gold(batch_path, seal_path, freeze_path)[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, default=BATCH_PATH)
    parser.add_argument("--seal", type=Path, default=SEAL_PATH)
    parser.add_argument("--freeze", type=Path, default=FREEZE_PATH)
    args = parser.parse_args()
    try:
        report = run_pre_gold_gate(args.batch, args.seal, args.freeze)
    except Exception as exc:
        report = {
            "replay_gate_version": REPLAY_GATE_VERSION,
            "status": "fail",
            "oracle_opened": False,
            "evaluation_executed": False,
            "error": f"{type(exc).__name__}:{exc}",
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
