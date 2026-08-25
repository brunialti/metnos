"""Data-only producer facades for the atomic Executor Birth runtime."""
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
    reason: str
    approval_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_source_root, Path):
            raise ValueError("birth_intent_invalid: candidate_source_root")
        if not isinstance(self.contract_id, ContractId):
            raise ValueError("birth_intent_invalid: contract_id")
        if not isinstance(self.reason, str) or not self.reason or "\x00" in self.reason:
            raise ValueError("birth_intent_invalid: reason")
        if not isinstance(self.approval_refs, tuple) or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in self.approval_refs
        ):
            raise ValueError("birth_intent_invalid: approval_refs")


RequestFactory = Callable[[BirthIntent], "BirthRequest"]
_CAPABILITY_SEAL = object()


class _ProducerCapability:
    __slots__ = ("producer_id", "operation", "_seal")

    def __init__(self, producer_id: str, operation: str, seal: object) -> None:
        if seal is not _CAPABILITY_SEAL:
            raise ValueError("birth_producer_capability_untrusted")
        self.producer_id = producer_id
        self.operation = operation
        self._seal = seal


def _is_producer_capability(value: object) -> bool:
    return isinstance(value, _ProducerCapability) and value._seal is _CAPABILITY_SEAL


_CHANGE_EXTEND = _ProducerCapability("change_applier", "extend", _CAPABILITY_SEAL)
_CHANGE_ROLLBACK = _ProducerCapability("change_rollback", "rollback", _CAPABILITY_SEAL)
_SYNTH_MULTISTAGE = _ProducerCapability("synt_multistage", "create_or_replay", _CAPABILITY_SEAL)
_SYNTH_SPECIALIZE = _ProducerCapability("synt_specialize", "specialize_or_replay", _CAPABILITY_SEAL)
_SYNTH_APPROVE = _ProducerCapability("synt_approve", "approve_or_replay", _CAPABILITY_SEAL)
_PROMOTE = _ProducerCapability("promoter", "promote", _CAPABILITY_SEAL)
_STACK_RECONCILE = _ProducerCapability("stack_reconcile", "restart_sign_first", _CAPABILITY_SEAL)
_SKILLS = _ProducerCapability("skills_cli", "skill_import_or_reactivation", _CAPABILITY_SEAL)
_INSTALLER = _ProducerCapability("installer_phase3", "install", _CAPABILITY_SEAL)
_BUILTIN = _ProducerCapability("builtin_contract_generator", "generate_builtin", _CAPABILITY_SEAL)
_PROMOTER_ROLLBACK = _ProducerCapability("promoter", "rollback", _CAPABILITY_SEAL)


def _submit(intent: BirthIntent, capability: _ProducerCapability) -> "BirthResult":
    from executor_birth_operational import _execute_intent_with_capability
    return _execute_intent_with_capability(intent, capability)


def require_birth_intent_adapter() -> None:
    """Compatibility name: require the one complete runtime bundle."""
    from executor_birth_operational import _runtime_bundle_snapshot
    if _runtime_bundle_snapshot() is None:
        raise RuntimeError("birth_runtime_bundle_unavailable")


def submit_change_extend_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _CHANGE_EXTEND)


def submit_change_rollback_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _CHANGE_ROLLBACK)


def submit_synth_multistage_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _SYNTH_MULTISTAGE)


def submit_synth_specialize_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _SYNTH_SPECIALIZE)


def submit_synth_approve_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _SYNTH_APPROVE)


def submit_promote_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _PROMOTE)


def submit_stack_reconcile_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _STACK_RECONCILE)


def submit_skills_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _SKILLS)


def submit_installer_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _INSTALLER)


def submit_builtin_generation_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _BUILTIN)


def submit_promoter_rollback_birth(intent: BirthIntent) -> "BirthResult":
    return _submit(intent, _PROMOTER_ROLLBACK)


def _producer_capabilities_for_bootstrap() -> tuple[_ProducerCapability, ...]:
    return (_CHANGE_EXTEND, _CHANGE_ROLLBACK, _SYNTH_MULTISTAGE,
            _SYNTH_SPECIALIZE, _SYNTH_APPROVE, _PROMOTE, _STACK_RECONCILE, _SKILLS,
            _INSTALLER, _BUILTIN, _PROMOTER_ROLLBACK)


def _submit_birth_intent_for_test(intent: BirthIntent, *, request_factory: RequestFactory) -> "BirthResult":
    """Local test hook; cannot read or replace productive runtime state."""
    from executor_birth_operational import BirthRequest, birth_executor
    request = request_factory(intent)
    if not isinstance(request, BirthRequest):
        raise ValueError("birth_request_invalid")
    return birth_executor(request)


__all__ = [
    "BirthIntent", "require_birth_intent_adapter", "submit_change_extend_birth",
    "submit_change_rollback_birth", "submit_synth_multistage_birth",
    "submit_synth_specialize_birth", "submit_synth_approve_birth",
    "submit_promote_birth", "submit_stack_reconcile_birth", "submit_skills_birth",
    "submit_installer_birth", "submit_builtin_generation_birth",
    "submit_promoter_rollback_birth",
]
