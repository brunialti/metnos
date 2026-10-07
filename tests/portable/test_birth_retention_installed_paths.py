"""Component seams only: host authentication and administrative UID are simulated."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import executor_birth_admin_preflight as native
from executor_birth_retention import RetentionError
from install import birth_retention_installed_paths as paths
from install.birth_retention_path_probe import CUSTODY_PROTOCOL, PATH_KEYS
from test_executor_birth_admin_preflight_launch import _entry, _materials

ROOT = Path(__file__).resolve().parents[2]
native_posix = pytest.mark.skipif(
    os.name != 'posix', reason='native administrative POSIX path probe and resource limits')


def test_static_environment_is_native_and_has_no_ambient_defaults(monkeypatch):
    monkeypatch.setenv('HOME', '/hostile')
    monkeypatch.setenv('METNOS_USER_DATA', '/hostile')
    entry = _entry(notify=True)
    result = dict(native._static_launch_environment_v1(_materials(entry), entry))
    assert result == dict(HOME='/var/lib/metnos', LOGNAME='metnos',
                         METNOS_INSTALL_ROOT='/release', SHELL='/usr/sbin/nologin',
                         USER='metnos', PROBE_MODE='signed')
    collision = entry._replace(target_environment=(native._ServiceEnvironmentV1('HOME', '/bad'),))
    with pytest.raises(native.PreflightError):
        native._static_launch_environment_v1(_materials(collision), collision)


@pytest.mark.parametrize('fault', [None, 'disagreement', 'path_drift', 'environment_drift', 'boundary', 'nonroot', 'native'])
def test_capture_checks_paths_and_final_materials(monkeypatch, fault):
    entry = _entry()
    other = entry._replace(entry_id='other')
    if fault == 'native':
        other = _entry(execution_kind='native_executable')._replace(entry_id='native')
    materials = _materials(entry)._replace(catalog=SimpleNamespace(entries=(entry, other)))
    environment = SimpleNamespace(materials=materials)
    calls = []
    def capture(**kwargs):
        calls.append('capture')
        return object() if fault == 'environment_drift' and calls.count('capture') == 3 else environment
    monkeypatch.setattr(paths, '_capture_installed_environment_v1', capture)
    monkeypatch.setattr(paths.os, 'geteuid', lambda: 1 if fault == 'nonroot' else 0, raising=False)
    count = []
    def probe(m, e):
        count.append(e.entry_id)
        value = '/a'
        if fault == 'disagreement' and e.entry_id == 'other' or fault == 'path_drift' and len(count) > 2:
            value = '/b'
        return paths._InstalledRuntimeCompositionV1(
            (('approvals', value),), CUSTODY_PROTOCOL,
        )
    monkeypatch.setattr(paths, '_probe_service_v1', probe)
    def boundary():
        if fault == 'boundary':
            raise RetentionError('retention_exclusion_lost')
    if fault and fault != 'native':
        with pytest.raises(RetentionError):
            paths._capture_installed_paths_v1(require_stability=boundary)
    else:
        result = paths._capture_installed_paths_v1(require_stability=boundary)
        composition = paths._InstalledRuntimeCompositionV1(
            (('approvals', '/a'),), CUSTODY_PROTOCOL,
        )
        expected = ((('service-probe', composition),)
                    if fault == 'native' else
                    (('service-probe', composition), ('other', composition)))
        assert result.observations == expected
        assert result.non_python_services == (('native',) if fault == 'native' else ())
        assert len(count) == (2 if fault == 'native' else 4) and len(calls) == 3


@pytest.mark.parametrize('fault', [None, 'missing', 'relative', 'nul', 'custody', 'oversize', 'timeout', 'exit', 'native'])
def test_subprocess_boundary(monkeypatch, fault):
    entry = _entry(execution_kind='native_executable' if fault == 'native' else 'python_module')
    monkeypatch.setattr(native, '_trusted_python_path_v1', lambda *a: ('/release/runtime', '/trusted'))
    monkeypatch.setenv('PYTHONPATH', '/hostile')
    def run(args, **kw):
        assert args[:4] == ['/usr/bin/python3', '-I', '-B', '-c']
        probe_path = str(Path('/release') / 'install/birth_retention_path_probe.py')
        assert 'RLIMIT_FSIZE' in args[4] and repr(probe_path) in args[4]
        assert 'resolve_runtime_composition' in args[4]
        assert 'PYTHONPATH' not in kw['env'] and kw['env']['HOME'] == '/var/lib/metnos'
        assert kw['cwd'] == '/release/runtime' and kw['timeout'] == 30
        if fault == 'timeout':
            raise subprocess.TimeoutExpired(args, 30)
        observed = {k: '/resolved/' + k for k in PATH_KEYS}
        if fault == 'missing': observed.pop('approvals')
        if fault == 'relative': observed['approvals'] = 'relative'
        if fault == 'nul': observed['approvals'] = '/a\0b'
        response = {'paths': observed, 'terminal_workspace_custody':
                    'wrong' if fault == 'custody' else CUSTODY_PROTOCOL}
        raw = b'x' * 65537 if fault == 'oversize' else json.dumps(response).encode()
        kw['stdout'].write(raw)
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0)
    monkeypatch.setattr(paths.subprocess, 'run', run)
    if fault:
        with pytest.raises(RetentionError): paths._probe_service_v1(_materials(entry), entry)
    else:
        result = paths._probe_service_v1(_materials(entry), entry)
        assert set(dict(result.paths)) == PATH_KEYS
        assert result.terminal_workspace_custody == CUSTODY_PROTOCOL


def fresh_probe(tmp_path, extra='', env_extra=None):
    # UID seam only: actual native modules and a fresh isolated interpreter.
    # No system installation, unit state, custody or native root claim.
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    bootstrap = (
        'import os, sys, runpy, json; os.geteuid=lambda:0; '
        f'sys.path.insert(0, {str(ROOT / "runtime")!r}); '
        f'p=runpy.run_path({str(ROOT / "install/birth_retention_path_probe.py")!r}); '
        + extra + f'print(json.dumps(p["resolve_paths"]({str(ROOT)!r})))')
    env = {'HOME': str(home), 'METNOS_INSTALL_ROOT': str(ROOT), **(env_extra or {})}
    return subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap], cwd=tmp_path,
                          env=env, capture_output=True, text=True, timeout=30)


@native_posix
def test_native_resolvers_in_fresh_environment_create_no_stores(tmp_path):
    result = fresh_probe(tmp_path, env_extra={'METNOS_APPROVALS_DB': 'relative.sqlite',
        'METNOS_PROPOSALS_STATE_DB': 'p.sqlite', 'METNOS_LLM_COST_LOG_PATH': 'cost.jsonl'})
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert set(observed) == PATH_KEYS
    assert observed['approvals'] == str(tmp_path / 'relative.sqlite')
    assert observed['proposals_state'] == str(tmp_path / 'p.sqlite')
    assert observed['llm_cost'] == str(tmp_path / 'cost.jsonl')
    assert observed['scheduler'] == str(tmp_path / 'home/.local/state/metnos/scheduler_v2.sqlite')
    assert observed['lre_artifact_store'] == observed['PATH_DURABLE_ARTIFACTS']
    assert observed['lre_source_authority'] == str(
        tmp_path / 'home/.local/state/metnos/durable_workloads/source_authority.sqlite3')
    assert list(tmp_path.iterdir()) == [tmp_path / 'home']
    assert list((tmp_path / 'home').iterdir()) == []


@native_posix
def test_fresh_probe_reports_production_custody_with_native_paths(tmp_path):
    result = fresh_probe(
        tmp_path,
        extra='p["resolve_paths"] = p["resolve_runtime_composition"]; ',
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert set(observed['paths']) == PATH_KEYS
    assert observed['terminal_workspace_custody'] == CUSTODY_PROTOCOL
    assert list(tmp_path.iterdir()) == [tmp_path / 'home']
    assert list((tmp_path / 'home').iterdir()) == []


def test_lre_factory_overrides_use_writer_path_normalizers_without_opening_stores(tmp_path, monkeypatch):
    from durable_workloads.artifacts import ArtifactStore
    from durable_workloads.runtime_bindings import RuntimeFactory
    from durable_workloads.source_authority import authority_file_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('USERPROFILE', str(tmp_path / 'home'))
    factory = RuntimeFactory(registry_factory=lambda: pytest.fail('registry opened'),
                             artifact_root='a/../artifacts',
                             source_authority_path='~/state/authority.sqlite3')
    artifact_root, authority_path = factory.selected_storage_paths()
    assert ArtifactStore.root_path(artifact_root) == tmp_path / 'artifacts'
    assert authority_file_path(authority_path) == tmp_path / 'home/state/authority.sqlite3'
    assert list(tmp_path.iterdir()) == []


@native_posix
def test_probe_rejects_nonroot(tmp_path):
    extra = 'os.geteuid=lambda:1; '
    result = fresh_probe(tmp_path, extra=extra)
    assert result.returncode != 0
    assert 'requires root' in result.stderr


@native_posix
def test_physical_cost_default_and_equivalent_relative_overrides(tmp_path):
    first = fresh_probe(tmp_path, env_extra={'METNOS_APPROVALS_DB': 'a/../approval.db'})
    second = fresh_probe(tmp_path, env_extra={'METNOS_APPROVALS_DB': str(tmp_path / 'approval.db')})
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    a, b = json.loads(first.stdout), json.loads(second.stdout)
    assert a == b
    assert a['llm_cost'] == str(ROOT / 'data/telemetry/llm_usage.jsonl')


@native_posix
def test_sys_path_change_after_probe_load_cannot_displace_selected_package(tmp_path):
    foreign = tmp_path / 'foreign'
    package = foreign / 'durable_workloads'
    package.mkdir(parents=True)
    marker = tmp_path / 'executed'
    (package / '__init__.py').write_text(f'open({str(marker)!r}, "w").close()')
    result = fresh_probe(tmp_path, extra=f'sys.path.insert(0, {str(foreign)!r}); ')
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == PATH_KEYS
    assert not marker.exists()


@native_posix
def test_actual_child_output_budget(tmp_path, monkeypatch):
    # Synthetic installed worker: exercises the real OS output bound only.
    (tmp_path / 'install').mkdir()
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'install/birth_retention_path_probe.py').write_text(
        "def resolve_runtime_composition(root):\n"
        "    return {'paths': {'oversize': 'x' * 1000000}, "
        "'terminal_workspace_custody': 'durable-job-metadata-v1'}\n")
    entry = _entry()._replace(target_executable=sys.executable,
                             target_working_directory=str(tmp_path / 'runtime'))
    materials = _materials(entry)
    materials.descriptor.installation_root = str(tmp_path)
    materials.descriptor.python_executable = sys.executable
    monkeypatch.setattr(native, '_trusted_python_path_v1', lambda *a: tuple(sys.path))
    original = paths.tempfile.TemporaryFile
    sizes = []
    @contextmanager
    def bounded_output():
        with original() as output:
            yield output
            sizes.append(os.fstat(output.fileno()).st_size)
    monkeypatch.setattr(paths.tempfile, 'TemporaryFile', bounded_output)
    with pytest.raises(RetentionError):
        paths._probe_service_v1(materials, entry)
    assert sizes == [65536]  # Fails if the OS bound is removed, not just parsing.


@native_posix
def test_native_resolution_has_no_persistent_mutating_python_audit_events(tmp_path):
    hook = """attempted = []
def audit(event, args):
    if event == 'open':
        path, mode, flags = args
        if path != os.devnull and ((mode and any(c in mode for c in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            attempted.append((event, repr(args)))
            raise RuntimeError('write attempted')
    if event in ('os.chmod', 'os.chown', 'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.link', 'os.symlink', 'subprocess.Popen', 'socket.connect'):
        attempted.append((event, repr(args)))
        raise RuntimeError('mutation attempted: ' + event)
sys.addaudithook(audit)
original = p['resolve_paths']
def checked(root):
    result = original(root)
    assert not attempted, attempted
    return result
p['resolve_paths'] = checked
"""
    result = fresh_probe(tmp_path, extra=f'exec({hook!r}); ')
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == PATH_KEYS


def test_scheduler_writer_uses_the_same_native_default():
    import config
    from scheduler_v2.storage import DEFAULT_DB_PATH
    assert DEFAULT_DB_PATH == config.DB_SCHEDULER_V2
    assert DEFAULT_DB_PATH == config.PATH_USER_STATE / 'scheduler_v2.sqlite'
