#!/usr/bin/env python3
"""One-shot, offline-only V26.5.4 validation worker.

This file is an executable protocol endpoint, not an importable Python API.
It accepts one closed JSON request on stdin, emits one closed JSON response,
and exits.  It contains no model or transport client.
"""
from __future__ import annotations

import sys


if __name__ != "__main__":
    raise RuntimeError("V26.5.4 worker is not importable")


def _deny_external_effects(event, _args):
    denied_prefixes = (
        "socket.", "http.client", "urllib.", "subprocess.", "ctypes.",
    )
    denied_events = {
        "os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn",
        "os.spawnve", "os.system",
    }
    if event in denied_events or event.startswith(denied_prefixes):
        raise RuntimeError(f"external effect denied by V26.5.4 worker: {event}")


sys.addaudithook(_deny_external_effects)

import json as _envelope_json


def run_once():
    """Consume exactly one request; all authority-bearing values stay local."""
    import ast
    import builtins
    import hashlib
    import importlib.metadata
    import json
    import math
    import os
    from pathlib import Path, PurePosixPath
    import pwd
    import stat
    import types
    import unicodedata

    protocol_version = "metnos.v26.5.4-worker-request/1.0"
    runtime_manifest_relative = (
        "internal/tools/request_analysis_lab/candidates/v2654/"
        "metnos_v2654_runtime_manifest.json"
    )
    runtime_manifest_sha256 = (
        "88d7b0096ab2e0c76164c0c3c98705e68cadf06ef2bd5698d353f2139f552391"
    )
    runtime_manifest_size = 1426
    expected_distributions = {"jsonschema": "4.10.3", "regex": "2026.3.32"}
    expected_python = (3, 12, 3)
    expected_unicode_version = "15.0.0"
    expected_artifacts = {
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
    allowed_read_paths = frozenset(
        {runtime_manifest_relative}
        | {metadata[1] for metadata in expected_artifacts.values()}
    )
    exact_imports = {
        "adapter": [
            ("from", "__future__", 0, (("annotations", None),)),
            ("import", (("copy", None),)),
            ("from", "typing", 0, (("Any", None),)),
        ],
        "validator": [
            ("from", "__future__", 0, (("annotations", None),)),
            ("import", (("hashlib", None),)),
            ("import", (("json", None),)),
            ("from", "typing", 0, (("Any", None),)),
            ("import", (("jsonschema", None),)),
        ],
    }
    allowed_import_roots = {
        "adapter": frozenset({"__future__", "copy", "typing"}),
        "validator": frozenset(
            {"__future__", "hashlib", "json", "jsonschema", "typing"}
        ),
    }
    denied_reflection_names = frozenset({
        "__builtins__", "__import__", "breakpoint", "compile", "delattr",
        "dir", "eval", "exec", "getattr", "globals", "input", "locals",
        "open", "setattr", "vars",
    })
    denied_subscript_keys = frozenset({
        "__builtins__", "__class__", "__dict__", "__globals__",
        "__import__", "__mro__", "__subclasses__",
    })

    repository = Path.cwd().resolve(strict=True)

    def sha_bytes(value):
        return hashlib.sha256(value).hexdigest()

    def reject_constant(token):
        raise RuntimeError(f"non-finite JSON constant denied: {token}")

    def json_unique(value, label):
        def closed_object(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise RuntimeError(f"duplicate JSON key in {label}: {key}")
                result[key] = item
            return result

        try:
            return json.loads(
                value, object_pairs_hook=closed_object,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise RuntimeError(f"invalid JSON in {label}") from error

    def read_fixed(relative, expected_size):
        if type(relative) is not str or relative not in allowed_read_paths:
            raise RuntimeError("path is not a fixed readable identity")
        if type(expected_size) is not int or not 1 <= expected_size <= 1_000_000:
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
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise RuntimeError("secure no-follow file API is unavailable")

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        file_flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            directory_flags |= os.O_CLOEXEC
            file_flags |= os.O_CLOEXEC
        directory_fds = []
        file_fd = None
        try:
            directory_fds.append(os.open(repository, directory_flags))
            for part in pure.parts[:-1]:
                directory_fds.append(
                    os.open(part, directory_flags, dir_fd=directory_fds[-1])
                )
            file_fd = os.open(
                pure.parts[-1], file_flags, dir_fd=directory_fds[-1]
            )
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise RuntimeError("artifact is not the expected regular file")
            chunks = []
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
            if (
                remaining
                or len(value) != expected_size
                or identity_before != identity_after
            ):
                raise RuntimeError("artifact changed while it was being read")
            return value
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for descriptor in reversed(directory_fds):
                os.close(descriptor)

    def read_manifest():
        value = read_fixed(runtime_manifest_relative, runtime_manifest_size)
        if sha_bytes(value) != runtime_manifest_sha256:
            raise RuntimeError("runtime manifest does not match worker pin")
        return value

    raw_request = sys.stdin.buffer.read(1_500_001)
    if len(raw_request) > 1_500_000:
        raise RuntimeError("protocol: request exceeds byte bound")
    request = json_unique(raw_request, "worker request")
    if type(request) is not dict or set(request) != {
        "version", "original_request", "frame",
    }:
        raise RuntimeError("protocol: request envelope is not closed")
    if request["version"] != protocol_version:
        raise RuntimeError("protocol: request version mismatch")
    original_request = request["original_request"]
    compact_frame = request["frame"]
    if type(original_request) is not str or len(original_request) > 100_000:
        raise RuntimeError("protocol: original_request must be a bounded string")
    if type(compact_frame) is not dict:
        raise RuntimeError("protocol: frame must be an exact JSON object")

    if sys.version_info[:3] != expected_python or sys.executable != "/usr/bin/python3":
        raise RuntimeError("environment: Python interpreter drift")
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    external_user_site = account_home / (
        f".local/lib/python{sys.version_info.major}.{sys.version_info.minor}/"
        "site-packages"
    )
    if not external_user_site.is_dir():
        raise RuntimeError("environment: fixed user package directory is absent")
    # Isolated mode deliberately omits the user site.  This one path is
    # derived from the OS account (never from the request or environment) and
    # is appended, not prepended, so the system jsonschema remains selected.
    # It supplies regex on this checkpoint host.  Distribution bytes are an
    # explicitly external trust root until a future environment freeze.
    sys.path.append(str(external_user_site))
    versions = {
        name: importlib.metadata.version(name)
        for name in expected_distributions
    }
    if versions != expected_distributions:
        raise RuntimeError(f"environment: dependency version drift: {versions}")
    if unicodedata.unidata_version != expected_unicode_version:
        raise RuntimeError("environment: Unicode database version drift")

    # These third-party modules are imported only after their installed
    # distribution versions have matched the fixed environment contract.
    # Their package bytes remain an external trusted-environment dependency;
    # the V26.5.4 checkpoint does not claim to freeze that environment.
    import jsonschema
    import regex

    if not Path(regex.__file__).resolve().is_relative_to(
        external_user_site.resolve()
    ):
        raise RuntimeError("environment: regex import origin drift")
    if not Path(jsonschema.__file__).resolve().is_relative_to(
        Path("/usr/lib/python3/dist-packages")
    ):
        raise RuntimeError("environment: jsonschema import origin drift")

    manifest_bytes = read_manifest()
    manifest = json_unique(manifest_bytes, "runtime manifest")
    if type(manifest) is not dict or set(manifest) != {"version", "artifacts"}:
        raise RuntimeError("manifest: envelope is not closed")
    if manifest["version"] != "metnos.v26.5.4-runtime-manifest/1.0":
        raise RuntimeError("manifest: version mismatch")
    artifacts = manifest["artifacts"]
    if type(artifacts) is not list or len(artifacts) != len(expected_artifacts):
        raise RuntimeError("manifest: artifact set has wrong cardinality")
    entries = {}
    for item in artifacts:
        if type(item) is not dict or set(item) != {
            "id", "kind", "path", "sha256", "size",
        }:
            raise RuntimeError("manifest: artifact entry is not closed")
        identity = item["id"]
        if type(identity) is not str or identity in entries:
            raise RuntimeError("manifest: artifact identity is invalid")
        if (
            type(item["kind"]) is not str
            or type(item["path"]) is not str
            or type(item["sha256"]) is not str
            or len(item["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in item["sha256"])
            or type(item["size"]) is not int
        ):
            raise RuntimeError("manifest: artifact metadata is invalid")
        entries[identity] = item
    if tuple(entries) != tuple(expected_artifacts):
        raise RuntimeError("manifest: artifact order or identity changed")

    verified_fixed_bytes = {}
    for identity, fixed in expected_artifacts.items():
        if read_manifest() != manifest_bytes:
            raise RuntimeError("manifest: changed during runtime load")
        item = entries[identity]
        if (item["kind"], item["path"]) != fixed:
            raise RuntimeError(f"manifest: fixed identity changed: {identity}")
        value = read_fixed(item["path"], item["size"])
        if sha_bytes(value) != item["sha256"]:
            raise RuntimeError(f"manifest: artifact hash mismatch: {identity}")
        verified_fixed_bytes[identity] = value

    registry = json_unique(verified_fixed_bytes["registry"], "registry")
    schema = json_unique(verified_fixed_bytes["schema"], "schema")
    if type(registry) is not dict or type(schema) is not dict:
        raise RuntimeError("runtime data root must be an object")
    try:
        verified_fixed_bytes["prompt"].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RuntimeError("prompt is not strict UTF-8") from error
    jsonschema.Draft202012Validator.check_schema(schema)
    schema_errors = list(
        jsonschema.Draft202012Validator(schema).iter_errors(compact_frame)
    )
    if schema_errors:
        return {
            "status": "evaluated_invalid",
            "stage": "schema",
            "codes": ["schema"],
        }

    modules = {}
    original_import = builtins.__import__
    for identity in ("adapter", "validator"):
        source = verified_fixed_bytes[identity]
        item = entries[identity]
        if sha_bytes(source) != item["sha256"]:
            raise RuntimeError(f"source changed before compile: {identity}")
        try:
            tree = ast.parse(source, filename=f"<verified:{identity}>")
        except (SyntaxError, ValueError) as error:
            raise RuntimeError(f"invalid verified Python source: {identity}") from error

        observed_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                observed_imports.append((
                    "import", tuple((alias.name, alias.asname) for alias in node.names),
                ))
            elif isinstance(node, ast.ImportFrom):
                observed_imports.append((
                    "from", node.module, node.level,
                    tuple((alias.name, alias.asname) for alias in node.names),
                ))
            if isinstance(node, ast.Name):
                if node.id in denied_reflection_names:
                    raise RuntimeError(f"source reflection denied: {identity}")
                if node.id.startswith("__") and node.id != "__metnos_registry_bytes__":
                    raise RuntimeError(f"source dunder name denied: {identity}")
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise RuntimeError(f"source dunder attribute denied: {identity}")
            elif isinstance(node, ast.Subscript):
                key = node.slice.value if isinstance(node.slice, ast.Constant) else None
                if isinstance(key, str) and (
                    key in denied_subscript_keys or key.startswith("__")
                ):
                    raise RuntimeError(f"source reflective subscript denied: {identity}")
            elif isinstance(node, ast.Call) and isinstance(
                node.func, (ast.Subscript, ast.Lambda)
            ):
                raise RuntimeError(f"source dynamic callable denied: {identity}")
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                raise RuntimeError(f"source global rebinding denied: {identity}")
        if observed_imports != exact_imports[identity]:
            raise RuntimeError(f"source import surface changed: {identity}")

        allowed_roots = allowed_import_roots[identity]

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                type(name) is not str
                or level != 0
                or name not in allowed_roots
            ):
                raise RuntimeError(f"source import denied: {identity}")
            return original_import(name, globals, locals, fromlist, level)

        safe_builtins = dict(vars(builtins))
        for denied_name in denied_reflection_names:
            safe_builtins.pop(denied_name, None)
        safe_builtins["__import__"] = guarded_import
        module = types.ModuleType(f"metnos_v2654_{identity}")
        filename = f"<verified:{identity}>"
        namespace = module.__dict__
        namespace["__file__"] = filename
        namespace["__package__"] = None
        namespace["__builtins__"] = safe_builtins
        if identity == "validator":
            namespace["__metnos_registry_bytes__"] = verified_fixed_bytes["registry"]

        # The only source execution sink in this worker.  Both identity and
        # source are local values from the fixed, freshly verified loop above.
        code = compile(source, filename, "exec", dont_inherit=True, optimize=0)
        exec(code, namespace)
        modules[identity] = module

    parts = regex.split(
        r"\b", original_request, flags=regex.WORD | regex.VERSION1,
    )
    segments = []
    cursor = 0
    for part in parts:
        if not part:
            continue
        start = original_request.find(part, cursor)
        if start < 0:
            raise RuntimeError("segmentation lost source alignment")
        end = start + len(part)
        cursor = end
        if part.isspace():
            continue
        segments.append({
            "id": len(segments) + 1,
            "text": part,
            "start_char": start,
            "end_char": end,
        })

    adapter = modules["adapter"]
    validator = modules["validator"]
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


_exit_code = 0
try:
    _result = run_once()
    _response = {
        "version": "metnos.v26.5.4-worker-response/1.0",
        "ok": True,
        "result": _result,
    }
except Exception as _error:
    _exit_code = 1
    _message = str(_error)
    _response = {
        "version": "metnos.v26.5.4-worker-response/1.0",
        "ok": False,
        "error": {
            "code": _message.split(":", 1)[0] if _message else "worker",
            "type": type(_error).__name__,
        },
    }

_encoded = _envelope_json.dumps(
    _response, ensure_ascii=False, allow_nan=False,
    separators=(",", ":"), sort_keys=True,
).encode("utf-8")
if len(_encoded) > 2_000_000:
    _encoded = b'{"error":{"code":"worker_output_bound","type":"RuntimeError"},"ok":false,"version":"metnos.v26.5.4-worker-response/1.0"}'
    _exit_code = 1
sys.stdout.buffer.write(_encoded + b"\n")
raise SystemExit(_exit_code)
