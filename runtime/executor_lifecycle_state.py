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
from logging_setup import get_logger


log = get_logger(__name__)


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


def _exact_identity(executor: object):
    """Return the authenticated contract and generation, or None."""
    from manifest_inventory import ContractId, ManifestOrigin

    raw = getattr(executor, "contract_id", None)
    generation_id = getattr(executor, "generation_id", None)
    if (not isinstance(raw, str) or raw.count(":") != 1
            or not isinstance(generation_id, str) or not generation_id):
        return None
    try:
        origin, relative = raw.split(":", 1)
        return ContractId(ManifestOrigin(origin), relative), generation_id
    except (TypeError, ValueError):
        return None


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_restrictions(executors) -> dict[str, tuple[Restriction, str]]:
    """Answer from the exact generations this catalog load produced."""
    from executor_birth_epoch_store import BirthLifecycle, EpochState, read_epochs

    db_path = _epoch_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    keys, by_key = [], {}
    for executor in executors:
        identity = _exact_identity(executor)
        if identity is None:
            # A loaded executor without its exact identity cannot be checked
            # against the owning store, so it cannot be admitted either.
            by_key[executor.name] = None
            continue
        keys.append(identity)
        by_key[executor.name] = (identity[0].value, identity[1])
    found = read_epochs(keys, db_path=db_path)
    restricted: dict[str, tuple[Restriction, str]] = {}
    for name, key in by_key.items():
        if key is None:
            restricted[name] = (Restriction.REMOVED, "no authenticated identity")
            continue
        record = found.get(key)
        if record is None:
            # The epoch owner has no decision about this generation yet, which
            # is not a restriction. A writable load records it first, so the
            # ordinary case never reaches here.
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


def _source_tag(executor: object) -> str:
    source = getattr(executor, "source", "")
    if source == "imported":
        return "skill"
    if source == "synthesized":
        return "synth:reactive"
    return "handcrafted"


def _aging_register(executors: Iterable[object]) -> int:
    from executor_aging import register

    recorded = 0
    for executor in executors:
        try:
            register(executor.name, source=_source_tag(executor))
        except Exception:
            continue
        recorded += 1
    return recorded


def _epoch_admit(executors: Iterable[object]) -> int:
    """Open or advance the epoch of each exact generation this load produced.

    Identity comes from the authenticated catalog, never from a name: the
    epoch is keyed by the contract and generation the loader verified. The row
    carries usage counters and the aging decision; it grants nothing, because
    execution authority stays with the signed generation in the contract
    store. An existing row for the same generation is left exactly as it is,
    so an aging decision survives every later load. Only a different signed
    generation closes it and opens a successor with fresh counters.
    """
    from executor_birth_epoch_store import (
        BirthLifecycle, EpochStoreError, open_epoch, read_current_epoch,
        replace_current_epoch,
    )

    db_path = _epoch_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    observed_at = _utc_now()
    recorded = 0
    for executor in executors:
        identity = _exact_identity(executor)
        if identity is None:
            continue
        contract_id, generation_id = identity
        try:
            lifecycle = BirthLifecycle(str(getattr(executor, "lifecycle", "") or "active"))
        except ValueError:
            log.warning("lifecycle state: %s declares an unknown lifecycle %r",
                        executor.name, getattr(executor, "lifecycle", None))
            continue
        try:
            current = read_current_epoch(contract_id=contract_id, db_path=db_path)
            if current is None:
                open_epoch(contract_id=contract_id, generation_id=generation_id,
                           name=executor.name, source=_source_tag(executor),
                           lifecycle=lifecycle, observed_at=observed_at, db_path=db_path)
            elif current.generation_id != generation_id:
                replace_current_epoch(
                    contract_id=contract_id,
                    expected_generation_id=current.generation_id,
                    expected_state_version=current.state_version,
                    generation_id=generation_id, name=executor.name,
                    source=_source_tag(executor), lifecycle=lifecycle,
                    observed_at=observed_at, db_path=db_path,
                    event_kind="generation_observed",
                )
            else:
                continue
        except EpochStoreError as ex:
            # A concurrent load or a real transition won the race. The next
            # load observes the settled state; nothing is guessed here.
            log.warning("lifecycle state: epoch for %s unchanged: %s",
                        executor.name, ex.code)
            continue
        recorded += 1
    return recorded


def register_loaded_executors(executors: Iterable[object]) -> int:
    """Let the owning store record the executors this catalog load produced."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        return _aging_register(executors)
    return _epoch_admit(executors)


def credit_uses(executor_name: str, uses: int) -> int:
    """Transfer proven demand to the executor that superseded a cache row.

    The credit exists only so the inactivity decision does not demote an heir
    while it is serving the demand its predecessor proved. Under the epoch
    owner the decision itself is not yet the epoch store's, so nothing is
    written: crediting the retired store would be a write nobody reads, and
    crediting a generation resolved from a bare name is the association the
    migration removed.
    """
    if not executor_name or uses <= 0:
        return 0
    if read_birth_activation_state().owner is not BirthStateOwner.LEGACY:
        log.warning("lifecycle state: inherited uses for %s not credited; "
                    "the epoch owner has no inactivity decision yet", executor_name)
        return 0
    from executor_aging import touch

    for _ in range(int(uses)):
        touch(executor_name)
    return int(uses)


def record_invocation(executor: object, *, ok: bool | None) -> None:
    """Count one completed call against the owning store's identity.

    Counters are telemetry for aging, not authority, so a generation that
    changed under a running call is reported and dropped rather than
    attributed to whatever is current now.
    """
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        from executor_aging import record_invocation as aging_record

        aging_record(str(getattr(executor, "name", "") or ""), ok=ok)
        return
    from executor_birth_epoch_store import (
        EpochCacheKey, EpochStoreError, read_current_epoch, record_execution,
    )

    identity = _exact_identity(executor)
    if identity is None or ok is None:
        return
    contract_id, generation_id = identity
    db_path = _epoch_db_path()
    try:
        current = read_current_epoch(contract_id=contract_id, db_path=db_path)
        if current is None or current.generation_id != generation_id:
            log.warning("lifecycle state: no selectable epoch for %s; call not counted",
                        getattr(executor, "name", ""))
            return
        record_execution(
            EpochCacheKey(contract_id, generation_id, current.lifecycle),
            expected_version=current.state_version, successful=bool(ok),
            occurred_at=_utc_now(), db_path=db_path,
        )
    except (EpochStoreError, OSError) as ex:
        log.warning("lifecycle state: call on %s not counted: %r",
                    getattr(executor, "name", ""), ex)


def catalog_restrictions(
    executors: Iterable[object], *, read_only: bool,
) -> dict[str, tuple[Restriction, str]]:
    """Return the restricted executors of this catalog load, by owning store."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        return _aging_restrictions(read_only=read_only)
    return _epoch_restrictions(executors)


__all__ = ["RESTRICTED_REJECT_PREFIX", "Restriction", "catalog_restrictions",
           "credit_uses", "record_invocation", "register_loaded_executors"]
