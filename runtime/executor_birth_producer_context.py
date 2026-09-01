"""Sealed constructor of the V2 Producer request bound to a birth context.

This module owns exactly one authority: turning a context selection that the
F4 head has already sealed into the deterministic identity of a Producer
request for the second epoch.  It is deliberately the *only* place where that
identity is derived, so the reattestation writer, the durable Producer
transaction and the later re-read all agree by construction instead of by
convention.

Three properties are structural rather than checked downstream:

* the caller never supplies a context.  ``admission_context_id``,
  ``transition_id``, ``context_epoch`` and ``set_id`` are read from the sealed
  ``ContextSelectionV1`` delivered by the loader; there is no keyword that
  accepts any of them, and no path or selector is accepted anywhere.
* a V1 request can never collide with a V2 request for the same generation.
  The derivation is domain separated and length delimited, and the V2 domains
  are not used by any V1 producer path.
* a request minted for one epoch cannot be reused in another.  Every field of
  the selection enters the framed pre-image, so a different transition, epoch,
  target set or admission context yields a different ``request_id``.
* two acts that differ only in the authenticated candidate source are two
  acts.  The source identity is part of the derivation, so it cannot be
  changed while keeping the same durable transaction.

The identity is a pure function of (contract, generation, selection): a retry
after an interruption derives the same request and therefore renews the same
durable transaction rather than opening a second one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "ProducerContextError",
    "ProducerRequestV2",
    "build_producer_request_v2",
]

# Domain separation.  Both values are derived from the same authenticated
# facts, which is precisely the point: the receipt and the Producer record are
# two representations of one act.  Distinct domains keep the two identities
# from ever being substituted for one another.
_REQUEST_DOMAIN = b"metnos.executor-birth.producer-request/v2\0"
_OBJECTIVE_DOMAIN = b"metnos.executor-birth.producer-objective/v2\0"

class ProducerContextError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


_REQUEST_SEAL = object()


@dataclass(frozen=True, slots=True)
class ProducerRequestV2:
    """Deterministic identity of one V2 Producer request.

    Instances cannot be built by a caller: the seal is module private and the
    single constructor below is the only holder.  A forged look-alike is
    therefore rejected by the consumers that check the type.
    """

    request_id: str
    objective_hash: str
    contract_id: str
    generation_id: str
    admission_context_id: str
    transition_id: str
    context_epoch: str
    set_id: str
    candidate_source_id: str
    _seal: object

    def __post_init__(self) -> None:
        if self._seal is not _REQUEST_SEAL:
            raise ProducerContextError("producer_request_v2_untrusted")


def _digest(value: object, field: str) -> str:
    """Accept only a canonical lowercase ``sha256:`` digest."""
    if (not isinstance(value, str) or len(value) != 71
            or not value.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value[7:])):
        raise ProducerContextError("producer_request_v2_invalid", field)
    return value


def _hash(domain: bytes, *values: bytes) -> str:
    """Length-delimited framing so no two field splits share a pre-image."""
    framed = bytearray(domain)
    for value in values:
        framed.extend(len(value).to_bytes(8, "big"))
        framed.extend(value)
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _selection_class():
    """Resolve the nominal type delivered by the loader.

    The check is nominal, not structural: an object that merely carries the
    right attribute names is not a context selection.  When the loader module
    is absent the constructor fails closed instead of trusting the argument.
    """
    try:
        from executor_birth_context_selection import ContextSelectionV1
    except Exception as exc:  # pragma: no cover - exercised by the absent-module test
        raise ProducerContextError(
            "producer_request_v2_selection_unavailable", str(exc),
        ) from exc
    if not isinstance(ContextSelectionV1, type):
        raise ProducerContextError(
            "producer_request_v2_selection_unavailable", "not a type",
        )
    return ContextSelectionV1


def _hex_digest(value: object, field: str) -> str:
    """Accept only a bare lowercase 64 hex digest, with no ``sha256:`` prefix.

    The loader delivers ``set_id`` in this form while the other identities
    carry the prefix.  Both are validated for the form they actually have
    rather than normalised into one, so a value that crossed from one field to
    the other is rejected instead of silently accepted.
    """
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ProducerContextError("producer_request_v2_invalid", field)
    return value


def _contract_value(contract_id: object) -> str:
    """Read the contract identity without ever stringifying the object.

    ``ContractId`` is a dataclass: ``str()`` on it yields the repr, which is a
    silent corruption of the pre-image rather than an error.
    """
    value = getattr(contract_id, "value", None)
    if not isinstance(value, str) or not value or "\0" in value:
        raise ProducerContextError("producer_request_v2_invalid", "contract_id")
    return value


def build_producer_request_v2(
    selection: object,
    *,
    contract_id: object,
    generation_id: str,
    candidate_source_id: str,
) -> ProducerRequestV2:
    """Derive the one V2 Producer request identity for this target and context.

    ``selection`` must be the sealed ``ContextSelectionV1`` produced by the
    loader.  Nothing about the context can be supplied by the caller, and the
    result is a pure function of the arguments, so an identical retry after an
    interruption reproduces it exactly.

    ``candidate_source_id`` is the authenticated source identity of the act.
    Section 9 requires it in both derivations: without it two reattestations
    that differ only in what they attest would share one durable transaction.
    """
    if not isinstance(selection, _selection_class()):
        raise ProducerContextError("producer_request_v2_invalid", "selection")

    transition_id = _digest(getattr(selection, "transition_id", None), "transition_id")
    set_id = _hex_digest(getattr(selection, "set_id", None), "set_id")
    admission_context_id = _digest(
        getattr(selection, "admission_context_id", None), "admission_context_id",
    )
    context_epoch = _digest(
        getattr(selection, "context_epoch", None), "context_epoch",
    )
    contract_value = _contract_value(contract_id)
    generation = _digest(generation_id, "generation_id")
    source = _digest(candidate_source_id, "candidate_source_id")

    # Every authenticated fact of the selection enters the pre-image, so a
    # request cannot survive a change of transition, epoch, target set or
    # admission context even if the target generation is unchanged.
    fields = (
        contract_value.encode("utf-8"),
        generation.encode("ascii"),
        admission_context_id.encode("ascii"),
        transition_id.encode("ascii"),
        set_id.encode("ascii"),
        context_epoch.encode("ascii"),
        source.encode("ascii"),
    )
    return ProducerRequestV2(
        _hash(_REQUEST_DOMAIN, *fields),
        _hash(_OBJECTIVE_DOMAIN, *fields),
        contract_value,
        generation,
        admission_context_id,
        transition_id,
        context_epoch,
        set_id,
        source,
        _REQUEST_SEAL,
    )
