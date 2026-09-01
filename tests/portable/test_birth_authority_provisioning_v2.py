"""Focused checks for the F4 transition provisioning header."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

import executor_birth_prepared_set as prepared_module
from executor_birth_distribution_manifest import (
    DistributionFile, _verified_distribution_for_test,
)
from executor_birth_ownership_coordinator import (
    SuccessorClaimV1, _successor_claim_id_v1,
)
from executor_birth_ownership_preflight import (
    _sealed_build_identity_for_test,
)
from executor_birth_prepared_set import PREPARED_STATE_V1, PreparedSetV1
from install.birth_authority_provisioner import (
    BirthProvisioningError, TransactionHeaderV2,
    _build_transaction_header_v2, decode_transaction_header_v2,
    provisioning_source_inventory_hash_v2,
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _prepared() -> PreparedSetV1:
    values = dict(
        set_id="6" * 64,
        state=PREPARED_STATE_V1,
        author_active_key_id="author",
        author_verifier_key_ids=("author",),
        admission_active_key_id="admission",
        producer_keys={},
        prepared_admission_context_id=D("7"),
        prepared_context_epoch=D("8"),
        context_material_sha256="9" * 64,
        set_json_sha256="a" * 64,
        provisioning_transaction_id="b" * 32,
        provisioner_build_id="build-v1",
    )
    return PreparedSetV1(
        **values,
        _artifact_binding=prepared_module._prepared_set_artifact_binding_v1(
            values,
        ),
        _seal=prepared_module._PREPARED_SET_SEAL_V1,
    )


def _distribution():
    identity = _sealed_build_identity_for_test(D("2"), D("d"), "closed-v1")
    distribution = _verified_distribution_for_test(
        identity,
        previous_closed_build_id=None,
        release_sequence=1,
        encoded=b"distribution",
        signature=b"s" * 64,
    )
    return replace(distribution, files=(
        DistributionFile("z/file", 2, D("e"), "runtime_code"),
        DistributionFile("a/file", 1, D("f"), "runtime_code"),
    ))


def _claim() -> SuccessorClaimV1:
    value = {
        "schema_version": 1,
        "previous_head_id": None,
        "release_sequence": 1,
        "request_id": D("1"),
        "source_id": D("3"),
        "closed_build_id": D("2"),
    }
    return SuccessorClaimV1(
        claim_id=_successor_claim_id_v1(value),
        previous_head_id=None,
        release_sequence=1,
        request_id=D("1"),
        source_id=D("3"),
        closed_build_id=D("2"),
    )


def test_v2_header_binds_claim_build_previous_set_and_sorted_sources():
    distribution = _distribution()
    header = _build_transaction_header_v2(
        transaction_id="0" * 32,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=distribution,
        previous_set=_prepared(),
    )

    assert isinstance(header, TransactionHeaderV2)
    assert header.request_id == D("1")
    assert header.closed_build_id == D("2")
    assert header.previous_set_id == "6" * 64
    assert header.source_inventory_hash == provisioning_source_inventory_hash_v2(
        distribution,
    )
    assert decode_transaction_header_v2(header.encode()) == header
    assert provisioning_source_inventory_hash_v2(
        replace(distribution, files=tuple(reversed(distribution.files))),
    ) == header.source_inventory_hash


@pytest.mark.parametrize(
    "change",
    (
        lambda value: value.update(schema_version=1),
        lambda value: value.update(protocol="birth-authority-provisioning-v1"),
        lambda value: value.update(extra=True),
        lambda value: value.pop("previous_set_id"),
    ),
)
def test_v2_header_has_no_legacy_or_open_schema_fallback(change):
    header = _build_transaction_header_v2(
        transaction_id="0" * 32,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    value = json.loads(header.encode())
    change(value)
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(BirthProvisioningError):
        decode_transaction_header_v2(encoded)


def test_v2_header_rejects_a_claim_for_another_verified_build():
    claim = _claim()
    with pytest.raises(BirthProvisioningError):
        _build_transaction_header_v2(
            transaction_id="0" * 32,
            provisioner_build_id="build-v2",
            claim=replace(
                claim,
                closed_build_id=D("4"),
                claim_id=_successor_claim_id_v1({
                    **claim.as_value(include_id=False),
                    "closed_build_id": D("4"),
                }),
            ),
            distribution=_distribution(),
            previous_set=_prepared(),
        )
