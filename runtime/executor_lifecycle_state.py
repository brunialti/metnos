"""One reader for the restrictions that shape the loaded catalog.

The aging store owns executor lifecycle state. An executor is active,
deprecated (kept in the catalog, ranked lower) or archived (out of the
catalog). The loader, the change applier and the nightly aging read and write
that state through this module. A restriction is recorded by name and holds
for the revision that earned it: a new revision admitted under the same name
starts unrestricted (`register_loaded_executors`).
"""
from __future__ import annotations

from enum import Enum
import sqlite3
from typing import Iterable

from logging_setup import get_logger


log = get_logger(__name__)


# One marker for "the lifecycle owner restricted this on purpose", shared by
# the loader that writes it and the cutover guard that reads it back. A load
# failure and a deliberate restriction must never be told apart by prose.
RESTRICTED_REJECT_PREFIX = "restricted by the lifecycle owner"


class Restriction(str, Enum):
    """What the owning store says about one loaded executor."""

    DEMOTED = "deprecated"   # stays in the catalog, ranked as deprecated
    REMOVED = "archived"     # leaves the catalog entirely


def restriction_snapshot() -> tuple[tuple[str, str, str], ...]:
    """Read the restrictions once, as ``(name, restriction, reason)`` rows.

    The snapshot is semantic and never a modification time: invocation
    accounting shares the same database, and its mtime changes on every
    recorded call, so a time-based signature invalidates every cache and makes
    bounded authentication look unstable under ordinary traffic. Reading once
    also lets the caller apply exactly what it signed, rather than rereading
    and racing a revival.
    """
    from executor_aging import lifecycle_override_map

    return tuple(sorted(
        (name,
         Restriction.REMOVED.value if state == "archived" else Restriction.DEMOTED.value,
         "inactive too long" if state == "archived" else state)
        for name, state in lifecycle_override_map(read_only=True).items()
    ))


def restrictions_from_snapshot(
    executors: Iterable[object],
    snapshot: Iterable[tuple[str, str, str]],
) -> dict[str, tuple[Restriction, str]]:
    """Decide this catalog load's restrictions from one taken snapshot."""
    loaded = {executor.name for executor in executors}
    return {
        name: (Restriction(restriction), reason)
        for name, restriction, reason in snapshot if name in loaded
    }


def _source_tag(executor: object) -> str:
    source = getattr(executor, "source", "")
    if source == "imported":
        return "skill"
    if source == "synthesized":
        return "synth:reactive"
    return "handcrafted"


def _generation(executor: object) -> str:
    """What changes when a new revision of the executor is admitted."""
    return str(getattr(executor, "generation_id", None)
               or getattr(executor, "digest", None) or "")


def register_loaded_executors(executors: Iterable[object]) -> int:
    """Let the aging store learn the names and revisions this load produced.

    A restriction earned by an earlier revision is lifted here when a new one
    arrives under the same name; the load that notices it still applies the
    snapshot it signed, the next one sees the executor unrestricted.
    """
    from executor_aging import note_generation, register

    recorded = 0
    for executor in executors:
        try:
            register(executor.name, source=_source_tag(executor))
            if note_generation(executor.name, _generation(executor)):
                log.info("lifecycle state: %s is a new revision, restriction lifted",
                         executor.name)
        except Exception:
            continue
        recorded += 1
    return recorded


def credit_uses(executor_name: str, uses: int) -> int:
    """Transfer proven demand to the executor that superseded a cache row.

    The credit exists only so the inactivity decision does not restrict an heir
    while it is serving the demand its predecessor proved.
    """
    if not executor_name or uses <= 0:
        return 0
    from executor_aging import touch

    for _ in range(int(uses)):
        touch(executor_name)
    return int(uses)


def record_invocation(executor: object, *, ok: bool | None) -> None:
    """Count one completed call; counters are telemetry for aging only."""
    from executor_aging import record_invocation as aging_record

    aging_record(str(getattr(executor, "name", "") or ""), ok=ok)


def apply_inactivity_decay(
    *, deprecate_days: int | None = None, archive_days: int | None = None,
    now_iso: str | None = None, catalog_names: list[str] | None = None,
) -> dict:
    """Run the one inactivity decision against the aging store."""
    from executor_aging import apply_executor_ager

    return apply_executor_ager(
        deprecate_days=deprecate_days, archive_days=archive_days,
        now_iso=now_iso, catalog_names=catalog_names,
    )


def recorded_source(executor_name: str) -> str | None:
    """Return the provenance the aging store recorded for an executor."""
    from executor_aging import lookup

    stat = lookup(executor_name)
    return None if stat is None else stat.source


def restrict_executor(executor_name: str, *, restriction: Restriction,
                      reason: str) -> bool:
    """Restrict one executor locally, through the store that owns the decision.

    Marking a duplicate, retiring an idle capability and closing a rolled-back
    creation are the same kind of local decision, so they share one reversible
    record and one owner.
    """
    import time

    from executor_aging import DB_PATH

    if not DB_PATH.exists():
        return False
    column = "archived_at" if restriction is Restriction.REMOVED else "deprecated_at"
    try:
        with sqlite3.connect(str(DB_PATH), timeout=10.0) as connection:
            changed = connection.execute(
                f"UPDATE executor_stats SET {column}=? "
                f"WHERE name=? AND {column} IS NULL",
                (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), executor_name),
            ).rowcount
        return bool(changed)
    except sqlite3.Error as ex:
        log.warning("lifecycle state: %s not restricted (%s): %r", executor_name, reason, ex)
        return False


def revive_executor(executor_name: str, *, reason: str) -> bool:
    """Undo a local restriction on one executor, through its owner."""
    from executor_aging import undeprecate

    return bool(undeprecate(executor_name, by=reason))


__all__ = ["RESTRICTED_REJECT_PREFIX", "Restriction", "apply_inactivity_decay",
           "credit_uses", "record_invocation", "recorded_source",
           "register_loaded_executors", "restrict_executor",
           "restriction_snapshot", "restrictions_from_snapshot",
           "revive_executor"]
