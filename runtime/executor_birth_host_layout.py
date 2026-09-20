"""Pure canonical host-layout policy for the RM-0008 service account.

The module describes desired directories, observations and convergence plans.
It deliberately owns no account lookup, filesystem access or mutation adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from executor_birth_account_identity import (
    PosixAccountSnapshotV1,
)
import executor_birth_host_path_policy as _path_policy
from executor_birth_host_path_policy import (
    HOST_PROVISIONING_ROOT_V1,
    HOST_TRUST_ANCHORS_V1,
    LEGACY_STATE_JOURNAL_ROOT_V1,
    OWNERSHIP_ROOT_V1,
    PREFLIGHT_ATTESTATION_ROOT_V1,
    SERVICE_ACCOUNT_NAME_V1,
    SERVICE_HOME_V1,
    SERVICE_SHELL_V1,
    HostOwnerKindV1,
    HostPathPolicyV1,
    HostPathRoleV1,
    PosixAclPolicyV1,
)


HOST_PATH_POLICY_V1 = _path_policy.HOST_PATH_POLICY_V1


class HostAccountKindV1(str, Enum):
    system = "system"


class HostNodeKindV1(str, Enum):
    missing = "missing"
    directory = "directory"
    other = "other"


class HostLayoutStepKindV1(str, Enum):
    create_directory = "create_directory"
    set_owner = "set_owner"
    remove_posix_acl = "remove_posix_acl"
    set_mode = "set_mode"


class HostLayoutConflictKindV1(str, Enum):
    not_directory = "not_directory"


@dataclass(frozen=True, slots=True)
class HostAccountPolicyV1:
    kind: HostAccountKindV1
    name: str
    primary_group_name: str
    supplementary_group_names: tuple[str, ...]
    home: PurePosixPath
    shell: PurePosixPath


SERVICE_ACCOUNT_POLICY_V1 = HostAccountPolicyV1(
    HostAccountKindV1.system,
    SERVICE_ACCOUNT_NAME_V1,
    SERVICE_ACCOUNT_NAME_V1,
    (),
    SERVICE_HOME_V1,
    SERVICE_SHELL_V1,
)


@dataclass(frozen=True, slots=True)
class HostOwnershipV1:
    kind: HostOwnerKindV1
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if type(self.uid) is not int or type(self.gid) is not int:
            raise TypeError("host ownership ids must be integers")
        if self.uid < 0 or self.gid < 0:
            raise ValueError("host ownership ids must be non-negative")
        if self.kind is HostOwnerKindV1.root and (self.uid, self.gid) != (0, 0):
            raise ValueError("root ownership must be root:root")
        if self.kind is HostOwnerKindV1.service and (self.uid == 0 or self.gid == 0):
            raise ValueError("service ownership must not be root-owned")


@dataclass(frozen=True, slots=True)
class HostPathSpecV1:
    role: HostPathRoleV1
    path: PurePosixPath
    ownership: HostOwnershipV1
    mode: int
    posix_acl: PosixAclPolicyV1

    def __post_init__(self) -> None:
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError("host layout paths must be normalized and absolute")
        if type(self.mode) is not int or self.mode < 0 or self.mode > 0o777:
            raise ValueError("host layout mode must contain permission bits only")
        if self.posix_acl is not PosixAclPolicyV1.absent:
            raise ValueError("host layout requires absent POSIX ACLs")


@dataclass(frozen=True, slots=True)
class HostLayoutSpecV1:
    account_policy: HostAccountPolicyV1
    account: PosixAccountSnapshotV1
    objects: tuple[HostPathSpecV1, ...]

    def __post_init__(self) -> None:
        if self.account_policy != SERVICE_ACCOUNT_POLICY_V1:
            raise ValueError("host layout requires the system metnos account")
        if type(self.account) is not PosixAccountSnapshotV1:
            raise TypeError("account must be PosixAccountSnapshotV1")
        paths = tuple(item.path for item in self.objects)
        roles = tuple(item.role for item in self.objects)
        if not self.objects or len(paths) != len(set(paths)):
            raise ValueError("host layout paths must be non-empty and unique")
        if len(roles) != len(set(roles)):
            raise ValueError("host layout roles must be unique")
        if self.objects != tuple(sorted(self.objects, key=_spec_order_key_v1)):
            raise ValueError("host layout objects must use canonical ordering")


@dataclass(frozen=True, slots=True)
class HostPathObservationV1:
    path: PurePosixPath
    node_kind: HostNodeKindV1
    uid: int | None = None
    gid: int | None = None
    mode: int | None = None
    has_extended_access_acl: bool | None = None
    has_default_acl: bool | None = None

    def __post_init__(self) -> None:
        metadata = (
            self.uid, self.gid, self.mode,
            self.has_extended_access_acl, self.has_default_acl,
        )
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise ValueError("observed paths must be normalized and absolute")
        if self.node_kind is HostNodeKindV1.missing and any(
            value is not None for value in metadata
        ):
            raise ValueError("missing paths must not carry metadata")
        if self.node_kind is not HostNodeKindV1.missing:
            _validate_existing_metadata_v1(metadata)


@dataclass(frozen=True, slots=True)
class HostLayoutObservationV1:
    account: PosixAccountSnapshotV1
    objects: tuple[HostPathObservationV1, ...]

    def __post_init__(self) -> None:
        if type(self.account) is not PosixAccountSnapshotV1:
            raise TypeError("account must be PosixAccountSnapshotV1")
        paths = tuple(item.path for item in self.objects)
        if len(paths) != len(set(paths)):
            raise ValueError("observed paths must be unique")
        if self.objects != tuple(sorted(self.objects, key=_observation_order_key_v1)):
            raise ValueError("observations must use canonical ordering")


@dataclass(frozen=True, slots=True)
class HostLayoutStepV1:
    kind: HostLayoutStepKindV1
    target: HostPathSpecV1


@dataclass(frozen=True, slots=True)
class HostLayoutConflictV1:
    kind: HostLayoutConflictKindV1
    target: HostPathSpecV1
    observed_kind: HostNodeKindV1


@dataclass(frozen=True, slots=True)
class HostLayoutPlanV1:
    steps: tuple[HostLayoutStepV1, ...]
    conflicts: tuple[HostLayoutConflictV1, ...]

    @property
    def is_noop(self) -> bool:
        return not self.steps and not self.conflicts


class HostLayoutVerificationError(RuntimeError):
    """The complete host observation does not match its desired layout."""

    def __init__(self, plan: HostLayoutPlanV1) -> None:
        self.plan = plan
        super().__init__("host_layout_not_converged")


def _spec_order_key_v1(item: HostPathSpecV1) -> tuple[int, str]:
    return len(item.path.parts), item.path.as_posix()


def _observation_order_key_v1(item: HostPathObservationV1) -> tuple[int, str]:
    return len(item.path.parts), item.path.as_posix()


def _validate_existing_metadata_v1(metadata: tuple[object, ...]) -> None:
    uid, gid, mode, access_acl, default_acl = metadata
    if type(uid) is not int or type(gid) is not int or uid < 0 or gid < 0:
        raise ValueError("existing paths require non-negative ownership ids")
    if type(mode) is not int or mode < 0 or mode > 0o777:
        raise ValueError("existing paths require permission bits")
    if type(access_acl) is not bool or type(default_acl) is not bool:
        raise ValueError("existing paths require explicit POSIX ACL observations")


def _validate_service_account_v1(account: PosixAccountSnapshotV1) -> None:
    if type(account) is not PosixAccountSnapshotV1:
        raise TypeError("account must be PosixAccountSnapshotV1")
    record = account.record
    if record.name != SERVICE_ACCOUNT_NAME_V1:
        raise ValueError("service account must be metnos")
    if PurePosixPath(record.home) != SERVICE_HOME_V1:
        raise ValueError("service account home must be /var/lib/metnos-service")
    if PurePosixPath(record.shell) != SERVICE_SHELL_V1:
        raise ValueError("service account shell must be /usr/sbin/nologin")
    if record.uid <= 0 or record.gid <= 0:
        raise ValueError("service account must not use root ownership ids")
    if account.supplementary_gids != (record.gid,):
        raise ValueError("service account must have only its primary group")


def _path_spec_v1(
    account: PosixAccountSnapshotV1,
    policy: HostPathPolicyV1,
) -> HostPathSpecV1:
    record = account.record
    ownership = (
        HostOwnershipV1(HostOwnerKindV1.root, 0, 0)
        if policy.owner_kind is HostOwnerKindV1.root
        else HostOwnershipV1(HostOwnerKindV1.service, record.uid, record.gid)
    )
    return HostPathSpecV1(
        policy.role, policy.path, ownership, policy.mode, policy.posix_acl,
    )


def build_host_layout_spec_v1(account: PosixAccountSnapshotV1) -> HostLayoutSpecV1:
    """Build the single canonical RM-0008 host tree for one pinned account."""
    _validate_service_account_v1(account)
    objects = tuple(
        _path_spec_v1(account, policy)
        for policy in _path_policy.HOST_PATH_POLICY_V1
    )
    return HostLayoutSpecV1(SERVICE_ACCOUNT_POLICY_V1, account, objects)


def require_canonical_host_layout_step_v1(
    step: HostLayoutStepV1,
    account: PosixAccountSnapshotV1,
) -> HostLayoutStepV1:
    """Reject any effect request not issued by the canonical layout planner."""
    if type(account) is not PosixAccountSnapshotV1:
        raise TypeError("account must be PosixAccountSnapshotV1")
    if type(step) is not HostLayoutStepV1:
        raise TypeError("step must be HostLayoutStepV1")
    allowed = (
        HostLayoutStepKindV1.create_directory,
        HostLayoutStepKindV1.set_owner,
        HostLayoutStepKindV1.remove_posix_acl,
        HostLayoutStepKindV1.set_mode,
    )
    if type(step.kind) is not HostLayoutStepKindV1 or step.kind not in allowed:
        raise ValueError("step kind is not canonical")
    if type(step.target) is not HostPathSpecV1:
        raise TypeError("step target must be HostPathSpecV1")
    matches = tuple(
        target for target in build_host_layout_spec_v1(account).objects
        if target == step.target
    )
    if len(matches) != 1:
        raise ValueError("step target is not the unique canonical host path")
    return HostLayoutStepV1(step.kind, matches[0])


def _observation_index_v1(
    spec: HostLayoutSpecV1,
    observed: HostLayoutObservationV1,
) -> dict[PurePosixPath, HostPathObservationV1]:
    spec.account.assert_unchanged(observed.account)
    expected_paths = tuple(item.path for item in spec.objects)
    observed_paths = tuple(item.path for item in observed.objects)
    if observed_paths != expected_paths:
        raise ValueError("host observation must cover exactly the canonical paths")
    return {item.path: item for item in observed.objects}


def _missing_steps_v1(target: HostPathSpecV1) -> tuple[HostLayoutStepV1, ...]:
    kinds = (
        HostLayoutStepKindV1.create_directory,
        HostLayoutStepKindV1.set_owner,
        HostLayoutStepKindV1.remove_posix_acl,
        HostLayoutStepKindV1.set_mode,
    )
    return tuple(HostLayoutStepV1(kind, target) for kind in kinds)


def _existing_steps_v1(
    target: HostPathSpecV1,
    observed: HostPathObservationV1,
) -> tuple[HostLayoutStepV1, ...]:
    owner_changed = (observed.uid, observed.gid) != (
        target.ownership.uid, target.ownership.gid,
    )
    acl_changed = bool(
        observed.has_extended_access_acl or observed.has_default_acl
    )
    kinds = []
    if owner_changed:
        kinds.append(HostLayoutStepKindV1.set_owner)
    if acl_changed:
        kinds.append(HostLayoutStepKindV1.remove_posix_acl)
    if observed.mode != target.mode or owner_changed or acl_changed:
        kinds.append(HostLayoutStepKindV1.set_mode)
    return tuple(HostLayoutStepV1(kind, target) for kind in kinds)


def diff_host_layout_v1(
    spec: HostLayoutSpecV1,
    observed: HostLayoutObservationV1,
) -> HostLayoutPlanV1:
    """Return a parent-first convergence plan without performing any effect."""
    if type(spec) is not HostLayoutSpecV1:
        raise TypeError("spec must be HostLayoutSpecV1")
    if type(observed) is not HostLayoutObservationV1:
        raise TypeError("observed must be HostLayoutObservationV1")
    index = _observation_index_v1(spec, observed)
    steps: list[HostLayoutStepV1] = []
    conflicts: list[HostLayoutConflictV1] = []
    for target in spec.objects:
        current = index[target.path]
        if current.node_kind is HostNodeKindV1.missing:
            steps.extend(_missing_steps_v1(target))
        elif current.node_kind is not HostNodeKindV1.directory:
            conflicts.append(HostLayoutConflictV1(
                HostLayoutConflictKindV1.not_directory, target, current.node_kind,
            ))
        else:
            steps.extend(_existing_steps_v1(target, current))
    return HostLayoutPlanV1(tuple(steps), tuple(conflicts))


def verify_host_layout_v1(
    spec: HostLayoutSpecV1,
    observed: HostLayoutObservationV1,
) -> HostLayoutObservationV1:
    """Return an exact observation or raise with its deterministic diff."""
    plan = diff_host_layout_v1(spec, observed)
    if not plan.is_noop:
        raise HostLayoutVerificationError(plan)
    return observed


__all__ = [
    "HostAccountKindV1", "HostAccountPolicyV1", "HostLayoutConflictKindV1",
    "HostLayoutConflictV1", "HostLayoutObservationV1",
    "HostLayoutPlanV1", "HostLayoutSpecV1", "HostLayoutStepKindV1",
    "HostLayoutStepV1", "HostLayoutVerificationError", "HostNodeKindV1",
    "HostOwnerKindV1", "HostOwnershipV1", "HostPathObservationV1",
    "HostPathPolicyV1", "HostPathRoleV1", "HostPathSpecV1",
    "HOST_PATH_POLICY_V1", "HOST_PROVISIONING_ROOT_V1",
    "HOST_TRUST_ANCHORS_V1",
    "OWNERSHIP_ROOT_V1",
    "PREFLIGHT_ATTESTATION_ROOT_V1",
    "PosixAclPolicyV1", "SERVICE_ACCOUNT_NAME_V1", "SERVICE_ACCOUNT_POLICY_V1",
    "SERVICE_HOME_V1", "SERVICE_SHELL_V1",
    "build_host_layout_spec_v1", "diff_host_layout_v1",
    "require_canonical_host_layout_step_v1", "verify_host_layout_v1",
]
