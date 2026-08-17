#!/usr/bin/env python3
"""Verify the V26.5.2 durable dependency manifest before any import."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
MANIFEST = HERE / "metnos_v2652_durable_manifest.json"
ALLOWED_ROLES = {
    "runtime_input", "evidence", "historical_source_only",
    "audit_dataset", "audit_tool", "audit_source",
}
IMPORTABLE_ROLES = {"runtime_input", "audit_tool"}
QUARANTINED_ROLES = {"historical_source_only", "audit_dataset", "audit_source"}
TMP_MARKER = bytes((47, 116, 109, 112, 47))
TRANSPORT_MARKERS = tuple(
    value.encode("ascii")
    for value in ("ur" + "llib", "http." + "client", "so" + "cket", "re" + "quests")
)
REQUIRED_POLICIES = {
    "internal/tools/request_analysis_lab/candidates/v2652/metnos_v2652_preimport_verifier.py":
        ("audit_tool", True),
    "internal/tools/request_analysis_lab/candidates/v2652/metnos_v2652_candidate_core.py":
        ("runtime_input", True),
    "internal/tools/request_analysis_lab/candidates/v265/metnos_v265_compact_adapter.py":
        ("runtime_input", True),
    "internal/tools/request_analysis_lab/candidates/v2651/metnos_v2651_self_contained_validator.py":
        ("runtime_input", True),
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


def inside_repository(path: Path) -> bool:
    try:
        path.relative_to(REPOSITORY)
        return True
    except ValueError:
        return False


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
        active_code_tmp_free = True
        audit_tool_transport_free = True
        if check_files and exists and role in {"runtime_input", "audit_tool"}:
            content = path.read_bytes()
            active_code_tmp_free = TMP_MARKER not in content
            if role == "audit_tool":
                audit_tool_transport_free = not any(
                    marker in content for marker in TRANSPORT_MARKERS
                )
        passed = all((
            fields_closed, identity_ok, relative_ok, unique, exists, digest_ok,
            role_ok, import_boolean, import_policy, source_quarantined,
            consumer_policy, active_code_tmp_free, audit_tool_transport_free,
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
            "active_code_tmp_free": active_code_tmp_free,
            "audit_tool_transport_free": audit_tool_transport_free,
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
    ]
    all_checks = checks + policy
    return {
        "version": "metnos.v26.5.2-preimport-verifier/0.1",
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
    manifest = json.loads(MANIFEST.read_text())
    return verify_document(
        manifest, check_files=True, manifest_sha256=sha(MANIFEST),
    )


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
