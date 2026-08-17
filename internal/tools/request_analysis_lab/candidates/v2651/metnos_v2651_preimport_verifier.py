#!/usr/bin/env python3
"""Verify the V26.5.1 durable dependency manifest before any import."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[4]
MANIFEST = HERE / "metnos_v2651_durable_manifest.json"
ALLOWED_ROLES = {
    "runtime_input", "evidence", "historical_source_only",
    "audit_dataset", "audit_tool", "audit_source",
}
IMPORTABLE_ROLES = {"runtime_input", "audit_tool"}
QUARANTINED_ROLES = {"historical_source_only", "audit_dataset", "audit_source"}
TRANSPORT_MARKERS = (b"urllib", b"http.client", b"socket", b"requests")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside_repository(path: Path) -> bool:
    try:
        path.relative_to(REPOSITORY)
        return True
    except ValueError:
        return False


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text())
    authorization = manifest.get("authorization")
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in manifest["artifacts"]:
        raw = item["path"]
        pure = PurePosixPath(raw)
        path = (REPOSITORY / raw).resolve()
        relative_ok = not pure.is_absolute() and ".." not in pure.parts
        unique = raw not in seen
        seen.add(raw)
        exists = path.is_file() and inside_repository(path)
        digest_ok = exists and sha(path) == item["sha256"]
        role_ok = item["role"] in ALLOWED_ROLES
        import_policy = (
            item["import_allowed"] is False
            or item["role"] in IMPORTABLE_ROLES
        )
        source_quarantined = (
            item["role"] not in QUARANTINED_ROLES
            or item["import_allowed"] is False
        )
        active_code_tmp_free = True
        audit_tool_transport_free = True
        if exists and item["role"] in {"runtime_input", "audit_tool"}:
            content = path.read_bytes()
            active_code_tmp_free = b"/tmp/" not in content
            if item["role"] == "audit_tool":
                audit_tool_transport_free = not any(
                    marker in content for marker in TRANSPORT_MARKERS
                )
        checks.append({
            "id": raw,
            "pass": all((relative_ok, unique, exists, digest_ok, role_ok,
                         import_policy, source_quarantined,
                         active_code_tmp_free, audit_tool_transport_free)),
            "relative": relative_ok,
            "unique": unique,
            "exists": exists,
            "sha256": digest_ok,
            "role": role_ok,
            "import_policy": import_policy,
            "source_quarantined": source_quarantined,
            "active_code_tmp_free": active_code_tmp_free,
            "audit_tool_transport_free": audit_tool_transport_free,
        })

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
    ]
    all_checks = checks + policy
    return {
        "version": "metnos.v26.5.1-preimport-verifier/0.9",
        "network_calls": 0,
        "dynamic_imports": 0,
        "manifest_sha256": sha(MANIFEST),
        "summary": {
            "tests": len(all_checks),
            "passed": sum(item["pass"] for item in all_checks),
            "failed": sum(not item["pass"] for item in all_checks),
            "artifacts": len(checks),
        },
        "remaining_blockers": manifest["remaining_blockers"],
        "checks": all_checks,
    }


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["summary"]["failed"] == 0 else 1)
