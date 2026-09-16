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
        elif record.lifecycle_override is BirthLifecycle.ARCHIVED:
            restricted[name] = (Restriction.REMOVED,
                                record.override_reason or "inactive too long")
        elif record.lifecycle_override is BirthLifecycle.DEPRECATED:
            restricted[name] = (Restriction.DEMOTED,
                                record.override_reason or "inactive too long")
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


def _catalog_executor(executor_name: str):
    """Resolve one name through the authenticated catalog, or nothing."""
    try:
        from loader import load_catalog

        return load_catalog(verify=True, lang="en").get(executor_name)
    except Exception as ex:
        log.warning("lifecycle state: catalog unavailable for %r: %r", executor_name, ex)
        return None


def credit_uses(executor_name: str, uses: int) -> int:
    """Transfer proven demand to the executor that superseded a cache row.

    The credit exists only so the inactivity decision does not restrict an heir
    while it is serving the demand its predecessor proved. A cache row knows a
    name, so under the epoch owner the name is resolved through the
    authenticated catalog to one exact selectable generation; an absent or
    ambiguous name credits nothing rather than guessing a sibling.
    """
    if not executor_name or uses <= 0:
        return 0
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        from executor_aging import touch

        for _ in range(int(uses)):
            touch(executor_name)
        return int(uses)
    from executor_birth_epoch_store import (
        EpochStoreError, credit_uses as credit_epoch_uses, read_current_epoch,
    )

    executor = _catalog_executor(executor_name)
    identity = _exact_identity(executor) if executor is not None else None
    if identity is None:
        log.warning("lifecycle state: inherited uses for %r not credited; "
                    "no authenticated generation", executor_name)
        return 0
    contract_id, generation_id = identity
    db_path = _epoch_db_path()
    try:
        current = read_current_epoch(contract_id=contract_id, db_path=db_path)
        if current is None or current.generation_id != generation_id:
            return 0
        return credit_epoch_uses(
            contract_id=contract_id, generation_id=generation_id,
            expected_version=current.state_version, uses=int(uses),
            occurred_at=_utc_now(), db_path=db_path,
        )
    except (EpochStoreError, OSError) as ex:
        log.warning("lifecycle state: inherited uses for %s not credited: %r",
                    executor_name, ex)
        return 0


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


def apply_inactivity_decay(
    *, deprecate_days: int | None = None, archive_days: int | None = None,
    now_iso: str | None = None, catalog_names: list[str] | None = None,
) -> dict:
    """Run the one inactivity decision against the store that owns it."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        from executor_aging import apply_executor_ager

        return apply_executor_ager(
            deprecate_days=deprecate_days, archive_days=archive_days,
            now_iso=now_iso, catalog_names=catalog_names,
        )
    return _epoch_inactivity_decay(
        deprecate_days=deprecate_days, archive_days=archive_days,
        now_iso=now_iso, catalog_names=catalog_names,
    )


def _epoch_inactivity_decay(
    *, deprecate_days: int | None, archive_days: int | None,
    now_iso: str | None, catalog_names: list[str] | None,
) -> dict:
    """Restrict selectable epochs nobody has needed for long enough.

    The thresholds, the exclusions and the two steps are the ones the
    name-based decision already used: protected capabilities and everything
    that is not synthesized never decay, because a rare use is not
    obsolescence and silently retiring a curated capability misroutes to a
    sibling. What changes is the subject: the decision is recorded against the
    exact generation that was idle, beside its signed lifecycle, and stays
    reversible.
    """
    from executor_aging import (
        ARCHIVED_DAYS, DEPRECATED_DAYS, PROTECTED_NAMES, _days_between_iso,
        _is_synth, now_iso_z,
    )
    from executor_birth_epoch_store import (
        BirthLifecycle, EpochStoreError, restrict_generation, selectable_epochs,
    )
    from manifest_inventory import ContractId, ManifestOrigin

    deprecate_days = DEPRECATED_DAYS if deprecate_days is None else deprecate_days
    archive_days = ARCHIVED_DAYS if archive_days is None else archive_days
    observed_at = now_iso or now_iso_z()
    report: dict = {
        "deprecated": [], "archived": [], "protected_skipped": 0,
        "handcrafted_skipped": 0, "already_deprecated": 0, "already_archived": 0,
        "total_seen": 0, "conflicts": 0,
        "thresholds": {"deprecate_days": deprecate_days, "archive_days": archive_days},
    }
    db_path = _epoch_db_path()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    for record in selectable_epochs(db_path=db_path):
        if catalog_names is not None and record.name not in catalog_names:
            continue
        report["total_seen"] += 1
        if record.name in PROTECTED_NAMES:
            report["protected_skipped"] += 1
            continue
        if not _is_synth(record.name, record.source):
            report["handcrafted_skipped"] += 1
            continue
        if record.lifecycle_override is BirthLifecycle.ARCHIVED:
            report["already_archived"] += 1
            continue
        if record.lifecycle_override is BirthLifecycle.DEPRECATED:
            report["already_deprecated"] += 1
            started = record.inactivity_since
            if started is None or _days_between_iso(observed_at, started) < archive_days:
                continue
            target, reason = BirthLifecycle.ARCHIVED, "inactive too long"
        else:
            anchor = record.last_used_at or record.first_seen_at
            if anchor is None or _days_between_iso(observed_at, anchor) < deprecate_days:
                continue
            target, reason = BirthLifecycle.DEPRECATED, "inactive too long"
        origin, relative = record.contract_id.split(":", 1)
        try:
            restrict_generation(
                contract_id=ContractId(ManifestOrigin(origin), relative),
                generation_id=record.generation_id,
                expected_version=record.state_version, override=target,
                reason=reason, observed_at=observed_at, db_path=db_path,
            )
        except (EpochStoreError, ValueError) as ex:
            # A transition or another pass won the race. The next run observes
            # the settled state; nothing is forced here.
            log.warning("lifecycle state: inactivity decision on %s skipped: %r",
                        record.name, ex)
            report["conflicts"] += 1
            continue
        report["archived" if target is BirthLifecycle.ARCHIVED else "deprecated"].append(
            record.name)
    return report


def recorded_source(executor_name: str) -> str | None:
    """Return the provenance the owning store recorded for an executor."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        from executor_aging import lookup

        stat = lookup(executor_name)
        return None if stat is None else stat.source
    from executor_birth_epoch_store import EpochStoreError, read_current_epoch

    executor = _catalog_executor(executor_name)
    identity = _exact_identity(executor) if executor is not None else None
    if identity is None:
        return None
    try:
        current = read_current_epoch(contract_id=identity[0], db_path=_epoch_db_path())
    except (EpochStoreError, OSError) as ex:
        log.warning("lifecycle state: provenance of %s unreadable: %r", executor_name, ex)
        return None
    if current is None or current.generation_id != identity[1]:
        return None
    return current.source


