"""Pure public model for descriptor-bound POSIX directory observations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from executor_birth_posix_acl import PosixAclSnapshotV1
from executor_birth_posix_metadata import PosixStatSnapshotV1


class PosixDirectoryFailureKindV1(str, Enum):
    platform_unsupported = "platform_unsupported"
    invalid_path = "invalid_path"
    not_found = "not_found"
    not_directory = "not_directory"
    symlink_refused = "symlink_refused"
    permission_denied = "permission_denied"
    acl_unsupported = "acl_unsupported"
    acl_malformed = "acl_malformed"
    binding_changed = "binding_changed"
    process_changed = "process_changed"
    capability_closed = "capability_closed"
    io_unavailable = "io_unavailable"


class PosixDirectoryError(RuntimeError):
    def __init__(
        self, kind: PosixDirectoryFailureKindV1,
        cause: BaseException | None = None,
    ) -> None:
        self.kind = kind
        self.internal_cause = cause
        super().__init__(kind.value)


class PosixNodeKindV1(str, Enum):
    missing = "missing"
    directory = "directory"
    other = "other"


@dataclass(frozen=True, slots=True)
class PosixDirectoryObservationV1:
    metadata: PosixStatSnapshotV1
    acl: PosixAclSnapshotV1


@dataclass(frozen=True, slots=True)
class PosixChildObservationV1:
    name: str
    node_kind: PosixNodeKindV1
    metadata: PosixStatSnapshotV1 | None
    acl: PosixAclSnapshotV1 | None

    def __post_init__(self) -> None:
        missing = self.node_kind is PosixNodeKindV1.missing
        directory = self.node_kind is PosixNodeKindV1.directory
        if missing != (self.metadata is None):
            raise ValueError("missing child metadata mismatch")
        if directory != (self.acl is not None):
            raise ValueError("child ACL metadata mismatch")


__all__ = [
    "PosixChildObservationV1", "PosixDirectoryError",
    "PosixDirectoryFailureKindV1", "PosixDirectoryObservationV1",
    "PosixNodeKindV1",
]
