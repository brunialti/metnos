"""Portable, independent oracles for the RM-0008 received-source codec."""
from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

import executor_birth_distribution_assembler as assembler


_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _independent_file_hash(path: str, content: bytes) -> str:
    encoded_path = path.encode("utf-8")
    material = (
        b"metnos.executor-birth.received-source-file/v1\0"
        + len(encoded_path).to_bytes(8, "big") + encoded_path
        + len(content).to_bytes(8, "big") + content
    )
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _with_source_id(unsigned: dict[str, object]) -> dict[str, object]:
    return {
        **unsigned,
        "source_id": "sha256:" + hashlib.sha256(
            b"metnos.executor-birth.received-source/v1\0"
            + _canonical(unsigned)
        ).hexdigest(),
    }


def _entry(
    path: str, *, size: int = 0, content_hash: str = _HASH_A,
    mode: int = 0o644,
) -> dict[str, object]:
    return {
        "path": path,
        "size": size,
        "content_hash": content_hash,
        "mode": mode,
    }


def _encoded_document(
    files: list[dict[str, object]], *, service_user: object = "metnos",
    schema_version: object = 1, extra: dict[str, object] | None = None,
) -> bytes:
    unsigned: dict[str, object] = {
        "schema_version": schema_version,
        "service_user": service_user,
        "files": files,
    }
    document = _with_source_id(unsigned)
    if extra:
        document.update(extra)
    return _canonical(document)


def test_streaming_file_hash_matches_independent_golden() -> None:
    path = "pkg/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py"
    chunks_seen: list[object] = []

    def chunks():
        for chunk in (b"print(", bytearray(b"'ok'"), memoryview(b")\n")):
            chunks_seen.append(chunk)
            yield chunk

    content = b"print('ok')\n"
    observed = assembler.received_source_file_hash_v1(
        path, len(content), chunks(),
    )
    assert [bytes(chunk) for chunk in chunks_seen] == [
        b"print(", b"'ok'", b")\n",
    ]
    assert observed == _independent_file_hash(path, content)
    assert assembler.received_source_file_hash_v1("empty", 0, ()) == (
        _independent_file_hash("empty", b"")
    )


@pytest.mark.parametrize("size,chunks", [
    (2, [b"a"]),
    (1, [b"ab"]),
    (1, [b""]),
    (0, [b""]),
    (1, ["a"]),
    (True, [b"a"]),
])
def test_streaming_file_hash_rejects_length_type_and_chunk_mismatch(
    size: object, chunks: list[object],
) -> None:
    with pytest.raises(assembler.DistributionAssemblerError) as failure:
        assembler.received_source_file_hash_v1("a", size, chunks)  # type: ignore[arg-type]
    assert failure.value.code == "birth_ownership_distribution_invalid"


def test_encode_decode_are_canonical_sorted_and_independently_bound() -> None:
    readme = b"hello\n"
    module = b"print('ok')\n"
    values = (
        assembler.ReceivedSourceFileV1(
            "pkg/main.py", len(module),
            _independent_file_hash("pkg/main.py", module), 0o755,
        ),
        assembler.ReceivedSourceFileV1(
            "README.md", len(readme),
            _independent_file_hash("README.md", readme), 0o644,
        ),
    )
    record = assembler.build_received_source_v1("metnos", values)
    encoded = assembler.encode_received_source_v1(record)
    unsigned = {
        "schema_version": 1,
        "service_user": "metnos",
        "files": [
            _entry(
                "README.md", size=len(readme),
                content_hash=_independent_file_hash("README.md", readme),
            ),
            _entry(
                "pkg/main.py", size=len(module),
                content_hash=_independent_file_hash("pkg/main.py", module),
                mode=0o755,
            ),
        ],
    }
    expected = _canonical(_with_source_id(unsigned))
    assert encoded == expected
    assert all(byte < 128 for byte in encoded)
    decoded = assembler.decode_received_source_v1(encoded)
    assert decoded.source_id == _with_source_id(unsigned)["source_id"]
    assert decoded.service_user == "metnos"
    assert tuple(item.path for item in decoded.files) == (
        "README.md", "pkg/main.py",
    )
    assert decoded == record
    assert assembler.encode_received_source_v1(decoded) == encoded
    with pytest.raises(FrozenInstanceError):
        decoded.service_user = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decoded.files[0].mode = 0o755  # type: ignore[misc]


