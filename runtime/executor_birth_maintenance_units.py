"""Compatibility projections from the single RM-0008 service topology."""
from __future__ import annotations

from executor_birth_service_catalog import (
    contract_cutover_units_from_source_v1,
    maintenance_targets_from_source_v1,
)

CONTRACT_CUTOVER_UNITS = contract_cutover_units_from_source_v1()
MAINTENANCE_TARGETS_V1 = maintenance_targets_from_source_v1()


__all__ = ["CONTRACT_CUTOVER_UNITS", "MAINTENANCE_TARGETS_V1"]
