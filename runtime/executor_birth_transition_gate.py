"""Nominal transition gate, authenticated census, and maintenance freeze."""
from __future__ import annotations

import os
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from executor_birth_ownership_coordinator import (
    OwnershipCoordinatorError,
    _require_deployment_lock_session_v1,
    _require_locked_coordinator_graph_snapshot_v2,
    _resolve_ownership_coordinator_locked_v2,
    _transition_edge_from_graph_v2,
)
from executor_birth_transition_chain_policy import (
    _transition_chain_authority_source_v2,
)


def _transition_gate_edge_phase_v2(graph, distribution):
    """Return the terminal edge and its exact current transaction phase."""
    claim, predecessor = _transition_edge_from_graph_v2(graph, distribution)
    matches = tuple(
        item for item in graph.transactions if item.claim == claim
    )
    if not matches:
        return claim, predecessor, None
    if len(matches) != 1 or matches[0] is not graph.transactions[-1]:
        raise OwnershipCoordinatorError("birth_ownership_request_conflict")
    return claim, predecessor, matches[0].latest


@dataclass(frozen=True, slots=True)
class _TransitionGateObservationV2:
    distribution: object
    claim: object
    predecessor: object
    phase: object
    chain: object
    partial: bool


class _TransitionGateSnapshotV2:
    """Opaque phase/chain observation bound to one live deployment lock."""

    __slots__ = ("_token", "__weakref__")

    def __init__(self, token: object) -> None:
        if type(token) is not object:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "gate authority",
            )
        object.__setattr__(self, "_token", token)

    def __copy__(self):
        raise TypeError("transition gate snapshots cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("transition gate snapshots cannot be copied")

    def __reduce__(self):
        raise TypeError("transition gate snapshots cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("transition gate snapshots cannot be serialized")


def _build_transition_gate_registry_v2():
    issued = weakref.WeakKeyDictionary()

    def issue(session, observation):
        _require_deployment_lock_session_v1(session)
        token = object()
        snapshot = _TransitionGateSnapshotV2(token)
        issued[snapshot] = (token, session, observation)
        return snapshot

    def require(snapshot, session):
        _require_deployment_lock_session_v1(session)
        try:
            registration = issued.get(snapshot)
        except (AttributeError, TypeError) as exc:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "gate authority",
            ) from exc
        if (
            type(snapshot) is not _TransitionGateSnapshotV2
            or registration is None
            or snapshot._token is not registration[0]
            or registration[1] is not session
        ):
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "gate authority",
            )
        _require_deployment_lock_session_v1(session)
        return registration[2]

    return issue, require


(
    _issue_transition_gate_snapshot_locked_v2,
    _require_transition_gate_snapshot_locked_v2,
) = _build_transition_gate_registry_v2()
del _build_transition_gate_registry_v2


def _transition_gate_snapshot_locked_v2(session, distribution):
    """Observe the coordinator phase and chain under one exact outer lock."""
    from executor_birth_ownership_chain import (
        OwnershipChainError, inspect_ownership_chain_state_v1,
        inspect_transition_ownership_chain_v1,
    )

    snapshot = _resolve_ownership_coordinator_locked_v2(session)
    graph = _require_locked_coordinator_graph_snapshot_v2(snapshot, session)
    claim, predecessor, phase = _transition_gate_edge_phase_v2(
        graph, distribution,
    )
    chain, partial = None, False
    try:
        if distribution.release_sequence > 1:
            from executor_birth_distribution_manifest import authenticate_distribution_record_v1

            chain = inspect_transition_ownership_chain_v1(
                authenticate_distribution_record_v1(distribution.encoded, distribution.signature),
            )
        else:
            chain = inspect_ownership_chain_state_v1()
    except OwnershipChainError as exc:
        if not (
            exc.code == "birth_ownership_recovery_required"
            and exc.detail == "partial chain"
        ):
            raise
        partial = True
    _require_deployment_lock_session_v1(session)
    return _issue_transition_gate_snapshot_locked_v2(
        session,
        _TransitionGateObservationV2(
            distribution, claim, predecessor, phase, chain, partial,
        ),
    )


