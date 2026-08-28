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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_semantic_review import (
    EvidenceStatus, IndependentEvidence, IndependentEvidenceKind, ReviewPolicyV1,
    ReviewRiskFacts, SemanticReviewError, SemanticReviewRequest,
)
from executor_birth_secure_file import (
    SecureFileReadError, _same_file, _win_close, _win_file_shape,
    _win_expected_path, _win_final_path, _win_info, _win_open, _win_read,
    read_immutable_regular_file,
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


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _secure_file_bytes(path: Path, *, maximum: int, error: str) -> bytes:
    """Read one immutable regular file through its descriptor, without links."""
    try:
        return read_immutable_regular_file(path, maximum=maximum)
    except SecureFileReadError as exc:
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
        # ``evidence_dir`` is a historical Path or a directory capability bound
        # to a Birth session; the second form cannot be reopened by name.
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
        from executor_birth_secure_fs import _SecureDirectoryHandle

        if isinstance(self.evidence_dir, _SecureDirectoryHandle):
            return self._evidence_for_capability(request)
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

    def _evidence_for_capability(
        self, request: SemanticReviewRequest
    ) -> tuple[IndependentEvidence, ...]:
        """Read the evidence store through the capability bound at load time.

        The location is a directory this authority already holds, not a name to
        resolve again: a store moved aside and replaced by another one at the
        same name is therefore not consulted.  Once the session that produced
        the capability is closed the store is simply unavailable, and the
        refusal carries no location.
        """
        from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

        try:
            names = sorted(
                (
                    name
                    for name in self.evidence_dir.inventory()
                    if name.endswith(".json")
                ),
                key=lambda name: name.encode(),
            )
        except BirthSecureFSError as exc:
            raise SemanticReviewError(
                "semantic_review_unavailable", "evidence store"
            ) from exc
        if len(names) > _MAX_EVIDENCE_FILES:
            raise SemanticReviewError(
                "semantic_review_unavailable", "evidence store bounds"
            )
        result: list[IndependentEvidence] = []
        seen: set[str] = set()
        for name in names:
            try:
                raw = self.evidence_dir.read_file(
                    name,
                    maximum=_MAX_EVIDENCE_BYTES,
                    role=_BirthObjectRole.birth_integrity_only,
                )
            except BirthSecureFSError as exc:
                raise SemanticReviewError(
                    "semantic_review_unavailable", "evidence store"
                ) from exc
            item = self._decode_record(raw, name)
            if item.candidate_id != request.candidate_id:
                continue
            if item.admission_context_id != request.admission_context_id:
                raise SemanticReviewError("evidence_obsolete", item.evidence_id)
            if item.owner_id == request.generator_owner_id:
                raise SemanticReviewError(
                    "evidence_forged", "candidate self-attestation"
                )
            if item.evidence_id in seen:
                raise SemanticReviewError("evidence_forged", "duplicate evidence_id")
            seen.add(item.evidence_id)
            result.append(item)
        return tuple(result)

    def _evidence_for_windows(self, request: SemanticReviewRequest) -> tuple[IndependentEvidence, ...]:
        handle = None
        try:
            handle = _win_open(self.evidence_dir, directory=True)
            before = _win_info(handle)
            attributes = before[0]
            if not attributes & 0x00000010 or attributes & 0x00000400:
                raise SemanticReviewError("semantic_review_unavailable", "evidence store type")
            final_directory = _win_final_path(handle)
            if final_directory != _win_expected_path(self.evidence_dir):
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
            shape_before = _win_file_shape(handle)
            attributes, size, links, delete_pending, directory = shape_before
            if (
                attributes & 0x00000400 or directory or delete_pending
                or links != 1 or size < 0 or size > _MAX_EVIDENCE_BYTES
            ):
                raise ValueError("unsafe evidence")
            final = _win_final_path(handle)
            if os.path.dirname(final) != final_directory or os.path.basename(final) != os.path.normcase(name):
                raise ValueError("unexpected evidence path")
            raw = _win_read(handle, _MAX_EVIDENCE_BYTES)
            if (
                len(raw) > _MAX_EVIDENCE_BYTES or len(raw) != size
                or _win_info(handle) != before
                or _win_file_shape(handle) != shape_before
            ):
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


MAXIMUM_SEMANTIC_AUTHORITY_BYTES = 64 * 1024


def _load_semantic_authority_in_session(
    authority_file: tuple[str, ...],
    public_directory: tuple[str, ...],
    evidence_directory: tuple[str, ...],
    session,
) -> PreprovisionedSemanticAuthority:
    """Load the authority through a session that already holds the global lock.

    The three relative names come from the closed catalogue and never from a
    value declared inside the document.  The evidence location is kept as a
    directory capability bound to this session rather than a path to reopen,
    so every use after the session is closed fails with the stable code and
    without a path in the message (section 16.13.3).
    """
    import json

    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    if not session._holds_global_lock():
        raise BirthSecureFSError("birth_provisioning_lock_unsafe")
    # One load is one operation, so the two subtrees it admits share a single
    # budget: the ceiling counts the material of the whole authority and not
    # of each container separately.
    from executor_birth_secure_fs import _InventoryBudgetV1

    budget = _InventoryBudgetV1()
    session._inventory_state(tuple(public_directory), budget)
    session._inventory_state(tuple(evidence_directory), budget)
    public = _BirthObjectRole.birth_integrity_only
    raw = session.read_file(
        tuple(authority_file),
        maximum=MAXIMUM_SEMANTIC_AUTHORITY_BYTES,
        role=public,
    )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticReviewError(
            "semantic_review_unavailable", "authority config"
        ) from exc
    if not isinstance(document, dict) or set(document) != {
        "evidence_dir", "verifiers", "versions", "owners"
    }:
        raise SemanticReviewError("semantic_review_unavailable", "authority config")
    try:
        expected_kinds = {kind.value for kind in IndependentEvidenceKind}
        if (set(document["versions"]) != expected_kinds
                or set(document["owners"]) != expected_kinds):
            raise ValueError("policy kinds")
        versions = {
            IndependentEvidenceKind(kind): frozenset(items)
            for kind, items in document["versions"].items()
        }
        owners = {
            IndependentEvidenceKind(kind): frozenset(items)
            for kind, items in document["owners"].items()
        }
        verifiers = {}
        for key_id, spec in document["verifiers"].items():
            if (not isinstance(spec, dict) or set(spec) != {"status", "path"}
                    or not isinstance(spec["path"], str)):
                raise ValueError("verifier schema")
            if spec["status"] == "revoked":
                continue
            name = PurePosixPath(spec["path"]).name
            verifiers[key_id] = Ed25519PublicKey.from_public_bytes(
                session.read_file(
                    tuple(public_directory) + (name,),
                    maximum=_MAX_KEY_BYTES,
                    role=public,
                )
            )
    except SemanticReviewError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SemanticReviewError(
            "semantic_review_unavailable", "authority config"
        ) from exc
    evidence = session.open_directory(tuple(evidence_directory), role=public)
    return PreprovisionedSemanticAuthority(
        ReviewPolicyV1(versions, owners), evidence, verifiers,
    )


def _read_verifier_below(config_dir: Path, declared: PurePosixPath) -> bytes:
    """Read one declared verifier key without ever resolving it as a path.

    The configuration directory is the only absolute name resolved; each
    declared component below it is then opened relative to the descriptor
    obtained for the previous one, so the key that is read is the key that
    hangs from the anchor and not one a substituted component points at.
    """
    import executor_birth_secure_fs as secure

    components = declared.parts
    if not components or any(part in {"", ".", ".."} for part in components):
        raise SemanticReviewError("semantic_review_unavailable", "authority config")
    windows = os.name == "nt"
    open_root = (
        secure._open_win_directory_root
        if windows
        else secure._open_posix_directory_root
    )
    close = secure._win_close if windows else os.close
    handles = [open_root(os.fspath(config_dir))]
    try:
        for part in components[:-1]:
            handles.append(
                secure._win_open_relative_v1(
                    handles[-1],
                    part,
                    purpose=secure._NtOpenPurposeV1.read_required,
                    directory=True,
                )
                if windows
                else secure._open_posix_child_directory(handles[-1], part)
            )
        if windows:
            return secure._read_win_relative_v1(
                handles[-1], components[-1], maximum=_MAX_KEY_BYTES,
            )
        return secure._read_posix_relative(
            handles[-1],
            components[-1],
            maximum=_MAX_KEY_BYTES,
            role=secure._BirthObjectRole.historical_public,
            expected_uid=None,
        )
    except (secure.BirthSecureFSError, OSError) as exc:
        raise SemanticReviewError(
            "semantic_review_unavailable", "authority config"
        ) from exc
    finally:
        for handle in reversed(handles):
            close(handle)


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
            declared = PurePosixPath(spec["path"])
            verifiers[key_id] = Ed25519PublicKey.from_public_bytes(
                _secure_file_bytes(
                    Path(spec["path"]),
                    maximum=_MAX_KEY_BYTES,
                    error="semantic_review_unavailable",
                )
                if declared.is_absolute()
                else _read_verifier_below(config_dir, declared)
            )
        evidence_dir = Path(value["evidence_dir"])
        evidence_dir = evidence_dir if evidence_dir.is_absolute() else config_dir / evidence_dir
        return PreprovisionedSemanticAuthority(ReviewPolicyV1(versions, owners), evidence_dir, verifiers)
    except SemanticReviewError:
        raise
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise SemanticReviewError("semantic_review_unavailable", "authority config") from exc
