"""Product orchestration for the canonical Executor Birth host layout."""
from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from typing import Protocol

import executor_birth_account_identity as account_identity
import executor_birth_host_provisioning_journal as journal
from executor_birth_host_layout import (
    SERVICE_ACCOUNT_NAME_V1,
    HostLayoutObservationV1,
    HostLayoutStepV1,
    HostNodeKindV1,
    build_host_layout_spec_v1,
    diff_host_layout_v1,
    verify_host_layout_v1,
)
from executor_birth_host_provisioning_evidence import (
    host_account_snapshot_sha256_v1,
    host_layout_observation_sha256_v1,
    host_layout_spec_sha256_v1,
)


class HostProvisioningError(RuntimeError):
    """Stable product failure at the host-provisioning boundary."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _fail(detail: str, *, recovery: bool = False) -> HostProvisioningError:
    code = (
        "birth_provisioning_recovery_required"
        if recovery else "birth_provisioning_host_invalid"
    )
    return HostProvisioningError(code, detail)


class _HostProvisioningEffectsV1(Protocol):
    def load_records(self) -> tuple[bytes, ...]: ...
    def append_record(self, sequence: int, encoded: bytes) -> None: ...
    def observe_account(self) -> account_identity.PosixAccountSnapshotV1 | None: ...
    def observe_primary_group(self) -> int | None: ...
    def create_primary_group(self) -> None: ...
    def create_account(self) -> None: ...
    def observe_layout(self, account) -> HostLayoutObservationV1: ...
    def apply_layout_step(
        self, step: HostLayoutStepV1,
        account: account_identity.PosixAccountSnapshotV1,
    ) -> None: ...
    def checkpoint(self, name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class HostProvisioningResultV1:
    account: account_identity.PosixAccountSnapshotV1
    record_sha256: str
    changed: bool


def _records_v1(effects: _HostProvisioningEffectsV1):
    encoded = effects.load_records()
    if type(encoded) is not tuple:
        raise _fail("journal container", recovery=True)
    if not encoded:
        return ()
    try:
        return journal.decode_host_provisioning_chain_v1(encoded)
    except journal.HostProvisioningJournalError as exc:
        raise _fail("journal", recovery=True) from exc


def _append_v1(effects, record) -> None:
    try:
        encoded = journal.encode_host_provisioning_record_v1(record)
        effects.append_record(record.sequence, encoded)
    except HostProvisioningError:
        raise
    except Exception as exc:
        raise _fail("journal append", recovery=True) from exc
    effects.checkpoint("record_" + record.state.value.lower())


def _ensure_planned_v1(effects, records):
    if records:
        return records
    planned = journal.plan_host_provisioning_v1()
    _append_v1(effects, planned)
    return (planned,)


def _require_group_v1(effects, account) -> None:
    observed_gid = effects.observe_primary_group()
    if type(observed_gid) is not int or observed_gid != account.record.gid:
        raise _fail("primary group identity")


def _require_fresh_identity_v1(effects, account) -> None:
    try:
        account.assert_unchanged(effects.observe_account())
    except account_identity.PosixAccountSnapshotChangedError as exc:
        raise _fail("account changed", recovery=True) from exc
    _require_group_v1(effects, account)


def _ensure_account_v1(effects):
    account = effects.observe_account()
    group_gid = effects.observe_primary_group()
    if account is None:
        if group_gid is None:
            effects.create_primary_group()
            effects.checkpoint("primary_group_created")
            group_gid = effects.observe_primary_group()
        if type(group_gid) is not int or group_gid <= 0:
            raise _fail("primary group identity")
        effects.create_account()
        effects.checkpoint("account_created")
        account = effects.observe_account()
    if account is None:
        raise _fail("account absent")
    _require_group_v1(effects, account)
    try:
        build_host_layout_spec_v1(account)
    except (TypeError, ValueError) as exc:
        raise _fail("account policy") from exc
    _require_fresh_identity_v1(effects, account)
    return account


def _require_carried_account_v1(record, account) -> None:
    try:
        actual = (
            host_account_snapshot_sha256_v1(account),
            host_layout_spec_sha256_v1(account),
        )
    except Exception as exc:
        raise _fail("account evidence") from exc
    if actual != (record.account_sha256, record.layout_spec_sha256):
        raise _fail("journal account changed", recovery=True)


def _converge_layout_v1(effects, account):
    spec = build_host_layout_spec_v1(account)
    observed = effects.observe_layout(account)
    plan = diff_host_layout_v1(spec, observed)
    if plan.conflicts:
        raise _fail("layout conflict")
    for step in plan.steps:
        effects.apply_layout_step(step, account)
        effects.checkpoint("layout_" + step.kind.value)
    _require_fresh_identity_v1(effects, account)
    fresh = effects.observe_layout(account)
    try:
        return verify_host_layout_v1(spec, fresh)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise _fail("layout verification", recovery=True) from exc


def _verify_final_v1(effects, record, account):
    _require_carried_account_v1(record, account)
    observation = effects.observe_layout(account)
    _require_fresh_identity_v1(effects, account)
    try:
        verify_host_layout_v1(build_host_layout_spec_v1(account), observation)
        digest = host_layout_observation_sha256_v1(account, observation)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise _fail("final host verification", recovery=True) from exc
    if digest != record.layout_observation_sha256:
        raise _fail("final host changed", recovery=True)
    return observation


def _provision_host_core_v1(effects: _HostProvisioningEffectsV1):
    records = _ensure_planned_v1(effects, _records_v1(effects))
    changed = len(records) < 4
    latest = records[-1]
    account = _ensure_account_v1(effects)
    if latest.state is journal.HostProvisioningStateV1.PLANNED:
        latest = journal.record_account_ready_v1(latest, account)
        _append_v1(effects, latest)
    else:
        _require_carried_account_v1(latest, account)
    if latest.state is journal.HostProvisioningStateV1.ACCOUNT_READY:
        observation = _converge_layout_v1(effects, account)
        latest = journal.record_layout_ready_v1(latest, account, observation)
        _append_v1(effects, latest)
    if latest.state is journal.HostProvisioningStateV1.LAYOUT_READY:
        observation = _verify_final_v1(effects, latest, account)
        latest = journal.record_host_verified_v1(latest, account, observation)
        _append_v1(effects, latest)
    _verify_final_v1(effects, latest, account)
    return HostProvisioningResultV1(account, latest.record_sha256, changed)


def provision_executor_birth_host_v1(
    service_user: object,
) -> HostProvisioningResultV1:
    """Provision and freshly verify the sole fixed productive host identity."""
    if not sys.platform.startswith("linux"):
        raise HostProvisioningError("birth_ownership_platform_unsupported")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise HostProvisioningError("birth_ownership_administrative_required")
    if type(service_user) is not str or service_user != SERVICE_ACCOUNT_NAME_V1:
        raise _fail("service account selection")
    from install.executor_birth_host_posix import (
        HostProvisioningPosixError,
        locked_host_effects_v1,
    )

    try:
        with locked_host_effects_v1() as effects:
            return _provision_host_core_v1(effects)
    except HostProvisioningError:
        raise
    except (HostProvisioningPosixError, OSError) as exc:
        raise _fail("POSIX adapter", recovery=True) from exc


__all__ = [
    "HostProvisioningError", "HostProvisioningResultV1",
    "provision_executor_birth_host_v1",
]
