"""Adversarial certification of the Birth provisioning journal (increment 2B).

The header and the checkpoints are the only durable memory a resumed run has,
so the bytes are pinned here.  A golden vector that moves is a protocol change,
not a test to update.
"""
from __future__ import annotations

import json

import pytest

import executor_birth_provisioning as provisioning
from executor_birth_provisioning import (
    BirthProvisioningError, CheckpointV1, PayloadConfidentialityV1,
    PayloadObjectTypeV1, PayloadRecordV1, PlatformIdentityV1,
    ProvisioningStateV1, TransactionHeaderV1, checkpoint_name_v1,
    decode_canonical_document_v1, decode_checkpoint_v1,
    decode_transaction_header_v1, empty_digests_v1,
)

TRANSACTION = "0123456789abcdef0123456789abcdef"
BUILD = "rm0008-group2-2b"

GOLDEN_HEADER = (
    b'{"protocol":"birth-authority-provisioning-v1",'
    b'"provisioner_build_id":"rm0008-group2-2b","schema_version":1,'
    b'"transaction_id":"0123456789abcdef0123456789abcdef"}'
)

GOLDEN_ZERO = (
    b'{"approval_input_sha256":null,"author_source_public_inventory_sha256":null,'
    b'"author_store_public_inventory_sha256":null,"checkpoint_sequence":0,'
    b'"checkpoint_sha256":"0735cc7302962950c4191c199697735a6908617b3ee59448bea'
    b'cae3985dce0f8","context_material_sha256":null,'
    b'"context_source_inventory_sha256":null,"payload_inventory":[],'
    b'"previous_checkpoint_sha256":null,"producer_catalog_sha256":null,'
    b'"schema_version":1,"semantic_input_sha256":null,"set_id":null,'
    b'"set_json_sha256":null,"state":"created",'
    b'"transaction_id":"0123456789abcdef0123456789abcdef"}'
)

GOLDEN_ZERO_DIGEST = (
    "0735cc7302962950c4191c199697735a6908617b3ee59448beacae3985dce0f8"
)

GOLDEN_ONE_DIGEST = (
    "f40ca3c88129cd674ae069597bf883be347dc032a6f44333cd2c47a0a7cd7e54"
)


def _header() -> TransactionHeaderV1:
    return TransactionHeaderV1(TRANSACTION, BUILD)


def _zero() -> CheckpointV1:
    return CheckpointV1(
        TRANSACTION, 0, None, ProvisioningStateV1.created, (),
        empty_digests_v1(), None,
    )


def _records() -> tuple[PayloadRecordV1, ...]:
    return (
        PayloadRecordV1(
            "author-root-v1/public/b.pub", PayloadObjectTypeV1.file,
            PayloadConfidentialityV1.integrity_only, 32, "33" * 32,
            PlatformIdentityV1("posix", device=2049, inode=7),
        ),
        PayloadRecordV1(
            "author-root-v1", PayloadObjectTypeV1.directory,
            PayloadConfidentialityV1.confidential, None, None,
            PlatformIdentityV1(
                "windows", volume_serial="0000000000000001", file_id="4" * 32,
            ),
        ),
    )


def _one() -> CheckpointV1:
    digests = empty_digests_v1()
    digests["author_source_public_inventory_sha256"] = "11" * 32
    digests["author_store_public_inventory_sha256"] = "22" * 32
    return CheckpointV1(
        TRANSACTION, 1, GOLDEN_ZERO_DIGEST, ProvisioningStateV1.author_staged,
        _records(), digests, "5" * 64,
    )


def _mutate(raw: bytes, **changes) -> bytes:
    value = json.loads(raw.decode("utf-8"))
    for key, item in changes.items():
        if item is provisioning:
            value.pop(key)
        else:
            value[key] = item
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def test_header_bytes_are_the_golden_vector():
    assert _header().encode() == GOLDEN_HEADER
    assert decode_transaction_header_v1(GOLDEN_HEADER) == _header()


def test_checkpoint_zero_bytes_and_digest_are_the_golden_vector():
    zero = _zero()
    assert zero.encode() == GOLDEN_ZERO
    assert zero.digest() == GOLDEN_ZERO_DIGEST
    assert decode_checkpoint_v1(GOLDEN_ZERO) == zero


