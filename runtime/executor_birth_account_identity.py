"""Pure POSIX account snapshots and Metnos XDG layout derivation.

This module owns representation and lookup only.  Callers retain their
domain-specific policy and translate lookup failures into their established
public error codes.  Importing it performs no account lookup, filesystem I/O,
environment mutation or platform registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re


POSIX_ACCOUNT_NAME_PATTERN_V1 = r"[a-z_][a-z0-9_-]{0,31}\Z"
_ACCOUNT_NAME_RE_V1 = re.compile(POSIX_ACCOUNT_NAME_PATTERN_V1)


class PosixAccountFailureKindV1(str, Enum):
    platform_unsupported = "platform_unsupported"
    account_lookup_failed = "account_lookup_failed"
    group_lookup_failed = "group_lookup_failed"


class PosixAccountResolutionError(RuntimeError):
    """Typed, policy-neutral failure at the POSIX account boundary."""

    def __init__(
        self, kind: PosixAccountFailureKindV1, cause: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.internal_cause = cause
        super().__init__(kind.value)


class PosixAccountSnapshotChangedError(RuntimeError):
    """The same account name no longer resolves to the same identity."""


@dataclass(frozen=True, slots=True)
class PosixAccountRecordV1:
    """One immutable record returned by the platform account database."""

    name: str
    uid: int
    gid: int
    home: str
    shell: str


@dataclass(frozen=True, slots=True)
class PosixAccountSnapshotV1:
    """One complete, immutable observation of a POSIX account."""

    record: PosixAccountRecordV1
    supplementary_gids: tuple[int, ...]

    def assert_unchanged(self, current: object) -> None:
        if type(current) is not PosixAccountSnapshotV1 or self != current:
            raise PosixAccountSnapshotChangedError("posix_account_changed")


@dataclass(frozen=True, slots=True)
class MetnosXdgLayoutV1:
    """All service-owned paths bound to exactly one account record."""

    account: PosixAccountRecordV1

    @property
    def home(self) -> Path:
        return Path(self.account.home)

    @property
    def data(self) -> Path:
        return self.home / ".local" / "share" / "metnos"

    @property
    def state(self) -> Path:
        return self.home / ".local" / "state" / "metnos"

    @property
    def config(self) -> Path:
        return self.home / ".config" / "metnos"

    @property
    def cache(self) -> Path:
        return self.home / ".cache" / "metnos"

    @property
    def workspace(self) -> Path:
        return self.data / "workspace"

    def environment(self) -> dict[str, str]:
        return {
            "HOME": self.home.as_posix(),
            "LOGNAME": self.account.name,
            "USER": self.account.name,
            "METNOS_USER_DATA": self.data.as_posix(),
            "METNOS_USER_STATE": self.state.as_posix(),
            "METNOS_USER_CONFIG": self.config.as_posix(),
            "METNOS_USER_CACHE": self.cache.as_posix(),
            "METNOS_WORKSPACE": self.workspace.as_posix(),
        }


def is_posix_account_name_v1(value: object) -> bool:
    return type(value) is str and _ACCOUNT_NAME_RE_V1.fullmatch(value) is not None


def resolve_posix_account_v1(name: str) -> PosixAccountRecordV1:
    """Read one account without imposing a caller's deployment policy."""
    try:
        import pwd
    except ImportError as exc:
        raise PosixAccountResolutionError(
            PosixAccountFailureKindV1.platform_unsupported, exc,
        ) from exc
    try:
        entry = pwd.getpwnam(name)
    except (KeyError, OSError) as exc:
        raise PosixAccountResolutionError(
            PosixAccountFailureKindV1.account_lookup_failed, exc,
        ) from exc
    return PosixAccountRecordV1(
        entry.pw_name, entry.pw_uid, entry.pw_gid, entry.pw_dir, entry.pw_shell,
    )


def resolve_supplementary_gids_v1(name: str, primary_gid: int) -> tuple[int, ...]:
    """Return the stable set representation shared by identity consumers."""
    getgrouplist = getattr(os, "getgrouplist", None)
    if not callable(getgrouplist):
        cause = TypeError("os.getgrouplist is unavailable")
        raise PosixAccountResolutionError(
            PosixAccountFailureKindV1.platform_unsupported, cause,
        ) from cause
    try:
        return tuple(sorted(set(getgrouplist(name, primary_gid))))
    except (KeyError, OSError) as exc:
        raise PosixAccountResolutionError(
            PosixAccountFailureKindV1.group_lookup_failed, exc,
        ) from exc


def resolve_posix_account_snapshot_v1(name: str) -> PosixAccountSnapshotV1:
    """Resolve account and group database data into one typed observation."""
    record = resolve_posix_account_v1(name)
    return PosixAccountSnapshotV1(
        record,
        resolve_supplementary_gids_v1(record.name, record.gid),
    )


def metnos_xdg_layout_v1(account: PosixAccountRecordV1) -> MetnosXdgLayoutV1:
    """Bind pure XDG derivation to one immutable account record."""
    if type(account) is not PosixAccountRecordV1:
        raise TypeError("account must be PosixAccountRecordV1")
    return MetnosXdgLayoutV1(account)


__all__ = [
    "MetnosXdgLayoutV1",
    "POSIX_ACCOUNT_NAME_PATTERN_V1",
    "PosixAccountFailureKindV1",
    "PosixAccountRecordV1",
    "PosixAccountResolutionError",
    "PosixAccountSnapshotChangedError",
    "PosixAccountSnapshotV1",
    "is_posix_account_name_v1",
    "metnos_xdg_layout_v1",
    "resolve_posix_account_v1",
    "resolve_posix_account_snapshot_v1",
    "resolve_supplementary_gids_v1",
]