@pytest.mark.parametrize("mutation", [
    "schema_bool",
    "root_extra",
    "file_extra",
    "size_bool",
    "mode_bool",
    "bad_mode",
    "uppercase_digest",
    "bad_source_id",
    "bad_service_user",
    "absolute_path",
    "backslash_path",
    "non_nfc_path",
    "reserved_path",
    "reserved_descendant",
    "empty_files",
    "duplicate_path",
    "file_ancestor_collision",
    "wrong_order",
    "float_size",
])
def test_semantic_mutants_are_rejected(mutation: str) -> None:
    files = [_entry("a"), _entry("b/c", content_hash=_HASH_B, mode=0o755)]
    service_user: object = "metnos"
    schema: object = 1
    extra = None
    if mutation == "schema_bool":
        schema = True
    elif mutation == "root_extra":
        extra = {"extra": None}
    elif mutation == "file_extra":
        files[0]["extra"] = None
    elif mutation == "size_bool":
        files[0]["size"] = True
    elif mutation == "mode_bool":
        files[0]["mode"] = True
    elif mutation == "bad_mode":
        files[0]["mode"] = 0o664
    elif mutation == "uppercase_digest":
        files[0]["content_hash"] = "sha256:" + "A" * 64
    elif mutation == "bad_service_user":
        service_user = "Root User"
    elif mutation == "absolute_path":
        files[0]["path"] = "/a"
    elif mutation == "backslash_path":
        files[0]["path"] = "a\\b"
    elif mutation == "non_nfc_path":
        files[0]["path"] = "cafe\N{COMBINING ACUTE ACCENT}"
    elif mutation == "reserved_path":
        files[0]["path"] = "received-source-v1.json"
    elif mutation == "reserved_descendant":
        files[0]["path"] = "received-source-v1.json/payload"
    elif mutation == "empty_files":
        files = []
    elif mutation == "duplicate_path":
        files[1]["path"] = "a"
    elif mutation == "file_ancestor_collision":
        files[1]["path"] = "a/child"
    elif mutation == "wrong_order":
        files.reverse()
    elif mutation == "float_size":
        files[0]["size"] = 0.0
    encoded = _encoded_document(
        files, service_user=service_user, schema_version=schema, extra=extra,
    )
    if mutation == "bad_source_id":
        document = json.loads(encoded)
        document["source_id"] = "sha256:" + "f" * 64
        encoded = _canonical(document)
    with pytest.raises(assembler.DistributionAssemblerError) as failure:
        assembler.decode_received_source_v1(encoded)
    assert failure.value.code == "birth_ownership_distribution_invalid"


def test_noncanonical_and_duplicate_json_are_rejected() -> None:
    canonical = _encoded_document([_entry("a")])
    value = json.loads(canonical)
    noncanonical = json.dumps(value, ensure_ascii=True, indent=1).encode("ascii")
    with pytest.raises(assembler.DistributionAssemblerError, match="canonical json"):
        assembler.decode_received_source_v1(noncanonical)
    duplicate = canonical.replace(
        b'{"files":', b'{"schema_version":1,"files":', 1,
    )
    with pytest.raises(assembler.DistributionAssemblerError, match="duplicate key"):
        assembler.decode_received_source_v1(duplicate)


def test_hostile_parser_depth_and_chunk_iterator_are_closed() -> None:
    nested = b"[" * 10_000 + b"]" * 10_000
    with pytest.raises(assembler.DistributionAssemblerError) as failure:
        assembler.decode_received_source_v1(nested)
    assert failure.value.code == "birth_ownership_distribution_invalid"

    def broken_chunks():
        yield b"a"
        raise OSError("private/source/path")

    with pytest.raises(assembler.DistributionAssemblerError) as failure:
        assembler.received_source_file_hash_v1("a", 2, broken_chunks())
    assert failure.value.code == "birth_ownership_distribution_invalid"
    assert "private/source/path" not in str(failure.value)


