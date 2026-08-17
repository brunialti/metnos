"""Closed, gold-free primitives for candidate-v0.2 live RUN 1.

Importing this module performs no network, GPU, runtime import, or write.  The
live runner reads only the materialized query-only artifacts in this directory.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[6]
LAB_ROOT = HERE.parents[1]
CANDIDATE_DIR = LAB_ROOT / "candidate_v0_2"
LEGACY_DIR = LAB_ROOT / "candidate_v0_1"

RUN_ID = "intent-shadow-v0.2-run1"
CREATED_DATE = "2026-08-13"
PROTOCOL_FORMAT = "metnos.intent-ir-live-protocol/0.2-run1"
SNAPSHOT_FORMAT = "metnos.intent-current-control-snapshot/0.2-run1"
QUERY_PANEL_FORMAT = "metnos.intent-live-query-panel/0.2-run1"
MANIFEST_FORMAT = "metnos.intent-live-request-manifest/0.2-run1"
FREEZE_FORMAT = "metnos.intent-live-protocol-freeze/0.2-run1"
AUTHORIZATION_FORMAT = "metnos.intent-live-authorization/0.2-run1"
RUNNER_VERSION = "metnos.intent-live-runner/0.2-run1"
SEALED_BATCH_FORMAT = "metnos.intent-live-sealed-batch/0.2-run1"

CONTROL_SNAPSHOT_PATH = HERE / "control_snapshot_run1.json"
QUERY_PANEL_PATH = HERE / "query_panel_run1.json"
PROTOCOL_PATH = HERE / "protocol_run1.json"
MANIFEST_PATH = HERE / "request_manifest_run1.json"
FREEZE_PATH = HERE / "protocol_run1.freeze.json"
AUTHORIZATION_PATH = HERE / "authorization_run1.json"

CANDIDATE_FREEZE_PATH = CANDIDATE_DIR / "candidate_v0_2.freeze.json"
CANDIDATE_PROJECTION_PATH = CANDIDATE_DIR / "intent_ir_registry_projection_v0_2.json"
CANDIDATE_PROMPT_PATH = CANDIDATE_DIR / "intent_ir_v0_2.prompt.txt"
CANDIDATE_SCHEMA_PATH = CANDIDATE_DIR / "intent_ir_v0_2.schema.json"
CONTROL_REGISTRY_PATH = LAB_ROOT / "intent_shadow_registry_v0_1.json"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CONTROL_REPO_SOURCES = frozenset({
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
})

BACKEND_ABSOLUTE_SOURCES = frozenset({
    "/etc/systemd/system/llama-server.service",
    "/etc/systemd/system/llama-server.service.d/override.conf",
    "/home/roberto/.config/metnos/llm_tiers.toml",
    "/home/roberto/llama.cpp-0712/build/bin/llama-server",
    "/home/roberto/llamacpp.env",
    "/home/roberto/models/qwen36-35b-mtp/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
    "/opt/suprastructure/config/models.yaml",
    "/opt/suprastructure/models/manifest.yaml",
})

BUILD_SOURCE_FILES = frozenset({
    str((HERE.parent / "__init__.py").relative_to(ROOT)),
    str((LEGACY_DIR / "intent_shadow_query_suite_v0_1.json").relative_to(ROOT)),
    str((LEGACY_DIR / "legacy_panel_v0_1.json").relative_to(ROOT)),
    str(CANDIDATE_FREEZE_PATH.relative_to(ROOT)),
    str(CANDIDATE_PROJECTION_PATH.relative_to(ROOT)),
    str(CANDIDATE_PROMPT_PATH.relative_to(ROOT)),
    str(CANDIDATE_SCHEMA_PATH.relative_to(ROOT)),
    str((CANDIDATE_DIR / "compiler.py").relative_to(ROOT)),
    str((CANDIDATE_DIR / "validator.py").relative_to(ROOT)),
    str((CANDIDATE_DIR / "structured_client.py").relative_to(ROOT)),
    str(CONTROL_REGISTRY_PATH.relative_to(ROOT)),
    str((LAB_ROOT / "intent_shadow_registry_v0_1.freeze.json").relative_to(ROOT)),
}) | CONTROL_REPO_SOURCES

IMMUTABLE_LOCAL_FILES = frozenset({
    "README.md", "__init__.py", "arm_a.py", "arm_b.py", "build_artifacts.py",
    "control_snapshot_run1.json", "evaluator.py", "protocol.py",
    "protocol_run1.json", "query_panel_run1.json", "request_manifest_run1.json",
    "runner.py", "self_review.md", "tests/__init__.py", "tests/test_mutations.py",
    "tests/test_run1.py", "verify.py",
})

RUN_ARTIFACT_NAMES = frozenset({
    "consumption_run1.json", "journal_run1.jsonl", "checkpoint_run1.json",
    "partial_batch_run1.json", "sealed_batch_run1.json", "seal_run1.json",
    "evaluation_run1.json",
})

CRITICAL_COLUMNS = (
    "semantic_exact_canonical", "root_exact", "correct_abstention",
    "technical_valid", "false_action_avoided", "undo_exact", "consent_exact",
    "negation_exact", "branch_ownership_exact",
)


@dataclass(frozen=True, slots=True)
class TechnicalLimits:
    max_bytes: int = 262_144
    max_depth: int = 64
    max_nodes: int = 10_000
    max_string_chars: int = 65_536
    max_integer_digits: int = 64


LIVE_LIMITS = TechnicalLimits()
HTTP_LIMITS = TechnicalLimits(1_048_576, 64, 20_000, 524_288, 64)


class ProtocolError(ValueError):
    pass


class _NonFinite:
    def __init__(self, token: str) -> None:
        self.token = token


class _OversizedInteger:
    def __init__(self, digits: int) -> None:
        self.digits = digits


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"D01_DUPLICATE_KEY:{key}")
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str, limits: TechnicalLimits = LIVE_LIMITS) -> Any:
    if type(raw) is bytes:
        if len(raw) > limits.max_bytes:
            raise ProtocolError("JSON_BYTE_LIMIT")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("JSON_UTF8") from exc
    elif type(raw) is str:
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ProtocolError("JSON_UNICODE") from exc
        if len(encoded) > limits.max_bytes:
            raise ProtocolError("JSON_BYTE_LIMIT")
        text = raw
    else:
        raise TypeError("raw JSON must be exact bytes or string")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=lambda token: _NonFinite(token),
            parse_float=lambda token: (
                float(token) if math.isfinite(float(token)) else _NonFinite(token)
            ),
            parse_int=lambda token: (
                int(token)
                if len(token) - int(token.startswith("-")) <= limits.max_integer_digits
                else _OversizedInteger(len(token) - int(token.startswith("-")))
            ),
        )
    except ProtocolError:
        raise
    except (json.JSONDecodeError, RecursionError, OverflowError, ValueError) as exc:
        raise ProtocolError("JSON_SYNTAX_OR_OVERFLOW") from exc
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise ProtocolError("JSON_NODE_LIMIT")
        if depth > limits.max_depth:
            raise ProtocolError("JSON_DEPTH_LIMIT")
        if isinstance(current, _NonFinite):
            raise ProtocolError("D02_NONFINITE")
        if isinstance(current, _OversizedInteger):
            raise ProtocolError("D02_INTEGER_OVERFLOW")
        if current is None or type(current) in (bool, int):
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ProtocolError("D02_NONFINITE")
            continue
        if type(current) is str:
            if len(current) > limits.max_string_chars:
                raise ProtocolError("JSON_STRING_LIMIT")
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ProtocolError("JSON_UNICODE") from exc
            continue
        if type(current) is list:
            stack.extend((item, depth + 1) for item in reversed(current))
            continue
        if type(current) is dict:
            if any(type(key) is not str for key in current):
                raise ProtocolError("D03_KEY_TYPE")
            stack.extend((item, depth + 1) for item in reversed(tuple(current.values())))
            continue
        raise ProtocolError("D03_JSON_TYPE")
    return value


def strict_json_file(path: Path, limits: TechnicalLimits = HTTP_LIMITS) -> Any:
    return strict_json_loads(path.read_bytes(), limits)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=256)
def _cached_file_sha256(path_text: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    del size, mtime_ns, ctime_ns
    return file_sha256(Path(path_text))


def stable_file_sha256(path: Path) -> str:
    before = path.stat()
    key = (str(path), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    result = _cached_file_sha256(*key)
    after = path.stat()
    if key != (str(path), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ProtocolError(f"AUTHORITY_CHANGED:{path}")
    return result


def payload_sha256(value: dict[str, Any], field: str) -> str:
    payload = deepcopy(value)
    payload.pop(field, None)
    return sha256(canonical_json_bytes(payload)).hexdigest()


def exact_json_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return set(left) == set(right) and all(
            exact_json_equal(left[key], item) for key, item in right.items()
        )
    if type(right) is list:
        return len(left) == len(right) and all(
            exact_json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def require_closed(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ProtocolError(f"D03_CLOSED_OBJECT:{path}")
    return value


def require_sha(value: Any, path: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise ProtocolError(f"SHA256:{path}")
    return value


def detection_lexicon_identity(path: Path = Path("/home/roberto/.local/share/metnos/detection.sqlite")) -> tuple[str, int]:
    columns = (
        "concept", "lang", "kind", "match_mode", "payload",
        "needs_translation", "source_lang", "version_hash", "source_text_hash",
    )
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                "SELECT " + ",".join(columns) + " FROM detection_lexicon ORDER BY concept,lang"
            )
        ]
    finally:
        connection.close()
    return sha256(canonical_json_bytes(rows)).hexdigest(), len(rows)


def backend_constants() -> dict[str, Any]:
    return {
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


def generation_profile() -> dict[str, Any]:
    return {
        "temperature": 0, "seed": 42, "max_output_tokens": 4000,
        "timeout_seconds": 120, "thinking": False, "retry_count": 0,
        "same_for_both_arms": True,
    }


def apply_generation_profile(request: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(request)
    result.update({
        "model": "local", "temperature": 0, "seed": 42, "max_tokens": 4000,
        "stream": False, "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    })
    return result


def load_control_snapshot(path: Path = CONTROL_SNAPSHOT_PATH) -> dict[str, Any]:
    value = strict_json_file(path)
    require_closed(value, {
        "snapshot_format", "run_id", "created_date", "arm_id", "provenance",
        "current_extractor", "repo_sources", "runtime_state", "backend_identity",
        "snapshot_payload_sha256",
    }, "$")
    if (
        value["snapshot_format"] != SNAPSHOT_FORMAT or value["run_id"] != RUN_ID
        or value["created_date"] != CREATED_DATE or value["arm_id"] != "A"
    ):
        raise ProtocolError("SNAPSHOT_IDENTITY")
    if not exact_json_equal(value["provenance"], {
        "label": "current_metnos_intent_extractor_fresh_run1_snapshot",
        "fresh_control": True,
        "production_read_only": True,
        "historic_candidate_repair": False,
    }):
        raise ProtocolError("SNAPSHOT_PROVENANCE")
    current = value["current_extractor"]
    require_closed(current, {
        "entrypoint", "workload", "tier", "level", "scaffold", "prompt_role",
        "prompt_languages", "response_format", "measurement_max_tokens",
        "single_primary_response", "secondary_probe_disabled",
    }, "current_extractor")
    languages = current["prompt_languages"]
    if (
        type(languages) is not list or any(type(item) is not str for item in languages)
        or len(languages) != len(set(languages)) or set(languages) != {"it", "en"}
        or not exact_json_equal({**current, "prompt_languages": None}, {
            "entrypoint": "runtime.intent_extractor.extract_intent",
            "workload": "intent.extract", "tier": "fast", "level": "micro",
            "scaffold": False, "prompt_role": "intent_extractor_v4",
            "prompt_languages": None, "response_format": "current_control_unchanged_none",
            "measurement_max_tokens": 4000, "single_primary_response": True,
            "secondary_probe_disabled": True,
        })
    ):
        raise ProtocolError("SNAPSHOT_CURRENT_EXTRACTOR")
    if type(value["repo_sources"]) is not dict or set(value["repo_sources"]) != set(CONTROL_REPO_SOURCES):
        raise ProtocolError("SNAPSHOT_SOURCE_SET")
    for relative, digest in value["repo_sources"].items():
        require_sha(digest, f"repo_sources.{relative}")
    runtime_state = value["runtime_state"]
    require_closed(runtime_state, {"path", "mode", "behavior_sha256", "row_count", "language_policy"}, "runtime_state")
    if (
        runtime_state["path"] != "/home/roberto/.local/share/metnos/detection.sqlite"
        or runtime_state["mode"] != "read_only"
        or type(runtime_state["row_count"]) is not int or runtime_state["row_count"] < 1
        or runtime_state["language_policy"] != "canonical_and_typed=it; legacy=case_language; loader fallback unchanged"
    ):
        raise ProtocolError("SNAPSHOT_RUNTIME_STATE")
    require_sha(runtime_state["behavior_sha256"], "runtime_state.behavior_sha256")
    if type(value["backend_identity"]) is not dict:
        raise ProtocolError("SNAPSHOT_BACKEND")
    backend = deepcopy(value["backend_identity"])
    authorities = backend.pop("absolute_sources", None)
    if not exact_json_equal(backend, backend_constants()) or type(authorities) is not dict or set(authorities) != set(BACKEND_ABSOLUTE_SOURCES):
        raise ProtocolError("SNAPSHOT_BACKEND_IDENTITY")
    for absolute, digest in authorities.items():
        require_sha(digest, f"backend_identity.absolute_sources.{absolute}")
    require_sha(value["snapshot_payload_sha256"], "snapshot_payload_sha256")
    if payload_sha256(value, "snapshot_payload_sha256") != value["snapshot_payload_sha256"]:
        raise ProtocolError("SNAPSHOT_PAYLOAD")
    return value


def verify_control_environment(snapshot: dict[str, Any]) -> None:
    for relative, expected in snapshot["repo_sources"].items():
        if stable_file_sha256(ROOT / relative) != expected:
            raise ProtocolError(f"CONTROL_DRIFT:{relative}")
    for absolute, expected in snapshot["backend_identity"]["absolute_sources"].items():
        path = Path(absolute)
        if not path.is_file() or stable_file_sha256(path) != expected:
            raise ProtocolError(f"BACKEND_DRIFT:{absolute}")
    digest, count = detection_lexicon_identity(Path(snapshot["runtime_state"]["path"]))
    if digest != snapshot["runtime_state"]["behavior_sha256"] or count != snapshot["runtime_state"]["row_count"]:
        raise ProtocolError("LEXICON_DRIFT")


def verify_candidate_environment() -> None:
    """Verify every candidate source/artifact through its own closed freeze."""
    from internal.tools.request_analysis_lab.prototypes.intent_shadow_v0_1.candidate_v0_2.build_artifacts import check

    errors = check()
    if type(errors) is not list or errors != []:
        raise ProtocolError(f"CANDIDATE_FREEZE_DRIFT:{errors!r}")


def load_query_panel(path: Path = QUERY_PANEL_PATH) -> dict[str, Any]:
    value = strict_json_file(path, HTTP_LIMITS)
    require_closed(value, {
        "panel_format", "run_id", "created_date", "status", "gold_fields_present",
        "counts", "cases", "panel_payload_sha256",
    }, "$")
    if value["panel_format"] != QUERY_PANEL_FORMAT or value["run_id"] != RUN_ID or value["created_date"] != CREATED_DATE:
        raise ProtocolError("QUERY_PANEL_IDENTITY")
    if value["status"] != "query_only_frozen" or value["gold_fields_present"] is not False:
        raise ProtocolError("QUERY_PANEL_GOLD")
    if not exact_json_equal(value["counts"], {"canonical_120": 120, "typed_controls_4": 4, "legacy_phase1_34": 34, "total": 158}):
        raise ProtocolError("QUERY_PANEL_COUNTS")
    if type(value["cases"]) is not list or len(value["cases"]) != 158:
        raise ProtocolError("QUERY_PANEL_CASES")
    identities: set[str] = set()
    panel_ordinals = {"canonical_120": 0, "typed_controls_4": 0, "legacy_phase1_34": 0}
    expected_panels = ["canonical_120"] * 120 + ["typed_controls_4"] * 4 + ["legacy_phase1_34"] * 34
    for index, (case, panel) in enumerate(zip(value["cases"], expected_panels, strict=True)):
        require_closed(case, {"sample_index", "panel", "panel_ordinal", "opaque_case_id", "language", "query", "query_sha256"}, f"cases[{index}]")
        panel_ordinals[panel] += 1
        if (
            case["sample_index"] != index or type(case["sample_index"]) is not int
            or case["panel"] != panel or type(case["panel_ordinal"]) is not int
            or case["panel_ordinal"] != panel_ordinals[panel]
        ):
            raise ProtocolError("QUERY_PANEL_ORDER")
        if (
            type(case["query"]) is not str or not case["query"]
            or type(case["language"]) is not str or not case["language"]
            or (panel != "legacy_phase1_34" and case["language"] != "it")
            or type(case["query_sha256"]) is not str
            or sha256(case["query"].encode("utf-8")).hexdigest() != case["query_sha256"]
        ):
            raise ProtocolError("QUERY_PANEL_QUERY")
        if type(case["opaque_case_id"]) is not str or case["opaque_case_id"] in identities:
            raise ProtocolError("QUERY_PANEL_ID")
        identities.add(case["opaque_case_id"])
    require_sha(value["panel_payload_sha256"], "panel_payload_sha256")
    if payload_sha256(value, "panel_payload_sha256") != value["panel_payload_sha256"]:
        raise ProtocolError("QUERY_PANEL_PAYLOAD")
    return value


def arm_order(sample_index: int) -> tuple[str, str]:
    if type(sample_index) is not int or not 0 <= sample_index < 158:
        raise TypeError("sample_index")
    return ("A", "B") if sample_index % 2 == 0 else ("B", "A")


def request_for(arm: str, query: str, language: str) -> dict[str, Any]:
    if arm == "A":
        from .arm_a import build_request
    elif arm == "B":
        from .arm_b import build_request
    else:
        raise ProtocolError("ARM")
    return build_request(query, language)


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, Any]:
    value = strict_json_file(path)
    require_closed(value, {
        "protocol_format", "run_id", "created_date", "status", "iteration",
        "authorization", "arms", "backend", "generation_profile", "technical_limits",
        "schedule", "panels", "failure_policy", "evaluation", "bindings",
        "protocol_payload_sha256",
    }, "$")
    if (
        value["protocol_format"] != PROTOCOL_FORMAT or value["run_id"] != RUN_ID
        or value["created_date"] != CREATED_DATE
        or value["status"] != "disarmed_prepared_no_inference"
    ):
        raise ProtocolError("PROTOCOL_IDENTITY")
    if not exact_json_equal(value["iteration"], {"run_number": 1, "maximum_new_runs_authorized": 3, "later_runs_require_result_review_and_new_protocol": True}):
        raise ProtocolError("PROTOCOL_ITERATION")
    if not exact_json_equal(value["authorization"], {"state": "disarmed", "authorization_file": "authorization_run1.json", "authorization_must_be_last": True, "independent_audit_required": True, "inference_executed": False}):
        raise ProtocolError("PROTOCOL_AUTHORIZATION")
    if not exact_json_equal(value["arms"], {
        "A": "fresh current Metnos intent extractor snapshot plus read-only lab adapter",
        "B": "candidate_v0_2 compact IR, strict structured output, deterministic compiler, critic off",
    }):
        raise ProtocolError("PROTOCOL_ARMS")
    snapshot = load_control_snapshot()
    if not exact_json_equal(value["backend"], snapshot["backend_identity"]) or not exact_json_equal(value["generation_profile"], generation_profile()):
        raise ProtocolError("PROTOCOL_BACKEND_PROFILE")
    if not exact_json_equal(value["technical_limits"], {"json_bytes": 262144, "depth": 64, "nodes": 10000, "string_chars": 65536, "integer_digits": 64, "classification": "technical_invalid", "never_unrepresentable": True}):
        raise ProtocolError("PROTOCOL_LIMITS")
    if not exact_json_equal(value["schedule"], {"algorithm": "AB on even sample_index; BA on odd sample_index", "sample_index_range": [0, 157], "query_count": 158, "arm_count": 2, "request_count": 316, "serial": True, "paired_request_optimization": False}):
        raise ProtocolError("PROTOCOL_SCHEDULE")
    if not exact_json_equal(value["panels"], {"canonical_120": {"queries": 120, "both_arms": True, "reported_separately": True}, "typed_controls_4": {"queries": 4, "both_arms": True, "reported_separately": True}, "legacy_phase1_34": {"queries": 34, "both_arms": True, "reported_separately": True, "automatic_conversion": False, "cross_panel_compensation": False}}):
        raise ProtocolError("PROTOCOL_PANELS")
    if not exact_json_equal(value["failure_policy"], {"consumed_at": "first accepted HTTP POST", "marker_written_before_first_socket_attempt": True, "ambiguous_first_transport_attempt_blocks_rerun": True, "retry": False, "transport_or_timeout": "stop_and_seal_partial", "envelope_or_adapter_failure": "stop_and_seal_partial", "document_invalid": "count_error_and_continue", "raw_response_capture": True, "append_only_journal": True, "atomic_checkpoints": True}):
        raise ProtocolError("PROTOCOL_FAILURE_POLICY")
    evaluation = value["evaluation"]
    require_closed(evaluation, {"version", "gold_access", "canonical_projection_symmetric", "critical_columns", "typed_special_gate", "canonical_improvement_gate", "inconclusive_delta", "legacy_report"}, "evaluation")
    columns = evaluation["critical_columns"]
    if (
        type(columns) is not list or any(type(item) is not str for item in columns)
        or len(columns) != len(set(columns)) or set(columns) != set(CRITICAL_COLUMNS)
        or not exact_json_equal({**evaluation, "critical_columns": None}, {
            "version": "v0.3-symmetric", "gold_access": "only_after_complete_316_record_batch_and_seal_validate",
            "canonical_projection_symmetric": True, "critical_columns": None,
            "typed_special_gate": "candidate_B_exact_4_of_4",
            "canonical_improvement_gate": "candidate_B_minus_control_A_at_least_3_exact_of_120",
            "inconclusive_delta": [-2, 2],
            "legacy_report": "separate Phase-1 direct-binding panel; no compensation",
        })
    ):
        raise ProtocolError("PROTOCOL_EVALUATION")
    expected_bindings = {
        "control_snapshot_file_sha256": file_sha256(CONTROL_SNAPSHOT_PATH),
        "query_panel_file_sha256": file_sha256(QUERY_PANEL_PATH),
        "candidate_freeze_file_sha256": file_sha256(CANDIDATE_FREEZE_PATH),
        "candidate_projection_file_sha256": file_sha256(CANDIDATE_PROJECTION_PATH),
        "candidate_prompt_file_sha256": file_sha256(CANDIDATE_PROMPT_PATH),
        "candidate_schema_file_sha256": file_sha256(CANDIDATE_SCHEMA_PATH),
        "control_registry_file_sha256": file_sha256(CONTROL_REGISTRY_PATH),
    }
    if not exact_json_equal(value["bindings"], expected_bindings):
        raise ProtocolError("PROTOCOL_BINDINGS")
    require_sha(value["protocol_payload_sha256"], "protocol_payload_sha256")
    if payload_sha256(value, "protocol_payload_sha256") != value["protocol_payload_sha256"]:
        raise ProtocolError("PROTOCOL_PAYLOAD")
    return value


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    value = strict_json_file(path, TechnicalLimits(8 * 1024 * 1024, 64, 100_000, 128 * 1024, 64))
    require_closed(value, {"manifest_format", "run_id", "created_date", "status", "gold_fields_present", "protocol_payload_sha256", "counts", "records", "manifest_payload_sha256"}, "$")
    protocol = load_protocol()
    panel = load_query_panel()
    if (
        value["manifest_format"] != MANIFEST_FORMAT or value["run_id"] != RUN_ID
        or value["created_date"] != CREATED_DATE or value["status"] != "query_only_frozen"
        or value["gold_fields_present"] is not False
    ):
        raise ProtocolError("MANIFEST_IDENTITY")
    if value["protocol_payload_sha256"] != protocol["protocol_payload_sha256"] or not exact_json_equal(value["counts"], {"queries": 158, "arms": 2, "requests": 316}):
        raise ProtocolError("MANIFEST_BINDING")
    if type(value["records"]) is not list or len(value["records"]) != 316:
        raise ProtocolError("MANIFEST_COUNT")
    identities: set[tuple[int, str]] = set()
    for offset, record in enumerate(value["records"]):
        require_closed(record, {"request_ordinal", "sample_index", "within_case_arm_ordinal", "panel", "panel_ordinal", "opaque_case_id", "language", "query", "query_sha256", "arm", "request_sha256"}, f"records[{offset}]")
        case = panel["cases"][offset // 2]
        arm = arm_order(case["sample_index"])[offset % 2]
        expected = {**case, "request_ordinal": offset + 1, "within_case_arm_ordinal": offset % 2 + 1, "arm": arm}
        if any(type(record.get(key)) is not type(item) or record.get(key) != item for key, item in expected.items()):
            raise ProtocolError(f"MANIFEST_RECORD:{offset}")
        request = request_for(arm, case["query"], case["language"])
        require_sha(record["request_sha256"], f"records[{offset}].request_sha256")
        if sha256(canonical_json_bytes(request)).hexdigest() != record["request_sha256"]:
            raise ProtocolError(f"MANIFEST_REQUEST_HASH:{offset}")
        identity = (case["sample_index"], arm)
        if identity in identities:
            raise ProtocolError("MANIFEST_DUPLICATE")
        identities.add(identity)
    require_sha(value["manifest_payload_sha256"], "manifest_payload_sha256")
    if payload_sha256(value, "manifest_payload_sha256") != value["manifest_payload_sha256"]:
        raise ProtocolError("MANIFEST_PAYLOAD")
    return value
