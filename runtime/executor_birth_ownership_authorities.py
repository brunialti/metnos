"""Cold loaders for the three separate root-owned F4 deployment authorities.

The mutating provisioner lives on the installer side.  This module owns only
the canonical public-registry codec and the fixed, handle-checked cold load.
The productive entries never accept a path.  Private keys are returned only
to a root process; startup consumers receive public registries only.
"""
from __future__ import annotations

import base64
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from executor_birth_distribution_manifest import (
    PURPOSE as DISTRIBUTION_PURPOSE,
    DistributionKey, DistributionRegistry, distribution_key_id,
)
from executor_birth_ownership_cutover import (
    PURPOSE as CUTOVER_PURPOSE,
    OwnershipCutoverKey, OwnershipCutoverRegistry, ownership_key_id,
)
from executor_birth_ownership_chain import HEAD_PURPOSE


DEFAULT_OWNERSHIP_ROOT_V1 = Path("/var/lib/metnos/executor-birth")
AUTHORITY_DIRECTORY_BASENAME_V1 = "authorities-v1"
DEFAULT_AUTHORITY_DIRECTORY_V1 = (
    DEFAULT_OWNERSHIP_ROOT_V1 / AUTHORITY_DIRECTORY_BASENAME_V1
)
MAX_REGISTRY_BYTES_V1 = 64 * 1024
PRIVATE_KEY_BYTES_V1 = 32

_KINDS = ("distribution", "cutover", "head")
_PURPOSES = {
    "distribution": DISTRIBUTION_PURPOSE,
    "cutover": CUTOVER_PURPOSE,
    "head": HEAD_PURPOSE,
}
_PRIVATE_BASENAMES = {
    kind: f"{kind}-private-v1.bin" for kind in _KINDS
}
_REGISTRY_BASENAMES = {
    kind: f"{kind}-registry-v1.json" for kind in _KINDS
}
_CHECKPOINT_BASENAMES = tuple(
    f"checkpoint-{index:03d}-v1.json" for index in range(len(_KINDS) + 2)
)
_REGISTRY_KEYS = frozenset({
    "schema_version", "authority", "key_id", "public_key", "purposes",
    "first_release_sequence", "last_release_sequence",
})


class OwnershipAuthorityError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "registry json",
        ) from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_invalid", "duplicate registry key",
            )
        result[key] = value
    return result


def _raw_public(public_key: Ed25519PublicKey) -> bytes:
    if not isinstance(public_key, Ed25519PublicKey):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "public key",
        )
    return public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )


def _key_id(kind: str, public_key: Ed25519PublicKey) -> str:
    if kind == "distribution":
        return distribution_key_id(public_key)
    return ownership_key_id(public_key)


def encode_ownership_registry_v1(
    kind: str, public_key: Ed25519PublicKey,
) -> bytes:
    """Encode one single-purpose public registry with a closed schema."""
    if kind not in _PURPOSES:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "authority kind",
        )
    return _canonical({
        "schema_version": 1,
        "authority": kind,
        "key_id": _key_id(kind, public_key),
        "public_key": base64.b64encode(_raw_public(public_key)).decode("ascii"),
        "purposes": [_PURPOSES[kind]],
        "first_release_sequence": 1 if kind == "distribution" else None,
        "last_release_sequence": None,
    })


def decode_ownership_registry_v1(
    encoded: bytes, *, expected_kind: str,
) -> DistributionRegistry | OwnershipCutoverRegistry:
    """Decode exactly one registry; multipurpose documents never enter memory."""
    if (
        expected_kind not in _PURPOSES
        or not isinstance(encoded, bytes)
        or len(encoded) > MAX_REGISTRY_BYTES_V1
    ):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "registry size",
        )
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except OwnershipAuthorityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "registry json",
        ) from exc
    schema_version = value.get("schema_version") if isinstance(value, dict) else None
    first_sequence = (
        value.get("first_release_sequence") if isinstance(value, dict) else None
    )
    if (
        not isinstance(value, dict)
        or set(value) != _REGISTRY_KEYS
        or _canonical(value) != encoded
        or type(schema_version) is not int
        or schema_version != 1
        or value.get("authority") != expected_kind
        or value.get("purposes") != [_PURPOSES[expected_kind]]
        or value.get("last_release_sequence") is not None
        or (
            expected_kind == "distribution"
            and (type(first_sequence) is not int or first_sequence != 1)
        )
        or (expected_kind != "distribution" and first_sequence is not None)
    ):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "registry schema",
        )
    raw = value.get("public_key")
    try:
        if not isinstance(raw, str):
            raise ValueError("public key")
        public_bytes = base64.b64decode(raw, validate=True)
        if len(public_bytes) != 32:
            raise ValueError("public key length")
        if base64.b64encode(public_bytes).decode("ascii") != raw:
            raise ValueError("non-canonical public key")
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
    except (ValueError, TypeError) as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "public key",
        ) from exc
    key_id = value.get("key_id")
    if key_id != _key_id(expected_kind, public_key):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "key id",
        )
    purpose = frozenset({_PURPOSES[expected_kind]})
    if expected_kind == "distribution":
        key = DistributionKey(str(key_id), public_key, purpose, 1, None)
        return DistributionRegistry({key.key_id: key})
    key = OwnershipCutoverKey(str(key_id), public_key, purpose)
    return OwnershipCutoverRegistry({key.key_id: key})


