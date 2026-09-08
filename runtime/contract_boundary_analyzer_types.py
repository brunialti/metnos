"""Immutable value types emitted by the contract-boundary analyzer."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeFacts:
    path: str
    scope: str
    line: int
    capabilities: tuple[str, ...]
    calls: tuple[str, ...]
    direct_manifest_dir_access: bool = False
    closed_dynamic_boundary: bool = False

    @property
    def key(self) -> str:
        return f"{self.path}:{self.scope}"


@dataclass(frozen=True)
class Finding:
    code: str
    scope: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.scope}: {self.message}"


ANALYZER_TYPE_NAMES_V1 = ("ScopeFacts", "Finding")

__all__ = ["ANALYZER_TYPE_NAMES_V1", "ScopeFacts", "Finding"]


def _validate_analyzer_type_catalog_v1() -> None:
    names = ANALYZER_TYPE_NAMES_V1
    if (
        type(names) is not tuple
        or any(type(name) is not str or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("contract_boundary_analyzer_type_catalog_invalid")
    if type(__all__) is not list or tuple(__all__) != (
        "ANALYZER_TYPE_NAMES_V1", *names,
    ):
        raise ValueError("contract_boundary_analyzer_type_catalog_exports")


_validate_analyzer_type_catalog_v1()
