"""Portable checks for the nominal F4 context selection."""
from __future__ import annotations

from dataclasses import replace

import pytest

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
        current_proof=proof,
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