def test_checkpoint_with_inventory_orders_by_utf8_bytes():
    one = _one()
    assert one.digest() == GOLDEN_ONE_DIGEST
    document = json.loads(one.encode().decode("utf-8"))
    assert [item["relative_path"] for item in document["payload_inventory"]] == [
        "author-root-v1", "author-root-v1/public/b.pub",
    ]
    assert decode_checkpoint_v1(one.encode()) == one


def test_digest_covers_every_field_except_its_own():
    one = _one()
    document = json.loads(one.encode().decode("utf-8"))
    assert set(document) - {"checkpoint_sha256"} == set(
        json.loads(
            provisioning.encode_canonical_document_v1({
                key: value for key, value in document.items()
                if key != "checkpoint_sha256"
            }).decode("utf-8")
        )
    )
    assert document["checkpoint_sha256"] == GOLDEN_ONE_DIGEST


def test_checkpoint_name_is_the_only_authoritative_name():
    assert checkpoint_name_v1(0) == "0" * 20 + ".json"
    assert checkpoint_name_v1(8191) == "0" * 16 + "8191.json"
    assert _one().name() == "0" * 19 + "1.json"
    for invalid in (-1, 8192, True, 1.0, "1"):
        with pytest.raises(BirthProvisioningError) as error:
            checkpoint_name_v1(invalid)
        assert error.value.code == "birth_provisioning_transaction_conflict"


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}',
    b'{"schema_version": 1}',
    b'{"b":1,"a":2}',
    GOLDEN_HEADER + b"\n",
    b'[]',
    b'{"a":NaN}',
    b"\xff\xfe",
    b'{"a":1} ',
])
def test_non_canonical_documents_are_refused(raw):
    with pytest.raises(BirthProvisioningError) as error:
        decode_canonical_document_v1(raw)
    assert error.value.code == "birth_provisioning_transaction_conflict"


@pytest.mark.parametrize("changes", [
    {"protocol": "other"},
    {"schema_version": 2},
    {"provisioner_build_id": ""},
    {"transaction_id": "0123"},
    {"extra": 1},
    {"provisioner_build_id": provisioning},
])
def test_header_schema_is_closed(changes):
    with pytest.raises(BirthProvisioningError) as error:
        decode_transaction_header_v1(_mutate(GOLDEN_HEADER, **changes))
    assert error.value.code == "birth_provisioning_transaction_conflict"


@pytest.mark.parametrize("changes", [
    {"checkpoint_sha256": "0" * 64},
    {"checkpoint_sha256": "zz" * 32},
    {"state": "unknown"},
    {"schema_version": 2},
    {"checkpoint_sequence": 8192},
    {"checkpoint_sequence": -1},
    {"previous_checkpoint_sha256": "aa" * 32},
    {"set_id": "5" * 63},
    {"unexpected": None},
    {"set_id": provisioning},
    {"payload_inventory": {}},
])
def test_checkpoint_schema_is_closed(changes):
    with pytest.raises(BirthProvisioningError) as error:
        decode_checkpoint_v1(_mutate(GOLDEN_ZERO, **changes))
    assert error.value.code == "birth_provisioning_transaction_conflict"


def test_shuffled_inventory_does_not_round_trip():
    raw = _one().encode()
    document = json.loads(raw.decode("utf-8"))
    document["payload_inventory"].reverse()
    shuffled = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert shuffled != raw
    with pytest.raises(BirthProvisioningError) as error:
        decode_checkpoint_v1(shuffled)
    assert error.value.code == "birth_provisioning_transaction_conflict"


def test_repeated_relative_path_is_refused():
    record = _records()[0]
    with pytest.raises(BirthProvisioningError):
        CheckpointV1(
            TRANSACTION, 0, None, ProvisioningStateV1.created,
            (record, record), empty_digests_v1(), None,
        ).encode()


