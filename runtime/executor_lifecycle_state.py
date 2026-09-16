"""One reader for the restrictions that shape the loaded catalog.

Two stores can answer "is this executor restricted?". The name-based aging
store is the only identity an unmigrated installation has. After the migration
the epoch store answers by the exact contract and generation the catalog
actually loaded, so a restriction can never reach a sibling generation that
merely shares a name, and a name can never revive one that was restricted.

Choosing between them is not a per-module decision: every reader of executor
lifecycle state resolves the owner here, once per catalog load.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable

from executor_birth_activation_mode import BirthStateOwner, read_birth_activation_state


# One marker for "the lifecycle owner restricted this on purpose", shared by
# the loader that writes it and the cutover guard that reads it back. A load
# failure and a deliberate restriction must never be told apart by prose.
RESTRICTED_REJECT_PREFIX = "restricted by the lifecycle owner"


class Restriction(str, Enum):
    """What the owning store says about one loaded generation."""

    DEMOTED = "deprecated"   # stays in the catalog, ranked as deprecated
    REMOVED = "archived"     # leaves the catalog entirely


def _epoch_db_path() -> Path:
    import config

    return Path(config.PATH_USER_STATE) / "birth" / "executor_epochs.sqlite"


def _epoch_restrictions(executors) -> dict[str, tuple[Restriction, str]]:
    """Answer from the exact generations this catalog load produced."""
    from executor_birth_epoch_store import BirthLifecycle, EpochState, read_epochs
    from manifest_inventory import ContractId, ManifestOrigin

    db_path = _epoch_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    keys, by_key = [], {}
    for executor in executors:
        raw, generation_id = getattr(executor, "contract_id", None), getattr(
            executor, "generation_id", None)
        if not isinstance(raw, str) or raw.count(":") != 1 or not isinstance(generation_id, str):
            # A loaded executor without its exact identity cannot be checked
            # against the owning store, so it cannot be admitted either.
            by_key[executor.name] = None
            continue
        origin, relative = raw.split(":", 1)
        contract_id = ContractId(ManifestOrigin(origin), relative)
        keys.append((contract_id, generation_id))
        by_key[executor.name] = (contract_id.value, generation_id)
    found = read_epochs(keys, db_path=db_path)
    restricted: dict[str, tuple[Restriction, str]] = {}
    for name, key in by_key.items():
        record = found.get(key) if key is not None else None
        if record is None:
            restricted[name] = (Restriction.REMOVED, "no epoch for the loaded generation")
            continue
        if (record.lifecycle in {BirthLifecycle.QUARANTINED, BirthLifecycle.ARCHIVED}
                or record.state is not EpochState.CURRENT):
            restricted[name] = (Restriction.REMOVED, record.lifecycle.value)
        elif record.lifecycle is BirthLifecycle.DEPRECATED:
            restricted[name] = (Restriction.DEMOTED, record.lifecycle.value)
    return restricted


def _aging_restrictions(*, read_only: bool) -> dict[str, tuple[Restriction, str]]:
    """Answer from the name-based store, the only identity it has."""
    from executor_aging import lifecycle_override_map

    return {
        name: ((Restriction.REMOVED, "inactive too long") if state == "archived"
               else (Restriction.DEMOTED, state))
        for name, state in lifecycle_override_map(read_only=read_only).items()
    }


def register_loaded_executors(executors: Iterable[object]) -> int:
    """Record newly discovered executors in the owning store, if it has one.

    The name-based store learns an executor the first time a catalog load sees
    it, because a name is all it can key on. The epoch owner records a
    generation when Birth publishes it and never from a catalog load: a name
    observed while loading is not an admission, and inventing a row here is
    exactly the name-based association the migration removed.
    """
    if read_birth_activation_state().owner is not BirthStateOwner.LEGACY:
        return 0
    from executor_aging import register

    recorded = 0
    for executor in executors:
        source = getattr(executor, "source", "")
        if source == "imported":
            tag = "skill"
        elif source == "synthesized":
            tag = "synth:reactive"
        else:
            tag = "handcrafted"
        try:
            register(executor.name, source=tag)
        except Exception:
            continue
        recorded += 1
    return recorded


def catalog_restrictions(
    executors: Iterable[object], *, read_only: bool,
) -> dict[str, tuple[Restriction, str]]:
    """Return the restricted executors of this catalog load, by owning store."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        return _aging_restrictions(read_only=read_only)
    return _epoch_restrictions(executors)


__all__ = ["RESTRICTED_REJECT_PREFIX", "Restriction", "catalog_restrictions",
           "register_loaded_executors"]
