"""Composition seam; native authentication and unit policy remain cluster oracles."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as native
from executor_birth_account_identity import PosixAccountRecordV1, PosixAccountSnapshotV1
from executor_birth_retention import RetentionError
from install import birth_retention_installed_environment as reader
from test_executor_birth_admin_preflight_launch import _entry, _materials


@pytest.fixture
def scenario(monkeypatch):
    entry = _entry()
    materials = _materials(entry)
    second = entry._replace(entry_id='service-other', target_environment=())
    materials = materials._replace(catalog=SimpleNamespace(entries=(entry, second)))
    account = PosixAccountSnapshotV1(
        PosixAccountRecordV1('metnos', 990, 991, '/var/lib/metnos', '/usr/sbin/nologin'),
        (44, 991))
    calls = []
    monkeypatch.setattr(native, '_authenticate_fixed_ownership_snapshot_v1', lambda: object())
    def load(snapshot, *, review_sources):
        assert review_sources is False
        return ('selected-head', materials)
    monkeypatch.setattr(native, '_load_installed_preflight_materials_v1', load)
    monkeypatch.setattr(native, '_check_installed_service_v1', lambda m, e: calls.append(e.entry_id))
    monkeypatch.setattr(reader, 'resolve_posix_account_snapshot_v1', lambda name: account)
    return SimpleNamespace(materials=materials, entry=entry, account=account, calls=calls)


def test_per_service_inputs_preserve_absent_overrides_without_ambient_inheritance(scenario, monkeypatch):
    monkeypatch.setenv('METNOS_USER_DATA', '/untrusted/data')
    monkeypatch.setenv('METNOS_APPROVALS_DB', '/untrusted/approval')
    checks = []
    result = reader._capture_installed_environment_v1(require_stability=lambda: checks.append(True))
    assert result.target_environments == (
        ('service-probe', (('PROBE_MODE', 'signed'),)), ('service-other', ()))
    expected_baseline = (('HOME', '/var/lib/metnos'),
                         ('LOGNAME', 'metnos'),
                         ('METNOS_INSTALL_ROOT', '/release'),
                         ('SHELL', '/usr/sbin/nologin'),
                         ('USER', 'metnos'))
    assert result.launch_environments == (
        ('service-probe', (*expected_baseline[:3], ('PROBE_MODE', 'signed'),
                           *expected_baseline[3:])),
        ('service-other', expected_baseline))
    assert scenario.calls == ['service-probe', 'service-other'] * 2
    assert len(checks) == 5
    assert result.layout.data.as_posix() == '/var/lib/metnos/.local/share/metnos'
    with pytest.raises(FrozenInstanceError):
        result.target_environments = ()


@pytest.mark.parametrize('fault', ['account', 'xdg', 'cache', 'workspace', 'empty', 'drift', 'unit', 'boundary'])
def test_native_refusal_or_changed_capture_stops_selection(scenario, monkeypatch, fault):
    materials = scenario.materials
    if fault == 'account':
        monkeypatch.setattr(reader, 'resolve_posix_account_snapshot_v1', lambda name:
            replace(scenario.account, record=replace(scenario.account.record, uid=123)))
    elif fault in ('xdg', 'cache', 'workspace', 'empty'):
        names = {'xdg': 'METNOS_USER_DATA', 'cache': 'METNOS_USER_CACHE',
                 'workspace': 'METNOS_WORKSPACE'}
        entries = () if fault == 'empty' else (scenario.entry._replace(target_environment=(
            native._ServiceEnvironmentV1(names[fault], '/foreign'),)),)
        materials = materials._replace(catalog=SimpleNamespace(entries=entries))
        monkeypatch.setattr(native, '_load_installed_preflight_materials_v1', lambda *a, **k: ('head', materials))
    elif fault == 'drift':
        heads = iter(('first', 'second'))
        monkeypatch.setattr(native, '_load_installed_preflight_materials_v1', lambda *a, **k: (next(heads), materials))
    elif fault == 'unit':
        def unit(m, e):
            scenario.calls.append(e.entry_id)
            if len(scenario.calls) == 3:
                raise RetentionError('retention_installation_changed')
        monkeypatch.setattr(native, '_check_installed_service_v1', unit)
    def boundary():
        if fault == 'boundary':
            raise RetentionError('retention_exclusion_lost')
    with pytest.raises(RetentionError):
        reader._capture_installed_environment_v1(require_stability=boundary)


def test_checker_is_required():
    with pytest.raises(TypeError):
        reader._capture_installed_environment_v1(require_stability=None)
