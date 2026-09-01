from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from sign import (
    ManifestSignatureError,
    sign_manifest_bytes,
    verify_manifest_bytes,
)


def test_byte_crypto_is_pure_and_uses_preloaded_key_objects(monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    payload = b'name = "sample"\n'

    def filesystem_forbidden(*_args, **_kwargs):
        raise AssertionError("pure crypto must not access the filesystem")

    monkeypatch.setattr(Path, "open", filesystem_forbidden)
    monkeypatch.setattr(Path, "read_bytes", filesystem_forbidden)
    signature = sign_manifest_bytes(payload, private_key=private)
    identity = verify_manifest_bytes(
        payload,
        signature,
        trusted_publics=(("test-author", private.public_key()),),
    )

    assert identity.name == "test-author"


def test_byte_crypto_rejects_different_bytes() -> None:
    private = Ed25519PrivateKey.generate()
    signature = sign_manifest_bytes(b"first", private_key=private)

    with pytest.raises(ManifestSignatureError):
        verify_manifest_bytes(
            b"second",
            signature,
            trusted_publics=(("test-author", private.public_key()),),
        )


def test_authoring_resolution_keeps_disabled_contracts_publishable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import manifest_inventory
    import sign

    directory = tmp_path / "disabled"
    directory.mkdir()
    manifest_path = directory / "manifest.toml"
    manifest_path.write_text('name = "disabled_executor"\n', encoding="utf-8")
    ref = SimpleNamespace(
        manifest_path=manifest_path,
        status=manifest_inventory.ManifestStatus.DISABLED,
        contract_id="user-skill:disabled/manifest.toml",
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda: SimpleNamespace(manifests=(ref,)),
    )

    assert sign._authoring_manifest_ref(directory) is ref


def test_authoring_resolution_rejects_source_retired_contracts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import manifest_inventory
    import sign

    directory = tmp_path / "retired"
    directory.mkdir()
    manifest_path = directory / "manifest.toml"
    manifest_path.write_text('name = "retired_executor"\n', encoding="utf-8")
    ref = SimpleNamespace(
        manifest_path=manifest_path,
        status=manifest_inventory.ManifestStatus.RETIRED,
        contract_id="user-skill:retired/manifest.toml",
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda: SimpleNamespace(manifests=(ref,)),
    )

    with pytest.raises(ValueError, match="not publishable"):
        sign._authoring_manifest_ref(directory)
