"""Pre-provisioned approver identities and signed Birth decisions."""
from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from executor_birth_approval import ApprovalDecision, BirthApprovalError
from executor_birth_identity import encode_framed_v1

DECISION_DOMAIN_V1 = b"metnos.executor-birth.approval-decision/v1\0"


@dataclass(frozen=True, slots=True)
class ApprovalAuthority:
    revision: int
    keys: Mapping[str, Ed25519PublicKey]
    actors: Mapping[str, Mapping[str, frozenset[str]]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))
        object.__setattr__(self, "actors", MappingProxyType(dict(self.actors)))


def decision_payload(*, token: str, subject_hash: str, actor: str,
                     decision: ApprovalDecision, decided_at: str, key_id: str) -> bytes:
    return DECISION_DOMAIN_V1 + encode_framed_v1({
        "schema_version": 1, "token": token, "subject_hash": subject_hash,
        "actor": actor, "decision": decision.value, "decided_at": decided_at,
        "key_id": key_id,
    })


def verify_decision(authority: ApprovalAuthority, *, token: str, subject_hash: str,
                    scope: str, actor: str, decision: ApprovalDecision,
                    decided_at: str, key_id: str, signature: str) -> None:
    actor_entry = authority.actors.get(actor)
    if actor_entry is None or scope not in actor_entry["scopes"] or key_id not in actor_entry["key_ids"]:
        raise BirthApprovalError("approval_invalid", "actor_scope_unauthorized")
    key = authority.keys.get(key_id)
    if key is None:
        raise BirthApprovalError("approval_invalid", "unknown_approver_key")
    try:
        encoded = bytes.fromhex(signature)
    except (TypeError, ValueError) as exc:
        raise BirthApprovalError("approval_invalid", "signature") from exc
    try:
        key.verify(encoded, decision_payload(
            token=token, subject_hash=subject_hash, actor=actor, decision=decision,
            decided_at=decided_at, key_id=key_id,
        ))
    except (InvalidSignature, ValueError) as exc:
        raise BirthApprovalError("approval_invalid", "signature") from exc


MAXIMUM_APPROVAL_REGISTRY_BYTES = 64 * 1024


def _load_approval_authority_in_session(
    authority_file: tuple[str, ...],
    session,
) -> ApprovalAuthority:
    """Load the registry through a session that already holds the global lock.

    Section 16.13.3 fixes this entry: it never releases or reacquires the
    global lock and it invents no local lock of its own, because only the key
    store owns one.  The relative name comes from the closed catalogue, not
    from a value declared inside the document.
    """
    from executor_birth_secure_fs import BirthSecureFSError, _BirthObjectRole

    if not session._holds_global_lock():
        # Missing the global lock is a violation of the lock hierarchy, not a
        # defect of the registry: the stable code belongs to the filesystem
        # capability, so the caller cannot mistake one for the other.
        raise BirthSecureFSError("birth_provisioning_lock_unsafe")
    try:
        raw = session.read_file(
            tuple(authority_file),
            maximum=MAXIMUM_APPROVAL_REGISTRY_BYTES,
            role=_BirthObjectRole.birth_integrity_only,
        )
    except BirthSecureFSError as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    return _decode_approval_authority(raw)


def load_approval_authority(path: Path) -> ApprovalAuthority:
    """Load a canonical public-only registry; retained keys verify old decisions.

    The containing directory is the only absolute name resolved here; the
    registry itself is opened relative to that descriptor, verified before the
    first byte and compared again after the last, so a component substituted
    after the anchor cannot redirect the read.
    """
    path = Path(path)
    if os.name == "nt":
        return _decode_approval_authority(_read_windows_registry(path))
    from executor_birth_secure_fs import (
        BirthSecureFSError,
        _BirthObjectRole,
        _open_posix_directory_root,
        _read_posix_relative,
    )

    try:
        directory = _open_posix_directory_root(os.fspath(path.parent))
    except BirthSecureFSError as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    try:
        raw = _read_posix_relative(
            directory,
            path.name,
            maximum=MAXIMUM_APPROVAL_REGISTRY_BYTES,
            role=_BirthObjectRole.historical_public,
            expected_uid=None,
        )
    except (BirthSecureFSError, OSError) as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    finally:
        os.close(directory)
    return _decode_approval_authority(raw)


def _read_windows_registry(path: Path) -> bytes:
    """Read the registry with the containing directory as the only absolute name."""
    from executor_birth_secure_fs import (
        BirthSecureFSError,
        _open_win_directory_root,
        _read_win_relative_v1,
        _win_close,
    )

    try:
        directory = _open_win_directory_root(os.fspath(path.parent))
    except (BirthSecureFSError, OSError) as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    try:
        return _read_win_relative_v1(
            directory, path.name, maximum=MAXIMUM_APPROVAL_REGISTRY_BYTES,
        )
    except (BirthSecureFSError, OSError) as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    finally:
        _win_close(directory)


def _decode_approval_authority(raw: bytes) -> ApprovalAuthority:
    """Validate the canonical registry bytes, whatever produced them."""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BirthApprovalError("approval_authority_unavailable") from exc
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode()
    if raw != canonical or not isinstance(value, dict) or set(value) != {
        "schema_version", "revision", "keys", "actors"
    } or value["schema_version"] != 1 or type(value["revision"]) is not int or value["revision"] < 1:
        raise BirthApprovalError("approval_authority_invalid")
    try:
        keys = {key_id: Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))
                for key_id, encoded in value["keys"].items()}
        if not keys or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError
        actors = {}
        for actor, entry in value["actors"].items():
            if not isinstance(actor, str) or not actor or set(entry) != {"key_ids", "scopes"}:
                raise ValueError
            key_ids, scopes = frozenset(entry["key_ids"]), frozenset(entry["scopes"])
            if not key_ids or not scopes or not key_ids <= keys.keys() or any(not isinstance(x, str) or not x for x in scopes):
                raise ValueError
            actors[actor] = MappingProxyType({"key_ids": key_ids, "scopes": scopes})
    except (AttributeError, TypeError, ValueError) as exc:
        raise BirthApprovalError("approval_authority_invalid") from exc
    return ApprovalAuthority(value["revision"], keys, actors)