def test_limits_are_exact_without_large_payload_allocations(monkeypatch) -> None:
    assert assembler.MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1 == 16 * 1024 * 1024
    assert assembler.MAX_RECEIVED_SOURCE_FILES_V1 == 20_000
    assert assembler.MAX_RECEIVED_SOURCE_DIRECTORIES_V1 == 20_000
    assert assembler.MAX_RECEIVED_SOURCE_PATH_DEPTH_V1 == 32
    assert assembler.MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1 == 2 * 1024 * 1024 * 1024

    over_total = _encoded_document([
        _entry("a", size=assembler.MAX_RECEIVED_SOURCE_TOTAL_BYTES_V1),
        _entry("b", size=1),
    ])
    with pytest.raises(assembler.DistributionAssemblerError, match="total size"):
        assembler.decode_received_source_v1(over_total)

    monkeypatch.setattr(assembler, "MAX_RECEIVED_SOURCE_FILES_V1", 2)
    with pytest.raises(assembler.DistributionAssemblerError, match="file count"):
        assembler.build_received_source_v1("metnos", (
            assembler.ReceivedSourceFileV1("a", 0, _HASH_A, 0o644),
            assembler.ReceivedSourceFileV1("b", 0, _HASH_A, 0o644),
            assembler.ReceivedSourceFileV1("c", 0, _HASH_A, 0o644),
        ))

    monkeypatch.setattr(assembler, "MAX_RECEIVED_SOURCE_DESCRIPTOR_BYTES_V1", 32)
    with pytest.raises(assembler.DistributionAssemblerError, match="descriptor size"):
        assembler.decode_received_source_v1(b" " * 33)
    with pytest.raises(assembler.DistributionAssemblerError, match="descriptor size"):
        assembler.build_received_source_v1("metnos", (
            assembler.ReceivedSourceFileV1("a", 0, _HASH_A, 0o644),
        ))


def test_path_depth_and_directory_limits_are_exact(monkeypatch) -> None:
    maximum_depth = "/".join(
        ["d"] * (assembler.MAX_RECEIVED_SOURCE_PATH_DEPTH_V1 - 1) + ["f"]
    )
    record = assembler.build_received_source_v1("metnos", (
        assembler.ReceivedSourceFileV1(maximum_depth, 0, _HASH_A, 0o644),
    ))
    assert record.files[0].path == maximum_depth

    over_depth = "d/" + maximum_depth
    with pytest.raises(assembler.DistributionAssemblerError, match="file path"):
        assembler.build_received_source_v1("metnos", (
            assembler.ReceivedSourceFileV1(over_depth, 0, _HASH_A, 0o644),
        ))
    with pytest.raises(assembler.DistributionAssemblerError, match="file path"):
        assembler.decode_received_source_v1(_encoded_document([
            _entry(over_depth),
        ]))

    monkeypatch.setattr(assembler, "MAX_RECEIVED_SOURCE_DIRECTORIES_V1", 1)
    files = (
        assembler.ReceivedSourceFileV1("a/f", 0, _HASH_A, 0o644),
        assembler.ReceivedSourceFileV1("b/f", 0, _HASH_B, 0o644),
    )
    with pytest.raises(assembler.DistributionAssemblerError, match="directory count"):
        assembler.build_received_source_v1("metnos", files)
    with pytest.raises(assembler.DistributionAssemblerError, match="directory count"):
        assembler.decode_received_source_v1(_encoded_document([
            _entry("a/f"), _entry("b/f", content_hash=_HASH_B),
        ]))


def test_encoder_rejects_invalid_entries_and_path_collisions() -> None:
    with pytest.raises(assembler.DistributionAssemblerError, match="file entry"):
        assembler.build_received_source_v1(
            "metnos", (_entry("a"),),  # type: ignore[arg-type]
        )
    with pytest.raises(assembler.DistributionAssemblerError, match="file path collision"):
        assembler.build_received_source_v1("metnos", (
            assembler.ReceivedSourceFileV1("a/child", 0, _HASH_A, 0o644),
            assembler.ReceivedSourceFileV1("a", 0, _HASH_B, 0o644),
        ))


def test_builder_is_the_only_source_id_minter_and_encoder_is_fail_closed() -> None:
    files = (assembler.ReceivedSourceFileV1("a", 0, _HASH_A, 0o644),)
    record = assembler.build_received_source_v1("metnos", files)
    assert assembler.decode_received_source_v1(
        assembler.encode_received_source_v1(record)
    ) == record
    forged = assembler.ReceivedSourceV1(_HASH_B, "metnos", record.files)
    with pytest.raises(assembler.DistributionAssemblerError, match="source id"):
        assembler.encode_received_source_v1(forged)
    with pytest.raises(assembler.DistributionAssemblerError, match="received source"):
        assembler.encode_received_source_v1(record.files)  # type: ignore[arg-type]
    with pytest.raises(assembler.DistributionAssemblerError, match="files"):
        assembler.build_received_source_v1(
            "metnos", list(files),  # type: ignore[arg-type]
        )
