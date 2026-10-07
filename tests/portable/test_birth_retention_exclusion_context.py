"""Readonly borrowed capabilities retain the existing live-checker contract."""
from dataclasses import FrozenInstanceError
import os
from types import SimpleNamespace

import pytest

from executor_birth_retention import RetentionError
from install.birth_retention_exclusion import (
    _RetentionExclusionContextV1, _require_authority_lock_v1,
)
from install.birth_ownership_authority_provisioner import (
    _LOCK_BASENAME_V1, _provisioning_exclusion_v1,
)


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX authority lock')
def test_borrowed_context_checks_real_held_descriptor_and_scope(tmp_path):
    tmp_path.chmod(0o755)
    account, layout, session = object(), object(), object()
    active = True
    calls = []
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        def checker():
            calls.append(True)
            if not active:
                raise RetentionError('retention_exclusion_lost')
            _require_authority_lock_v1(descriptor, tmp_path / _LOCK_BASENAME_V1,
                                      (os.geteuid(), os.getegid()))
        context = _RetentionExclusionContextV1(session, account, layout, ('head', 'build'), checker)
        context()
        context.require_exclusion()
        assert len(calls) == 2
        assert context.session is session and context.account is account and context.layout is layout
        assert context.frontier == ('head', 'build')
        for name in ('session', 'account', 'layout', 'frontier', '_checker'):
            with pytest.raises(FrozenInstanceError):
                setattr(context, name, object())
        active = False
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context()
    with pytest.raises(RetentionError, match='retention_exclusion_lost'):
        context.require_exclusion()


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX authority lock')
def test_context_never_swallows_lock_loss(tmp_path):
    tmp_path.chmod(0o755)
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        context = _RetentionExclusionContextV1(object(), object(), object(), ('head', 'build'),
            lambda: _require_authority_lock_v1(descriptor, tmp_path / _LOCK_BASENAME_V1,
                                               (os.geteuid(), os.getegid())))
        context()
        (tmp_path / _LOCK_BASENAME_V1).rename(tmp_path / 'original')
        (tmp_path / _LOCK_BASENAME_V1).touch(mode=0o600)
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context()


@pytest.mark.parametrize('matching_data', [True, False])
def test_administrative_data_binding_before_any_lock(tmp_path, monkeypatch, matching_data):
    import config
    import executor_birth_account_identity as accounts
    import executor_birth_ownership_coordinator as coordinator
    from install import birth_retention_exclusion as exclusion

    account = accounts.PosixAccountSnapshotV1(
        accounts.PosixAccountRecordV1('service', 1234, 1234, str(tmp_path), '/bin/false'), ())
    layout = accounts.metnos_xdg_layout_v1(account.record)
    monkeypatch.setattr(exclusion, 'sys', SimpleNamespace(platform='linux'))
    monkeypatch.setattr(os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(os, 'getegid', lambda: 0, raising=False)
    monkeypatch.setattr(accounts, 'resolve_posix_account_snapshot_v1', lambda name: account)
    monkeypatch.setattr(config, 'PATH_USER_CONFIG', layout.config)
    monkeypatch.setattr(config, 'PATH_USER_STATE', layout.state)
    monkeypatch.setattr(config, 'PATH_USER_DATA', layout.data if matching_data else tmp_path / 'other-data')
    reached = []
    class StopBeforeLocks(Exception):
        pass
    def stop():
        reached.append(True)
        raise StopBeforeLocks
    monkeypatch.setattr(coordinator, '_deployment_exclusion_v1', stop)
    error = StopBeforeLocks if matching_data else RetentionError
    with pytest.raises(error) as failure:
        with exclusion.administrative_retention_exclusion_v1():
            pytest.fail('test must stop before acquiring locks')
    assert reached == ([True] if matching_data else [])
    if not matching_data:
        assert 'retention_service_identity_invalid' in str(failure.value)
