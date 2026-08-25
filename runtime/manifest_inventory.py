"""Neutral, deterministic inventory of executor manifest sources.

Discovery is deliberately separate from admission, signing and publication.
Finding a manifest here never makes it executable or writable.
"""
from __future__ import annotations

import hashlib
import os
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

import config as _C


class ManifestOrigin(str, Enum):
    CORE = "core"
    BUILTIN = "builtin"
    BUILTIN_SKILL = "builtin_skill"
    USER = "user"
    USER_SKILL = "user_skill"
    LEGACY_IMPORT = "legacy_import"
    RETIRED = "retired"
    EXPLICIT = "explicit"


class ManifestStatus(str, Enum):
    ADMITTED = "admitted"
    DISABLED = "disabled"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True, order=True)
class ContractId:
    origin: ManifestOrigin
    relative_manifest: str

    def __post_init__(self) -> None:
        relative = Path(self.relative_manifest)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != self.relative_manifest
            or relative.name != "manifest.toml"
        ):
            raise ValueError("relative_manifest must be a canonical manifest path")

    @property
    def value(self) -> str:
        return f"{self.origin.value}:{self.relative_manifest}"

    @property
    def storage_key(self) -> str:
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ManifestSource:
    origin: ManifestOrigin
    root: Path
    min_depth: int = 1
    max_depth: int | None = 1
    default_status: ManifestStatus = ManifestStatus.ADMITTED
    skill_scoped: bool = False
    allowed_code_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.min_depth < 0:
            raise ValueError("min_depth must be non-negative")
        if self.max_depth is not None and self.max_depth < self.min_depth:
            raise ValueError("max_depth must be greater than or equal to min_depth")


@dataclass(frozen=True, slots=True)
class ManifestRef:
    """A structural contract location plus optional authoring observations.

    ``contract_id``, origin, status, paths and code roots are sufficient to
    resolve an immutable published generation.  The remaining fields are
    populated by the legacy/authoring inventory, but are deliberately nullable
    so the post-cutover inventory can be reconstructed from ``binding.json``
    without reopening an authoring manifest.
    """

    contract_id: ContractId
    origin: ManifestOrigin
    status: ManifestStatus
    source_root: Path
    manifest_path: Path
    manifest_relative: str
    allowed_code_roots: tuple[Path, ...]
    manifest_hash: str | None = None
    name: str | None = None
    lifecycle: str | None = None
    skill_name: str | None = None

    @property
    def manifest_dir(self) -> Path:
        return self.manifest_path.parent


@dataclass(frozen=True, slots=True)
class InventoryProblem:
    code: str
    path: str
    detail: str
    origin: ManifestOrigin | None = None
    contracts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ManifestInventory:
    manifests: tuple[ManifestRef, ...]
    problems: tuple[InventoryProblem, ...]

    def by_id(self) -> Mapping[ContractId, ManifestRef]:
        return {item.contract_id: item for item in self.manifests}

    def admitted(self) -> tuple[ManifestRef, ...]:
        return tuple(
            item for item in self.manifests
            if item.status is ManifestStatus.ADMITTED
        )


def default_manifest_sources() -> tuple[ManifestSource, ...]:
    """Return every known topology without granting runtime authority."""
    return (
        ManifestSource(
            ManifestOrigin.CORE, _C.PATH_EXECUTORS,
            # Core contracts may bind their own executor files and shared
            # runtime modules (for example through a repository symlink), but
            # never arbitrary files elsewhere in the installation root.
            allowed_code_roots=(_C.PATH_EXECUTORS, _C.PATH_RUNTIME),
        ),
        ManifestSource(
            ManifestOrigin.BUILTIN,
            _C.PATH_RUNTIME / "builtin_executor_contracts",
            allowed_code_roots=(_C.PATH_RUNTIME,),
        ),
        ManifestSource(
            ManifestOrigin.BUILTIN_SKILL, _C.PATH_SKILLS_BUILTIN,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(_C.PATH_SKILLS_BUILTIN,),
        ),
        ManifestSource(
            ManifestOrigin.USER, _C.PATH_SYNTH_EXECUTORS,
            allowed_code_roots=(_C.PATH_SYNTH_EXECUTORS,),
        ),
        ManifestSource(
            ManifestOrigin.USER_SKILL, _C.PATH_SKILLS_USER,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(_C.PATH_SKILLS_USER,),
        ),
        ManifestSource(
            ManifestOrigin.LEGACY_IMPORT, _C.PATH_SKILLS_USER_LEGACY,
            min_depth=2, max_depth=2, skill_scoped=True,
            allowed_code_roots=(_C.PATH_SKILLS_USER_LEGACY,),
        ),
        ManifestSource(
            ManifestOrigin.RETIRED, _C.PATH_EXECUTORS / "_retired",
            min_depth=1, max_depth=None,
            default_status=ManifestStatus.RETIRED,
            allowed_code_roots=(_C.PATH_EXECUTORS / "_retired",),
        ),
    )


