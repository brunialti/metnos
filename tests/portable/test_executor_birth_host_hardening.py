"""Adversarial contracts for the productive host capability boundary."""
from __future__ import annotations

import copy
from dataclasses import replace
import os
from pathlib import Path
import pickle
import subprocess
import sys
from types import SimpleNamespace

import pytest

import executor_birth_account_identity as identity
import executor_birth_host_layout as layout
from install import executor_birth_host_capability as capability_module
from install import executor_birth_host_journal_posix as journal_posix
from install import executor_birth_host_posix as posix
from install import executor_birth_host_provisioning as provisioning
from install import executor_birth_transition as transition


def _snapshot() -> identity.PosixAccountSnapshotV1:
    record = identity.PosixAccountRecordV1(
        "metnos", 991, 992,
        "/var/lib/metnos-service", "/usr/sbin/nologin",
    )
    return identity.PosixAccountSnapshotV1(record, (992,))


def test_layout_effect_rejects_noncanonical_requests_before_io(monkeypatch) -> None:
    account = _snapshot()
    target = layout.build_host_layout_spec_v1(account).objects[-1]
    kind = layout.HostLayoutStepKindV1.set_mode
    mutants = (
        SimpleNamespace(kind=kind, target=target),
        layout.HostLayoutStepV1("set_mode", target),
        layout.HostLayoutStepV1(kind, replace(target, mode=0o755)),
        layout.HostLayoutStepV1(kind, SimpleNamespace(path=target.path)),
    )
    monkeypatch.setattr(
        posix, "_open_chain_v1",
        lambda *_args, **_kwargs: pytest.fail("invalid step reached I/O"),
    )
    effects = posix._PosixHostEffectsV1()
    for mutant in mutants:
        with pytest.raises(
            journal_posix.HostProvisioningPosixError, match="layout step",
        ):
            effects.apply_layout_step(mutant, account)
    foreign = SimpleNamespace(record=account.record, supplementary_gids=(992,))
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        effects.apply_layout_step(layout.HostLayoutStepV1(kind, target), foreign)


def test_canonical_step_is_rematerialized_from_the_single_policy() -> None:
    account = _snapshot()
    target = layout.build_host_layout_spec_v1(account).objects[-1]
    copied = replace(target)
    step = layout.HostLayoutStepV1(layout.HostLayoutStepKindV1.set_mode, copied)
    validated = layout.require_canonical_host_layout_step_v1(step, account)
    canonical = layout.build_host_layout_spec_v1(account).objects[-1]
    assert validated == step
    assert validated.target == canonical


class _RawEffects:
    def __init__(self) -> None:
        self.calls = []

    def observe_primary_group(self):
        self.calls.append("observe_primary_group")
        return 992

    def create_account(self):
        self.calls.append("create_account")
        raise RuntimeError("effect failed")


def test_locked_capability_is_pid_bound_and_noncopyable(monkeypatch) -> None:
    raw, attestations = _RawEffects(), []
    current_pid = os.getpid()
    bound = capability_module.bind_locked_host_effects_v1(
        raw, lambda: attestations.append("attest"),
    )
    assert not hasattr(bound, "_call_v1")
    assert bound.observe_primary_group() == 992
    assert attestations == ["attest", "attest"]
    for operation in (lambda: copy.copy(bound), lambda: copy.deepcopy(bound),
                      lambda: pickle.dumps(bound)):
        with pytest.raises(journal_posix.HostProvisioningPosixError):
            operation()
    monkeypatch.setattr(
        capability_module.os, "getpid", lambda: current_pid + 1,
    )
    with pytest.raises(
        journal_posix.HostProvisioningPosixError,
        match="host capability inactive",
    ):
        bound.observe_primary_group()


def test_locked_capability_reattests_after_a_failed_effect() -> None:
    raw, attestations = _RawEffects(), []
    bound = capability_module.bind_locked_host_effects_v1(
        raw, lambda: attestations.append("attest"),
    )
    with pytest.raises(RuntimeError, match="effect failed"):
        bound.create_account()
    assert attestations == ["attest", "attest"]


def test_context_reattests_each_operation_and_exit(monkeypatch) -> None:
    events, locks = [], []
    monkeypatch.setattr(posix, "_ensure_bootstrap_root_v1", lambda: None)
    monkeypatch.setattr(posix, "_bootstrap_expected_v1", lambda: {})
    monkeypatch.setattr(posix, "_open_chain_v1", lambda *_args: [10, 11, 12, 13])
    monkeypatch.setattr(posix, "open_journal_lock_v1", lambda *_args: 14)
    monkeypatch.setattr(
        posix, "PosixJournalStoreV1",
        lambda *_args: SimpleNamespace(load_records=lambda: (), append_record=lambda *_: None),
    )
    monkeypatch.setattr(
        posix, "_attest_locked_host_v1",
        lambda *_args: events.append("attest"),
    )
    monkeypatch.setattr(
        posix.fcntl, "flock", lambda descriptor, operation:
        locks.append((descriptor, operation)),
    )
    monkeypatch.setattr(posix.os, "close", lambda *_args: None)
    with posix.locked_host_effects_v1() as effects:
        assert not isinstance(effects, posix._PosixHostEffectsV1)
        effects.checkpoint("test")
    assert events == ["attest", "attest", "attest", "attest"]
    assert locks == [(14, posix.fcntl.LOCK_EX), (14, posix.fcntl.LOCK_UN)]
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        effects.checkpoint("expired")


