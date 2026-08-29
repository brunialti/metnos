#!/usr/bin/env python3
"""Fail-closed source-root gate for the private public-release publisher.

The reviewed roots are arguments owned by ``publish-public.sh``.  This tool
only implements the independent byte census, the exact public pin rewrite and
the final Git-index comparison; it never chooses or approves a new root.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SCAN_ROOTS = ("runtime", "install", "scripts", "executors")
SOURCE_REVIEW_DOMAIN = (
    b"metnos.executor-birth.closed-python-source-review/v1\0"
)
PIN_LINE = re.compile(
    rb'(?m)^_?BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = '
    rb'(?:(?:"sha256:" \+ "0" \* 64)|(?:"sha256:[0-9a-f]{64}"))$'
)
PIN_PLACEHOLDER = (
    b'BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:' + b"0" * 64 + b'"'
)
PIN_LITERAL = re.compile(
    rb'(?m)^(?P<name>_?BIRTH_CLOSED_SOURCE_REVIEW_SHA256) = '
    rb'"(?P<value>sha256:[0-9a-f]{64})"$'
)


def _fail(message: str) -> None:
    raise SystemExit(f"ABORT: RM-0008 source-review gate: {message}")


def _filesystem_sources(tree: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for base in SCAN_ROOTS:
        directory = tree / base
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            if path.is_file() and "__pycache__" not in path.parts:
                result[path.relative_to(tree).as_posix()] = path.read_bytes()
    return result


def _indexed_sources(tree: Path) -> dict[str, bytes]:
    listing = subprocess.run(
        ("git", "-C", str(tree), "ls-files", "--stage", "-z", "--",
         *SCAN_ROOTS),
        check=True,
        capture_output=True,
    ).stdout
    records: list[tuple[str, str]] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        metadata, separator, path_bytes = raw.partition(b"\t")
        if not separator:
            _fail("malformed public Git index record")
        fields = metadata.decode("ascii").split()
        if len(fields) != 3:
            _fail("malformed public Git index metadata")
        git_mode, object_id, stage = fields
        relative = path_bytes.decode("utf-8")
        if not relative.endswith(".py"):
            continue
        if "__pycache__" in relative.split("/"):
            continue
        if stage != "0" or git_mode not in {"100644", "100755"}:
            _fail(f"non-regular or unmerged public Python path: {relative}")
        records.append((relative, object_id))

    process = subprocess.Popen(
        ("git", "-C", str(tree), "cat-file", "--batch"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    result: dict[str, bytes] = {}
    try:
        for relative, object_id in records:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().rstrip(b"\n").split()
            if len(header) != 3 or header[1] != b"blob":
                _fail(f"public Python index object is not a blob: {relative}")
            size = int(header[2])
            content = process.stdout.read(size)
            if len(content) != size or process.stdout.read(1) != b"\n":
                _fail(f"truncated public Python index blob: {relative}")
            result[relative] = content
    finally:
        process.stdin.close()
        if process.wait() != 0:
            _fail("git cat-file failed while reading the public index")
    return result


def _source_root(sources: dict[str, bytes]) -> str:
    digest = hashlib.sha256(SOURCE_REVIEW_DOMAIN)
    for relative, content in sorted(
        sources.items(), key=lambda item: item[0].encode("utf-8")
    ):
        normalized = PIN_LINE.sub(PIN_PLACEHOLDER, content)
        encoded_path = relative.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(normalized).to_bytes(8, "big"))
        digest.update(hashlib.sha256(normalized).digest())
    return f"sha256:{digest.hexdigest()}"


def _require_root(
    sources: dict[str, bytes], expected: str, expected_count: int, label: str,
) -> None:
    if len(sources) != expected_count:
        _fail(
            f"{label} Python count {len(sources)} != reviewed {expected_count}"
        )
    observed = _source_root(sources)
    if observed != expected:
        _fail(f"{label} root {observed} != reviewed {expected}")


def _literal(tree: Path, path: Path, name: bytes) -> str:
    matches = [
        match for match in PIN_LITERAL.finditer(path.read_bytes())
        if match.group("name") == name
    ]
    if len(matches) != 1:
        _fail(f"expected exactly one pin literal in {path.relative_to(tree)}")
    return matches[0].group("value").decode("ascii")


def _main() -> None:
    if len(sys.argv) not in {5, 6}:
        _fail("usage: MODE TREE EXPECTED COUNT [INPUT_PIN]")
    mode, raw_tree, expected, raw_count = sys.argv[1:5]
    input_pin = sys.argv[5] if len(sys.argv) == 6 else None
    tree = Path(raw_tree).resolve()
    expected_count = int(raw_count)
    guard_path = tree / "runtime" / "contract_boundary_guard.py"
    admin_path = tree / "runtime" / "executor_birth_admin_preflight.py"

    if mode == "private-fs":
        if input_pin is not None:
            _fail("private mode does not accept INPUT_PIN")
        _require_root(
            _filesystem_sources(tree), expected, expected_count,
            "private filesystem",
        )
        if _literal(
            tree, guard_path, b"BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
        ) != expected:
            _fail("private guard pin differs from the reviewed root")
        if _literal(
            tree, admin_path, b"_BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
        ) != expected:
            _fail("private admin pin differs from the reviewed root")
        inventory_path = (
            tree / "internal/reports/rm0007-m4-boundary-inventory.json"
        )
        try:
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "cannot read the private boundary inventory: "
                f"{type(exc).__name__}"
            )
        if inventory.get("source_census") != expected:
            _fail("private boundary inventory pin differs from the reviewed root")
        return

    if mode == "public-fs-pin":
        if input_pin is None:
            _fail("public pinning mode requires INPUT_PIN")
        _require_root(
            _filesystem_sources(tree), expected, expected_count,
            "public filesystem before pinning",
        )
        targets = (
            (guard_path, b"BIRTH_CLOSED_SOURCE_REVIEW_SHA256"),
            (admin_path, b"_BIRTH_CLOSED_SOURCE_REVIEW_SHA256"),
        )
        for path, name in targets:
            if _literal(tree, path, name) != input_pin:
                _fail(f"unexpected input pin in {path.relative_to(tree)}")
            replacement = name + b' = "' + expected.encode("ascii") + b'"'
            pattern = re.compile(
                rb"(?m)^" + re.escape(name)
                + rb' = "sha256:[0-9a-f]{64}"$'
            )
            updated, count = pattern.subn(replacement, path.read_bytes())
            if count != 1:
                _fail(
                    f"pin replacement count is {count} in "
                    f"{path.relative_to(tree)}"
                )
            path.write_bytes(updated)
        _require_root(
            _filesystem_sources(tree), expected, expected_count,
            "public filesystem after pinning",
        )
        for path, name in targets:
            if _literal(tree, path, name) != expected:
                _fail(
                    f"public pin differs from reviewed root in "
                    f"{path.relative_to(tree)}"
                )
        return

    if mode == "public-index":
        if input_pin is not None:
            _fail("public index mode does not accept INPUT_PIN")
        indexed = _indexed_sources(tree)
        _require_root(indexed, expected, expected_count, "public Git index")
        if _filesystem_sources(tree) != indexed:
            _fail("public Python filesystem and final Git index differ")
        if _literal(
            tree, guard_path, b"BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
        ) != expected:
            _fail("indexed public guard pin differs from the reviewed root")
        if _literal(
            tree, admin_path, b"_BIRTH_CLOSED_SOURCE_REVIEW_SHA256",
        ) != expected:
            _fail("indexed public admin pin differs from the reviewed root")
        return

    _fail(f"unknown gate mode: {mode}")


if __name__ == "__main__":
    _main()
