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


def make_config(tmp_path: Path, *, author=None, extra=()) -> Path:
    """Build one isolated installer configuration root."""
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
