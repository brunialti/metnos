"""Installer custody for the dedicated F6 retention-receipt key.

This optional procedure provisions no executor or deletion permission.
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
from executor_birth_retention_authority import (
    DIRECTORY_BASENAME_V1, HISTORY_BASENAME_V1, ROTATION_BASENAME_V1,
    MAX_REGISTRY_BYTES_V1, PRIVATE_BASENAME_V1,
    REGISTRY_BASENAME_V1, RetentionPublicKeyV1,
    decode_retention_registry_v1, encode_retention_registry_v1, _load_retention_history_at_v1,
    _load_retention_archive_at_v1,
)
from executor_birth_ownership_authorities import (
    DEFAULT_AUTHORITY_DIRECTORY_V1, DEFAULT_OWNERSHIP_ROOT_V1,
    OwnershipAuthorityError, _birth_public_keys_v1, _directory_metadata,
    _load_public_at_v1, _managed_authority_platform_supported_v1,
    _read_regular, _registry_public_bytes, _root_owned_chain,
)


def _verify_pair(
    directory: Path, *, root_owned: bool, forbidden_public_keys: frozenset[bytes],
) -> RetentionPublicKeyV1:
    names = {item.name for item in directory.iterdir()}
    if names not in ({PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1},
                     {PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1, HISTORY_BASENAME_V1}):
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "inventory")
    # Verification cannot create or repair a key, even in an incomplete tree.
    raw_private = _read_regular(
        directory / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=root_owned,
    )
    if len(raw_private) != 32:
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "private key")
    private = Ed25519PrivateKey.from_private_bytes(raw_private)
    public = decode_retention_registry_v1(_read_regular(
        directory / REGISTRY_BASENAME_V1,
        maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned,
    ))
    if private.public_key().public_bytes_raw() != public.public_key.public_bytes_raw():
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "key binding")
    if public.public_key.public_bytes_raw() in forbidden_public_keys:
        raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    if HISTORY_BASENAME_V1 in names:
        for historical in _load_retention_history_at_v1(directory, root_owned=root_owned):
            if historical.public_key.public_bytes_raw() in forbidden_public_keys:
                raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    return public


def _provision_retention_at_v1(
    root: Path, *, root_owned: bool, forbidden_public_keys: frozenset[bytes],
    crash: Callable[[str], None] | None = None,
) -> RetentionPublicKeyV1:
    """Isolated traversal seam sharing the exact native persistence path."""
    if (type(forbidden_public_keys) is not frozenset
            or any(type(key) is not bytes or len(key) != 32 for key in forbidden_public_keys)):
        raise OwnershipAuthorityError("birth_retention_authority_invalid", "key inventory")
    _directory_metadata(root, root_owned=root_owned)
    with _provisioning_lock(root, root_owned=root_owned):
        return _provision_retention_locked_v1(
            root, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys, crash=crash,
        )


def _provision_retention_locked_v1(
    root: Path, *, root_owned: bool, forbidden_public_keys: frozenset[bytes],
    crash: Callable[[str], None] | None = None,
) -> RetentionPublicKeyV1:
    """Caller holds the administrative lock, including other-key observation."""
    final = root / DIRECTORY_BASENAME_V1
    pending = root / f".{DIRECTORY_BASENAME_V1}.pending"
    if _path_present(final):
        if _path_present(pending):
            raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "double transaction")
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
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "pending inventory")
    private = _load_or_create_private(private_path, root_owned=root_owned, crash=crash)
    if private.public_key().public_bytes_raw() in forbidden_public_keys:
        raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    if not _path_present(registry_path):
        _write_exclusive(
            registry_path, encode_retention_registry_v1(private.public_key()),
            0o644, root_owned=root_owned, crash=crash,
        )
    _verify_pair(pending, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)
    pending.chmod(0o755)
    _sync_directory(pending)
    _publish_no_replace(
        pending, final, crash=crash, stage="after_retention_directory_rename",
        expected_identity=_identity(pending.lstat()),
    )
    return _verify_pair(final, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)


def _archive_public(directory: Path, encoded: bytes, *, root_owned: bool,
                    crash: Callable[[str], None] | None) -> None:
    history = directory / HISTORY_BASENAME_V1
    pending = directory / ROTATION_BASENAME_V1 / HISTORY_BASENAME_V1
    if not _path_present(history):
        if not _path_present(pending):
            pending.mkdir(mode=0o700)
            _sync_directory(pending.parent)
            if crash:
                crash("rotation_history_directory_created")
        _pending_metadata(pending, root_owned=root_owned)
        if tuple(pending.iterdir()):
            raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "history staging")
        pending.chmod(0o755)
        _sync_directory(pending)
        _publish_no_replace(pending, history, crash=crash,
                            stage="rotation_history_directory_published",
                            expected_identity=_identity(pending.lstat()))
    elif _path_present(pending):
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "double history")
    _directory_metadata(history, root_owned=root_owned)
    public = decode_retention_registry_v1(encoded)
    path = history / (public.key_id + ".json")
    if not _path_present(path):
        _write_exclusive(path, encoded, 0o644, root_owned=root_owned, crash=None)
    if _read_regular(path, maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned) != encoded:
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "historical key changed")


def _rotate_retention_locked_v1(
    root: Path, *, expected_key_id: str, root_owned: bool,
    forbidden_public_keys: frozenset[bytes], crash: Callable[[str], None] | None = None,
) -> RetentionPublicKeyV1:
    """Resume one key replacement under the native administrative lock.

    The private/public pair is deliberately unavailable during rotation. The
    durable transaction keeps both identities until replacement and readback
    finish. Historical verification material is written before either effect.
    """
    directory = root / DIRECTORY_BASENAME_V1
    transaction = directory / ROTATION_BASENAME_V1
    _directory_metadata(directory, root_owned=root_owned)
    if not _path_present(transaction):
        current = _verify_pair(directory, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)
        if current.key_id != expected_key_id or current.status != "active":
            raise OwnershipAuthorityError("birth_retention_authority_changed")
        transaction.mkdir(mode=0o700)
        _sync_directory(directory)
    _pending_metadata(transaction, root_owned=root_owned)
    directory_names = {item.name for item in directory.iterdir()}
    if directory_names - {PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1, HISTORY_BASENAME_V1, ROTATION_BASENAME_V1}:
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "authority inventory")
    allowed = {"previous.json", PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1,
               "replace-private.bin", "replace-registry.json"}
    allowed |= {_temporary_path(Path(name)).name for name in allowed}
    allowed.add(HISTORY_BASENAME_V1)
    if {item.name for item in transaction.iterdir()} - allowed:
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "rotation inventory")
    # Existing pair may be in the precise intermediate state private=new,
    # public=old; no reader or signer can use it while the marker exists.
    public_path = directory / REGISTRY_BASENAME_V1
    current_raw = _read_regular(public_path, maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned)
    current = decode_retention_registry_v1(current_raw)
    previous_path = transaction / "previous.json"
    if not _path_present(previous_path):
        if not tuple(transaction.iterdir()) and current.key_id != expected_key_id:
            # Last cleanup operation died after removing the intent. Re-read
            # both archived identities and the current private/public binding.
            historical = {key.key_id for key in _load_retention_archive_at_v1(directory, root_owned=root_owned)}
            if expected_key_id not in historical:
                raise OwnershipAuthorityError("birth_retention_authority_changed")
            private = Ed25519PrivateKey.from_private_bytes(_read_regular(
                directory / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=root_owned))
            if private.public_key().public_bytes_raw() != current.public_key.public_bytes_raw():
                raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "key binding")
            transaction.rmdir()
            _sync_directory(directory)
            return _verify_pair(directory, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)
        if current.key_id != expected_key_id or current.status != "active":
            raise OwnershipAuthorityError("birth_retention_authority_changed")
        _write_exclusive(previous_path, current_raw, 0o644, root_owned=root_owned, crash=None)
    previous_raw = _read_regular(previous_path, maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned)
    previous = decode_retention_registry_v1(previous_raw)
    if previous.key_id != expected_key_id or previous.status != "active":
        raise OwnershipAuthorityError("birth_retention_authority_changed")
    if previous.public_key.public_bytes_raw() in forbidden_public_keys:
        raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    next_private_path = transaction / PRIVATE_BASENAME_V1
    next_registry_path = transaction / REGISTRY_BASENAME_V1
    if not _path_present(next_private_path) and current.key_id != previous.key_id:
        # Private staging has already been erased after successful replacement.
        private_raw = _read_regular(directory / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=root_owned)
        private = Ed25519PrivateKey.from_private_bytes(private_raw)
    else:
        if not _path_present(next_private_path) and _path_present(next_registry_path):
            raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "lost staged key")
        private = _load_or_create_private(next_private_path, root_owned=root_owned, crash=crash)
        private_raw = private.private_bytes_raw()
    next_raw = encode_retention_registry_v1(private.public_key())
    successor = decode_retention_registry_v1(next_raw)
    if successor.key_id == previous.key_id or private.public_key().public_bytes_raw() in forbidden_public_keys:
        raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    if _path_present(next_registry_path):
        if _read_regular(next_registry_path, maximum=MAX_REGISTRY_BYTES_V1, mode=0o644, root_owned=root_owned) != next_raw:
            raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "staged key binding")
    elif current.key_id == previous.key_id:
        _write_exclusive(next_registry_path, next_raw, 0o644, root_owned=root_owned, crash=crash)
    current_private_raw = _read_regular(directory / PRIVATE_BASENAME_V1, maximum=32, mode=0o600, root_owned=root_owned)
    current_private = Ed25519PrivateKey.from_private_bytes(current_private_raw).public_key().public_bytes_raw()
    if (current_raw not in (previous_raw, next_raw)
            or current_private not in (previous.public_key.public_bytes_raw(), successor.public_key.public_bytes_raw())
            or (current_raw == next_raw and current_private != successor.public_key.public_bytes_raw())):
        raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "unexpected pair")
    _archive_public(directory, previous_raw, root_owned=root_owned, crash=crash)
    _archive_public(directory, next_raw, root_owned=root_owned, crash=crash)
    archived = _load_retention_archive_at_v1(directory, root_owned=root_owned)
    if any(key.public_key.public_bytes_raw() in forbidden_public_keys for key in archived):
        raise OwnershipAuthorityError("birth_retention_authority_key_reused")
    if crash:
        crash("rotation_history_saved")
    for name, payload, mode in ((PRIVATE_BASENAME_V1, private_raw, 0o600), (REGISTRY_BASENAME_V1, next_raw, 0o644)):
        target, staged = directory / name, transaction / ("replace-" + name)
        observed = _read_regular(target, maximum=len(payload), mode=mode, root_owned=root_owned)
        if observed != payload:
            if not _path_present(staged):
                _write_exclusive(staged, payload, mode, root_owned=root_owned, crash=None)
            if _read_regular(staged, maximum=len(payload), mode=mode, root_owned=root_owned) != payload:
                raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "replacement bytes")
            os.replace(staged, target)
            _sync_directory(directory)
            _sync_directory(transaction)
        if crash:
            crash("rotation_replaced_" + name)
    # Remove private staging only after both destination files were read back.
    for name in (PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1):
        if _read_regular(directory / name, maximum=MAX_REGISTRY_BYTES_V1,
                         mode=0o600 if name == PRIVATE_BASENAME_V1 else 0o644,
                         root_owned=root_owned) != (private_raw if name == PRIVATE_BASENAME_V1 else next_raw):
            raise OwnershipAuthorityError("birth_retention_authority_recovery_required", "rotation readback")
    for name in (PRIVATE_BASENAME_V1, REGISTRY_BASENAME_V1, "previous.json"):
        path = transaction / name
        if _path_present(path):
            path.unlink()
            _sync_directory(transaction)
        if crash:
            crash("rotation_cleaned_" + name)
    transaction.rmdir()
    _sync_directory(directory)
    return _verify_pair(directory, root_owned=root_owned, forbidden_public_keys=forbidden_public_keys)


def _rotate_retention_at_v1(root: Path, *, expected_key_id: str, root_owned: bool,
                            forbidden_public_keys: frozenset[bytes], crash=None):
    _directory_metadata(root, root_owned=root_owned)
    with _provisioning_lock(root, root_owned=root_owned):
        return _rotate_retention_locked_v1(
            root, expected_key_id=expected_key_id, root_owned=root_owned,
            forbidden_public_keys=forbidden_public_keys, crash=crash,
        )


def _administrative_retention_authority_v1(expected_key_id: str | None) -> RetentionPublicKeyV1:
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError("birth_retention_authority_platform_unsupported")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise OwnershipAuthorityError("birth_retention_authority_root_required")
    _root_owned_chain(DEFAULT_OWNERSHIP_ROOT_V1)
    _root_owned_chain(DEFAULT_AUTHORITY_DIRECTORY_V1)
    with _provisioning_lock(DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True):
        owners = _load_public_at_v1(DEFAULT_AUTHORITY_DIRECTORY_V1, root_owned=True)
        forbidden = _birth_public_keys_v1() | frozenset(
            _registry_public_bytes(registry)
            for registry in (owners.distribution, owners.cutover, owners.head)
        )
        # An optional F5 key may already exist. Absence is allowed during joint
        # preparation; any present registry must be authentic, including revocation.
        from executor_birth_certification_authority import (
            DEFAULT_DIRECTORY_V1 as certification_directory,
            _load_certification_public_at_v1,
        )
        if _path_present(certification_directory):
            _root_owned_chain(certification_directory)
            certification = _load_certification_public_at_v1(
                certification_directory, root_owned=True,
            )
            forbidden |= frozenset({certification.public_key.public_bytes_raw()})
        if expected_key_id is None:
            return _provision_retention_locked_v1(
                DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True, forbidden_public_keys=forbidden,
            )
        return _rotate_retention_locked_v1(
            DEFAULT_OWNERSHIP_ROOT_V1, root_owned=True, forbidden_public_keys=forbidden,
            expected_key_id=expected_key_id,
        )


def provision_retention_authority_v1() -> RetentionPublicKeyV1:
    """Provision at the fixed native root and return public material only."""
    return _administrative_retention_authority_v1(None)


def rotate_retention_authority_v1(*, expected_key_id: str) -> RetentionPublicKeyV1:
    if type(expected_key_id) is not str or not expected_key_id:
        raise OwnershipAuthorityError("birth_retention_authority_invalid", "expected key")
    return _administrative_retention_authority_v1(expected_key_id)


__all__ = ["provision_retention_authority_v1", "rotate_retention_authority_v1"]
