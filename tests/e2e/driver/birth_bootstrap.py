"""Ephemeral, valid Executor Birth provisioning for isolated E2E servers.

Production intentionally has no key-generation fallback.  The E2E driver is
the operator for its throw-away instance, so it provisions the same files the
productive bootstrap validates and consumes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


# Closed productive registries mirrored as fixture data.  The E2E driver must
# remain independent of runtime imports because it controls the server only
# through process and HTTP boundaries.
_PRODUCER_CAPABILITIES = (
    ("change_applier", "extend"),
    ("change_rollback", "rollback"),
    ("synt_multistage", "create_or_replay"),
    ("synt_specialize", "specialize_or_replay"),
    ("synt_approve", "approve_or_replay"),
    ("promoter", "promote"),
    ("stack_reconcile", "restart_sign_first"),
    ("skills_cli", "skill_import_or_reactivation"),
    ("installer_phase3", "install"),
    ("builtin_contract_generator", "generate_builtin"),
    ("promoter", "rollback"),
)
_EVIDENCE_KINDS = ("deterministic_oracle", "human_case", "metamorphic_relation")


def _raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes_raw()


def _key_id(public: bytes) -> str:
    return f"birth-ed25519-v1-sha256-{hashlib.sha256(public).hexdigest()}"


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    if os.name != "nt":
        path.chmod(0o600)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _provision_keystore(root: Path, key: Ed25519PrivateKey) -> None:
    root.mkdir(mode=0o700, parents=True)
    (root / "private").mkdir(mode=0o700)
    (root / "public").mkdir(mode=0o700)
    public = _raw_public(key)
    key_id = _key_id(public)
    _write_private(root / "birth-keystore.lock", b"0")
    _write_private(root / "public" / f"{key_id}.pub", public)
    _write_private(root / "private" / f"{key_id}.key", key.private_bytes_raw())
    _write_private(root / "keystore.json", _canonical({
        "active_key_id": key_id,
        "config_revision": 1,
        "keys": [{
            "key_id": key_id,
            "public_file": f"public/{key_id}.pub",
            "status": "active",
        }],
        "private_file": f"private/{key_id}.key",
        "schema_version": 1,
    }))


def provision_e2e_birth_bootstrap(user_config: Path) -> Path:
    """Create one complete Birth authority domain under ``user_config``.

    Every signing role gets fresh, distinct key material.  Author, Admission
    and Producer retain only the private keys required by their sealed runtime
    operations.  Approval and semantic registries intentionally contain public
    verification material only.
    """
    birth_dir = user_config / "birth"
    birth_dir.mkdir(mode=0o700, parents=True, exist_ok=False)

    used_publics: set[bytes] = set()

    def distinct_key() -> Ed25519PrivateKey:
        while True:
            key = Ed25519PrivateKey.generate()
            public = _raw_public(key)
            if public not in used_publics:
                used_publics.add(public)
                return key

    _provision_keystore(birth_dir / "author-keystore", distinct_key())
    _provision_keystore(birth_dir / "admission", distinct_key())

    producers = {}
    for index, (producer_id, operation) in enumerate(_PRODUCER_CAPABILITIES):
        store_name = f"producer-{index}"
        _provision_keystore(birth_dir / store_name, distinct_key())
        producers[f"{producer_id}:{operation}"] = {
            "issuer_id": producer_id,
            "keystore": store_name,
            "origin": "synthesized",
            "author": "model",
        }

    approval_key = distinct_key()
    approval_registry = {
        "schema_version": 1,
        "revision": 1,
        "keys": {"e2e-approver-v1": base64.b64encode(
            _raw_public(approval_key)).decode("ascii")},
        "actors": {"e2e-operator": {
            "key_ids": ["e2e-approver-v1"],
            "scopes": ["synthesized", "imported"],
        }},
    }
    _write_private(birth_dir / "approval-authority.json", _canonical(approval_registry))

    semantic_key = distinct_key()
    _write_private(birth_dir / "semantic-review.pub",
                   _raw_public(semantic_key))
    (birth_dir / "semantic-evidence").mkdir(mode=0o700)
    kinds = _EVIDENCE_KINDS
    semantic_review = {
        "evidence_dir": "semantic-evidence",
        "verifiers": {"e2e-semantic-v1": {
            "path": "semantic-review.pub", "status": "active",
        }},
        "versions": {kind: ["v1"] for kind in kinds},
        "owners": {kind: [f"e2e-owner:{kind}"] for kind in kinds},
    }

    # Admission context is explicit evidence.  The property catalog entry
    # identifies the closed, code-owned V1 registry; it does not replace it
    # with caller-controlled property definitions.
    component_names = (
        "standard", "linter", "vocabulary", "authority_registry",
        "sandbox_registry", "property_catalog", "runner", "review_policy",
        "template_allowlist", "primitive_allowlist", "dependency_allowlist",
    )
    context = {
        name: {
            "version": "v1",
            "files": [],
            "configuration": {
                "authority": "e2e-isolated",
                "registry": "PROPERTY_CATALOG_V1" if name == "property_catalog" else name,
            },
        }
        for name in component_names
    }
    config = {
        "schema_version": 1,
        "policy_version": "birth-policy-v1",
        "receipt_ttl_seconds": 3600,
        "admission": {"keystore": "admission"},
        "approval": {
            "db_path": "approval-store/approvals.sqlite",
            "authority_registry": "approval-authority.json",
        },
        "producers": producers,
        "context": context,
        "semantic_review": semantic_review,
    }
    config_path = birth_dir / "bootstrap.json"
    _write_private(config_path, _canonical(config))
    return config_path