def test_forked_cleanup_only_closes_inherited_descriptors(monkeypatch) -> None:
    pid, events, locks, closed = [100], [], [], []
    monkeypatch.setattr(posix.os, "getpid", lambda: pid[0])
    monkeypatch.setattr(posix, "_ensure_bootstrap_root_v1", lambda: None)
    monkeypatch.setattr(posix, "_bootstrap_expected_v1", lambda: {})
    monkeypatch.setattr(posix, "_open_chain_v1", lambda *_args: [10, 11, 12, 13])
    monkeypatch.setattr(posix, "open_journal_lock_v1", lambda *_args: 14)
    monkeypatch.setattr(
        posix, "PosixJournalStoreV1",
        lambda *_args: SimpleNamespace(load_records=lambda: (), append_record=lambda *_: None),
    )
    monkeypatch.setattr(
        posix, "_attest_locked_host_v1",
        lambda *_args: events.append("attest"),
    )
    monkeypatch.setattr(
        posix.fcntl, "flock", lambda descriptor, operation:
        locks.append((descriptor, operation)),
    )
    monkeypatch.setattr(posix.os, "close", closed.append)
    with posix.locked_host_effects_v1() as effects:
        pid[0] = 101
    assert events == ["attest"]
    assert locks == [(14, posix.fcntl.LOCK_EX)]
    assert closed == [14, 13, 12, 11, 10]
    with pytest.raises(journal_posix.HostProvisioningPosixError):
        effects.checkpoint("forked")


def test_transition_preserves_host_failure_code(monkeypatch) -> None:
    code = "birth_provisioning_recovery_required"
    monkeypatch.setattr(
        provisioning, "provision_executor_birth_host_v1",
        lambda _user: (_ for _ in ()).throw(
            provisioning.HostProvisioningError(code),
        ),
    )
    with pytest.raises(transition.TransitionEntryError) as caught:
        transition._provisioned_service_environment_v1("metnos")
    assert caught.value.code == code


def test_transition_rejects_an_unknown_host_failure_code(monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning, "provision_executor_birth_host_v1",
        lambda _user: (_ for _ in ()).throw(
            provisioning.HostProvisioningError("birth_unrelated_authority"),
        ),
    )
    with pytest.raises(transition.TransitionEntryError) as caught:
        transition._provisioned_service_environment_v1("metnos")
    assert caught.value.code == "birth_ownership_deployment_invalid"


def test_deploy_validates_legacy_inputs_before_host_mutation(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(transition, "_require_root_linux_v1", lambda: None)
    monkeypatch.setattr(
        transition, "_validated_legacy_inputs_v1",
        lambda *_args: events.append("legacy") or (
            "legacy-metnos", {}, Path("/opt/metnos"),
        ),
    )

    def stop_after_provisioning(_user):
        events.append("provision")
        raise RuntimeError("stop")

    monkeypatch.setattr(
        transition, "_provisioned_service_environment_v1",
        stop_after_provisioning,
    )
    with pytest.raises(RuntimeError, match="stop"):
        transition.deploy_source_v1(
            object(), "metnos", "legacy-metnos", "/opt/metnos",
        )
    assert events == ["legacy", "provision"]


def test_invalid_legacy_root_stops_before_host_mutation(monkeypatch) -> None:
    monkeypatch.setattr(transition, "_require_root_linux_v1", lambda: None)
    monkeypatch.setattr(
        transition, "_service_environment_v1",
        lambda _user: ("legacy-metnos", {}),
    )
    monkeypatch.setattr(
        transition, "_provisioned_service_environment_v1",
        lambda _user: pytest.fail("host mutation must not begin"),
    )
    with pytest.raises(transition.TransitionEntryError) as caught:
        transition.deploy_source_v1(
            object(), "metnos", "legacy-metnos", "relative/root",
        )
    assert caught.value.code == "birth_ownership_deployment_invalid"


def test_host_modules_import_isolated_without_path_or_environment_mutation(
    tmp_path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    program = """
import importlib, os, pathlib, sys
repo = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / 'runtime'))
before_path, before_env = tuple(sys.path), dict(os.environ)
for name in (
    'executor_birth_host_path_policy', 'executor_birth_host_layout',
    'executor_birth_host_provisioning_evidence',
    'executor_birth_host_provisioning_journal',
    'install.executor_birth_host_journal_posix',
    'install.executor_birth_host_capability',
    'install.executor_birth_host_posix',
    'install.executor_birth_host_provisioning',
):
    importlib.import_module(name)
assert tuple(sys.path) == before_path
assert dict(os.environ) == before_env
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", program, repository.as_posix()],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert tuple(tmp_path.iterdir()) == ()
