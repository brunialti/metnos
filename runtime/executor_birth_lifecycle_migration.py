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
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    return "sha256:" + hashlib.sha256(
        LIFECYCLE_MIGRATION_DOMAIN_V1 + payload).hexdigest()


def _decide(facts: LegacyRowFacts,
            selectable: Mapping[str, tuple[str, str]]) -> LegacyDispositionV1:
    identity = None if facts.name is None else selectable.get(facts.name)
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


_PROMOTER_OPEN_STATES = frozenset({"promoted_grace", "review_needed"})
_PROMOTER_ARCHIVED_STATES = frozenset({"archived"})


def promoter_facts(
    rows: Sequence[Mapping[str, object]], *, body_digests: Sequence[str],
) -> tuple[LegacyRowFacts, ...]:
    """Read open promotion cases out of preserved promoter rows.

    An unknown state is treated as open rather than closed: the retirement must
    not decide on a case it does not recognise.
    """
    if len(rows) != len(body_digests):
        raise LifecycleMigrationError("legacy_facts_invalid", "body_digests")
    facts = []
    for ordinal, (row, digest) in enumerate(zip(rows, body_digests)):
        if not isinstance(row, Mapping):
            raise LifecycleMigrationError("legacy_facts_invalid", "row")
        state = row.get("state")
        name = row.get("executor_name") or row.get("name")
        closed = isinstance(state, str) and state in _PROMOTER_ARCHIVED_STATES
        facts.append(LegacyRowFacts(
            ordinal, digest,
            name if isinstance(name, str) and name else None,
            LegacyEffect.NONE, not closed,
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


__all__ = [
    "Disposition", "LIFECYCLE_MIGRATION_DOMAIN_V1", "LegacyDispositionV1",
    "LegacyEffect", "LegacyRowFacts", "LegacySourceIdentityV1",
    "LifecycleMigrationError", "census_source", "plan_digest_v1",
    "plan_dispositions", "promoter_facts", "source_unchanged",
    "statistics_facts",
]
