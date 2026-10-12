"""Readonly borrowed capabilities retain the existing live-checker contract."""
from contextlib import contextmanager, nullcontext
from dataclasses import FrozenInstanceError
import os
from types import SimpleNamespace

import pytest

from executor_birth_retention import RetentionError
from install.birth_retention_exclusion import (
    _RetentionExclusionContextV1, _require_authority_lock_v1,
)
from install.birth_ownership_authority_provisioner import (
    _LOCK_BASENAME_V1, _provisioning_exclusion_v1, _provisioning_lock,
)


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX authority lock')
def test_borrowed_context_checks_real_held_descriptor_and_scope(tmp_path):
    tmp_path.chmod(0o755)
    account, layout, session = object(), object(), object()
    active = True
    calls = []
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        def held_checker():
            if not active:
                raise RetentionError('retention_exclusion_lost')
            _require_authority_lock_v1(descriptor, tmp_path / _LOCK_BASENAME_V1,
                                      (os.geteuid(), os.getegid()))

        def checker():
            calls.append(True)
            held_checker()

        context = _RetentionExclusionContextV1(
            session, account, layout, ('head', 'build'), checker, held_checker)
        context()
        context.require_exclusion()
        for _ in range(100):
            context.require_held_exclusion()
        assert len(calls) == 2
        assert context.session is session and context.account is account and context.layout is layout
        assert context.frontier == ('head', 'build')
        for name in ('session', 'account', 'layout', 'frontier', '_checker', '_held_checker'):
            with pytest.raises(FrozenInstanceError):
                setattr(context, name, object())
        active = False
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context()
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context.require_held_exclusion()
    with pytest.raises(RetentionError, match='retention_exclusion_lost'):
        context.require_exclusion()


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX authority lock')
def test_context_never_swallows_lock_loss(tmp_path):
    tmp_path.chmod(0o755)
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        def checker():
            _require_authority_lock_v1(descriptor, tmp_path / _LOCK_BASENAME_V1,
                                      (os.geteuid(), os.getegid()))

        context = _RetentionExclusionContextV1(
            object(), object(), object(), ('head', 'build'), checker, checker)
        context()
        (tmp_path / _LOCK_BASENAME_V1).rename(tmp_path / 'original')
        (tmp_path / _LOCK_BASENAME_V1).touch(mode=0o600)
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context()
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            context.require_held_exclusion()


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX authority lock')
@pytest.mark.parametrize('dangling', [False, True])
def test_existing_raw_authority_exclusion_admits_recovery_without_reopening_normal_writes(
        tmp_path, dangling):
    from executor_birth_authority_files import OwnershipAuthorityError
    tmp_path.chmod(0o755)
    root = tmp_path / 'retention-maintenance-v1'
    root.mkdir(mode=0o755)
    active = root / 'active.json'
    if dangling:
        active.symlink_to(root / 'missing-intent')
    else:
        active.write_bytes(b'original interrupted maintenance')
        active.chmod(0o600)
    before = active.lstat()
    with _provisioning_exclusion_v1(tmp_path, root_owned=False) as descriptor:
        _require_authority_lock_v1(descriptor, tmp_path / _LOCK_BASENAME_V1,
                                  (os.geteuid(), os.getegid()))
    with pytest.raises(OwnershipAuthorityError, match='birth_retention_recovery_required'):
        with _provisioning_lock(tmp_path, root_owned=False):
            pytest.fail('normal authority writes must remain blocked')
    assert (active.lstat().st_dev, active.lstat().st_ino) == (before.st_dev, before.st_ino)


@pytest.mark.parametrize('writer_drift', [False, True])
def test_administrative_observations_bracket_held_inventory_checks(
        tmp_path, monkeypatch, writer_drift):
    import config
    import contract_cutover_guard as maintenance_guard
    import executor_birth_account_identity as accounts
    import executor_birth_ownership_chain as chain
    import executor_birth_ownership_coordinator as coordinator
    import executor_birth_startup_gate as startup_gate
    from install import birth_ownership_authority_provisioner as authority
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
    monkeypatch.setattr(config, 'PATH_USER_DATA', layout.data)
    held = []
    capabilities = {name: object() for name in ('deployment', 'startup', 'authority', 'maintenance')}
    all_locks = ('deployment', 'startup', 'authority', 'birth', 'maintenance')
    observations = []
    drift = False

    @contextmanager
    def hold(name, value):
        held.append(name)
        try:
            yield value
        finally:
            assert held.pop() == name

    def require_held(name, value):
        assert tuple(held) == all_locks and value is capabilities[name]

    birth = SimpleNamespace(_require_exclusive_global_lock=lambda: require_held('birth', birth))
    capabilities['birth'] = birth
    monkeypatch.setattr(coordinator, '_deployment_exclusion_v1',
                        lambda: hold('deployment', capabilities['deployment']))
    monkeypatch.setattr(coordinator, '_require_deployment_lock_session_v1',
                        lambda value: require_held('deployment', value))
    monkeypatch.setattr(startup_gate, '_exclusive_startup_gate_v1',
                        lambda: hold('startup', capabilities['startup']))
    monkeypatch.setattr(startup_gate, '_require_exclusive_startup_gate_session_v1',
                        lambda value: require_held('startup', value))
    monkeypatch.setattr(authority, '_provisioning_exclusion_v1',
                        lambda root, root_owned: hold('authority', capabilities['authority']))
    monkeypatch.setattr(exclusion, '_require_authority_lock_v1',
                        lambda value, path, owner: require_held('authority', value))
    monkeypatch.setattr(exclusion, '_birth_exclusion_v1', lambda value: hold('birth', birth))
    monkeypatch.setattr(maintenance_guard, '_contract_cutover_guard_for_service_user_v1',
                        lambda name, catalog_trusted_owner: hold(
                            'maintenance', (capabilities['maintenance'], {})))
    monkeypatch.setattr(maintenance_guard, '_require_held_maintenance_session_v1',
                        lambda value: require_held('maintenance', value))

    def observe_maintenance(value):
        require_held('maintenance', value)
        observations.append('maintenance')

    def observe_units(name):
        assert tuple(held) == all_locks and name == account.record.name
        observations.append('units')
        if drift:
            raise RetentionError('retention_writer_running')

    def read_window():
        assert tuple(held) == all_locks
        observations.append('frontier')
        return SimpleNamespace(
            required_head=SimpleNamespace(head_id='head'),
            required_distribution=SimpleNamespace(
                installation_root=config.PATH_ROOT,
                identity=SimpleNamespace(closed_build_id='build')))

    monkeypatch.setattr(maintenance_guard, '_require_maintenance_session_v1', observe_maintenance)
    monkeypatch.setattr(exclusion, '_require_empty_units_v1', observe_units)
    monkeypatch.setattr(chain, 'OwnershipChainStore', lambda: SimpleNamespace(
        read_required_window_v1=read_window))

    expected_exit = (pytest.raises(RetentionError, match='retention_writer_running')
                     if writer_drift else nullcontext())
    with expected_exit:
        with exclusion.administrative_retention_exclusion_v1() as context:
            baseline = tuple(observations)
            assert baseline == ('frontier', 'maintenance', 'frontier', 'units')
            for _ in range(100):
                context.require_held_exclusion()
            assert tuple(observations) == baseline
            context.require_exclusion()
            assert observations.count('units') == 2
            drift = writer_drift
            context.require_held_exclusion()
    assert observations.count('units') == 3
    assert not held
    for checker in (context.require_held_exclusion, context.require_exclusion):
        with pytest.raises(RetentionError, match='retention_exclusion_lost'):
            checker()


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