@pytest.mark.parametrize("path", [
    "", "/absolute", "a//b", "a/", "a\\b", "./a", "a/../b", "a/./b", 1, None,
])
def test_payload_paths_are_canonical_relative(path):
    with pytest.raises(BirthProvisioningError):
        PayloadRecordV1(
            path, PayloadObjectTypeV1.directory,
            PayloadConfidentialityV1.confidential, None, None,
            PlatformIdentityV1("posix", device=1, inode=1),
        )


def test_file_and_directory_records_carry_disjoint_facts():
    with pytest.raises(BirthProvisioningError):
        PayloadRecordV1(
            "a", PayloadObjectTypeV1.file, PayloadConfidentialityV1.confidential,
            None, None, PlatformIdentityV1("posix", device=1, inode=1),
        )
    with pytest.raises(BirthProvisioningError):
        PayloadRecordV1(
            "a", PayloadObjectTypeV1.directory,
            PayloadConfidentialityV1.confidential, 0, "0" * 64,
            PlatformIdentityV1("posix", device=1, inode=1),
        )


@pytest.mark.parametrize("kwargs", [
    {"platform": "posix", "device": -1, "inode": 0},
    {"platform": "posix", "device": True, "inode": 0},
    {"platform": "posix", "device": 0},
    {"platform": "posix", "device": 0, "inode": 0, "file_id": "4" * 32},
    {"platform": "windows", "volume_serial": "0" * 15, "file_id": "4" * 32},
    {"platform": "windows", "volume_serial": "0" * 16, "file_id": "G" * 32},
    {"platform": "windows", "volume_serial": "0" * 16, "file_id": "4" * 32,
     "device": 1},
    {"platform": "darwin", "device": 0, "inode": 0},
])
def test_platform_identity_is_typed_and_closed(kwargs):
    with pytest.raises(BirthProvisioningError):
        PlatformIdentityV1(**kwargs)


def test_states_are_closed_and_monotone():
    assert [state.value for state in ProvisioningStateV1] == [
        "created", "author_staged", "inputs_staged", "authorities_staged",
        "context_staged", "verified", "author_installed", "set_installed",
        "marker_installed",
    ]
    ranks = [provisioning.state_rank_v1(state) for state in ProvisioningStateV1]
    assert ranks == sorted(set(ranks))


def test_checkpoint_zero_alone_may_omit_the_predecessor():
    with pytest.raises(BirthProvisioningError):
        CheckpointV1(
            TRANSACTION, 1, None, ProvisioningStateV1.created, (),
            empty_digests_v1(), None,
        )
    with pytest.raises(BirthProvisioningError):
        CheckpointV1(
            TRANSACTION, 0, "aa" * 32, ProvisioningStateV1.created, (),
            empty_digests_v1(), None,
        )


def test_digest_fields_are_the_closed_set():
    digests = empty_digests_v1()
    assert set(digests) == {
        "author_source_public_inventory_sha256", "approval_input_sha256",
        "semantic_input_sha256", "producer_catalog_sha256",
        "context_source_inventory_sha256",
        "author_store_public_inventory_sha256", "set_json_sha256",
        "context_material_sha256",
    }
    for broken in ({}, {**digests, "other": None},
                   {**digests, "approval_input_sha256": "zz" * 32}):
        with pytest.raises(BirthProvisioningError):
            CheckpointV1(
                TRANSACTION, 0, None, ProvisioningStateV1.created, (),
                broken, None,
            )


def test_checkpoint_keeps_no_writable_reference_to_its_digests():
    digests = empty_digests_v1()
    checkpoint = CheckpointV1(
        TRANSACTION, 0, None, ProvisioningStateV1.created, (), digests, None,
    )
    digests["approval_input_sha256"] = "aa" * 32
    assert checkpoint.digests["approval_input_sha256"] is None
    with pytest.raises(TypeError):
        checkpoint.digests["approval_input_sha256"] = "aa" * 32


def test_public_failure_carries_only_the_stable_code():
    try:
        try:
            raise OSError(13, "permission denied on /home/someone/secret")
        except OSError as exc:
            raise BirthProvisioningError("birth_provisioning_io_unavailable", exc)
    except BirthProvisioningError as error:
        assert error.args == ("birth_provisioning_io_unavailable",)
        assert error.__cause__ is None and error.__context__ is None
        assert "secret" not in str(error)