def _directory_metadata(path: Path, *, root_owned: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_missing", path.name,
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
        or (hasattr(path, "is_junction") and path.is_junction())
        or stat.S_IMODE(info.st_mode) != 0o755
        or info.st_mode & 0o022
        or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
    ):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_unsafe", path.name,
        )


def _root_owned_chain(path: Path) -> None:
    if not path.is_absolute():
        raise OwnershipAuthorityError(
            "birth_ownership_authority_unsafe", "non-absolute root",
        )
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_missing", component.name,
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", component.name,
            )


def _read_regular(
    path: Path, *, maximum: int, mode: int, root_owned: bool,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_missing", path.name,
        ) from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or (root_owned and (before.st_uid != 0 or before.st_gid != 0))
            or before.st_size > maximum
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", path.name,
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(fd)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if (
            len(payload) > maximum
            or len(payload) != before.st_size
            or identity(before) != identity(after)
        ):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_unsafe", path.name,
            )
        return payload
    finally:
        os.close(fd)


def _registry_public_bytes(
    registry: DistributionRegistry | OwnershipCutoverRegistry,
) -> bytes:
    entries = tuple(registry.keys.values())
    if len(entries) != 1:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "registry cardinality",
        )
    return _raw_public(entries[0].public_key)


def _checkpoint_payload(index: int) -> bytes:
    if index < 0 or index >= len(_CHECKPOINT_BASENAMES):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "checkpoint",
        )
    completed = list(_KINDS[:min(index, len(_KINDS))])
    state = "verified" if index == len(_KINDS) + 1 else "preparing"
    if index == len(_KINDS):
        state = "complete"
    return _canonical({
        "schema_version": 1,
        "checkpoint_sequence": index,
        "state": state,
        "completed": completed,
    })


def _verify_final_inventory(directory: Path, *, root_owned: bool) -> None:
    expected = {
        *_PRIVATE_BASENAMES.values(), *_REGISTRY_BASENAMES.values(),
        *_CHECKPOINT_BASENAMES,
    }
    try:
        before = tuple(sorted(item.name for item in directory.iterdir()))
        if set(before) != expected or len(before) != len(expected):
            raise OwnershipAuthorityError(
                "birth_ownership_authority_recovery_required", "inventory",
            )
        for index, basename in enumerate(_CHECKPOINT_BASENAMES):
            payload = _read_regular(
                directory / basename, maximum=4096, mode=0o644,
                root_owned=root_owned,
            )
            if payload != _checkpoint_payload(index):
                raise OwnershipAuthorityError(
                    "birth_ownership_authority_recovery_required", "checkpoint",
                )
        for basename in _PRIVATE_BASENAMES.values():
            info = (directory / basename).lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or bool(getattr(info, "st_file_attributes", 0) & 0x400)
                or info.st_nlink != 1
                or info.st_size != PRIVATE_KEY_BYTES_V1
                or stat.S_IMODE(info.st_mode) != 0o600
                or (root_owned and (info.st_uid != 0 or info.st_gid != 0))
            ):
                raise OwnershipAuthorityError(
                    "birth_ownership_authority_unsafe", basename,
                )
        after = tuple(sorted(item.name for item in directory.iterdir()))
    except OwnershipAuthorityError:
        raise
    except OSError as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "inventory",
        ) from exc
    if before != after:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_recovery_required", "inventory changed",
        )


@dataclass(frozen=True, slots=True)
class OwnershipPublicRegistriesV1:
    distribution: DistributionRegistry
    cutover: OwnershipCutoverRegistry
    head: OwnershipCutoverRegistry
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _PUBLIC_SEAL:
            raise OwnershipAuthorityError("birth_ownership_authority_untrusted")
        raw = (
            _registry_public_bytes(self.distribution),
            _registry_public_bytes(self.cutover),
            _registry_public_bytes(self.head),
        )
        if len(set(raw)) != 3:
            raise OwnershipAuthorityError("birth_ownership_authority_key_reused")


