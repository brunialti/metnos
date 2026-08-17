"""Interface-only hook for a future bounded semantic critic.

No implementation here invokes a model.  A caller may materialize at most one
critic request after an independently supplied mechanical disagreement signal.
"""
from __future__ import annotations

from typing import Protocol

from .types import CompilationResult, CriticRequest, DisagreementSignal


TARGET_MAX_AVERAGE_MODEL_CALLS = 1.20
MAX_MODEL_CALLS_PER_ANALYSIS = 2


class MechanicalDisagreementDetector(Protocol):
    def detect(self, query: str, primary: CompilationResult) -> DisagreementSignal: ...


class SemanticCritic(Protocol):
    def review(self, request: CriticRequest) -> bytes: ...


class NoDisagreement:
    """Default detector: one primary call and no speculative second call."""

    def detect(self, query: str, primary: CompilationResult) -> DisagreementSignal:
        del query, primary
        return DisagreementSignal(False, ())


def critic_is_allowed(signal: DisagreementSignal, completed_calls: int) -> bool:
    return signal.required and completed_calls == 1 and completed_calls < MAX_MODEL_CALLS_PER_ANALYSIS
