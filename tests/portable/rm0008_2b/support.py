"""Shared apparatus for the increment 2B certification.

Every root here is a temporary one and the installer configuration is pointed
at it, so the real installation of the machine that runs the suite is never
opened, read or written.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BUILD = "rm0008-group2-2b"


def provisioner():
    return importlib.import_module("install.birth_authority_provisioner")


def installer_layout_module():
    return importlib.import_module("install.birth_authority_provisioning")


def private_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    os.chmod(path, mode)


def make_config(tmp_path: Path, *, author=None, extra=(), operator=False) -> Path:
    """Build one isolated installer configuration root.

    ``operator`` installs the ordinary, valid public registries; the tests that
    certify their absence or their defects install their own instead.
    """
    base = tmp_path / "config"
    (base / "birth" / "operator-input-v1").mkdir(mode=0o755, parents=True)
    os.chmod(base / "birth", 0o755)
    if author is not None or extra:
        keys = base / "keys"
        keys.mkdir(mode=0o700)
        if author is not None:
            write(keys / "author_priv.bin", private_bytes(author), 0o600)
            write(keys / "author_pub.bin", public_bytes(author), 0o644)
        for name, payload, mode in extra:
            write(keys / name, payload, mode)
    if operator:
        complete_operator_input(base)
    return base


def use_config(monkeypatch, base: Path) -> None:
    """Point every installer resolver at the temporary configuration root."""
    runtime_config = importlib.import_module("config")
    monkeypatch.setattr(runtime_config, "PATH_USER_CONFIG", base)


def open_layout(monkeypatch, base: Path):
    use_config(monkeypatch, base)
    return installer_layout_module().open_birth_provisioning_layout_v1()


def provision(monkeypatch, base: Path):
    """Run one whole provisioning attempt on its own session."""
    module = provisioner()
    layout = open_layout(monkeypatch, base)
    try:
        return module.provision_author_root_v1(
            layout, provisioner_build_id=BUILD,
        )
    finally:
        layout.birth_session.close()


def canonical_json(value: object) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def approval_document(actors=None) -> bytes:
    """One canonical operator approval registry, with no private key."""
    import base64

    key = public_bytes(Ed25519PrivateKey.generate())
    return canonical_json({
        "actors": actors if actors is not None else {
            "operator": {"key_ids": ["operator-key"], "scopes": ["birth"]}
        },
        "keys": {"operator-key": base64.b64encode(key).decode("ascii")},
        "revision": 1,
        "schema_version": 1,
    })


def semantic_document(names=("review.pub",), *, revoked=()) -> bytes:
    import importlib

    review = importlib.import_module("executor_birth_semantic_review")
    kinds = sorted(item.value for item in review.IndependentEvidenceKind)
    verifiers = {
        f"review-key-{index}": {
            "path": f"public/{name}",
            "status": "revoked" if name in revoked else "active",
        }
        for index, name in enumerate(names)
    }
    return canonical_json({
        "evidence_dir": "evidence",
        "owners": {kind: ["independent-owner"] for kind in kinds},
        "verifiers": verifiers,
        "versions": {kind: ["v1"] for kind in kinds},
    })


def install_operator_input(
    base: Path, *, approval: bytes | None = None, semantic: bytes | None = None,
    keys=None,
) -> None:
    """Write the two public registries the administrator is expected to install."""
    location = base / "birth" / "operator-input-v1"
    if approval is not None:
        write(location / "approval-authority.json", approval, 0o644)
    if semantic is not None:
        write(location / "semantic-authority.json", semantic, 0o644)
    if keys is not None:
        container = location / "semantic-public"
        container.mkdir(mode=0o755, exist_ok=True)
        for name, payload in keys.items():
            write(container / name, payload, 0o644)


def complete_operator_input(base: Path) -> dict[str, bytes]:
    """The ordinary, valid operator input of a supported installation."""
    keys = {"review.pub": public_bytes(Ed25519PrivateKey.generate())}
    install_operator_input(
        base,
        approval=approval_document(),
        semantic=semantic_document(tuple(keys)),
        keys=keys,
    )
    return keys


def transaction_root(base: Path) -> Path:
    """The one transaction directory of an isolated root."""
    module = provisioner()
    roots = [
        item for item in (base / "birth").iterdir()
        if item.name.startswith(module.TRANSACTION_PREFIX_V1)
    ]
    assert len(roots) == 1, roots
    return roots[0]


def staged_author_store(base: Path) -> Path:
    return transaction_root(base) / "author-root-v1"
