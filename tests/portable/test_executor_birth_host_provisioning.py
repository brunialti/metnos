"""Portable tests for resumable Executor Birth host convergence."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from install import executor_birth_host_provisioning as provisioning
from install import executor_birth_transition as transition
import executor_birth_account_identity as identity
import executor_birth_host_layout as layout
import executor_birth_host_provisioning_journal as journal


class SimulatedCrash(RuntimeError):
    pass


def _snapshot(*, uid: int = 991) -> identity.PosixAccountSnapshotV1:
    record = identity.PosixAccountRecordV1(
        "metnos", uid, 992, "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    return identity.PosixAccountSnapshotV1(record, (record.gid,))


class FakeEffects:
    def __init__(self, account=None, *, conforming: bool = False) -> None:
        self.desired = _snapshot()
        self.account = account
        self.group = account.record.gid if account is not None else None
        self.encoded: list[bytes] = []
        self.nodes = {}
        self.events = []
        self.fail_checkpoint = None
        self.account_reads = 0
        self.change_account_on_read = None
        spec = layout.build_host_layout_spec_v1(self.desired)
        bootstrap = next(
            item for item in spec.objects
            if item.role is layout.HostPathRoleV1.bootstrap_root
        )
        self._put_exact(bootstrap)
        if conforming:
            for target in spec.objects:
                self._put_exact(target)

    def _put_exact(self, target) -> None:
        self.nodes[target.path] = [
            layout.HostNodeKindV1.directory,
            target.ownership.uid, target.ownership.gid, target.mode, False, False,
        ]

    def load_records(self):
        return tuple(self.encoded)

    def append_record(self, sequence, encoded):
        assert sequence == len(self.encoded)
        self.encoded.append(encoded)
        self.events.append(("record", sequence))

    def observe_account(self):
        self.account_reads += 1
        if self.account_reads == self.change_account_on_read:
            return _snapshot(uid=993)
        return self.account

    def observe_primary_group(self):
        return self.group

    def create_primary_group(self):
        self.group = self.desired.record.gid
        self.events.append(("create_group", None))

    def create_account(self):
        self.account = self.desired
        self.events.append(("create_account", None))

    def observe_layout(self, account):
        objects = []
        for target in layout.build_host_layout_spec_v1(account).objects:
            values = self.nodes.get(target.path)
            if values is None:
                item = layout.HostPathObservationV1(
                    target.path, layout.HostNodeKindV1.missing,
                )
            else:
                item = layout.HostPathObservationV1(target.path, *values)
            objects.append(item)
        return layout.HostLayoutObservationV1(account, tuple(objects))

    def apply_layout_step(self, step, account):
        assert account == self.account
        target = step.target
        self.events.append((step.kind.value, target.path.as_posix()))
        if step.kind is layout.HostLayoutStepKindV1.create_directory:
            self.nodes[target.path] = [
                layout.HostNodeKindV1.directory, 0, 0, 0o700, False, False,
            ]
        elif step.kind is layout.HostLayoutStepKindV1.set_owner:
            self.nodes[target.path][1:3] = [
                target.ownership.uid, target.ownership.gid,
            ]
        elif step.kind is layout.HostLayoutStepKindV1.remove_posix_acl:
            self.nodes[target.path][4:6] = [False, False]
        elif step.kind is layout.HostLayoutStepKindV1.set_mode:
            self.nodes[target.path][3] = target.mode

    def checkpoint(self, name):
        self.events.append(("checkpoint", name))
        if name == self.fail_checkpoint:
            self.fail_checkpoint = None
            raise SimulatedCrash(name)


def _conforming_observation(account):
    effects = FakeEffects(account, conforming=True)
    return effects.observe_layout(account)


def _seed_chain(effects: FakeEffects, depth: int) -> None:
    account = effects.desired
    observation = _conforming_observation(account)
    records = [journal.plan_host_provisioning_v1()]
    records.append(journal.record_account_ready_v1(records[-1], account))
    records.append(journal.record_layout_ready_v1(
        records[-1], account, observation,
    ))
    records.append(journal.record_host_verified_v1(
        records[-1], account, observation,
    ))
    effects.encoded[:] = [
        journal.encode_host_provisioning_record_v1(item)
        for item in records[:depth]
    ]


def test_new_host_converges_parent_first_then_is_a_noop() -> None:
    effects = FakeEffects()
    result = provisioning._provision_host_core_v1(effects)
    assert result.changed is True
    assert len(effects.encoded) == 4
    assert ("create_group", None) in effects.events
    assert ("create_account", None) in effects.events
    spec = layout.build_host_layout_spec_v1(effects.desired)
    created = [path for kind, path in effects.events if kind == "create_directory"]
    assert created == [
        item.path.as_posix() for item in spec.objects
        if item.role is not layout.HostPathRoleV1.bootstrap_root
    ]
    before = list(effects.events)
    second = provisioning._provision_host_core_v1(effects)
    assert second.changed is False
    assert effects.events == before


def test_existing_owner_mode_and_acl_drift_are_repaired() -> None:
    effects = FakeEffects(_snapshot(), conforming=True)
    target = layout.build_host_layout_spec_v1(effects.desired).objects[-1]
    effects.nodes[target.path][1:6] = [44, 45, 0o755, True, True]
    provisioning._provision_host_core_v1(effects)
    changes = [kind for kind, path in effects.events if path == target.path.as_posix()]
    assert changes == ["set_owner", "remove_posix_acl", "set_mode"]
    assert effects.nodes[target.path][1:6] == [991, 992, 0o700, False, False]


def test_non_directory_conflict_never_mutates_layout() -> None:
    effects = FakeEffects(_snapshot(), conforming=True)
    target = layout.build_host_layout_spec_v1(effects.desired).objects[-1]
    effects.nodes[target.path] = [
        layout.HostNodeKindV1.other, 0, 0, 0o600, False, False,
    ]
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.code == "birth_provisioning_host_invalid"
    assert not any(kind in {item.value for item in layout.HostLayoutStepKindV1}
                   for kind, _ in effects.events)


def test_effect_boundary_rejects_non_tuple_journal_container() -> None:
    effects = FakeEffects()
    effects.load_records = lambda: []
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.code == "birth_provisioning_recovery_required"


@pytest.mark.parametrize("depth", range(5))
def test_resume_from_every_durable_state(depth: int) -> None:
    account = None if depth < 2 else _snapshot()
    effects = FakeEffects(account, conforming=depth >= 3)
    _seed_chain(effects, depth)
    provisioning._provision_host_core_v1(effects)
    decoded = journal.decode_host_provisioning_chain_v1(tuple(effects.encoded))
    assert decoded[-1].state is journal.HostProvisioningStateV1.HOST_VERIFIED
    assert len(decoded) == 4


@pytest.mark.parametrize(
    "checkpoint",
    [
        "record_planned", "record_account_ready",
        "record_layout_ready", "record_host_verified",
    ],
)
def test_crash_after_each_persisted_state_resumes(checkpoint: str) -> None:
    effects = FakeEffects()
    effects.fail_checkpoint = checkpoint
    with pytest.raises(SimulatedCrash):
        provisioning._provision_host_core_v1(effects)
    provisioning._provision_host_core_v1(effects)
    assert len(journal.decode_host_provisioning_chain_v1(tuple(effects.encoded))) == 4


@pytest.mark.parametrize(
    "kind",
    [
        layout.HostLayoutStepKindV1.create_directory,
        layout.HostLayoutStepKindV1.set_owner,
        layout.HostLayoutStepKindV1.remove_posix_acl,
        layout.HostLayoutStepKindV1.set_mode,
    ],
)
def test_crash_after_each_layout_effect_resumes(kind) -> None:
    effects = FakeEffects(_snapshot(), conforming=True)
    target = layout.build_host_layout_spec_v1(effects.desired).objects[-1]
    if kind is layout.HostLayoutStepKindV1.create_directory:
        effects.nodes.pop(target.path)
    elif kind is layout.HostLayoutStepKindV1.set_owner:
        effects.nodes[target.path][1] = 44
    elif kind is layout.HostLayoutStepKindV1.remove_posix_acl:
        effects.nodes[target.path][4] = True
    else:
        effects.nodes[target.path][3] = 0o755
    effects.fail_checkpoint = "layout_" + kind.value
    with pytest.raises(SimulatedCrash):
        provisioning._provision_host_core_v1(effects)
    assert (kind.value, target.path.as_posix()) in effects.events
    provisioning._provision_host_core_v1(effects)
    assert len(journal.decode_host_provisioning_chain_v1(tuple(effects.encoded))) == 4


def test_carried_account_change_requires_recovery() -> None:
    effects = FakeEffects(_snapshot(uid=993))
    _seed_chain(effects, 2)
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.code == "birth_provisioning_recovery_required"


def test_closed_journal_still_rejects_primary_group_drift() -> None:
    effects = FakeEffects(_snapshot(), conforming=True)
    _seed_chain(effects, 4)
    effects.group = 1234
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.code == "birth_provisioning_host_invalid"
    assert caught.value.detail == "primary group identity"


def test_nonpositive_group_is_rejected_before_user_creation() -> None:
    effects = FakeEffects()
    effects.group = 0
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.detail == "primary group identity"
    assert ("create_account", None) not in effects.events


def test_account_change_after_layout_effect_is_translated_stably() -> None:
    effects = FakeEffects(_snapshot(), conforming=True)
    target = layout.build_host_layout_spec_v1(effects.desired).objects[-1]
    effects.nodes[target.path][3] = 0o755
    effects.change_account_on_read = 3
    with pytest.raises(provisioning.HostProvisioningError) as caught:
        provisioning._provision_host_core_v1(effects)
    assert caught.value.code == "birth_provisioning_recovery_required"
    assert caught.value.detail == "account changed"


def test_transition_provisions_before_service_account_resolution(monkeypatch) -> None:
    account, events = _snapshot(), []

    def provision(user):
        events.append(("provision", user))
        return SimpleNamespace(account=account)

    def fresh(user):
        events.append(("fresh", user))
        return account

    monkeypatch.setattr(provisioning, "provision_executor_birth_host_v1", provision)
    monkeypatch.setattr(
        transition, "_service_environment_v1",
        lambda *_args: pytest.fail("provisioned identity must not be looked up twice"),
    )
    monkeypatch.setattr(
        transition._account_identity, "resolve_posix_account_snapshot_v1", fresh,
    )
    selected, environment = transition._provisioned_service_environment_v1("metnos")
    assert selected == "metnos"
    assert environment["HOME"] == "/var/lib/metnos-service"
    assert environment["METNOS_USER_DATA"] == (
        "/var/lib/metnos-service/.local/share/metnos"
    )
    assert events == [
        ("provision", "metnos"),
        ("fresh", "metnos"),
    ]


def test_transition_translates_fresh_snapshot_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning, "provision_executor_birth_host_v1",
        lambda _user: SimpleNamespace(account=_snapshot()),
    )

    def unavailable(_user):
        raise identity.PosixAccountResolutionError(
            identity.PosixAccountFailureKindV1.account_lookup_failed,
        )

    monkeypatch.setattr(
        transition._account_identity, "resolve_posix_account_snapshot_v1",
        unavailable,
    )
    with pytest.raises(transition.TransitionEntryError) as caught:
        transition._provisioned_service_environment_v1("metnos")
    assert caught.value.code == "birth_ownership_deployment_invalid"


def test_product_wrapper_requires_linux_root_and_exact_service_name(monkeypatch) -> None:
    monkeypatch.setattr(provisioning.sys, "platform", "unsupported")
    with pytest.raises(provisioning.HostProvisioningError) as unsupported:
        provisioning.provision_executor_birth_host_v1("metnos")
    assert unsupported.value.code == "birth_ownership_platform_unsupported"
    monkeypatch.setattr(provisioning.sys, "platform", "linux")
    monkeypatch.setattr(provisioning.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(provisioning.HostProvisioningError) as unprivileged:
        provisioning.provision_executor_birth_host_v1("metnos")
    assert unprivileged.value.code == "birth_ownership_administrative_required"
    monkeypatch.setattr(provisioning.os, "geteuid", lambda: 0)
    with pytest.raises(provisioning.HostProvisioningError) as invalid:
        provisioning.provision_executor_birth_host_v1(
            SimpleNamespace(__eq__=lambda _self, _other: True),
        )
    assert invalid.value.code == "birth_provisioning_host_invalid"
