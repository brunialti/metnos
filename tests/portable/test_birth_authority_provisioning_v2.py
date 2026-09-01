"""Focused checks for the F4 transition provisioning header."""
from __future__ import annotations

import hashlib
import json
import os
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
    BirthProvisioningError, CheckpointV1, ProvisioningStateV1,
    MaterialPlanEntryV2, MaterialPlanV2, PayloadConfidentialityV1,
    PayloadObjectTypeV1, TransactionHeaderV2,
    _build_transaction_header_v2, decode_transaction_header_v2,
    decode_material_plan_v2, empty_digests_v1,
    provisioning_source_inventory_hash_v2,
)
from rm0008_2b import support


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


def _material_plan(header: TransactionHeaderV2) -> MaterialPlanV2:
    entries = (
        MaterialPlanEntryV2(
            "authority-set", PayloadObjectTypeV1.directory,
            PayloadConfidentialityV1.integrity_only, None,
        ),
        MaterialPlanEntryV2(
            "authority-set/admission", PayloadObjectTypeV1.directory,
            PayloadConfidentialityV1.confidential, None,
        ),
        MaterialPlanEntryV2(
            "authority-set/admission/keystore.json",
            PayloadObjectTypeV1.file,
            PayloadConfidentialityV1.confidential, b"sealed-plan",
        ),
    )
    return MaterialPlanV2(
        transaction_id=header.transaction_id,
        transaction_header_sha256=hashlib.sha256(header.encode()).hexdigest(),
        entries=entries,
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


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_header_and_checkpoint_survive_a_real_reopen(tmp_path, monkeypatch):
    from install import birth_authority_provisioner as provisioning

    base = support.make_config(tmp_path)
    layout = support.open_layout(monkeypatch, base)
    transaction_id = "0" * 32
    header = _build_transaction_header_v2(
        transaction_id=transaction_id,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            journal.ensure_checkpoints()
            journal.append(CheckpointV1(
                transaction_id, 0, None, ProvisioningStateV1.created, (),
                empty_digests_v1(), None,
            ))

    reopened = support.open_layout(monkeypatch, base).birth_session
    with reopened:
        with reopened.global_lock(exclusive=True, create=True):
            state = provisioning._TransactionJournalV1.transition_v2(
                reopened, transaction_id,
            ).read_state()
    assert state.header == header
    assert state.last.state is ProvisioningStateV1.created
    assert state.pending_checkpoint_sequence is None
    assert not state.header_pending


def test_v2_filesystem_grammar_is_versioned_and_does_not_admit_v1_finals():
    import executor_birth_secure_fs as secure_fs

    transaction_id = "0" * 32
    root = (f".birth-provisioning-v2.txn.{transaction_id}",)
    for components in (
        root,
        root + ("transaction-v2.json",),
        root + (f".transaction-v2.pending.{transaction_id}",),
        root + ("material-plan-v2.json",),
        root + (f".material-plan-v2.pending.{transaction_id}",),
        root + ("checkpoints-v1",),
        root + ("authority-set",),
    ):
        assert secure_fs._matching_rows(components)
    for components in (
        root + ("transaction-v1.json",),
        root + (f".transaction-v1.pending.{transaction_id}",),
        root + ("prepared-v1.json",),
        root + ("author-root-v1",),
    ):
        assert secure_fs._matching_rows(components) == ()


def test_v2_material_plan_is_closed_ordered_and_self_authenticating():
    header = _build_transaction_header_v2(
        transaction_id="0" * 32,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    plan = _material_plan(header)

    assert decode_material_plan_v2(plan.encode()) == plan
    value = json.loads(plan.encode())
    value["objects"][2]["payload_hex"] = "00"
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(BirthProvisioningError):
        decode_material_plan_v2(encoded)


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_material_plan_is_reused_after_a_real_reopen(tmp_path, monkeypatch):
    from install import birth_authority_provisioner as provisioning

    base = support.make_config(tmp_path)
    transaction_id = "0" * 32
    header = _build_transaction_header_v2(
        transaction_id=transaction_id,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    layout = support.open_layout(monkeypatch, base)
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            assert journal.ensure_material_plan_v2(
                lambda: _material_plan(header),
            ) == _material_plan(header)

    reopened = support.open_layout(monkeypatch, base).birth_session
    with reopened:
        with reopened.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                reopened, transaction_id,
            )
            assert journal.ensure_material_plan_v2(
                lambda: pytest.fail("the committed plan must be reused"),
            ) == _material_plan(header)


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_material_plan_recovers_a_complete_pending(tmp_path, monkeypatch):
    from executor_birth_secure_fs import _BirthObjectRole
    from install import birth_authority_provisioner as provisioning

    base = support.make_config(tmp_path)
    transaction_id = "0" * 32
    header = _build_transaction_header_v2(
        transaction_id=transaction_id,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    plan = _material_plan(header)
    layout = support.open_layout(monkeypatch, base)
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            session.create_file_exclusive(
                journal.root_components + (
                    f".material-plan-v2.pending.{transaction_id}",
                ),
                plan.encode(), role=_BirthObjectRole.birth_confidential,
            )

            assert journal.ensure_material_plan_v2(
                lambda: pytest.fail("the complete pending must be promoted"),
            ) == plan
            assert set(session.inventory(journal.root_components)) == {
                "transaction-v2.json", "material-plan-v2.json",
            }


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_material_plan_replaces_only_an_incomplete_pending(
    tmp_path, monkeypatch,
):
    from executor_birth_secure_fs import _BirthObjectRole
    from install import birth_authority_provisioner as provisioning

    base = support.make_config(tmp_path)
    transaction_id = "0" * 32
    header = _build_transaction_header_v2(
        transaction_id=transaction_id,
        provisioner_build_id="build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    plan = _material_plan(header)
    layout = support.open_layout(monkeypatch, base)
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            session.create_file_exclusive(
                journal.root_components + (
                    f".material-plan-v2.pending.{transaction_id}",
                ),
                plan.encode()[:31], role=_BirthObjectRole.birth_confidential,
            )

            assert journal.ensure_material_plan_v2(lambda: plan) == plan
            assert set(session.inventory(journal.root_components)) == {
                "transaction-v2.json", "material-plan-v2.json",
            }
