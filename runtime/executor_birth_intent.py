"""Data-only producer adapter for the operational Executor Birth boundary.

Callers describe provenance and intent.  They never select receipt issuers,
trust registries, checks, keys or a publisher.  A sealed, core-owned adapter
is the only component allowed to construct the authenticated ``BirthRequest``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from manifest_inventory import ContractId

if TYPE_CHECKING:
    from executor_birth_operational import BirthRequest, BirthResult


@dataclass(frozen=True, slots=True)
class BirthIntent:
    candidate_source_root: Path
    contract_id: ContractId
    actor: str
    reason: str
    operation: str
    approval_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_source_root, Path):
            raise ValueError("birth_intent_invalid: candidate_source_root")
        if not isinstance(self.contract_id, ContractId):
            raise ValueError("birth_intent_invalid: contract_id")
        for field in ("actor", "reason", "operation"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError(f"birth_intent_invalid: {field}")
        if not isinstance(self.approval_refs, tuple) or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in self.approval_refs
        ):
            raise ValueError("birth_intent_invalid: approval_refs")


RequestFactory = Callable[[BirthIntent], "BirthRequest"]
_ADAPTER_SEAL = object()


class _SealedIntentAdapter:
    __slots__ = ("_factory", "_seal")

    def __init__(self, factory: RequestFactory, seal: object) -> None:
        if seal is not _ADAPTER_SEAL or not callable(factory):
            raise ValueError("birth_intent_adapter_untrusted")
        self._factory = factory
        self._seal = seal

    def execute(self, intent: BirthIntent) -> "BirthResult":
        from executor_birth_operational import BirthRequest, birth_executor
        request = self._factory(intent)
        if not isinstance(request, BirthRequest):
            raise ValueError("birth_request_invalid")
        return birth_executor(request)


def _assemble_intent_adapter(factory: RequestFactory) -> _SealedIntentAdapter:
    """Core bootstrap hook; callers cannot install policy dependencies."""
    return _SealedIntentAdapter(factory, _ADAPTER_SEAL)


_PRODUCTION_ADAPTER: _SealedIntentAdapter | None = None


def _install_production_intent_adapter(adapter: _SealedIntentAdapter) -> None:
    """Install once during trusted process bootstrap."""
    global _PRODUCTION_ADAPTER
    if not isinstance(adapter, _SealedIntentAdapter) or adapter._seal is not _ADAPTER_SEAL:
        raise ValueError("birth_intent_adapter_untrusted")
    if _PRODUCTION_ADAPTER is not None:
        raise ValueError("birth_intent_adapter_already_installed")
    _PRODUCTION_ADAPTER = adapter


def submit_birth_intent(intent: BirthIntent) -> "BirthResult":
    if not isinstance(intent, BirthIntent):
        raise ValueError("birth_intent_invalid")
    adapter = _PRODUCTION_ADAPTER
    if adapter is None:
        raise RuntimeError("birth_intent_adapter_unavailable")
    return adapter.execute(intent)


def require_birth_intent_adapter() -> None:
    """Fail before a caller mutates an authoring candidate."""
    if _PRODUCTION_ADAPTER is None:
        raise RuntimeError("birth_intent_adapter_unavailable")


def _submit_birth_intent_for_test(
    intent: BirthIntent, *, request_factory: RequestFactory,
) -> "BirthResult":
    """Test-only sealed path; it cannot replace productive process state."""
    return _assemble_intent_adapter(request_factory).execute(intent)


__all__ = ["BirthIntent", "require_birth_intent_adapter", "submit_birth_intent"]
