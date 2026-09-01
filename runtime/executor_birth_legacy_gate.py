"""Compiled F4 denial for contract authorities superseded by Executor Birth.

This module deliberately has no environment, configuration, user-state or
caller-controlled activation switch.  The closed F4 distribution authenticates
this exact source literal in its signed manifest, so an older build cannot
claim the same policy state.

The check still distinguishes the productive store from an explicitly
supplied isolated root.  That distinction preserves non-productive offline
fixtures; it cannot redirect a productive write because ``contract_store``
rejects every explicit root overlapping the production container.
"""
from __future__ import annotations

from pathlib import Path


LEGACY_API_CLOSED = "birth_ownership_legacy_api_closed"


class LegacyBirthAuthorityClosed(RuntimeError):
    """A superseded authority was invoked by a closed F4 build."""

    def __init__(self, operation: str) -> None:
        self.code = LEGACY_API_CLOSED
        self.operation = operation
        super().__init__(f"{LEGACY_API_CLOSED}: {operation}")


def closed_build_enforcement() -> bool:
    """Return the build-authenticated, compile-time policy bit.

    Do not replace this with an environment/configuration lookup.  The signed
    build-manifest verifier authenticates this file before the transition can
    certify or select the release.
    """
    return True


def deny_legacy_contract_api(
    operation: str, *, store_root: Path | str | None,
) -> None:
    """Deny a legacy store mutation before inspecting any other argument.

    ``None`` denotes the productive root.  An explicit path is admitted only
    as a non-productive candidate; the store performs its stricter overlap and
    link checks immediately afterwards.
    """
    if closed_build_enforcement() and store_root is None:
        raise LegacyBirthAuthorityClosed(operation)


def deny_legacy_signing_api(operation: str) -> None:
    """Deny legacy authoring/signing in a closed build unconditionally.

    A future offline tool must live in a distribution that cannot load the
    productive store.  Accepting a path or mode flag here would recreate the
    authority being removed.
    """
    if closed_build_enforcement():
        raise LegacyBirthAuthorityClosed(operation)


__all__ = [
    "LEGACY_API_CLOSED", "LegacyBirthAuthorityClosed",
    "closed_build_enforcement", "deny_legacy_contract_api",
    "deny_legacy_signing_api",
]
