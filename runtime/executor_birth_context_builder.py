"""Productive immutable adapter for the RM-0008 admission-context resolver."""
from __future__ import annotations

from dataclasses import dataclass

from executor_birth_context import (
    AdmissionContextMaterial, BuiltAdmissionContext, build_admission_context,
)
from executor_birth_identity import AdmissionContextV1
from executor_birth_predecessor import AdmissionContextPin


@dataclass(frozen=True, slots=True)
class ProductionContextBuilder:
    """A process-lifetime snapshot of explicitly supplied core material."""

    snapshot: BuiltAdmissionContext

    def preview(self, _intent: object) -> tuple[AdmissionContextV1, AdmissionContextPin]:
        return self.snapshot.context, self.snapshot.pin

    def resolve(self, _request: object) -> tuple[AdmissionContextV1, AdmissionContextPin]:
        return self.snapshot.context, self.snapshot.pin

    def current_epoch(self) -> str:
        return self.snapshot.pin.context_epoch


def production_context_builder(material: AdmissionContextMaterial) -> ProductionContextBuilder:
    """Freeze an explicit core-owned material map; no implicit default exists."""
    return ProductionContextBuilder(build_admission_context(material))


__all__ = ["ProductionContextBuilder", "production_context_builder"]
