"""Byte-backed construction of the RM-0008 admission context.

The builder accepts versions and *material*, never caller supplied digests.
Every component digest is computed from an immutable copy of the files and
configuration values that implement the active admission boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from executor_birth_identity import (
    AdmissionContextV1, ContextComponent, admission_context_id,
)
from executor_birth_predecessor import AdmissionContextPin


_COMPONENT_NAMES = (
    "standard", "linter", "vocabulary", "authority_registry",
    "sandbox_registry", "property_catalog", "runner", "review_policy",
    "template_allowlist", "primitive_allowlist", "dependency_allowlist",
)
_MATERIAL_DOMAIN = b"metnos.executor-birth.context-component/v1\0"
_EPOCH_DOMAIN = b"metnos.executor-birth.context-epoch/v1\0"


class AdmissionContextBuildError(ValueError):
    """The active admission material cannot be frozen safely."""


def _canonical_json(value: object) -> bytes:
    def validate(item: object) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if math.isfinite(item):
                return
            raise AdmissionContextBuildError("admission_context_config_invalid")
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                validate(child)
            return
        raise AdmissionContextBuildError("admission_context_config_invalid")

    validate(value)
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdmissionContextBuildError("admission_context_config_invalid") from exc


@dataclass(frozen=True, slots=True)
class MaterialFile:
    """One effective implementation/configuration file and its stable label."""

    label: str
    path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise AdmissionContextBuildError("admission_context_material_label_invalid")
        path = Path(self.path)
        if not path.is_absolute():
            raise AdmissionContextBuildError("admission_context_material_path_not_absolute")
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class ComponentMaterial:
    """Complete effective material for one context component.

    ``configuration`` must be the already resolved configuration consumed by
    the implementation (not a path or an asserted hash).  Files are copied by
    the builder with anti-link and anti-race checks.
    """

    version: str
    files: tuple[MaterialFile, ...] = ()
    configuration: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise AdmissionContextBuildError("admission_context_version_invalid")
        files = tuple(self.files)
        if any(not isinstance(item, MaterialFile) for item in files):
            raise AdmissionContextBuildError("admission_context_material_invalid")
        labels = [item.label for item in files]
        if len(labels) != len(set(labels)):
            raise AdmissionContextBuildError("admission_context_material_label_duplicate")
        if not files and self.configuration is None:
            raise AdmissionContextBuildError("admission_context_material_empty")
        # Validate while the caller still has a useful construction traceback.
        if self.configuration is not None:
            _canonical_json(self.configuration)
        object.__setattr__(self, "files", files)


@dataclass(frozen=True, slots=True)
class AdmissionContextMaterial:
    standard: ComponentMaterial
    linter: ComponentMaterial
    vocabulary: ComponentMaterial
    authority_registry: ComponentMaterial
    sandbox_registry: ComponentMaterial
    property_catalog: ComponentMaterial
    runner: ComponentMaterial
    review_policy: ComponentMaterial
    template_allowlist: ComponentMaterial
    primitive_allowlist: ComponentMaterial
    dependency_allowlist: ComponentMaterial


@dataclass(frozen=True, slots=True)
class FrozenComponentMaterial:
    version: str
    files: Mapping[str, bytes]
    configuration: bytes | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", MappingProxyType(dict(self.files)))


@dataclass(frozen=True, slots=True)
class BuiltAdmissionContext:
    context: AdmissionContextV1
    pin: AdmissionContextPin
    material: Mapping[str, FrozenComponentMaterial]

    def __post_init__(self) -> None:
        object.__setattr__(self, "material", MappingProxyType(dict(self.material)))


def _read_regular_file(source: MaterialFile) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(source.path, flags)
    except OSError as exc:
        raise AdmissionContextBuildError(
            f"admission_context_material_unreadable: {source.label}"
        ) from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AdmissionContextBuildError(
                f"admission_context_material_unsafe: {source.label}"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        payload = b"".join(chunks)
        if identity_before != identity_after or len(payload) != after.st_size:
            raise AdmissionContextBuildError(
                f"admission_context_material_changed: {source.label}"
            )
        return payload
    finally:
        os.close(fd)


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(8, "big") + payload


def _freeze_component(material: ComponentMaterial) -> FrozenComponentMaterial:
    files = {item.label: _read_regular_file(item) for item in material.files}
    configuration = (
        None if material.configuration is None
        else _canonical_json(material.configuration)
    )
    return FrozenComponentMaterial(material.version, files, configuration)


def _component_digest(name: str, material: FrozenComponentMaterial) -> str:
    body = bytearray(_MATERIAL_DOMAIN)
    body += _frame(name.encode("utf-8"))
    body += _frame(material.version.encode("utf-8"))
    body += len(material.files).to_bytes(8, "big")
    for label, payload in sorted(
        material.files.items(), key=lambda item: item[0].encode("utf-8")
    ):
        body += _frame(label.encode("utf-8"))
        body += _frame(payload)
    body += b"\x00" if material.configuration is None else b"\x01" + _frame(material.configuration)
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _context_epoch(context_id: str) -> str:
    return "sha256:" + hashlib.sha256(
        _EPOCH_DOMAIN + _frame(context_id.encode("ascii"))
    ).hexdigest()


def build_admission_context(material: AdmissionContextMaterial) -> BuiltAdmissionContext:
    """Freeze all eleven V1 components and derive the context and epoch pin."""
    if not isinstance(material, AdmissionContextMaterial):
        raise AdmissionContextBuildError("admission_context_material_invalid")
    frozen = {name: _freeze_component(getattr(material, name)) for name in _COMPONENT_NAMES}
    context = AdmissionContextV1(**{
        name: ContextComponent(value.version, _component_digest(name, value))
        for name, value in frozen.items()
    })
    context_id = admission_context_id(context)
    pin = AdmissionContextPin(context_id, _context_epoch(context_id))
    return BuiltAdmissionContext(context, pin, frozen)


__all__ = [
    "AdmissionContextBuildError", "AdmissionContextMaterial",
    "BuiltAdmissionContext", "ComponentMaterial", "FrozenComponentMaterial",
    "MaterialFile", "build_admission_context",
]
