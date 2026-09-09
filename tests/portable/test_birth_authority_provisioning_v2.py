"""Focused checks for the F4 transition provisioning header."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_prepared_set as prepared_module
from executor_birth_distribution_manifest import (
    DistributionFile, _verified_distribution_for_test,
)
from executor_birth_cutover import CurrentInventoryV1, CurrentReceiptProof
from executor_birth_ownership_coordinator import (
    SuccessorClaimV1, _successor_claim_id_v1,
)
from executor_birth_ownership_preflight import (
    _sealed_build_identity_for_test,
)
from executor_birth_prepared_set import (
    PREPARED_STATE_V1, PreparedSetError, PreparedSetV1,
)
from install.birth_authority_provisioner import (
    BirthProvisioningError, CheckpointV1, ProvisioningStateV1,
    MaterialPlanEntryV2, MaterialPlanV2, PayloadConfidentialityV1,
    PayloadObjectTypeV1, PreparedAuthoritySetV2, TransactionHeaderV2,
    _build_transaction_header_v2, _materialize_material_plan_v2,
    _publish_prepared_authority_set_v2,
    decode_transaction_header_v2,
    decode_material_plan_v2, empty_digests_v1,
    _prepare_transition_authority_set_v2, is_prepared_authority_set_v2,
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


def _transition_inputs(tmp_path, monkeypatch):
    import config as runtime_config
    from executor_birth_prepared_root import read_prepared_set_v1

    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    previous = read_prepared_set_v1()
    distribution = replace(
        _distribution(),
        installation_root=str(Path(runtime_config.PATH_RUNTIME).parent),
    )
    return base, previous, distribution


def _real_revertible_contract(tmp_path: Path, author: Ed25519PrivateKey):
    from manifest_inventory import ManifestOrigin, ManifestSource
    from sign import sign_manifest_bytes

    project = Path(__file__).resolve().parents[2]
    source_root = tmp_path / "contract-source"
    source = project / "executors" / "change_files_format"
    destination = source_root / source.name
    destination.mkdir(parents=True)
    for name in (
        "manifest.toml", "manifest.lang_state.json", "change_files_format.py",
    ):
        shutil.copyfile(source / name, destination / name)
    (destination / "manifest.toml.sig").write_bytes(sign_manifest_bytes(
        (destination / "manifest.toml").read_bytes(), private_key=author,
    ))
    from manifest_inventory import inventory_authoring_manifests

    observed = inventory_authoring_manifests((ManifestSource(
        ManifestOrigin.USER, source_root,
        min_depth=1, max_depth=1, allowed_code_roots=(source_root,),
    ),))
    assert not observed.problems, observed.problems
    assert len(observed.manifests) == 1
    return observed.manifests[0]


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_first_transition_reads_a_stale_v1_anchor_without_selecting_it(
    tmp_path, monkeypatch,
):
    import config as runtime_config
    from executor_birth_prepared_root import (
        _load_historical_transition_anchor_v1,
        read_prepared_set_v1,
    )

    base, previous, _distribution_value = _transition_inputs(
        tmp_path, monkeypatch,
    )
    marker = base / "birth" / "prepared-v1.json"
    marker_before = marker.read_bytes()
    context_source = Path(runtime_config.PATH_RUNTIME) / "executor_standard.py"
    context_source.write_bytes(context_source.read_bytes() + b"\n")

    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        read_prepared_set_v1()
    assert _load_historical_transition_anchor_v1() == previous
    assert marker.read_bytes() == marker_before


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_stale_v1_anchor_supports_only_verification_transition_consumers(
    tmp_path, monkeypatch,
):
    """Cross every verification-only consumer with one genuine stale anchor."""
    import config as runtime_config
    import contract_store
    import executor_birth_authority_gate as authority_gate
    import executor_birth_bootstrap as bootstrap
    import executor_birth_ownership_chain as ownership_chain
    import executor_birth_prepared_root as prepared_root
    import executor_birth_transition_gate as transition_gate
    import install.executor_birth_contract_convergence as convergence
    import manifest_inventory
    from contract_bootstrap import STORE_RELATIVE
    from executor_birth_context_transition import issue_context_transition_v1
    from executor_birth_cutover import (
        BirthCutoverError, freeze_current_inventory_v1,
    )
    from executor_birth_receipts import verify_admission_receipt
    import executor_birth_transition_receipts as transition_receipts

    author = Ed25519PrivateKey.generate()
    base = support.make_config(
        tmp_path / "installation", author=author, operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    previous = prepared_root.read_prepared_set_v1()

    ref = _real_revertible_contract(tmp_path / "contract", author)
    monkeypatch.setattr(runtime_config, "PATH_SYNTH_EXECUTORS", ref.source_root)
    state_root = tmp_path / "service-state"
    store_root = state_root / STORE_RELATIVE
    trusted = (("fixture-author", author.public_key()),)
    publication = contract_store.publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store_root,
    )
    monkeypatch.setattr(runtime_config, "PATH_USER_STATE", state_root)
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(convergence._REPOSITORY))

    # Keep the source census isolated while retaining its real parser and
    # authentication.  The fixture contract deliberately reuses a product
    # name, which would otherwise create an unrelated cross-origin collision.
    inventory_authoring = manifest_inventory.inventory_authoring_manifests
    source = manifest_inventory.ManifestSource(
        manifest_inventory.ManifestOrigin.USER,
        ref.source_root,
        min_depth=1,
        max_depth=1,
        allowed_code_roots=(ref.source_root,),
    )
    monkeypatch.setattr(
        manifest_inventory,
        "inventory_authoring_manifests",
        lambda *_args, **_kwargs: inventory_authoring((source,)),
    )

    materializations = []
    monkeypatch.setattr(
        contract_store,
        "materialize_repository_authoring_for_transition_v1",
        lambda *, trusted_publics: materializations.append(
            tuple(trusted_publics),
        ) or 1,
    )
    monkeypatch.setattr(
        bootstrap,
        "_build_initial_transition_installer_runtime_v1",
        lambda: pytest.fail("unchanged catalog selected a strict runtime"),
    )

    context_source = Path(runtime_config.PATH_RUNTIME) / "executor_standard.py"
    context_source.write_bytes(context_source.read_bytes() + b"\n")
    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        prepared_root.load_sealed_authorities_v1()

    historical = prepared_root._load_historical_transition_verifiers_v1()
    assert historical.prepared == previous
    assert support.public_bytes(author) in {
        key.public_bytes_raw()
        for key in historical.author_verifier_keys.values()
    }
    assert historical.admission_verifier_keys

    class InitialChain:
        pass

    monkeypatch.setattr(
        ownership_chain, "_InitialOwnershipChainStateV1", InitialChain,
    )
    monkeypatch.setattr(
        authority_gate, "closed_build_enforcement", lambda: True,
    )
    gate, session = object(), object()
    observation = SimpleNamespace(
        distribution=SimpleNamespace(release_sequence=1),
        claim=None,
        predecessor=None,
        phase=None,
        chain=InitialChain(),
        partial=False,
    )
    monkeypatch.setattr(
        transition_gate,
        "_require_transition_gate_snapshot_locked_v2",
        lambda candidate, current: observation
        if candidate is gate and current is session
        else pytest.fail("transition gate binding changed"),
    )
    enumerate_current = transition_gate._transition_current_enumerator_v2(
        gate, session,
    )
    current = enumerate_current()
    assert len(current) == 1
    assert current[0].generation_id == publication.current_generation_id

    assert convergence.converge() == {
        "changed": 0, "current": 1, "examined": 1,
    }
    assert bootstrap.verify_initial_installer_store_v1(
        prove_quiescent=lambda: True,
        trusted_authoring_owner=(os.geteuid(), os.getegid()),
        defer_v1_receipts_to_transition_v2=True,
    ) == {"contracts": 1, "receipts": 0}
    assert len(materializations) == 2

    # Continue on the same fixture into the new set and a genuine V2 receipt.
    # The historical view is verification-only; signing happens exclusively
    # through the newly staged context built from source B.
    distribution = replace(
        _distribution(),
        installation_root=str(Path(runtime_config.PATH_RUNTIME).parent),
    )
    claim = _claim()
    prepared = _prepare_transition_authority_set_v2(
        claim, distribution, previous,
    )
    _publish_prepared_authority_set_v2(prepared)
    inventory = freeze_current_inventory_v1(current)
    _encoded, transition = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=None,
        previous_set_id=previous.set_id,
        previous_admission_context_id=(
            previous.prepared_admission_context_id
        ),
        previous_context_epoch=previous.prepared_context_epoch,
        set_id=prepared.target_set_id,
        prepared_admission_context_id=(
            prepared.target_admission_context_id
        ),
        prepared_context_epoch=prepared.target_context_epoch,
        context_material_sha256=prepared.target_context_material_sha256,
        set_json_sha256=prepared.target_set_json_sha256,
        current_inventory=inventory,
    )
    staged = prepared_root._load_staged_reattestation_context_v1(
        transition, distribution, inventory,
    )
    owner = (os.getuid(), os.getgid())
    effective = [(0, 0)]
    monkeypatch.setattr(
        transition_receipts.os, "geteuid", lambda: effective[0][0],
    )
    monkeypatch.setattr(
        transition_receipts.os, "getegid", lambda: effective[0][1],
    )

    @contextmanager
    def service_identity():
        assert effective[0] == (0, 0)
        effective[0] = owner
        try:
            yield
        finally:
            effective[0] = (0, 0)

    def enumerate_for_receipts():
        assert effective[0] == (0, 0)
        return current

    proof = transition_receipts._build_staged_current_receipts_v2(
        staged,
        now=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        prove_quiescent=lambda: True,
        expected_inventory=inventory,
        enumerate_current=enumerate_for_receipts,
        identity_scope=service_identity,
        catalog_owner=owner,
    )
    receipts = tuple(store_root.glob("*/admission-receipts-v2/*/*.json"))
    assert proof.inventory == inventory
    assert len(receipts) == 1
    assert verify_admission_receipt(
        receipts[0].read_bytes(),
        verifier_keys=staged.authorities.admission.verifier_keys,
    ).generation_id == publication.current_generation_id

    contract_dir = contract_store._existing_contract_directory(
        ref.contract_id, store_root=store_root,
    )
    binding = contract_dir / contract_store.BINDING_FILE
    mutated = json.loads(binding.read_bytes())
    mutated["schema_version"] = 2
    binding.write_bytes(support.canonical_json(mutated))
    with pytest.raises(BirthCutoverError, match="birth_cutover_inventory_invalid"):
        enumerate_current()

    marker = base / "birth" / "prepared-v1.json"
    marker_value = json.loads(marker.read_bytes())
    marker_value["author_store_public_inventory_sha256"] = "0" * 64
    marker.write_bytes(support.canonical_json(marker_value))
    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        prepared_root._load_historical_transition_verifiers_v1()


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
            "authority-set/admission/birth-keystore.lock",
            PayloadObjectTypeV1.file,
            PayloadConfidentialityV1.confidential, b"0",
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
    value["objects"][3]["payload_hex"] = "00"
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


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_material_plan_preserves_a_complete_foreign_pending(
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
    foreign_header = _build_transaction_header_v2(
        transaction_id=transaction_id,
        provisioner_build_id="another-build-v2",
        claim=_claim(),
        distribution=_distribution(),
        previous_set=_prepared(),
    )
    foreign_plan = _material_plan(foreign_header)
    layout = support.open_layout(monkeypatch, base)
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            pending = f".material-plan-v2.pending.{transaction_id}"
            session.create_file_exclusive(
                journal.root_components + (pending,),
                foreign_plan.encode(), role=_BirthObjectRole.birth_confidential,
            )

            with pytest.raises(
                BirthProvisioningError,
                match="birth_provisioning_transaction_conflict",
            ):
                journal.ensure_material_plan_v2(
                    lambda: pytest.fail("a foreign plan must stop recovery"),
                )
            assert set(session.inventory(journal.root_components)) == {
                "transaction-v2.json", pending,
            }
            assert session.read_file(
                journal.root_components + (pending,),
                maximum=len(foreign_plan.encode()),
                role=_BirthObjectRole.birth_confidential,
            ) == foreign_plan.encode()


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_material_plan_expansion_resumes_without_replacing_exact_bytes(
    tmp_path, monkeypatch,
):
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
            plan = journal.ensure_material_plan_v2(lambda: plan)
            original = provisioning._TransactionJournalV1.publish_payload
            calls = 0

            def interrupt_second_file(self, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated stop")
                return original(self, *args, **kwargs)

            monkeypatch.setattr(
                provisioning._TransactionJournalV1,
                "publish_payload", interrupt_second_file,
            )
            with pytest.raises(RuntimeError, match="simulated stop"):
                _materialize_material_plan_v2(session, journal, plan)
            first_identity = next(
                entry.identity
                for entry in session._inventory_state(
                    journal.root_components + ("authority-set", "admission")
                )
                if entry.name == "birth-keystore.lock"
            )

            monkeypatch.setattr(
                provisioning._TransactionJournalV1,
                "publish_payload", original,
            )
            recovered = _materialize_material_plan_v2(session, journal, plan)
            repeated = _materialize_material_plan_v2(session, journal, plan)
            second_identity = next(
                entry.identity
                for entry in session._inventory_state(
                    journal.root_components + ("authority-set", "admission")
                )
                if entry.name == "birth-keystore.lock"
            )

            assert recovered == repeated
            assert first_identity == second_identity
            assert session.read_file(
                journal.root_components + (
                    "authority-set", "admission", "keystore.json",
                ),
                maximum=len(b"sealed-plan"),
                role=provisioning._TransactionJournalV1._confidential_role(),
            ) == b"sealed-plan"
            changed_entries = list(plan.entries)
            changed_entries[-1] = replace(
                changed_entries[-1], payload=b"different-plan",
            )
            with pytest.raises(BirthProvisioningError):
                _materialize_material_plan_v2(
                    session, journal, replace(
                        plan, entries=tuple(changed_entries),
                    ),
                )


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
@pytest.mark.parametrize(
    ("pending_payload", "conflicts"),
    ((b"0", False), (b"", False), (b"1", True)),
)
def test_v2_material_plan_expansion_recovers_its_next_pending(
    tmp_path, monkeypatch, pending_payload, conflicts,
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
            plan = journal.ensure_material_plan_v2(lambda: plan)
            base_components = journal.root_components + ("authority-set",)
            session.create_directory_exclusive(
                base_components, role=_BirthObjectRole.birth_integrity_only,
            )
            session.create_directory_exclusive(
                base_components + ("admission",),
                role=_BirthObjectRole.birth_confidential,
            )
            pending = (
                ".payload-pending-00000000000000000001-" + transaction_id
            )
            session.create_file_exclusive(
                base_components + ("admission", pending), pending_payload,
                role=_BirthObjectRole.birth_confidential,
            )

            if conflicts:
                with pytest.raises(
                    BirthProvisioningError,
                    match="birth_provisioning_transaction_conflict",
                ):
                    _materialize_material_plan_v2(session, journal, plan)
                assert session.read_file(
                    base_components + ("admission", pending),
                    maximum=len(pending_payload),
                    role=_BirthObjectRole.birth_confidential,
                ) == pending_payload
                return

            _materialize_material_plan_v2(session, journal, plan)

            assert pending not in session.inventory(
                base_components + ("admission",)
            )
            assert session.read_file(
                base_components + ("admission", "birth-keystore.lock"),
                maximum=1, role=_BirthObjectRole.birth_confidential,
            ) == b"0"


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_builds_a_new_set_without_copying_or_replacing_the_author_root(
    tmp_path, monkeypatch,
):
    import config as runtime_config
    from executor_birth_prepared_set import load_prepared_set_v1
    from install import birth_authority_provisioner as provisioning

    base = support.make_config(
        tmp_path, author=Ed25519PrivateKey.generate(), operator=True,
    )
    support.provision(monkeypatch, base)
    marker_before = (base / "birth" / "prepared-v1.json").read_bytes()
    author_before = {
        item.relative_to(base / "birth" / "author-root-v1").as_posix(): (
            item.read_bytes()
        )
        for item in (base / "birth" / "author-root-v1").rglob("*")
        if item.is_file()
    }
    layout = support.open_layout(monkeypatch, base)
    target_root = runtime_config.PATH_RUNTIME
    distribution = replace(
        _distribution(), installation_root=str(Path(target_root).parent),
    )
    monkeypatch.setattr(
        runtime_config, "PATH_RUNTIME", tmp_path / "must-not-be-opened",
    )
    transaction_id = "0" * 32
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            previous = load_prepared_set_v1(session)
            header = _build_transaction_header_v2(
                transaction_id=transaction_id,
                provisioner_build_id="build-v2",
                claim=_claim(),
                distribution=distribution,
                previous_set=previous,
            )
            journal = provisioning._TransactionJournalV1.transition_v2(
                session, transaction_id,
            )
            journal.create_root()
            journal.write_header(header)
            checkpoint = provisioning._prepare_staged_authority_set_v2(
                session, layout, header, previous, distribution,
            )
            repeated = provisioning._prepare_staged_authority_set_v2(
                session, layout, header, previous, distribution,
            )
            plan = journal._read_material_plan_v2(header)
            staged = journal.root_components + ("authority-set",)
            registry = provisioning._authority_registry_v1(session, staged)
            set_document = json.loads(session.read_file(
                staged + ("set.json",), maximum=1024 * 1024,
            ))

            assert registry["admission"]["active_key_id"] != (
                previous.admission_active_key_id
            )
            assert set_document["author_active_key_id"] == (
                previous.author_active_key_id
            )
            assert set_document["author_verifier_key_ids"] == list(
                previous.author_verifier_key_ids
            )
            assert set_document["provisioning_transaction_id"] == transaction_id
            assert checkpoint == repeated
            assert checkpoint.state is ProvisioningStateV1.verified
            assert len(journal.read_state().chain) == 2
            assert {record.relative_path for record in checkpoint.payload_inventory} == {
                entry.relative_path for entry in plan.entries
            }
            assert "author-root-v1" not in session.inventory(
                journal.root_components
            )

    assert (base / "birth" / "prepared-v1.json").read_bytes() == marker_before
    assert {
        item.relative_to(base / "birth" / "author-root-v1").as_posix(): (
            item.read_bytes()
        )
        for item in (base / "birth" / "author-root-v1").rglob("*")
        if item.is_file()
    } == author_before


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_fixed_entry_returns_the_same_sealed_prepared_set_on_resume(
    tmp_path, monkeypatch,
):
    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    marker_before = (base / "birth" / "prepared-v1.json").read_bytes()

    first = _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    )
    second = _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    )

    assert isinstance(first, PreparedAuthoritySetV2)
    assert is_prepared_authority_set_v2(first)
    assert second == first
    assert first.previous_set_id == previous.set_id
    assert first.target_set_id != previous.set_id
    assert first.request_id == _claim().request_id
    assert len(list((base / "birth").glob(
        ".birth-provisioning-v2.txn.*",
    ))) == 1
    assert (base / "birth" / "prepared-v1.json").read_bytes() == marker_before
    with pytest.raises(PreparedSetError):
        replace(first, target_set_id="0" * 64)
    claim = _claim()
    changed_request = D("4")
    changed_claim = replace(
        claim,
        request_id=changed_request,
        claim_id=_successor_claim_id_v1({
            **claim.as_value(include_id=False),
            "request_id": changed_request,
        }),
    )
    with pytest.raises(BirthProvisioningError):
        _prepare_transition_authority_set_v2(
            changed_claim, distribution, previous,
        )
    assert _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    ) == first


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_publication_moves_the_exact_set_and_preserves_the_v1_anchor(
    tmp_path, monkeypatch,
):
    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    marker = base / "birth" / "prepared-v1.json"
    marker_before = marker.read_bytes()
    author_root = base / "birth" / "author-root-v1"
    author_before = {
        item.relative_to(author_root).as_posix(): item.read_bytes()
        for item in author_root.rglob("*") if item.is_file()
    }
    prepared = _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    )
    transaction = (
        base / "birth"
        / f".birth-provisioning-v2.txn.{prepared.transaction_id}"
    )
    staged = transaction / "authority-set"
    staged_identity = staged.stat().st_ino

    assert _publish_prepared_authority_set_v2(prepared) is prepared

    published = base / "birth" / "authority-sets" / prepared.target_set_id
    assert published.stat().st_ino == staged_identity
    assert _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    ) == prepared
    assert not staged.exists()
    assert marker.read_bytes() == marker_before
    assert {
        item.relative_to(author_root).as_posix(): item.read_bytes()
        for item in author_root.rglob("*") if item.is_file()
    } == author_before
    assert _publish_prepared_authority_set_v2(prepared) is prepared
    assert published.stat().st_ino == staged_identity


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_product_composition_reaches_receipts_after_set_publication(
    tmp_path, monkeypatch,
):
    import config as runtime_config
    from install import birth_authority_provisioner as provisioning
    import executor_birth_distribution_manifest as distribution_module
    import executor_birth_ownership_coordinator as coordinator_module
    import executor_birth_ownership_preflight as preflight_module
    import executor_birth_prepared_root as prepared_root_module
    import executor_birth_transition_gate as transition_gate_module
    import executor_birth_transition_receipts as transition_receipts_module

    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    context_source = Path(runtime_config.PATH_RUNTIME) / "executor_standard.py"
    context_source.write_bytes(context_source.read_bytes() + b"\n")
    with pytest.raises(PreparedSetError, match="birth_prepared_set_mismatch"):
        prepared_root_module.read_prepared_set_v1()
    claim = _claim()
    descriptor = SimpleNamespace(
        descriptor_id=D("9"), service_uid=991, service_gid=992,
    )
    inventory = CurrentInventoryV1(())
    order = []
    session = object()
    result = object()
    publication = object()
    complete = SimpleNamespace(
        state="RECEIPTS_COMPLETE", request_id=claim.request_id,
        current_proof=None, cutover_id=None,
    )

    monkeypatch.setattr(
        distribution_module, "capture_current_deployment_descriptor_v1",
        lambda value: (
            order.append("distribution") or value,
            descriptor,
        ),
    )

    @contextmanager
    def deployment_lock():
        order.append("deployment-lock")
        yield session

    monkeypatch.setattr(
        coordinator_module, "_deployment_lock_v1", deployment_lock,
    )
    monkeypatch.setattr(
        coordinator_module, "_require_deployment_lock_session_v1",
        lambda observed: None if observed is session else pytest.fail(
            "wrong deployment session",
        ),
    )
    monkeypatch.setattr(
        coordinator_module, "_transition_edge_locked_v2",
        lambda observed_session, verified: (
            order.append("graph") or claim,
            None,
        ) if observed_session is session and verified is distribution else None,
    )
    original_anchor = (
        prepared_root_module._load_historical_transition_anchor_v1
    )

    def load_previous_anchor():
        order.append("previous")
        return original_anchor()

    monkeypatch.setattr(
        prepared_root_module, "_load_historical_transition_anchor_v1",
        load_previous_anchor,
    )

    original_prepare = provisioning._prepare_transition_authority_set_v2

    def prepare(*args, completed_predecessor):
        assert completed_predecessor is None
        assert args[2] == previous
        order.append("stage")
        return original_prepare(*args)

    monkeypatch.setattr(
        provisioning, "_prepare_transition_authority_set_v2", prepare,
    )

    @contextmanager
    def service_identity(_descriptor):
        yield

    monkeypatch.setattr(
        provisioning, "_service_owned_birth_identity_v2",
        service_identity,
    )

    @contextmanager
    def maintenance_inventory():
        class Maintenance:
            def __call__(self):
                return True

            def observe(self):
                return {
                    "source": "inactive_http_and_inactive_sidecar",
                    "units": [],
                }

        order.append("maintenance-enter")
        try:
            yield Maintenance(), inventory, b"maintenance", enumerate_current
        finally:
            order.append("maintenance-exit")

    def enumerate_current():
        return ()

    monkeypatch.setattr(
        transition_gate_module, "_require_transition_current_enumerator_v2",
        lambda value, observed_session, candidate: value
        if value is enumerate_current
        and observed_session is session and candidate is distribution
        else pytest.fail("transition enumerator binding changed"),
    )

    monkeypatch.setattr(
        coordinator_module, "_append_prepared_transition_locked_v2",
        lambda observed_session, **values: (
            order.append("prepared") or "record", "transition",
        ) if (
            observed_session is session
            and values["current_inventory"] == inventory
        ) else None,
    )
    original_publish = provisioning._publish_prepared_authority_set_v2

    def publish(prepared):
        order.append("publish")
        return original_publish(prepared)

    monkeypatch.setattr(
        provisioning, "_publish_prepared_authority_set_v2", publish,
    )
    monkeypatch.setattr(
        coordinator_module, "_prepared_transition_publication_v2",
        lambda *args, **kwargs: order.append("publication") or publication,
    )
    monkeypatch.setattr(
        prepared_root_module, "_load_staged_reattestation_context_v1",
        lambda *args: order.append("staged-context") or "context",
    )
    def build_staged_receipts(*_args, **kwargs):
        assert kwargs["enumerate_current"] is enumerate_current
        assert kwargs["catalog_owner"] == (991, 992)
        order.extend(("staged-runtime", "receipts"))
        return CurrentReceiptProof((), {})

    monkeypatch.setattr(
        transition_receipts_module, "_build_staged_current_receipts_v2",
        build_staged_receipts,
    )
    monkeypatch.setattr(
        preflight_module, "canonical_maintenance_proof",
        lambda **_values: b"maintenance",
    )
    monkeypatch.setattr(
        coordinator_module, "_append_receipts_complete_locked_v2",
        lambda *args, **kwargs: order.append("receipts-complete") or complete,
    )
    monkeypatch.setattr(
        coordinator_module, "_publish_context_transition_locked_v2",
        lambda *args: order.append("transition-record") or "transition",
    )
    monkeypatch.setattr(
        coordinator_module, "_result",
        lambda record: order.append("result") or result,
    )

    def prepare_receipts_for_test(candidate):
        with coordinator_module._deployment_lock_v1() as locked_session:
            staged = provisioning._prepare_transition_receipt_material_locked_v2(
                locked_session, candidate,
            )
            with maintenance_inventory() as frozen:
                completed = provisioning._complete_transition_receipts_locked_v2(
                    locked_session, staged, frozen,
                )
            return coordinator_module._result(completed)

    assert prepare_receipts_for_test(distribution) is result
    assert order == [
        "deployment-lock", "distribution", "graph", "previous", "stage",
        "maintenance-enter", "distribution", "prepared", "publish", "publication",
        "staged-context", "staged-runtime", "receipts",
        "receipts-complete", "transition-record", "maintenance-exit",
        "result",
    ]
    assert any(
        item.name == "set.json"
        for item in (
            base / "birth" / "authority-sets"
        ).rglob("set.json")
    )


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_initial_v2_transition_reattests_a_real_revertible_contract(
    tmp_path, monkeypatch,
):
    import config as runtime_config
    import executor_birth_reattestation as reattestation_module
    import executor_birth_transition_receipts as receipts_module
    from contract_bootstrap import STORE_RELATIVE
    from contract_store import publish_signed_source
    from executor_birth_context_transition import issue_context_transition_v1
    from executor_birth_cutover import (
        enumerate_authenticated_current_generations,
        freeze_current_inventory_v1,
    )
    from executor_birth_prepared_root import (
        _load_staged_reattestation_context_v1,
        read_prepared_set_v1,
    )
    from executor_birth_receipts import verify_admission_receipt
    from executor_birth_transition_receipts import (
        _build_staged_current_receipts_v2,
    )

    author = Ed25519PrivateKey.generate()
    base = support.make_config(
        tmp_path / "installation", author=author, operator=True,
    )
    support.provision(monkeypatch, base)
    support.use_config(monkeypatch, base)
    previous = read_prepared_set_v1()
    distribution = replace(
        _distribution(),
        installation_root=str(Path(runtime_config.PATH_RUNTIME).parent),
    )
    ref = _real_revertible_contract(tmp_path, author)
    monkeypatch.setattr(runtime_config, "PATH_SYNTH_EXECUTORS", ref.source_root)
    trusted = (("initial-author", author.public_key()),)
    state_root = tmp_path / "service-state"
    store_root = state_root / STORE_RELATIVE
    publication = publish_signed_source(
        ref, expected_generation_id=None,
        trusted_publics=trusted, store_root=store_root,
    )
    monkeypatch.setattr(runtime_config, "PATH_USER_STATE", state_root)
    monkeypatch.setenv(
        "METNOS_INSTALL_ROOT",
        str(Path(reattestation_module.__file__).resolve().parents[1]),
    )

    from manifest_inventory import inventory_store_manifests

    stored_inventory = inventory_store_manifests(
        store_root=store_root, skill_enabled=lambda _name: True,
    )
    assert not stored_inventory.problems, stored_inventory.problems
    current = enumerate_authenticated_current_generations(
        trusted_publics=trusted, store_root=store_root,
    )
    assert len(current) == 1
    assert current[0].ref.manifest_relative == (
        "change_files_format/manifest.toml"
    )
    assert current[0].generation_id == publication.current_generation_id
    inventory = freeze_current_inventory_v1(current)

    claim = _claim()
    prepared = _prepare_transition_authority_set_v2(
        claim, distribution, previous,
    )
    _publish_prepared_authority_set_v2(prepared)
    _encoded, transition = issue_context_transition_v1(
        request_id=claim.request_id,
        closed_build_id=claim.closed_build_id,
        previous_cutover_id=None,
        previous_set_id=previous.set_id,
        previous_admission_context_id=(
            previous.prepared_admission_context_id
        ),
        previous_context_epoch=previous.prepared_context_epoch,
        set_id=prepared.target_set_id,
        prepared_admission_context_id=(
            prepared.target_admission_context_id
        ),
        prepared_context_epoch=prepared.target_context_epoch,
        context_material_sha256=prepared.target_context_material_sha256,
        set_json_sha256=prepared.target_set_json_sha256,
        current_inventory=inventory,
    )
    staged = _load_staged_reattestation_context_v1(
        transition, distribution, inventory,
    )

    class RefuseLegacyRunner:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("legacy property protocol was constructed")

    monkeypatch.setattr(
        reattestation_module, "ObservedPropertyRunner", RefuseLegacyRunner,
    )
    owner = (os.getuid(), os.getgid())
    effective = [(0, 0)]
    monkeypatch.setattr(
        receipts_module.os, "geteuid", lambda: effective[0][0],
    )
    monkeypatch.setattr(
        receipts_module.os, "getegid", lambda: effective[0][1],
    )

    @contextmanager
    def service_identity():
        assert effective[0] == (0, 0)
        effective[0] = owner
        try:
            yield
        finally:
            effective[0] = (0, 0)

    def enumerate_current():
        assert effective[0] == (0, 0)
        return enumerate_authenticated_current_generations(
            trusted_publics=trusted, store_root=store_root,
        )

    fixed_now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    inputs = dict(
        now=lambda: fixed_now,
        prove_quiescent=lambda: True,
        expected_inventory=inventory,
        enumerate_current=enumerate_current,
        identity_scope=service_identity,
        catalog_owner=owner,
    )
    proof = _build_staged_current_receipts_v2(staged, **inputs)
    repeated = _build_staged_current_receipts_v2(staged, **inputs)

    stored = tuple(store_root.glob(
        "*/admission-receipts-v2/*/*.json",
    ))
    assert len(stored) == 1
    receipt = verify_admission_receipt(
        stored[0].read_bytes(),
        verifier_keys=staged.authorities.admission.verifier_keys,
    )
    assert proof.inventory == inventory
    assert repeated == proof
    assert receipt.contract_id == current[0].ref.contract_id.value
    assert receipt.generation_id == current[0].generation_id
    assert receipt.check_results["properties"].status.value == "not_applicable"
    assert receipt.check_results[
        "initial_current_generation_adoption_v1"
    ].status.value == "passed"


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_publication_refuses_a_collision_without_moving_staging(
    tmp_path, monkeypatch,
):
    from executor_birth_secure_fs import _BirthObjectRole

    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    prepared = _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    )
    transaction = (
        base / "birth"
        / f".birth-provisioning-v2.txn.{prepared.transaction_id}"
    )
    layout = support.open_layout(monkeypatch, base)
    with layout.birth_session as session:
        with session.global_lock(exclusive=True, create=True):
            session.create_directory_exclusive(
                ("authority-sets", prepared.target_set_id),
                role=_BirthObjectRole.birth_integrity_only,
            )

    with pytest.raises(
        BirthProvisioningError,
        match="birth_provisioning_recovery_ambiguous",
    ):
        _publish_prepared_authority_set_v2(prepared)

    assert (transaction / "authority-set").is_dir()
    assert (base / "birth" / "authority-sets" / prepared.target_set_id).is_dir()


@pytest.mark.skipif(os.name == "nt", reason=support.POSIX_SCENARIO_ONLY_V1)
def test_v2_publication_requires_the_historical_marker_before_the_move(
    tmp_path, monkeypatch,
):
    base, previous, distribution = _transition_inputs(tmp_path, monkeypatch)
    prepared = _prepare_transition_authority_set_v2(
        _claim(), distribution, previous,
    )
    transaction = (
        base / "birth"
        / f".birth-provisioning-v2.txn.{prepared.transaction_id}"
    )
    (base / "birth" / "prepared-v1.json").unlink()

    with pytest.raises(
        BirthProvisioningError,
        match="birth_provisioning_recovery_ambiguous",
    ):
        _publish_prepared_authority_set_v2(prepared)

    assert (transaction / "authority-set").is_dir()
    assert not (
        base / "birth" / "authority-sets" / prepared.target_set_id
    ).exists()