@dataclass(frozen=True, slots=True)
class RootOwnershipAuthoritiesV1:
    public: OwnershipPublicRegistriesV1
    distribution_private: Ed25519PrivateKey
    cutover_private: Ed25519PrivateKey
    head_private: Ed25519PrivateKey
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _PRIVATE_SEAL or self.public._seal is not _PUBLIC_SEAL:
            raise OwnershipAuthorityError("birth_ownership_authority_untrusted")
        pairs = (
            (self.distribution_private, self.public.distribution),
            (self.cutover_private, self.public.cutover),
            (self.head_private, self.public.head),
        )
        for private, registry in pairs:
            if (
                not isinstance(private, Ed25519PrivateKey)
                or _raw_public(private.public_key()) != _registry_public_bytes(registry)
            ):
                raise OwnershipAuthorityError(
                    "birth_ownership_authority_invalid", "private binding",
                )


_PUBLIC_SEAL = object()
_PRIVATE_SEAL = object()


@dataclass(frozen=True, slots=True)
class _FixedOwnershipPublicSnapshotV1:
    public: OwnershipPublicRegistriesV1
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _FIXED_PUBLIC_SNAPSHOT_SEAL
            or not isinstance(self.public, OwnershipPublicRegistriesV1)
            or self.public._seal is not _PUBLIC_SEAL
        ):
            raise OwnershipAuthorityError("birth_ownership_authority_untrusted")


_FIXED_PUBLIC_SNAPSHOT_SEAL = object()


def _ownership_public_registries_for_test(
    distribution: DistributionRegistry,
    cutover: OwnershipCutoverRegistry,
    head: OwnershipCutoverRegistry,
) -> OwnershipPublicRegistriesV1:
    """Build an in-memory bundle only for portable algorithm tests.

    Productive callers cannot select this bundle: their public entry points
    always cold-load the fixed, root-owned Linux deployment directory.
    """
    return OwnershipPublicRegistriesV1(
        distribution, cutover, head, _PUBLIC_SEAL,
    )


def _root_ownership_authorities_for_test(
    distribution_private: Ed25519PrivateKey,
    cutover_private: Ed25519PrivateKey,
    head_private: Ed25519PrivateKey,
) -> RootOwnershipAuthoritiesV1:
    """Build private authorities only for portable coordinator tests.

    The productive private loader remains bound to the root-owned Linux
    deployment directory; this seam exercises only the portable algorithms.
    """
    private_keys = (
        distribution_private, cutover_private, head_private,
    )
    if any(not isinstance(key, Ed25519PrivateKey) for key in private_keys):
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "private key",
        )
    public = _ownership_public_registries_for_test(
        decode_ownership_registry_v1(
            encode_ownership_registry_v1(
                "distribution", distribution_private.public_key(),
            ),
            expected_kind="distribution",
        ),
        decode_ownership_registry_v1(
            encode_ownership_registry_v1(
                "cutover", cutover_private.public_key(),
            ),
            expected_kind="cutover",
        ),
        decode_ownership_registry_v1(
            encode_ownership_registry_v1(
                "head", head_private.public_key(),
            ),
            expected_kind="head",
        ),
    )
    return RootOwnershipAuthoritiesV1(
        public, distribution_private, cutover_private, head_private,
        _PRIVATE_SEAL,
    )


def _managed_authority_platform_supported_v1() -> bool:
    """The G5-A administrative authority surface is Linux-only."""
    return sys.platform.startswith("linux")


def _load_public_at_v1(
    directory: Path, *, root_owned: bool,
) -> OwnershipPublicRegistriesV1:
    directory = Path(directory)
    _directory_metadata(directory, root_owned=root_owned)
    _verify_final_inventory(directory, root_owned=root_owned)
    registries = {
        kind: decode_ownership_registry_v1(
            _read_regular(
                directory / _REGISTRY_BASENAMES[kind],
                maximum=MAX_REGISTRY_BYTES_V1, mode=0o644,
                root_owned=root_owned,
            ),
            expected_kind=kind,
        )
        for kind in _KINDS
    }
    distribution = registries["distribution"]
    cutover = registries["cutover"]
    head = registries["head"]
    if (
        not isinstance(distribution, DistributionRegistry)
        or not isinstance(cutover, OwnershipCutoverRegistry)
        or not isinstance(head, OwnershipCutoverRegistry)
    ):
        raise OwnershipAuthorityError("birth_ownership_authority_invalid")
    return OwnershipPublicRegistriesV1(
        distribution, cutover, head, _PUBLIC_SEAL,
    )


