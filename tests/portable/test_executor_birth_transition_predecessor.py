"""Successor transition reads must follow the actual required-pointer side."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import executor_birth_distribution_manifest as manifest
import executor_birth_ownership_chain as chain_module
import executor_birth_prepared_root as prepared_root
import executor_birth_transition_gate as gate_module
from executor_birth_ownership_coordinator import OwnershipCoordinatorStateV1


@pytest.mark.parametrize("sequence", (1, 2))
def test_transition_snapshot_uses_explicit_historical_door_only_for_successor(
    monkeypatch, sequence,
):
    session, record, chain = object(), object(), object()
    distribution = SimpleNamespace(release_sequence=sequence, encoded=b"signed", signature=b"sig")
    calls = []
    monkeypatch.setattr(gate_module, "_require_deployment_lock_session_v1", lambda _: None)
    monkeypatch.setattr(gate_module, "_resolve_ownership_coordinator_locked_v2", lambda _: object())
    monkeypatch.setattr(gate_module, "_require_locked_coordinator_graph_snapshot_v2", lambda *_: object())
    monkeypatch.setattr(gate_module, "_transition_gate_edge_phase_v2", lambda *_: (object(), object(), None))
    monkeypatch.setattr(
        manifest, "authenticate_distribution_record_v1",
        lambda encoded, signature: calls.append("authenticate") or record
        if (encoded, signature) == (b"signed", b"sig") else pytest.fail("candidate changed"),
    )
    monkeypatch.setattr(
        chain_module, "inspect_ownership_chain_state_v1", lambda: calls.append("ordinary") or chain,
    )
    monkeypatch.setattr(
        chain_module, "inspect_transition_ownership_chain_v1",
        lambda candidate: calls.append("transition") or chain
        if candidate is record else pytest.fail("authenticated record lost"),
    )
    gate = gate_module._transition_gate_snapshot_locked_v2(session, distribution)
    observed = gate_module._require_transition_gate_snapshot_locked_v2(gate, session)
    assert observed.chain is chain
    assert calls == (["ordinary"] if sequence == 1 else ["authenticate", "transition"])


@pytest.mark.parametrize("phase", (
    OwnershipCoordinatorStateV1.BUILD_VERIFIED,
    OwnershipCoordinatorStateV1.HEAD_REQUIRED,
    OwnershipCoordinatorStateV1.PREFLIGHT_VERIFIED,
))
@pytest.mark.parametrize("advanced", (False, True))
def test_successor_enumerator_uses_current_authority_after_pointer_cas(
    monkeypatch, phase, advanced,
):
    import executor_birth_authority_gate as authority_gate
    import executor_birth_cutover as cutover

    session, gate, record = object(), object(), object()
    distribution = SimpleNamespace(release_sequence=2, encoded=b"signed", signature=b"sig")
    head_id = "sha256:" + ("2" if advanced else "1") * 64
    observation = SimpleNamespace(
        distribution=distribution, phase=SimpleNamespace(state=phase),
        chain=SimpleNamespace(required_head=SimpleNamespace(
            release_sequence=2 if advanced else 1, head_id=head_id,
        )),
    )
    calls, verifier, inventory = [], object(), object()
    selected = SimpleNamespace(
        required_head_id=head_id,
        authorities=SimpleNamespace(author=SimpleNamespace(verifier_keys={"selected": verifier})),
    )
    monkeypatch.setattr(gate_module, "_require_transition_gate_snapshot_locked_v2", lambda *_: observation)
    # Chain/phase acceptance has its own exhaustive matrix; test the authority routing here.
    monkeypatch.setattr(gate_module, "_transition_chain_authority_source_v2", lambda _: "required")
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(manifest, "authenticate_distribution_record_v1", lambda *_: record)
    monkeypatch.setattr(
        prepared_root, "load_required_context_runtime_v1",
        lambda: calls.append("current") or selected,
    )
    monkeypatch.setattr(
        prepared_root, "load_previous_context_runtime_v1",
        lambda candidate: calls.append("previous") or selected
        if candidate is record else pytest.fail("previous reader lacks authenticated candidate"),
    )
    monkeypatch.setattr(
        prepared_root, "_load_historical_transition_verifiers_v1",
        lambda: pytest.fail("successor selected the initial legacy authority"),
    )
    monkeypatch.setattr(
        cutover, "enumerate_authenticated_current_generations",
        lambda *, trusted_publics, store_root: inventory
        if trusted_publics == (("selected", verifier),) and store_root.is_absolute()
        else pytest.fail("receipt verifier ring changed"),
    )
    enumerator = gate_module._transition_current_enumerator_v2(gate, session)
    assert enumerator() is inventory
    assert calls == ["current" if advanced else "previous"]


def test_successor_enumerator_refuses_context_pointer_drift(monkeypatch):
    import executor_birth_authority_gate as authority_gate

    observed = SimpleNamespace(
        distribution=SimpleNamespace(release_sequence=2, encoded=b"signed", signature=b"sig"),
        chain=SimpleNamespace(required_head=SimpleNamespace(release_sequence=1, head_id="old")),
    )
    monkeypatch.setattr(gate_module, "_require_transition_gate_snapshot_locked_v2", lambda *_: observed)
    monkeypatch.setattr(gate_module, "_transition_chain_authority_source_v2", lambda _: "required")
    monkeypatch.setattr(authority_gate, "closed_build_enforcement", lambda: True)
    monkeypatch.setattr(manifest, "authenticate_distribution_record_v1", lambda *_: object())
    monkeypatch.setattr(
        prepared_root, "load_previous_context_runtime_v1", lambda _: SimpleNamespace(required_head_id="other"),
    )
    with pytest.raises(gate_module.OwnershipCoordinatorError, match="chain phase"):
        gate_module._transition_current_enumerator_v2(object(), object())
