"""Neutral, deterministic inventory of executor manifest sources.

Discovery is deliberately separate from admission, signing and publication.
Finding a manifest here never makes it executable or writable.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

import config as _C
from contract_bootstrap import (
    ACTIVE_BYTES,
    ACTIVE_RELATIVE,
    STORE_RELATIVE,
    BootstrapStateError,
    ProductionStoreMode,
    classify_production_store,
)


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


class ManifestLayout(str, Enum):
    AUTHORING = "authoring"
    STORE_ONLY = "store_only"


_REPOSITORY_AUTHORING_ORIGINS = frozenset({
    ManifestOrigin.CORE,
    ManifestOrigin.BUILTIN,
    ManifestOrigin.BUILTIN_SKILL,
    ManifestOrigin.RETIRED,
})
_STORE_AUTHORING_RELATIVE = Path("contract-authoring") / "v1"


class ManifestBootstrapError(RuntimeError):
    """Fail-closed error at the irreversible publication boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


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

    def installed(self) -> tuple[ManifestRef, ...]:
        """Return contracts installed in this topology, enabled or disabled.

        Enablement is external visibility policy.  Only an explicit retired
        status removes a contract from the install/publish census.
        """
        return tuple(
            item for item in self.manifests
            if item.status is not ManifestStatus.RETIRED
        )


