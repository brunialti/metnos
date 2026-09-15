"""Read-only binding of an RM-0009 inventory to Git and working source bytes.

This is a preparatory consistency check, not a completeness, approval, sandbox,
signature, or runtime-certification verifier. Git and the checkout must be
trusted and quiescent; static path checks do not defend against hostile races.
Only content bytes are bound, not permission/executable modes or the meaning
of the remaining inventory fields. Reserved report claims are rejected.
Source ``scope`` and ``schema_version`` describe the specialist inventory;
they are explicitly unchecked, never merged into this checker's report.
It imports no runtime module and never opens installed data or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BINDING_FIELDS = frozenset({"source_commit", "input_sha256", "errors"})
_REPORT_FIELDS = frozenset({
    "valid", "certifies_completeness", "authorizes_implementation",
    "checked_files", "expected_commit", "inventory_sha256",
    "checks_file_mode", "inventory_payload_validated",
    "unchecked_inventory_fields",
})


def _is_source_path(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if any(char in value for char in "\\:*?[]"):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", "..", ".git"} for part in parts)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def load_inventory(path: Path) -> Any:
    return _decode_inventory(path.read_bytes())


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _decode_inventory(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
    )


def validate_inventory(
    inventory: Any,
    *,
    expected_commit: str,
    baseline_reader: Callable[[str], bytes],
    checkout_reader: Callable[[str], bytes],
) -> dict[str, Any]:
    """Verify declared file hashes, without claiming the inventory is complete.

    The expected commit comes from the coordinator, not from the inventory.
    Reader exceptions become failed checks; their text is not echoed because
    it may contain arbitrary source/output. Each path is checked independently.
    """
    errors: list[dict[str, str]] = []
    checked = 0

    def fail(code: str, path: str = "") -> None:
        errors.append({"code": code, "path": path})

    def report() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": "inventory_source_content_binding_only",
            "expected_commit": expected_commit,
            "valid": not errors,
            "checked_files": checked,
            "errors": errors,
            "certifies_completeness": False,
            "authorizes_implementation": False,
            "checks_file_mode": False,
            "inventory_payload_validated": False,
            "unchecked_inventory_fields": sorted(
                str(key) for key in inventory if key not in _BINDING_FIELDS
            ) if isinstance(inventory, dict) else [],
        }

    if not isinstance(expected_commit, str) or not _COMMIT.fullmatch(expected_commit):
        fail("invalid_expected_commit")
        return report()
    if not isinstance(inventory, dict):
        fail("inventory_not_object")
        return report()
    if inventory.get("source_commit") != expected_commit:
        fail("source_commit_mismatch")
        return report()
    if _REPORT_FIELDS.intersection(inventory):
        fail("reserved_report_claim_in_inventory")
    declared_errors = inventory.get("errors")
    if not isinstance(declared_errors, list) or declared_errors:
        fail("inventory_reports_errors_or_omits_status")
    hashes = inventory.get("input_sha256")
    if not isinstance(hashes, dict) or not hashes:
        fail("missing_input_hashes")
        return report()
    for path in sorted(hashes, key=str):
        digest = hashes[path]
        if not _is_source_path(path):
            # Do not echo control characters or non-path objects.
            fail("invalid_source_path")
            continue
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            fail("invalid_input_digest", path)
            continue
        valid_file = True
        for label, reader in (("baseline", baseline_reader), ("checkout", checkout_reader)):
            try:
                content = reader(path)
                if not isinstance(content, bytes):
                    raise ValueError("reader must return bytes")
                actual = hashlib.sha256(content).hexdigest()
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                fail(f"{label}_unreadable", path)
                valid_file = False
                continue
            if actual != digest:
                fail(f"{label}_digest_mismatch", path)
                valid_file = False
        if valid_file:
            checked += 1
    return report()


class GitBaseline:
    """Read regular tracked files at one fixed commit and in a trusted tree."""

    def __init__(self, repository: Path, commit: str):
        if not _COMMIT.fullmatch(commit):
            raise ValueError("a full commit ID is required")
        self.repository = repository.resolve(strict=True)
        if not self.repository.is_dir():
            raise ValueError("repository must be a directory")
        self.commit = commit
        self._commit_verified = False

    def _git(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.repository), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30,
        ).stdout

    def read_baseline(self, path: str) -> bytes:
        if not _is_source_path(path):
            raise ValueError("invalid source path")
        if not self._commit_verified:
            if self._git("cat-file", "-t", self.commit).strip() != b"commit":
                raise ValueError("source object must be a commit")
            self._commit_verified = True
        output = self._git("ls-tree", "-z", self.commit, "--", path)
        records = [record for record in output.split(b"\0") if record]
        if len(records) != 1:
            raise ValueError("source is not a single tracked file")
        metadata, actual_path = records[0].split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if (actual_path != path.encode("utf-8") or kind != b"blob"
                or mode not in {b"100644", b"100755"}):
            raise ValueError("source is not a regular tracked file")
        return self._git("cat-file", "blob", object_id.decode("ascii"))

    def read_checkout(self, path: str) -> bytes:
        if not _is_source_path(path):
            raise ValueError("invalid source path")
        current = self.repository
        for component in path.split("/"):
            current /= component
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("symlink source component")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("source must be a regular single-link file")
        return current.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        raw_inventory = args.inventory.read_bytes()
        inventory = _decode_inventory(raw_inventory)
        source = GitBaseline(args.repository, args.expected_commit)
        result = validate_inventory(
            inventory, expected_commit=args.expected_commit,
            baseline_reader=source.read_baseline,
            checkout_reader=source.read_checkout,
        )
        result["inventory_sha256"] = hashlib.sha256(raw_inventory).hexdigest()
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
