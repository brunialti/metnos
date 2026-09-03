"""Canonical typed evidence for the pure host-provisioning journal."""
from __future__ import annotations

from executor_birth_account_identity import (
    PosixAccountRecordV1,
    PosixAccountSnapshotV1,
)
from executor_birth_canonical import encode_canonical_ascii_v1
from executor_birth_crypto_framing import framed_sha256_v1
import executor_birth_host_path_policy as path_policy
from executor_birth_host_layout import (
    SERVICE_ACCOUNT_POLICY_V1,
    HostAccountPolicyV1,
    HostLayoutObservationV1,
    HostLayoutSpecV1,
    HostPathObservationV1,
    build_host_layout_spec_v1,
    verify_host_layout_v1,
)


HOST_PROVISIONING_POLICY_PROTOCOL_V1 = (
    "metnos.executor-birth.host-provisioning-policy/v1"
)
_REQUEST_DOMAIN_V1 = b"metnos.executor-birth.host-provisioning-request/v1\0"
_POLICY_DOMAIN_V1 = b"metnos.executor-birth.host-provisioning-policy/v1\0"
_ACCOUNT_DOMAIN_V1 = b"metnos.executor-birth.host-account-snapshot/v1\0"
_LAYOUT_DOMAIN_V1 = b"metnos.executor-birth.host-layout-spec/v1\0"
_OBSERVATION_DOMAIN_V1 = b"metnos.executor-birth.host-layout-observation/v1\0"


class HostProvisioningEvidenceError(ValueError):
    """A typed value is not canonical host-provisioning evidence."""

    def __init__(self, detail: str) -> None:
        self.code = "host_provisioning_evidence_invalid"
        self.detail = detail
        super().__init__(self.code)


def _digest_v1(domain: bytes, value: object) -> str:
    return framed_sha256_v1(domain, encode_canonical_ascii_v1(value))


def _account_policy_value_v1(policy: HostAccountPolicyV1) -> dict[str, object]:
    if type(policy) is not HostAccountPolicyV1 or policy != SERVICE_ACCOUNT_POLICY_V1:
        raise HostProvisioningEvidenceError("account_policy")
    return {
        "kind": policy.kind.value, "name": policy.name,
        "primary_group_name": policy.primary_group_name,
        "supplementary_group_names": list(policy.supplementary_group_names),
        "home": policy.home.as_posix(), "shell": policy.shell.as_posix(),
    }


def _policy_value_v1() -> dict[str, object]:
    return {
        "protocol": HOST_PROVISIONING_POLICY_PROTOCOL_V1,
        "account_policy": _account_policy_value_v1(SERVICE_ACCOUNT_POLICY_V1),
        "path_policy": [{
            "role": item.role.value, "path": item.path.as_posix(),
            "owner_kind": item.owner_kind.value, "mode": item.mode,
            "posix_acl": item.posix_acl.value,
        } for item in path_policy.HOST_PATH_POLICY_V1],
        "trust_anchors": [
            path.as_posix() for path in path_policy.HOST_TRUST_ANCHORS_V1
        ],
    }


def host_provisioning_policy_sha256_v1() -> str:
    return _digest_v1(_POLICY_DOMAIN_V1, _policy_value_v1())


def host_provisioning_request_id_v1() -> str:
    return _digest_v1(_REQUEST_DOMAIN_V1, {
        "protocol": HOST_PROVISIONING_POLICY_PROTOCOL_V1,
        "policy_sha256": host_provisioning_policy_sha256_v1(),
    })


def _account_value_v1(account: PosixAccountSnapshotV1) -> dict[str, object]:
    if type(account) is not PosixAccountSnapshotV1:
        raise HostProvisioningEvidenceError("account_type")
    record = account.record
    groups = account.supplementary_gids
    if type(record) is not PosixAccountRecordV1 or type(groups) is not tuple:
        raise HostProvisioningEvidenceError("account_shape")
    try:
        build_host_layout_spec_v1(account)
    except (TypeError, ValueError) as exc:
        raise HostProvisioningEvidenceError("account_policy") from exc
    if groups != tuple(sorted(set(groups))) or any(
        type(item) is not int or item <= 0 for item in groups
    ):
        raise HostProvisioningEvidenceError("account_groups")
    return {
        "name": record.name, "uid": record.uid, "gid": record.gid,
        "home": record.home, "shell": record.shell,
        "supplementary_gids": list(groups),
    }


def host_account_snapshot_sha256_v1(account: PosixAccountSnapshotV1) -> str:
    return _digest_v1(_ACCOUNT_DOMAIN_V1, _account_value_v1(account))


def _layout_spec_value_v1(spec: HostLayoutSpecV1) -> dict[str, object]:
    return {
        "account_sha256": host_account_snapshot_sha256_v1(spec.account),
        "objects": [{
            "role": item.role.value, "path": item.path.as_posix(),
            "owner_kind": item.ownership.kind.value,
            "uid": item.ownership.uid, "gid": item.ownership.gid,
            "mode": item.mode, "posix_acl": item.posix_acl.value,
        } for item in spec.objects],
    }


def host_layout_spec_sha256_v1(account: PosixAccountSnapshotV1) -> str:
    try:
        spec = build_host_layout_spec_v1(account)
    except (TypeError, ValueError) as exc:
        raise HostProvisioningEvidenceError("layout_spec") from exc
    return _digest_v1(_LAYOUT_DOMAIN_V1, _layout_spec_value_v1(spec))


def _observation_value_v1(
    observation: HostLayoutObservationV1,
) -> dict[str, object]:
    if type(observation) is not HostLayoutObservationV1 or any(
        type(item) is not HostPathObservationV1 for item in observation.objects
    ):
        raise HostProvisioningEvidenceError("layout_observation_type")
    return {
        "account_sha256": host_account_snapshot_sha256_v1(observation.account),
        "objects": [{
            "path": item.path.as_posix(), "node_kind": item.node_kind.value,
            "uid": item.uid, "gid": item.gid, "mode": item.mode,
            "has_extended_access_acl": item.has_extended_access_acl,
            "has_default_acl": item.has_default_acl,
        } for item in observation.objects],
    }


def host_layout_observation_sha256_v1(
    account: PosixAccountSnapshotV1,
    observation: HostLayoutObservationV1,
) -> str:
    try:
        spec = build_host_layout_spec_v1(account)
        verify_host_layout_v1(spec, observation)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HostProvisioningEvidenceError("layout_observation") from exc
    return _digest_v1(
        _OBSERVATION_DOMAIN_V1, _observation_value_v1(observation),
    )


__all__ = [
    "HOST_PROVISIONING_POLICY_PROTOCOL_V1", "HostProvisioningEvidenceError",
    "host_account_snapshot_sha256_v1", "host_layout_observation_sha256_v1",
    "host_layout_spec_sha256_v1", "host_provisioning_policy_sha256_v1",
    "host_provisioning_request_id_v1",
]
