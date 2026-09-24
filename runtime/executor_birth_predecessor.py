"""Authenticated predecessor and admission-context pins for RM-0008 F4.

The objects in this module are immutable evidence, not caller classification
hints.  The contract store authenticates the selected revision, builds the
pin, and compares it again at the publication linearization point.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from executor_birth_shadow import RevisionFacts
from executor_birth_snapshot import CandidateSnapshot


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_id(domain: str, value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(domain.encode("ascii") + b"\0" + encoded)


@dataclass(frozen=True, slots=True)
class AuthenticatedPredecessorSnapshot:
    revision_id: str | None
    revision_kind: str
    payload_hashes: Mapping[str, str]
    snapshot_id: str
    approved_lifecycle: str | None = None
    admission_receipt_hash: str | None = None

    def __post_init__(self) -> None:
        if self.revision_kind not in {"absent", "generation", "retirement"}:
            raise ValueError("predecessor_snapshot_invalid: revision_kind")
        if (self.revision_id is None) != (self.revision_kind == "absent"):
            raise ValueError("predecessor_snapshot_invalid: revision_id")
        if (self.approved_lifecycle is None) != (self.admission_receipt_hash is None):
            raise ValueError("predecessor_snapshot_invalid: admission")
        if self.approved_lifecycle is not None:
            from executor_birth_receipts import ApprovedLifecycle
            ApprovedLifecycle(self.approved_lifecycle)
            if self.revision_kind != "generation":
                raise ValueError("predecessor_snapshot_invalid: admission revision")
        object.__setattr__(self, "payload_hashes", MappingProxyType(dict(self.payload_hashes)))


@dataclass(frozen=True, slots=True)
class AdmissionContextPin:
    admission_context_id: str
    context_epoch: str


def predecessor_snapshot(
    revision_id: str | None, revision_kind: str,
    payloads: Mapping[str, bytes] | None,
    *, approved_lifecycle: str | None = None,
    admission_receipt: bytes | None = None,
) -> AuthenticatedPredecessorSnapshot:
    hashes = {} if payloads is None else {
        name: _sha256(payload) for name, payload in sorted(payloads.items())
    }
    value = {"revision_id": revision_id, "revision_kind": revision_kind,
             "payload_hashes": hashes}
    receipt_hash = None if admission_receipt is None else _sha256(admission_receipt)
    if receipt_hash is not None:
        value.update(approved_lifecycle=approved_lifecycle,
                     admission_receipt_hash=receipt_hash)
    return AuthenticatedPredecessorSnapshot(
        revision_id, revision_kind, hashes,
        _canonical_id("metnos.executor-birth.predecessor-snapshot/v1", value),
        approved_lifecycle, receipt_hash,
    )


def revision_facts_id(facts: RevisionFacts) -> str:
    value = {name: getattr(facts, name) for name in facts.__dataclass_fields__}
    return _canonical_id("metnos.executor-birth.revision-facts/v1", value)


def _without_descriptions(value: object) -> object:
    if isinstance(value, list):
        return [_without_descriptions(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: _without_descriptions(item) for key, item in value.items()
            if key != "description" and key != "birth"}


def derive_revision_facts(
    predecessor: AuthenticatedPredecessorSnapshot,
    predecessor_payloads: Mapping[str, bytes] | None,
    candidate: CandidateSnapshot,
) -> RevisionFacts:
    """Derive byte-backed facts from the authenticated predecessor.

    Promotion requires the admission authenticated by the store, independently
    of the lifecycle claimed in the author's manifest.
    """
    if predecessor.revision_kind == "absent":
        return RevisionFacts(first_birth=True)
    if predecessor.revision_kind == "retirement":
        return RevisionFacts(reactivation=True)
    if predecessor_payloads is None or "manifest.toml" not in predecessor_payloads:
        raise ValueError("predecessor_snapshot_invalid: manifest.toml")
    old_manifest = tomllib.loads(predecessor_payloads["manifest.toml"].decode("utf-8"))
    new_manifest = tomllib.loads(candidate.manifest_bytes.decode("utf-8"))
    if (predecessor.approved_lifecycle == "active"
            and new_manifest.get("lifecycle") in {"synthesized", "preexercise"}):
        raise ValueError("birth_active_to_preexercise_forbidden")
    # Immutable generations authenticate the manifest and its declared code
    # digest/file map; source bytes are deliberately not duplicated in the
    # contract store.  Comparing this closed declaration is consequently the
    # authoritative predecessor comparison.
    old_code = old_manifest.get("code")
    new_code = new_manifest.get("code")
    code_changed = old_code != new_code
    old_semantic = _without_descriptions(old_manifest)
    new_semantic = _without_descriptions(new_manifest)
    linguistic_changed = old_manifest != new_manifest and old_semantic == new_semantic
    contract_changed = old_semantic != new_semantic and not code_changed
    exact = (old_manifest == new_manifest and
             predecessor_payloads.get("manifest.lang_state.json") == candidate.language_state_bytes)
    return RevisionFacts(
        promotion=(predecessor.approved_lifecycle == "preexercise"
                   and new_manifest.get("lifecycle", "active") == "active"),
        code_changed=code_changed,
        contract_changed=contract_changed,
        linguistic_surface_changed=linguistic_changed,
        semantic_core_unchanged=old_semantic == new_semantic and not code_changed,
        exact_republish=exact,
    )
