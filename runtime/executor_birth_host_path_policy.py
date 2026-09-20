"""Immutable account-independent path policy for Executor Birth hosts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from executor_birth_account_identity import (
    PosixAccountRecordV1,
    metnos_xdg_layout_v1,
)


SERVICE_ACCOUNT_NAME_V1 = "metnos"
SERVICE_HOME_V1 = PurePosixPath("/var/lib/metnos-service")
SERVICE_SHELL_V1 = PurePosixPath("/usr/sbin/nologin")
OWNERSHIP_ROOT_V1 = PurePosixPath("/var/lib/metnos/executor-birth")
HOST_PROVISIONING_ROOT_V1 = PurePosixPath("/var/lib/metnos-host-provisioning-v1")
PREFLIGHT_ATTESTATION_ROOT_V1 = OWNERSHIP_ROOT_V1 / "preflight-attestations-v1"
LEGACY_STATE_JOURNAL_ROOT_V1 = OWNERSHIP_ROOT_V1 / "legacy-state-adoption-v1"
HOST_TRUST_ANCHORS_V1 = (
    PurePosixPath("/"), PurePosixPath("/var"), PurePosixPath("/var/lib"),
)


class HostPathRoleV1(str, Enum):
    ownership_parent = "ownership_parent"
    bootstrap_root = "bootstrap_root"
    legacy_state_journal = "legacy_state_journal"
    service_home = "service_home"
    ownership_root = "ownership_root"
    cache_parent = "cache_parent"
    config_parent = "config_parent"
    local_parent = "local_parent"
    cache = "cache"
    config = "config"
    share_parent = "share_parent"
    state_parent = "state_parent"
    preflight_attestations = "preflight_attestations"
    data = "data"
    state = "state"
    workspace = "workspace"


class HostOwnerKindV1(str, Enum):
    root = "root"
    service = "service"


class PosixAclPolicyV1(str, Enum):
    absent = "absent"


@dataclass(frozen=True, slots=True)
class HostPathPolicyV1:
    role: HostPathRoleV1
    path: PurePosixPath
    owner_kind: HostOwnerKindV1
    mode: int
    posix_acl: PosixAclPolicyV1

    def __post_init__(self) -> None:
        if type(self.role) is not HostPathRoleV1:
            raise TypeError("host path policy role")
        if (
            type(self.path) is not PurePosixPath or not self.path.is_absolute()
            or ".." in self.path.parts
        ):
            raise ValueError("host path policy path")
        if type(self.owner_kind) is not HostOwnerKindV1:
            raise TypeError("host path policy owner")
        if type(self.mode) is not int or not 0 <= self.mode <= 0o777:
            raise ValueError("host path policy mode")
        if self.posix_acl is not PosixAclPolicyV1.absent:
            raise ValueError("host path policy ACL")


def _policy_order_v1(item: HostPathPolicyV1) -> tuple[int, str]:
    return len(item.path.parts), item.path.as_posix()


def _canonical_path_policy_v1() -> tuple[HostPathPolicyV1, ...]:
    record = PosixAccountRecordV1(
        SERVICE_ACCOUNT_NAME_V1, 1, 1,
        SERVICE_HOME_V1.as_posix(), SERVICE_SHELL_V1.as_posix(),
    )
    xdg = metnos_xdg_layout_v1(record)
    values = (
        (HostPathRoleV1.ownership_parent, OWNERSHIP_ROOT_V1.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.bootstrap_root, HOST_PROVISIONING_ROOT_V1, HostOwnerKindV1.root, 0o700),
        (HostPathRoleV1.service_home, SERVICE_HOME_V1, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.cache_parent, xdg.cache.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.config_parent, xdg.config.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.local_parent, xdg.data.parent.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.ownership_root, OWNERSHIP_ROOT_V1, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.cache, xdg.cache, HostOwnerKindV1.service, 0o700),
        (HostPathRoleV1.config, xdg.config, HostOwnerKindV1.service, 0o700),
        (HostPathRoleV1.share_parent, xdg.data.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.state_parent, xdg.state.parent, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.legacy_state_journal, LEGACY_STATE_JOURNAL_ROOT_V1, HostOwnerKindV1.root, 0o700),
        (HostPathRoleV1.preflight_attestations, PREFLIGHT_ATTESTATION_ROOT_V1, HostOwnerKindV1.root, 0o755),
        (HostPathRoleV1.data, xdg.data, HostOwnerKindV1.service, 0o700),
        (HostPathRoleV1.state, xdg.state, HostOwnerKindV1.service, 0o700),
        (HostPathRoleV1.workspace, xdg.workspace, HostOwnerKindV1.service, 0o700),
    )
    return tuple(HostPathPolicyV1(
        role, PurePosixPath(path.as_posix()), owner, mode,
        PosixAclPolicyV1.absent,
    ) for role, path, owner, mode in values)


def _validate_policy_v1(policy: tuple[HostPathPolicyV1, ...]) -> None:
    if type(policy) is not tuple or any(
        type(item) is not HostPathPolicyV1 for item in policy
    ):
        raise TypeError("host path policy container")
    roles = tuple(item.role for item in policy)
    paths = tuple(item.path for item in policy)
    if (
        len(roles) != len(set(roles)) or len(paths) != len(set(paths))
        or policy != tuple(sorted(policy, key=_policy_order_v1))
    ):
        raise ValueError("host path policy canonical order")


def _validate_trust_anchors_v1(anchors: tuple[PurePosixPath, ...]) -> None:
    if (
        type(anchors) is not tuple or not anchors
        or any(type(path) is not PurePosixPath for path in anchors)
        or anchors[0] != PurePosixPath("/")
        or len(anchors) != len(set(anchors))
    ):
        raise ValueError("host trust anchor policy")
    if any(
        current.parent != previous
        for previous, current in zip(anchors, anchors[1:])
    ):
        raise ValueError("host trust anchor chain")


HOST_PATH_POLICY_V1 = _canonical_path_policy_v1()
_validate_policy_v1(HOST_PATH_POLICY_V1)
_validate_trust_anchors_v1(HOST_TRUST_ANCHORS_V1)


__all__ = [
    "HOST_PATH_POLICY_V1", "HOST_PROVISIONING_ROOT_V1",
    "HOST_TRUST_ANCHORS_V1",
    "LEGACY_STATE_JOURNAL_ROOT_V1",
    "HostOwnerKindV1", "HostPathPolicyV1", "HostPathRoleV1",
    "OWNERSHIP_ROOT_V1", "PREFLIGHT_ATTESTATION_ROOT_V1",
    "PosixAclPolicyV1", "SERVICE_ACCOUNT_NAME_V1", "SERVICE_HOME_V1",
    "SERVICE_SHELL_V1",
]
