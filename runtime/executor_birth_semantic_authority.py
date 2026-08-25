"""Core-owned semantic-review authority for the executor Birth boundary.

Only public verification material is loaded here.  Producers and candidates
cannot mint evidence through this interface; an operator provisions signed
evidence records out of band and the authority authenticates them at use time.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import ctypes
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_semantic_review import (
    EvidenceStatus, IndependentEvidence, IndependentEvidenceKind, ReviewPolicyV1,
    ReviewRiskFacts, SemanticReviewError, SemanticReviewRequest,
)


EVIDENCE_DOMAIN = b"metnos.executor-birth.independent-evidence/v1\0"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RECORD_KEYS = frozenset({"schema_version", "key_id", "evidence", "signature"})
_EVIDENCE_KEYS = frozenset({
    "evidence_id", "evidence_version", "kind", "owner_id", "candidate_id",
    "admission_context_id", "status", "evidence_hash",
})
_MAX_EVIDENCE_FILES = 256
_MAX_EVIDENCE_BYTES = 64 * 1024
_MAX_KEY_BYTES = 32


if os.name == "nt":
    from ctypes import wintypes

    class _WinFileInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD), ("creation_low", wintypes.DWORD),
            ("creation_high", wintypes.DWORD), ("access_low", wintypes.DWORD),
            ("access_high", wintypes.DWORD), ("write_low", wintypes.DWORD),
            ("write_high", wintypes.DWORD), ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD), ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        wintypes.HANDLE)
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.GetFileInformationByHandle.argtypes = (wintypes.HANDLE,
                                                      ctypes.POINTER(_WinFileInfo))
    _KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _KERNEL32.GetFinalPathNameByHandleW.argtypes = (wintypes.HANDLE,
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    _KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _KERNEL32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    _KERNEL32.ReadFile.restype = wintypes.BOOL
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _KERNEL32.CloseHandle.restype = wintypes.BOOL


def _win_error(operation: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), operation)


def _win_open(path: Path, *, directory: bool) -> int:
    # No write/delete sharing: the object and its directory entry remain fixed
    # for the lifetime of the verification handle.
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    access = 0x00000001 if directory else 0x80000000  # LIST_DIRECTORY / GENERIC_READ
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = _KERNEL32.CreateFileW(str(path), access, 0x00000001, None, 3, flags, None)
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise _win_error("CreateFileW")
    return handle


def _win_info(handle: int) -> tuple[int, ...]:
    info = _WinFileInfo()
    if not _KERNEL32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _win_error("GetFileInformationByHandle")
    return (info.attributes, info.write_high, info.write_low, info.volume,
            info.size_high, info.size_low, info.links, info.file_index_high,
            info.file_index_low)


def _win_final_path(handle: int) -> str:
    needed = _KERNEL32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not needed:
        raise _win_error("GetFinalPathNameByHandleW")
    buffer = ctypes.create_unicode_buffer(needed + 1)
    written = _KERNEL32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not written or written >= len(buffer):
        raise _win_error("GetFinalPathNameByHandleW")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _win_read(handle: int, maximum: int) -> bytes:
    result = bytearray()
    while len(result) <= maximum:
        capacity = min(8192, maximum + 1 - len(result))
        buffer = ctypes.create_string_buffer(capacity)
        count = wintypes.DWORD()
        if not _KERNEL32.ReadFile(handle, buffer, capacity, ctypes.byref(count), None):
            raise _win_error("ReadFile")
        if not count.value:
            break
        result.extend(buffer.raw[:count.value])
    return bytes(result)


def _win_close(handle: int) -> None:
    _KERNEL32.CloseHandle(handle)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_size, after.st_mtime_ns, after.st_ctime_ns)


def _secure_file_bytes(path: Path, *, maximum: int, error: str) -> bytes:
    """Read one immutable regular file through its descriptor, without links."""
    if os.name == "nt":
        handle = None
        try:
            handle = _win_open(path, directory=False)
            before = _win_info(handle)
            attributes, _, _, _, high, low, links, _, _ = before
            size = (high << 32) | low
            if attributes & 0x00000400 or attributes & 0x00000010 or links != 1 or size > maximum:
                raise ValueError("unsafe file")
            if _win_final_path(handle) != os.path.normcase(os.path.abspath(path)):
                raise ValueError("unexpected final path")
            raw = _win_read(handle, maximum)
            if len(raw) > maximum or _win_info(handle) != before:
                raise ValueError("file changed")
            return raw
        except (OSError, ValueError) as exc:
            raise SemanticReviewError(error, path.name) from exc
        finally:
            if handle is not None:
                _win_close(handle)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size > maximum
                    or (os.name != "nt" and before.st_mode & 0o022)):
                raise ValueError("unsafe file")
            raw = bytearray()
            while len(raw) <= maximum:
                block = os.read(fd, min(8192, maximum + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
            after = os.fstat(fd)
            if len(raw) > maximum or not _same_file(before, after):
                raise ValueError("file changed")
            return bytes(raw)
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        raise SemanticReviewError(error, path.name) from exc


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class SemanticAuthorityProvider(Protocol):
    """The sole productive source of policy, risk facts and independent proof."""

    def inputs_for(self, request: SemanticReviewRequest) -> tuple[
        ReviewPolicyV1, ReviewRiskFacts, tuple[IndependentEvidence, ...]
    ]: ...


def derive_review_risk_facts(request: SemanticReviewRequest) -> ReviewRiskFacts:
    """Derive bounded facts from the immutable candidate, never caller hints."""
    if not isinstance(request, SemanticReviewRequest):
        raise SemanticReviewError("birth_request_invalid", "review request")
    total = len(request.manifest_bytes) + len(request.language_state_bytes)
    total += sum(len(path.encode("utf-8")) + len(data) for path, data in request.code_files.items())
    code_count = len(request.code_files)
    # Deliberately conservative, monotonic and deterministic.  The thresholds
    # remain constants owned by ReviewPolicyV1.
    complexity = min(100, code_count * 8 + total // 4096)
    joined = b"\n".join(request.code_files[path] for path in sorted(request.code_files))
    danger = sum(token in joined.lower() for token in (
        b"socket", b"subprocess", b"eval(", b"exec(", b"ctypes", b"powershell",
        b"os.system", b"http://", b"https://",
    ))
    risk = min(100, danger * 15 + (20 if total > 128 * 1024 else 0))
    uncertainty = min(100, (15 if not request.language_state_bytes.strip() else 0)
                      + (20 if code_count > 8 else 0) + total // 16384)
    return ReviewRiskFacts(risk, complexity, uncertainty)


@dataclass(frozen=True, slots=True)
class PreprovisionedSemanticAuthority:
    policy: ReviewPolicyV1
    evidence_dir: Path
    verifier_keys: Mapping[str, Ed25519PublicKey]

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ReviewPolicyV1) or not self.verifier_keys:
            raise SemanticReviewError("semantic_review_unavailable", "authority config")
        keys = dict(self.verifier_keys)
        if any(not isinstance(key_id, str) or not key_id or
               not isinstance(key, Ed25519PublicKey) for key_id, key in keys.items()):
            raise SemanticReviewError("semantic_review_unavailable", "authority keys")
        object.__setattr__(self, "verifier_keys", MappingProxyType(keys))

    def inputs_for(self, request: SemanticReviewRequest):
        return self.policy, derive_review_risk_facts(request), self._evidence_for(request)

    def _evidence_for(self, request: SemanticReviewRequest) -> tuple[IndependentEvidence, ...]:
        if os.name == "nt":
            return self._evidence_for_windows(request)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(self.evidence_dir, flags)
            before = os.fstat(directory_fd)
            if (not stat.S_ISDIR(before.st_mode)
                    or (os.name != "nt" and before.st_mode & 0o022)):
                os.close(directory_fd)
                raise SemanticReviewError("semantic_review_unavailable", "evidence store permissions")
        except OSError as exc:
            raise SemanticReviewError("semantic_review_unavailable", "evidence store") from exc
        try:
            names = sorted((name for name in os.listdir(directory_fd) if name.endswith(".json")), key=lambda name: name.encode())
            if len(names) > _MAX_EVIDENCE_FILES or any(name in {".", ".."} or "/" in name or "\\" in name for name in names):
                raise SemanticReviewError("semantic_review_unavailable", "evidence store bounds")
            result: list[IndependentEvidence] = []
            seen: set[str] = set()
            for name in names:
                item = self._read_record_at(directory_fd, name)
                if item.candidate_id != request.candidate_id:
                    continue
                if item.admission_context_id != request.admission_context_id:
                    raise SemanticReviewError("evidence_obsolete", item.evidence_id)
                if item.owner_id == request.generator_owner_id:
                    raise SemanticReviewError("evidence_forged", "candidate self-attestation")
                if item.evidence_id in seen:
                    raise SemanticReviewError("evidence_forged", "duplicate evidence_id")
                seen.add(item.evidence_id)
                result.append(item)
            if not _same_file(before, os.fstat(directory_fd)):
                raise SemanticReviewError("semantic_review_unavailable", "evidence store changed")
            return tuple(result)
        finally:
            os.close(directory_fd)

    def _evidence_for_windows(self, request: SemanticReviewRequest) -> tuple[IndependentEvidence, ...]:
        handle = None
        try:
            handle = _win_open(self.evidence_dir, directory=True)
            before = _win_info(handle)
            attributes = before[0]
            if not attributes & 0x00000010 or attributes & 0x00000400:
                raise SemanticReviewError("semantic_review_unavailable", "evidence store type")
            final_directory = _win_final_path(handle)
            if final_directory != os.path.normcase(os.path.abspath(self.evidence_dir)):
                raise SemanticReviewError("semantic_review_unavailable", "evidence store path")
            names = sorted((name for name in os.listdir(self.evidence_dir)
                            if name.endswith(".json")), key=lambda name: name.encode())
            if len(names) > _MAX_EVIDENCE_FILES or any(
                    name in {".", ".."} or "/" in name or "\\" in name for name in names):
                raise SemanticReviewError("semantic_review_unavailable", "evidence store bounds")
            result: list[IndependentEvidence] = []
            seen: set[str] = set()
            for name in names:
                item = self._read_record_windows(final_directory, name)
                if item.candidate_id != request.candidate_id:
                    continue
                if item.admission_context_id != request.admission_context_id:
                    raise SemanticReviewError("evidence_obsolete", item.evidence_id)
                if item.owner_id == request.generator_owner_id:
                    raise SemanticReviewError("evidence_forged", "candidate self-attestation")
                if item.evidence_id in seen:
                    raise SemanticReviewError("evidence_forged", "duplicate evidence_id")
                seen.add(item.evidence_id)
                result.append(item)
            if _win_info(handle) != before:
                raise SemanticReviewError("semantic_review_unavailable", "evidence store changed")
            return tuple(result)
        except SemanticReviewError:
            raise
        except (OSError, ValueError) as exc:
            raise SemanticReviewError("semantic_review_unavailable", "evidence store") from exc
        finally:
            if handle is not None:
                _win_close(handle)

    def _read_record_windows(self, final_directory: str, name: str) -> IndependentEvidence:
        handle = None
        try:
            handle = _win_open(self.evidence_dir / name, directory=False)
            before = _win_info(handle)
            attributes, _, _, _, high, low, links, _, _ = before
            size = (high << 32) | low
            if attributes & (0x00000400 | 0x00000010) or links != 1 or size > _MAX_EVIDENCE_BYTES:
                raise ValueError("unsafe evidence")
            final = _win_final_path(handle)
            if os.path.dirname(final) != final_directory or os.path.basename(final) != os.path.normcase(name):
                raise ValueError("unexpected evidence path")
            raw = _win_read(handle, _MAX_EVIDENCE_BYTES)
            if len(raw) > _MAX_EVIDENCE_BYTES or _win_info(handle) != before:
                raise ValueError("evidence changed")
            return self._decode_record(raw, name)
        except SemanticReviewError:
            raise
        except (OSError, ValueError) as exc:
            raise SemanticReviewError("evidence_forged", name) from exc
        finally:
            if handle is not None:
                _win_close(handle)

    def _read_record_at(self, directory_fd: int, name: str) -> IndependentEvidence:
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_size > _MAX_EVIDENCE_BYTES
                        or (os.name != "nt" and before.st_mode & 0o022)):
                    raise ValueError("unsafe evidence")
                raw = os.read(fd, _MAX_EVIDENCE_BYTES + 1)
                after = os.fstat(fd)
                if len(raw) > _MAX_EVIDENCE_BYTES or not _same_file(before, after):
                    raise ValueError("evidence changed")
            finally:
                os.close(fd)
            return self._decode_record(raw, name)
        except SemanticReviewError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError,
                InvalidSignature) as exc:
            raise SemanticReviewError("evidence_forged", name) from exc

    def _decode_record(self, raw: bytes, name: str) -> IndependentEvidence:
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(value, dict) or set(value) != _RECORD_KEYS or _canonical(value) != raw:
                raise ValueError("record")
            if value["schema_version"] != 1 or not isinstance(value["evidence"], dict):
                raise ValueError("version")
            evidence = value["evidence"]
            if set(evidence) != _EVIDENCE_KEYS:
                raise ValueError("evidence")
            if not isinstance(value["key_id"], str):
                raise ValueError("key id")
            key = self.verifier_keys.get(value["key_id"])
            if key is None:
                raise ValueError("key")
            signature = base64.b64decode(value["signature"], validate=True)
            key.verify(signature, EVIDENCE_DOMAIN + _canonical(evidence))
            item = IndependentEvidence(
                evidence["evidence_id"], evidence["evidence_version"],
                IndependentEvidenceKind(evidence["kind"]), evidence["owner_id"],
                evidence["candidate_id"], evidence["admission_context_id"],
                EvidenceStatus(evidence["status"]), evidence["evidence_hash"],
            )
            if not all(_DIGEST.fullmatch(getattr(item, field)) for field in (
                "evidence_id", "candidate_id", "admission_context_id", "evidence_hash"
            )):
                raise ValueError("digest")
            if item.owner_id not in self.policy.owners[item.kind]:
                raise ValueError("owner")
            return item
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError,
                InvalidSignature, KeyError) as exc:
            raise SemanticReviewError("evidence_forged", name) from exc


def load_semantic_authority(value: object, config_dir: Path) -> PreprovisionedSemanticAuthority:
    """Load an exact, explicitly provisioned productive authority configuration."""
    if not isinstance(value, dict) or set(value) != {"evidence_dir", "verifiers", "versions", "owners"}:
        raise SemanticReviewError("semantic_review_unavailable", "authority config")
    try:
        if not isinstance(config_dir, Path) or not isinstance(value["evidence_dir"], str):
            raise ValueError("paths")
        if not all(isinstance(value[field], dict) for field in ("verifiers", "versions", "owners")):
            raise ValueError("maps")
        expected_kinds = {kind.value for kind in IndependentEvidenceKind}
        if set(value["versions"]) != expected_kinds or set(value["owners"]) != expected_kinds:
            raise ValueError("policy kinds")
        for field in ("versions", "owners"):
            for items in value[field].values():
                if (not isinstance(items, list) or not items
                        or any(not isinstance(item, str) or not item for item in items)
                        or len(set(items)) != len(items)):
                    raise ValueError("policy list")
        versions = {IndependentEvidenceKind(kind): frozenset(items)
                    for kind, items in value["versions"].items()}
        owners = {IndependentEvidenceKind(kind): frozenset(items)
                  for kind, items in value["owners"].items()}
        verifiers = {}
        for key_id, spec in value["verifiers"].items():
            if (not isinstance(key_id, str) or not key_id or not isinstance(spec, dict)
                    or set(spec) != {"path", "status"} or spec["status"] not in {"active", "revoked"}
                    or not isinstance(spec["path"], str)):
                raise ValueError("verifier schema")
            if spec["status"] == "revoked":
                continue
            path = Path(spec["path"])
            path = path if path.is_absolute() else config_dir / path
            verifiers[key_id] = Ed25519PublicKey.from_public_bytes(
                _secure_file_bytes(path, maximum=_MAX_KEY_BYTES, error="semantic_review_unavailable"))
        evidence_dir = Path(value["evidence_dir"])
        evidence_dir = evidence_dir if evidence_dir.is_absolute() else config_dir / evidence_dir
        return PreprovisionedSemanticAuthority(ReviewPolicyV1(versions, owners), evidence_dir, verifiers)
    except SemanticReviewError:
        raise
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise SemanticReviewError("semantic_review_unavailable", "authority config") from exc
