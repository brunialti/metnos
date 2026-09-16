"""What each preserved legacy lifecycle row means now — the decision only.

The preserved row is evidence and never changes. This module decides what that
evidence still obliges the installation to do, and it does so as a value: a
plan can be compared before and after, replayed without effects and bound into
the migration's record. The mutation belongs to the epoch store, the census and
the reads to the administrative driver.

Two rules shape every case. Historical data is never attributed to a generation
because a name matches, so counters and instants are not carried across: only a
restriction that is effective *now* is, and only onto the exact generation the
authenticated catalog currently selects. And visibility never increases in
silence: a restriction whose capability no longer resolves is kept for an
explicit disposition instead of disappearing with the reader that held it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence


LIFECYCLE_MIGRATION_DOMAIN_V1 = b"metnos.executor-birth.lifecycle-migration/v1\0"


class LifecycleMigrationError(ValueError):
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


class LegacyEffect(str, Enum):
    """The restriction a legacy row still asserts, if any."""

    NONE = "none"
    DEMOTED = "deprecated"
    REMOVED = "archived"


class Disposition(str, Enum):
    """The closed set of things the migration can conclude about one row."""

    ATTESTED = "attested"
    DISCARDED = "discarded"
    CURRENT_RESTRICTION = "current_restriction"
    PENDING_DISPOSITION = "pending_disposition"


@dataclass(frozen=True, slots=True)
class LegacyRowFacts:
    """Facts read from one preserved row; nothing here grants authority."""

    ordinal: int
    body_digest: str
    name: str | None
    effect: LegacyEffect
    open_promotion: bool = False
    # A row that records which generation it was about can be bound exactly.
    # A row that records only a name cannot, and a present-time restriction is
    # the only thing a name may still carry.
    asserted_generation_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise LifecycleMigrationError("legacy_facts_invalid", "ordinal")
        if not isinstance(self.body_digest, str) or not self.body_digest:
            raise LifecycleMigrationError("legacy_facts_invalid", "body_digest")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise LifecycleMigrationError("legacy_facts_invalid", "name")
        if not isinstance(self.effect, LegacyEffect):
            raise LifecycleMigrationError("legacy_facts_invalid", "effect")
        if type(self.open_promotion) is not bool:
            raise LifecycleMigrationError("legacy_facts_invalid", "open_promotion")
        if self.asserted_generation_id is not None and (
                not isinstance(self.asserted_generation_id, str)
                or not self.asserted_generation_id):
            raise LifecycleMigrationError(
                "legacy_facts_invalid", "asserted_generation_id")


@dataclass(frozen=True, slots=True)
class LegacyDispositionV1:
    """One decision about one preserved row, with the evidence it rests on."""

    ordinal: int
    kind: Disposition
    contract_id: str | None
    generation_id: str | None
    effect: LegacyEffect
    reason: str
    evidence_id: str

    def __post_init__(self) -> None:
        if (self.contract_id is None) != (self.generation_id is None):
            raise LifecycleMigrationError("legacy_disposition_invalid", "identity pair")
        if (self.kind in {Disposition.ATTESTED, Disposition.CURRENT_RESTRICTION}
                and self.contract_id is None):
            raise LifecycleMigrationError("legacy_disposition_invalid", "identity required")
        if (self.kind is Disposition.CURRENT_RESTRICTION
                and self.effect is LegacyEffect.NONE):
            raise LifecycleMigrationError("legacy_disposition_invalid", "empty restriction")


def _evidence_id(facts: LegacyRowFacts, kind: Disposition,
                 identity: tuple[str, str] | None) -> str:
    payload = json.dumps({
        "body_digest": facts.body_digest,
        "contract_id": None if identity is None else identity[0],
        "effect": facts.effect.value,
        "generation_id": None if identity is None else identity[1],
        "kind": kind.value,
        "ordinal": facts.ordinal,
        "row_generation_id": facts.asserted_generation_id,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(
        LIFECYCLE_MIGRATION_DOMAIN_V1 + payload).hexdigest()


def _decide(facts: LegacyRowFacts,
            selectable: Mapping[str, tuple[str, str]]) -> LegacyDispositionV1:
    identity = None if facts.name is None else selectable.get(facts.name)
    if (identity is not None and facts.asserted_generation_id is not None
            and facts.asserted_generation_id != identity[1]):
        # The row is about a generation that is no longer the selected one.
        # Binding it to the successor because the name matches is exactly the
        # association this migration exists to avoid.
        identity = None
    if facts.open_promotion:
        # An open grace case is neither carried nor closed here: draining it is
        # an operational decision, and guessing either way changes what a user
        # is offered.
        kind, identity, reason = (Disposition.PENDING_DISPOSITION, None,
                                 "open promotion grace awaits disposition")
    elif facts.effect is LegacyEffect.NONE:
        kind, reason = ((Disposition.ATTESTED, "no effective restriction to carry")
                        if identity is not None
                        else (Disposition.DISCARDED,
                              "no effective restriction and no selectable generation"))
    elif identity is not None:
        kind, reason = (Disposition.CURRENT_RESTRICTION,
                        "restriction effective on the selected generation")
    else:
        # The capability is not selectable, so nothing becomes visible by
        # keeping this open; inventing a generation for it would.
        kind, identity, reason = (Disposition.PENDING_DISPOSITION, None,
                                 "restriction without a selectable generation")
    effect = LegacyEffect.NONE if kind in {
        Disposition.ATTESTED, Disposition.DISCARDED} else facts.effect
    return LegacyDispositionV1(
        facts.ordinal, kind, None if identity is None else identity[0],
        None if identity is None else identity[1], effect, reason,
        _evidence_id(facts, kind, identity),
    )


def plan_dispositions(
    facts: Sequence[LegacyRowFacts], *, selectable: Mapping[str, tuple[str, str]],
) -> tuple[LegacyDispositionV1, ...]:
    """Decide every row once, in source order, with no default branch."""
    ordinals = [item.ordinal for item in facts]
    if len(set(ordinals)) != len(ordinals) or ordinals != sorted(ordinals):
        raise LifecycleMigrationError("legacy_facts_invalid", "ordinals")
    for name, identity in selectable.items():
        if (not isinstance(name, str) or not name or not isinstance(identity, tuple)
                or len(identity) != 2
                or any(not isinstance(item, str) or not item for item in identity)):
            raise LifecycleMigrationError("legacy_facts_invalid", "selectable")
    return tuple(_decide(item, selectable) for item in facts)


def plan_digest_v1(dispositions: Sequence[LegacyDispositionV1]) -> str:
    """Bind a complete plan so the record and the replay name one decision."""
    payload = json.dumps([{
        "contract_id": item.contract_id, "effect": item.effect.value,
        "evidence_id": item.evidence_id, "generation_id": item.generation_id,
        "kind": item.kind.value, "ordinal": item.ordinal, "reason": item.reason,
    } for item in dispositions], ensure_ascii=True, sort_keys=True,
        separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(
        LIFECYCLE_MIGRATION_DOMAIN_V1 + b"plan\0" + payload).hexdigest()


def statistics_facts(
    rows: Sequence[Mapping[str, object]], *, body_digests: Sequence[str],
) -> tuple[LegacyRowFacts, ...]:
    """Read the effective restriction out of preserved statistics rows.

    Archiving outranks deprecation, exactly as the reader it replaces decided,
    and a row that asserts neither asserts nothing.
    """
    if len(rows) != len(body_digests):
        raise LifecycleMigrationError("legacy_facts_invalid", "body_digests")
    facts = []
    for ordinal, (row, digest) in enumerate(zip(rows, body_digests)):
        if not isinstance(row, Mapping):
            raise LifecycleMigrationError("legacy_facts_invalid", "row")
        name = row.get("name")
        effect = (LegacyEffect.REMOVED if row.get("archived_at")
                  else LegacyEffect.DEMOTED if row.get("deprecated_at")
                  else LegacyEffect.NONE)
        facts.append(LegacyRowFacts(
            ordinal, digest,
            name if isinstance(name, str) and name else None, effect,
        ))
    return tuple(facts)


# Closed set of settled promoter states. Everything else, including a state
# this reader does not recognise, is treated as open: the retirement must not
# close a case it cannot read.
_PROMOTER_SETTLED_STATES = frozenset({
    "archived", "rolled_back", "promoted_finalized",
})


def promoter_facts(
    rows: Sequence[Mapping[str, object]], *, body_digests: Sequence[str],
) -> tuple[LegacyRowFacts, ...]:
    """Read open promotion cases out of preserved promoter rows.

    A settled case is bound to the generation the row itself records, so the
    association is exact rather than by name. An unrecognised state is treated
    as open: the retirement must not close a case it cannot read.
    """
    if len(rows) != len(body_digests):
        raise LifecycleMigrationError("legacy_facts_invalid", "body_digests")
    facts = []
    for ordinal, (row, digest) in enumerate(zip(rows, body_digests)):
        if not isinstance(row, Mapping):
            raise LifecycleMigrationError("legacy_facts_invalid", "row")
        state = row.get("state")
        name = row.get("name")
        generation = row.get("active_generation_id")
        settled = isinstance(state, str) and state in _PROMOTER_SETTLED_STATES
        facts.append(LegacyRowFacts(
            ordinal, digest,
            name if isinstance(name, str) and name else None,
            LegacyEffect.NONE, not settled,
            generation if isinstance(generation, str) and generation else None,
        ))
    return tuple(facts)


# --- the exact source, read without changing it -------------------------------

_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_SOURCE_ROWS = 100_000


@dataclass(frozen=True, slots=True)
class LegacySourceIdentityV1:
    """Exactly which file was read, so a later claim names the same bytes.

    The path alone is not the identity: two installations select different
    files for the same logical store, and the same path can be replaced between
    the census and the copy. Device and inode pin the object, the content digest
    pins what it said, and the schema digest pins how to read it.
    """

    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    schema_id: str
    content_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise LifecycleMigrationError("legacy_source_invalid", "path")
        for field in ("device", "inode", "size", "mtime_ns"):
            value = getattr(self, field)
            if type(value) is not int or isinstance(value, bool) or value < 0:
                raise LifecycleMigrationError("legacy_source_invalid", field)
        for field in ("schema_id", "content_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise LifecycleMigrationError("legacy_source_invalid", field)

    @property
    def source_id(self) -> str:
        """One digest naming this exact object and its exact content."""
        payload = json.dumps({
            "content_id": self.content_id, "device": self.device,
            "inode": self.inode, "path": self.path, "size": self.size,
        }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        return "sha256:" + hashlib.sha256(
            LIFECYCLE_MIGRATION_DOMAIN_V1 + b"source\0" + payload).hexdigest()


def _file_identity(path: Path) -> tuple[os.stat_result, str]:
    """Read the whole file once, refusing anything that is not a plain file."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_CLOEXEC", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LifecycleMigrationError("legacy_source_invalid", "not a plain file")
        if info.st_size > _MAX_SOURCE_BYTES:
            raise LifecycleMigrationError("legacy_source_invalid", "size")
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1 << 20, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        if remaining:
            raise LifecycleMigrationError("legacy_source_invalid", "short read")
        if os.fstat(descriptor).st_mtime_ns != info.st_mtime_ns:
            raise LifecycleMigrationError("legacy_source_invalid", "changed while read")
        return info, "sha256:" + digest.hexdigest()
    finally:
        os.close(descriptor)


