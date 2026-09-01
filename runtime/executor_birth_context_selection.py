"""Nominal selection of one verified RM-0008 authority context.

Only the required-chain reader and the staged F4 coordinator mint this value.
Both paths must present an authenticated transition, a fully read-back
authority set and the sealed distribution selected for that transition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from executor_birth_context_transition import (
    ContextTransitionV1,
    verify_context_transition_v1,
)
from executor_birth_distribution_manifest import (
    VerifiedDistribution,
    is_verified_distribution,
)
from executor_birth_prepared_set import PreparedSetV1, is_prepared_set_v1


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SELECTION_SEAL_V1 = object()
_REQUIRED_MODE_V1 = object()
_STAGED_REATTESTATION_MODE_V1 = object()


class ContextSelectionError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ContextSelectionV1:
    """Sealed context identity plus its authenticated closed distribution."""

    transition_id: str
    set_id: str
    admission_context_id: str
    context_epoch: str
    distribution: VerifiedDistribution
    _mode: object
    _seal: object

    def __post_init__(self) -> None:
        if (
            self._seal is not _SELECTION_SEAL_V1
            or self._mode not in {
                _REQUIRED_MODE_V1,
                _STAGED_REATTESTATION_MODE_V1,
            }
            or _DIGEST_RE.fullmatch(self.transition_id or "") is None
            or _HEX_SHA256_RE.fullmatch(self.set_id or "") is None
            or _DIGEST_RE.fullmatch(self.admission_context_id or "") is None
            or _DIGEST_RE.fullmatch(self.context_epoch or "") is None
            or not is_verified_distribution(self.distribution)
        ):
            raise ContextSelectionError("birth_context_selection_invalid")

    @property
    def staged_reattestation_only(self) -> bool:
        return self._mode is _STAGED_REATTESTATION_MODE_V1


def _mint_context_selection_v1(
    transition: ContextTransitionV1,
    prepared: PreparedSetV1,
    distribution: VerifiedDistribution,
    *,
    mode: object,
) -> ContextSelectionV1:
    if (
        not isinstance(transition, ContextTransitionV1)
        or not is_prepared_set_v1(prepared)
        or not is_verified_distribution(distribution)
    ):
        raise ContextSelectionError(
            "birth_context_selection_invalid", "authority",
        )
    try:
        verified_transition = verify_context_transition_v1(
            transition.encoded,
            expected_transition_id=transition.transition_id,
        )
    except Exception as exc:
        raise ContextSelectionError(
            "birth_context_selection_invalid", "transition authority",
        ) from exc
    if verified_transition != transition:
        raise ContextSelectionError(
            "birth_context_selection_invalid", "transition authority",
        )
    if (
        transition.closed_build_id != distribution.identity.closed_build_id
        or transition.set_id != prepared.set_id
        or transition.prepared_admission_context_id
        != prepared.prepared_admission_context_id
        or transition.prepared_context_epoch != prepared.prepared_context_epoch
        or transition.context_material_sha256
        != prepared.context_material_sha256
        or transition.set_json_sha256 != prepared.set_json_sha256
    ):
        raise ContextSelectionError(
            "birth_context_selection_binding_invalid", "transition evidence",
        )
    return ContextSelectionV1(
        transition_id=transition.transition_id,
        set_id=transition.set_id,
        admission_context_id=transition.prepared_admission_context_id,
        context_epoch=transition.prepared_context_epoch,
        distribution=distribution,
        _mode=mode,
        _seal=_SELECTION_SEAL_V1,
    )


def _context_selection_from_required_chain_v1(
    transition: ContextTransitionV1,
    prepared: PreparedSetV1,
    distribution: VerifiedDistribution,
) -> ContextSelectionV1:
    """Private producer used only after the required chain is re-read."""
    return _mint_context_selection_v1(
        transition,
        prepared,
        distribution,
        mode=_REQUIRED_MODE_V1,
    )


def _context_selection_for_staged_reattestation_v1(
    transition: ContextTransitionV1,
    prepared: PreparedSetV1,
    distribution: VerifiedDistribution,
) -> ContextSelectionV1:
    """Private producer whose result cannot authorize ordinary bootstrap."""
    return _mint_context_selection_v1(
        transition,
        prepared,
        distribution,
        mode=_STAGED_REATTESTATION_MODE_V1,
    )


def is_context_selection_v1(value: object, *, allow_staged: bool = False) -> bool:
    """Recognize genuine selections, optionally including the staged scope."""
    return (
        isinstance(value, ContextSelectionV1)
        and value._seal is _SELECTION_SEAL_V1
        and (
            value._mode is _REQUIRED_MODE_V1
            or (allow_staged and value._mode is _STAGED_REATTESTATION_MODE_V1)
        )
    )


__all__ = [
    "ContextSelectionError",
    "ContextSelectionV1",
    "is_context_selection_v1",
]