def _load_private_at_v1(
    directory: Path, *, root_owned: bool,
) -> RootOwnershipAuthoritiesV1:
    public = _load_public_at_v1(directory, root_owned=root_owned)
    private: dict[str, Ed25519PrivateKey] = {}
    for kind in _KINDS:
        encoded = _read_regular(
            directory / _PRIVATE_BASENAMES[kind],
            maximum=PRIVATE_KEY_BYTES_V1, mode=0o600,
            root_owned=root_owned,
        )
        if len(encoded) != PRIVATE_KEY_BYTES_V1:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_invalid", "private key size",
            )
        try:
            private[kind] = Ed25519PrivateKey.from_private_bytes(encoded)
        except ValueError as exc:
            raise OwnershipAuthorityError(
                "birth_ownership_authority_invalid", "private key",
            ) from exc
    return RootOwnershipAuthoritiesV1(
        public, private["distribution"], private["cutover"], private["head"],
        _PRIVATE_SEAL,
    )


def _require_public_keys_disjoint_v1(
    authorities: OwnershipPublicRegistriesV1,
    forbidden_public_keys: Iterable[bytes],
) -> None:
    observed = {
        _registry_public_bytes(authorities.distribution),
        _registry_public_bytes(authorities.cutover),
        _registry_public_bytes(authorities.head),
    }
    try:
        forbidden = {bytes(item) for item in forbidden_public_keys}
    except (TypeError, ValueError) as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_invalid", "forbidden key inventory",
        ) from exc
    if any(len(item) != 32 for item in forbidden) or observed & forbidden:
        raise OwnershipAuthorityError("birth_ownership_authority_key_reused")


def _require_no_reused_public_keys_v1(
    authorities: RootOwnershipAuthoritiesV1,
    forbidden_public_keys: Iterable[bytes],
) -> None:
    _require_public_keys_disjoint_v1(
        authorities.public, forbidden_public_keys,
    )


def _birth_public_keys_v1() -> frozenset[bytes]:
    """Reload public Birth trust through its fixed authenticated door."""
    from executor_birth_prepared_root import _birth_public_inventory_v1

    try:
        result = _birth_public_inventory_v1()
    except Exception as exc:
        raise OwnershipAuthorityError(
            "birth_ownership_authority_untrusted",
        ) from exc
    if not result or any(len(item) != 32 for item in result):
        raise OwnershipAuthorityError("birth_ownership_authority_untrusted")
    return result


def load_ownership_public_registries_v1() -> OwnershipPublicRegistriesV1:
    """Cold-load public trust from the one fixed administrative directory."""
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError(
            "birth_ownership_authority_platform_unsupported",
        )
    _root_owned_chain(DEFAULT_AUTHORITY_DIRECTORY_V1)
    loaded = _load_public_at_v1(
        DEFAULT_AUTHORITY_DIRECTORY_V1, root_owned=True,
    )
    _require_public_keys_disjoint_v1(loaded, _birth_public_keys_v1())
    return loaded


def _load_fixed_ownership_public_snapshot_v1() -> _FixedOwnershipPublicSnapshotV1:
    """Seal one all-domain snapshot obtained through the fixed cold loader."""
    return _FixedOwnershipPublicSnapshotV1(
        load_ownership_public_registries_v1(), _FIXED_PUBLIC_SNAPSHOT_SEAL,
    )


def load_root_ownership_authorities_v1() -> RootOwnershipAuthoritiesV1:
    """Cold-load signing authority only inside the root coordinator."""
    if not _managed_authority_platform_supported_v1():
        raise OwnershipAuthorityError(
            "birth_ownership_authority_platform_unsupported",
        )
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise OwnershipAuthorityError("birth_ownership_authority_root_required")
    _root_owned_chain(DEFAULT_AUTHORITY_DIRECTORY_V1)
    loaded = _load_private_at_v1(
        DEFAULT_AUTHORITY_DIRECTORY_V1, root_owned=True,
    )
    _require_no_reused_public_keys_v1(loaded, _birth_public_keys_v1())
    return loaded


__all__ = [
    "AUTHORITY_DIRECTORY_BASENAME_V1", "DEFAULT_AUTHORITY_DIRECTORY_V1",
    "OwnershipAuthorityError", "OwnershipPublicRegistriesV1",
    "RootOwnershipAuthoritiesV1", "decode_ownership_registry_v1",
    "encode_ownership_registry_v1", "load_ownership_public_registries_v1",
    "load_root_ownership_authorities_v1",
]
