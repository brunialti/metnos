"""Staged current-receipt preparation for an ownership transition."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

from executor_birth_cutover import CurrentReceiptProof
from executor_birth_ownership_coordinator import (
    OwnershipCoordinatorError,
    _wrapped_cause_detail_v1,
)


def _require_staged_receipt_inputs_v2(
    staged_runtime, prove_quiescent, expected_inventory,
    enumerate_current, identity_scope,
) -> None:
    from executor_birth_bootstrap import _is_staged_reattestation_runtime_v2
    from executor_birth_cutover import CurrentInventoryV1

    if (
        not _is_staged_reattestation_runtime_v2(staged_runtime)
        or not callable(prove_quiescent)
        or not callable(enumerate_current)
        or not callable(identity_scope)
        or not isinstance(expected_inventory, CurrentInventoryV1)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_birth_runtime_unavailable",
        )


class _StagedReceiptAccessV2:
    __slots__ = ("_runtime", "_scope", "_owner", "_prepared")

    def __init__(self, runtime, identity_scope, catalog_owner) -> None:
        self._runtime = runtime
        self._scope = identity_scope
        self._owner = catalog_owner
        self._prepared = {}

    def _staged(self, operation):
        return _staged_owner_call_v2(self._scope, self._owner, operation)

    def _prepared_for(self, current):
        identity = current.identity
        prepared = self._prepared.get(identity)
        if prepared is None:
            prepared = self._staged(lambda: self._runtime.prepare(current))
            self._prepared[identity] = prepared
        elif prepared.current != current:
            raise OwnershipCoordinatorError(
                "birth_ownership_recovery_required",
                "current inventory changed",
            )
        return prepared

    def read_receipt(self, current):
        prepared = self._prepared_for(current)
        return self._staged(lambda: self._runtime.read_receipt(prepared))

    def reattest(self, current):
        prepared = self._prepared_for(current)
        return self._staged(lambda: self._runtime.reattest(prepared))


def _require_expected_receipt_proof_v2(report, expected_inventory):
    if (
        not isinstance(report.proof, CurrentReceiptProof)
        or report.proof.inventory != expected_inventory
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_recovery_required", "current inventory changed",
        )
    return report.proof


def _prepare_staged_current_receipts_v2(
    staged_runtime: object, *, prove_quiescent: Callable[[], bool],
    expected_inventory: object, enumerate_current: object,
    identity_scope: object, catalog_owner: object,
) -> CurrentReceiptProof:
    """Build a V2-only receipt proof for one frozen transition inventory."""
    from executor_birth_cutover import (
        BirthCutoverError, prepare_current_receipt_proof,
    )

    _require_staged_receipt_inputs_v2(
        staged_runtime, prove_quiescent, expected_inventory,
        enumerate_current, identity_scope,
    )
    access = _StagedReceiptAccessV2(
        staged_runtime, identity_scope, catalog_owner,
    )
    try:
        report = prepare_current_receipt_proof(
            prove_quiescent=prove_quiescent,
            enumerate_current=enumerate_current,
            read_receipt=access.read_receipt,
            reattest_via_birth=access.reattest,
            verify_receipt=staged_runtime.verify_receipt,
        )
    except BirthCutoverError as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
            _wrapped_cause_detail_v1(exc),
        ) from exc
    except Exception as exc:
        raise OwnershipCoordinatorError(
            "birth_ownership_receipt_proof_invalid",
        ) from exc
    return _require_expected_receipt_proof_v2(report, expected_inventory)


def _build_staged_current_receipts_v2(
    staged_context: object, *, now: Callable[[], datetime],
    prove_quiescent: Callable[[], bool], expected_inventory: object,
    enumerate_current: object, identity_scope: object, catalog_owner: object,
) -> CurrentReceiptProof:
    """Own staged-runtime composition at the receipt-proof boundary."""
    from executor_birth_bootstrap import (
        _build_staged_reattestation_runtime_v2,
    )
    from executor_birth_distribution_manifest import authenticate_distribution_record_v1
    from executor_birth_prepared_root import load_previous_context_runtime_v1

    distribution = staged_context.selection.distribution
    # The caller has released its Birth session. Read the authenticated
    # predecessor once as administrator, before adopting the service identity.
    previous_context = None
    if distribution.release_sequence > 1:
        previous_context = load_previous_context_runtime_v1(
            authenticate_distribution_record_v1(distribution.encoded, distribution.signature),
        )

    staged_runtime = _staged_owner_call_v2(
        identity_scope,
        catalog_owner,
        lambda: _build_staged_reattestation_runtime_v2(
            staged_context, now=now, previous_context=previous_context,
        ),
    )
    return _prepare_staged_current_receipts_v2(
        staged_runtime,
        prove_quiescent=prove_quiescent,
        expected_inventory=expected_inventory,
        enumerate_current=enumerate_current,
        identity_scope=identity_scope,
        catalog_owner=catalog_owner,
    )


def _staged_owner_call_v2(
    identity_scope: object, catalog_owner: object,
    operation: Callable[[], object],
):
    """Run one staged filesystem effect as its signed service identity."""
    if (
        os.name != "posix" or not callable(identity_scope)
        or not callable(operation) or type(catalog_owner) is not tuple
        or len(catalog_owner) != 2
        or any(type(value) is not int or value <= 0 for value in catalog_owner)
        or (os.geteuid(), os.getegid()) != (0, 0)
    ):
        raise OwnershipCoordinatorError(
            "birth_ownership_birth_runtime_unavailable",
        )
    try:
        with identity_scope():
            if (os.geteuid(), os.getegid()) != catalog_owner:
                raise OwnershipCoordinatorError(
                    "birth_ownership_birth_runtime_unavailable",
                )
            return operation()
    finally:
        if (os.geteuid(), os.getegid()) != (0, 0):
            raise OwnershipCoordinatorError(
                "birth_ownership_birth_runtime_unavailable",
            )
