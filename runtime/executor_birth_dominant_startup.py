#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Private completion capability for the dominant startup installation.

Group 7 crosses the certificate boundary in ONE call that never releases the
deployment, exclusive-startup and maintenance locks it already holds. This
module owns the capability that authorises that crossing, and nothing else: it
does not install a topology, does not neutralise a legacy binding and does not
touch the coordinator journal.

Keeping the authority here, alone, is what makes the crossing provable. A
caller cannot obtain it by holding a path, a digest or a state name: the type
is the credential, it is minted only from a complete and self-consistent set of
bindings, and it is consumed exactly once. The productive graph does not reach
this module until the wrapper that mints it exists, which is deliberate — G6
proved the same shape for the publication core before any installer used it.
"""
from __future__ import annotations

import hashlib
import threading
import weakref
from dataclasses import dataclass, field


DOMINANT_STARTUP_DOMAIN_V1 = b"metnos.executor-birth.dominant-startup/v1\0"
DOMINANT_STARTUP_RECEIPT_DOMAIN_V1 = (
    b"metnos.executor-birth.dominant-startup-receipt/v1\0"
)

_CAPABILITY_SEAL_V1 = object()
_TEST_CAPABILITY_SEAL_V1 = object()
_RECEIPT_SEAL_V1 = object()
_CONSUMED_GUARD_V1 = threading.Lock()
_CONSUMED_CAPABILITIES_V1: weakref.WeakSet[object] = weakref.WeakSet()


class DominantStartupError(RuntimeError):
    """One stable denial class; detail never reaches an operator stream."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


def _invalid(code: str, detail: str = "") -> DominantStartupError:
    return DominantStartupError(code, detail)