def _transition_gate_phase_locked_v2(gate, session):
    """Return the phase only while the issuing deployment lock is live."""
    return _require_transition_gate_snapshot_locked_v2(gate, session).phase


class _TransitionCurrentEnumeratorV2:
    """Fresh structural census bound to one authenticated transition gate."""

    __slots__ = ("_token", "__weakref__")

    def __init__(self, token: object) -> None:
        if type(token) is not object:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "current enumerator",
            )
        object.__setattr__(self, "_token", token)

    def __call__(self):
        return _invoke_transition_current_enumerator_v2(self)

    def __copy__(self):
        raise TypeError("transition current enumerators cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("transition current enumerators cannot be copied")

    def __reduce__(self):
        raise TypeError("transition current enumerators cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("transition current enumerators cannot be serialized")


def _current_enumerator_registration_v2(issued, value):
    try:
        record = issued.get(value)
    except (AttributeError, TypeError) as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current enumerator",
        ) from exc
    if (
        type(value) is not _TransitionCurrentEnumeratorV2
        or record is None or value._token is not record[0]
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current enumerator",
        )
    return record


def _issue_transition_current_enumerator_core_v2(
    issued, *, gate, session, distribution, trusted_publics, store_root,
):
    observed = _require_transition_gate_snapshot_locked_v2(gate, session)
    if (
        observed.distribution != distribution
        or type(trusted_publics) is not tuple or not trusted_publics
        or not isinstance(store_root, Path) or not store_root.is_absolute()
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current enumerator",
        )
    token = object()
    value = _TransitionCurrentEnumeratorV2(token)
    issued[value] = (
        token, gate, session, distribution, trusted_publics, store_root,
    )
    return value


def _require_transition_current_enumerator_core_v2(
    issued, value, session, distribution,
):
    record = _current_enumerator_registration_v2(issued, value)
    if record[2] is not session or record[3] != distribution:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current enumerator",
        )
    observed = _require_transition_gate_snapshot_locked_v2(record[1], session)
    if observed.distribution != distribution:
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current enumerator",
        )
    return value


def _invoke_transition_current_enumerator_core_v2(issued, value):
    record = _current_enumerator_registration_v2(issued, value)
    _token, gate, session, distribution, trusted_publics, store_root = record
    _require_transition_current_enumerator_core_v2(
        issued, value, session, distribution,
    )
    from executor_birth_cutover import (
        enumerate_authenticated_current_generations,
    )

    return enumerate_authenticated_current_generations(
        trusted_publics=trusted_publics,
        store_root=store_root,
    )


def _transition_current_trusted_publics_core_v2(
    issued, value, session, distribution,
):
    """Return only the verifier ring sealed into this issued read port."""
    _require_transition_current_enumerator_core_v2(
        issued, value, session, distribution,
    )
    return _current_enumerator_registration_v2(issued, value)[4]


def _build_transition_current_enumerator_registry_v2():
    issued = weakref.WeakKeyDictionary()

    def issue(**values):
        return _issue_transition_current_enumerator_core_v2(issued, **values)

    def require(value, session, distribution):
        return _require_transition_current_enumerator_core_v2(
            issued, value, session, distribution,
        )

    def invoke(value):
        return _invoke_transition_current_enumerator_core_v2(issued, value)

    def trusted_publics(value, session, distribution):
        return _transition_current_trusted_publics_core_v2(
            issued, value, session, distribution,
        )

    return issue, require, invoke, trusted_publics


(
    _issue_transition_current_enumerator_v2,
    _require_transition_current_enumerator_v2,
    _invoke_transition_current_enumerator_v2,
    _transition_current_trusted_publics_v2,
) = _build_transition_current_enumerator_registry_v2()
del _build_transition_current_enumerator_registry_v2


