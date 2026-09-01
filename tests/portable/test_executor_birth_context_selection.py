"""Portable checks for the nominal F4 context selection."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import executor_birth_prepared_set as prepared_module
from executor_birth_context_selection import (
    ContextSelectionError,
    ContextSelectionV1,
    _context_selection_for_staged_reattestation_v1,
    _context_selection_from_required_chain_v1,
    is_context_selection_v1,
)
from executor_birth_context_transition import issue_context_transition_v1
from executor_birth_cutover import CurrentReceiptProof
from executor_birth_distribution_manifest import _verified_distribution_for_test
from executor_birth_ownership_preflight import _sealed_build_identity_for_test
from executor_birth_prepared_set import (
    PREPARED_STATE_V1,
    PreparedSetError,
    PreparedSetV1,
)


def D(character: str) -> str:
    return "sha256:" + character * 64


def _evidence():
    proof = CurrentReceiptProof((), {})
    encoded, transition = issue_context_transition_v1(
        request_id=D("1"),
        closed_build_id=D("2"),
        previous_cutover_id=None,
        previous_set_id="3" * 64,
        previous_admission_context_id=D("4"),
        previous_context_epoch=D("5"),
        set_id="6" * 64,
        prepared_admission_context_id=D("7"),
        prepared_context_epoch=D("8"),
        context_material_sha256="9" * 64,
        set_json_sha256="a" * 64,
        current_inventory=proof.inventory,
    )
    assert encoded == transition.encoded
    prepared_values = dict(
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
        provisioner_build_id="c" * 64,
    )
    prepared = PreparedSetV1(
        **prepared_values,
        _artifact_binding=prepared_module._prepared_set_artifact_binding_v1(
            prepared_values,
        ),
        _seal=prepared_module._PREPARED_SET_SEAL_V1,
    )
    identity = _sealed_build_identity_for_test(D("2"), D("d"), "closed-v1")
    distribution = _verified_distribution_for_test(
        identity,
        previous_closed_build_id=None,
        release_sequence=1,
        encoded=b"distribution",
        signature=b"s" * 64,
    )
    return transition, prepared, distribution


def _prepared_with(prepared: PreparedSetV1, **changes) -> PreparedSetV1:
    values = {
        field: getattr(prepared, field)
        for field in prepared_module._PREPARED_SET_BINDING_FIELDS_V1
    }
    values.update(changes)
    return PreparedSetV1(
        **values,
        _artifact_binding=prepared_module._prepared_set_artifact_binding_v1(values),
        _seal=prepared_module._PREPARED_SET_SEAL_V1,
    )


def test_staged_selection_builds_only_a_context_bound_reattestation(
    monkeypatch, tmp_path,
):
    import contract_store
    import executor_birth_producer_store as producer_store
    from executor_birth_bootstrap import (
        _CutoverReattestationFactoryV2, _ProducerAuthority,
        _REATTESTATION_FACTORY_TOKEN,
        _STAGED_REATTESTATION_RUNTIME_TOKEN_V2,
        _StagedReattestationRuntimeV2,
        _is_staged_reattestation_runtime_v2,
    )
    from executor_birth_commit_publisher import (
        _BirthCommitPublisher, _PUBLISHER_TOKEN,
    )
    import executor_birth_cutover as cutover_module
    from executor_birth_cutover import (
        CurrentGeneration, CurrentInventoryV1, CurrentReceiptProof,
    )
    from executor_birth_identity import RevisionAuthor
    from executor_birth_intent import _INSTALLER
    from executor_birth_operational import _assemble_birth_core
    from executor_birth_receipts import IssuerRegistry
    from executor_birth_shadow import _sealed_dependencies_for_test
    from executor_birth_ownership_coordinator import (
        OwnershipCoordinatorError, _prepare_staged_current_receipts_v2,
    )
    from manifest_inventory import (
        ContractId, ManifestOrigin, ManifestRef, ManifestStatus,
    )

    transition, prepared, distribution = _evidence()
    selection = _context_selection_for_staged_reattestation_v1(
        transition, prepared, distribution,
    )
    author = Ed25519PrivateKey.generate()
    admission = Ed25519PrivateKey.generate()
    publisher = _BirthCommitPublisher(
        _PUBLISHER_TOKEN,
        author_private=author,
        author_ring=(("author", author.public_key()),),
        admission_private=admission,
        admission_key_id="admission",
        admission_verifiers={"admission": admission.public_key()},
        prepared_admission_context_id=selection.admission_context_id,
        prepared_context_epoch=selection.context_epoch,
        primitive=lambda *_args, **_kwargs: None,
        store_root=tmp_path / "store",
        registry_reconciler=lambda _revision: None,
    )
    key = Ed25519PrivateKey.generate()
    authority = _ProducerAuthority(
        _INSTALLER, "installer", "producer", key, RevisionAuthor.MODEL,
    )
    contract_id = ContractId(ManifestOrigin.EXPLICIT, "alpha/manifest.toml")
    manifest_path = tmp_path / "alpha" / "manifest.toml"
    ref = ManifestRef(
        contract_id, ManifestOrigin.EXPLICIT, ManifestStatus.ADMITTED,
        tmp_path, manifest_path, "alpha/manifest.toml", (manifest_path.parent,),
    )
    current = CurrentGeneration(ref, D("d"))
    snapshot = SimpleNamespace(
        manifest_bytes=b"manifest", language_state_bytes=b"language",
        code_files={"executor.py": b"code"},
    )
    captures = []

    def capture(*_args, **_kwargs):
        if captures:
            raise AssertionError("prepared reattestation recaptured its source")
        captures.append(snapshot)
        return snapshot

    monkeypatch.setattr(
        contract_store, "acquire_current_reattestation_snapshot", capture,
    )
    observed = {}

    def issue_v2(**kwargs):
        observed.update(kwargs)
        return b"producer-receipt"

    monkeypatch.setattr(
        producer_store, "get_or_issue_and_claim_producer_receipt_v2", issue_v2,
    )
    factory = _CutoverReattestationFactoryV2(
        _REATTESTATION_FACTORY_TOKEN,
        selection=selection,
        port=publisher.reattestation_port(),
        authority=authority,
        registry=IssuerRegistry({}),
        db_path=Path(tmp_path / "producer.sqlite"),
        ttl_seconds=300,
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    core = _assemble_birth_core(
        producer_registry=IssuerRegistry({}),
        producer_db=Path(tmp_path / "producer.sqlite"),
        context_resolver=lambda _request: None,
        context_epoch_resolver=lambda: selection.context_epoch,
        approval_resolver=lambda *_args: (None, None),
        shadow_dependencies=_sealed_dependencies_for_test(),
        admission_private_key=admission,
        admission_verifier_keys={"admission": admission.public_key()},
        admission_key_id="admission",
        policy_version="birth-policy-v1",
        now=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
        commit_publisher=publisher,
        postcondition_verifier=lambda *_args: None,
    )
    runtime = _StagedReattestationRuntimeV2(
        _STAGED_REATTESTATION_RUNTIME_TOKEN_V2,
        core=core,
        factory=factory,
    )
    assert _is_staged_reattestation_runtime_v2(runtime)
    assert runtime.transition_id == selection.transition_id
    assert not hasattr(runtime, "producer_factories")

    prepared_request = factory.prepare(current)
    preview = prepared_request.producer_request
    assert observed == {}
    request = factory(prepared_request)
    assert preview == request.producer_request
    assert captures == [snapshot]
    assert request.producer_request is observed["request"]
    assert request.request_id == request.producer_request.request_id
    assert request.producer_binding.objective_hash == (
        request.producer_request.objective_hash
    )
    assert request.producer_binding.candidate_source_id == (
        request.producer_request.candidate_source_id
    )
    assert request.producer_request.transition_id == selection.transition_id

    proof = CurrentReceiptProof(
        (current.identity,), {current.identity: D("e")},
    )
    callbacks = {}
    handles = []
    prepared_handle = SimpleNamespace(current=current)

    def prepare_once(_runtime, observed_current):
        assert observed_current == current
        handles.append(prepared_handle)
        return prepared_handle

    monkeypatch.setattr(type(runtime), "prepare", prepare_once)
    monkeypatch.setattr(
        type(runtime), "read_receipt",
        lambda _runtime, handle: b"receipt" if handle is prepared_handle else None,
    )
    monkeypatch.setattr(
        type(runtime), "reattest",
        lambda _runtime, handle: b"receipt" if handle is prepared_handle else b"",
    )

    def prepare_proof(**kwargs):
        callbacks.update(kwargs)
        assert kwargs["read_receipt"](current) == b"receipt"
        assert kwargs["reattest_via_birth"](current) == b"receipt"
        assert kwargs["read_receipt"](current) == b"receipt"
        return SimpleNamespace(proof=proof)

    monkeypatch.setattr(
        cutover_module, "prepare_current_receipt_proof", prepare_proof,
    )
    assert _prepare_staged_current_receipts_v2(
        runtime, prove_quiescent=lambda: True,
        expected_inventory=CurrentInventoryV1((current.identity,)),
    ) is proof
    assert handles == [prepared_handle]
    for name in ("enumerate_current", "verify_receipt"):
        assert callbacks[name].__self__ is runtime
    assert callable(callbacks["read_receipt"])
    assert callable(callbacks["reattest_via_birth"])
    with pytest.raises(
        OwnershipCoordinatorError, match="birth_ownership_recovery_required",
    ):
        _prepare_staged_current_receipts_v2(
            runtime,
            prove_quiescent=lambda: True,
            expected_inventory=CurrentInventoryV1(()),
        )

    def unavailable(**_kwargs):
        raise cutover_module.BirthCutoverError(
            "birth_cutover_not_quiescent", current.identity[0],
        )

    monkeypatch.setattr(
        cutover_module, "prepare_current_receipt_proof", unavailable,
    )
    with pytest.raises(OwnershipCoordinatorError) as failure:
        _prepare_staged_current_receipts_v2(
            runtime,
            prove_quiescent=lambda: False,
            expected_inventory=CurrentInventoryV1((current.identity,)),
        )
    assert failure.value.code == "birth_ownership_receipt_proof_invalid"
    assert failure.value.detail == (
        f"birth_cutover_not_quiescent: {current.identity[0]}"
    )


def test_required_and_staged_producers_preserve_scope():
    transition, prepared, distribution = _evidence()
    required = _context_selection_from_required_chain_v1(
        transition,
        prepared,
        distribution,
    )
    staged = _context_selection_for_staged_reattestation_v1(
        transition,
        prepared,
        distribution,
    )

    assert required.transition_id == transition.transition_id
    assert required.set_id == transition.set_id
    assert required.admission_context_id == transition.prepared_admission_context_id
    assert required.context_epoch == transition.prepared_context_epoch
    assert required.distribution is distribution
    assert not required.staged_reattestation_only
    assert staged.staged_reattestation_only
    assert is_context_selection_v1(required)
    assert not is_context_selection_v1(staged)
    assert is_context_selection_v1(staged, allow_staged=True)


def test_verified_distribution_context_opens_only_its_runtime_tree(monkeypatch):
    import executor_birth_prepared_root as prepared_root
    import executor_birth_secure_fs as secure_fs

    _transition, _prepared, distribution = _evidence()
    sentinel = object()
    observed = {}

    def open_root(path, *, exact_private):
        observed["path"] = path
        observed["exact_private"] = exact_private
        return sentinel

    monkeypatch.setattr(secure_fs, "_open_legacy_root_session", open_root)
    assert prepared_root._open_distribution_sources_for_verified_v1(
        distribution,
    ) is sentinel
    assert observed == {
        "path": Path(distribution.installation_root) / "runtime",
        "exact_private": False,
    }


def test_staged_runtime_reverifies_inventory_and_cannot_become_required(
    monkeypatch,
):
    import executor_birth_prepared_root as prepared_root
    from executor_birth_cutover import CurrentInventoryV1
    from executor_birth_prepared_root import (
        PreparedRootError, SealedAuthoritiesV1,
        StagedReattestationContextV1,
        _load_staged_reattestation_context_v1,
    )

    transition, prepared, distribution = _evidence()
    authorities = SealedAuthoritiesV1(
        prepared=prepared,
        author=object(),
        admission=object(),
        producers={},
        approval=object(),
        semantic=object(),
        sandbox=None,
        context_epoch=prepared.prepared_context_epoch,
        material=object(),
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def global_lock(self, **_kwargs):
            return nullcontext()

    monkeypatch.setattr(
        prepared_root, "open_prepared_root_session_v1", lambda: Session(),
    )
    monkeypatch.setattr(
        prepared_module, "load_authority_set_v1",
        lambda *_args, **_kwargs: prepared,
    )
    monkeypatch.setattr(
        prepared_root, "_load_sealed_authorities_from_set_v1",
        lambda *_args, **_kwargs: authorities,
    )

    staged = _load_staged_reattestation_context_v1(
        transition, distribution, CurrentInventoryV1(()),
    )
    assert isinstance(staged, StagedReattestationContextV1)
    assert staged.selection.staged_reattestation_only

    required = _context_selection_from_required_chain_v1(
        transition, prepared, distribution,
    )
    with pytest.raises(
        PreparedRootError, match="birth_context_selection_invalid",
    ):
        StagedReattestationContextV1(required, authorities)

    with pytest.raises(
        PreparedRootError, match="birth_context_transition_binding_invalid",
    ):
        _load_staged_reattestation_context_v1(
            transition,
            distribution,
            CurrentInventoryV1((("explicit:alpha/manifest.toml", D("e")),)),
        )


def test_direct_construction_cannot_create_a_selection():
    transition, _prepared, distribution = _evidence()
    with pytest.raises(ContextSelectionError, match="birth_context_selection_invalid"):
        ContextSelectionV1(
            transition.transition_id,
            transition.set_id,
            transition.prepared_admission_context_id,
            transition.prepared_context_epoch,
            distribution,
            object(),
            object(),
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda transition, prepared, distribution: (
            replace(transition, set_id="f" * 64), prepared, distribution
        ),
        lambda transition, prepared, distribution: (
            transition,
            _prepared_with(prepared, prepared_context_epoch=D("f")),
            distribution,
        ),
        lambda transition, prepared, distribution: (
            transition,
            prepared,
            _verified_distribution_for_test(
                _sealed_build_identity_for_test(D("f"), D("d"), "closed-v1"),
                previous_closed_build_id=None,
                release_sequence=1,
                encoded=b"other",
                signature=b"o" * 64,
            ),
        ),
    ],
)
def test_transition_set_and_distribution_must_agree(change):
    transition, prepared, distribution = change(*_evidence())
    with pytest.raises(ContextSelectionError):
        _context_selection_from_required_chain_v1(
            transition,
            prepared,
            distribution,
        )


def test_reconstructed_transition_object_is_not_accepted_as_verified():
    transition, prepared, distribution = _evidence()
    reconstructed = replace(
        transition,
        request_id=D("f"),
    )
    with pytest.raises(
        ContextSelectionError,
        match="birth_context_selection_invalid",
    ):
        _context_selection_from_required_chain_v1(
            reconstructed,
            prepared,
            distribution,
        )


def test_copied_prepared_seal_does_not_authorize_changed_readback_fields():
    _transition, prepared, _distribution = _evidence()
    with pytest.raises(PreparedSetError, match="birth_prepared_set_invalid"):
        replace(prepared, prepared_context_epoch=D("f"))
