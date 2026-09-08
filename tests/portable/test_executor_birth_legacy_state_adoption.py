"""Pure orchestration tests for crash-resumable legacy-state adoption."""
from __future__ import annotations

from pathlib import PurePosixPath

import pytest

import executor_birth_account_identity as identity
import executor_birth_legacy_state_journal as journal
import executor_birth_legacy_state_policy as policy
from executor_birth_legacy_state_request import build_legacy_state_request_v1
from install.executor_birth_legacy_state_adoption import (
    _complete_legacy_state_ready_v1,
    LegacyStateAdoptionError,
    prepare_legacy_state_authoring_v1,
)


_DISTRIBUTION = "sha256:" + "4" * 64


def _request():
    account = identity.PosixAccountSnapshotV1(
        identity.PosixAccountRecordV1(
            "metnos", 991, 992,
            "/var/lib/metnos-service", "/usr/sbin/nologin",
        ),
        (992,),
    )
    return build_legacy_state_request_v1(account, _DISTRIBUTION)


def _entry(path: str, uid: int, inode: int):
    return policy.LegacyPathObservationV1(
        PurePosixPath(path), policy.LegacyNodeKindV1.directory,
        uid, 0 if uid == 0 else 992, 0o700, 2, None, None,
        False, False, 7, inode,
    )


def _root_authoring():
    return policy.LegacyStateObservationV1((
        _entry("contract-authoring", 0, 20),
        _entry("contract-authoring/v1", 0, 21),
    ))


class _Effects:
    def __init__(self, observation):
        self.raw = []
        self.observation = observation
        self.observations = 0
        self.adoptions = 0
        self.fail_sequence_once = None
        self.checkpoints = []

    def load_records(self):
        return tuple(self.raw)

    def append_record(self, sequence, encoded):
        if self.fail_sequence_once == sequence:
            self.fail_sequence_once = None
            raise RuntimeError("simulated crash")
        if sequence == len(self.raw):
            self.raw.append(encoded)
        elif self.raw[sequence] != encoded:
            raise RuntimeError("record conflict")

    def observe(self):
        self.observations += 1
        return self.observation

    def adopt_authoring(self, before):
        self.adoptions += 1
        self.observation = policy.project_legacy_state_adoption_v1(
            _request(), before,
        )
        return self.observation

    def checkpoint(self, name):
        self.checkpoints.append(name)


def test_adopted_state_reaches_terminal_and_terminal_replay_is_historical() -> None:
    effects = _Effects(_root_authoring())
    prepared = prepare_legacy_state_authoring_v1(_request(), effects)
    assert prepared.changed is True and prepared.ready is False
    first = _complete_legacy_state_ready_v1(
        _request(), effects, expected_record_sha256=prepared.record_sha256,
    )
    assert first.changed is True and first.ready is True
    assert [item.state for item in journal.decode_legacy_state_chain_v1(
        tuple(effects.raw),
    )] == list(journal.LegacyStateV1)
    observations = effects.observations
    repeated = prepare_legacy_state_authoring_v1(_request(), effects)
    assert repeated.changed is False
    assert repeated.record_sha256 == first.record_sha256
    assert effects.observations == observations


def test_ready_refuses_fresh_state_before_contract_convergence() -> None:
    effects = _Effects(policy.LegacyStateObservationV1(()))
    prepared = prepare_legacy_state_authoring_v1(_request(), effects)
    with pytest.raises(
        LegacyStateAdoptionError, match="birth_legacy_state_recovery_required",
    ):
        _complete_legacy_state_ready_v1(
            _request(), effects,
            expected_record_sha256=prepared.record_sha256,
        )
    assert len(effects.raw) == 3


def test_crash_after_convergence_resumes_before_ready_append() -> None:
    effects = _Effects(_root_authoring())
    prepared = prepare_legacy_state_authoring_v1(_request(), effects)
    before = tuple(effects.raw)

    resumed = prepare_legacy_state_authoring_v1(_request(), effects)

    assert resumed.record_sha256 == prepared.record_sha256
    assert resumed.changed is False and resumed.ready is False
    assert tuple(effects.raw) == before
    ready = _complete_legacy_state_ready_v1(
        _request(), effects,
        expected_record_sha256=resumed.record_sha256,
    )
    assert ready.ready is True
    assert len(effects.raw) == 4


def test_crash_after_chown_before_record_resumes_from_normalized_target() -> None:
    effects = _Effects(_root_authoring())
    effects.fail_sequence_once = 2
    with pytest.raises(
        LegacyStateAdoptionError, match="birth_legacy_state_recovery_required",
    ):
        prepare_legacy_state_authoring_v1(_request(), effects)
    assert len(effects.raw) == 2
    assert effects.adoptions == 1
    assert policy.classify_legacy_state_v1(
        _request(), effects.observation,
    ) is policy.LegacyStateDispositionV1.exact_service
    result = prepare_legacy_state_authoring_v1(_request(), effects)
    assert result.changed is True
    assert result.ready is False
    assert len(effects.raw) == 3
    assert effects.adoptions == 2


def test_inode_substitution_after_inventory_fails_before_adoption() -> None:
    effects = _Effects(_root_authoring())
    effects.fail_sequence_once = 2
    with pytest.raises(LegacyStateAdoptionError):
        prepare_legacy_state_authoring_v1(_request(), effects)
    adopted = list(effects.observation.entries)
    first = adopted[0]
    adopted[0] = policy.LegacyPathObservationV1(
        first.relative_path, first.node_kind, first.uid, first.gid,
        first.mode, first.nlink, first.size, first.content_sha256,
        first.has_access_acl, first.has_default_acl, first.device,
        first.inode + 1,
    )
    effects.observation = policy.LegacyStateObservationV1(tuple(adopted))
    with pytest.raises(
        LegacyStateAdoptionError, match="birth_legacy_state_recovery_required",
    ):
        prepare_legacy_state_authoring_v1(_request(), effects)
    assert effects.adoptions == 1
