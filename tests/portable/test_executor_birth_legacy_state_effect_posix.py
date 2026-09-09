"""Focused tests for the sole legacy ownership effect authority."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from pathlib import PurePosixPath
import pickle
import stat
import sys
from types import SimpleNamespace

import pytest

if sys.platform != "linux":
    pytest.skip("Linux ownership effects require POSIX descriptors", allow_module_level=True)

import contract_cutover_guard as cutover_guard
import executor_birth_account_identity as identity
import executor_birth_legacy_state_policy as policy
from executor_birth_legacy_state_request import build_legacy_state_request_v1
from install import executor_birth_legacy_state_effect_posix as effect


def _request():
    account = identity.PosixAccountSnapshotV1(
        identity.PosixAccountRecordV1(
            "metnos", 991, 992,
            "/var/lib/metnos-service", "/usr/sbin/nologin",
        ),
        (992,),
    )
    return build_legacy_state_request_v1(
        account, "sha256:" + "8" * 64,
    )


def _observation(owners):
    return policy.LegacyStateObservationV1(tuple(
        policy.LegacyPathObservationV1(
            PurePosixPath(path), policy.LegacyNodeKindV1.directory,
            owner[0], owner[1], 0o700, 2, None, None,
            False, False, 7, inode,
        )
        for path, inode, owner in (
            ("contract-authoring", 20, owners["contract-authoring"]),
            ("contract-authoring/v1", 21, owners["contract-authoring/v1"]),
        )
    ))


def test_adoption_changes_children_before_root_and_reattests_every_effect(
    monkeypatch,
) -> None:
    request = _request()
    owners = {
        "contract-authoring": (0, 0),
        "contract-authoring/v1": (0, 0),
    }
    descriptors = {"contract-authoring": 30, "contract-authoring/v1": 31}
    reverse = {value: key for key, value in descriptors.items()}
    inodes = {"contract-authoring": 20, "contract-authoring/v1": 21}
    opened, changed, attestations = [], [], []

    class Chain:
        @contextmanager
        def open_relative(self, relative, *, directory):
            assert directory is True
            name = relative.as_posix()
            opened.append(name)
            yield descriptors[name]

    def fstat(descriptor):
        name = reverse[descriptor]
        uid, gid = owners[name]
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700, st_dev=7, st_ino=inodes[name],
            st_uid=uid, st_gid=gid, st_nlink=2, st_size=4096,
        )

    def fchown(descriptor, uid, gid):
        name = reverse[descriptor]
        owners[name] = (uid, gid)
        changed.append(name)

    monkeypatch.setattr(effect, "observe_legacy_state_v1", lambda _request: _observation(owners))
    monkeypatch.setattr(effect.os, "fstat", fstat)
    monkeypatch.setattr(effect.os, "fchown", fchown)
    monkeypatch.setattr(effect.os, "fsync", lambda _descriptor: None)
    monkeypatch.setattr(effect, "require_no_acl_v1", lambda _descriptor: None)
    raw = effect._LegacyStateEffectsV1(
        request, Chain(), object(), lambda: attestations.append("attest"),
    )
    result = raw.adopt_authoring(_observation(owners))
    assert opened == ["contract-authoring/v1", "contract-authoring"]
    assert changed == opened
    assert owners == {
        "contract-authoring": (991, 992),
        "contract-authoring/v1": (991, 992),
    }
    assert result.observation_sha256 == policy.legacy_state_adoption_target_sha256_v1(
        request, _observation({
            "contract-authoring": (0, 0),
            "contract-authoring/v1": (0, 0),
        }),
    )
    assert attestations == ["attest"] * 4


def test_locked_capability_is_process_bound_and_not_transferable(monkeypatch) -> None:
    pid = [os.getpid()]
    raw = SimpleNamespace(
        load_records=lambda: (), append_record=lambda *_: None,
        observe=lambda: policy.LegacyStateObservationV1(()),
        adopt_authoring=lambda value: value, checkpoint=lambda _name: None,
    )
    monkeypatch.setattr(effect.os, "getpid", lambda: pid[0])
    capability = effect._LockedLegacyStateEffectsV1(raw, lambda: None)
    for operation in (
        lambda: copy.copy(capability), lambda: copy.deepcopy(capability),
        lambda: pickle.dumps(capability),
    ):
        with pytest.raises(effect.LegacyStateEffectPosixError):
            operation()
    pid[0] += 1
    with pytest.raises(effect.LegacyStateEffectPosixError, match="inactive"):
        capability.observe()


@pytest.mark.parametrize("observation", [
    policy.LegacyStateObservationV1(()),
    _observation({
        "contract-authoring": (991, 992),
        "contract-authoring/v1": (991, 992),
    }),
])
def test_fresh_and_service_owned_states_never_call_fchown(
    monkeypatch, observation,
) -> None:
    monkeypatch.setattr(
        effect, "observe_legacy_state_v1", lambda _request: observation,
    )
    monkeypatch.setattr(
        effect.os, "fchown",
        lambda *_args: pytest.fail("no-op adoption attempted fchown"),
    )
    raw = effect._LegacyStateEffectsV1(
        _request(), object(), object(), lambda: None,
    )
    assert raw.adopt_authoring(observation) == observation


def test_callable_lookalike_is_rejected_before_filesystem_io(monkeypatch) -> None:
    request = _request()
    monkeypatch.setattr(
        effect, "_bound_chains_v1",
        lambda *_args: pytest.fail("invalid maintenance reached filesystem"),
    )
    with pytest.raises(cutover_guard.ContractCutoverGuardError) as denied:
        with effect.locked_legacy_state_effects_v1(
            request, request._account, lambda: True,
        ):
            pass
    assert denied.value.code == "cutover_session_invalid"


def test_effect_revalidates_maintenance_before_every_attestation(monkeypatch) -> None:
    request, events = _request(), []

    class Chain:
        root_fd = 7

        def attest_metadata(self, _expected):
            events.append("filesystem")

        def close(self):
            pass

    def require_maintenance(_session):
        events.append("maintenance")

    monkeypatch.setattr(effect, "_require_maintenance_session_v1", require_maintenance)
    monkeypatch.setattr(effect, "_require_effect_platform_v1", lambda: None)
    monkeypatch.setattr(effect, "require_canonical_legacy_state_request_v1", lambda _: None)
    monkeypatch.setattr(effect, "build_legacy_state_request_v1", lambda *_: request)
    monkeypatch.setattr(effect, "_bound_chains_v1", lambda *_: (Chain(), Chain(), ({}, {})))
    monkeypatch.setattr(effect, "open_legacy_journal_lock_v1", lambda _fd: 9)
    monkeypatch.setattr(effect.fcntl, "flock", lambda *_: None)
    monkeypatch.setattr(effect.os, "close", lambda _fd: None)
    monkeypatch.setattr(
        effect, "require_legacy_journal_lock_bound_v1",
        lambda *_: events.append("filesystem"),
    )
    monkeypatch.setattr(effect, "LegacyStateJournalStoreV1", lambda _fd: object())
    monkeypatch.setattr(effect, "observe_legacy_state_v1", lambda _: object())
    with effect.locked_legacy_state_effects_v1(
        request, request._account, object(),
    ) as capability:
        capability.observe()
    attest = ["maintenance", "filesystem", "filesystem", "filesystem", "maintenance"]
    assert events == ["maintenance"] + attest * 4


def test_release_closes_every_fd_when_unlock_fails(monkeypatch) -> None:
    events = []

    class Capability:
        @staticmethod
        def deactivate():
            events.append("deactivate")

    class Chain:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append(self.name)

    monkeypatch.setattr(
        effect.fcntl, "flock",
        lambda *_args: (_ for _ in ()).throw(OSError("unlock")),
    )
    monkeypatch.setattr(effect.os, "close", lambda fd: events.append(("close", fd)))
    with pytest.raises(effect.LegacyStateEffectPosixError) as denied:
        effect._release_context_v1(
            Capability(), 9, True, True, Chain("journal"), Chain("state"),
        )
    assert isinstance(denied.value.__cause__, effect.PosixDirectoryError)
    assert isinstance(denied.value.__cause__.__cause__, OSError)
    assert events == ["deactivate", ("close", 9), "journal", "state"]


def test_forked_release_closes_without_unlock(monkeypatch) -> None:
    events = []
    capability = SimpleNamespace(deactivate=lambda: events.append("deactivate"))
    chain = SimpleNamespace(close=lambda: events.append("close-chain"))
    monkeypatch.setattr(
        effect.fcntl, "flock",
        lambda *_args: pytest.fail("forked cleanup attempted LOCK_UN"),
    )
    monkeypatch.setattr(effect.os, "close", lambda fd: events.append(("close", fd)))
    effect._release_context_v1(capability, 9, True, False, chain, chain)
    assert events == ["deactivate", ("close", 9), "close-chain", "close-chain"]
