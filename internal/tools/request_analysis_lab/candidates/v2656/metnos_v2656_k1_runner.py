#!/usr/bin/env python3
"""Compact V26.5.6 K1/34 runner, frozen with inference disabled.

The transport path is present for independent infrastructure review, but live
execution is impossible without a separate external lock that does not exist
in this author checkpoint.  Offline tests inject a fake ``urlopen``-compatible
opener; they never contact a server.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
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


VERSION = "metnos.v26.5.6-compact-k1-runner/1.0"
HERE = Path(__file__).resolve(strict=True).parent
REPOSITORY = HERE.parents[4]
RUNNER_PATH = HERE / "metnos_v2656_k1_runner.py"
FREEZE_PATH = HERE / "metnos_v2656_author.freeze.json"
AUTHOR_GATE_PATH = HERE / "metnos_v2656_author_pre_gate.json"
EXTERNAL_GATE_PATH = HERE / "metnos_v2656_external_gate.lock.json"

MAX_SUCCESS_BODY_BYTES = 8 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 1024 * 1024
FIXED_SEED = 92
CONFIGURED_CASES = 34
EXTERNAL_PREFLIGHT_MAX_AGE_NS = 15 * 60 * 1_000_000_000
EXTERNAL_PREFLIGHT_FUTURE_TOLERANCE_NS = 5 * 1_000_000_000


def _rp(relative: str) -> Path:
    return REPOSITORY / relative


ARTIFACT_PINS: dict[str, tuple[Path, str, int]] = {
    "facade_v2655": (
        _rp("internal/tools/request_analysis_lab/candidates/v2655/metnos_v2655_facade.py"),
        "fc34ffefc8c77c51a959a1fea5625ea896242afcfff0ed2a41e7f89c14d48d63", 15053,
    ),
    "worker_v2654": (
        _rp("internal/tools/request_analysis_lab/candidates/v2654/metnos_v2654_worker.py"),
        "c57c8ececdfffdc2380c7e318e1c9fe8070d074fadf641e0c458367704c20e22", 19601,
    ),
    "worker_manifest_v2654": (
        _rp("internal/tools/request_analysis_lab/candidates/v2654/metnos_v2654_runtime_manifest.json"),
        "88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391", 1426,
    ),
    "compact_adapter": (
        _rp("internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py"),
        "3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1", 5600,
    ),
    "injected_validator": (
        _rp("internal/tools/request_analysis_lab/candidates/v2653/metnos_v2653_injected_validator.py"),
        "66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d", 31627,
    ),
    "typed_registry": (
        _rp("internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_registry.json"),
        "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f", 9456,
    ),
    "compact_schema": (
        _rp("internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.schema.json"),
        "76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921", 13751,
    ),
    "compact_prompt": (
        _rp("internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.prompt.txt"),
        "893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418", 8154,
    ),
    "v2655_review_json": (
        _rp("internal/tools/request_analysis_lab/candidates/v2655/metnos_v2655_independent_static_review.json"),
        "47e73c4bd52664241e94b60680ffef95efee7a406d937d61405207bf3da40485", 4022,
    ),
    "v2655_review_md": (
        _rp("internal/tools/request_analysis_lab/candidates/v2655/metnos_v2655_independent_static_review.md"),
        "048e488aa9c7f32cea0f72a8732690e8b4b55b56313c55b9c47eb9b6067e73e5", 1630,
    ),
    "runtime_controls34": (
        HERE / "metnos_v2656_runtime_controls34.json",
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
        HERE / "metnos_v2656_phase1_evaluator.py",
        "fdee11190fe913f26069ff7627a62d7b143b28c911c2ea22a6afee91632126c7", 12154,
    ),
    "python_dependency_tree": (
        HERE / "metnos_v2656_python_dependency_tree.json",
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
_FACADE_MODULE: Any | None = None


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical_hash(value: Any) -> str:
    return sha_bytes(json.dumps(
        value, ensure_ascii=True, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))


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


def _facade() -> Any:
    global _FACADE_MODULE
    if _FACADE_MODULE is None:
        _FACADE_MODULE = _load_runtime_module(
            "facade_v2655", "metnos_v2656_pinned_facade",
        )
    return _FACADE_MODULE


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
        _runtime_artifact_bytes("compact_schema"), "compact schema",
    )
    prompt = _runtime_artifact_bytes("compact_prompt").decode(
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
                "name": "phase1_typed_relational_v265_compact",
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


def _request_bytes_once(
    request: urllib.request.Request, *, timeout_s: float, success_limit: int,
    opener: Any | None = None, sensitive_values: tuple[str, ...] = (),
) -> tuple[bytes | None, dict[str, Any]]:
    started = time.perf_counter()
    diagnostic: dict[str, Any] = {
        "error": "", "phase": "transport", "socket_attempts": 1,
        "http_responses": 0, "server_accepted_requests": 0,
        "request_reached_server": False, "http_status": None,
        "response_body": None, "exception_chain": [],
    }
    open_once = opener if opener is not None else urllib.request.urlopen
    try:
        response = open_once(request, timeout=timeout_s)
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
        response_started = diagnostic.get("http_responses") == 1
        diagnostic.update({
            "error": "response_body_read_error" if response_started else "transport_error",
            "phase": "response_body" if response_started else "transport",
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
        parsed = urllib.parse.urlsplit(endpoint)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port is not None
            and parsed.username is None and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query and not parsed.fragment
        )
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
    diagnostic: dict[str, Any], *, facade_evaluations: int = 0,
    evaluated_cases: int = 0, valid_cases: int = 0, invalid_cases: int = 0,
    model_request_attempts: int = 0,
) -> dict[str, int]:
    return {
        "socket_attempts": int(diagnostic.get("socket_attempts", 0)),
        "http_responses": int(diagnostic.get("http_responses", 0)),
        "server_accepted_requests": int(diagnostic.get("server_accepted_requests", 0)),
        "response_json_documents_decoded": int(diagnostic.get("response_json_documents_decoded", 0)),
        "decoded_chat_responses": int(diagnostic.get("decoded_chat_responses", 0)),
        "decoded_frames": int(diagnostic.get("decoded_frames", 0)),
        "facade_evaluations": facade_evaluations,
        "evaluated_cases": evaluated_cases,
        "valid_cases": valid_cases,
        "invalid_cases": invalid_cases,
        "model_request_attempts": model_request_attempts,
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
    return {
        "version": "metnos.v26.5.6-transport-preflight/1.0",
        "status": status, "method": "GET",
        "endpoint": normalized_endpoint,
        "request_path": "/v1/models", "request_url": request_url,
        "request_body_bytes": 0, "transport_preflight_calls": 1,
        "inference_calls": 0, "diagnostic": diagnostic,
        "counters": _counters(diagnostic),
        "started_at_unix_ns": started_at_unix_ns,
        "completed_at_unix_ns": time.time_ns(),
        "started_at_monotonic_ns": started_at_monotonic_ns,
        "completed_at_monotonic_ns": time.monotonic_ns(),
        "interpreter_context": _interpreter_context(),
    }


def _run_case(query: str, endpoint: str, opener: Any | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    segments = unicode_segments(query)
    frame, diagnostic = call_once(query, endpoint, opener)
    common = {
        "model_request_attempts": 1,
        "segment_count": len(segments),
        "segment_layout_sha256": canonical_hash([
            {key: item[key] for key in ("id", "start_char", "end_char")}
            for item in segments
        ]),
        "diagnostic": diagnostic,
    }
    if diagnostic["error"] == "model_frame_json_error":
        return {
            **common, "status": "evaluated_invalid",
            "failure_class": "model_content_json_invalid",
            "validation": {"attempted": False, "stage": "model_json", "codes": ["model_frame_json_error"]},
            "counters": _counters(
                diagnostic, evaluated_cases=1, invalid_cases=1,
                model_request_attempts=1,
            ),
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    if diagnostic["error"]:
        return {
            **common, "status": "NOT_EVALUATED",
            "failure_class": "transport_or_response_protocol",
            "validation": {"attempted": False, "stage": None, "codes": []},
            "counters": _counters(diagnostic, model_request_attempts=1),
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    facade_started = time.perf_counter()
    try:
        validation = _facade().evaluate(query, frame)
    except (TypeError, ValueError) as error:
        diagnostic.update({
            "error": "facade_model_input_invalid", "phase": "facade_validation",
            **_exception_diagnostic(error, (query,)),
        })
        return {
            **common, "status": "evaluated_invalid",
            "failure_class": "model_frame_rejected_before_facade_worker",
            "raw_frame_sha256": canonical_hash(frame),
            "validation": {"attempted": True, "stage": "facade_input", "codes": [type(error).__name__]},
            "counters": _counters(
                diagnostic, facade_evaluations=1, evaluated_cases=1, invalid_cases=1,
                model_request_attempts=1,
            ),
            "facade_latency_ms": (time.perf_counter() - facade_started) * 1000,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    except RuntimeError as error:
        diagnostic.update({
            "error": "facade_infrastructure_error", "phase": "facade_infrastructure",
            **_exception_diagnostic(error, (query,)),
        })
        return {
            **common, "status": "NOT_EVALUATED",
            "failure_class": "local_validation_infrastructure",
            "validation": {"attempted": True, "stage": None, "codes": []},
            "counters": _counters(
                diagnostic, facade_evaluations=1, model_request_attempts=1,
            ),
            "facade_latency_ms": (time.perf_counter() - facade_started) * 1000,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    facade_latency = (time.perf_counter() - facade_started) * 1000
    if validation["status"] == "evaluated_invalid":
        return {
            **common, "status": "evaluated_invalid",
            "failure_class": "model_frame_schema_adapter_or_semantic_invalid",
            "raw_frame_sha256": canonical_hash(frame),
            "validation": {
                "attempted": True, "stage": validation["stage"],
                "codes": validation["codes"],
            },
            "counters": _counters(
                diagnostic, facade_evaluations=1, evaluated_cases=1, invalid_cases=1,
                model_request_attempts=1,
            ),
            "facade_latency_ms": facade_latency,
            "total_latency_ms": (time.perf_counter() - started) * 1000,
        }
    return {
        **common, "status": "evaluated_valid", "failure_class": None,
        "raw_frame_sha256": canonical_hash(frame),
        "expanded_frame": validation["expanded_frame"],
        "validation": {"attempted": True, "stage": "accepted", "codes": []},
        "counters": _counters(
            diagnostic, facade_evaluations=1, evaluated_cases=1, valid_cases=1,
            model_request_attempts=1,
        ),
        "facade_latency_ms": facade_latency,
        "total_latency_ms": (time.perf_counter() - started) * 1000,
    }


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
        "decoded_frames", "facade_evaluations", "evaluated_cases",
        "valid_cases", "invalid_cases",
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
    _freeze, checkpoint_hashes = _verify_author_checkpoint()
    if (
        expected_checkpoint_hashes is not None
        and checkpoint_hashes != expected_checkpoint_hashes
    ):
        raise RuntimeError("author checkpoint changed after external gate verification")
    freeze_sha256 = checkpoint_hashes["freeze_sha256"]
    if not _valid_local_endpoint(endpoint):
        raise ValueError("endpoint is not explicit loopback HTTP with a port")
    controls = _runtime_controls()
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
        "interpreter_context",
    }
    if type(preflight) is not dict or set(preflight) != required:
        raise RuntimeError("external transport preflight envelope is not closed")
    normalized_endpoint = endpoint.rstrip("/")
    fixed = {
        "version": "metnos.v26.5.6-transport-preflight/1.0",
        "status": "PASS", "method": "GET",
        "endpoint": normalized_endpoint, "request_path": "/v1/models",
        "request_url": normalized_endpoint + "/v1/models",
        "request_body_bytes": 0, "transport_preflight_calls": 1,
        "inference_calls": 0, "interpreter_context": _interpreter_context(),
    }
    for key, value in fixed.items():
        if preflight.get(key) != value:
            raise RuntimeError(f"external transport preflight mismatch: {key}")
    diagnostic = preflight["diagnostic"]
    counters = preflight["counters"]
    if (
        type(diagnostic) is not dict or type(counters) is not dict
        or diagnostic.get("error") != "" or diagnostic.get("phase") != "complete"
        or diagnostic.get("http_status") != 200
        or diagnostic.get("request_reached_server") is not True
        or counters.get("socket_attempts") != 1
        or counters.get("server_accepted_requests") != 1
        or counters.get("response_json_documents_decoded") != 1
        or counters.get("model_request_attempts") != 0
    ):
        raise RuntimeError("external transport preflight diagnostic did not prove PASS")
    names = (
        "started_at_unix_ns", "completed_at_unix_ns",
        "started_at_monotonic_ns", "completed_at_monotonic_ns",
    )
    if any(type(preflight.get(name)) is not int for name in names):
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


def _verify_external_gate_snapshot(
    endpoint: str, output: Path,
) -> tuple[dict[str, Any], str]:
    _freeze, checkpoint_hashes = _verify_author_checkpoint()
    try:
        gate_bytes = _fixed_repo_snapshot(EXTERNAL_GATE_PATH, 2 * 1024 * 1024)
    except FileNotFoundError as error:
        raise RuntimeError(
            "V26.5.6 external gate is absent; author inference=false",
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
        raise RuntimeError("V26.5.6 external gate envelope is not closed")
    expected = {
        "version": "metnos.v26.5.6-external-gate/1.0",
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
        if gate.get(key) != expected_value:
            raise RuntimeError(f"V26.5.6 external gate field mismatch: {key}")
    if gate.get("authority") != "independent_v2656_infra_review_and_gate_verifier":
        raise RuntimeError("V26.5.6 external gate authority mismatch")
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


def _verify_author_checkpoint() -> tuple[dict[str, Any], dict[str, str]]:
    if (
        sys.executable != str(PYTHON_EXECUTABLE)
        or sys.version_info[:3] != (3, 12, 3)
        or sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or unicodedata.unidata_version != "15.0.0"
    ):
        raise RuntimeError("V26.5.6 requires /usr/bin/python3 -I -B and Unicode 15.0.0")
    if not PYTHON_EXECUTABLE.is_symlink() or os.readlink(PYTHON_EXECUTABLE) != PYTHON_LINK_TARGET:
        raise RuntimeError("/usr/bin/python3 symlink identity changed")
    _read_exact_file(PYTHON_TARGET, PYTHON_SHA256, PYTHON_SIZE)
    verify_runtime_artifacts()
    verify_python_dependency_tree()
    freeze_bytes = _fixed_repo_snapshot(FREEZE_PATH, 2 * 1024 * 1024)
    author_gate_bytes = _fixed_repo_snapshot(AUTHOR_GATE_PATH, 2 * 1024 * 1024)
    runner_bytes = _fixed_repo_snapshot(RUNNER_PATH, 2 * 1024 * 1024)
    freeze = _strict_json(freeze_bytes, "author freeze")
    gate = _strict_json(author_gate_bytes, "author pre-gate")
    if (
        gate.get("inference_allowed") is not False
        or gate.get("transport_preflight_allowed") is not False
        or freeze.get("author_inference_allowed") is not False
        or freeze.get("external_gate_required") is not True
        or freeze.get("status") != "PRE_INFRA_REVIEW"
    ):
        raise RuntimeError("V26.5.6 author gate/freeze unexpectedly authorizes work")
    actual_pins = {
        identity: {
            "path": str(path.relative_to(REPOSITORY)),
            "sha256": expected_sha, "size": expected_size,
        }
        for identity, (path, expected_sha, expected_size) in ARTIFACT_PINS.items()
    }
    expected_fields = {
        "runner_sha256": sha_bytes(runner_bytes),
        "author_pre_gate_sha256": sha_bytes(author_gate_bytes),
        "python_executable_sha256": PYTHON_SHA256,
        "artifact_pins": actual_pins,
        "request_body_probe_sha256": canonical_hash(request_body("opaque-native-probe")),
    }
    for key, value in expected_fields.items():
        if freeze.get(key) != value:
            raise RuntimeError(f"V26.5.6 freeze pin mismatch: {key}")
    return freeze, {
        "freeze_sha256": sha_bytes(freeze_bytes),
        "author_gate_sha256": sha_bytes(author_gate_bytes),
        "runner_sha256": sha_bytes(runner_bytes),
    }


def verify_freeze() -> dict[str, Any]:
    return _verify_author_checkpoint()[0]


def write_json_exclusive_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(
        payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True,
    ) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def run_live(endpoint: str, output: Path) -> dict[str, Any]:
    gate, external_gate_sha256 = _verify_external_gate_snapshot(endpoint, output)
    if not _valid_local_endpoint(endpoint):
        raise RuntimeError("external gate endpoint is not loopback HTTP")
    if not output.is_absolute() or not output.parent.is_dir() or output.exists():
        raise RuntimeError("authorized output must be an absent absolute path in an existing directory")
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
    write_json_exclusive_atomic(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--verify-freeze", action="store_true")
    actions.add_argument("--controls", action="store_true")
    parser.add_argument("--endpoint")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.verify_freeze:
        freeze = verify_freeze()
        print(json.dumps({
            "status": "PASS", "freeze_status": freeze["status"],
            "inference_allowed": False,
        }, sort_keys=True))
        return 0
    if not args.endpoint or not args.output:
        raise SystemExit("--controls requires --endpoint and --output")
    result = run_live(args.endpoint, Path(args.output))
    print(json.dumps(result["summary"], sort_keys=True))
    return 0 if result["status"] == "MODEL_BATCH_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