def manifest_name_collisions(
    entries: Iterable[tuple[ContractId | str, str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return deterministic duplicate executor names across contract IDs.

    The helper is deliberately independent from discovery and publication so
    candidate-policy checks and the live loader apply exactly the same global
    uniqueness rule.  Empty or malformed names are rejected by the caller's
    schema/authentication boundary and are not silently normalized here.
    """
    by_name: dict[str, set[str]] = {}
    for contract_id, name in entries:
        by_name.setdefault(name, set()).add(str(contract_id))
    return tuple(
        (name, tuple(sorted(contract_ids)))
        for name, contract_ids in sorted(by_name.items())
        if len(contract_ids) > 1
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


def _store_authoring_sources(
    sources: Iterable[ManifestSource],
) -> tuple[ManifestSource, ...]:
    """Rebase repository-owned authoring outside the immutable release.

    Store-only readers authenticate code through the current generation, but
    technical updates still need a recoverable canonical authoring tree.  A
    closed release cannot be that tree: changing it would invalidate the
    distribution at the next preflight.  User-owned origins are already
    outside the release and retain their existing roots.
    """
    result: list[ManifestSource] = []
    for source in sources:
        if source.origin not in _REPOSITORY_AUTHORING_ORIGINS:
            result.append(source)
            continue
        root = (
            _C.PATH_USER_STATE / _STORE_AUTHORING_RELATIVE
            / source.origin.value
        )
        result.append(ManifestSource(
            source.origin,
            root,
            min_depth=source.min_depth,
            max_depth=source.max_depth,
            default_status=source.default_status,
            skill_scoped=source.skill_scoped,
            allowed_code_roots=(root,),
        ))
    return tuple(result)


def _publication_paths(
    *,
    store_root: Path | str | None,
    active_marker: Path | str | None,
) -> tuple[Path, Path]:
    version_root = (
        Path(store_root)
        if store_root is not None
        else _C.PATH_USER_STATE / STORE_RELATIVE
    )
    marker = (
        Path(active_marker)
        if active_marker is not None
        else _C.PATH_USER_STATE / ACTIVE_RELATIVE
    )
    return version_root, marker


def _has_reparse_flag(status: os.stat_result) -> bool:
    return bool(
        getattr(status, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_plain_directory(path: Path, *, code: str) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise ManifestBootstrapError(code, f"{path}: {exc}") from exc
    if (
        _has_reparse_flag(status)
        or stat.S_ISLNK(status.st_mode)
        or not stat.S_ISDIR(status.st_mode)
    ):
        raise ManifestBootstrapError(code, str(path))


def resolve_manifest_layout(
    *,
    store_root: Path | str | None = None,
    active_marker: Path | str | None = None,
) -> ManifestLayout:
    """Apply the one-way bootstrap matrix without consulting authoring.

    ``store_root`` is the version directory (``.../contract-publications/v1``).
    Presence of its parent is the global production-root boundary.  Broken
    links and wrong file types count as presence and therefore fail closed.
    """
    version_root, marker = _publication_paths(
        store_root=store_root,
        active_marker=active_marker,
    )
    try:
        state = classify_production_store(
            version_root=version_root,
            active_marker=marker,
            active_bytes=ACTIVE_BYTES,
        )
    except BootstrapStateError as exc:
        code = (
            "store_root_invalid"
            if exc.code == "production_store_invalid"
            else exc.code
        )
        raise ManifestBootstrapError(code, exc.detail) from exc
    if state.mode is ProductionStoreMode.LEGACY:
        return ManifestLayout.AUTHORING
    if state.mode in {ProductionStoreMode.ACTIVE, ProductionStoreMode.STORE_ONLY}:
        return ManifestLayout.STORE_ONLY
    raise ManifestBootstrapError(
        state.recovery_code or "production_store_incomplete",
        state.detail,
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


def inventory_authoring_manifests(
    sources: Iterable[ManifestSource] | None = None,
    *,
    skill_enabled: Callable[[str], bool] | None = None,
) -> ManifestInventory:
    """Explicitly enumerate authoring manifests for lint/migration tools."""
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

    installed_names = (
        (ref.contract_id, ref.name)
        for ref in manifests
        if ref.status is not ManifestStatus.RETIRED and ref.name is not None
    )
    for name, contract_ids in manifest_name_collisions(installed_names):
        problems.append(InventoryProblem(
            "name_collision", name,
            "multiple installed manifests declare the same executor name",
            contracts=contract_ids,
        ))

    manifests.sort(key=lambda item: (item.origin.value, item.manifest_relative))
    problems.sort(key=lambda item: (item.code, item.path, item.detail))
    return ManifestInventory(tuple(manifests), tuple(problems))


def _structural_location(
    source: ManifestSource,
    contract_id: ContractId,
) -> tuple[Path, Path, str | None, tuple[Path, ...]]:
    relative = Path(contract_id.relative_manifest)
    depth = len(relative.parts) - 1
    if depth < source.min_depth or (
        source.max_depth is not None and depth > source.max_depth
    ):
        raise ValueError("contract path is outside the origin topology")
    root = Path(source.root)
    skill_name = relative.parts[0] if source.skill_scoped else None
    allowed_code_roots = tuple(
        Path(item).resolve(strict=False) for item in source.allowed_code_roots
    )
    if source.skill_scoped:
        allowed_code_roots = (
            (root / relative.parts[0]).resolve(strict=False),
        )
    manifest_path = root / relative
    if not allowed_code_roots:
        allowed_code_roots = (manifest_path.parent.resolve(strict=False),)
    return root, manifest_path, skill_name, allowed_code_roots


def inventory_store_manifests(
    sources: Iterable[ManifestSource] | None = None,
    *,
    store_root: Path | str | None = None,
    skill_enabled: Callable[[str], bool] | None = None,
    binding_reader: Callable[[Path], object] | None = None,
) -> ManifestInventory:
    """Build structural refs from immutable bindings and the origin map.

    This path deliberately performs no existence, metadata or content read on
    ``ManifestRef.manifest_path``.  The returned nullable authoring
    observations remain empty; only ``current_manifest()`` may supply live
    manifest content after cutover.
    """
    selected_sources = (
        _store_authoring_sources(default_manifest_sources())
        if sources is None else tuple(sources)
    )
    if binding_reader is None:
        from contract_store import read_binding as binding_reader

    version_root, _marker = _publication_paths(
        store_root=store_root,
        active_marker=None,
    )
    _require_plain_directory(version_root, code="store_version_invalid")
    enabled = skill_enabled or _default_skill_enabled
    manifests: list[ManifestRef] = []
    problems: list[InventoryProblem] = []

    grouped_sources: dict[ManifestOrigin, list[ManifestSource]] = {}
    for source in selected_sources:
        grouped_sources.setdefault(source.origin, []).append(source)
    source_map: dict[ManifestOrigin, ManifestSource] = {}
    for origin, matches in sorted(grouped_sources.items(), key=lambda item: item[0].value):
        if len(matches) != 1:
            problems.append(InventoryProblem(
                "origin_map_duplicate",
                origin.value,
                "origin map must contain exactly one source per origin",
                origin,
            ))
            continue
        source_map[origin] = matches[0]

    seen_contracts: set[ContractId] = set()
    try:
        entries = tuple(sorted(version_root.iterdir(), key=lambda item: item.name))
    except OSError as exc:
        raise ManifestBootstrapError(
            "store_version_unreadable", f"{version_root}: {exc}",
        ) from exc
    for contract_dir in entries:
        try:
            binding = binding_reader(contract_dir)
            contract_id = getattr(binding, "contract_id")
            if not isinstance(contract_id, ContractId):
                raise TypeError("binding returned no ContractId")
        except Exception as exc:
            problems.append(InventoryProblem(
                "binding_invalid",
                str(contract_dir),
                str(exc),
            ))
            continue
        if contract_id in seen_contracts:
            problems.append(InventoryProblem(
                "duplicate_contract_id",
                str(contract_dir),
                "contract id appears in more than one binding",
                contract_id.origin,
                (str(contract_id),),
            ))
            continue
        seen_contracts.add(contract_id)
        source = source_map.get(contract_id.origin)
        if source is None:
            problems.append(InventoryProblem(
                "origin_unknown",
                str(contract_dir),
                "binding origin is absent or ambiguous in the origin map",
                contract_id.origin,
                (str(contract_id),),
            ))
            continue
        try:
            root, manifest_path, skill_name, allowed_code_roots = (
                _structural_location(source, contract_id)
            )
        except ValueError as exc:
            problems.append(InventoryProblem(
                "binding_invalid",
                str(contract_dir),
                str(exc),
                contract_id.origin,
                (str(contract_id),),
            ))
            continue

        status = source.default_status
        if skill_name is not None:
            try:
                if not enabled(skill_name):
                    status = ManifestStatus.DISABLED
            except Exception as exc:
                status = ManifestStatus.DISABLED
                problems.append(InventoryProblem(
                    "skill_status_error",
                    str(contract_dir),
                    str(exc),
                    contract_id.origin,
                    (str(contract_id),),
                ))
        manifests.append(ManifestRef(
            contract_id=contract_id,
            origin=contract_id.origin,
            status=status,
            source_root=root,
            manifest_path=manifest_path,
            manifest_relative=contract_id.relative_manifest,
            allowed_code_roots=allowed_code_roots,
            skill_name=skill_name,
        ))

    manifests.sort(key=lambda item: (item.origin.value, item.manifest_relative))
    problems.sort(key=lambda item: (item.code, item.path, item.detail))
    return ManifestInventory(tuple(manifests), tuple(problems))


def manifest_ref_for_source_path(
    inventory: ManifestInventory,
    manifest_path: Path | str,
) -> ManifestRef:
    """Resolve one exact structural source path without reading that path."""
    wanted = os.path.normcase(os.path.abspath(os.fspath(manifest_path)))
    matches = tuple(
        ref for ref in inventory.manifests
        if os.path.normcase(os.path.abspath(os.fspath(ref.manifest_path))) == wanted
    )
    if len(matches) != 1:
        raise KeyError(f"manifest source path has {len(matches)} matches: {manifest_path}")
    return matches[0]


def inventory_manifests(
    sources: Iterable[ManifestSource] | None = None,
    *,
    skill_enabled: Callable[[str], bool] | None = None,
    store_root: Path | str | None = None,
    active_marker: Path | str | None = None,
) -> ManifestInventory:
    """Return the live inventory selected by the irreversible matrix."""
    layout = resolve_manifest_layout(
        store_root=store_root,
        active_marker=active_marker,
    )
    if layout is ManifestLayout.AUTHORING:
        return inventory_authoring_manifests(
            sources,
            skill_enabled=skill_enabled,
        )
    return inventory_store_manifests(
        sources,
        store_root=store_root,
        skill_enabled=skill_enabled,
    )


__all__ = [
    "ContractId",
    "InventoryProblem",
    "ManifestBootstrapError",
    "ManifestInventory",
    "ManifestLayout",
    "ManifestOrigin",
    "ManifestRef",
    "ManifestSource",
    "ManifestStatus",
    "default_manifest_sources",
    "inventory_authoring_manifests",
    "inventory_manifests",
    "inventory_store_manifests",
    "manifest_name_collisions",
    "manifest_ref_for_source_path",
    "resolve_manifest_layout",
]
