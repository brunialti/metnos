#!/usr/bin/env python3
"""Clause-owned V26.5.6.9 K1/34 runner, frozen with inference disabled.

The transport path is present for independent infrastructure review, but live
execution is impossible without a separate external lock that does not exist
in this author checkpoint.  Offline tests inject a fake ``urlopen``-compatible
opener; they never contact a server.

Delta against V26.5.6.4, whose transport was reviewed STATIC PASS and whose
bytes are inherited here unchanged wherever the contract does not touch them:

* the request contract is V26.5.6.6 (clause-owned): prompt, response schema,
  expander and evaluation pipeline are re-pinned by SHA-256 to that bundle,
  and the schema derived at runtime from the frozen registry must equal the
  materialised one byte for byte, so prompt and schema cannot drift apart;
* every case record carries the query-free structural fingerprint of whatever
  the model emitted, valid or invalid (S4 of the V26.5.6.4 post mortem).  The
  live K1/34 of V26.5.6.4 was undiagnosable precisely because a failing case
  left no structural trace;
* every case record carries the codes of all three stages (S3), so a schema
  failure no longer hides the semantic surface;
* the expanded frame is persisted only for a schema-valid frame.  A
  schema-valid frame contains no free-form string by construction -- every
  string position is an enum or a const of the frozen registry -- so the batch
  stays query-free without needing a redaction pass.

The V26.5.5 sealed worker of V26.5.6.4 is deliberately NOT used: it executes
the V26.5 compact adapter, which does not speak this contract, and its
manifest is pinned inside its own frozen bytes.  Rebuilding it for V26.5.6.6
would create new unreviewed security-critical surface.  The property it
provided is preserved differently and stated plainly: this runner never
executes model-derived data.  Its only compile/exec sites load SHA-pinned
repository bytes, model output is parsed with a strict JSON decoder into plain
data, and the pipeline that processes it is pure Python over that data.
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
import statistics
import sys
import time
import types
from typing import Any
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


VERSION = "metnos.v26.5.6.9-clause-owned-k1-runner/1.0"
HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v26569_k1_runner.py"
OFFLINE_EVALUATOR_PATH = HERE / "metnos_v26569_offline_evaluator.py"
SELFTEST_PATH = HERE / "metnos_v26569_offline_selftest.py"
FREEZE_PATH = HERE / "metnos_v26569_author.freeze.json"
AUTHOR_GATE_PATH = HERE / "metnos_v26569_author_pre_gate.json"
EXTERNAL_GATE_PATH = HERE / "metnos_v26569_external_gate.lock.json"
PREFLIGHT_GATE_PATH = HERE / "metnos_v26569_preflight_gate.lock.json"

MAX_SUCCESS_BODY_BYTES = 8 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 1024 * 1024
MODEL_BATCH_MAX_SERIALIZED_BYTES = 7_609_728
FIXED_SEED = 92
CONFIGURED_CASES = 34
EXTERNAL_PREFLIGHT_MAX_AGE_NS = 15 * 60 * 1_000_000_000
EXTERNAL_PREFLIGHT_FUTURE_TOLERANCE_NS = 5 * 1_000_000_000


def _rp(relative: str) -> Path:
    return REPOSITORY / relative


ARTIFACT_PINS: dict[str, tuple[Path, str, int]] = {
    "registry_projection_v26568": (
        _rp("internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_registry_projection.py"),
        "afaeaf669bb52fa2b4078917e6a9b8c7270ff138d8827d159a671895fc5edb0e", 25288,
    ),
    "expander_v26566": (
        _rp("internal/tools/request_analysis_lab/candidates/v26566/metnos_v26566_expander.py"),
        "4bcbc3c3563ee51e6ce5f038f8b3f13f3eb3b1ac16261fcc640011774f672701", 19179,
    ),
    "pipeline_v26566": (
        _rp("internal/tools/request_analysis_lab/candidates/v26566/metnos_v26566_offline_pipeline.py"),
        "9d1a2be5c16d7ae634a2a1d4b1acd38d757a0a1973412bb771667bea1dd8ea42", 12101,
    ),
    "injected_validator": (
        _rp("internal/tools/request_analysis_lab/candidates/v2653/metnos_v2653_injected_validator.py"),
        "66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d", 31627,
    ),
    "typed_registry": (
        _rp("internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_registry.json"),
        "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f", 9456,
    ),
    "clause_owned_schema": (
        _rp("internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_relation_typed.schema.json"),
        "c4313f044725a7dc4b0fc5a54f648f35d1792e488c00d8a4daac9fac3585c010", 111165,
    ),
    "clause_owned_prompt": (
        _rp("internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_relation_typed.prompt.txt"),
        "19c7a5e811319b39cff3c2c8cef6e6eb0ffe3f2cc2e67daf68b83f1eeb4cd3e4", 8125,
    ),
    "v26568_freeze": (
        _rp("internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_author.freeze.json"),
        "4fb54c3ecc89bafdc8748d1d056f9ee45f64ca0f7b2d8310f46367ec500d3ac3", 4856,
    ),
    "v26568_selftest_result": (
        _rp("internal/tools/request_analysis_lab/candidates/v26568/metnos_v26568_author_selftest_result.json"),
        "745ea59bd340b70707f35af7bd4c6611ef2a3fba6598a9a3d04e2f381171caac", 4823,
    ),
    "runtime_controls34": (
        _rp("internal/tools/request_analysis_lab/candidates/v2656/metnos_v2656_runtime_controls34.json"),
        "23e6e39df4d697a36c81fd911a0f3aa17e3ccaeeb126fcb2bcd2bcd5b3a191e1", 8955,
    ),
    "source_controls34": (
        _rp("internal/tools/request_analysis_lab/question_focus_controls_v1.json"),
        "22dac65689f720e07022ac204ba0fd25feebdf5e8db98a9d89f5bf00c120dcbf", 8296,
    ),
    "phase1_fixture": (
        _rp("internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_phase1_controls_frozen.json"),
        "1dfbc5d2a7a83308a0f414d1b2c0b395f096d034471930fbaa886627aa79d954", 41888,
    ),
    "phase1_evaluator": (
        _rp("internal/tools/request_analysis_lab/candidates/v2656/metnos_v2656_phase1_evaluator.py"),
        "fdee11190fe913f26069ff7627a62d7b143b28c911c2ea22a6afee91632126c7", 12154,
    ),
    "python_dependency_tree": (
        _rp("internal/tools/request_analysis_lab/candidates/v2656/metnos_v2656_python_dependency_tree.json"),
        "248b6a6877ad98d361890b50eb91664e3fec477ab6a8376f5dd48c0cb3e9b156", 28187,
    ),
    "oracle_freeze": (
        _rp("internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_oracle_audit_v1.freeze.json"),
        "f2f5d6046a7fb0b6fead8f5ab27c35c230aebd928411d5d5a962bce90209a70f", 1268,
    ),
    "oracle_audit_json": (
        _rp("internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_oracle_audit_v1.json"),
        "b8443ad2e27a2d773b971147c1b3d37ee19beb1091b1d357c0a84e6aa0c98a77", 2805,
    ),
    "oracle_audit_md": (
        _rp("internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_oracle_audit_v1.md"),
        "538f24876768eb0e8612f3bb440c5f38a51a222122d5b34080e5a5c19ef9799a", 11615,
    ),
    "oracle_overlay": (
        _rp("internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.overlay.json"),
        "e62d0605622e5f2c71e329e63448d29ad27fbfd3dafc64b880d4240cda9846af", 25627,
    ),
    "oracle_schema": (
        _rp("internal/tools/request_analysis_lab/oracles/phase1_v1/metnos_phase1_typed_oracle_v1.schema.json"),
        "4929fbc4ac524491f452d11d0f2d4d032887dd423c7487233c3d89065f4e17f2", 9073,
    ),
}

# Only the first namespace may be opened before the 34-case batch completes.
# The freeze pins both namespaces, but gold/evaluation evidence stays unread.
POSTBATCH_IDENTITIES = frozenset({
    "source_controls34", "phase1_fixture", "phase1_evaluator",
    "oracle_freeze", "oracle_audit_json", "oracle_audit_md",
    "oracle_overlay", "oracle_schema",
})
RUNTIME_ARTIFACTS = {
    identity: pin for identity, pin in ARTIFACT_PINS.items()
    if identity not in POSTBATCH_IDENTITIES
}
POSTBATCH_EVIDENCE = {
    identity: pin for identity, pin in ARTIFACT_PINS.items()
    if identity in POSTBATCH_IDENTITIES
}
if set(RUNTIME_ARTIFACTS).intersection(POSTBATCH_EVIDENCE):
    raise RuntimeError("runtime/postbatch artifact namespaces overlap")
if set(RUNTIME_ARTIFACTS).union(POSTBATCH_EVIDENCE) != set(ARTIFACT_PINS):
    raise RuntimeError("runtime/postbatch artifact partition is incomplete")

PYTHON_EXECUTABLE = Path("/usr/bin/python3")
PYTHON_TARGET = Path("/usr/bin/python3.12")
PYTHON_LINK_TARGET = "python3.12"
PYTHON_SHA256 = "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118"
PYTHON_SIZE = 8020928

_REGEX_MODULE: Any | None = None
_JSONSCHEMA_MODULE: Any | None = None
_CONTRACT: dict[str, Any] | None = None


class _ClosedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise RuntimeError("invalid command-line contract")


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical_hash(value: Any) -> str:
    return sha_bytes(json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


def _exact_json_equal(actual: Any, expected: Any) -> bool:
    """JSON equality without Python's bool/int equivalence."""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            _exact_json_equal(actual[key], expected[key]) for key in expected
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _exact_json_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


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


