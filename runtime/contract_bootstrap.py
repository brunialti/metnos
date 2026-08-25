"""Neutral filesystem classifier for the irreversible contract-store cutover.

Both the low-level store and the manifest loader must derive their bootstrap
decision from exactly the same matrix.  This module owns that read-only
classification without importing either subsystem.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_PHYSICAL_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
STORE_RELATIVE = Path("contract-publications") / "v1"
ACTIVE_RELATIVE = Path("contract-publications.ACTIVE")
ACTIVE_BYTES = b"v1\n"


class ProductionStoreMode(str, Enum):
    """Irreversible bootstrap state derived only from root and marker."""

    LEGACY = "legacy"
    STORE_ONLY = "store_only"
    RECOVERY_REQUIRED = "recovery_required"
    ACTIVE = "active"


class BootstrapStateError(RuntimeError):
    """A malformed filesystem object prevents trustworthy classification."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True, slots=True)
class ProductionStoreState:
    """One shared classification plus a stable recovery diagnosis."""

    mode: ProductionStoreMode
    recovery_code: str | None = None
    detail: str = ""


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _is_link_like(path: Path) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(
        getattr(status, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ) or stat.S_ISLNK(status.st_mode) or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _require_no_link_components(path: Path, *, code: str) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        if _is_link_like(component):
            raise BootstrapStateError(code, str(component))


def _require_plain_directory(path: Path, *, code: str) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise BootstrapStateError(code, f"{path}: {exc}") from exc
    if (
        _is_link_like(path)
        or not stat.S_ISDIR(status.st_mode)
    ):
        raise BootstrapStateError(code, str(path))


def _read_active_marker(path: Path, *, active_bytes: bytes) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise BootstrapStateError(
            "active_marker_invalid", f"{path}: {exc}",
        ) from exc
    if _is_link_like(path) or not stat.S_ISREG(status.st_mode):
        raise BootstrapStateError("active_marker_invalid", str(path))
    _require_no_link_components(path.parent, code="active_marker_invalid")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise BootstrapStateError(
            "active_marker_invalid", f"{path}: {exc}",
        ) from exc
    if payload != active_bytes:
        raise BootstrapStateError(
            "active_marker_invalid", f"unexpected payload in {path}",
        )


def _container_is_complete(container: Path, version_root: Path) -> bool:
    """Recognize the complete global skeleton without trusting revisions."""
    try:
        if {child.name for child in container.iterdir()} != {version_root.name}:
            return False
        _require_plain_directory(
            version_root, code="production_store_incomplete",
        )
        _require_no_link_components(
            version_root, code="production_store_incomplete",
        )
        contracts = tuple(version_root.iterdir())
        if not contracts:
            return False
        return all(
            _PHYSICAL_ID_RE.fullmatch(contract_dir.name) is not None
            and not _is_link_like(contract_dir)
            and contract_dir.is_dir()
            for contract_dir in contracts
        )
    except (BootstrapStateError, OSError):
        return False


def classify_production_store(
    *,
    version_root: Path,
    active_marker: Path,
    active_bytes: bytes,
) -> ProductionStoreState:
    """Classify one production location with the canonical one-way matrix.

    An empty, partial or debris-contaminated container is never a store-only
    layout.  It is a recovery state, with a stable diagnosis for callers that
    must fail closed rather than perform recovery themselves.
    """
    version_root = Path(version_root)
    active_marker = Path(active_marker)
    container = version_root.parent
    marker_present = _lexists(active_marker)
    container_present = _lexists(container)

    if marker_present:
        _read_active_marker(active_marker, active_bytes=active_bytes)
    if container_present:
        _require_plain_directory(container, code="production_store_invalid")
        _require_no_link_components(container, code="production_store_invalid")

    if not marker_present and not container_present:
        return ProductionStoreState(ProductionStoreMode.LEGACY)
    if not container_present:
        return ProductionStoreState(
            ProductionStoreMode.RECOVERY_REQUIRED,
            "store_root_missing",
            str(container),
        )
    if not _lexists(version_root):
        return ProductionStoreState(
            ProductionStoreMode.RECOVERY_REQUIRED,
            "store_version_missing",
            str(version_root),
        )
    if not _container_is_complete(container, version_root):
        return ProductionStoreState(
            ProductionStoreMode.RECOVERY_REQUIRED,
            "production_store_incomplete",
            str(container),
        )
    return ProductionStoreState(
        ProductionStoreMode.ACTIVE
        if marker_present
        else ProductionStoreMode.STORE_ONLY,
    )


__all__ = [
    "ACTIVE_BYTES",
    "ACTIVE_RELATIVE",
    "BootstrapStateError",
    "ProductionStoreMode",
    "ProductionStoreState",
    "STORE_RELATIVE",
    "classify_production_store",
]
