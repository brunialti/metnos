"""Installer custody for the dedicated F5 evidence-certification key.

This optional procedure provisions no executor, certificate or activation.
It deliberately leaves the mandatory F4 authority inventory unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from install.birth_ownership_authority_provisioner import (
    _identity, _load_or_create_private, _path_present, _pending_metadata,
    _provisioning_lock, _publish_no_replace, _sync_directory, _temporary_path,
    _write_exclusive,
)
from executor_birth_certification_authority import (
    DIRECTORY_BASENAME_V1, MAX_REGISTRY_BYTES_V1, PRIVATE_BASENAME_V1,
    REGISTRY_BASENAME_V1, CertificationPublicKeyV1,
    decode_certification_registry_v1, encode_certification_registry_v1,
)
from executor_birth_ownership_authorities import (
    DEFAULT_AUTHORITY_DIRECTORY_V1, DEFAULT_OWNERSHIP_ROOT_V1,
    OwnershipAuthorityError, _birth_public_keys_v1, _directory_metadata,
    _load_public_at_v1, _managed_authority_platform_supported_v1,
    _read_regular, _registry_public_bytes, _root_owned_chain,
)


def _verify_pair(
    directory: Path, *, root_owned: bool, forbidden_public_keys: frozenset[bytes],
) -> CertificationPublicKeyV1:
    if {item.name for item in directory.iterdir()} != {PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1}:
        raise OwnershipAuthorityError("birth_certification_authority_recovery_required", "inventory")
    # Verification cannot create or repair a key, even in an incomplete tree.
    raw_private = _read_regular(
        directory / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=root_owned,
    )
    if len(raw_private) != 32:
        raise OwnershipAuthorityError("birth_certification_authority_recovery_required", "private key")
    private = Ed25519PrivateKey.from_private_bytes(raw_private)
    public = decode_certification_registry_v1(_read_regular(
        directory / REGISTRY_BASENAME_V1,
        maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned,
    ))
    if private.public_key().public_bytes_raw() != public.public_key.public_bytes_raw():
        raise OwnershipAuthorityError("birth_certification_authority_recovery_required", "key binding")
    if public.public_key.public_bytes_raw() in forbidden_public_keys:
        raise OwnershipAuthorityError("birth_certification_authority_key_reused")
    return public


def _provision_certification_at_v1(
    root: Path, *, root_owned: bool, forbidden_public_keys: frozenset[bytes],
    crash: Callable[[str], None] | None = None,
) -> CertificationPublicKeyV1:
    """Isolated traversal seam sharing the exact native persistence path."""
    if (type(forbidden_public_keys) is not frozenset
            or any(type(key) is not bytes or len(key) != 32 for key in forbidden_public_keys)):
        raise OwnershipAuthorityError("birth_certification_authority_invalid", "key inventory")
    _directory_metadata(root, root_owned=root_owned)
    final = root / DIRECTORY_BASENAME_V1
    pending = root / f".{DIRECTORY_BASENAME_V1}.pending"
    with _provisioning_lock(root, root_owned=root_owned):
        if _path_present(final):
            if _path_present(pending):
                raise OwnershipAuthorityError("birth_certification_authority_recovery_required", "double transaction")
            _directory_metadata(final, root_owned=root_owned)
            result = _verify_pair(final, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)
            _sync_directory(root)
            return result
        if not _path_present(pending):
            pending.mkdir(mode=0o700)
            _sync_directory(root)
        _pending_metadata(pending, root_owned=root_owned)
        private_path, registry_path = pending / PRIVATE_BASENAME_V1, pending / REGISTRY_BASENAME_V1
        names = {item.name for item in pending.iterdir()}
        allowed = {PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1}
        allowed.update(_temporary_path(path).name for path in (private_path, registry_path))
        if (names - allowed
                or any(_path_present(path) and _path_present(_temporary_path(path))
                       for path in (private_path, registry_path))
                or ((_path_present(registry_path) or _path_present(_temporary_path(registry_path)))
                    and not _path_present(private_path))):
            raise OwnershipAuthorityError("birth_certification_authority_recovery_required", "pending inventory")
        private = _load_or_create_private(private_path, root_owned=root_owned, crash=crash)
        if private.public_key().public_bytes_raw() in forbidden_public_keys:
            raise OwnershipAuthorityError("birth_certification_authority_key_reused")
        if not _path_present(registry_path):
            _write_exclusive(
                registry_path, encode_certification_registry_v1(private.public_key()),
                0o644, root_owned=root_owned, crash=crash,
            )
        _verify_pair(pending, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)
        pending.chmod(0o755)
        _sync_directory(pending)
        _publish_no_replace(
            pending, final, crash=crash, stage="after_certification_directory_rename",
            expected_identity=_identity(pending.lstat()),
        )
        return _verify_pair(final, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)


def provision_certification_authority_v1() -> CertificationPublicKeyV1:
    """Provision at the fixed native root and return public material only."""
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_certification_authority_platform_unsupported")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise OwnershipAuthorityError("birth_certification_authority_root_required")
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    _root_owned_chain(DEFAULT_AUTHORITY_DIRECTORY_V1)
    owners = _load_public_at_v1(DEFAULT_AUTHORITY_DIRECTORY_V1, root_owned=True)
    forbidden = _birth_public_keys_v1() | frozenset(
        _registry_public_bytes(registry)
        for registry in (owners.distribution, owners.cutover, owners.head)
    )
    return _provision_certification_at_v1(
        DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True, forbidden_public_keys=forbidden,
    )


__all__ = ["provision_certification_authority_v1"]
