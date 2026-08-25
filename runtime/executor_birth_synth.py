"""Producer-specific Synth facade over the core-owned Birth intent adapter."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from executor_birth_intent import (
    BirthIntent, require_birth_intent_adapter, submit_birth_intent,
)
from executor_birth_operational import BirthResult
from manifest_inventory import ContractId

SynthProducer = Literal["synt_multistage", "synt_specialize", "synt_approve"]
SynthOperation = Literal["create", "specialize", "approve", "replay"]


@dataclass(frozen=True, slots=True)
class SynthBirthData:
    candidate_root: Path
    contract_id: ContractId
    producer: SynthProducer
    operation: SynthOperation
    reason: str
    approval_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_root, Path):
            raise ValueError("synth_birth_invalid: candidate_root")
        if not isinstance(self.contract_id, ContractId):
            raise ValueError("synth_birth_invalid: contract_id")
        if self.producer not in {"synt_multistage", "synt_specialize", "synt_approve"}:
            raise ValueError("synth_birth_invalid: producer")
        if self.operation not in {"create", "specialize", "approve", "replay"}:
            raise ValueError("synth_birth_invalid: operation")
        if not isinstance(self.reason, str) or not self.reason or "\x00" in self.reason:
            raise ValueError("synth_birth_invalid: reason")
        if not isinstance(self.approval_refs, tuple) or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in self.approval_refs
        ):
            raise ValueError("synth_birth_invalid: approval_refs")


def _as_intent(data: SynthBirthData) -> BirthIntent:
    if not isinstance(data, SynthBirthData):
        raise ValueError("synth_birth_invalid")
    return BirthIntent(
        candidate_source_root=data.candidate_root, actor=data.producer,
        contract_id=data.contract_id,
        reason=data.reason, operation=data.operation,
        approval_refs=data.approval_refs,
    )


def require_synth_birth_service() -> None:
    """Fail before Synth creates or copies an authoring candidate."""
    require_birth_intent_adapter()


def submit_synth_birth(data: SynthBirthData) -> BirthResult:
    return submit_birth_intent(_as_intent(data))


__all__ = ["SynthBirthData", "require_synth_birth_service", "submit_synth_birth"]
