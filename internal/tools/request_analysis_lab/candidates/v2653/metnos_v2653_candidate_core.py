#!/usr/bin/env python3
"""Pinned, request-bound offline core for the V26.5.3 compact candidate."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import types
import unicodedata
from typing import Any

import jsonschema
import regex


__all__ = ("evaluate",)
VERSION = "metnos.v26.5.3-candidate-core/1.0"
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
RUNTIME_MANIFEST_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2653/"
    "metnos_v2653_runtime_manifest.json"
)
FIXTURE_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2651/"
    "metnos_v2651_compact_mutation_fixture.json"
)
EXPECTED_RUNTIME_MANIFEST_SHA256 = (
    "14c1e2d6a666cef335d5567e550ec55db4af4c0cd46b59f9b8b344cf7e506d50"
)
EXPECTED_RUNTIME_MANIFEST_SIZE = 1426
EXPECTED_FIXTURE_SHA256 = (
    "8ce526f2384c4e1a9e1103c97ee3eb0af3f4bff1302cae621d61f5fe792d023e"
)
EXPECTED_FIXTURE_SIZE = 242411
EXPECTED_DISTRIBUTIONS = {"jsonschema": "4.10.3", "regex": "2026.3.32"}
EXPECTED_UNICODE_VERSION = "15.0.0"
EXPECTED_ARTIFACTS = {
    "adapter": (
        "python_source",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact_adapter.py",
    ),
    "validator": (
        "python_source",
        "internal/tools/request_analysis_lab/candidates/v2653/"
        "metnos_v2653_injected_validator.py",
    ),
    "registry": (
        "json_data",
        "internal/tools/request_analysis_lab/candidates/v2641/"
        "metnos_v2641_typed_registry.json",
    ),
    "schema": (
        "json_data",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact.schema.json",
    ),
    "prompt": (
        "utf8_data",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact.prompt.txt",
    ),
}
ALLOWED_READ_PATHS = frozenset(
    {RUNTIME_MANIFEST_RELATIVE, FIXTURE_RELATIVE}
    | {metadata[1] for metadata in EXPECTED_ARTIFACTS.values()}
)
ALLOWED_SOURCE_IMPORTS = {
    "adapter": frozenset({"__future__", "copy", "typing"}),
    "validator": frozenset(
        {"__future__", "hashlib", "json", "jsonschema", "typing"}
    ),
}
DENIED_AUDIT_PREFIXES = (
    "socket.", "http.client", "urllib.", "subprocess.", "ctypes.",
)
DENIED_AUDIT_EVENTS = frozenset(
    {"os.system", "os.posix_spawn", "os.spawn", "os.spawnve"}
)


def _deny_external_effects(event: str, _args: tuple[Any, ...]) -> None:
    if event in DENIED_AUDIT_EVENTS or event.startswith(DENIED_AUDIT_PREFIXES):
        raise RuntimeError(f"external effect denied by candidate core: {event}")


sys.addaudithook(_deny_external_effects)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inside_repository(path: Path) -> bool:
    try:
        path.relative_to(REPOSITORY)
        return True
    except ValueError:
        return False


def _read_repository_relative(relative: str, expected_size: int) -> bytes:
    """Read one allowlisted regular file without following symlinks."""
    if type(relative) is not str or relative not in ALLOWED_READ_PATHS:
        raise RuntimeError("path is not a fixed readable identity")
    if type(expected_size) is not int or expected_size < 1 or expected_size > 1_000_000:
        raise RuntimeError("artifact size bound is invalid")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.as_posix() != relative
        or ".." in pure.parts
        or "." in pure.parts
        or not pure.parts
    ):
        raise RuntimeError("artifact path is not canonical repository-relative")
    resolved = (REPOSITORY / pure).resolve()
    if not _inside_repository(resolved):
        raise RuntimeError("artifact path escaped repository")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeError("secure no-follow file API is unavailable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        directory_fds.append(os.open(REPOSITORY, directory_flags))
        for part in pure.parts[:-1]:
            directory_fds.append(
                os.open(part, directory_flags, dir_fd=directory_fds[-1])
            )
        file_fd = os.open(pure.parts[-1], file_flags, dir_fd=directory_fds[-1])
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise RuntimeError("artifact is not the expected regular file")
        chunks: list[bytes] = []
        remaining = expected_size
        while remaining:
            chunk = os.read(file_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(file_fd)
        identity_before = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if remaining or len(value) != expected_size or identity_before != identity_after:
            raise RuntimeError("artifact changed while it was being read")
        return value
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


def _json_unique(value: bytes, label: str) -> Any:
    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise RuntimeError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=closed_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid JSON in {label}") from error


def _runtime_snapshot() -> dict[str, bytes]:
    manifest_bytes = _read_repository_relative(
        RUNTIME_MANIFEST_RELATIVE, EXPECTED_RUNTIME_MANIFEST_SIZE,
    )
    if _sha_bytes(manifest_bytes) != EXPECTED_RUNTIME_MANIFEST_SHA256:
        raise RuntimeError("runtime manifest does not match the core pin")
    document = _json_unique(manifest_bytes, "runtime manifest")
    if type(document) is not dict or set(document) != {"version", "artifacts"}:
        raise RuntimeError("runtime manifest envelope is not closed")
    if document["version"] != "metnos.v26.5.3-runtime-manifest/1.0":
        raise RuntimeError("runtime manifest version mismatch")
    artifacts = document["artifacts"]
    if type(artifacts) is not list or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise RuntimeError("runtime artifact set has wrong cardinality")

    entries: dict[str, dict[str, Any]] = {}
    for item in artifacts:
        if type(item) is not dict or set(item) != {
            "id", "kind", "path", "sha256", "size",
        }:
            raise RuntimeError("runtime artifact entry is not closed")
        identity = item["id"]
        if type(identity) is not str or identity in entries:
            raise RuntimeError("runtime artifact identity is invalid or duplicated")
        if (
            type(item["kind"]) is not str
            or type(item["path"]) is not str
            or type(item["sha256"]) is not str
            or len(item["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in item["sha256"])
            or type(item["size"]) is not int
        ):
            raise RuntimeError("runtime artifact metadata types are invalid")
        entries[identity] = item
    if tuple(entries) != tuple(EXPECTED_ARTIFACTS):
        raise RuntimeError("runtime artifact order or identity changed")

    snapshot: dict[str, bytes] = {}
    for identity, expected in EXPECTED_ARTIFACTS.items():
        item = entries[identity]
        if (item["kind"], item["path"]) != expected:
            raise RuntimeError(f"runtime identity changed: {identity}")
        value = _read_repository_relative(item["path"], item["size"])
        if _sha_bytes(value) != item["sha256"]:
            raise RuntimeError(f"runtime artifact hash mismatch: {identity}")
        snapshot[identity] = value
    return snapshot


def _source_ast_safe(identity: str, source: bytes) -> None:
    allowed_imports = ALLOWED_SOURCE_IMPORTS[identity]
    try:
        tree = ast.parse(source, filename=f"<verified:{identity}>")
    except (SyntaxError, ValueError) as error:
        raise RuntimeError(f"invalid verified Python source: {identity}") from error
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name not in allowed_imports for alias in node.names):
                raise RuntimeError(f"source import denied: {identity}")
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module not in allowed_imports:
                raise RuntimeError(f"source import-from denied: {identity}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "__import__", "compile", "eval", "exec", "open",
            }:
                raise RuntimeError(f"dynamic source capability denied: {identity}")
            if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                raise RuntimeError(f"dynamic import denied: {identity}")
        elif isinstance(node, ast.Name) and node.id in {
            "__builtins__", "__import__",
        }:
            raise RuntimeError(f"builtins import access denied: {identity}")


def _exec_verified_source(
    identity: str, source: bytes, injections: dict[str, Any] | None = None,
) -> Any:
    if identity not in {"adapter", "validator"}:
        raise RuntimeError("only fixed adapter and validator may be compiled")
    _source_ast_safe(identity, source)
    module = types.ModuleType(f"metnos_v2653_{identity}")
    module.__file__ = f"<verified:{identity}>"
    if injections:
        module.__dict__.update(injections)
    code = compile(source, module.__file__, "exec", dont_inherit=True, optimize=0)
    exec(code, module.__dict__)
    return module


def _verified_environment() -> dict[str, str]:
    versions = {
        name: importlib.metadata.version(name)
        for name in EXPECTED_DISTRIBUTIONS
    }
    if versions != EXPECTED_DISTRIBUTIONS:
        raise RuntimeError(f"candidate dependency version drift: {versions}")
    if unicodedata.unidata_version != EXPECTED_UNICODE_VERSION:
        raise RuntimeError("Unicode database version drift")
    return {**versions, "unicode": unicodedata.unidata_version}


def _runtime_components() -> tuple[Any, Any, dict[str, Any], str]:
    _verified_environment()
    snapshot = _runtime_snapshot()
    registry = _json_unique(snapshot["registry"], "registry")
    if type(registry) is not dict:
        raise RuntimeError("registry root must be an object")
    schema = _json_unique(snapshot["schema"], "schema")
    if type(schema) is not dict:
        raise RuntimeError("schema root must be an object")
    try:
        prompt = snapshot["prompt"].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError("prompt is not strict UTF-8") from error
    adapter = _exec_verified_source("adapter", snapshot["adapter"])
    validator = _exec_verified_source(
        "validator", snapshot["validator"],
        {"__metnos_registry_bytes__": snapshot["registry"]},
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    return adapter, validator, schema, prompt


def _segments(text: str) -> list[dict[str, Any]]:
    if type(text) is not str:
        raise TypeError("original request must be an exact string")
    if len(text) > 100_000:
        raise ValueError("original request exceeds the technical size bound")
    parts = regex.split(r"\b", text, flags=regex.WORD | regex.VERSION1)
    result: list[dict[str, Any]] = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            raise ValueError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        result.append({
            "id": len(result) + 1,
            "text": part,
            "start_char": start,
            "end_char": end,
        })
    return result


def _json_snapshot(value: Any) -> dict[str, Any]:
    budget = {"nodes": 0, "characters": 0}

    def clone(item: Any, depth: int) -> Any:
        budget["nodes"] += 1
        if depth > 48 or budget["nodes"] > 20_000:
            raise ValueError("frame exceeds structural bounds")
        if type(item) is dict:
            try:
                pairs = list(item.items())
            except RuntimeError as error:
                raise ValueError("frame changed during snapshot") from error
            result: dict[str, Any] = {}
            for key, child in pairs:
                if type(key) is not str or key in result:
                    raise TypeError("frame object keys must be unique exact strings")
                budget["characters"] += len(key)
                result[key] = clone(child, depth + 1)
            return result
        if type(item) is list:
            return [clone(child, depth + 1) for child in list(item)]
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if item.bit_length() > 63:
                raise ValueError("frame integer exceeds bound")
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("frame float is not finite")
            return item
        if type(item) is str:
            budget["characters"] += len(item)
            if budget["characters"] > 1_000_000:
                raise ValueError("frame text exceeds bound")
            return item
        raise TypeError("frame contains a non-JSON or subclassed value")

    snapshot = clone(value, 0)
    if type(snapshot) is not dict:
        raise TypeError("frame must be an exact JSON object")
    return snapshot


def _request_body(original_request: str, seed: int = 92) -> dict[str, Any]:
    segments = _segments(original_request)
    _adapter, _validator, schema, prompt = _runtime_components()
    payload = {"original_request": original_request, "segments": segments}
    return {
        "model": "local",
        "temperature": 0,
        "seed": seed,
        "max_tokens": 2200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "phase1_typed_relational_v2653_compact",
                "schema": schema,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }


def evaluate(original_request: str, frame: dict[str, Any]) -> dict[str, Any]:
    """Validate a compact frame grounded only in the exact original request."""
    segments = _segments(original_request)
    compact_frame = _json_snapshot(frame)
    adapter, validator, schema, _prompt = _runtime_components()
    schema_errors = list(
        jsonschema.Draft202012Validator(schema).iter_errors(compact_frame)
    )
    if schema_errors:
        return {
            "status": "evaluated_invalid",
            "stage": "schema",
            "codes": ["schema"],
        }
    try:
        expanded = adapter.expand_frame(compact_frame)
    except adapter.UnsafeCompactGraph as error:
        return {
            "status": "evaluated_invalid",
            "stage": "adapter",
            "codes": [str(error)],
        }
    validation = validator.validate_frame(expanded, segments)
    if not validation["valid"]:
        return {
            "status": "evaluated_invalid",
            "stage": "validator",
            "codes": sorted({item["code"] for item in validation["errors"]}),
        }
    return {
        "status": "evaluated_valid",
        "stage": "accepted",
        "codes": [],
        "expanded_frame": expanded,
    }


def self_test() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def record(test_id: str, passed: bool, detail: Any = None) -> None:
        checks.append({"id": test_id, "pass": bool(passed), "detail": detail})

    fixture_bytes = _read_repository_relative(FIXTURE_RELATIVE, EXPECTED_FIXTURE_SIZE)
    record("fixture_hash", _sha_bytes(fixture_bytes) == EXPECTED_FIXTURE_SHA256)
    fixture = _json_unique(fixture_bytes, "fixture")
    fixture_request = " ".join(
        f"s{index:03d}" for index in range(1, fixture["segment_count"] + 1)
    )
    fixture_segments = _segments(fixture_request)
    record(
        "fixture_real_segments",
        len(fixture_segments) == fixture["segment_count"]
        and all(
            fixture_request[item["start_char"]:item["end_char"]] == item["text"]
            for item in fixture_segments
        ),
        len(fixture_segments),
    )

    for item in fixture["positive_controls"]:
        outcome = evaluate(fixture_request, item["compact_frame"])
        record(f"positive:{item['id']}", outcome["stage"] == "accepted", outcome["stage"])
    for family in ("native_negative_cases", "historical_cases"):
        for item in fixture[family]:
            outcome = evaluate(fixture_request, item["compact_frame"])
            actual = {"stage": outcome["stage"], "codes": outcome["codes"]}
            record(f"{family}:{item['id']}", actual == item["expected"], actual)

    body = _request_body("opaque-native-probe")
    user_payload = json.loads(body["messages"][1]["content"])
    record("request_keeps_original", user_payload["original_request"] == "opaque-native-probe")
    record(
        "request_segments_align",
        all(
            user_payload["original_request"][item["start_char"]:item["end_char"]]
            == item["text"]
            for item in user_payload["segments"]
        ),
    )
    record("public_api_exact", __all__ == ("evaluate",))
    record("evaluate_segments_absent", "evaluate_segments" not in globals())
    record("candidate_class_absent", "CandidateCore" not in globals())

    try:
        evaluate([{"id": 1}], fixture["positive_controls"][0]["compact_frame"])
    except TypeError as error:
        record("id_only_original_rejected", True, str(error))
    else:
        record("id_only_original_rejected", False, "id-only original accepted")

    class DictSubclass(dict):
        pass

    try:
        evaluate(fixture_request, DictSubclass(fixture["positive_controls"][0]["compact_frame"]))
    except TypeError as error:
        record("frame_subclass_rejected", True, str(error))
    else:
        record("frame_subclass_rejected", False, "dict subclass accepted")

    beyond = json.loads(json.dumps(fixture["positive_controls"][0]["compact_frame"]))
    beyond["atoms"][0]["clause_end_segment_id"] = (
        fixture["segment_count"] + 1
    )
    beyond_outcome = evaluate(fixture_request, beyond)
    record(
        "span_beyond_original_rejected",
        beyond_outcome["stage"] == "validator"
        and "clause_span" in beyond_outcome["codes"],
        beyond_outcome,
    )

    duplicate_manifest = (
        b'{"version":"x","version":"y","artifacts":[]}'
    )
    try:
        _json_unique(duplicate_manifest, "duplicate canary")
    except RuntimeError as error:
        record("duplicate_json_key_rejected", True, str(error))
    else:
        record("duplicate_json_key_rejected", False, "duplicate key accepted")

    path_canaries = [
        "/tmp/metnos_v2653_forbidden.py",
        "../internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py",
        "./internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py",
        "internal//tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py",
        "internal/tools/request_analysis_lab/candidates/v265/../v265/metnos_v265_compact_adapter.py",
    ]
    for index, path in enumerate(path_canaries, 1):
        try:
            _read_repository_relative(path, 1)
        except RuntimeError as error:
            record(f"path_canary:{index}", True, str(error))
        else:
            record(f"path_canary:{index}", False, "forbidden path accepted")

    source_canaries = [
        ("direct_socket", b"import socket\n"),
        ("alias_socket", b"import socket as harmless\n"),
        ("local_import", b"from . import local\n"),
        ("constructed_import", b'__import__("so" + "cket")\n'),
        ("open_builtin", b'open("x")\n'),
    ]
    for test_id, source in source_canaries:
        try:
            _source_ast_safe("adapter", source)
        except RuntimeError as error:
            record(f"source_canary:{test_id}", True, str(error))
        else:
            record(f"source_canary:{test_id}", False, "source capability accepted")

    compile_canary = b"x = 1\n"
    try:
        _exec_verified_source("schema", compile_canary)
    except RuntimeError as error:
        record("data_to_compile_rejected", True, str(error))
    else:
        record("data_to_compile_rejected", False, "data identity compiled")

    try:
        sys.audit("socket.connect", "synthetic")
    except RuntimeError as error:
        record("audit_transport_denied", True, str(error))
    else:
        record("audit_transport_denied", False, "transport audit event accepted")

    environment = _verified_environment()
    record("environment_pinned", environment == {
        "jsonschema": "4.10.3", "regex": "2026.3.32", "unicode": "15.0.0",
    })
    snapshot = _runtime_snapshot()
    record("runtime_identity_exact", set(snapshot) == set(EXPECTED_ARTIFACTS))
    failed = [item for item in checks if not item["pass"]]
    return {
        "version": VERSION,
        "network_calls": 0,
        "model_calls": 0,
        "candidate_outputs_read": 0,
        "runtime_manifest_sha256": EXPECTED_RUNTIME_MANIFEST_SHA256,
        "environment": environment,
        "summary": {
            "tests": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "positive_controls": len(fixture["positive_controls"]),
            "native_negative_cases": len(fixture["native_negative_cases"]),
            "historical_cases": len(fixture["historical_cases"]),
            "path_canaries": len(path_canaries),
            "source_canaries": len(source_canaries),
        },
        "failed": failed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    args = parser.parse_args()
    result = self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