def _require_digest_v1(value: object, field: str) -> str:
    if (
        type(value) is not str or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise _invalid("dominant_startup_binding_invalid", field)
    return value


@dataclass(frozen=True, slots=True)
class DominantStartupBindingsV1:
    """Everything the crossing is bound to, named once and never re-derived.

    Each field is an identity the wrapper has already OBSERVED, not a promise
    about what it will observe. Recomputing any of them after the capability
    exists would let a drift slip between the check and the use, which is the
    failure this whole boundary exists to prevent.
    """

    request_id: str
    previous_head_digest: str
    context_transition_id: str
    catalog_id: str
    effective_topology_hash: str
    enforcement_evidence_digest: str

    def __post_init__(self) -> None:
        for field in (
            "request_id", "previous_head_digest", "context_transition_id",
            "catalog_id",
            "effective_topology_hash", "enforcement_evidence_digest",
        ):
            _require_digest_v1(getattr(self, field), field)


def bindings_digest_v1(bindings: DominantStartupBindingsV1) -> str:
    """Frame the bindings so no field can slide into its neighbour."""
    if type(bindings) is not DominantStartupBindingsV1:
        raise _invalid("dominant_startup_binding_invalid", "type")
    digest = hashlib.sha256(DOMINANT_STARTUP_DOMAIN_V1)
    for field in (
        bindings.request_id, bindings.previous_head_digest,
        bindings.context_transition_id, bindings.catalog_id,
        bindings.effective_topology_hash,
        bindings.enforcement_evidence_digest,
    ):
        encoded = field.encode("ascii")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


class _DominantStartupInstalledV1:
    """Non-transferable, single-use authority to cross the certificate boundary.

    Neither copyable nor serialisable: a capability that survived a pickle or a
    fork would authorise a crossing whose locks are no longer held by the
    process performing it.
    """

    __slots__ = (
        "_bindings", "_digest", "_sessions", "_seal", "__weakref__",
    )

    def __init__(
        self, bindings: object, sessions: object, seal: object,
    ) -> None:
        if seal is not _CAPABILITY_SEAL_V1:
            raise _invalid("dominant_startup_capability_invalid", "seal")
        self._bindings = bindings
        self._digest = bindings_digest_v1(bindings)
        self._sessions = sessions
        self._seal = seal

    def __copy__(self):
        raise TypeError("dominant startup capabilities cannot be copied")

    def __deepcopy__(self, _memo):
        raise TypeError("dominant startup capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("dominant startup capabilities cannot be serialized")

    def __reduce_ex__(self, _protocol):
        raise TypeError("dominant startup capabilities cannot be serialized")


class _TestOnlyDominantStartupCapabilityV1(_DominantStartupInstalledV1):
    """Nominally distinct capability; the productive graph never mints it."""

    __slots__ = ()

    def __init__(self, bindings: object, sessions: object, seal: object) -> None:
        if seal is not _TEST_CAPABILITY_SEAL_V1:
            raise _invalid("dominant_startup_capability_invalid", "seal")
        super().__init__(bindings, sessions, _CAPABILITY_SEAL_V1)


def _require_live_sessions_v1(sessions: object) -> tuple[object, ...]:
    """The three locks must be HELD, and held by distinct live sessions.

    The wrapper acquires deployment, exclusive startup and maintenance before
    it observes anything. Passing the same session three times, or a session
    that is merely shaped like one, would satisfy a count but not the property:
    that no other actor can move the ground under the crossing.
    """
    if type(sessions) is not tuple or len(sessions) != 3:
        raise _invalid("dominant_startup_sessions_invalid", "cardinality")
    if any(session is None for session in sessions):
        raise _invalid("dominant_startup_sessions_invalid", "absent")
    identities = {id(session) for session in sessions}
    if len(identities) != 3:
        raise _invalid("dominant_startup_sessions_invalid", "aliased")
    for session in sessions:
        if type(session).__reduce__ is object.__reduce__:
            # A session that can be serialised is not a lock session: the
            # product's own sessions all refuse it, and accepting one here
            # would accept a forgery that survived a process boundary.
            raise _invalid("dominant_startup_sessions_invalid", "transferable")
    return sessions


def mint_for_test_v1(
    bindings: DominantStartupBindingsV1, sessions: tuple[object, ...],
) -> _TestOnlyDominantStartupCapabilityV1:
    """Mint through a seam no productive caller can reach."""
    return _TestOnlyDominantStartupCapabilityV1(
        bindings, _require_live_sessions_v1(sessions), _TEST_CAPABILITY_SEAL_V1,
    )


def consume_v1(
    capability: _DominantStartupInstalledV1,
    expected: DominantStartupBindingsV1,
) -> str:
    """Consume the capability once, against the bindings the caller re-read.

    The caller passes what it has just OBSERVED again; this compares it with
    what the capability was minted from. A mismatch means the ground moved
    between mint and use, and the crossing must not happen.
    """
    if not isinstance(capability, _DominantStartupInstalledV1):
        raise _invalid("dominant_startup_capability_invalid", "type")
    if capability._digest != bindings_digest_v1(expected):
        raise _invalid("dominant_startup_binding_drift")
    _require_live_sessions_v1(capability._sessions)
    with _CONSUMED_GUARD_V1:
        if capability in _CONSUMED_CAPABILITIES_V1:
            raise _invalid("dominant_startup_capability_spent")
        _CONSUMED_CAPABILITIES_V1.add(capability)
    return capability._digest


__all__ = [
    "DOMINANT_STARTUP_DOMAIN_V1",
    "DOMINANT_STARTUP_RECEIPT_DOMAIN_V1",
    "DominantStartupBindingsV1",
    "DominantStartupError",
    "bindings_digest_v1",
    "dominant_startup_receipt_v1",
]


@dataclass(frozen=True, slots=True)
class DominantStartupReceiptV1:
    """What the crossing actually consumed, re-read and agreed twice."""

    bindings: DominantStartupBindingsV1
    bindings_digest: str
    retirement_plan_digest: str
    enforcement_evidence_digest: str
    dominant_startup_receipt: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._seal is not _RECEIPT_SEAL_V1
            or type(self.bindings) is not DominantStartupBindingsV1
            or self.bindings_digest != bindings_digest_v1(self.bindings)
            or self.enforcement_evidence_digest
            != self.bindings.enforcement_evidence_digest
            or self.dominant_startup_receipt != dominant_startup_receipt_v1(
                self.bindings_digest,
                self.retirement_plan_digest,
                self.enforcement_evidence_digest,
            )
        ):
            raise _invalid("dominant_startup_receipt_invalid")


def dominant_startup_receipt_v1(
    bindings_digest: str,
    retirement_plan_digest: str,
    enforcement_evidence_digest: str,
) -> str:
    """Frame the three complete crossing facts in their required order."""
    values = (
        _require_digest_v1(bindings_digest, "bindings_digest"),
        _require_digest_v1(
            retirement_plan_digest,
            "retirement_plan_digest",
        ),
        _require_digest_v1(
            enforcement_evidence_digest,
            "enforcement_evidence_digest",
        ),
    )
    digest = hashlib.sha256(DOMINANT_STARTUP_RECEIPT_DOMAIN_V1)
    for value in values:
        encoded = value.encode("ascii")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def is_dominant_startup_receipt_v1(value: object) -> bool:
    """Recognize only receipts minted after the private capability was spent."""
    if type(value) is not DominantStartupReceiptV1:
        return False
    try:
        return (
            value._seal is _RECEIPT_SEAL_V1
            and type(value.bindings) is DominantStartupBindingsV1
            and value.bindings_digest == bindings_digest_v1(value.bindings)
            and value.enforcement_evidence_digest
            == value.bindings.enforcement_evidence_digest
            and value.dominant_startup_receipt
            == dominant_startup_receipt_v1(
                value.bindings_digest,
                value.retirement_plan_digest,
                value.enforcement_evidence_digest,
            )
        )
    except (AttributeError, DominantStartupError):
        return False


def _require_product_sessions_v1(sessions: object) -> tuple[object, ...]:
    held = _require_live_sessions_v1(sessions)
    from contract_cutover_guard import _require_maintenance_session_v1
    from executor_birth_ownership_coordinator import (
        _require_deployment_lock_session_v1,
    )
    from executor_birth_startup_gate import (
        _require_exclusive_startup_gate_session_v1,
    )

    try:
        _require_deployment_lock_session_v1(held[0])
        _require_exclusive_startup_gate_session_v1(held[1])
        _require_maintenance_session_v1(held[2])
    except Exception as exc:
        raise _invalid("dominant_startup_sessions_invalid", "product") from exc
    return held


def _complete_dominant_startup_core_v1(
    *,
    sessions: tuple[object, ...],
    observe_identity,
    observe_topology,
    observe_catalog,
    plan_retirement,
    observe_enforcement,
    cross,
    require_sessions,
    _crash_seam=None,
) -> DominantStartupReceiptV1:
    """Compose the whole crossing in ONE call that never releases the locks.

    The order is the property, not a convenience. Every observation happens
    while the three locks are held; the capability is minted only after all of
    them; each is then OBSERVED AGAIN and compared before the crossing runs.
    A second reading that agrees is the only thing separating "it was true when
    we looked" from "it is true now", and the whole boundary exists because
    those differ.

    Nothing is passed in already decided: the caller supplies observers, not
    values. An argument that carried a digest instead of a way to obtain one
    would let the decision be made outside the locks and merely reported here.
    """
    for observer in (
        observe_identity, observe_topology, observe_catalog, plan_retirement,
        observe_enforcement, cross, require_sessions,
    ):
        if not callable(observer):
            raise _invalid("dominant_startup_observer_invalid")
    held = require_sessions(sessions)

    identity = observe_identity()
    if type(identity) is not tuple or len(identity) != 3:
        raise _invalid("dominant_startup_binding_invalid", "identity")
    catalog = _require_digest_v1(observe_catalog(), "catalog_id")
    enforcement = _require_digest_v1(
        observe_enforcement(), "enforcement_evidence_digest",
    )
    retirement = _require_digest_v1(
        plan_retirement(), "retirement_plan_digest",
    )
    topology = _require_digest_v1(
        observe_topology(), "effective_topology_hash",
    )
    request_id, previous_head, context_transition_id = identity
    bindings = DominantStartupBindingsV1(
        request_id=_require_digest_v1(request_id, "request_id"),
        previous_head_digest=_require_digest_v1(
            previous_head, "previous_head_digest",
        ),
        context_transition_id=_require_digest_v1(
            context_transition_id,
            "context_transition_id",
        ),
        catalog_id=catalog,
        effective_topology_hash=topology,
        enforcement_evidence_digest=enforcement,
    )
    capability = _DominantStartupInstalledV1(bindings, held, _CAPABILITY_SEAL_V1)
    if _crash_seam:
        _crash_seam("capability_minted")

    # The second reading. Same observers, same locks, no cached value.
    require_sessions(held)
    if (
        observe_identity() != identity
        or observe_catalog() != catalog
        or observe_enforcement() != enforcement
        or plan_retirement() != retirement
        or observe_topology() != topology
    ):
        raise _invalid("dominant_startup_binding_drift", "second reading")
    require_sessions(held)
    bindings_digest = consume_v1(capability, bindings)
    if _crash_seam:
        _crash_seam("capability_consumed")
    receipt = DominantStartupReceiptV1(
        bindings,
        bindings_digest,
        retirement,
        enforcement,
        dominant_startup_receipt_v1(
            bindings_digest,
            retirement,
            enforcement,
        ),
        _RECEIPT_SEAL_V1,
    )
    cross(receipt)
    return receipt


def complete_dominant_startup_v1(
    *,
    sessions: tuple[object, ...],
    observe_identity,
    observe_topology,
    observe_catalog,
    plan_retirement,
    observe_enforcement,
    cross,
    _crash_seam=None,
) -> DominantStartupReceiptV1:
    """Cross only while the three exact productive sessions remain live."""
    return _complete_dominant_startup_core_v1(
        sessions=sessions,
        observe_identity=observe_identity,
        observe_topology=observe_topology,
        observe_catalog=observe_catalog,
        plan_retirement=plan_retirement,
        observe_enforcement=observe_enforcement,
        cross=cross,
        require_sessions=_require_product_sessions_v1,
        _crash_seam=_crash_seam,
    )


def _complete_dominant_startup_for_test_v1(
    *,
    sessions: tuple[object, ...],
    observe_identity,
    observe_topology,
    observe_catalog,
    plan_retirement,
    observe_enforcement,
    cross,
    _crash_seam=None,
) -> DominantStartupReceiptV1:
    """Nominal portable seam; it never accepts productive lock authority."""
    return _complete_dominant_startup_core_v1(
        sessions=sessions,
        observe_identity=observe_identity,
        observe_topology=observe_topology,
        observe_catalog=observe_catalog,
        plan_retirement=plan_retirement,
        observe_enforcement=observe_enforcement,
        cross=cross,
        require_sessions=_require_live_sessions_v1,
        _crash_seam=_crash_seam,
    )