def _transition_current_enumerator_v2(gate, session):
    """Select the authenticated read port valid on this side of the gate."""
    from executor_birth_authority_gate import closed_build_enforcement
    from executor_birth_prepared_root import (
        _load_historical_transition_verifiers_v1,
    )

    observed = _require_transition_gate_snapshot_locked_v2(gate, session)
    authority_source = _transition_chain_authority_source_v2(observed)
    sealed = None
    if authority_source == "required":
        from executor_birth_prepared_root import (
            load_previous_context_runtime_v1, load_required_context_runtime_v1,
        )

        if (
            observed.distribution.release_sequence > 1
            and observed.chain.required_head.release_sequence
            < observed.distribution.release_sequence
        ):
            from executor_birth_distribution_manifest import authenticate_distribution_record_v1

            required = load_previous_context_runtime_v1(
                authenticate_distribution_record_v1(
                    observed.distribution.encoded, observed.distribution.signature,
                ),
            )
        else:
            required = load_required_context_runtime_v1()
        if required.required_head_id != observed.chain.required_head.head_id:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required", "chain phase",
            )
        sealed = required.authorities
    if closed_build_enforcement() is not True:
        raise OwnershipCoordinatorError(
            "birth_ownership_birth_runtime_unavailable",
        )
    from contract_store import _store_root

    if authority_source == "historical":
        historical = _load_historical_transition_verifiers_v1()
        trusted = tuple(sorted(historical.author_verifier_keys.items()))
    else:
        trusted = tuple(sorted(sealed.author.verifier_keys.items()))
    return _issue_transition_current_enumerator_v2(
        gate=gate,
        session=session,
        distribution=observed.distribution,
        trusted_publics=trusted,
        store_root=Path(os.path.abspath(_store_root(None))),
    )


def _freeze_transition_inventory_v2(
    gate, session, enumerate_current, maintenance, evidence,
    catalog_trusted_owner,
):
    from contract_cutover_guard import (
        _maintenance_evidence_under_transition_v1,
        _verify_store_only_catalog_locked,
    )
    from executor_birth_cutover import freeze_current_inventory_v1
    from executor_birth_ownership_preflight import canonical_maintenance_proof

    observed = _require_transition_gate_snapshot_locked_v2(gate, session)
    enumerate_current = _require_transition_current_enumerator_v2(
        enumerate_current, session, observed.distribution,
    )
    trusted_publics = _transition_current_trusted_publics_v2(
        enumerate_current, session, observed.distribution,
    )
    initial = _maintenance_evidence_under_transition_v1(maintenance)
    supplied = canonical_maintenance_proof(
        source=evidence["source"], units=evidence["units"],
    )
    if supplied != initial or maintenance() is not True:
        raise OwnershipCoordinatorError(
            "birth_ownership_maintenance_changed",
        )
    inventory = freeze_current_inventory_v1(enumerate_current())
    _verify_store_only_catalog_locked(
        catalog_trusted_owner=catalog_trusted_owner,
        trusted_publics=trusted_publics,
    )
    if (
        _maintenance_evidence_under_transition_v1(maintenance) != initial
        or maintenance() is not True
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_maintenance_changed",
        )
    return enumerate_current, initial, inventory, trusted_publics


def _require_transition_inventory_unchanged_v2(
    enumerate_current, maintenance, initial, inventory,
    catalog_trusted_owner, trusted_publics,
) -> None:
    from contract_cutover_guard import (
        _maintenance_evidence_under_transition_v1,
        _verify_store_only_catalog_locked,
    )
    from executor_birth_cutover import freeze_current_inventory_v1

    _verify_store_only_catalog_locked(
        catalog_trusted_owner=catalog_trusted_owner,
        trusted_publics=trusted_publics,
    )
    final_inventory = freeze_current_inventory_v1(enumerate_current())
    if (
        final_inventory != inventory
        or _maintenance_evidence_under_transition_v1(maintenance) != initial
        or maintenance() is not True
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required",
            "current inventory or maintenance changed",
        )


@contextmanager
def _transition_inventory_under_maintenance_v2(
    gate, session, enumerate_current, maintenance, evidence, *,
    catalog_trusted_owner,
):
    """Freeze exact current identities under an already held maintenance guard."""
    enumerate_current, initial, inventory, trusted_publics = (
        _freeze_transition_inventory_v2(
            gate, session, enumerate_current, maintenance, evidence,
            catalog_trusted_owner,
        )
    )
    try:
        yield maintenance, inventory, initial, enumerate_current
    except BaseException:
        raise
    else:
        _require_transition_inventory_unchanged_v2(
            enumerate_current, maintenance, initial, inventory,
            catalog_trusted_owner, trusted_publics,
        )
