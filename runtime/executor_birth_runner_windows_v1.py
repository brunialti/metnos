# SPDX-License-Identifier: AGPL-3.0-only
"""Strict wire adapter for the RM-0008 Windows Birth sandbox helper."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import AbstractSet, Sequence


SCHEMA_VERSION = 1
BACKEND = "windows-appcontainer-job-v1"
PROFILE_NAME = "Metnos.ExecutorBirth.V1"
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
PROCESS_LIMIT = 32
STDOUT_LIMIT_BYTES = 1024 * 1024
STDERR_LIMIT_BYTES = 1024 * 1024
HELPER_RESPONSE_LIMIT_BYTES = 3 * 1024 * 1024
_PHASES = frozenset({"candidate", "reference", "equivalence"})
_STATUSES = frozenset({"passed", "failed", "test_environment_unavailable"})
_TOP_KEYS = frozenset({
    "schema_version", "request_id", "candidate_id", "status", "error_code",
    "exit_code", "stdout_base64", "stderr_base64", "stdout_bytes",
    "stderr_bytes", "stdout_truncated", "stderr_truncated", "elapsed_ms",
    "attestation",
})
_ATTESTATION_KEYS = frozenset({
    "backend", "helper_binary_hash", "runtime_binary_hash", "profile_name", "appcontainer_sid",
    "network_capability", "assigned_before_resume", "active_processes",
    "tree_empty", "termination_attested", "memory_limit_bytes",
    "process_limit", "stdout_limit_bytes", "stderr_limit_bytes",
})


class WindowsBirthHelperError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WindowsBirthHelperResult:
    status: str
    error_code: str | None
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    elapsed_ms: int
    stdout_truncated: bool
    stderr_truncated: bool
    attestation: dict[str, object]


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise WindowsBirthHelperError(f"{field}_invalid")
    try:
        bytes.fromhex(value[7:])
    except ValueError as exc:
        raise WindowsBirthHelperError(f"{field}_invalid") from exc
    if value != value.lower():
        raise WindowsBirthHelperError(f"{field}_invalid")
    return value


def helper_binary_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _bounded_transport(command: Sequence[str], request: bytes, timeout_s: float) -> tuple[int, bytes]:
    """Capture the trusted helper without an unbounded communicate buffer."""
    process = subprocess.Popen(tuple(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env={}, creationflags=0x08000000)
    assert process.stdin is not None and process.stdout is not None
    captured = bytearray()
    overflow = threading.Event()

    def read_stdout() -> None:
        while chunk := process.stdout.read(64 * 1024):
            remaining = HELPER_RESPONSE_LIMIT_BYTES + 1 - len(captured)
            if remaining > 0:
                captured.extend(chunk[:remaining])
            if len(captured) > HELPER_RESPONSE_LIMIT_BYTES:
                overflow.set()
                try:
                    process.kill()
                except OSError:
                    pass
                return

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        process.stdin.write(request)
        process.stdin.close()
        process.wait(timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        try:
            process.kill()
        except OSError:
            pass
        process.wait(timeout=2.0)
        raise WindowsBirthHelperError("helper_transport_unavailable") from exc
    reader.join(timeout=2.0)
    if reader.is_alive():
        raise WindowsBirthHelperError("helper_transport_unavailable")
    if overflow.is_set():
        raise WindowsBirthHelperError("helper_response_too_large")
    return process.returncode, bytes(captured)


def _entrypoint(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise WindowsBirthHelperError("entrypoint_invalid")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise WindowsBirthHelperError("entrypoint_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in path.parts):
        raise WindowsBirthHelperError("entrypoint_invalid")
    return path.as_posix()


def validate_private_layout(private_root: Path, entrypoint: str) -> str:
    """Validate the acquired V1 tree without following any candidate link."""
    if not isinstance(private_root, Path) or not private_root.is_absolute():
        raise WindowsBirthHelperError("private_root_invalid")
    try:
        if private_root.is_symlink() or not private_root.is_dir():
            raise WindowsBirthHelperError("private_root_invalid")
        names = {item.name for item in private_root.iterdir()}
        if names != {"candidate", "work"}:
            raise WindowsBirthHelperError("private_layout_invalid")
        candidate = private_root / "candidate"
        work = private_root / "work"
        if candidate.is_symlink() or work.is_symlink() or not candidate.is_dir() or not work.is_dir():
            raise WindowsBirthHelperError("private_layout_invalid")
        folded: set[str] = set()
        for tree in (candidate, work):
            for item in tree.rglob("*"):
                if item.is_symlink():
                    raise WindowsBirthHelperError("private_tree_link")
                relative = item.relative_to(private_root).as_posix()
                key = relative.casefold()
                if key in folded:
                    raise WindowsBirthHelperError("private_tree_case_collision")
                folded.add(key)
        relative = _entrypoint(entrypoint)
        if not relative.endswith(".py"):
            raise WindowsBirthHelperError("entrypoint_invalid")
        target = candidate.joinpath(*PurePosixPath(relative).parts)
        if target.is_symlink() or not target.is_file():
            raise WindowsBirthHelperError("entrypoint_invalid")
    except OSError as exc:
        raise WindowsBirthHelperError("private_layout_unavailable") from exc
    return relative


def _arguments(values: object) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or len(values) > 32:
        raise WindowsBirthHelperError("arguments_invalid")
    result: list[str] = []
    for value in values:
        if (not isinstance(value, str) or "\x00" in value
                or len(value.encode("utf-8")) > 4096):
            raise WindowsBirthHelperError("arguments_invalid")
        result.append(value)
    return result


def canonical_request(*, request_id: str, candidate_id: str, phase: str,
                      private_root: Path, entrypoint: str,
                      arguments: Sequence[str]) -> bytes:
    request_id = _digest(request_id, "request_id")
    candidate_id = _digest(candidate_id, "candidate_id")
    if phase not in _PHASES:
        raise WindowsBirthHelperError("phase_invalid")
    if not isinstance(private_root, Path) or not private_root.is_absolute():
        raise WindowsBirthHelperError("private_root_invalid")
    value = {
        "arguments": _arguments(arguments),
        "candidate_id": candidate_id,
        "entrypoint": validate_private_layout(private_root, entrypoint),
        "phase": phase,
        "private_root": str(private_root),
        "request_id": request_id,
        "schema_version": SCHEMA_VERSION,
    }
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _loads_unique_canonical(raw: bytes) -> dict[str, object]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise WindowsBirthHelperError("response_duplicate_key")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WindowsBirthHelperError("response_malformed") from exc
    if not isinstance(value, dict):
        raise WindowsBirthHelperError("response_schema_invalid")
    canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    if raw != canonical:
        raise WindowsBirthHelperError("response_not_canonical")
    return value


def _uint(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WindowsBirthHelperError(f"{field}_invalid")
    return value


def validate_response(raw: bytes, *, request_id: str, candidate_id: str,
                      expected_helper_hash: str,
                      expected_runtime_hash: str) -> WindowsBirthHelperResult:
    value = _loads_unique_canonical(raw)
    if frozenset(value) != _TOP_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise WindowsBirthHelperError("response_schema_invalid")
    if value["request_id"] != _digest(request_id, "request_id") or value["candidate_id"] != _digest(candidate_id, "candidate_id"):
        raise WindowsBirthHelperError("response_binding_mismatch")
    status = value["status"]
    if status not in _STATUSES:
        raise WindowsBirthHelperError("status_invalid")
    error_code, exit_code = value["error_code"], value["exit_code"]
    if status == "passed":
        if error_code is not None or exit_code != 0:
            raise WindowsBirthHelperError("status_fields_incoherent")
    elif status == "failed":
        if not isinstance(error_code, str) or not error_code or isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise WindowsBirthHelperError("status_fields_incoherent")
    elif not isinstance(error_code, str) or not error_code or exit_code is not None:
        raise WindowsBirthHelperError("status_fields_incoherent")
    att = value["attestation"]
    if not isinstance(att, dict) or frozenset(att) != _ATTESTATION_KEYS:
        raise WindowsBirthHelperError("attestation_schema_invalid")
    expected_helper_hash = _digest(expected_helper_hash, "helper_binary_hash")
    expected_runtime_hash = _digest(expected_runtime_hash, "runtime_binary_hash")
    invariant = {
        "backend": BACKEND, "helper_binary_hash": expected_helper_hash,
        "runtime_binary_hash": expected_runtime_hash,
        "profile_name": PROFILE_NAME, "network_capability": False,
        "memory_limit_bytes": MEMORY_LIMIT_BYTES, "process_limit": PROCESS_LIMIT,
        "stdout_limit_bytes": STDOUT_LIMIT_BYTES,
        "stderr_limit_bytes": STDERR_LIMIT_BYTES,
    }
    if any(att.get(k) != v for k, v in invariant.items()):
        raise WindowsBirthHelperError("attestation_mismatch")
    for field in ("assigned_before_resume", "tree_empty", "termination_attested"):
        if not isinstance(att[field], bool):
            raise WindowsBirthHelperError("attestation_schema_invalid")
    active_processes = _uint(att["active_processes"], "active_processes")
    if not isinstance(att["appcontainer_sid"], str):
        raise WindowsBirthHelperError("appcontainer_sid_invalid")
    if status != "test_environment_unavailable" and (
        not att["appcontainer_sid"].startswith("S-1-15-2-")
        or not att["assigned_before_resume"] or active_processes != 0
        or not att["tree_empty"] or not att["termination_attested"]
    ):
        raise WindowsBirthHelperError("attestation_mismatch")
    stdout_bytes = _uint(value["stdout_bytes"], "stdout_bytes")
    stderr_bytes = _uint(value["stderr_bytes"], "stderr_bytes")
    elapsed_ms = _uint(value["elapsed_ms"], "elapsed_ms")
    try:
        stdout = base64.b64decode(value["stdout_base64"], validate=True)
        stderr = base64.b64decode(value["stderr_base64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise WindowsBirthHelperError("output_base64_invalid") from exc
    if len(stdout) != stdout_bytes or len(stderr) != stderr_bytes:
        raise WindowsBirthHelperError("output_length_mismatch")
    if stdout_bytes > STDOUT_LIMIT_BYTES or stderr_bytes > STDERR_LIMIT_BYTES:
        raise WindowsBirthHelperError("output_limit_mismatch")
    for field in ("stdout_truncated", "stderr_truncated"):
        if not isinstance(value[field], bool):
            raise WindowsBirthHelperError(f"{field}_invalid")
    return WindowsBirthHelperResult(status, error_code, exit_code, stdout, stderr,
                                    elapsed_ms, value["stdout_truncated"],
                                    value["stderr_truncated"], dict(att))


def invoke_helper(helper: Path, *, trusted_hashes: AbstractSet[str], config: Path,
                  expected_config_hash: str, request_id: str,
                  candidate_id: str, phase: str, private_root: Path,
                  entrypoint: str, arguments: Sequence[str], timeout_s: float,
                  expected_runtime_hash: str) -> WindowsBirthHelperResult:
    if os.name != "nt":
        raise WindowsBirthHelperError("windows_platform_unavailable")
    if not helper.is_absolute() or not helper.is_file():
        raise WindowsBirthHelperError("helper_unavailable")
    actual_hash = helper_binary_hash(helper)
    if actual_hash not in trusted_hashes:
        raise WindowsBirthHelperError("helper_untrusted")
    expected_config_hash = _digest(expected_config_hash, "config_hash")
    if not config.is_absolute() or not config.is_file():
        raise WindowsBirthHelperError("helper_config_unavailable")
    if helper_binary_hash(config) != expected_config_hash:
        raise WindowsBirthHelperError("helper_config_mismatch")
    request = canonical_request(request_id=request_id, candidate_id=candidate_id,
                                phase=phase, private_root=private_root,
                                entrypoint=entrypoint, arguments=arguments)
    started = time.monotonic()
    try:
        returncode, response = _bounded_transport(
            (str(helper), "--config", str(config), "--config-hash", expected_config_hash),
            request, timeout_s)
    except WindowsBirthHelperError:
        raise
    result = validate_response(response, request_id=request_id,
                               candidate_id=candidate_id,
                               expected_helper_hash=actual_hash,
                               expected_runtime_hash=expected_runtime_hash)
    if returncode != 0:
        raise WindowsBirthHelperError("helper_exit_unattested")
    if result.elapsed_ms > int((time.monotonic() - started + 1.0) * 1000):
        raise WindowsBirthHelperError("elapsed_unattested")
    return result