def _path_has_symlink(root: Path, path: Path) -> bool:
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _iter_source_paths(source: ManifestSource) -> Iterable[Path]:
    root = Path(source.root)
    if not root.is_dir():
        return ()
    paths: list[Path] = []
    for path in root.rglob("manifest.toml"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        depth = len(relative.parts) - 1
        if depth < source.min_depth:
            continue
        if source.max_depth is not None and depth > source.max_depth:
            continue
        paths.append(path)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _default_skill_enabled(name: str) -> bool:
    from skill_registry import is_skill_enabled

    return bool(is_skill_enabled(name))


def inventory_manifests(
    sources: Iterable[ManifestSource] | None = None,
    *,
    skill_enabled: Callable[[str], bool] | None = None,
) -> ManifestInventory:
    """Enumerate manifests and report defects without changing any source."""
    selected_sources = (
        default_manifest_sources() if sources is None else tuple(sources)
    )
    enabled = skill_enabled or _default_skill_enabled
    manifests: list[ManifestRef] = []
    problems: list[InventoryProblem] = []
    physical_paths: dict[tuple[int, int], ManifestRef] = {}
    contract_ids: dict[ContractId, ManifestRef] = {}

    for source in selected_sources:
        root = Path(source.root)
        for path in _iter_source_paths(source):
            relative = path.relative_to(root).as_posix()
            if _path_has_symlink(root, path):
                problems.append(InventoryProblem(
                    "symlink", str(path),
                    "manifest or one of its parent components is a symlink",
                    source.origin,
                ))
                continue
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError as exc:
                problems.append(InventoryProblem(
                    "unreadable", str(path), str(exc), source.origin,
                ))
                continue
            if not path.is_file() or not os.path.isfile(path):
                problems.append(InventoryProblem(
                    "not_regular", str(path), "manifest is not a regular file",
                    source.origin,
                ))
                continue
            physical_key = (int(stat.st_dev), int(stat.st_ino))
            if physical_key in physical_paths:
                original = physical_paths[physical_key]
                problems.append(InventoryProblem(
                    "alias", str(path),
                    f"same file already inventoried as {original.manifest_path}",
                    source.origin, (str(original.contract_id),),
                ))
                continue
            try:
                raw = path.read_bytes()
                parsed = tomllib.loads(raw.decode("utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
                problems.append(InventoryProblem(
                    "parse_error", str(path), str(exc), source.origin,
                ))
                continue
            raw_name = parsed.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                problems.append(InventoryProblem(
                    "missing_name", str(path), "manifest has no non-empty name",
                    source.origin,
                ))
                continue
            contract_id = ContractId(source.origin, relative)
            if contract_id in contract_ids:
                original = contract_ids[contract_id]
                problems.append(InventoryProblem(
                    "duplicate_contract_id", str(path),
                    f"contract id already assigned to {original.manifest_path}",
                    source.origin, (str(contract_id),),
                ))
                continue
            lifecycle = str(parsed.get("lifecycle") or "active")
            status = source.default_status
            skill_name = None
            if source.skill_scoped:
                skill_name = Path(relative).parts[0]
                try:
                    if not enabled(skill_name):
                        status = ManifestStatus.DISABLED
                except Exception as exc:  # fail closed only for the status view
                    status = ManifestStatus.DISABLED
                    problems.append(InventoryProblem(
                        "skill_status_error", str(path), str(exc), source.origin,
                    ))
            if lifecycle in {"retired", "disabled"}:
                status = (
                    ManifestStatus.RETIRED
                    if lifecycle == "retired" else ManifestStatus.DISABLED
                )
            allowed_code_roots = tuple(
                Path(item).resolve(strict=False)
                for item in source.allowed_code_roots
            )
            if source.skill_scoped:
                # A skill is its own code boundary.  A broad catalog root is
                # useful for discovery but must never authorize one bundle to
                # resolve code from a sibling bundle.
                bundle_root = (root / Path(relative).parts[0]).resolve(strict=False)
                allowed_code_roots = (bundle_root,)
            if not allowed_code_roots:
                allowed_code_roots = (path.parent.resolve(strict=False),)
            ref = ManifestRef(
                contract_id=contract_id,
                origin=source.origin,
                status=status,
                source_root=root,
                manifest_path=path,
                manifest_relative=relative,
                manifest_hash="sha256:" + hashlib.sha256(raw).hexdigest(),
                name=raw_name.strip(),
                lifecycle=lifecycle,
                skill_name=skill_name,
                allowed_code_roots=allowed_code_roots,
            )
            manifests.append(ref)
            physical_paths[physical_key] = ref
            contract_ids[contract_id] = ref

    by_name: dict[str, list[ManifestRef]] = {}
    for ref in manifests:
        if ref.status is ManifestStatus.ADMITTED:
            assert ref.name is not None
            by_name.setdefault(ref.name, []).append(ref)
    for name, refs in sorted(by_name.items()):
        if len(refs) < 2:
            continue
        problems.append(InventoryProblem(
            "name_collision", name,
            "multiple admitted manifests declare the same executor name",
            contracts=tuple(sorted(str(ref.contract_id) for ref in refs)),
        ))

    manifests.sort(key=lambda item: (item.origin.value, item.manifest_relative))
    problems.sort(key=lambda item: (item.code, item.path, item.detail))
    return ManifestInventory(tuple(manifests), tuple(problems))


__all__ = [
    "ContractId",
    "InventoryProblem",
    "ManifestInventory",
    "ManifestOrigin",
    "ManifestRef",
    "ManifestSource",
    "ManifestStatus",
    "default_manifest_sources",
    "inventory_manifests",
]