def restrict_executor(executor_name: str, *, restriction: Restriction,
                      reason: str) -> bool:
    """Restrict one executor locally, through the store that owns the decision.

    Marking a duplicate, retiring an idle capability and closing a rolled-back
    creation are the same kind of local decision, so they share one reversible
    record and one owner. Only the reason differs, and it is kept.
    """
    archived = restriction is Restriction.REMOVED
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        import sqlite3
        import time

        from executor_aging import DB_PATH

        if not DB_PATH.exists():
            return False
        column = "archived_at" if archived else "deprecated_at"
        try:
            with sqlite3.connect(str(DB_PATH), timeout=10.0) as connection:
                changed = connection.execute(
                    f"UPDATE executor_stats SET {column}=? "
                    f"WHERE name=? AND {column} IS NULL",
                    (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), executor_name),
                ).rowcount
            return bool(changed)
        except sqlite3.Error as ex:
            log.warning("lifecycle state: %s not restricted: %r", executor_name, ex)
            return False
    from executor_birth_epoch_store import (
        BirthLifecycle, EpochStoreError, read_current_epoch, restrict_generation,
    )

    executor = _catalog_executor(executor_name)
    identity = _exact_identity(executor) if executor is not None else None
    if identity is None:
        log.warning("lifecycle state: %r cannot be restricted; no selectable generation",
                    executor_name)
        return False
    contract_id, generation_id = identity
    target = BirthLifecycle.ARCHIVED if archived else BirthLifecycle.DEPRECATED
    db_path = _epoch_db_path()
    try:
        current = read_current_epoch(contract_id=contract_id, db_path=db_path)
        if (current is None or current.generation_id != generation_id
                or current.lifecycle_override is target):
            return False
        restrict_generation(
            contract_id=contract_id, generation_id=generation_id,
            expected_version=current.state_version, override=target,
            reason=reason, observed_at=_utc_now(), db_path=db_path,
        )
        return True
    except (EpochStoreError, OSError) as ex:
        log.warning("lifecycle state: %s not restricted: %r", executor_name, ex)
        return False


def revive_executor(executor_name: str, *, reason: str) -> bool:
    """Undo a local restriction on one executor, through its owner."""
    if read_birth_activation_state().owner is BirthStateOwner.LEGACY:
        from executor_aging import undeprecate

        return bool(undeprecate(executor_name))
    from executor_birth_epoch_store import (
        EpochStoreError, clear_restriction, read_current_epoch,
    )

    executor = _catalog_executor(executor_name)
    identity = _exact_identity(executor) if executor is not None else None
    if identity is None:
        log.warning("lifecycle state: %r cannot be revived; no authenticated generation",
                    executor_name)
        return False
    contract_id, generation_id = identity
    db_path = _epoch_db_path()
    try:
        current = read_current_epoch(contract_id=contract_id, db_path=db_path)
        if current is None or current.generation_id != generation_id:
            return False
        if current.lifecycle_override is None:
            return False
        clear_restriction(
            contract_id=contract_id, generation_id=generation_id,
            expected_version=current.state_version, reason=reason,
            observed_at=_utc_now(), db_path=db_path,
        )
        return True
    except (EpochStoreError, OSError) as ex:
        log.warning("lifecycle state: %s not revived: %r", executor_name, ex)
        return False


__all__ = ["RESTRICTED_REJECT_PREFIX", "Restriction", "apply_inactivity_decay",
           "catalog_restrictions", "credit_uses", "record_invocation",
           "recorded_source", "register_loaded_executors", "restrict_executor",
           "revive_executor"]
