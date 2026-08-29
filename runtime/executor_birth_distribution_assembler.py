"""Portable codecs for RM-0008 closed-distribution assembly.

Group 6-B2 intentionally adds only the received-source V1 format.  This
module is a standard-library leaf: importing or using the codec performs no
filesystem, platform, authority, or deployment operation.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Mapping


RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1 = "received-source-v1.json"
RECEIVED_SOURCE_FILE_HASH_DOMAIN_V1 = (
    b"metnos.executor-birth.received-source-file/v1\0"
)
RECEIVED_SOURCE_ID_DOMAIN_V1 = b"metnos.executor-birth.received-source/v1\0"
MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1 = 16 * 1024 * 1024
MAX_RECEIVED_SOURCE_FILES_V1 = 20_000
MAX_RECEIVED_SOURCE_DIRECTORIES_V1 = 20_000
MAX_RECEIVED_SOURCE_PATH_DEPTH_V1 = 32
MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1 = 2 * 1024 * 1024 * 1024

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_DOCUMENT_KEYS = frozenset({
    "schema_version", "source_id", "service_user", "files",
})
_FILE_KEYS = frozenset({"path", "size", "content_hash", "mode"})
_FILE_MODES = frozenset({0o644, 0o755})


class DistributionAssemblerError(RuntimeError):
    """Stable failure raised by a portable distribution-material codec."""

    def __init__(
        self, code: str = "birth_ownership_distribution_invalid",
        detail: str = "",
    ) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ReceivedSourceFileV1:
    path: str
    size: int
    content_hash: str
    mode: int


@dataclass(frozen=True, slots=True)
class ReceivedSourceV1:
    source_id: str
    service_user: str
    files: tuple[ReceivedSourceFileV1, ...]


def _invalid(detail: str) -> DistributionAssemblerError:
    return DistributionAssemblerError(
        "birth_ownership_distribution_invalid", detail,
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid("json") from exc


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise _invalid("duplicate key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise _invalid("json constant")


def _relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or unicodedata.normalize("NFC", value) != value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
    ):
        raise _invalid("file path")
    parts = value.split("/")
    if (
        any(part in {"", ".", ".."} for part in parts)
        or len(parts) > MAX_RECEIVED_SOURCE_PATH_DEPTH_V1
        or PurePosixPath(value).as_posix() != value
        or parts[0] == RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1
    ):
        raise _invalid("file path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid("file path") from exc
    return value


def _service_user(value: object) -> str:
    if type(value) is not str or _ACCOUNT_RE.fullmatch(value) is None:
        raise _invalid("service user")
    return value


def _digest(value: object, detail: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid(detail)
    return value


def _u64be(value: int) -> bytes:
    return value.to_bytes(8, "big", signed=False)


def received_source_file_hash_v1(
    path: str, size: int, chunks: Iterable[bytes],
) -> str:
    """Hash one file without materializing its contents.

    ``size`` is authoritative framing, but the iterable must yield exactly
    that many bytes.  A short or overlong stream is invalid.
    """

    canonical_path = _relative_path(path)
    if (
        type(size) is not int
        or size < 0
        or size > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1
    ):
        raise _invalid("file size")
    path_bytes = canonical_path.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(RECEIVED_SOURCE_FILE_HASH_DOMAIN_V1)
    digest.update(_u64be(len(path_bytes)))
    digest.update(path_bytes)
    digest.update(_u64be(size))
    observed = 0
    try:
        iterator = iter(chunks)
    except TypeError as exc:
        raise _invalid("file chunks") from exc
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception as exc:
            raise _invalid("file chunks") from exc
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise _invalid("file chunk")
        try:
            view = memoryview(chunk)
            chunk_size = view.nbytes
        except (TypeError, ValueError) as exc:
            raise _invalid("file chunk") from exc
        if chunk_size == 0:
            raise _invalid("file chunk")
        observed += chunk_size
        if observed > size:
            raise _invalid("file size")
        try:
            digest.update(view)
        except (BufferError, TypeError, ValueError) as exc:
            raise _invalid("file chunk") from exc
    if observed != size:
        raise _invalid("file size")
    return "sha256:" + digest.hexdigest()


def _file_document(item: ReceivedSourceFileV1) -> dict[str, object]:
    return {
        "path": item.path,
        "size": item.size,
        "content_hash": item.content_hash,
        "mode": item.mode,
    }


def _validated_files(
    values: Iterable[ReceivedSourceFileV1], *, sort: bool,
) -> tuple[ReceivedSourceFileV1, ...]:
    items: list[ReceivedSourceFileV1] = []
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise _invalid("files") from exc
    for value in iterator:
        if type(value) is not ReceivedSourceFileV1:
            raise _invalid("file entry")
        path = _relative_path(value.path)
        if (
            type(value.size) is not int
            or value.size < 0
            or value.size > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1
        ):
            raise _invalid("file size")
        content_hash = _digest(value.content_hash, "file hash")
        if type(value.mode) is not int or value.mode not in _FILE_MODES:
            raise _invalid("file mode")
        items.append(ReceivedSourceFileV1(
            path, value.size, content_hash, value.mode,
        ))
        if len(items) > MAX_RECEIVED_SOURCE_FILES_V1:
            raise _invalid("file count")
    if not items:
        raise _invalid("file count")
    if sort:
        items.sort(key=lambda item: item.path.encode("utf-8"))
    total = 0
    previous_key: bytes | None = None
    seen: set[str] = set()
    directories: set[str] = set()
    for item in items:
        key = item.path.encode("utf-8")
        if previous_key is not None and key <= previous_key:
            raise _invalid("file order")
        previous_key = key
        parts = item.path.split("/")
        parents = tuple(
            "/".join(parts[:index]) for index in range(1, len(parts))
        )
        if any(parent in seen for parent in parents):
            raise _invalid("file path collision")
        if item.path in seen or item.path in directories:
            raise _invalid("file path collision")
        seen.add(item.path)
        directories.update(parents)
        if len(directories) > MAX_RECEIVED_SOURCE_DIRECTORIES_V1:
            raise _invalid("directory count")
        total += item.size
        if total > MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1:
            raise _invalid("total size")
    return tuple(items)


def _source_id(value_without_id: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(
        RECEIVED_SOURCE_ID_DOMAIN_V1 + _canonical(value_without_id)
    ).hexdigest()


def build_received_source_v1(
    service_user: str, files: tuple[ReceivedSourceFileV1, ...],
) -> ReceivedSourceV1:
    """Build the sole canonical in-memory record and mint its source ID."""

    if type(files) is not tuple:
        raise _invalid("files")
    user = _service_user(service_user)
    canonical_files = _validated_files(files, sort=True)
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": user,
        "files": [_file_document(item) for item in canonical_files],
    }
    source_id = _source_id(unsigned)
    if len(_canonical({**unsigned, "source_id": source_id})) > (
        MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1
    ):
        raise _invalid("descriptor size")
    return ReceivedSourceV1(source_id, user, canonical_files)


def encode_received_source_v1(record: ReceivedSourceV1) -> bytes:
    """Validate and encode one canonical received-source record."""

    if type(record) is not ReceivedSourceV1:
        raise _invalid("received source")
    rebuilt = build_received_source_v1(record.service_user, record.files)
    if rebuilt != record:
        raise _invalid("source id")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": rebuilt.service_user,
        "files": [_file_document(item) for item in rebuilt.files],
    }
    document: dict[str, object] = {
        **unsigned,
        "source_id": rebuilt.source_id,
    }
    encoded = _canonical(document)
    if len(encoded) > MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1:
        raise _invalid("descriptor size")
    # Keep a single validation path for emitted and received documents.
    decode_received_source_v1(encoded)
    return encoded


def decode_received_source_v1(encoded: bytes) -> ReceivedSourceV1:
    """Strictly decode and authenticate the content-addressed descriptor."""

    if (
        type(encoded) is not bytes
        or not encoded
        or len(encoded) > MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1
    ):
        raise _invalid("descriptor size")
    try:
        value = json.loads(
            encoded.decode("ascii"), object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except DistributionAssemblerError:
        raise
    except (
        UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError,
        RecursionError,
    ) as exc:
        raise _invalid("json") from exc
    if type(value) is not dict or frozenset(value) != _DOCUMENT_KEYS:
        raise _invalid("document keys")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _invalid("schema version")
    source_id = _digest(value["source_id"], "source id")
    user = _service_user(value["service_user"])
    raw_files = value["files"]
    if type(raw_files) is not list:
        raise _invalid("files")
    parsed: list[ReceivedSourceFileV1] = []
    for raw in raw_files:
        if type(raw) is not dict or frozenset(raw) != _FILE_KEYS:
            raise _invalid("file keys")
        parsed.append(ReceivedSourceFileV1(
            raw["path"], raw["size"], raw["content_hash"], raw["mode"],
        ))
    files = _validated_files(parsed, sort=False)
    record = build_received_source_v1(user, files)
    if source_id != record.source_id:
        raise _invalid("source id")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "service_user": record.service_user,
        "files": [_file_document(item) for item in record.files],
    }
    canonical = _canonical({**unsigned, "source_id": record.source_id})
    if encoded != canonical:
        raise _invalid("canonical json")
    return record


__all__ = [
    "DistributionAssemblerError",
    "MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1",
    "MAX_RECEIVED_SOURCE_DIRECTORIES_V1",
    "MAX_RECEIVED_SOURCE_FILES_V1",
    "MAX_RECEIVED_SOURCE_PATH_DEPTH_V1",
    "MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1",
    "RECEIVED_SOURCE_DESCRIPTOR_BASENAME_V1",
    "ReceivedSourceFileV1",
    "ReceivedSourceV1",
    "build_received_source_v1",
    "decode_received_source_v1",
    "encode_received_source_v1",
    "received_source_file_hash_v1",
]
