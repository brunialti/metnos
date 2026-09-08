#!/usr/bin/env python3
"""Audit public bytes and the final index, without rewriting signed payloads.

The owner's public attribution/name is allowed by CLAUDE.md section 7.5.
Private account/machine identifiers are not generally allowed. Four explicitly
reviewed, already-public signed examples are bound to their exact payloads.
See internal/reports/publication-gii-20260908.md for the review rationale.
"""
from __future__ import annotations

import hashlib
import collections
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_FILE_BYTES = 128 * 1024 * 1024
SENSITIVE_NAMES = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\..*)?|google_token\.json|.*client_secret.*|"
    r"[^/]+_priv\.bin|credentials\.json|secrets\.json|"
    r"[^/]+\.(?:age|key|pem|p12|pfx))$"
)
RULES = (
    ("personal-identity", re.compile(
        rb"(?i)(?:iacopo|silvia|matteo|metnos_roberto|mykleos|knowcastle|tiscali|"
        rb"pc-roberto|telegram:roberto|alice_brunialti|roberto@host\.local|"
        rb"roberto\.brunialti@)"
    )),
    ("personal-path", re.compile(
        rb"(?i)(?:/home/roberto(?:/|\b)|[a-z]:\\users\\roberto(?:\\|\b))"
    )),
    ("private-service", re.compile(
        rb"(?i)(?:chat\.metnos\.com|cloudflared|"
        rb"(?:cloudflare.{0,80}tunnel)|(?:tunnel.{0,80}cloudflare)|"
        rb"imap\.register\.it|authsmtp\.securemail\.pro|securemail\.pro)"
    )),
    ("access-token", re.compile(
        rb"(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|"
        rb"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})"
    )),
    ("private-key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("private-ipv4-host", re.compile(
        rb"(?<![0-9])(?:10\.(?:[0-9]{1,3}\.){2}[1-9][0-9]{0,2}|"
        rb"172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[1-9][0-9]{0,2}|"
        rb"192\.168\.[0-9]{1,3}\.[1-9][0-9]{0,2})(?![0-9])"
    )),
    ("private-ipv6", re.compile(
        rb"(?i)(?<![0-9a-f])fd[0-9a-f]{2}:(?:[0-9a-f]{0,4}:){1,}[0-9a-f]{0,4}"
    )),
)
EMAIL = re.compile(rb"(?i)(?<![A-Z0-9._%+-])([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})(?![A-Z0-9.-])")
ALLOWED_EMAIL_DOMAINS = frozenset({
    b"example.com", b"example.net", b"example.org", b"users.noreply.github.com",
})
PUBLIC_PROJECT_EMAILS = frozenset({
    b"admin@metnos.com", b"metnos@metnos.com", b"synt@metnos.com",
    b"contact@metnos.com", b"importer@metnos.com", b"synt@metnos.local",
})
# These are examples in unchanged signed files of public commit 005dd002.
# A new file, a changed payload, or any other sensitive value is NOT covered.
REVIEWED_EXAMPLES = {
    "executors/send_messages/send_messages.py": (
        "69d00fe4753f7e97dabb3d28820ea54497a373c630c83fdee9b380732ef4f807",
        (b"'metnos_roberto'|'mykleos'",),
    ),
    "executors/get_processes/get_processes.py": (
        "e24233c8a21b01d31d91f31b36c0b89c61ce2e4a9ca70ea00edc460d4c8f3d9f",
        (b"pc-roberto",),
    ),
    "executors/create_events/manifest.toml": (
        "177d373dafea0e9c372d3bee676b12f53dd6212be706ecbd001ed5a07e63f96b",
        (b"alice@co.com", b"bob@co.com"),
    ),
    "executors/read_messages/manifest.toml": (
        "278d057a0980f37b58e897804290aee4320035adbdb769e59d3ca04359c8fedf",
        (b"noreply@amazon.com", b"orders@amazon.it"),
    ),
}


def _public_email(match: re.Match[bytes]) -> bool:
    # A leading '+' is conventional in HTTP User-Agent contact URLs.
    address = match.group(0).lower().lstrip(b"+")
    domain = match.group(2).lower()
    return address in PUBLIC_PROJECT_EMAILS or any(
        domain == allowed or domain.endswith(b"." + allowed)
        for allowed in ALLOWED_EMAIL_DOMAINS
    )


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    path: str


