"""Closed-build gate for authorities superseded by Executor Birth.

The policy is compiled into the authenticated release: it has no environment,
configuration, user-state, or caller-controlled activation switch.  Productive
legacy mutation is denied, while an explicit isolated store remains available
to non-productive fixtures and offline verification.
"""
from __future__ import annotations

from pathlib import Path


LEGACY_API_CLOSED = "birth_ownership_legacy_api_closed"


class BirthAuthorityGateClosed(RuntimeError):
    """A superseded authority was invoked by a closed build."""

    def __init__(self, operation: str) -> None:
        self.code = LEGACY_API_CLOSED
        self.operation = operation
        super().__init__(f"{LEGACY_API_CLOSED}: {operation}")


def closed_build_enforcement() -> bool:
    """Return the build-authenticated compile-time policy bit."""
    return True


def require_closed_build_v1() -> None:
    """Fail unless this authenticated release permanently closes legacy APIs."""
    if closed_build_enforcement() is not True:
        raise BirthAuthorityGateClosed("closed_build_enforcement")


def deny_legacy_contract_api(
    operation: str, *, store_root: Path | str | None,
) -> None:
    """Deny productive legacy store mutation before any state observation."""
    if closed_build_enforcement() and store_root is None:
        raise BirthAuthorityGateClosed(operation)


def deny_legacy_signing_api(operation: str) -> None:
    """Deny superseded signing and authoring entry points unconditionally."""
    if closed_build_enforcement():
        raise BirthAuthorityGateClosed(operation)


__all__ = [
    "LEGACY_API_CLOSED", "BirthAuthorityGateClosed",
    "closed_build_enforcement", "deny_legacy_contract_api",
    "deny_legacy_signing_api", "require_closed_build_v1",
]
