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


def load_approval_authority(path: Path) -> ApprovalAuthority:
    """Load a canonical public-only registry; retained keys verify old decisions."""
    try:
        path = Path(path)
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or info.st_size > 64 * 1024
                or (os.name != "nt" and info.st_mode & 0o022)):
            raise OSError("unsafe registry")
        raw = path.read_bytes()
        after = path.stat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OSError("registry changed")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
