#!/usr/bin/env python3
"""Offline, post-batch Phase-1 evaluator for a V26.5.6.1 model artifact.

The batch is read once through a bounded no-follow descriptor.  Checkpoint,
batch structure, exact counters, query absence, and (for native runs) the
external gate hash are verified before any oracle/evaluator artifact is read.
Evaluation is written to a distinct ``*_evaluation.json`` path exclusively;
the input batch is never opened for writing.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import time
import types
from typing import Any


VERSION = "metnos.v26.5.6.1-offline-phase1-evaluator/1.0"
HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
SELF_PATH = HERE / "metnos_v26561_offline_evaluator.py"
RUNNER_PATH = HERE / "metnos_v26561_k1_runner.py"
FREEZE_PATH = HERE / "metnos_v26561_author.freeze.json"
AUTHOR_GATE_PATH = HERE / "metnos_v26561_author_pre_gate.json"
EXTERNAL_GATE_PATH = HERE / "metnos_v26561_external_gate.lock.json"
MAX_BATCH_BYTES = 7_609_728
MAX_FIXED_BYTES = 4 * 1024 * 1024
CONFIGURED_CASES = 34


FIXED_ARTIFACT_PATHS = {
    "runtime_controls34": (
        "internal/tools/request_analysis_lab/candidates/v2656/"
        "metnos_v2656_runtime_controls34.json"
    ),
    "source_controls34": "internal/tools/request_analysis_lab/question_focus_controls_v1.json",
    "phase1_fixture": (
        "internal/tools/request_analysis_lab/candidates/v2641/"
        "metnos_v2641_typed_phase1_controls_frozen.json"
    ),
    "phase1_evaluator": (
        "internal/tools/request_analysis_lab/candidates/v2656/"
        "metnos_v2656_phase1_evaluator.py"
    ),
    "typed_registry": (
        "internal/tools/request_analysis_lab/candidates/v2641/"
        "metnos_v2641_typed_registry.json"
    ),
    "injected_validator": (
        "internal/tools/request_analysis_lab/candidates/v2653/"
        "metnos_v2653_injected_validator.py"
    ),
    "python_dependency_tree": (
        "internal/tools/request_analysis_lab/candidates/v2656/"
        "metnos_v2656_python_dependency_tree.json"
    ),
    "oracle_freeze": (
        "internal/tools/request_analysis_lab/oracles/phase1_v1/"
        "metnos_phase1_oracle_audit_v1.freeze.json"
    ),
    "oracle_audit_json": (
        "internal/tools/request_analysis_lab/oracles/phase1_v1/"
        "metnos_phase1_oracle_audit_v1.json"
    ),
    "oracle_audit_md": (
        "internal/tools/request_analysis_lab/oracles/phase1_v1/"
        "metnos_phase1_oracle_audit_v1.md"
    ),
    "oracle_overlay": (
        "internal/tools/request_analysis_lab/oracles/phase1_v1/"
        "metnos_phase1_typed_oracle_v1.overlay.json"
    ),
    "oracle_schema": (
        "internal/tools/request_analysis_lab/oracles/phase1_v1/"
        "metnos_phase1_typed_oracle_v1.schema.json"
    ),
}
GOLD_IDENTITIES = (
    "source_controls34", "phase1_fixture", "phase1_evaluator",
    "oracle_freeze", "oracle_audit_json", "oracle_audit_md",
    "oracle_overlay", "oracle_schema",
)


class _ClosedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise RuntimeError("invalid command-line contract")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json(value: bytes | str, label: str) -> Any:
    def closed_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise RuntimeError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    def reject_constant(token):
        raise RuntimeError(f"non-finite JSON constant in {label}: {token}")

    try:
        return json.loads(
            value, object_pairs_hook=closed_object, parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise RuntimeError(f"invalid strict JSON: {label}") from error


def _read_descriptor_snapshot(descriptor: int, maximum_size: int, label: str) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= maximum_size:
        raise RuntimeError(f"{label} is not a bounded regular file")
    remaining = before.st_size
    chunks = []
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    value = b"".join(chunks)
    extra = os.read(descriptor, 1)
    after = os.fstat(descriptor)
    before_identity = (
        before.st_dev, before.st_ino, before.st_mode, before.st_size,
        before.st_mtime_ns, before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev, after.st_ino, after.st_mode, after.st_size,
        after.st_mtime_ns, after.st_ctime_ns,
    )
    if remaining or extra or len(value) != before.st_size or before_identity != after_identity:
        raise RuntimeError(f"{label} changed during its single read")
    return value


def _open_directory_chain(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("path is not canonical absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    current = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            following = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def _read_absolute_nofollow(path: Path, maximum_size: int, label: str) -> bytes:
    if not path.is_absolute() or not path.name or path.name in {".", ".."}:
        raise RuntimeError(f"{label} path is not canonical absolute")
    directory = _open_directory_chain(path.parent)
    descriptor = None
    try:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        return _read_descriptor_snapshot(descriptor, maximum_size, label)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _read_fixed(path: Path, maximum_size: int, label: str) -> bytes:
    return _read_absolute_nofollow(path, maximum_size, label)


def _fixed_artifact_bytes(
    identity: str, pins: dict[str, Any], *, gold_allowed: bool,
) -> bytes:
    if identity not in FIXED_ARTIFACT_PATHS:
        raise RuntimeError("unknown fixed evaluator artifact")
    if identity in GOLD_IDENTITIES and not gold_allowed:
        raise RuntimeError("gold artifact read attempted before batch closure")
    pin = pins.get(identity)
    if (
        type(pin) is not dict or set(pin) != {"path", "sha256", "size"}
        or pin.get("path") != FIXED_ARTIFACT_PATHS[identity]
        or type(pin.get("sha256")) is not str or len(pin["sha256"]) != 64
        or type(pin.get("size")) is not int or not 0 <= pin["size"] <= MAX_FIXED_BYTES
    ):
        raise RuntimeError(f"freeze artifact pin invalid: {identity}")
    value = _read_fixed(REPOSITORY / pin["path"], pin["size"], identity)
    if len(value) != pin["size"] or sha_bytes(value) != pin["sha256"]:
        raise RuntimeError(f"fixed evaluator artifact hash failed: {identity}")
    return value


def _verify_dependency_environment(pins: dict[str, Any]) -> dict[str, Any]:
    manifest = _strict_json(
        _fixed_artifact_bytes("python_dependency_tree", pins, gold_allowed=False),
        "Python dependency inventory",
    )
    if (
        type(manifest) is not dict
        or manifest.get("version") != "metnos.v26.5.6-python-dependency-tree/1.1"
        or type(manifest.get("packages")) is not list
        or type(manifest.get("loaded_nonstdlib_modules")) is not list
    ):
        raise RuntimeError("Python dependency inventory envelope changed")
    if [item.get("name") for item in manifest["packages"]] != ["jsonschema", "regex"]:
        raise RuntimeError("Python dependency identities changed")
    for package in manifest["packages"]:
        files = package.get("files")
        if type(files) is not list or len(files) != package.get("file_count"):
            raise RuntimeError("Python dependency tree count changed")
        canonical = json.dumps(
            files, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if sha_bytes(canonical) != package.get("tree_sha256"):
            raise RuntimeError("Python dependency tree self-hash changed")
        for item in files:
            if (
                type(item) is not dict
                or set(item) != {
                    "path", "relative_path", "role", "sha256", "size",
                }
                or type(item["path"]) is not str
                or type(item["relative_path"]) is not str
                or type(item["role"]) is not str
                or type(item["sha256"]) is not str
                or len(item["sha256"]) != 64
                or type(item["size"]) is not int
                or not 0 <= item["size"] <= MAX_FIXED_BYTES
            ):
                raise RuntimeError("Python dependency file pin shape changed")
            value = _read_absolute_nofollow(
                Path(item["path"]), item["size"], "Python dependency file",
            )
            if len(value) != item["size"] or sha_bytes(value) != item["sha256"]:
                raise RuntimeError("Python dependency file hash changed")
    inventory = manifest["loaded_nonstdlib_modules"]
    probe = manifest.get("inventory_probe", {})
    inventory_hash = sha_bytes(json.dumps(
        inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    if (
        len(inventory) != probe.get("loaded_module_count")
        or inventory_hash != probe.get("loaded_modules_sha256")
    ):
        raise RuntimeError("loaded non-stdlib inventory self-hash changed")
    for item in inventory:
        if (
            type(item) is not dict
            or set(item) != {"module", "origin", "sha256", "size"}
            or type(item["module"]) is not str
            or type(item["origin"]) is not str
            or type(item["sha256"]) is not str
            or len(item["sha256"]) != 64
            or type(item["size"]) is not int
            or not 0 <= item["size"] <= MAX_FIXED_BYTES
        ):
            raise RuntimeError("loaded non-stdlib module pin shape changed")
        value = _read_absolute_nofollow(
            Path(item["origin"]), item["size"], "loaded non-stdlib module",
        )
        if len(value) != item["size"] or sha_bytes(value) != item["sha256"]:
            raise RuntimeError("loaded non-stdlib module hash changed")
    return manifest


def _load_pre_gold_validator(pins: dict[str, Any]) -> tuple[Any, Any]:
    manifest = _verify_dependency_environment(pins)
    regex_package = next(item for item in manifest["packages"] if item["name"] == "regex")
    site_root = str(Path(regex_package["package_root"]).parent)
    if site_root not in sys.path:
        sys.path.append(site_root)
    regex_module = importlib.import_module("regex")
    if (
        Path(regex_module.__file__).resolve() != Path(regex_package["module_origin"])
        or regex_module.__version__ != regex_package["version"]
    ):
        raise RuntimeError("regex import origin/version changed")
    registry_bytes = _fixed_artifact_bytes("typed_registry", pins, gold_allowed=False)
    validator_bytes = _fixed_artifact_bytes(
        "injected_validator", pins, gold_allowed=False,
    )
    validator_path = REPOSITORY / FIXED_ARTIFACT_PATHS["injected_validator"]
    validator = types.ModuleType("metnos_v26561_pre_gold_validator")
    validator.__file__ = str(validator_path)
    validator.__package__ = None
    validator.__dict__["__metnos_registry_bytes__"] = registry_bytes
    exec(
        compile(validator_bytes, str(validator_path), "exec", dont_inherit=True),
        validator.__dict__,
    )
    jsonschema_package = next(
        item for item in manifest["packages"] if item["name"] == "jsonschema"
    )
    loaded_jsonschema = validator.jsonschema
    if (
        Path(loaded_jsonschema.__file__).resolve()
        != Path(jsonschema_package["module_origin"])
        or loaded_jsonschema.__version__ != jsonschema_package["version"]
    ):
        raise RuntimeError("jsonschema import origin/version changed")
    registry_issues = validator.registry_errors()
    if registry_issues:
        raise RuntimeError("pinned registry/validator contract is internally invalid")
    return validator, regex_module


def _unicode_segments(text: str, regex_module: Any) -> list[dict[str, Any]]:
    if type(text) is not str:
        raise RuntimeError("runtime query is not an exact string")
    parts = regex_module.split(
        r"\b", text, flags=regex_module.WORD | regex_module.VERSION1,
    )
    result = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise RuntimeError("pre-gold segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        result.append({
            "id": len(result) + 1, "text": part,
            "start_char": start, "end_char": end,
        })
    return result


def _canonical_hash(value: Any) -> str:
    return sha_bytes(json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


def _checkpoint_before_gold(batch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    runner_bytes = _read_fixed(RUNNER_PATH, MAX_FIXED_BYTES, "runner")
    freeze_bytes = _read_fixed(FREEZE_PATH, MAX_FIXED_BYTES, "freeze")
    author_gate_bytes = _read_fixed(AUTHOR_GATE_PATH, MAX_FIXED_BYTES, "author gate")
    self_bytes = _read_fixed(SELF_PATH, MAX_FIXED_BYTES, "offline evaluator")
    freeze = _strict_json(freeze_bytes, "freeze")
    author_gate = _strict_json(author_gate_bytes, "author gate")
    if type(freeze) is not dict or type(author_gate) is not dict:
        raise RuntimeError("checkpoint roots are not objects")
    observed = {
        "runner_sha256": sha_bytes(runner_bytes),
        "freeze_sha256": sha_bytes(freeze_bytes),
        "author_pre_gate_sha256": sha_bytes(author_gate_bytes),
    }
    if any(batch.get(key) != value for key, value in observed.items()):
        raise RuntimeError("batch/checkpoint hash mismatch before gold")
    if (
        freeze.get("runner_sha256") != observed["runner_sha256"]
        or freeze.get("author_pre_gate_sha256") != observed["author_pre_gate_sha256"]
        or freeze.get("offline_evaluator_sha256") != sha_bytes(self_bytes)
        or freeze.get("author_inference_allowed") is not False
        or freeze.get("external_gate_required") is not True
        or author_gate.get("inference_allowed") is not False
    ):
        raise RuntimeError("author checkpoint does not pin the offline evaluator/batch")
    pins = freeze.get("artifact_pins")
    if type(pins) is not dict:
        raise RuntimeError("freeze artifact pins missing")
    for identity, expected_path in FIXED_ARTIFACT_PATHS.items():
        pin = pins.get(identity)
        if type(pin) is not dict or pin.get("path") != expected_path:
            raise RuntimeError(f"freeze fixed path mismatch: {identity}")
    native = batch.get("native_run")
    if type(native) is not bool:
        raise RuntimeError("batch native_run is not exact boolean")
    if native:
        external_gate_bytes = _read_fixed(
            EXTERNAL_GATE_PATH, MAX_FIXED_BYTES, "external gate",
        )
        if (
            batch.get("external_gate_sha256") != sha_bytes(external_gate_bytes)
            or batch.get("external_gate_authority")
            != "independent_v26561_infra_review_and_gate_verifier"
        ):
            raise RuntimeError("native batch external gate hash/authority mismatch")
    return freeze, pins


def _walk_query_free(value: Any, raw_queries: tuple[str, ...]) -> None:
    forbidden_keys = {
        "query", "original_request", "segments", "messages",
        "excerpt_redacted", "message_redacted",
    }
    stack = [value]
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > 1_000_000:
            raise RuntimeError("batch structure exceeds query-free audit bound")
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str or key in forbidden_keys:
                    raise RuntimeError("batch contains forbidden raw-query field")
                stack.append(item)
        elif type(current) is list:
            stack.extend(current)
        elif type(current) is str:
            if any(query and query in current for query in raw_queries):
                raise RuntimeError("batch contains raw query material")
        elif current is not None and type(current) not in (bool, int, float):
            raise RuntimeError("batch contains a non-JSON exact value")


def _validate_closed_diagnostic(diagnostic: Any) -> None:
    required = {
        "error", "phase", "socket_attempts", "http_responses",
        "server_accepted_requests", "request_reached_server", "http_status",
        "response_body", "exception_chain", "transport_latency_ms",
        "request_body_bytes", "request_body_sha256",
        "response_json_documents_decoded", "decoded_chat_responses",
        "decoded_frames",
    }
    optional = {"exception_type", "cause_type", "errno", "model_content"}
    if (
        type(diagnostic) is not dict
        or not required <= set(diagnostic)
        or set(diagnostic) - required - optional
        or diagnostic.get("error")
        not in {"", "model_frame_json_error", "facade_model_input_invalid"}
        or diagnostic.get("phase")
        not in {"complete", "model_frame_json", "facade_validation"}
    ):
        raise RuntimeError("batch diagnostic keys/status are not closed")
    response_body = diagnostic["response_body"]
    if type(response_body) is not dict or set(response_body) != {
        "captured_bytes", "captured_sha256", "body_complete", "declared_bytes",
        "body_bytes", "body_sha256",
    }:
        raise RuntimeError("batch response-body diagnostic is not hash-only")
    chain = diagnostic["exception_chain"]
    if type(chain) is not list:
        raise RuntimeError("batch exception chain is not a list")
    for item in chain:
        if type(item) is not dict or not set(item) <= {"module", "type", "errno"}:
            raise RuntimeError("batch exception diagnostic contains text")
        if set(item) not in ({"module", "type"}, {"module", "type", "errno"}):
            raise RuntimeError("batch exception diagnostic shape changed")
    model_content = diagnostic.get("model_content")
    if model_content is not None and (
        type(model_content) is not dict
        or set(model_content) != {"bytes", "sha256"}
    ):
        raise RuntimeError("batch model-content diagnostic is not hash-only")


def _validate_batch_before_gold(
    batch: dict[str, Any], runtime_controls: dict[str, Any],
    validator: Any, regex_module: Any,
) -> list[dict[str, Any]]:
    native = batch.get("native_run")
    base_keys = {
        "version", "artifact_kind", "status", "model_batch_complete",
        "freeze_sha256", "runner_sha256", "author_pre_gate_sha256",
        "summary", "inline_transport_preflight", "records",
        "query_material_included", "native_run", "evaluation_status",
        "posthoc_credit", "accuracy_claimed",
    }
    required = base_keys | (
        {"external_gate_sha256", "external_gate_authority"} if native is True else set()
    )
    if type(batch) is not dict or set(batch) != required:
        raise RuntimeError("complete model batch envelope is not closed")
    if (
        batch.get("version") != "metnos.v26.5.6.1-compact-k1-runner/1.0"
        or batch.get("artifact_kind") != "model_batch"
        or batch.get("status") != "MODEL_BATCH_COMPLETE"
        or batch.get("model_batch_complete") is not True
        or batch.get("query_material_included") is not False
        or batch.get("evaluation_status") != "NOT_RUN"
        or batch.get("posthoc_credit") is not False
        or batch.get("accuracy_claimed") is not False
    ):
        raise RuntimeError("model batch closure/claim fields failed")
    records = batch.get("records")
    controls = runtime_controls.get("cases")
    if type(records) is not list or type(controls) is not list:
        raise RuntimeError("batch/runtime controls records missing")
    if len(records) != CONFIGURED_CASES or len(controls) != CONFIGURED_CASES:
        raise RuntimeError("model batch is not exact K1/34")
    raw_queries = tuple(item.get("query") for item in controls)
    if any(type(item) is not str for item in raw_queries):
        raise RuntimeError("runtime query fixture invalid")
    _walk_query_free(batch, raw_queries)
    expected_counter_keys = {
        "socket_attempts", "http_responses", "server_accepted_requests",
        "response_json_documents_decoded", "decoded_chat_responses",
        "decoded_frames", "facade_evaluations", "evaluated_cases",
        "valid_cases", "invalid_cases", "model_request_attempts",
    }
    totals = {key: 0 for key in expected_counter_keys}
    seen_ids = set()
    for ordinal, (record, control) in enumerate(zip(records, controls), 1):
        if (
            type(record) is not dict
            or set(record) != {"ordinal", "opaque_case_id", "query_sha256_utf8", "result"}
            or record.get("ordinal") != ordinal
            or record.get("opaque_case_id") != control.get("opaque_case_id")
            or record.get("query_sha256_utf8") != control.get("query_sha256_utf8")
            or record["opaque_case_id"] in seen_ids
        ):
            raise RuntimeError("batch record identity/order/hash mismatch")
        seen_ids.add(record["opaque_case_id"])
        result = record.get("result")
        counters = result.get("counters") if type(result) is dict else None
        common_result_keys = {
            "status", "failure_class", "model_request_attempts",
            "segment_count", "segment_layout_sha256", "diagnostic",
            "validation", "counters", "total_latency_ms",
        }
        valid_result_keys = common_result_keys | {
            "raw_frame_sha256", "expanded_frame", "facade_latency_ms",
        }
        content_invalid_result_keys = common_result_keys
        facade_invalid_result_keys = common_result_keys | {
            "raw_frame_sha256", "facade_latency_ms",
        }
        if (
            type(result) is not dict
            or result.get("status") not in {"evaluated_valid", "evaluated_invalid"}
            or type(counters) is not dict or set(counters) != expected_counter_keys
            or counters.get("model_request_attempts") != 1
            or counters.get("socket_attempts") != 1
            or counters.get("http_responses") != 1
            or counters.get("server_accepted_requests") != 1
            or counters.get("response_json_documents_decoded") != 1
            or counters.get("decoded_chat_responses") != 1
            or counters.get("evaluated_cases") != 1
            or counters.get("valid_cases") + counters.get("invalid_cases") != 1
        ):
            raise RuntimeError("batch record status/counters are not closed")
        _validate_closed_diagnostic(result.get("diagnostic"))
        query = control.get("query")
        segments = _unicode_segments(query, regex_module)
        expected_layout_hash = _canonical_hash([
            {key: item[key] for key in ("id", "start_char", "end_char")}
            for item in segments
        ])
        if (
            result.get("model_request_attempts") != 1
            or result.get("segment_count") != len(segments)
            or result.get("segment_layout_sha256") != expected_layout_hash
            or type(result.get("total_latency_ms")) not in (int, float)
            or type(result.get("total_latency_ms")) is bool
            or not math.isfinite(result["total_latency_ms"])
            or result["total_latency_ms"] < 0
        ):
            raise RuntimeError("batch record segment/request/latency proof changed")
        status = result["status"]
        failure_class = result.get("failure_class")
        diagnostic = result["diagnostic"]
        validation = result.get("validation")
        if status == "evaluated_valid":
            if (
                set(result) != valid_result_keys
                or failure_class is not None
                or validation != {
                    "attempted": True, "stage": "accepted", "codes": [],
                }
                or diagnostic.get("error") != ""
                or diagnostic.get("phase") != "complete"
                or counters["decoded_frames"] != 1
                or counters["facade_evaluations"] != 1
                or counters["valid_cases"] != 1
                or counters["invalid_cases"] != 0
                or type(result.get("expanded_frame")) is not dict
                or type(result.get("raw_frame_sha256")) is not str
                or len(result["raw_frame_sha256"]) != 64
                or type(result.get("facade_latency_ms")) not in (int, float)
                or type(result.get("facade_latency_ms")) is bool
                or not math.isfinite(result["facade_latency_ms"])
                or result["facade_latency_ms"] < 0
            ):
                raise RuntimeError("valid batch result shape/proof is not exact")
            semantic_validation = validator.validate_frame(
                result["expanded_frame"], segments,
            )
            if semantic_validation != {"valid": True, "errors": []}:
                raise RuntimeError(
                    "expanded frame failed pinned pre-gold semantic validation"
                )
        elif failure_class == "model_content_json_invalid":
            if (
                set(result) != content_invalid_result_keys
                or validation != {
                    "attempted": False, "stage": "model_json",
                    "codes": ["model_frame_json_error"],
                }
                or diagnostic.get("error") != "model_frame_json_error"
                or diagnostic.get("phase") != "model_frame_json"
                or counters["decoded_frames"] != 0
                or counters["facade_evaluations"] != 0
                or counters["valid_cases"] != 0
                or counters["invalid_cases"] != 1
            ):
                raise RuntimeError("model-JSON invalid result shape is not exact")
        elif failure_class == "model_frame_rejected_before_facade_worker":
            if (
                set(result) != facade_invalid_result_keys
                or type(validation) is not dict
                or set(validation) != {"attempted", "stage", "codes"}
                or validation.get("attempted") is not True
                or validation.get("stage") != "facade_input"
                or type(validation.get("codes")) is not list
                or not validation["codes"]
                or any(type(code) is not str for code in validation["codes"])
                or diagnostic.get("error") != "facade_model_input_invalid"
                or diagnostic.get("phase") != "facade_validation"
                or counters["decoded_frames"] != 1
                or counters["facade_evaluations"] != 1
                or counters["valid_cases"] != 0
                or counters["invalid_cases"] != 1
            ):
                raise RuntimeError("facade-input invalid result shape is not exact")
        elif failure_class == "model_frame_schema_adapter_or_semantic_invalid":
            if (
                set(result) != facade_invalid_result_keys
                or type(validation) is not dict
                or set(validation) != {"attempted", "stage", "codes"}
                or validation.get("attempted") is not True
                or validation.get("stage") not in {"schema", "adapter", "validator"}
                or type(validation.get("codes")) is not list
                or not validation["codes"]
                or any(type(code) is not str for code in validation["codes"])
                or diagnostic.get("error") != ""
                or diagnostic.get("phase") != "complete"
                or counters["decoded_frames"] != 1
                or counters["facade_evaluations"] != 1
                or counters["valid_cases"] != 0
                or counters["invalid_cases"] != 1
            ):
                raise RuntimeError("semantic-invalid result shape is not exact")
        else:
            raise RuntimeError("evaluated result failure class is not closed")
        if "raw_frame_sha256" in result and (
            type(result["raw_frame_sha256"]) is not str
            or len(result["raw_frame_sha256"]) != 64
        ):
            raise RuntimeError("raw frame hash proof is malformed")
        for key in totals:
            if type(counters[key]) is not int or counters[key] < 0:
                raise RuntimeError("batch counter is not an exact nonnegative integer")
            totals[key] += counters[key]
    summary = batch.get("summary")
    expected_summary_keys = {
        "configured_cases", "attempted_cases", "evaluated_cases",
        "model_request_attempts", "server_accepted_requests",
        "decoded_chat_responses", "model_evaluated_results", "retries",
        "inline_preflight_counters", "inference_counters",
        "total_transport_attempts", "latency_min_ms", "latency_median_ms",
        "latency_p95_ms", "latency_max_ms",
    }
    if (
        type(summary) is not dict or set(summary) != expected_summary_keys
        or summary.get("configured_cases") != CONFIGURED_CASES
        or summary.get("attempted_cases") != CONFIGURED_CASES
        or summary.get("evaluated_cases") != CONFIGURED_CASES
        or summary.get("model_request_attempts") != CONFIGURED_CASES
        or summary.get("server_accepted_requests") != CONFIGURED_CASES
        or summary.get("decoded_chat_responses") != CONFIGURED_CASES
        or summary.get("model_evaluated_results") != CONFIGURED_CASES
        or summary.get("retries") != 0
        or summary.get("inference_counters") != totals
    ):
        raise RuntimeError("batch summary/counter reconciliation failed")
    preflight = batch.get("inline_transport_preflight")
    if (
        type(preflight) is not dict or preflight.get("status") != "PASS"
        or preflight.get("method") != "GET" or preflight.get("request_path") != "/v1/models"
        or preflight.get("request_body_bytes") != 0
        or preflight.get("transport_preflight_calls") != 1
        or preflight.get("inference_calls") != 0
    ):
        raise RuntimeError("inline transport preflight proof missing")
    return records


def evaluate_batch_bytes(batch_bytes: bytes, batch_label: str = "<memory>") -> dict[str, Any]:
    if type(batch_bytes) is not bytes or len(batch_bytes) > MAX_BATCH_BYTES:
        raise RuntimeError("batch byte input exceeds offline evaluator boundary")
    batch_sha256 = sha_bytes(batch_bytes)
    batch = _strict_json(batch_bytes, "model batch")
    if type(batch) is not dict:
        raise RuntimeError("model batch root is not an object")

    # This checkpoint/gate step and the query-only control verification precede
    # every gold/evaluator read.
    freeze, pins = _checkpoint_before_gold(batch)
    runtime_bytes = _fixed_artifact_bytes(
        "runtime_controls34", pins, gold_allowed=False,
    )
    runtime_controls = _strict_json(runtime_bytes, "query-only runtime controls")
    validator, regex_module = _load_pre_gold_validator(pins)
    records = _validate_batch_before_gold(
        batch, runtime_controls, validator, regex_module,
    )

    # Gold opens begin only after the exact complete batch has been accepted.
    gold = {
        identity: _fixed_artifact_bytes(identity, pins, gold_allowed=True)
        for identity in GOLD_IDENTITIES
    }
    fixture = _strict_json(gold["phase1_fixture"], "Phase-1 fixture")
    registry = _strict_json(
        _fixed_artifact_bytes("typed_registry", pins, gold_allowed=True),
        "typed registry",
    )
    evaluator_path = REPOSITORY / FIXED_ARTIFACT_PATHS["phase1_evaluator"]
    evaluator = types.ModuleType("metnos_v26561_pinned_phase1_evaluator_offline")
    evaluator.__file__ = str(evaluator_path)
    evaluator.__package__ = None
    exec(
        compile(gold["phase1_evaluator"], str(evaluator_path), "exec", dont_inherit=True),
        evaluator.__dict__,
    )
    metrics = evaluator.evaluate_phase1(records, fixture, registry)
    return {
        "version": VERSION,
        "artifact_kind": "phase1_evaluation",
        "status": "PHASE1_EVALUATED",
        "batch_path": batch_label,
        "batch_sha256": batch_sha256,
        "batch_runner_sha256": batch["runner_sha256"],
        "batch_freeze_sha256": batch["freeze_sha256"],
        "batch_author_pre_gate_sha256": batch["author_pre_gate_sha256"],
        "native_run": batch["native_run"],
        "phase1": metrics,
        "network_calls": 0,
        "model_calls": 0,
        "cutover_authorized": False,
        "scope": "typed-registry Phase-1 K1/34 only",
    }


def write_json_exclusive_atomic(path: Path, payload: dict[str, Any]) -> None:
    if not path.is_absolute() or not path.name.endswith("_evaluation.json"):
        raise RuntimeError("evaluation output must be an absolute *_evaluation.json path")
    data = (json.dumps(
        payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    directory = _open_directory_chain(path.parent)
    temporary_name = f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    descriptor = None
    try:
        descriptor = os.open(
            temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(
            temporary_name, path.name,
            src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False,
        )
        os.fsync(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _main(argv: list[str] | None = None) -> int:
    parser = _ClosedArgumentParser()
    parser.add_argument("--batch", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    batch_path = Path(arguments.batch)
    output_path = Path(arguments.output)
    if not batch_path.is_absolute() or batch_path == output_path:
        raise RuntimeError("batch must be a distinct absolute input path")
    if output_path.exists():
        raise FileExistsError(output_path)
    batch_bytes = _read_absolute_nofollow(batch_path, MAX_BATCH_BYTES, "model batch")
    result = evaluate_batch_bytes(batch_bytes, str(batch_path))
    write_json_exclusive_atomic(output_path, result)
    print(json.dumps({
        "status": result["status"], "batch_sha256": result["batch_sha256"],
        "output": str(output_path),
    }, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception as error:
        cause = error.__cause__ or error.__context__
        payload = {
            "version": VERSION,
            "status": "CLI_ERROR",
            "phase": "offline_evaluator_cli",
            "error": "offline_evaluator_failed",
            "exception_type": type(error).__name__,
            "cause_type": type(cause).__name__ if cause is not None else None,
            "errno": (
                error.errno
                if type(getattr(error, "errno", None)) is int else None
            ),
            "traceback_emitted": False,
        }
        sys.stderr.write(json.dumps(
            payload, ensure_ascii=True, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        ) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