def census_source(
    path: Path, *, tables: Sequence[str],
) -> tuple[LegacySourceIdentityV1, dict[str, tuple[Mapping[str, object], ...]]]:
    """Read one legacy store without opening it for writing or migrating it.

    The connection is read-only and query-only, so an unsupported journal or an
    older schema cannot be silently upgraded by the act of looking at it. An
    absent table is reported as absent rather than as empty: the difference
    decides whether a store was ever used.
    """
    resolved = Path(path)
    info, content_id = _file_identity(resolved)
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=1")
        declared = dict(connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        ).fetchall())
        missing = [name for name in tables if name not in declared]
        if missing:
            raise LifecycleMigrationError("legacy_source_invalid", ",".join(missing))
        schema_payload = json.dumps(
            {name: " ".join((declared[name] or "").split()) for name in sorted(tables)},
            ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        rows: dict[str, tuple[Mapping[str, object], ...]] = {}
        for name in tables:
            fetched = connection.execute(
                f"SELECT * FROM {name} LIMIT ?", (_MAX_SOURCE_ROWS + 1,)).fetchall()
            if len(fetched) > _MAX_SOURCE_ROWS:
                raise LifecycleMigrationError("legacy_source_invalid", "row budget")
            rows[name] = tuple(dict(row) for row in fetched)
    finally:
        connection.close()
    identity = LegacySourceIdentityV1(
        str(resolved), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        "sha256:" + hashlib.sha256(
            LIFECYCLE_MIGRATION_DOMAIN_V1 + b"schema\0" + schema_payload).hexdigest(),
        content_id,
    )
    return identity, rows


def source_unchanged(identity: LegacySourceIdentityV1) -> bool:
    """Say whether the exact object read earlier is still exactly that."""
    try:
        info, content_id = _file_identity(Path(identity.path))
    except (LifecycleMigrationError, OSError):
        return False
    return (info.st_dev == identity.device and info.st_ino == identity.inode
            and info.st_size == identity.size
            and info.st_mtime_ns == identity.mtime_ns
            and content_id == identity.content_id)


# --- the one-time migration, as a plan and then as its application ------------

_STATISTICS_TABLE = "executor_stats"
_PROMOTER_TABLE = "proposal_promote"
# Closed table: a legacy table is readable only by the reader written for it.
# A dynamic lookup here would let a new name pick an arbitrary function.
_TABLE_FACTS = {
    _STATISTICS_TABLE: statistics_facts,
    _PROMOTER_TABLE: promoter_facts,
}


@dataclass(frozen=True, slots=True)
class SourcePlanV1:
    """One legacy store: what it was, what it said, and what that means now."""

    identity: LegacySourceIdentityV1
    legacy_table: str
    rows: tuple[Mapping[str, object], ...]
    dispositions: tuple[LegacyDispositionV1, ...]


@dataclass(frozen=True, slots=True)
class MigrationPlanV1:
    """A complete, replayable decision about every selected legacy store."""

    sources: tuple[SourcePlanV1, ...]

    def __post_init__(self) -> None:
        keys = [(item.identity.source_id, item.legacy_table) for item in self.sources]
        if not keys or len(set(keys)) != len(keys):
            raise LifecycleMigrationError("migration_plan_invalid", "sources")

    @property
    def migration_id(self) -> str:
        """Name this exact decision over these exact sources."""
        payload = json.dumps([{
            "content_id": item.identity.content_id,
            "legacy_table": item.legacy_table,
            "plan_id": plan_digest_v1(item.dispositions),
            "schema_id": item.identity.schema_id,
            "source_id": item.identity.source_id,
        } for item in sorted(
            self.sources, key=lambda item: (item.identity.source_id, item.legacy_table))
        ], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
        return "sha256:" + hashlib.sha256(
            LIFECYCLE_MIGRATION_DOMAIN_V1 + b"migration\0" + payload).hexdigest()


def plan_migration(
    sources: Sequence[tuple[Path, str]], *, selectable: Mapping[str, tuple[str, str]],
) -> MigrationPlanV1:
    """Read every selected store once and decide, without writing anything.

    Reading and deciding are one pass so the plan and the rows it decided about
    are the same observation. Applying it re-checks that the stores have not
    moved since.
    """
    from executor_birth_epoch_store import _encode_legacy_rows

    planned = []
    for path, legacy_table in sources:
        try:
            reader = _TABLE_FACTS[legacy_table]
        except KeyError as exc:
            raise LifecycleMigrationError("migration_plan_invalid", legacy_table) from exc
        identity, rows = census_source(path, tables=(legacy_table,))
        table_rows = rows[legacy_table]
        digests = [
            "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
            for _name, body in _encode_legacy_rows(table_rows)
        ]
        facts = reader(table_rows, body_digests=digests)
        planned.append(SourcePlanV1(
            identity, legacy_table, table_rows,
            plan_dispositions(facts, selectable=selectable),
        ))
    return MigrationPlanV1(tuple(planned))


def apply_migration(
    plan: MigrationPlanV1, *, epoch_db_path: Path, applied_at: str,
) -> dict:
    """Preserve, decide, restrict — in that order, and idempotently.

    Preservation precedes every decision so a crash leaves the evidence and not
    a conclusion without it. Restrictions come last because they are the only
    step a user can observe, and they are applied through the same owner an
    ordinary decision uses, so a generation that moved meanwhile is skipped
    instead of forced.
    """
    from executor_birth_epoch_store import (
        EpochStoreError, preserve_legacy_rows, read_legacy_resolutions,
        record_legacy_resolutions,
    )
    from executor_lifecycle_state import Restriction, restrict_generation_exact

    if not isinstance(plan, MigrationPlanV1):
        raise LifecycleMigrationError("migration_plan_invalid", "type")
    epoch_db_path = Path(epoch_db_path)
    if not epoch_db_path.is_file():
        raise LifecycleMigrationError("migration_plan_invalid", "epoch store absent")
    report = {"migration_id": plan.migration_id, "preserved": 0, "resolved": 0,
              "restricted": 0, "pending": 0, "sources": []}
    for source in plan.sources:
        if not source_unchanged(source.identity):
            raise LifecycleMigrationError(
                "migration_source_changed", source.identity.path)
        report["preserved"] += preserve_legacy_rows(
            source_id=source.identity.source_id,
            source_schema_id=source.identity.schema_id,
            legacy_table=source.legacy_table, rows=source.rows,
            migrated_at=applied_at, db_path=epoch_db_path,
        )
        migration_id = _preserved_migration_id(source)
        report["resolved"] += record_legacy_resolutions(
            migration_id=migration_id,
            resolutions=[(item.ordinal, item.kind.value, item.contract_id,
                          item.generation_id, item.evidence_id)
                         for item in source.dispositions],
            recorded_at=applied_at, db_path=epoch_db_path,
        )
        recorded = len(read_legacy_resolutions(
            migration_id=migration_id, db_path=epoch_db_path))
        if recorded != len(source.dispositions):
            raise LifecycleMigrationError(
                "migration_resolution_incomplete", source.legacy_table)
        for item in source.dispositions:
            if item.kind is Disposition.PENDING_DISPOSITION:
                report["pending"] += 1
                continue
            if item.kind is not Disposition.CURRENT_RESTRICTION:
                continue
            restriction = (Restriction.REMOVED if item.effect is LegacyEffect.REMOVED
                           else Restriction.DEMOTED)
            try:
                if restrict_generation_exact(
                    contract_id=item.contract_id, generation_id=item.generation_id,
                    restriction=restriction, reason=item.reason,
                    observed_at=applied_at, db_path=epoch_db_path,
                ):
                    report["restricted"] += 1
            except EpochStoreError as exc:
                raise LifecycleMigrationError(
                    "migration_restriction_failed", exc.code) from exc
        report["sources"].append({
            "legacy_table": source.legacy_table,
            "migration_id": migration_id,
            "plan_id": plan_digest_v1(source.dispositions),
            "rows": len(source.rows),
            "source_id": source.identity.source_id,
        })
        if not source_unchanged(source.identity):
            raise LifecycleMigrationError(
                "migration_source_changed", source.identity.path)
    return report


def _preserved_migration_id(source: SourcePlanV1) -> str:
    """The identity the preservation storage assigns to this exact copy."""
    from executor_birth_epoch_store import legacy_rows_digest

    return "sha256:" + hashlib.sha256(
        b"metnos.executor-birth.legacy-migration-id/v2\0"
        + json.dumps([source.identity.source_id, source.identity.schema_id,
                      source.legacy_table, legacy_rows_digest(source.rows)],
                     ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


__all__ = [
    "Disposition", "LIFECYCLE_MIGRATION_DOMAIN_V1", "LegacyDispositionV1",
    "LegacyEffect", "LegacyRowFacts", "LegacySourceIdentityV1",
    "LifecycleMigrationError", "MigrationPlanV1", "SourcePlanV1",
    "apply_migration", "census_source", "plan_digest_v1",
    "plan_dispositions", "plan_migration", "promoter_facts",
    "source_unchanged", "statistics_facts",
]