def _check_blob(relative: str, content: bytes) -> list[Finding]:
    reviewed = content
    example = REVIEWED_EXAMPLES.get(relative)
    if example is not None and hashlib.sha256(content).hexdigest() == example[0]:
        for value in example[1]:
            reviewed = reviewed.replace(value, b"reviewed-public-example")
    findings = [
        Finding(rule, relative) for rule, pattern in RULES
        if pattern.search(reviewed)
    ]
    if any(not _public_email(match) for match in EMAIL.finditer(reviewed)):
        findings.append(Finding("email-domain", relative))
    token = os.environ.get("GHTOKEN", "").encode()
    if token and token in content:
        findings.append(Finding("active-publish-token", relative))
    return findings


def _filesystem(root: Path) -> tuple[dict[str, bytes], list[Finding]]:
    blobs: dict[str, bytes] = {}
    findings: list[Finding] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names[:] = sorted(name for name in names if name != ".git")
        base = Path(directory)
        for name in [*names, *sorted(files)]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
            ):
                findings.append(Finding("non-regular-path", relative))
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_size > MAX_FILE_BYTES:
                findings.append(Finding("file-too-large", relative))
                continue
            if SENSITIVE_NAMES.search(relative):
                findings.append(Finding("sensitive-filename", relative))
            content = path.read_bytes()
            blobs[relative] = content
            findings.extend(_check_blob(relative, content))
    return blobs, findings


def _index(root: Path) -> tuple[dict[str, bytes], list[Finding]]:
    listing = subprocess.run(
        ("git", "-C", str(root), "ls-files", "--stage", "-z"),
        check=True, capture_output=True,
    ).stdout
    records: list[tuple[str, str]] = []
    findings: list[Finding] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        metadata, separator, path_bytes = raw.partition(b"\t")
        if not separator:
            raise SystemExit("GII gate: malformed Git index record")
        mode, object_id, stage = metadata.decode("ascii").split()
        relative = path_bytes.decode("utf-8")
        if mode not in {"100644", "100755"} or stage != "0":
            findings.append(Finding("non-regular-index-entry", relative))
            continue
        if SENSITIVE_NAMES.search(relative):
            findings.append(Finding("sensitive-filename", relative))
        records.append((relative, object_id))
    blobs: dict[str, bytes] = {}
    process = subprocess.Popen(
        ("git", "-C", str(root), "cat-file", "--batch"),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for relative, object_id in records:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().rstrip(b"\n").split()
            if len(header) != 3 or header[1] != b"blob":
                raise SystemExit(f"GII gate: index object is not a blob: {relative}")
            size = int(header[2])
            if size > MAX_FILE_BYTES:
                findings.append(Finding("file-too-large", relative))
            content = process.stdout.read(size)
            if len(content) != size or process.stdout.read(1) != b"\n":
                raise SystemExit(f"GII gate: truncated index blob: {relative}")
            blobs[relative] = content
            findings.extend(_check_blob(relative, content))
    finally:
        process.stdin.close()
        if process.wait() != 0:
            raise SystemExit("GII gate: git cat-file failed")
    return blobs, findings


def _fingerprints(blobs: dict[str, bytes]) -> dict[str, str]:
    return {path: hashlib.sha256(content).hexdigest() for path, content in blobs.items()}


def main() -> int:
    if len(sys.argv) < 2 or any(
        argument not in {"--index", "--summary"} for argument in sys.argv[2:]
    ):
        raise SystemExit("usage: gii-gate TREE [--index] [--summary]")
    root = Path(sys.argv[1]).resolve()
    filesystem, findings = _filesystem(root)
    if "--index" in sys.argv[2:]:
        indexed, index_findings = _index(root)
        findings.extend(index_findings)
        if _fingerprints(filesystem) != _fingerprints(indexed):
            findings.append(Finding("filesystem-index-divergence", "."))
    unique = sorted(set(findings), key=lambda item: (item.path, item.rule))
    if "--summary" in sys.argv[2:]:
        by_rule = collections.Counter(item.rule for item in unique)
        by_root = collections.Counter(item.path.split("/", 1)[0] for item in unique)
        for key, value in sorted(by_rule.items()):
            print(f"rule={key} findings={value}")
        for key, value in sorted(by_root.items()):
            print(f"root={key} findings={value}")
    else:
        for finding in unique:
            print(f"{finding.rule}: {finding.path}")
    print(f"GII findings: {len(unique)}")
    return 1 if unique else 0


if __name__ == "__main__":
    raise SystemExit(main())
