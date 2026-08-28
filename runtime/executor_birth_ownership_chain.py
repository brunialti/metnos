"""Portable append-only ownership-head codec and contiguous-chain verifier.

The signed distribution verifier remains the sole authority for build
objects.  This module accepts only its sealed :class:`VerifiedDistribution`;
it never invents build trust from bytes, filenames or a caller callback.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from executor_birth_ownership_cutover import (
    MAX_PAYLOAD_BYTES, PAYLOAD_BASENAME, SIGNATURE_BASENAME,
    OwnershipCutoverCertificate, OwnershipCutoverError,
    OwnershipCutoverRegistry,
    _publish_no_replace, _safe_directory, _safe_read, _sync_directory,
    _write_temporary, verify_ownership_cutover_certificate,
    PURPOSE as CUTOVER_PURPOSE,
)
from executor_birth_distribution_manifest import (
    AuthenticatedDistributionRecordV1,
    VerifiedDistribution, _AuthenticatedDistributionRecordForTestV1,
    _authenticate_distribution_record_for_test,
    _authenticate_distribution_record_from_fixed_snapshot_v1,
    _is_authenticated_distribution_record_v1,
    _verify_authenticated_distribution_record_for_test,
    is_verified_distribution,
    verify_installed_distribution_record_v1,
)

if TYPE_CHECKING:
    from executor_birth_ownership_authorities import OwnershipPublicRegistriesV1


HEAD_ID_DOMAIN = b"metnos.executor-birth.ownership-head-id/v1\0"
HEAD_SIGNATURE_DOMAIN = b"metnos.executor-birth.ownership-head/v1\0"
HEAD_PURPOSE = "ownership_head_v1"
REQUIRED_HEAD_MAGIC = b"metnos-ownership-required-head-v1\0"
REQUIRED_HEAD_BASENAME = "required-head-v1.bin"
MAX_HEAD_BYTES = 16 * 1024
MAX_REQUIRED_HEAD_BYTES = len(REQUIRED_HEAD_MAGIC) + 4 + MAX_HEAD_BYTES + 64
MAX_BUILD_BYTES = 16 * 1024 * 1024
DEFAULT_OWNERSHIP_CHAIN_ROOT_V1 = Path(
    "/var/lib/metnos/executor-birth/chain-v1"
)
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_KEY_ID_RE = re.compile(r"birth-ed25519-v1-sha256-[0-9a-f]{64}\Z")
_HEAD_KEYS = frozenset({
    "schema_version", "release_sequence", "cutover_id", "closed_build_id",
    "previous_head_id", "head_id", "signing_key_id",
})
_BUILD_ID_DOMAIN = b"metnos.executor-birth.closed-build-id/v1\0"


class OwnershipChainError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class _OwnershipChainCrashForTest(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OwnershipHead:
    release_sequence: int
    cutover_id: str
    closed_build_id: str
    previous_head_id: str | None
    head_id: str
    signing_key_id: str
    encoded: bytes
    signature: bytes


@dataclass(frozen=True, slots=True)
class VerifiedOwnershipChain:
    anchor_cutover_id: str
    heads: tuple[OwnershipHead, ...]
    authenticated_records: tuple[
        AuthenticatedDistributionRecordV1
        | _AuthenticatedDistributionRecordForTestV1, ...
    ] = ()
    required_distribution: VerifiedDistribution | None = None

    @property
    def required_head(self) -> OwnershipHead:
        return self.heads[-1]


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "json") from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "duplicate key",
            )
        result[key] = value
    return result


def _digest(value: object, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", field)
    return value


def _head_id(value: Mapping[str, object]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "head_id"}
    return "sha256:" + hashlib.sha256(HEAD_ID_DOMAIN + _canonical(unsigned)).hexdigest()


def issue_ownership_head(
    *, release_sequence: int, cutover_id: str, closed_build_id: str,
    previous_head_id: str | None, signing_key_id: str,
    private_key: Ed25519PrivateKey,
) -> tuple[bytes, bytes]:
    if (
        isinstance(release_sequence, bool) or not isinstance(release_sequence, int)
        or release_sequence <= 0
    ):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "release_sequence")
    _digest(cutover_id, "cutover_id")
    _digest(closed_build_id, "closed_build_id")
    _digest(previous_head_id, "previous_head_id", nullable=True)
    if _KEY_ID_RE.fullmatch(signing_key_id or "") is None:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "signing_key_id")
    if not isinstance(private_key, Ed25519PrivateKey):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "private_key")
    value: dict[str, object] = {
        "schema_version": 1,
        "release_sequence": release_sequence,
        "cutover_id": cutover_id,
        "closed_build_id": closed_build_id,
        "previous_head_id": previous_head_id,
        "head_id": None,
        "signing_key_id": signing_key_id,
    }
    value["head_id"] = _head_id(value)
    encoded = _canonical(value)
    return encoded, private_key.sign(HEAD_SIGNATURE_DOMAIN + encoded)


def verify_ownership_head(
    encoded: bytes, signature: bytes, *, registry: OwnershipCutoverRegistry,
) -> OwnershipHead:
    if not isinstance(encoded, bytes) or len(encoded) > MAX_HEAD_BYTES:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "head size")
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "signature size")
    try:
        value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
    except OwnershipChainError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "json") from exc
    if not isinstance(value, dict) or set(value) != _HEAD_KEYS or _canonical(value) != encoded:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "head schema")
    sequence = value.get("release_sequence")
    if (
        value.get("schema_version") != 1 or isinstance(sequence, bool)
        or not isinstance(sequence, int) or sequence <= 0
    ):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "release_sequence")
    cutover_id = _digest(value.get("cutover_id"), "cutover_id")
    build_id = _digest(value.get("closed_build_id"), "closed_build_id")
    previous = _digest(value.get("previous_head_id"), "previous_head_id", nullable=True)
    claimed = _digest(value.get("head_id"), "head_id")
    if claimed != _head_id(value):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "head_id")
    key_id = value.get("signing_key_id")
    if (
        not isinstance(registry, OwnershipCutoverRegistry)
        or not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None
    ):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "registry")
    entry = registry.keys.get(key_id)
    if entry is None or HEAD_PURPOSE not in entry.purposes:
        raise OwnershipChainError("birth_ownership_key_unauthorized", "ownership_head_v1")
    try:
        entry.public_key.verify(signature, HEAD_SIGNATURE_DOMAIN + encoded)
    except InvalidSignature as exc:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "signature") from exc
    assert isinstance(cutover_id, str) and isinstance(build_id, str)
    assert isinstance(claimed, str) and (previous is None or isinstance(previous, str))
    return OwnershipHead(
        sequence, cutover_id, build_id, previous, claimed, key_id,
        bytes(encoded), bytes(signature),
    )


def encode_required_head(head: OwnershipHead) -> bytes:
    """Frame one already authenticated head as the atomic required pointer."""
    if not isinstance(head, OwnershipHead):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "required head")
    if len(head.encoded) > MAX_HEAD_BYTES or len(head.signature) != 64:
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "required size")
    return (
        REQUIRED_HEAD_MAGIC + len(head.encoded).to_bytes(4, "big")
        + head.encoded + head.signature
    )


def decode_required_head(
    framed: bytes, *, registry: OwnershipCutoverRegistry,
) -> OwnershipHead:
    """Decode exactly one bounded pointer; trailing bytes are forbidden."""
    if not isinstance(framed, bytes) or len(framed) > MAX_REQUIRED_HEAD_BYTES:
        raise OwnershipChainError("birth_ownership_distribution_recovery_required", "required size")
    prefix = len(REQUIRED_HEAD_MAGIC)
    if len(framed) < prefix + 4 + 64 or framed[:prefix] != REQUIRED_HEAD_MAGIC:
        raise OwnershipChainError("birth_ownership_distribution_recovery_required", "required magic")
    payload_length = int.from_bytes(framed[prefix:prefix + 4], "big")
    if payload_length > MAX_HEAD_BYTES:
        raise OwnershipChainError("birth_ownership_distribution_recovery_required", "required length")
    expected_length = prefix + 4 + payload_length + 64
    if len(framed) != expected_length:
        raise OwnershipChainError("birth_ownership_distribution_recovery_required", "required framing")
    encoded = framed[prefix + 4:prefix + 4 + payload_length]
    signature = framed[-64:]
    return verify_ownership_head(encoded, signature, registry=registry)


@contextmanager
def _required_head_lock(root: Path):
    """Serialize the read/compare/replace CAS across cooperating processes."""
    path = root / ".required-head-v1.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "required lock",
            )
        if info.st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _windows_platform() -> bool:
    return os.name == "nt"


def _replace_required_pointer(
    source: Path, destination: Path, *, timeout: float,
) -> None:
    """Retry only a verified transient Windows sharing conflict, bounded."""
    if timeout < 0:
        raise ValueError("replace timeout must be non-negative")
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if not _windows_platform():
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required replace",
                ) from exc
            # WinError 32/33 are the OS' explicit sharing/lock violations.
            # Access denied (5) is deliberately not guessed to be transient:
            # without an independent handle probe it is a persistent denial.
            if getattr(exc, "winerror", None) not in {32, 33}:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required replace",
                ) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required replace timeout",
                ) from exc
            time.sleep(min(0.02, remaining))


def verify_contiguous_chain(
    *, anchor: OwnershipCutoverCertificate,
    heads: Iterable[OwnershipHead], required_head: OwnershipHead,
    cutovers: Mapping[str, OwnershipCutoverCertificate],
    builds: Mapping[str, VerifiedDistribution],
) -> VerifiedOwnershipChain:
    """Verify exactly the supplied chain; never select a maximum or fallback."""
    if not isinstance(anchor, OwnershipCutoverCertificate):
        raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "anchor")
    if not isinstance(required_head, OwnershipHead):
        raise OwnershipChainError("birth_ownership_downgrade", "required head")
    materialized = tuple(heads)
    if not materialized:
        raise OwnershipChainError("birth_ownership_downgrade", "empty chain")
    if len(materialized) > required_head.release_sequence:
        raise OwnershipChainError("birth_ownership_downgrade", "required head is older")
    if len(materialized) != required_head.release_sequence:
        raise OwnershipChainError("birth_ownership_distribution_recovery_required", "gap or extra head")
    seen_ids: set[str] = set()
    previous: OwnershipHead | None = None
    for expected_sequence, head in enumerate(materialized, 1):
        if not isinstance(head, OwnershipHead) or head.release_sequence != expected_sequence:
            raise OwnershipChainError("birth_ownership_distribution_recovery_required", "head gap")
        if head.head_id in seen_ids:
            raise OwnershipChainError("birth_ownership_distribution_recovery_required", "head fork")
        seen_ids.add(head.head_id)
        if previous is None:
            if head.previous_head_id is not None or head.cutover_id != anchor.cutover_id:
                raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "anchor link")
            if head.closed_build_id != anchor.closed_build_id:
                raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "anchor build")
        elif head.previous_head_id != previous.head_id:
            raise OwnershipChainError("birth_ownership_distribution_recovery_required", "predecessor")
        cutover = cutovers.get(head.cutover_id)
        build = builds.get(head.closed_build_id)
        if cutover is None or build is None:
            raise OwnershipChainError("birth_ownership_distribution_recovery_required", "missing object")
        if cutover.closed_build_id != head.closed_build_id:
            raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "object binding")
        if previous is not None and cutover.previous_cutover_id != previous.cutover_id:
            raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "cutover predecessor")
        if (
            not is_verified_distribution(build)
            or build.identity.closed_build_id != head.closed_build_id
            or build.release_sequence != head.release_sequence
        ):
            raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "build identity")
        if previous is None:
            if build.previous_closed_build_id is not None:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "initial build predecessor",
                )
        elif build.previous_closed_build_id != previous.closed_build_id:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "build predecessor",
            )
        previous = head
    final = materialized[-1]
    if (
        final.head_id != required_head.head_id
        or final.encoded != required_head.encoded
        or final.signature != required_head.signature
    ):
        raise OwnershipChainError("birth_ownership_downgrade", "required head mismatch")
    return VerifiedOwnershipChain(anchor.cutover_id, materialized)


def _verify_contiguous_authenticated_chain_v1(
    *, anchor: OwnershipCutoverCertificate,
    heads: Iterable[OwnershipHead], required_head: OwnershipHead,
    cutovers: Mapping[str, OwnershipCutoverCertificate],
    records: Mapping[
        str, AuthenticatedDistributionRecordV1
        | _AuthenticatedDistributionRecordForTestV1
    ],
    required_distribution: VerifiedDistribution, for_test: bool,
) -> VerifiedOwnershipChain:
    """Verify historical signed records, with live bytes only for the head."""
    if not isinstance(anchor, OwnershipCutoverCertificate):
        raise OwnershipChainError(
            "birth_ownership_distribution_chain_invalid", "anchor",
        )
    if not isinstance(required_head, OwnershipHead):
        raise OwnershipChainError("birth_ownership_downgrade", "required head")
    materialized = tuple(heads)
    if not materialized:
        raise OwnershipChainError("birth_ownership_downgrade", "empty chain")
    if len(materialized) > required_head.release_sequence:
        raise OwnershipChainError(
            "birth_ownership_downgrade", "required head is older",
        )
    if len(materialized) != required_head.release_sequence:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required", "gap or extra head",
        )

    ordered_records = []
    seen_ids: set[str] = set()
    previous_head: OwnershipHead | None = None
    previous_record: (
        AuthenticatedDistributionRecordV1
        | _AuthenticatedDistributionRecordForTestV1
        | None
    ) = None
    for expected_sequence, head in enumerate(materialized, 1):
        if not isinstance(head, OwnershipHead) or head.release_sequence != expected_sequence:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "head gap",
            )
        if head.head_id in seen_ids:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "head fork",
            )
        seen_ids.add(head.head_id)
        if previous_head is None:
            if head.previous_head_id is not None or head.cutover_id != anchor.cutover_id:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "anchor link",
                )
            if head.closed_build_id != anchor.closed_build_id:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "anchor build",
                )
        elif head.previous_head_id != previous_head.head_id:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "predecessor",
            )

        cutover = cutovers.get(head.cutover_id)
        record = records.get(head.closed_build_id)
        if cutover is None or record is None:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "missing object",
            )
        if not _is_authenticated_distribution_record_v1(
            record, for_test=for_test,
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "build authority",
            )
        if cutover.closed_build_id != head.closed_build_id:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "object binding",
            )
        if (
            previous_head is not None
            and cutover.previous_cutover_id != previous_head.cutover_id
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "cutover predecessor",
            )
        if (
            record.closed_build_id != head.closed_build_id
            or record.release_sequence != head.release_sequence
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "build identity",
            )
        if previous_record is None:
            if record.previous_closed_build_id is not None:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid",
                    "initial build predecessor",
                )
        elif record.previous_closed_build_id != previous_record.closed_build_id:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "build predecessor",
            )
        ordered_records.append(record)
        previous_head = head
        previous_record = record

    final = materialized[-1]
    if (
        final.head_id != required_head.head_id
        or final.encoded != required_head.encoded
        or final.signature != required_head.signature
    ):
        raise OwnershipChainError(
            "birth_ownership_downgrade", "required head mismatch",
        )
    final_record = ordered_records[-1]
    if (
        not is_verified_distribution(required_distribution)
        or required_distribution.identity.closed_build_id != final.closed_build_id
        or required_distribution.release_sequence != final.release_sequence
        or required_distribution.encoded != final_record.encoded
        or required_distribution.signature != final_record.signature
    ):
        raise OwnershipChainError(
            "birth_ownership_distribution_chain_invalid", "live build binding",
        )
    return VerifiedOwnershipChain(
        anchor.cutover_id, materialized, tuple(ordered_records),
        required_distribution,
    )


def _require_separate_ownership_registries(
    cutover_registry: OwnershipCutoverRegistry,
    head_registry: OwnershipCutoverRegistry,
) -> None:
    if (
        not isinstance(cutover_registry, OwnershipCutoverRegistry)
        or not isinstance(head_registry, OwnershipCutoverRegistry)
        or any(
            entry.purposes != frozenset({CUTOVER_PURPOSE})
            for entry in cutover_registry.keys.values()
        )
        or any(
            entry.purposes != frozenset({HEAD_PURPOSE})
            for entry in head_registry.keys.values()
        )
        or {
            entry.public_key.public_bytes_raw()
            for entry in cutover_registry.keys.values()
        } & {
            entry.public_key.public_bytes_raw()
            for entry in head_registry.keys.values()
        }
    ):
        raise OwnershipChainError(
            "birth_ownership_key_unauthorized", "shared ownership registry",
        )


def _registries_from_authorities(
    authorities: "OwnershipPublicRegistriesV1",
) -> tuple[object, OwnershipCutoverRegistry, OwnershipCutoverRegistry]:
    # Lazy import avoids the codec/chain import cycle.  Productive construction
    # accepts only the sealed bundle returned by the ownership cold loader.
    from executor_birth_ownership_authorities import (
        OwnershipPublicRegistriesV1, _PUBLIC_SEAL,
    )

    if (
        not isinstance(authorities, OwnershipPublicRegistriesV1)
        or authorities._seal is not _PUBLIC_SEAL
    ):
        raise OwnershipChainError(
            "birth_ownership_key_unauthorized", "untrusted authority bundle",
        )
    _require_separate_ownership_registries(
        authorities.cutover, authorities.head,
    )
    return authorities.distribution, authorities.cutover, authorities.head


def _require_product_chain_metadata_v1(root: Path) -> None:
    """Require the fixed chain and every ancestor to remain root-owned."""
    absolute = Path(os.path.abspath(root))
    for component in reversed((absolute, *absolute.parents)):
        try:
            info = component.lstat()
        except OSError as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "chain metadata",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "chain metadata",
            )
    for name in ("builds-v1", "cutovers-v1", "heads-v1"):
        try:
            info = (absolute / name).lstat()
        except OSError as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "chain metadata",
            ) from exc
        if (
            not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or info.st_uid != 0 or info.st_gid != 0
            or stat.S_IMODE(info.st_mode) != 0o755
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "chain metadata",
            )
    if stat.S_IMODE(absolute.lstat().st_mode) != 0o755:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required", "chain metadata",
        )


def _require_product_file_metadata_v1(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required", "object metadata",
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_uid != 0 or info.st_gid != 0
    ):
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required", "object metadata",
        )


def _require_linux_product_v1() -> None:
    if not sys.platform.startswith("linux"):
        raise OwnershipChainError("birth_ownership_platform_unsupported")


def _ensure_exact_directory_v1(path: Path) -> os.stat_result:
    """Create/open one directory without accepting umask-altered metadata."""
    path = Path(path)
    created = False
    try:
        os.mkdir(path, 0o755)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required",
            "directory create",
        ) from exc
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required",
            "directory open",
        ) from exc
    try:
        info = os.fstat(fd)
        path_info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(path_info.st_mode)
            or bool(getattr(path_info, "st_file_attributes", 0) & 0x400)
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
            or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "directory metadata",
            )
        repaired = stat.S_IMODE(info.st_mode) != 0o755
        if repaired:
            os.fchmod(fd, 0o755)
            info = os.fstat(fd)
        os.fsync(fd)
        path_info = path.lstat()
        if (
            stat.S_IMODE(info.st_mode) != 0o755
            or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
            or info.st_uid != path_info.st_uid or info.st_gid != path_info.st_gid
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "directory metadata",
            )
    except OwnershipChainError:
        raise
    except OSError as exc:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required",
            "directory metadata",
        ) from exc
    finally:
        os.close(fd)
    try:
        _sync_directory(path.parent)
    except OSError as exc:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required",
            "directory sync",
        ) from exc
    return info


def _ensure_product_directory_v1(path: Path) -> None:
    info = _ensure_exact_directory_v1(path)
    if info.st_uid != 0 or info.st_gid != 0:
        raise OwnershipChainError(
            "birth_ownership_distribution_recovery_required",
            "directory ownership",
        )


class OwnershipChainStore:
    """No-replace object store plus an atomic signed required-head pointer."""

    def __init__(self) -> None:
        _require_linux_product_v1()
        from executor_birth_ownership_authorities import (
            _load_fixed_ownership_public_snapshot_v1,
        )

        snapshot = _load_fixed_ownership_public_snapshot_v1()
        authorities = snapshot.public
        self.root = DEFAULT_OWNERSHIP_CHAIN_ROOT_V1
        self._fixed_authority_snapshot = snapshot
        self._authorities = authorities
        (
            self.distribution_registry, self.cutover_registry,
            self.head_registry,
        ) = _registries_from_authorities(authorities)
        try:
            _safe_directory(self.root)
            for name in ("builds-v1", "cutovers-v1", "heads-v1"):
                _safe_directory(self.root / name)
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "store missing or unsafe",
            ) from exc
        _require_product_chain_metadata_v1(self.root)

    @classmethod
    def initialize(cls) -> "OwnershipChainStore":
        """Explicit installer/coordinator initialization; ordinary open never writes."""
        _require_linux_product_v1()
        from executor_birth_ownership_authorities import (
            _load_fixed_ownership_public_snapshot_v1,
        )

        if cls is not OwnershipChainStore:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "productive store",
            )
        # Fail on the fixed authority before the first filesystem mutation.
        _load_fixed_ownership_public_snapshot_v1()
        root = DEFAULT_OWNERSHIP_CHAIN_ROOT_V1
        _ensure_product_directory_v1(root)
        for name in ("builds-v1", "cutovers-v1", "heads-v1"):
            _ensure_product_directory_v1(root / name)
        return cls()

    def _append_pair(
        self, directory: str, stem: str, encoded: bytes, signature: bytes,
        *, _crash_seam=None,
    ) -> None:
        target = self.root / directory
        payload_path = target / f"{stem}.json"
        signature_path = target / f"{stem}.sig"
        if payload_path.exists() and not signature_path.exists():
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "incomplete pair",
            )
        if signature_path.exists() and not payload_path.exists():
            try:
                if _safe_read(signature_path, len(signature)) != signature:
                    raise OwnershipChainError(
                        "birth_ownership_distribution_recovery_required", "orphan signature",
                    )
            except OwnershipChainError:
                raise
            except Exception as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "orphan signature",
                ) from exc
        suffix = hashlib.sha256(encoded + signature).hexdigest()
        payload_tmp = target / f".{stem}.{suffix}.json.tmp"
        signature_tmp = target / f".{stem}.{suffix}.sig.tmp"
        try:
            if not signature_tmp.exists():
                _write_temporary(signature_tmp, signature)
            elif _safe_read(signature_tmp, len(signature)) != signature:
                raise OwnershipChainError("birth_ownership_distribution_recovery_required", "temporary")
            _publish_no_replace(signature_tmp, signature_path, signature)
            if _crash_seam is not None:
                _crash_seam("after_signature")
            if not payload_tmp.exists():
                _write_temporary(payload_tmp, encoded)
            elif _safe_read(payload_tmp, len(encoded)) != encoded:
                raise OwnershipChainError("birth_ownership_distribution_recovery_required", "temporary")
            _publish_no_replace(payload_tmp, payload_path, encoded)
        except Exception as exc:
            if isinstance(exc, _OwnershipChainCrashForTest):
                raise
            if isinstance(exc, OwnershipChainError):
                raise
            if isinstance(exc, OwnershipCutoverError):
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "append conflict",
                ) from exc
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "append",
            ) from exc
        finally:
            for item in (signature_tmp, payload_tmp):
                try:
                    item.unlink()
                except FileNotFoundError:
                    pass

    def append_authenticated_build(
        self, distribution: VerifiedDistribution, *, _crash_seam=None,
    ) -> None:
        if not is_verified_distribution(distribution):
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build authority")
        identity = distribution.identity
        encoded = distribution.encoded
        signature = distribution.signature
        if not isinstance(encoded, bytes) or len(encoded) > MAX_BUILD_BYTES:
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build size")
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build signature")
        self._verify_build_bytes(distribution, encoded)
        self._append_pair(
            "builds-v1", identity.closed_build_id.removeprefix("sha256:"),
            encoded, signature, _crash_seam=_crash_seam,
        )

    def append_cutover(
        self, encoded: bytes, signature: bytes, *, _crash_seam=None,
    ) -> OwnershipCutoverCertificate:
        try:
            certificate = verify_ownership_cutover_certificate(
                encoded, signature, registry=self.cutover_registry,
            )
        except Exception as exc:
            raise OwnershipChainError("birth_ownership_distribution_chain_invalid", "cutover") from exc
        self._append_pair(
            "cutovers-v1", certificate.cutover_id.removeprefix("sha256:"),
            encoded, signature, _crash_seam=_crash_seam,
        )
        return certificate

    def append_head(
        self, encoded: bytes, signature: bytes, *, _crash_seam=None,
    ) -> OwnershipHead:
        head = verify_ownership_head(
            encoded, signature, registry=self.head_registry,
        )
        stem = f"{head.release_sequence:020d}-{head.cutover_id.removeprefix('sha256:')}"
        self._append_pair(
            "heads-v1", stem, encoded, signature, _crash_seam=_crash_seam,
        )
        return head

    def update_required_head(
        self, encoded: bytes, signature: bytes, *,
        expected_head_id: str | None,
        replace_timeout: float = 1.0,
        _crash_seam=None,
    ) -> OwnershipHead:
        with _required_head_lock(self.root):
            return self._update_required_head_locked(
                encoded, signature, expected_head_id=expected_head_id,
                replace_timeout=replace_timeout, _crash_seam=_crash_seam,
            )

    def _update_required_head_locked(
        self, encoded: bytes, signature: bytes, *,
        expected_head_id: str | None,
        replace_timeout: float,
        _crash_seam=None,
    ) -> OwnershipHead:
        """CAS the single-file pointer; exact retry succeeds without fallback."""
        head = verify_ownership_head(
            encoded, signature, registry=self.head_registry,
        )
        stem = f"{head.release_sequence:020d}-{head.cutover_id.removeprefix('sha256:')}"
        stored_bytes, stored_signature = self._read_pair(
            self.root / "heads-v1", stem, maximum=MAX_HEAD_BYTES,
        )
        if stored_bytes != encoded or stored_signature != signature:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "head binding",
            )
        destination = self.root / REQUIRED_HEAD_BASENAME
        current_frame: bytes | None
        if destination.exists():
            try:
                current_frame = _safe_read(destination, MAX_REQUIRED_HEAD_BYTES)
                current = decode_required_head(
                    current_frame, registry=self.head_registry,
                )
            except Exception as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required pointer",
                ) from exc
            if current.head_id == head.head_id:
                if current.encoded == encoded and current.signature == signature:
                    return head
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required conflict",
                )
            if expected_head_id != current.head_id:
                raise OwnershipChainError("birth_ownership_downgrade", "required CAS")
            if (
                head.release_sequence != current.release_sequence + 1
                or head.previous_head_id != current.head_id
            ):
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "required successor",
                )
        else:
            current_frame = None
            if expected_head_id is not None:
                raise OwnershipChainError("birth_ownership_downgrade", "required missing")
            if head.release_sequence != 1 or head.previous_head_id is not None:
                raise OwnershipChainError("birth_ownership_downgrade", "initial required head")
        frame = encode_required_head(head)
        temporary = self.root / f".{REQUIRED_HEAD_BASENAME}.{head.head_id.removeprefix('sha256:')}.tmp"
        try:
            if not temporary.exists():
                _write_temporary(temporary, frame)
            elif _safe_read(temporary, len(frame)) != frame:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required temporary",
                )
            # Recheck the CAS bytes immediately before the atomic point.
            if current_frame is None:
                if destination.exists():
                    raise OwnershipChainError("birth_ownership_downgrade", "required race")
            else:
                try:
                    if _safe_read(destination, MAX_REQUIRED_HEAD_BYTES) != current_frame:
                        raise OwnershipChainError("birth_ownership_downgrade", "required race")
                except OwnershipChainError:
                    raise
                except Exception as exc:
                    raise OwnershipChainError("birth_ownership_downgrade", "required race") from exc
            if _crash_seam is not None:
                _crash_seam("before_replace")
            try:
                if current_frame is None:
                    _publish_no_replace(temporary, destination, frame)
                else:
                    # ``os.replace`` is the one-file atomic point of no return.
                    _replace_required_pointer(
                        temporary, destination, timeout=replace_timeout,
                    )
                    _sync_directory(self.root)
            except OwnershipCutoverError as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "required replace",
                ) from exc
            if _crash_seam is not None:
                _crash_seam("after_replace")
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        reread = self.read_required_head()
        if reread.encoded != encoded or reread.signature != signature:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "required reread",
            )
        return head

    def read_required_head(self) -> OwnershipHead:
        path = self.root / REQUIRED_HEAD_BASENAME
        if not path.exists():
            raise OwnershipChainError("birth_ownership_downgrade", "required head missing")
        try:
            framed = _safe_read(path, MAX_REQUIRED_HEAD_BYTES)
            return decode_required_head(framed, registry=self.head_registry)
        except OwnershipChainError:
            raise
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "required pointer",
            ) from exc

    def _read_pair(
        self, directory: Path, stem: str, *, maximum: int,
    ) -> tuple[bytes, bytes]:
        payload = directory / f"{stem}.json"
        signature = directory / f"{stem}.sig"
        if not payload.exists() and not signature.exists():
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "missing object",
            )
        if payload.exists() != signature.exists():
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "incomplete pair",
            )
        try:
            return _safe_read(payload, maximum), _safe_read(signature, 64)
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "unsafe object",
            ) from exc

    @staticmethod
    def _verify_build_bytes(distribution: VerifiedDistribution, encoded: bytes) -> None:
        if not is_verified_distribution(distribution):
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build authority")
        identity = distribution.identity
        try:
            value = json.loads(encoded.decode("ascii"), object_pairs_hook=_pairs)
        except Exception as exc:
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build json") from exc
        if (
            not isinstance(value, dict) or _canonical(value) != encoded
            or value.get("schema_version") != 1
            or value.get("closed_build_id") != identity.closed_build_id
        ):
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build binding")
        unsigned = {key: item for key, item in value.items() if key != "closed_build_id"}
        computed = "sha256:" + hashlib.sha256(
            _BUILD_ID_DOMAIN + _canonical(unsigned),
        ).hexdigest()
        if computed != identity.closed_build_id:
            raise OwnershipChainError("birth_ownership_distribution_invalid", "build id")

    def read_required_chain(
        self, *, anchor: OwnershipCutoverCertificate,
        builds: Mapping[str, VerifiedDistribution],
    ) -> VerifiedOwnershipChain:
        """Follow only required-head through the unique contiguous prefix.

        Higher unreferenced records are authenticated enough to detect forks
        but are never selected.  The required pointer, not directory order or
        a maximum filename, determines the accepted prefix.
        """
        required = self.read_required_head()
        heads_by_sequence: dict[int, OwnershipHead] = {}
        head_directory = self.root / "heads-v1"
        names = tuple(item.name for item in head_directory.iterdir())
        if any(name.startswith(".") or not name.endswith((".json", ".sig")) for name in names):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "unexpected head object",
            )
        stems = {name.rsplit(".", 1)[0] for name in names}
        for stem in sorted(stems):
            encoded, signature = self._read_pair(
                head_directory, stem, maximum=MAX_HEAD_BYTES,
            )
            head = verify_ownership_head(
                encoded, signature, registry=self.head_registry,
            )
            expected_stem = (
                f"{head.release_sequence:020d}-"
                f"{head.cutover_id.removeprefix('sha256:')}"
            )
            if stem != expected_stem or head.release_sequence in heads_by_sequence:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "head fork",
                )
            heads_by_sequence[head.release_sequence] = head
        selected: list[OwnershipHead] = []
        cutovers: dict[str, OwnershipCutoverCertificate] = {}
        selected_builds: dict[str, VerifiedDistribution] = {}
        for sequence in range(1, required.release_sequence + 1):
            head = heads_by_sequence.get(sequence)
            if head is None:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "head gap",
                )
            selected.append(head)
            cutover_bytes, cutover_signature = self._read_pair(
                self.root / "cutovers-v1", head.cutover_id.removeprefix("sha256:"),
                maximum=MAX_PAYLOAD_BYTES,
            )
            try:
                cutovers[head.cutover_id] = verify_ownership_cutover_certificate(
                    cutover_bytes, cutover_signature,
                    registry=self.cutover_registry,
                )
            except Exception as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "cutover object",
                ) from exc
            distribution = builds.get(head.closed_build_id)
            if not is_verified_distribution(distribution):
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "build authority",
                )
            build_bytes, build_signature = self._read_pair(
                self.root / "builds-v1", head.closed_build_id.removeprefix("sha256:"),
                maximum=MAX_BUILD_BYTES,
            )
            if (
                build_bytes != distribution.encoded
                or build_signature != distribution.signature
            ):
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "build pair",
                )
            self._verify_build_bytes(distribution, build_bytes)
            selected_builds[head.closed_build_id] = distribution
        return verify_contiguous_chain(
            anchor=anchor, heads=selected, required_head=required,
            cutovers=cutovers, builds=selected_builds,
        )

    @staticmethod
    def _paired_stems(
        directory: Path, *, pattern: re.Pattern[str], label: str,
    ) -> tuple[str, ...]:
        try:
            names = tuple(sorted(item.name for item in directory.iterdir()))
        except OSError as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", label,
            ) from exc
        if any(
            name.startswith(".")
            or re.fullmatch(r".+\.(?:json|sig)", name) is None
            for name in names
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                f"unexpected {label} object",
            )
        stems = tuple(sorted({name.rsplit(".", 1)[0] for name in names}))
        if any(pattern.fullmatch(stem) is None for stem in stems):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                f"invalid {label} name",
            )
        if len(names) != len(stems) * 2 or any(
            f"{stem}.json" not in names or f"{stem}.sig" not in names
            for stem in stems
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                f"incomplete {label} pair",
            )
        return stems

    def _read_required_chain_cold_core_v1(
        self, *, authenticate_record, verify_live_record, for_test: bool,
    ) -> VerifiedOwnershipChain:
        if not for_test:
            _require_product_file_metadata_v1(
                self.root / REQUIRED_HEAD_BASENAME,
            )
        required = self.read_required_head()

        records: dict[
            str, AuthenticatedDistributionRecordV1
            | _AuthenticatedDistributionRecordForTestV1
        ] = {}
        digest_name = re.compile(r"[0-9a-f]{64}\Z")
        for stem in self._paired_stems(
            self.root / "builds-v1", pattern=digest_name, label="build",
        ):
            if not for_test:
                _require_product_file_metadata_v1(
                    self.root / "builds-v1" / f"{stem}.json",
                )
                _require_product_file_metadata_v1(
                    self.root / "builds-v1" / f"{stem}.sig",
                )
            encoded, signature = self._read_pair(
                self.root / "builds-v1", stem, maximum=MAX_BUILD_BYTES,
            )
            try:
                record = authenticate_record(encoded, signature)
            except Exception as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "build object",
                ) from exc
            if record.closed_build_id.removeprefix("sha256:") != stem:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "build name",
                )
            if record.closed_build_id in records:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "build duplicate",
                )
            records[record.closed_build_id] = record
        record_sequences = [record.release_sequence for record in records.values()]
        if len(record_sequences) != len(set(record_sequences)):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "build fork",
            )

        cutovers: dict[str, OwnershipCutoverCertificate] = {}
        cutover_raw: dict[str, tuple[bytes, bytes]] = {}
        for stem in self._paired_stems(
            self.root / "cutovers-v1", pattern=digest_name, label="cutover",
        ):
            if not for_test:
                _require_product_file_metadata_v1(
                    self.root / "cutovers-v1" / f"{stem}.json",
                )
                _require_product_file_metadata_v1(
                    self.root / "cutovers-v1" / f"{stem}.sig",
                )
            encoded, signature = self._read_pair(
                self.root / "cutovers-v1", stem, maximum=MAX_PAYLOAD_BYTES,
            )
            try:
                cutover = verify_ownership_cutover_certificate(
                    encoded, signature, registry=self.cutover_registry,
                )
            except Exception as exc:
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "cutover object",
                ) from exc
            if cutover.cutover_id.removeprefix("sha256:") != stem:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "cutover name",
                )
            cutovers[cutover.cutover_id] = cutover
            cutover_raw[cutover.cutover_id] = (encoded, signature)

        heads_by_sequence: dict[int, OwnershipHead] = {}
        head_name = re.compile(r"[0-9]{20}-[0-9a-f]{64}\Z")
        for stem in self._paired_stems(
            self.root / "heads-v1", pattern=head_name, label="head",
        ):
            if not for_test:
                _require_product_file_metadata_v1(
                    self.root / "heads-v1" / f"{stem}.json",
                )
                _require_product_file_metadata_v1(
                    self.root / "heads-v1" / f"{stem}.sig",
                )
            encoded, signature = self._read_pair(
                self.root / "heads-v1", stem, maximum=MAX_HEAD_BYTES,
            )
            head = verify_ownership_head(
                encoded, signature, registry=self.head_registry,
            )
            expected_stem = (
                f"{head.release_sequence:020d}-"
                f"{head.cutover_id.removeprefix('sha256:')}"
            )
            if stem != expected_stem or head.release_sequence in heads_by_sequence:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "head fork",
                )
            heads_by_sequence[head.release_sequence] = head

        if not heads_by_sequence:
            raise OwnershipChainError("birth_ownership_downgrade", "empty chain")
        maximum_sequence = max(heads_by_sequence)
        if set(heads_by_sequence) != set(range(1, maximum_sequence + 1)):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "head gap",
            )
        all_heads = tuple(
            heads_by_sequence[sequence]
            for sequence in range(1, maximum_sequence + 1)
        )
        if required.release_sequence > maximum_sequence:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "required head gap",
            )
        archived_required = heads_by_sequence.get(required.release_sequence)
        if (
            archived_required is None
            or archived_required.head_id != required.head_id
            or archived_required.encoded != required.encoded
            or archived_required.signature != required.signature
        ):
            raise OwnershipChainError(
                "birth_ownership_downgrade", "required head mismatch",
            )

        ownership_root = self.root.parent
        try:
            if not for_test:
                _require_product_file_metadata_v1(
                    ownership_root / PAYLOAD_BASENAME,
                )
                _require_product_file_metadata_v1(
                    ownership_root / SIGNATURE_BASENAME,
                )
            anchor_encoded = _safe_read(
                ownership_root / PAYLOAD_BASENAME, MAX_PAYLOAD_BYTES,
            )
            anchor_signature = _safe_read(
                ownership_root / SIGNATURE_BASENAME, 64,
            )
            anchor = verify_ownership_cutover_certificate(
                anchor_encoded, anchor_signature, registry=self.cutover_registry,
            )
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "anchor object",
            ) from exc
        archived_anchor = cutover_raw.get(anchor.cutover_id)
        if archived_anchor != (anchor_encoded, anchor_signature):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "anchor copy",
            )

        previous_head: OwnershipHead | None = None
        previous_record = None
        for head in all_heads:
            cutover = cutovers.get(head.cutover_id)
            record = records.get(head.closed_build_id)
            if cutover is None or record is None:
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "missing object",
                )
            if previous_head is None:
                if (
                    head.previous_head_id is not None
                    or head.cutover_id != anchor.cutover_id
                    or head.closed_build_id != anchor.closed_build_id
                    or record.previous_closed_build_id is not None
                ):
                    raise OwnershipChainError(
                        "birth_ownership_distribution_chain_invalid", "anchor link",
                    )
            elif (
                head.previous_head_id != previous_head.head_id
                or cutover.previous_cutover_id != previous_head.cutover_id
                or record.previous_closed_build_id
                != previous_record.closed_build_id
            ):
                raise OwnershipChainError(
                    "birth_ownership_distribution_recovery_required", "predecessor",
                )
            if (
                cutover.closed_build_id != head.closed_build_id
                or record.closed_build_id != head.closed_build_id
                or record.release_sequence != head.release_sequence
            ):
                raise OwnershipChainError(
                    "birth_ownership_distribution_chain_invalid", "object binding",
                )
            previous_head = head
            previous_record = record

        selected = all_heads[:required.release_sequence]
        selected_records = {
            head.closed_build_id: records[head.closed_build_id]
            for head in selected
        }
        live_record = selected_records[required.closed_build_id]
        try:
            required_distribution = verify_live_record(live_record)
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_chain_invalid", "live build",
            ) from exc
        return _verify_contiguous_authenticated_chain_v1(
            anchor=anchor, heads=selected, required_head=required,
            cutovers=cutovers, records=selected_records,
            required_distribution=required_distribution, for_test=for_test,
        )

    def read_required_chain_cold_v1(self) -> VerifiedOwnershipChain:
        """Reconstruct product trust from fixed storage and fixed authorities."""
        _require_linux_product_v1()
        if type(self) is not OwnershipChainStore or self.root != DEFAULT_OWNERSHIP_CHAIN_ROOT_V1:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required", "productive store",
            )
        if (
            self.distribution_registry
            is not self._fixed_authority_snapshot.public.distribution
            or self.cutover_registry
            is not self._fixed_authority_snapshot.public.cutover
            or self.head_registry is not self._fixed_authority_snapshot.public.head
        ):
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "authority snapshot",
            )
        _require_product_chain_metadata_v1(self.root)
        return self._read_required_chain_cold_core_v1(
            authenticate_record=lambda encoded, signature:
                _authenticate_distribution_record_from_fixed_snapshot_v1(
                    encoded, signature, self._fixed_authority_snapshot,
                ),
            verify_live_record=verify_installed_distribution_record_v1,
            for_test=False,
        )


class _OwnershipChainStoreForTest(OwnershipChainStore):
    """Portable root/authority seam, nominally outside productive construction."""

    def __init__(self, root: Path, authorities: "OwnershipPublicRegistriesV1") -> None:
        self.root = Path(root)
        self._authorities = authorities
        (
            self.distribution_registry, self.cutover_registry,
            self.head_registry,
        ) = _registries_from_authorities(authorities)
        try:
            _safe_directory(self.root)
            for name in ("builds-v1", "cutovers-v1", "heads-v1"):
                _safe_directory(self.root / name)
        except Exception as exc:
            raise OwnershipChainError(
                "birth_ownership_distribution_recovery_required",
                "test store missing or unsafe",
            ) from exc

    @classmethod
    def _initialize_with_authorities(
        cls, root: Path, authorities: "OwnershipPublicRegistriesV1",
    ) -> "_OwnershipChainStoreForTest":
        _registries_from_authorities(authorities)
        root = Path(root)
        root.mkdir(mode=0o755, exist_ok=True)
        _safe_directory(root)
        for name in ("builds-v1", "cutovers-v1", "heads-v1"):
            directory = root / name
            directory.mkdir(mode=0o755, exist_ok=True)
            _safe_directory(directory)
        _sync_directory(root)
        return cls(root, authorities)

    def _read_required_chain_cold_for_test(self) -> VerifiedOwnershipChain:
        def authenticate(encoded: bytes, signature: bytes):
            return _authenticate_distribution_record_for_test(
                encoded, signature, registry=self.distribution_registry,
            )

        def verify_live(record):
            root = (
                self.root.parent / "releases-v1"
                / f"{record.release_sequence:020d}"
            )
            from executor_birth_distribution_manifest import _environment_for_test

            return _verify_authenticated_distribution_record_for_test(
                record, environment=_environment_for_test(
                    "windows" if os.name == "nt" else "linux",
                    "x86_64", root,
                ),
            )

        return self._read_required_chain_cold_core_v1(
            authenticate_record=authenticate, verify_live_record=verify_live,
            for_test=True,
        )


__all__ = [
    "HEAD_PURPOSE", "REQUIRED_HEAD_BASENAME", "REQUIRED_HEAD_MAGIC",
    "OwnershipChainError", "OwnershipChainStore",
    "OwnershipHead", "VerifiedOwnershipChain", "issue_ownership_head",
    "decode_required_head", "encode_required_head", "verify_contiguous_chain",
    "verify_ownership_head",
]
