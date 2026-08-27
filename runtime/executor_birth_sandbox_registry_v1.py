"""The measured sandbox backend, frozen into the prepared set (RM-0008).

Group 2 left ``sandbox_registry`` in the admission context as an identity with
nothing behind it: on Linux the two programs that run a phase were whatever
``PATH`` and the current interpreter happened to name at that instant.

This module owns the measurement and its document, once.  The installer takes
it while it holds its single door and freezes it in the set; the runtime reads
it back under its own barrier and hands the runner an authenticated backend.
Neither side chooses a path afterwards, and a program replaced after the
measurement is a refusal, not a substitution.

The document is a measurement, not an operator opinion: the administrator does
not author it.  When the machine changes, the set is prepared again.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

SANDBOX_CONTAINER_BASENAME_V1 = "sandbox"
SANDBOX_REGISTRY_BASENAME_V1 = "registry.json"
MAXIMUM_SANDBOX_REGISTRY_BYTES_V1 = 16 * 1024

MEASURED_STATE_V1 = "measured"
UNAVAILABLE_STATE_V1 = "unavailable"


class SandboxRegistryError(RuntimeError):
    """The sandbox backend cannot be measured or read back."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest_program_v1(path: Path) -> str:
    """Digest one program through a handle that follows no final link."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SandboxRegistryError("sandbox_program_not_regular", path.name)
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def measure_sandbox_backend_v1() -> bytes:
    """Measure this machine's backend once and return the canonical document.

    A machine without a usable backend produces an explicit ``unavailable``
    document with the reason, never an empty one that would read as measured.
    """
    if os.name == "nt":
        return _canonical({
            "schema_version": 1, "platform": "windows",
            "state": UNAVAILABLE_STATE_V1,
            "reason": "windows_backend_not_measured",
            "programs": {},
        })
    if not sys.platform.startswith("linux"):
        return _canonical({
            "schema_version": 1, "platform": sys.platform,
            "state": UNAVAILABLE_STATE_V1,
            "reason": "platform_backend_unavailable",
            "programs": {},
        })
    found = shutil.which("bwrap")
    if not found:
        return _canonical({
            "schema_version": 1, "platform": "linux",
            "state": UNAVAILABLE_STATE_V1, "reason": "bwrap_absent",
            "programs": {},
        })
    programs = {}
    for label, candidate in (
        ("bwrap", Path(found)), ("interpreter", Path(sys.executable)),
    ):
        # The real program is named, never a link to it: that is what makes the
        # digest mean something when the runner checks it again.
        resolved = candidate.resolve()
        try:
            programs[label] = {
                "path": str(resolved), "sha256": _digest_program_v1(resolved),
            }
        except (OSError, SandboxRegistryError):
            return _canonical({
                "schema_version": 1, "platform": "linux",
                "state": UNAVAILABLE_STATE_V1,
                "reason": f"{label}_unreadable", "programs": {},
            })
    return _canonical({
        "schema_version": 1, "platform": "linux",
        "state": MEASURED_STATE_V1, "reason": "", "programs": programs,
    })


def _decoded_document_v1(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxRegistryError("sandbox_registry_unreadable") from exc
    if raw != _canonical(value):
        raise SandboxRegistryError("sandbox_registry_noncanonical")
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "platform", "state", "reason", "programs"
    } or value["schema_version"] != 1:
        raise SandboxRegistryError("sandbox_registry_invalid")
    if value["state"] not in {MEASURED_STATE_V1, UNAVAILABLE_STATE_V1}:
        raise SandboxRegistryError("sandbox_registry_invalid", "state")
    return value


def decode_sandbox_registry_v1(raw: bytes):
    """Return the typed backend the runner accepts, or ``None`` when absent.

    ``None`` is the honest answer for a machine measured without a backend: the
    runner already refuses with its own named code, so nothing here invents a
    fallback.
    """
    value = _decoded_document_v1(raw)
    if value["state"] == UNAVAILABLE_STATE_V1:
        if value["programs"]:
            raise SandboxRegistryError("sandbox_registry_invalid", "programs")
        return None
    if value["platform"] != "linux":
        # Only Linux is measured today; a measured document for any other
        # platform is a document this version cannot honour.
        raise SandboxRegistryError("sandbox_registry_unsupported_platform")
    programs = value["programs"]
    if not isinstance(programs, dict) or set(programs) != {"bwrap", "interpreter"}:
        raise SandboxRegistryError("sandbox_registry_invalid", "programs")
    for entry in programs.values():
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"}
                or not isinstance(entry["path"], str) or not entry["path"]
                or not isinstance(entry["sha256"], str)
                or len(entry["sha256"]) != 64):
            raise SandboxRegistryError("sandbox_registry_invalid", "program")
    from executor_birth_runner import LinuxSandboxRegistry

    return LinuxSandboxRegistry(
        bwrap_path=Path(programs["bwrap"]["path"]),
        bwrap_binary_hash=programs["bwrap"]["sha256"],
        interpreter_path=Path(programs["interpreter"]["path"]),
        interpreter_binary_hash=programs["interpreter"]["sha256"],
    )
