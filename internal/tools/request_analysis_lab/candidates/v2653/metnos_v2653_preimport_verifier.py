#!/usr/bin/env python3
"""Verify the V26.5.3 durable dependency manifest before any import."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
MANIFEST = HERE / "metnos_v2653_durable_manifest.json"
ALLOWED_ROLES = {
    "runtime_input", "evidence", "historical_source_only",
    "audit_dataset", "audit_tool", "audit_source",
}
IMPORTABLE_ROLES = {"runtime_input", "audit_tool"}
QUARANTINED_ROLES = {"historical_source_only", "audit_dataset", "audit_source"}
BANNED_TRANSPORT_ROOTS = {
    "aiohttp", "ctypes", "http", "httpx", "marshal", "pickle",
    "requests", "runpy", "socket", "subprocess", "urllib",
}
CORE_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2653/"
    "metnos_v2653_candidate_core.py"
)
VERIFIER_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2653/"
    "metnos_v2653_preimport_verifier.py"
)
RUNTIME_MANIFEST_RELATIVE = (
    "internal/tools/request_analysis_lab/candidates/v2653/"
    "metnos_v2653_runtime_manifest.json"
)
EXPECTED_RUNTIME_MANIFEST_SHA256 = (
    "14c1e2d6a666cef335d5567e550ec55db4af4c0cd46b59f9b8b344cf7e506d50"
)
EXPECTED_RUNTIME_ENTRIES = (
    (
        "adapter", "python_source",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact_adapter.py",
        "3375207bd7bbf6d99098d0ec2066d0c9f3eb6b76b34f1ccd450abaf89abecef1",
        5600,
    ),
    (
        "validator", "python_source",
        "internal/tools/request_analysis_lab/candidates/v2653/"
        "metnos_v2653_injected_validator.py",
        "66760987f5d2335c79114632affa5c04ffecd9ffe5f8e1a7adec7bc50b4b793d",
        31627,
    ),
    (
        "registry", "json_data",
        "internal/tools/request_analysis_lab/candidates/v2641/"
        "metnos_v2641_typed_registry.json",
        "448c058d805e141c871580253e815849b24a9999d98c505cab970fcd55d1e46f",
        9456,
    ),
    (
        "schema", "json_data",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact.schema.json",
        "76cd30411704c780e5866fd165f945bd3e773f0dab1b359e875d41f6eb54a921",
        13751,
    ),
    (
        "prompt", "utf8_data",
        "internal/tools/request_analysis_lab/candidates/v265/"
        "metnos_v265_compact.prompt.txt",
        "893db06f910acd48f8929251a89afaaaf630632b90589e0863948792643a7418",
        8154,
    ),
)
ACTIVE_IMPORT_ALLOWLIST = {
    CORE_RELATIVE: frozenset({
        "__future__", "argparse", "ast", "hashlib", "importlib.metadata",
        "json", "jsonschema", "math", "os", "pathlib", "regex", "stat",
        "sys", "types", "typing", "unicodedata",
    }),
    VERIFIER_RELATIVE: frozenset({
        "__future__", "ast", "hashlib", "json", "pathlib", "typing",
    }),
    "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py":
        frozenset({"__future__", "copy", "typing"}),
    "internal/tools/request_analysis_lab/candidates/v2653/metnos_v2653_injected_validator.py":
        frozenset({"__future__", "hashlib", "json", "jsonschema", "typing"}),
    "internal/tools/request_analysis_lab/candidates/v2651/metnos_v2651_contamination_auditor.py":
        frozenset({
            "__future__", "collections", "hashlib", "json", "pathlib", "re",
            "typing", "unicodedata",
        }),
}
REQUIRED_POLICIES = {
    VERIFIER_RELATIVE: ("audit_tool", True),
    CORE_RELATIVE: ("runtime_input", True),
    "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py":
        ("runtime_input", True),
    "internal/tools/request_analysis_lab/candidates/v2653/metnos_v2653_injected_validator.py":
        ("runtime_input", True),
    RUNTIME_MANIFEST_RELATIVE: ("runtime_input", False),
    "internal/tools/request_analysis_lab/candidates/v2641/metnos_v2641_typed_registry.json":
        ("runtime_input", False),
    "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.schema.json":
        ("runtime_input", False),
    "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact.prompt.txt":
        ("runtime_input", False),
    "internal/tools/request_analysis_lab/candidates/v2651/metnos_v2651_compact_mutation_fixture.json":
        ("runtime_input", False),
    "internal/tools/request_analysis_lab/candidates/v2651/metnos_v2651_contamination_auditor.py":
        ("audit_tool", True),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_unique(value: bytes, label: str) -> Any:
    """Decode UTF-8 JSON while rejecting duplicate object keys."""
    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {label}: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=closed_object)


def inside_repository(path: Path) -> bool:
    try:
        path.relative_to(REPOSITORY)
        return True
    except ValueError:
        return False


def transport_ast_safe(content: bytes, path: Path) -> bool:
    """Enforce exact imports and deny dynamic/external capabilities."""
    if path.suffix != ".py":
        return True
    try:
        relative = path.relative_to(REPOSITORY).as_posix()
    except ValueError:
        return False
    allowed_imports = ACTIVE_IMPORT_ALLOWLIST.get(relative)
    if allowed_imports is None:
        return False
    try:
        tree = ast.parse(content, filename=str(path))
    except (SyntaxError, ValueError):
        return False
    compile_calls = 0
    exec_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[0] in BANNED_TRANSPORT_ROOTS
                or alias.name not in allowed_imports
                for alias in node.names
            ):
                return False
        elif isinstance(node, ast.ImportFrom):
            if (
                node.level != 0
                or not node.module
                or node.module.split(".")[0] in BANNED_TRANSPORT_ROOTS
                or node.module not in allowed_imports
            ):
                return False
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in {"__import__", "eval", "getattr", "open"}:
                    return False
                if node.func.id == "compile":
                    compile_calls += 1
                elif node.func.id == "exec":
                    exec_calls += 1
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "import_module":
                    return False
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in {
                        "popen", "posix_spawn", "spawn", "spawnve", "system",
                    }
                ):
                    return False
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr == "modules"
        ):
            return False
    if relative == CORE_RELATIVE:
        return compile_calls == 1 and exec_calls == 1
    return compile_calls == 0 and exec_calls == 0


def runtime_contract_safe(
    entries: dict[str, dict[str, Any]], *, check_files: bool,
) -> bool:
    """Bind the five runtime identities to the durable manifest and core pin."""
    durable_runtime = entries.get(RUNTIME_MANIFEST_RELATIVE)
    if not isinstance(durable_runtime, dict):
        return False
    if durable_runtime.get("sha256") != EXPECTED_RUNTIME_MANIFEST_SHA256:
        return False
    runtime_path = REPOSITORY / RUNTIME_MANIFEST_RELATIVE
    try:
        document = json_unique(runtime_path.read_bytes(), "runtime manifest")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False
    if (
        type(document) is not dict
        or set(document) != {"version", "artifacts"}
        or document.get("version") != "metnos.v26.5.3-runtime-manifest/1.0"
        or type(document.get("artifacts")) is not list
        or len(document["artifacts"]) != len(EXPECTED_RUNTIME_ENTRIES)
    ):
        return False
    for item, expected in zip(document["artifacts"], EXPECTED_RUNTIME_ENTRIES):
        identity, kind, relative, digest, size = expected
        if (
            type(item) is not dict
            or set(item) != {"id", "kind", "path", "sha256", "size"}
            or (
                item.get("id"), item.get("kind"), item.get("path"),
                item.get("sha256"), item.get("size"),
            ) != expected
        ):
            return False
        durable = entries.get(relative)
        if (
            not isinstance(durable, dict)
            or durable.get("sha256") != digest
            or durable.get("role") != "runtime_input"
            or type(durable.get("import_allowed")) is not bool
            or durable.get("import_allowed") is not (kind == "python_source")
        ):
            return False
        if check_files:
            artifact = REPOSITORY / relative
            try:
                if artifact.stat().st_size != size or sha(artifact) != digest:
                    return False
            except OSError:
                return False
    if check_files:
        try:
            runtime_bytes = runtime_path.read_bytes()
            core_bytes = (REPOSITORY / CORE_RELATIVE).read_bytes()
            validator_bytes = (
                REPOSITORY / EXPECTED_RUNTIME_ENTRIES[1][2]
            ).read_bytes()
        except OSError:
            return False
        if hashlib.sha256(runtime_bytes).hexdigest() != EXPECTED_RUNTIME_MANIFEST_SHA256:
            return False
        if EXPECTED_RUNTIME_MANIFEST_SHA256.encode("ascii") not in core_bytes:
            return False
        if (
            b"__metnos_registry_bytes__" not in validator_bytes
            or b"read_bytes" in validator_bytes
            or b"from pathlib" in validator_bytes
            or b"import pathlib" in validator_bytes
        ):
            return False
    return True


def verify_document(
    manifest: dict[str, Any], *, check_files: bool,
    manifest_sha256: str | None = None,
) -> dict[str, Any]:
    authorization = manifest.get("authorization")
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        checks.append({"id": "artifact_array", "pass": False})

    entries: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            checks.append({"id": f"artifact:{index}", "pass": False})
            continue
        fields_closed = set(item) == {"path", "sha256", "role", "import_allowed"}
        raw = item.get("path")
        digest = item.get("sha256")
        role = item.get("role")
        import_allowed = item.get("import_allowed")
        identity_ok = (
            isinstance(raw, str)
            and isinstance(digest, str)
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
        )
        pure = PurePosixPath(raw) if isinstance(raw, str) else None
        relative_ok = bool(
            pure is not None and not pure.is_absolute() and ".." not in pure.parts
        )
        unique = isinstance(raw, str) and raw not in seen
        if isinstance(raw, str):
            seen.add(raw)
        path = (REPOSITORY / raw).resolve() if relative_ok else REPOSITORY
        exists = bool(
            not check_files
            or (relative_ok and path.is_file() and inside_repository(path))
        )
        digest_ok = bool(
            not check_files
            or (identity_ok and exists and sha(path) == digest)
        )
        role_ok = role in ALLOWED_ROLES
        import_boolean = type(import_allowed) is bool
        import_policy = bool(
            import_boolean
            and (import_allowed is False or role in IMPORTABLE_ROLES)
        )
        source_quarantined = bool(
            import_boolean
            and (
                role not in QUARANTINED_ROLES
                or import_allowed is False
            )
        )
        expected = REQUIRED_POLICIES.get(raw) if isinstance(raw, str) else None
        consumer_policy = bool(
            expected is None or (role, import_allowed) == expected
        )
        transport_syntax_safe = True
        if (
            check_files and exists and import_allowed is True
            and role in {"runtime_input", "audit_tool"}
        ):
            content = path.read_bytes()
            transport_syntax_safe = transport_ast_safe(content, path)
        passed = all((
            fields_closed, identity_ok, relative_ok, unique, exists, digest_ok,
            role_ok, import_boolean, import_policy, source_quarantined,
            consumer_policy, transport_syntax_safe,
        ))
        checks.append({
            "id": raw if isinstance(raw, str) else f"artifact:{index}",
            "pass": passed,
            "fields_closed": fields_closed,
            "identity": identity_ok,
            "relative": relative_ok,
            "unique": unique,
            "exists": exists,
            "sha256": digest_ok,
            "role": role_ok,
            "import_boolean": import_boolean,
            "import_policy": import_policy,
            "source_quarantined": source_quarantined,
            "consumer_policy": consumer_policy,
            "transport_syntax_safe": transport_syntax_safe,
        })
        if isinstance(raw, str):
            entries[raw] = item

    policy = [
        {
            "id": "repository_relative_manifest",
            "pass": manifest.get("repository_relative") is True,
        },
        {
            "id": "preimport_required",
            "pass": manifest.get("preimport_verification_required") is True,
        },
        {
            "id": "authorization_closed",
            "pass": (
                isinstance(authorization, dict)
                and set(authorization) == {"freeze", "gate", "network", "inference"}
                and all(value is False for value in authorization.values())
            ),
        },
        {
            "id": "remaining_blockers_visible",
            "pass": len(manifest.get("remaining_blockers", [])) == 3,
        },
        {
            "id": "required_consumer_policy_complete",
            "pass": all(
                relative in entries
                and (entries[relative].get("role"),
                     entries[relative].get("import_allowed")) == expected
                for relative, expected in REQUIRED_POLICIES.items()
            ),
        },
        {
            "id": "runtime_contract_closed",
            "pass": runtime_contract_safe(entries, check_files=check_files),
        },
    ]
    all_checks = checks + policy
    return {
        "version": "metnos.v26.5.3-preimport-verifier/0.1",
        "network_calls": 0,
        "dynamic_imports": 0,
        "manifest_sha256": manifest_sha256,
        "summary": {
            "tests": len(all_checks),
            "passed": sum(item["pass"] for item in all_checks),
            "failed": sum(not item["pass"] for item in all_checks),
            "artifacts": len(artifacts),
        },
        "remaining_blockers": manifest.get("remaining_blockers", []),
        "checks": all_checks,
    }


def verify() -> dict[str, Any]:
    manifest = json_unique(MANIFEST.read_bytes(), "durable manifest")
    if type(manifest) is not dict:
        raise ValueError("durable manifest root must be an object")
    return verify_document(
        manifest, check_files=True, manifest_sha256=sha(MANIFEST),
    )


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