def _read_exact_file(path: Path, expected_sha: str, expected_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise RuntimeError(f"pinned file type/size mismatch: {path}")
        chunks = []
        remaining = expected_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if (
            remaining or len(value) != expected_size
            or before_identity != after_identity or sha_bytes(value) != expected_sha
        ):
            raise RuntimeError(f"pinned file hash/TOCTOU mismatch: {path}")
        return value
    finally:
        os.close(descriptor)


def _read_repo_relative_nofollow(relative: str, maximum_size: int) -> bytes:
    """Read one bounded repository file through a no-follow openat walk."""
    if type(relative) is not str or type(maximum_size) is not int or maximum_size < 0:
        raise RuntimeError("invalid bounded repository read request")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute() or not pure.parts or pure.as_posix() != relative
        or any(component in {"", ".", ".."} for component in pure.parts)
    ):
        raise RuntimeError("repository reference is not canonical relative POSIX")
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors = []
    file_descriptor = None
    try:
        descriptors.append(os.open(REPOSITORY, directory_flags))
        for component in pure.parts[:-1]:
            descriptors.append(os.open(
                component, directory_flags, dir_fd=descriptors[-1],
            ))
        file_descriptor = os.open(
            pure.parts[-1], file_flags, dir_fd=descriptors[-1],
        )
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum_size:
            raise RuntimeError("repository reference is not a bounded regular file")
        remaining = before.st_size
        chunks = []
        while remaining:
            chunk = os.read(file_descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        extra = os.read(file_descriptor, 1)
        after = os.fstat(file_descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if remaining or extra or len(value) != before.st_size or before_identity != after_identity:
            raise RuntimeError("repository reference changed during its single read")
        return value
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _fixed_repo_snapshot(path: Path, maximum_size: int) -> bytes:
    try:
        relative = path.relative_to(REPOSITORY).as_posix()
    except ValueError as error:
        raise RuntimeError("fixed artifact is outside repository") from error
    return _read_repo_relative_nofollow(relative, maximum_size)


def _runtime_artifact_bytes(identity: str) -> bytes:
    if identity not in RUNTIME_ARTIFACTS:
        raise RuntimeError("unknown fixed runtime artifact identity")
    return _read_exact_file(*RUNTIME_ARTIFACTS[identity])


def verify_runtime_artifacts() -> None:
    for identity in RUNTIME_ARTIFACTS:
        _runtime_artifact_bytes(identity)


def verify_python_dependency_tree() -> dict[str, Any]:
    manifest = _strict_json(
        _runtime_artifact_bytes("python_dependency_tree"), "dependency tree",
    )
    if (
        type(manifest) is not dict
        or manifest.get("version") != "metnos.v26.5.6-python-dependency-tree/1.1"
        or type(manifest.get("packages")) is not list
        or type(manifest.get("loaded_nonstdlib_modules")) is not list
    ):
        raise RuntimeError("dependency tree envelope changed")
    if [item.get("name") for item in manifest["packages"]] != ["jsonschema", "regex"]:
        raise RuntimeError("direct dependency identities changed")

    for package in manifest["packages"]:
        expected_files = package["files"]
        canonical = json.dumps(
            expected_files, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if (
            len(expected_files) != package["file_count"]
            or sha_bytes(canonical) != package["tree_sha256"]
        ):
            raise RuntimeError(f"dependency tree self-hash failed: {package['name']}")
        actual_paths = set()
        for role, root_text in (
            ("package", package["package_root"]),
            ("metadata", package["metadata_root"]),
        ):
            root = Path(root_text)
            if not root.is_dir() or root.is_symlink():
                raise RuntimeError(f"dependency root changed: {root}")
            for path in sorted(root.rglob("*")):
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                if path.is_symlink():
                    raise RuntimeError(f"dependency symlink denied: {path}")
                if path.is_file():
                    actual_paths.add(str(path.resolve()))
        expected_paths = {item["path"] for item in expected_files}
        if actual_paths != expected_paths:
            raise RuntimeError(f"dependency tree membership changed: {package['name']}")
        for item in expected_files:
            _read_exact_file(Path(item["path"]), item["sha256"], item["size"])

    inventory = manifest["loaded_nonstdlib_modules"]
    inventory_hash = sha_bytes(json.dumps(
        inventory, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8"))
    probe = manifest.get("inventory_probe", {})
    if (
        len(inventory) != probe.get("loaded_module_count")
        or inventory_hash != probe.get("loaded_modules_sha256")
    ):
        raise RuntimeError("loaded-module inventory self-hash failed")
    for item in inventory:
        _read_exact_file(Path(item["origin"]), item["sha256"], item["size"])
    return manifest


def _regex() -> Any:
    global _REGEX_MODULE
    if _REGEX_MODULE is not None:
        return _REGEX_MODULE
    manifest = verify_python_dependency_tree()
    regex_package = next(item for item in manifest["packages"] if item["name"] == "regex")
    site_root = str(Path(regex_package["package_root"]).parent)
    if site_root not in sys.path:
        sys.path.append(site_root)
    module = importlib.import_module("regex")
    if (
        Path(module.__file__).resolve() != Path(regex_package["module_origin"])
        or module.__version__ != regex_package["version"]
    ):
        raise RuntimeError("regex import origin/version changed")
    _REGEX_MODULE = module
    return module


def _load_runtime_module(identity: str, module_name: str) -> Any:
    source = _runtime_artifact_bytes(identity)
    path = RUNTIME_ARTIFACTS[identity][0]
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = None
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def _jsonschema() -> Any:
    global _JSONSCHEMA_MODULE
    if _JSONSCHEMA_MODULE is not None:
        return _JSONSCHEMA_MODULE
    manifest = verify_python_dependency_tree()
    package = next(
        item for item in manifest["packages"] if item["name"] == "jsonschema"
    )
    site_root = str(Path(package["package_root"]).parent)
    if site_root not in sys.path:
        sys.path.append(site_root)
    module = importlib.import_module("jsonschema")
    if (
        Path(module.__file__).resolve() != Path(package["module_origin"])
        or module.__version__ != package["version"]
    ):
        raise RuntimeError("jsonschema import origin/version changed")
    _JSONSCHEMA_MODULE = module
    return module


def _schema_key_vocabulary(schema: Any) -> frozenset[str]:
    """Every property name the frozen contract can legitimately carry.

    A JSON-schema error path is built from the keys of the frame, and a model
    can invent a key.  Only these names, and array indices, are ever persisted.
    """
    names: set[str] = set()

    def walk(node: Any) -> None:
        if type(node) is dict:
            properties = node.get("properties")
            if type(properties) is dict:
                names.update(key for key in properties if type(key) is str)
            required = node.get("required")
            if type(required) is list:
                names.update(item for item in required if type(item) is str)
            for value in node.values():
                walk(value)
        elif type(node) is list:
            for item in node:
                walk(item)

    walk(schema)
    return frozenset(names)


def _contract() -> dict[str, Any]:
    """Load the pinned V26.5.6.6 request contract and evaluation pipeline.

    The response schema is DERIVED from the frozen registry and must equal the
    materialised, hash-pinned one exactly: prompt and schema are generated by
    the same pinned module, so they cannot drift apart.
    """
    global _CONTRACT
    if _CONTRACT is not None:
        return _CONTRACT
    _jsonschema()
    projection = _load_runtime_module(
        "registry_projection_v26568", "metnos_v26569_pinned_projection",
    )
    expander = _load_runtime_module(
        "expander_v26566", "metnos_v26569_pinned_expander",
    )
    pipeline = _load_runtime_module(
        "pipeline_v26566", "metnos_v26569_pinned_pipeline",
    )
    registry = projection.load_registry(_runtime_artifact_bytes("typed_registry"))
    derived_schema = projection.build_schema(registry)
    materialised_schema = _strict_json(
        _runtime_artifact_bytes("clause_owned_schema"), "clause-owned schema",
    )
    if not _exact_json_equal(derived_schema, materialised_schema):
        raise RuntimeError("derived schema differs from the materialised contract")
    _CONTRACT = {
        "projection": projection,
        "expander": expander,
        "pipeline": pipeline,
        "registry": registry,
        "schema": materialised_schema,
        "validator": pipeline.load_validator(),
        "max_atoms": registry["limits"]["max_atoms_per_analysis"],
        "fingerprint_vocabulary": projection.fingerprint_vocabulary(registry),
        "schema_keys": _schema_key_vocabulary(materialised_schema),
    }
    return _CONTRACT


def unicode_segments(text: str) -> list[dict[str, Any]]:
    if type(text) is not str:
        raise TypeError("query is not an exact string")
    regex_module = _regex()
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
            raise RuntimeError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        result.append({
            "id": len(result) + 1, "text": part,
            "start_char": start, "end_char": end,
        })
    return result


def request_body(query: str, seed: int = FIXED_SEED) -> dict[str, Any]:
    if type(query) is not str or len(query) > 100_000 or seed != FIXED_SEED:
        raise ValueError("query/seed outside frozen request contract")
    schema = _strict_json(
        _runtime_artifact_bytes("clause_owned_schema"), "clause-owned schema",
    )
    prompt = _runtime_artifact_bytes("clause_owned_prompt").decode(
        "utf-8", errors="strict",
    )
    payload = {"original_request": query, "segments": unicode_segments(query)}
    return {
        "model": "local",
        "temperature": 0,
        "seed": FIXED_SEED,
        "max_tokens": 2200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "phase1_typed_relational_v26568_relation_typed",
                "schema": schema,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(
                payload, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"),
            )},
        ],
    }


def request_body_bytes(query: str) -> bytes:
    return json.dumps(
        request_body(query), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _exception_diagnostic(
    exc: BaseException, sensitive_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    # No exception or response text is persisted in a model batch.  Types and
    # errno retain transport diagnostics without leaking a query fragment.
    del sensitive_values
    chain = []
    current: BaseException | None = exc
    seen = set()
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        item = {
            "module": type(current).__module__,
            "type": type(current).__name__,
        }
        if isinstance(getattr(current, "errno", None), int):
            item["errno"] = current.errno
        chain.append(item)
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else (
            current.__cause__ or current.__context__
        )
    return {
        "exception_type": chain[0]["type"] if chain else type(exc).__name__,
        "cause_type": chain[1]["type"] if len(chain) > 1 else None,
        "errno": next((item.get("errno") for item in chain if "errno" in item), None),
        "exception_chain": chain,
    }


def _read_bounded(stream: Any, limit: int) -> tuple[bytes, bool]:
    captured = stream.read(limit + 1)
    if not isinstance(captured, bytes):
        captured = bytes(captured)
    return captured[:limit], len(captured) <= limit


def _declared_length(headers: Any) -> int | None:
    try:
        value = headers.get("Content-Length")
        return int(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _body_diagnostic(
    body: bytes, *, complete: bool, declared: int | None,
    excerpt: bool, sensitive_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    del excerpt, sensitive_values
    result = {
        "captured_bytes": len(body),
        "captured_sha256": sha_bytes(body),
        "body_complete": complete,
        "declared_bytes": declared,
        "body_bytes": len(body) if complete else None,
        "body_sha256": sha_bytes(body) if complete else None,
    }
    return result


class _DenyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed on redirects so one logical call cannot open twice."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, message, headers, new_url
        raise urllib.error.HTTPError(
            "redirect-denied", code, "redirect denied by frozen transport", {}, None,
        )


class _FrozenNativeTransport:
    """Explicitly counted opener with neither environment proxy nor redirect."""

    def __init__(self) -> None:
        self.open_count = 0
        self.last_effective_url: str | None = None
        self._director = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _DenyRedirectHandler(),
        )

    def open_once(self, request: urllib.request.Request, timeout: float):
        self.open_count += 1
        self.last_effective_url = request.full_url
        return self._director.open(request, timeout=timeout)


def _transport_state(transport: Any) -> tuple[int, str | None]:
    count = getattr(transport, "open_count", None)
    effective = getattr(transport, "last_effective_url", None)
    if type(count) is not int or count < 0 or (
        effective is not None and type(effective) is not str
    ):
        raise RuntimeError("transport does not expose exact open accounting")
    return count, effective


def _request_bytes_once(
    request: urllib.request.Request, *, timeout_s: float, success_limit: int,
    opener: Any | None = None, sensitive_values: tuple[str, ...] = (),
) -> tuple[bytes | None, dict[str, Any]]:
    started = time.perf_counter()
    diagnostic: dict[str, Any] = {
        "error": "", "phase": "transport", "socket_attempts": 0,
        "http_responses": 0, "server_accepted_requests": 0,
        "request_reached_server": False, "http_status": None,
        "response_body": None, "exception_chain": [],
    }
    transport = _FrozenNativeTransport() if opener is None else opener
    before_opens, _before_url = _transport_state(transport)
    response = None
    try:
        response = transport.open_once(request, timeout_s)
        after_opens, effective_url = _transport_state(transport)
        diagnostic["socket_attempts"] = after_opens - before_opens
        response_url = response.geturl() if hasattr(response, "geturl") else effective_url
        if diagnostic["socket_attempts"] != 1 or effective_url != request.full_url:
            response.close()
            response = None
            diagnostic.update({
                "error": "transport_open_count_or_destination_violation",
                "phase": "transport_boundary",
            })
            return None, diagnostic
        if response_url is not None and response_url != request.full_url:
            response.close()
            response = None
            diagnostic.update({
                "error": "transport_redirect_violation", "phase": "transport_boundary",
            })
            return None, diagnostic
        diagnostic.update({
            "http_responses": 1, "request_reached_server": True,
            "http_status": response.getcode(),
        })
        if (
            isinstance(diagnostic["http_status"], int)
            and 200 <= diagnostic["http_status"] < 300
        ):
            diagnostic["server_accepted_requests"] = 1
        declared = _declared_length(getattr(response, "headers", None))
        try:
            body, complete = _read_bounded(response, success_limit)
        finally:
            response.close()
            response = None
        diagnostic["response_body"] = _body_diagnostic(
            body, complete=complete, declared=declared, excerpt=not complete,
            sensitive_values=sensitive_values,
        )
        if not complete:
            diagnostic.update({"error": "response_body_too_large", "phase": "response_body"})
            return None, diagnostic
        status_code = diagnostic["http_status"]
        if not isinstance(status_code, int) or not 200 <= status_code < 300:
            diagnostic["response_body"] = _body_diagnostic(
                body, complete=True, declared=declared, excerpt=True,
                sensitive_values=sensitive_values,
            )
            diagnostic.update({"error": "http_status_error", "phase": "http"})
            return None, diagnostic
        diagnostic["phase"] = "response_body"
        return body, diagnostic
    except urllib.error.HTTPError as exc:
        after_opens, effective_url = _transport_state(transport)
        diagnostic["socket_attempts"] = after_opens - before_opens
        if diagnostic["socket_attempts"] != 1 or effective_url != request.full_url:
            diagnostic.update({
                "error": "transport_open_count_or_destination_violation",
                "phase": "transport_boundary",
                **_exception_diagnostic(exc, sensitive_values),
            })
            exc.close()
            return None, diagnostic
        try:
            body, complete = _read_bounded(exc, MAX_ERROR_BODY_BYTES)
        except Exception as read_error:
            diagnostic.update({
                "error": "http_error_body_read_error", "phase": "response_body",
                "http_responses": 1, "request_reached_server": True,
                "http_status": exc.code,
                **_exception_diagnostic(read_error, sensitive_values),
            })
            return None, diagnostic
        finally:
            exc.close()
        diagnostic.update({
            "error": "http_error", "phase": "http", "http_responses": 1,
            "request_reached_server": True, "http_status": exc.code,
            "response_body": _body_diagnostic(
                body, complete=complete, declared=_declared_length(exc.headers),
                excerpt=True, sensitive_values=sensitive_values,
            ),
            **_exception_diagnostic(exc, sensitive_values),
        })
        return None, diagnostic
    except Exception as exc:
        if response is not None:
            try:
                response.close()
            finally:
                response = None
        after_opens, effective_url = _transport_state(transport)
        diagnostic["socket_attempts"] = after_opens - before_opens
        boundary_failed = (
            diagnostic["socket_attempts"] != 1
            or effective_url != request.full_url
        )
        response_started = diagnostic.get("http_responses") == 1
        diagnostic.update({
            "error": (
                "transport_open_count_or_destination_violation" if boundary_failed
                else "response_body_read_error" if response_started else "transport_error"
            ),
            "phase": (
                "transport_boundary" if boundary_failed
                else "response_body" if response_started else "transport"
            ),
            **_exception_diagnostic(exc, sensitive_values),
        })
        return None, diagnostic
    finally:
        diagnostic["transport_latency_ms"] = (time.perf_counter() - started) * 1000


def call_once(
    query: str, endpoint: str, opener: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    body_out = request_body_bytes(query)
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body_out, headers={"Content-Type": "application/json"}, method="POST",
    )
    body, diagnostic = _request_bytes_once(
        request, timeout_s=120, success_limit=MAX_SUCCESS_BODY_BYTES,
        opener=opener, sensitive_values=(query,),
    )
    diagnostic.update({
        "request_body_bytes": len(body_out),
        "request_body_sha256": sha_bytes(body_out),
        "response_json_documents_decoded": 0,
        "decoded_chat_responses": 0, "decoded_frames": 0,
    })
    if body is None:
        return None, diagnostic
    try:
        response = _strict_json(body, "chat response")
    except Exception as exc:
        diagnostic.update({
            "error": "api_response_json_error", "phase": "api_response_json",
            **_exception_diagnostic(exc, (query,)),
            "response_body": _body_diagnostic(
                body, complete=True, declared=len(body), excerpt=True,
                sensitive_values=(query,),
            ),
        })
        return None, diagnostic
    diagnostic["response_json_documents_decoded"] = 1
    try:
        if type(response) is not dict:
            raise TypeError("chat response root is not an object")
        raw = response["choices"][0]["message"]["content"]
        if type(raw) is not str:
            raise TypeError("response content is not an exact string")
    except Exception as exc:
        diagnostic.update({
            "error": "api_response_shape_error", "phase": "api_response_shape",
            **_exception_diagnostic(exc, (query,)),
        })
        return None, diagnostic
    diagnostic["decoded_chat_responses"] = 1
    try:
        frame = _strict_json(raw, "model frame")
        if type(frame) is not dict:
            raise TypeError("model frame is not an exact object")
    except Exception as exc:
        diagnostic.update({
            "error": "model_frame_json_error", "phase": "model_frame_json",
            **_exception_diagnostic(exc, (query,)),
            "model_content": {
                "bytes": len(raw.encode("utf-8", "surrogatepass")),
                "sha256": sha_bytes(raw.encode("utf-8", "surrogatepass")),
            },
        })
        return None, diagnostic
    diagnostic.update({"phase": "complete", "decoded_frames": 1})
    return frame, diagnostic


def _valid_local_endpoint(endpoint: str) -> bool:
    try:
        if type(endpoint) is not str:
            return False
        parsed = urllib.parse.urlsplit(endpoint)
        if not (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and parsed.username is None and parsed.password is None
            and parsed.path == ""
            and not parsed.query and not parsed.fragment
        ):
            return False
        canonical = (
            f"http://[{parsed.hostname}]:{parsed.port}"
            if parsed.hostname == "::1"
            else f"http://{parsed.hostname}:{parsed.port}"
        )
        return endpoint == canonical
    except (TypeError, ValueError):
        return False


def _interpreter_context() -> dict[str, Any]:
    return {
        "executable": str(PYTHON_EXECUTABLE),
        "executable_link_target": PYTHON_LINK_TARGET,
        "python_target_sha256": PYTHON_SHA256,
        "python_version": "3.12.3",
        "isolated": True,
        "dont_write_bytecode": True,
        "unicode_version": "15.0.0",
        "dependency_manifest_sha256": ARTIFACT_PINS["python_dependency_tree"][1],
    }


def _counters(
    diagnostic: dict[str, Any], *, local_evaluations: int = 0,
    evaluated_cases: int = 0, valid_cases: int = 0, invalid_cases: int = 0,
    model_request_attempts: int = 0, schema_valid_cases: int = 0,
    expansion_clean_cases: int = 0, semantic_measured_cases: int = 0,
) -> dict[str, int]:
    return {
        "socket_attempts": int(diagnostic.get("socket_attempts", 0)),
        "http_responses": int(diagnostic.get("http_responses", 0)),
        "server_accepted_requests": int(diagnostic.get("server_accepted_requests", 0)),
        "response_json_documents_decoded": int(diagnostic.get("response_json_documents_decoded", 0)),
        "decoded_chat_responses": int(diagnostic.get("decoded_chat_responses", 0)),
        "decoded_frames": int(diagnostic.get("decoded_frames", 0)),
        "local_evaluations": local_evaluations,
        "evaluated_cases": evaluated_cases,
        "valid_cases": valid_cases,
        "invalid_cases": invalid_cases,
        # S3: how far the diagnosis actually reached, per stage.  A batch that
        # cannot answer these is the V26.5.6.4 batch.
        "schema_valid_cases": schema_valid_cases,
        "expansion_clean_cases": expansion_clean_cases,
        "semantic_measured_cases": semantic_measured_cases,
        "model_request_attempts": model_request_attempts,
    }


def _persisted_diagnostic(diagnostic: dict[str, Any]) -> dict[str, Any]:
    """Return the closed query-free diagnostic projection persisted in a batch.

    The response bytes are deliberately absent.  Their digest/length cannot be
    independently derived by the offline evaluator once the HTTP payload has
    been discarded, so retaining them would turn an observation into a false
    proof.  Request-body bytes remain: the evaluator reconstructs those exactly.
    """
    return {
        key: value for key, value in diagnostic.items()
        if key not in {"response_body", "model_content"}
    }


def _transport_smoke(endpoint: str, opener: Any | None = None) -> dict[str, Any]:
    started_at_unix_ns = time.time_ns()
    started_at_monotonic_ns = time.monotonic_ns()
    normalized_endpoint = endpoint.rstrip("/")
    request_url = normalized_endpoint + "/v1/models"
    request = urllib.request.Request(
        request_url,
        headers={"Accept": "application/json"}, method="GET",
    )
    body, diagnostic = _request_bytes_once(
        request, timeout_s=10, success_limit=1024 * 1024, opener=opener,
    )
    diagnostic.update({
        "response_json_documents_decoded": 0,
        "decoded_chat_responses": 0, "decoded_frames": 0,
    })
    status = "PASS"
    if body is None:
        status = "NOT_EVALUATED"
    else:
        try:
            payload = _strict_json(body, "preflight response")
            if type(payload) not in (dict, list):
                raise TypeError("model-list response has invalid root")
            diagnostic["response_json_documents_decoded"] = 1
            diagnostic["phase"] = "complete"
        except Exception as exc:
            status = "NOT_EVALUATED"
            diagnostic.update({
                "error": "preflight_response_json_or_shape_error",
                "phase": "preflight_response_json_or_shape",
                **_exception_diagnostic(exc),
            })
    persisted_diagnostic = _persisted_diagnostic(diagnostic)
    return {
        "version": "metnos.v26.5.6.9-transport-preflight/1.0",
        "status": status, "method": "GET",
        "endpoint": normalized_endpoint,
        "request_path": "/v1/models", "request_url": request_url,
        "request_body_bytes": 0, "transport_preflight_calls": 1,
        "inference_calls": 0, "diagnostic": persisted_diagnostic,
        "counters": _counters(diagnostic),
        "started_at_unix_ns": started_at_unix_ns,
        "completed_at_unix_ns": time.time_ns(),
        "started_at_monotonic_ns": started_at_monotonic_ns,
        "completed_at_monotonic_ns": time.monotonic_ns(),
        "interpreter_context": _interpreter_context(),
    }


def _sanitised_schema_paths(codes: Any, schema_keys: frozenset[str]) -> dict[str, Any]:
    """Persist a schema error path without persisting model-invented keys.

    A JSON-schema error path is built out of the frame's own keys, and the
    model controls those keys.  Only array indices and property names the
    frozen contract declares survive; everything else becomes a placeholder
    and is counted, exactly as the structural fingerprint does with strings.
    """
    kept: list[str] = []
    replaced = 0
    for code in codes if isinstance(codes, list) else []:
        if not isinstance(code, str):
            replaced += 1
            continue
        parts = []
        for component in code.split("/"):
            if component == "" or component.isdigit() or component in schema_keys:
                parts.append(component)
            else:
                replaced += 1
                parts.append("?")
        kept.append("/".join(parts))
    return {"paths": kept, "out_of_contract_components": replaced}


def _absent_fingerprint() -> dict[str, Any]:
    """The fingerprint of "no frame at all", from the pinned expander.

    S4 asks for a structural fingerprint on EVERY record.  A case whose model
    content never parsed, or whose transport failed, still gets one: it is the
    frame-less fingerprint, and it says so with well_formed=false.  Keeping the
    record shape uniform is part of the contract, not cosmetics.
    """
    contract = _contract()
    return contract["expander"].structural_fingerprint(
        None, vocabulary=contract["fingerprint_vocabulary"],
    )


def _run_case(query: str, endpoint: str, opener: Any | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    segments = unicode_segments(query)
    frame, diagnostic = call_once(query, endpoint, opener)
    absent = _absent_fingerprint()
    common = {
        "model_request_attempts": 1,
        "segment_count": len(segments),
        "segment_layout_sha256": canonical_hash([
            {key: item[key] for key in ("id", "start_char", "end_char")}
            for item in segments
        ]),
    }
    if diagnostic["error"] == "model_frame_json_error":
        return {
            **common, "status": "evaluated_invalid",
            "diagnostic": _persisted_diagnostic(diagnostic),
            "failure_class": "model_content_json_invalid",
            "validation": {
                "attempted": False, "stage": "model_json",
                "codes": ["model_frame_json_error"],
            },
            "fingerprint": absent,
            "fingerprint_sha256": canonical_hash(absent),
            "counters": _counters(
                diagnostic, evaluated_cases=1, invalid_cases=1,
                model_request_attempts=1,
            ),
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    if diagnostic["error"]:
        return {
            **common, "status": "NOT_EVALUATED",
            "diagnostic": _persisted_diagnostic(diagnostic),
            "failure_class": "transport_or_response_protocol",
            "validation": {"attempted": False, "stage": None, "codes": []},
            "fingerprint": absent,
            "fingerprint_sha256": canonical_hash(absent),
            "counters": _counters(diagnostic, model_request_attempts=1),
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    evaluation_started = time.perf_counter()
    try:
        contract = _contract()
        evaluation = contract["pipeline"].evaluate(
            frame, segments,
            schema=contract["schema"],
            validator_module=contract["validator"],
            expander=contract["expander"],
            max_atoms=contract["max_atoms"],
            fingerprint_vocabulary=contract["fingerprint_vocabulary"],
        )
        expanded, _expansion_codes = contract["expander"].expand_frame(
            frame, max_atoms=contract["max_atoms"],
        )
    except Exception as error:
        diagnostic.update({
            "error": "local_evaluation_infrastructure_error",
            "phase": "local_evaluation",
            **_exception_diagnostic(error, (query,)),
        })
        return {
            **common, "status": "NOT_EVALUATED",
            "diagnostic": _persisted_diagnostic(diagnostic),
            "failure_class": "local_validation_infrastructure",
            "validation": {"attempted": True, "stage": None, "codes": []},
            "counters": _counters(
                diagnostic, local_evaluations=1, model_request_attempts=1,
            ),
            "evaluation_latency_ms": (time.perf_counter() - evaluation_started) * 1000,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    evaluation_latency = (time.perf_counter() - evaluation_started) * 1000
    schema_ok = bool(evaluation["schema_ok"])
    valid = bool(evaluation["valid"])
    # S3: all three stages are persisted, so a schema failure never hides the
    # semantic surface the way it did in the V26.5.6.4 batch.
    validation = {
        "attempted": True,
        "stage": "accepted" if valid else "rejected",
        "schema_ok": schema_ok,
        "expansion_ok": bool(evaluation["expansion_ok"]),
        "validator_ok": bool(evaluation["validator_ok"]),
        "stages_measured": int(evaluation["stages_measured"]),
        "schema_paths": _sanitised_schema_paths(
            evaluation["stage_codes"]["schema"], contract["schema_keys"],
        ),
        "expansion_codes": list(evaluation["stage_codes"]["expansion"]),
        "validator_codes": list(evaluation["stage_codes"]["validator"]),
        "validator_schema_censored": bool(evaluation["validator_schema_censored"]),
        "validator_semantic_measured": bool(evaluation["validator_semantic_measured"]),
        "validator_semantic_codes": list(evaluation["validator_semantic_codes"]),
        # An offline evaluator can only re-derive these codes from a persisted
        # expansion, and an expansion is persisted only for a schema-valid
        # frame. On a schema-invalid frame they stay a useful OBSERVATION and
        # the record says so, instead of passing an unverifiable value off as
        # proof -- the mistake V26.5.6.3 removed for the response body.
        "semantic_codes_reproducible": schema_ok,
        "codes": list(evaluation["stage_codes"]["expansion"])
        + list(evaluation["stage_codes"]["validator"]),
    }
    result = {
        **common,
        "status": "evaluated_valid" if valid else "evaluated_invalid",
        "failure_class": None if valid else "model_frame_schema_expansion_or_semantic_invalid",
        "diagnostic": _persisted_diagnostic(diagnostic),
        "validation": validation,
        # S4: the query-free structural fingerprint is on EVERY record, valid
        # or not.  This is the evidence V26.5.6.4 did not keep.
        "fingerprint": evaluation["fingerprint"],
        "fingerprint_sha256": evaluation["fingerprint_sha256"],
        "counters": _counters(
            diagnostic, local_evaluations=1, evaluated_cases=1,
            valid_cases=1 if valid else 0, invalid_cases=0 if valid else 1,
            schema_valid_cases=1 if schema_ok else 0,
            expansion_clean_cases=1 if evaluation["expansion_ok"] else 0,
            semantic_measured_cases=1 if evaluation["validator_semantic_measured"] else 0,
            model_request_attempts=1,
        ),
        "evaluation_latency_ms": evaluation_latency,
        "total_latency_ms": (time.perf_counter() - started) * 1000,
    }
    if schema_ok:
        # A schema-valid frame carries no free-form string: every string
        # position of the contract is an enum or a const of the frozen
        # registry.  So the expanded frame can be persisted for every
        # schema-valid case, including the semantically invalid ones that
        # V26.5.6.4 left with no evidence at all, and the batch stays
        # query-free by construction.
        result["expanded_frame"] = expanded
    return result


def _runtime_controls() -> list[dict[str, Any]]:
    payload = _strict_json(
        _runtime_artifact_bytes("runtime_controls34"), "runtime controls",
    )
    if (
        type(payload) is not dict
        or set(payload) != {
            "version", "source_controls_sha256", "oracle_fields_present", "cases",
        }
        or payload["oracle_fields_present"] is not False
        or type(payload["cases"]) is not list
        or len(payload["cases"]) != CONFIGURED_CASES
    ):
        raise RuntimeError("runtime controls are not the query-only 34 fixture")
    cases = payload["cases"]
    for ordinal, item in enumerate(cases, 1):
        if (
            type(item) is not dict
            or set(item) != {"ordinal", "opaque_case_id", "query", "query_sha256_utf8"}
            or item["ordinal"] != ordinal
            or type(item["query"]) is not str
            or sha_text(item["query"]) != item["query_sha256_utf8"]
            or type(item["opaque_case_id"]) is not str
        ):
            raise RuntimeError("runtime control identity/content drift")
    if len({item["opaque_case_id"] for item in cases}) != CONFIGURED_CASES:
        raise RuntimeError("runtime control identities are not unique")
    return cases


def _aggregate(records: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "socket_attempts", "http_responses", "server_accepted_requests",
        "response_json_documents_decoded", "decoded_chat_responses",
        "decoded_frames", "local_evaluations", "evaluated_cases",
        "valid_cases", "invalid_cases",
        "schema_valid_cases", "expansion_clean_cases", "semantic_measured_cases",
        "model_request_attempts",
    )
    return {
        key: sum(int(item["result"]["counters"].get(key, 0)) for item in records)
        for key in keys
    }


def _latency_summary(records: list[dict[str, Any]]) -> dict[str, float | None]:
    values = sorted(float(item["result"]["total_latency_ms"]) for item in records)
    if not values:
        return {
            "latency_min_ms": None, "latency_median_ms": None,
            "latency_p95_ms": None, "latency_max_ms": None,
        }
    p95_index = max(0, min(len(values) - 1, (95 * len(values) + 99) // 100 - 1))
    return {
        "latency_min_ms": min(values),
        "latency_median_ms": statistics.median(values),
        "latency_p95_ms": values[p95_index],
        "latency_max_ms": max(values),
    }


def _run_controls(
    endpoint: str, opener: Any | None, *, native_run: bool,
    expected_checkpoint_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    _freeze, checkpoint_hashes = _verify_author_bytes()
    if (
        expected_checkpoint_hashes is not None
        and checkpoint_hashes != expected_checkpoint_hashes
    ):
        raise RuntimeError("author checkpoint changed after external gate verification")
    freeze_sha256 = checkpoint_hashes["freeze_sha256"]
    if not _valid_local_endpoint(endpoint):
        raise ValueError("endpoint is not explicit loopback HTTP with a port")
    controls = _runtime_controls()
    # The contract is loaded and verified BEFORE any transport: a schema that
    # no longer matches its registry must cost zero model calls, not 34.
    _contract()
    preflight = _transport_smoke(endpoint, opener)
    if preflight["status"] != "PASS":
        return {
            "version": VERSION, "artifact_kind": "model_batch",
            "status": "NOT_EVALUATED", "model_batch_complete": False,
            "freeze_sha256": freeze_sha256,
            "runner_sha256": checkpoint_hashes["runner_sha256"],
            "author_pre_gate_sha256": checkpoint_hashes["author_gate_sha256"],
            "summary": {
                "configured_cases": CONFIGURED_CASES, "attempted_cases": 0,
                "evaluated_cases": 0, "model_request_attempts": 0,
                "server_accepted_requests": 0, "decoded_chat_responses": 0,
                "model_evaluated_results": 0, "retries": 0,
                "inference_counters": _aggregate([]), **_latency_summary([]),
            },
            "abort": {
                "after_attempted_cases": 0, "failure_class": "inline_transport_preflight",
                "phase": preflight["diagnostic"].get("phase"),
                "error": preflight["diagnostic"].get("error"),
            },
            "inline_transport_preflight": preflight, "records": [],
            "query_material_included": False, "native_run": native_run,
            "evaluation_status": "NOT_RUN", "posthoc_credit": False,
            "accuracy_claimed": False,
        }
    records = []
    abort = None
    for case in controls:
        try:
            result = _run_case(case["query"], endpoint, opener)
            record = {
                "ordinal": case["ordinal"],
                "opaque_case_id": case["opaque_case_id"],
                "query_sha256_utf8": case["query_sha256_utf8"],
                "result": result,
            }
            records.append(record)
        except Exception as error:
            abort = {
                "at_opaque_case_id": case["opaque_case_id"],
                "at_case_ordinal": case["ordinal"],
                "after_completed_records": len(records),
                "case_record_written": False,
                "failure_class": "unexpected_local_case_exception",
                "phase": "local_case_orchestration",
                "error": "unexpected_local_case_exception",
                "exception": _exception_diagnostic(error, (case["query"],)),
            }
            break
        if result["status"] == "NOT_EVALUATED":
            abort = {
                "at_opaque_case_id": case["opaque_case_id"],
                "after_attempted_cases": len(records),
                "failure_class": result["failure_class"],
                "phase": result["diagnostic"].get("phase"),
                "error": result["diagnostic"].get("error"),
            }
            break
    aggregate = _aggregate(records)
    summary = {
        "configured_cases": CONFIGURED_CASES,
        "attempted_cases": len(records),
        "evaluated_cases": aggregate["evaluated_cases"],
        "model_request_attempts": aggregate["model_request_attempts"],
        "server_accepted_requests": aggregate["server_accepted_requests"],
        "decoded_chat_responses": aggregate["decoded_chat_responses"],
        "model_evaluated_results": aggregate["evaluated_cases"], "retries": 0,
        "inline_preflight_counters": preflight["counters"],
        "inference_counters": aggregate,
        "total_transport_attempts": (
            preflight["counters"]["socket_attempts"] + aggregate["socket_attempts"]
        ),
        **_latency_summary(records),
    }
    if abort is not None:
        return {
            "version": VERSION, "artifact_kind": "model_batch",
            "status": "NOT_EVALUATED", "model_batch_complete": False,
            "freeze_sha256": freeze_sha256,
            "runner_sha256": checkpoint_hashes["runner_sha256"],
            "author_pre_gate_sha256": checkpoint_hashes["author_gate_sha256"],
            "summary": summary, "abort": abort,
            "inline_transport_preflight": preflight, "records": records,
            "query_material_included": False, "native_run": native_run,
            "evaluation_status": "NOT_RUN", "posthoc_credit": False,
            "accuracy_claimed": False,
        }
    return {
        "version": VERSION, "artifact_kind": "model_batch",
        "status": "MODEL_BATCH_COMPLETE", "model_batch_complete": True,
        "freeze_sha256": freeze_sha256,
        "runner_sha256": checkpoint_hashes["runner_sha256"],
        "author_pre_gate_sha256": checkpoint_hashes["author_gate_sha256"],
        "summary": summary,
        "inline_transport_preflight": preflight, "records": records,
        "query_material_included": False, "native_run": native_run,
        "evaluation_status": "NOT_RUN", "posthoc_credit": False,
        "accuracy_claimed": False,
    }


def _repo_hash_reference(reference: dict[str, Any], label: str) -> dict[str, Any]:
    if type(reference) is not dict or set(reference) != {"path", "sha256"}:
        raise RuntimeError(f"external gate {label} reference is not closed")
    relative = reference["path"]
    value = _read_repo_relative_nofollow(relative, 4 * 1024 * 1024)
    if sha_bytes(value) != reference["sha256"]:
        raise RuntimeError(f"external gate {label} hash failed")
    payload = _strict_json(value, f"external gate {label}")
    if type(payload) is not dict:
        raise RuntimeError(f"external gate {label} payload is not an object")
    return payload


def _validate_external_preflight(
    preflight: dict[str, Any], endpoint: str, *,
    now_unix_ns: int | None = None, now_monotonic_ns: int | None = None,
) -> None:
    required = {
        "version", "status", "method", "endpoint", "request_path",
        "request_url", "request_body_bytes", "transport_preflight_calls",
        "inference_calls", "diagnostic", "counters",
        "started_at_unix_ns", "completed_at_unix_ns",
        "started_at_monotonic_ns", "completed_at_monotonic_ns",
        "interpreter_context", "preflight_gate_sha256", "preflight_gate_authority",
    }
    if type(preflight) is not dict or set(preflight) != required:
        raise RuntimeError("external transport preflight envelope is not closed")
    normalized_endpoint = endpoint.rstrip("/")
    fixed = {
        "version": "metnos.v26.5.6.9-transport-preflight/1.0",
        "status": "PASS", "method": "GET",
        "endpoint": normalized_endpoint, "request_path": "/v1/models",
        "request_url": normalized_endpoint + "/v1/models",
        "request_body_bytes": 0, "transport_preflight_calls": 1,
        "inference_calls": 0, "interpreter_context": _interpreter_context(),
        "preflight_gate_authority": "user_authorized_v26569_preflight_gate_verifier",
    }
    for key, value in fixed.items():
        if not _exact_json_equal(preflight.get(key), value):
            raise RuntimeError(f"external transport preflight mismatch: {key}")
    gate_hash = preflight.get("preflight_gate_sha256")
    if type(gate_hash) is not str or len(gate_hash) != 64:
        raise RuntimeError("external preflight lacks preliminary gate hash")
    try:
        preliminary_gate_bytes = _fixed_repo_snapshot(
            PREFLIGHT_GATE_PATH, 2 * 1024 * 1024,
        )
    except FileNotFoundError as error:
        raise RuntimeError("preliminary preflight gate is unavailable") from error
    if sha_bytes(preliminary_gate_bytes) != gate_hash:
        raise RuntimeError("external preflight preliminary gate hash mismatch")
    diagnostic = preflight["diagnostic"]
    counters = preflight["counters"]
    if (
        type(diagnostic) is not dict or type(counters) is not dict
        or diagnostic.get("error") != "" or diagnostic.get("phase") != "complete"
        or type(diagnostic.get("http_status")) is not int
        or diagnostic.get("http_status") != 200
        or diagnostic.get("request_reached_server") is not True
        or type(counters.get("socket_attempts")) is not int
        or counters.get("socket_attempts") != 1
        or type(counters.get("server_accepted_requests")) is not int
        or counters.get("server_accepted_requests") != 1
        or type(counters.get("response_json_documents_decoded")) is not int
        or counters.get("response_json_documents_decoded") != 1
        or type(counters.get("model_request_attempts")) is not int
        or counters.get("model_request_attempts") != 0
    ):
        raise RuntimeError("external transport preflight diagnostic did not prove PASS")
    names = (
        "started_at_unix_ns", "completed_at_unix_ns",
        "started_at_monotonic_ns", "completed_at_monotonic_ns",
    )
    if any(
        type(preflight.get(name)) is not int or preflight[name] < 0
        for name in names
    ):
        raise RuntimeError("external transport preflight timestamps are not exact integers")
    wall_start = preflight["started_at_unix_ns"]
    wall_complete = preflight["completed_at_unix_ns"]
    mono_start = preflight["started_at_monotonic_ns"]
    mono_complete = preflight["completed_at_monotonic_ns"]
    now_wall = time.time_ns() if now_unix_ns is None else now_unix_ns
    now_mono = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
    wall_duration = wall_complete - wall_start
    mono_duration = mono_complete - mono_start
    if (
        type(now_wall) is not int or type(now_mono) is not int
        or not 0 <= wall_duration <= 30 * 1_000_000_000
        or not 0 <= mono_duration <= 30 * 1_000_000_000
        or abs(wall_duration - mono_duration) > EXTERNAL_PREFLIGHT_FUTURE_TOLERANCE_NS
        or not -EXTERNAL_PREFLIGHT_FUTURE_TOLERANCE_NS
        <= now_wall - wall_complete <= EXTERNAL_PREFLIGHT_MAX_AGE_NS
        or not -EXTERNAL_PREFLIGHT_FUTURE_TOLERANCE_NS
        <= now_mono - mono_complete <= EXTERNAL_PREFLIGHT_MAX_AGE_NS
    ):
        raise RuntimeError("external transport preflight is stale or clock-inconsistent")


def _verify_preflight_gate_snapshot(
    endpoint: str, output: Path,
) -> tuple[dict[str, Any], str]:
    _freeze, checkpoint_hashes = _verify_author_bytes()
    try:
        gate_bytes = _fixed_repo_snapshot(PREFLIGHT_GATE_PATH, 2 * 1024 * 1024)
    except FileNotFoundError as error:
        raise RuntimeError(
            "V26.5.6.9 preflight gate is absent; author transport=false",
        ) from error
    gate = _strict_json(gate_bytes, "preflight gate")
    required = {
        "version", "status", "authority", "transport_preflight_allowed",
        "inference_allowed", "runner_sha256", "freeze_sha256",
        "author_gate_sha256", "authorized_endpoint", "authorized_output_path",
        "method", "request_path", "transport_call_limit", "inference_call_limit",
        "independent_review", "gate_verification",
    }
    if type(gate) is not dict or set(gate) != required:
        raise RuntimeError("V26.5.6.9 preflight gate envelope is not closed")
    expected = {
        "version": "metnos.v26.5.6.9-preflight-gate/1.0",
        "status": "authorized_transport_preflight_only",
        "authority": "user_authorized_v26569_preflight_gate_verifier",
        "transport_preflight_allowed": True, "inference_allowed": False,
        "runner_sha256": checkpoint_hashes["runner_sha256"],
        "freeze_sha256": checkpoint_hashes["freeze_sha256"],
        "author_gate_sha256": checkpoint_hashes["author_gate_sha256"],
        "authorized_endpoint": endpoint.rstrip("/"),
        "authorized_output_path": str(output),
        "method": "GET", "request_path": "/v1/models",
        "transport_call_limit": 1, "inference_call_limit": 0,
    }
    for key, value in expected.items():
        if not _exact_json_equal(gate.get(key), value):
            raise RuntimeError(f"V26.5.6.9 preflight gate mismatch: {key}")
    review = _repo_hash_reference(gate["independent_review"], "preflight_review")
    verification = _repo_hash_reference(
        gate["gate_verification"], "preflight_gate_verification",
    )
    if review.get("verdict") != "STATIC_PASS" or review.get("live_authorization") is not False:
        raise RuntimeError("preflight independent review contract failed")
    if (
        verification.get("status") != "PASS"
        or verification.get("authorizes_preflight") is not True
        or verification.get("authorizes_live") is not False
    ):
        raise RuntimeError("preflight gate verification contract failed")
    return gate, sha_bytes(gate_bytes)


def _verify_external_gate_snapshot(
    endpoint: str, output: Path,
) -> tuple[dict[str, Any], str]:
    _freeze, checkpoint_hashes = _verify_author_bytes()
    try:
        gate_bytes = _fixed_repo_snapshot(EXTERNAL_GATE_PATH, 2 * 1024 * 1024)
    except FileNotFoundError as error:
        raise RuntimeError(
            "V26.5.6.9 external gate is absent; author inference=false",
        ) from error
    gate = _strict_json(gate_bytes, "external gate")
    required = {
        "version", "status", "authority", "inference_allowed",
        "runner_sha256", "freeze_sha256", "author_gate_sha256",
        "authorized_endpoint", "authorized_output_path", "authorized_case_count",
        "model_request_attempt_limit", "retries", "one_call_per_case",
        "inline_preflight_required", "external_preflight_required",
        "inference_calls_before_lock", "transport_preflight_calls_before_lock",
        "independent_review", "gate_verification", "transport_preflight",
    }
    if type(gate) is not dict or set(gate) != required:
        raise RuntimeError("V26.5.6.9 external gate envelope is not closed")
    expected = {
        "version": "metnos.v26.5.6.9-external-gate/1.0",
        "status": "authorized_native_k1_34",
        "inference_allowed": True,
        "runner_sha256": checkpoint_hashes["runner_sha256"],
        "freeze_sha256": checkpoint_hashes["freeze_sha256"],
        "author_gate_sha256": checkpoint_hashes["author_gate_sha256"],
        "authorized_endpoint": endpoint.rstrip("/"),
        "authorized_output_path": str(output),
        "authorized_case_count": CONFIGURED_CASES,
        "model_request_attempt_limit": CONFIGURED_CASES,
        "retries": 0, "one_call_per_case": True,
        "inline_preflight_required": True, "external_preflight_required": True,
        "inference_calls_before_lock": 0,
        "transport_preflight_calls_before_lock": 1,
    }
    for key, expected_value in expected.items():
        if not _exact_json_equal(gate.get(key), expected_value):
            raise RuntimeError(f"V26.5.6.9 external gate field mismatch: {key}")
    if gate.get("authority") != "user_authorized_v26569_infra_review_and_gate_verifier":
        raise RuntimeError("V26.5.6.9 external gate authority mismatch")
    review = _repo_hash_reference(gate["independent_review"], "independent_review")
    verification = _repo_hash_reference(gate["gate_verification"], "gate_verification")
    preflight = _repo_hash_reference(gate["transport_preflight"], "transport_preflight")
    if review.get("verdict") != "STATIC_PASS" or review.get("live_authorization") is not False:
        raise RuntimeError("independent infra review contract failed")
    if verification.get("status") != "PASS" or verification.get("authorizes_live") is not True:
        raise RuntimeError("independent gate verification contract failed")
    _validate_external_preflight(preflight, endpoint)
    return gate, sha_bytes(gate_bytes)


def verify_external_gate(endpoint: str, output: Path) -> dict[str, Any]:
    return _verify_external_gate_snapshot(endpoint, output)[0]


def _verify_author_bytes() -> tuple[dict[str, Any], dict[str, str]]:
    """Verify immutable author bytes without inspecting later gate presence."""
    if (
        sys.executable != str(PYTHON_EXECUTABLE)
        or sys.version_info[:3] != (3, 12, 3)
        or sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or unicodedata.unidata_version != "15.0.0"
    ):
        raise RuntimeError("V26.5.6.9 requires /usr/bin/python3 -I -B and Unicode 15.0.0")
    if not PYTHON_EXECUTABLE.is_symlink() or os.readlink(PYTHON_EXECUTABLE) != PYTHON_LINK_TARGET:
        raise RuntimeError("/usr/bin/python3 symlink identity changed")
    _read_exact_file(PYTHON_TARGET, PYTHON_SHA256, PYTHON_SIZE)
    verify_runtime_artifacts()
    verify_python_dependency_tree()
    freeze_bytes = _fixed_repo_snapshot(FREEZE_PATH, 2 * 1024 * 1024)
    author_gate_bytes = _fixed_repo_snapshot(AUTHOR_GATE_PATH, 2 * 1024 * 1024)
    runner_bytes = _fixed_repo_snapshot(RUNNER_PATH, 2 * 1024 * 1024)
    evaluator_bytes = _fixed_repo_snapshot(OFFLINE_EVALUATOR_PATH, 2 * 1024 * 1024)
    selftest_bytes = _fixed_repo_snapshot(SELFTEST_PATH, 2 * 1024 * 1024)
    freeze = _strict_json(freeze_bytes, "author freeze")
    gate = _strict_json(author_gate_bytes, "author pre-gate")
    expected_gate = {
        "author_inference_attempts": 0,
        "external_gate_required": True,
        "external_transport_preflight_required": True,
        "independent_infrastructure_review": "pending_on_byte_stable_bundle",
        "inference_allowed": False,
        "live_runner_allowed": False,
        "lock_version": "metnos.v26.5.6.9-author-pre-gate/1.0",
        "model_calls_before_checkpoint": 0,
        "network_calls_before_checkpoint": 0,
        "offline_evaluator_in_bundle": True,
        "preflight_gate_required": True,
        "preflight_gate_status": "absent",
        "required_external_gate_path": (
            "internal/tools/request_analysis_lab/candidates/v26569/"
            "metnos_v26569_external_gate.lock.json"
        ),
        "required_preflight_gate_path": (
            "internal/tools/request_analysis_lab/candidates/v26569/"
            "metnos_v26569_preflight_gate.lock.json"
        ),
        "scope": "typed-registry Phase-1 K1/34 infrastructure only",
        "status": "PRE_INFRA_REVIEW_BLOCKED",
        "transport_preflight_allowed": False,
        "version": VERSION,
    }
    if (
        not _exact_json_equal(gate, expected_gate)
        or freeze.get("author_inference_allowed") is not False
        or freeze.get("external_gate_required") is not True
        or freeze.get("preflight_gate_required") is not True
        or freeze.get("preflight_gate_status") != "absent"
        or freeze.get("status") != "PRE_INFRA_REVIEW"
    ):
        raise RuntimeError("V26.5.6.9 author gate/freeze unexpectedly authorizes work")
    actual_pins = {
        identity: {
            "path": str(path.relative_to(REPOSITORY)),
            "sha256": expected_sha, "size": expected_size,
        }
        for identity, (path, expected_sha, expected_size) in ARTIFACT_PINS.items()
    }
    expected_fields = {
        "runner_sha256": sha_bytes(runner_bytes),
        "offline_evaluator_sha256": sha_bytes(evaluator_bytes),
        "selftest_sha256": sha_bytes(selftest_bytes),
        "author_pre_gate_sha256": sha_bytes(author_gate_bytes),
        "python_executable_sha256": PYTHON_SHA256,
        "artifact_pins": actual_pins,
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe")),
        "model_batch_max_serialized_bytes": MODEL_BATCH_MAX_SERIALIZED_BYTES,
    }
    for key, value in expected_fields.items():
        if freeze.get(key) != value:
            raise RuntimeError(f"V26.5.6.9 freeze pin mismatch: {key}")
    return freeze, {
        "freeze_sha256": sha_bytes(freeze_bytes),
        "author_gate_sha256": sha_bytes(author_gate_bytes),
        "runner_sha256": sha_bytes(runner_bytes),
    }


def _verify_author_gates_absent() -> tuple[dict[str, Any], dict[str, str]]:
    """Author-time policy only; operational gate verifiers never call this."""
    freeze, checkpoint_hashes = _verify_author_bytes()
    for absent_path, label in (
        (PREFLIGHT_GATE_PATH, "preflight gate"),
        (EXTERNAL_GATE_PATH, "external gate"),
    ):
        try:
            absent_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError(f"V26.5.6.9 {label} must be absent at author checkpoint")
    return freeze, checkpoint_hashes


def verify_freeze() -> dict[str, Any]:
    return _verify_author_gates_absent()[0]


def _open_absolute_directory_chain(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("output parent is not canonical absolute")
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


class _OutputAnchor:
    __slots__ = ("path", "name", "directory_fd", "directory_identity", "closed")

    def __init__(self, path: Path, required_suffix: str) -> None:
        if (
            not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or not path.name or not path.name.endswith(required_suffix)
        ):
            raise RuntimeError("output path is not an absolute authorized suffix")
        self.path = path
        self.name = path.name
        self.directory_fd = _open_absolute_directory_chain(path.parent)
        metadata = os.fstat(self.directory_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(self.directory_fd)
            raise RuntimeError("output parent is not a directory")
        self.directory_identity = (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
        )
        self.closed = False
        try:
            os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            self.close()
            raise FileExistsError(path)

    def assert_stable(self) -> None:
        if self.closed:
            raise RuntimeError("output anchor is closed")
        metadata = os.fstat(self.directory_fd)
        observed = (
            metadata.st_dev, metadata.st_ino, metadata.st_mode,
        )
        if observed != self.directory_identity:
            raise RuntimeError("anchored output directory identity changed")

    def close(self) -> None:
        if not self.closed:
            os.close(self.directory_fd)
            self.closed = True


def _canonical_json_size(value: Any, limit: int) -> int:
    if type(limit) is not int or limit < 1:
        raise RuntimeError("invalid JSON output cap")
    total = 0
    nodes = 0
    active = set()

    def add(amount: int) -> None:
        nonlocal total
        total += amount
        if total > limit:
            raise ValueError("serialized artifact exceeds frozen byte cap")

    def string_size(text: str) -> int:
        size = 2
        for character in text:
            codepoint = ord(character)
            if 0xD800 <= codepoint <= 0xDFFF:
                raise ValueError("serialized artifact contains Unicode surrogate")
            if character in ('"', "\\"):
                size += 2
            elif codepoint < 0x20:
                size += 2 if codepoint in (8, 9, 10, 12, 13) else 6
            elif codepoint <= 0x7F:
                size += 1
            elif codepoint <= 0x7FF:
                size += 2
            elif codepoint <= 0xFFFF:
                size += 3
            else:
                size += 4
            if total + size > limit:
                raise ValueError("serialized artifact exceeds frozen byte cap")
        return size

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 2_000_000 or depth > 128:
            raise ValueError("serialized artifact exceeds structural bound")
        item_type = type(item)
        if item is None:
            add(4)
        elif item_type is bool:
            add(4 if item else 5)
        elif item_type is int:
            add(len(str(item)))
        elif item_type is float:
            if not math.isfinite(item):
                raise ValueError("serialized artifact contains non-finite float")
            add(len(json.dumps(item, allow_nan=False, separators=(",", ":"))))
        elif item_type is str:
            add(string_size(item))
        elif item_type in (list, dict):
            identity = id(item)
            if identity in active:
                raise ValueError("serialized artifact contains a cycle")
            active.add(identity)
            try:
                add(2)
                if item_type is list:
                    for index, member in enumerate(item):
                        if index:
                            add(1)
                        visit(member, depth + 1)
                else:
                    for index, (key, member) in enumerate(item.items()):
                        if type(key) is not str:
                            raise TypeError("serialized artifact key is not an exact string")
                        if index:
                            add(1)
                        add(string_size(key))
                        add(1)
                        visit(member, depth + 1)
            finally:
                active.remove(identity)
        else:
            raise TypeError("serialized artifact contains a non-JSON exact value")

    visit(value, 0)
    return total


def write_json_exclusive_atomic(
    anchor: _OutputAnchor, payload: dict[str, Any], *,
    maximum_bytes: int = MODEL_BATCH_MAX_SERIALIZED_BYTES,
) -> None:
    anchor.assert_stable()
    encoded_size = _canonical_json_size(payload, maximum_bytes - 1)
    data = (json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")
    if len(data) != encoded_size + 1 or len(data) > maximum_bytes:
        raise RuntimeError("JSON output counter disagrees with canonical encoding")
    temporary_name = f".{anchor.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    descriptor = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=anchor.directory_fd,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        anchor.assert_stable()
        os.link(
            temporary_name, anchor.name,
            src_dir_fd=anchor.directory_fd, dst_dir_fd=anchor.directory_fd,
            follow_symlinks=False,
        )
        os.fsync(anchor.directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=anchor.directory_fd)
        except FileNotFoundError:
            pass


def run_preflight(
    endpoint: str, output: Path, opener: Any | None = None,
) -> dict[str, Any]:
    if not _valid_local_endpoint(endpoint):
        raise RuntimeError("preflight endpoint is not literal loopback HTTP")
    anchor = _OutputAnchor(output, "_preflight.json")
    try:
        gate, gate_sha256 = _verify_preflight_gate_snapshot(endpoint, output)
        result = _transport_smoke(endpoint, opener)
        result["preflight_gate_sha256"] = gate_sha256
        result["preflight_gate_authority"] = gate["authority"]
        write_json_exclusive_atomic(anchor, result)
        return result
    finally:
        anchor.close()


def run_live(endpoint: str, output: Path) -> dict[str, Any]:
    if not _valid_local_endpoint(endpoint):
        raise RuntimeError("external gate endpoint is not literal loopback HTTP")
    anchor = _OutputAnchor(output, ".json")
    try:
        gate, external_gate_sha256 = _verify_external_gate_snapshot(endpoint, output)
        result = _run_controls(
            endpoint, None, native_run=True,
            expected_checkpoint_hashes={
                "runner_sha256": gate["runner_sha256"],
                "freeze_sha256": gate["freeze_sha256"],
                "author_gate_sha256": gate["author_gate_sha256"],
            },
        )
        result["external_gate_sha256"] = external_gate_sha256
        result["external_gate_authority"] = gate["authority"]
        write_json_exclusive_atomic(anchor, result)
        return result
    finally:
        anchor.close()


def _main(argv: list[str] | None = None) -> int:
    parser = _ClosedArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--verify-freeze", action="store_true")
    actions.add_argument("--controls", action="store_true")
    actions.add_argument("--preflight", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.verify_freeze:
        freeze = verify_freeze()
        print(json.dumps({
            "status": "PASS", "freeze_status": freeze["status"],
            "inference_allowed": False,
        }, sort_keys=True))
        return 0
    if not args.endpoint or not args.output:
        raise RuntimeError("selected action requires endpoint and output")
    if args.preflight:
        result = run_preflight(args.endpoint, Path(args.output))
        print(json.dumps({
            "status": result["status"], "method": "GET", "inference_calls": 0,
        }, sort_keys=True))
        return 0 if result["status"] == "PASS" else 2
    result = run_live(args.endpoint, Path(args.output))
    print(json.dumps(result["summary"], sort_keys=True))
    return 0 if result["status"] == "MODEL_BATCH_COMPLETE" else 2


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception as error:
        diagnostic = {
            "version": VERSION,
            "status": "CLI_ERROR",
            "phase": "cli_boundary",
            "error": "technical_operation_failed",
            "exception_type": type(error).__name__,
            "cause_type": type(error.__cause__).__name__ if error.__cause__ else None,
            "errno": error.errno if isinstance(getattr(error, "errno", None), int) else None,
            "traceback_emitted": False,
        }
        try:
            sys.stderr.write(json.dumps(
                diagnostic, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            ) + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
