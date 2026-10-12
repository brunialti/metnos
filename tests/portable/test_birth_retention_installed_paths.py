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


def _display_entry():
    entry = _entry(execution_kind='native_executable')
    policies = (
        ('ProtectSystem', 'scalar', 'strict'),
        ('PrivateTmp', 'boolean', 'yes'),
        ('NoNewPrivileges', 'boolean', 'yes'),
        ('StandardOutput', 'scalar', 'journal'),
        ('StandardError', 'scalar', 'journal'),
    )
    spec = entry.unit_spec._replace(directives=entry.unit_spec.directives + tuple(
        native._ServiceDirectiveV1('Service', name, kind, (value,))
        for name, kind, value in policies))
    return entry._replace(entry_id='service-side-display',
        unit_name='metnos-side-display.service', target_executable='/usr/bin/Xvfb',
        target_args=(':99', '-screen', '0', '1920x1080x24', '-ac',
                     '-nolisten', 'tcp', '-dpi', '96'),
        target_working_directory='/release', target_environment=(), unit_spec=spec)


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


@pytest.mark.parametrize('fault', [None, 'disagreement', 'path_drift', 'environment_drift', 'boundary', 'nonroot', 'native', 'display'])
def test_capture_checks_paths_and_final_materials(monkeypatch, fault):
    entry = _entry()
    other = entry._replace(entry_id='other')
    if fault == 'native':
        other = _entry(execution_kind='native_executable')._replace(entry_id='native')
    elif fault == 'display':
        other = _display_entry()
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
    if fault not in (None, 'native', 'display'):
        with pytest.raises(RetentionError):
            paths._capture_installed_paths_v1(require_stability=boundary)
    else:
        result = paths._capture_installed_paths_v1(require_stability=boundary)
        composition = paths._InstalledRuntimeCompositionV1(
            (('approvals', '/a'),), CUSTODY_PROTOCOL,
        )
        expected = ((('service-probe', composition),)
                    if fault in ('native', 'display') else
                    (('service-probe', composition), ('other', composition)))
        assert result.observations == expected
        assert result.non_python_services == (('native',) if fault == 'native' else ())
        assert result.non_writing_services == (('service-side-display',) if fault == 'display' else ())
        assert len(count) == (2 if fault in ('native', 'display') else 4) and len(calls) == 3


@pytest.mark.parametrize('fault', [None, 'id', 'basename', 'arguments', 'working_directory',
    'environment', 'ProtectSystem', 'PrivateTmp', 'StandardOutput', 'StandardError',
    'ReadWritePaths', 'duplicate'])
def test_native_display_classification_requires_exact_target_and_readonly_unit(fault):
    entry = _display_entry()
    replacements = {
        'id': {'entry_id': 'another-native'},
        'basename': {'target_executable': '/untrusted/Xvfb'},
        'arguments': {'target_args': entry.target_args + ('-fbdir', '/release/data')},
        'working_directory': {'target_working_directory': '/other'},
        'environment': {'target_environment': (native._ServiceEnvironmentV1('OUTPUT', '/release/data'),)},
    }
    if fault in replacements:
        entry = entry._replace(**replacements[fault])
    elif fault:
        directives = entry.unit_spec.directives
        if fault == 'ReadWritePaths':
            directives += (native._ServiceDirectiveV1('Service', fault, 'path_list', ('/release/data',)),)
        elif fault == 'duplicate':
            directives += (directives[-1],)
        else:
            directives = tuple(item._replace(values=('unsafe',)) if item.name == fault else item
                               for item in directives)
        entry = entry._replace(unit_spec=entry.unit_spec._replace(directives=directives))
    assert paths._is_non_writing_native_service_v1(_materials(entry), entry) is (fault is None)


@pytest.mark.parametrize('fault', [None, 'disagreement', 'environment_drift', 'held', 'full'])
def test_held_capture_compares_native_boundaries_and_probes_each_writer_once(monkeypatch, fault):
    entry = _entry()
    other = entry._replace(entry_id='other')
    materials = _materials(entry)._replace(catalog=SimpleNamespace(entries=(entry, other)))
    environment = SimpleNamespace(materials=materials)
    captures, probes, full_checks, held_checks = [], [], [], []
    def full():
        full_checks.append(True)
        if fault == 'full' and len(full_checks) == 2:
            raise RetentionError('retention_exclusion_lost')
    def held():
        held_checks.append(True)
        if fault == 'held' and probes:
            raise RetentionError('retention_exclusion_lost')
    def capture(*, require_stability):
        require_stability()
        captures.append(True)
        require_stability()
        return object() if fault == 'environment_drift' and len(captures) == 2 else environment
    def probe(m, e):
        probes.append(e.entry_id)
        value = '/other' if fault == 'disagreement' and e.entry_id == 'other' else '/a'
        return paths._InstalledRuntimeCompositionV1((('approvals', value),), CUSTODY_PROTOCOL)
    monkeypatch.setattr(paths.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(paths, '_capture_installed_environment_once_v1', capture)
    monkeypatch.setattr(paths, '_capture_installed_environment_v1',
                        lambda **kw: pytest.fail('duplicate native capture'))
    monkeypatch.setattr(paths, '_probe_service_v1', probe)
    if fault:
        with pytest.raises(RetentionError):
            paths._capture_installed_paths_v1(require_stability=full, require_held_stability=held)
    else:
        observed = paths._capture_installed_paths_v1(require_stability=full, require_held_stability=held)
        assert tuple(row[0] for row in observed.observations) == ('service-probe', 'other')
        assert len(full_checks) == 2 and len(captures) == 2
        assert probes == ['service-probe', 'other']
        assert held_checks
    if fault == 'held':
        assert probes == ['service-probe'] and len(captures) == 1


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


@native_posix
@pytest.mark.parametrize('override', [None, '', 'relative/turns', '~/archived-turns'])
def test_legacy_turn_archive_namespace_matches_the_former_writer(tmp_path, override):
    environment = {} if override is None else {'METNOS_TURN_LOG_ARCHIVE_DIR': override}
    result = fresh_probe(tmp_path, env_extra=environment)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    if not override:
        expected = Path(observed['PATH_USER_DATA']) / 'turns_archive'
    elif override.startswith('~/'):
        expected = tmp_path / 'home' / override[2:]
    else:
        expected = tmp_path / override
    assert observed['turn_archives'] == str(expected)
    assert observed['turn_archives'] != str(Path(observed['PATH_TURNS']) / 'archive')
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


@native_posix
def test_native_birth_adjacent_overrides_are_observed_without_writes(tmp_path):
    overrides = {'SYNT_PROPOSALS_DIR': 'persistent/candidates',
                 'METNOS_SYNT_PROPOSALS_DIR': 'promoter/proposals',
                 'METNOS_SYNTH_ARCHIVE_DIR': 'promoter/archive',
                 'METNOS_PROMOTER_DB': 'promoter/state.sqlite',
                 'METNOS_PROMOTER_BLOB_DIR': 'promoter/blobs',
                 'METNOS_PROMOTER_AUDIT_DIR': 'promoter/audit'}
    result = fresh_probe(tmp_path, env_extra=overrides)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    for name, relative in (
            ('persistent_synth', 'persistent/candidates'),
            ('promoter_proposals', 'promoter/proposals'),
            ('promoter_archive', 'promoter/archive'),
            ('promoter_state', 'promoter/state.sqlite'),
            ('promoter_blobs', 'promoter/blobs'),
            ('promoter_audit', 'promoter/audit')):
        assert observed[name] == str(tmp_path / relative)
    data = tmp_path / 'home/.local/share/metnos'
    assert observed['undo'] == str(data / 'undo.jsonl')
    assert observed['protected_undo_blobs'] == str(data / 'undo_blobs')
    assert observed['turn_feedback'] == str(data / 'turn_feedback.jsonl')
    assert observed['telos_decisions'] == str(data / 'telos_decisions.jsonl')
    assert list(tmp_path.iterdir()) == [tmp_path / 'home']
    assert list((tmp_path / 'home').iterdir()) == []


@native_posix
def test_new_native_path_module_origin_is_checked_before_resolution(tmp_path):
    result = fresh_probe(tmp_path, extra="p['promoter_state'].__file__='/foreign/promoter_state.py'; ")
    assert result.returncode != 0
    assert 'path module outside selected installation: jobs.promoter_state' in result.stderr


@native_posix
@pytest.mark.parametrize('value', ['', 'relative', '/absolute'])
def test_writer_aliases_preserve_native_synth_and_feedback_paths(tmp_path, value):
    result = fresh_probe(tmp_path, env_extra={'SYNT_PROPOSALS_DIR': value}, extra="""
import synt, turn_feedback
from jobs import promoter
assert synt.DEFAULT_PROPOSALS_DIR == p['c'].PATH_SYNT_PROPOSALS
assert synt.DEFAULT_PROPOSALS_DIR == __import__('pathlib').Path(os.environ['SYNT_PROPOSALS_DIR'])
assert turn_feedback.FEEDBACK_PATH == p['c'].PATH_TURN_FEEDBACK
assert turn_feedback.TURNS_DIR == p['c'].PATH_TURNS
assert promoter._proposals_dir is p['promoter_state']._proposals_dir
assert promoter._archive_dir is p['promoter_state']._archive_dir
""")
    assert result.returncode == 0, result.stderr


@native_posix
@pytest.mark.parametrize('override', [None, '', 'relative/history', '/absolute/history', '~/history'])
def test_history_census_preserves_writer_legacy_and_expanded_paths(tmp_path, override):
    from pathlib import Path
    extra = {} if override is None else {'METNOS_HISTORY_DIR': override}
    result = fresh_probe(tmp_path, env_extra=extra)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    default = Path(observed['PATH_USER_DATA']) / '_history'
    writer = Path(override) if override else default
    legacy = Path(override) if override is not None else default
    def resolved(value):
        return str(value if value.is_absolute() else tmp_path / value)
    assert observed['history_backups'] == resolved(writer)
    assert observed['history_legacy'] == resolved(legacy)
    expanded = (tmp_path / 'home/history') if override == '~/history' else writer
    assert observed['history_expanded_backups'] == resolved(expanded)
    assert list((tmp_path / 'home').iterdir()) == []


@pytest.mark.parametrize('override', [None, '', 'relative/history'])
def test_actual_undo_backup_and_restore_use_censused_native_path(tmp_path, monkeypatch, override):
    import config
    from executor_helpers import backup_file_for_undo, restore_file_from_undo_backup
    from history_paths import backup_directory, legacy_blob_directory
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, 'PATH_USER_DATA', tmp_path / 'data')
    monkeypatch.setenv('METNOS_TURN_ID', 'retention-path-test')
    monkeypatch.delenv('METNOS_HISTORY_DIR', raising=False)
    if override is not None:
        monkeypatch.setenv('METNOS_HISTORY_DIR', override)
    original = b'original undo bytes'
    source = tmp_path / 'source'
    source.write_bytes(original)
    blob = Path(backup_file_for_undo(source))
    assert blob.parent == backup_directory() / 'retention-path-test/blob'
    assert blob.read_bytes() == original
    source.write_bytes(b'changed')
    assert restore_file_from_undo_backup(source, str(blob))
    assert source.read_bytes() == original
    if override == '':
        assert legacy_blob_directory() == Path('.')
        assert backup_directory() == tmp_path / 'data/_history'


@native_posix
@pytest.mark.parametrize('override', [None, '', 'relative/audit', '/absolute/audit'])
def test_native_audit_paths_are_censused_without_creating_directories(tmp_path, override):
    extra = {} if override is None else {'METNOS_AUDIT_DIR': override}
    result = fresh_probe(tmp_path, env_extra=extra)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    default = Path(observed['PATH_USER_DATA']) / 'synth_audit'
    directory = Path(override) if override else default
    if not directory.is_absolute():
        directory = tmp_path / directory
    assert observed['synth_audit'] == str(default)
    assert observed['skill_admission_audit'] == str(directory / 'imports.jsonl')
    assert observed['skills_cli_audit'] == str(directory / 'imports.jsonl')
    assert observed['telos_acceptance_markers'] == str(
        Path(observed['PATH_USER_DATA']) / 'proposal_accepts')
    assert list((tmp_path / 'home').iterdir()) == []


def test_import_writers_preserve_distinct_runtime_user_data_fallbacks(tmp_path, monkeypatch):
    import config
    import skill_admission
    from cli import skills_cli
    monkeypatch.setattr(config, 'PATH_USER_DATA', tmp_path / 'configured')
    monkeypatch.setenv('METNOS_USER_DATA', str(tmp_path / 'dynamic'))
    monkeypatch.delenv('METNOS_AUDIT_DIR', raising=False)
    assert skill_admission._audit_log_directory() == tmp_path / 'dynamic/synth_audit'
    assert skills_cli._audit_log_directory() == tmp_path / 'configured/synth_audit'
    assert not (tmp_path / 'dynamic').exists()
    assert not (tmp_path / 'configured').exists()
    assert skill_admission._audit_log_path() == tmp_path / 'dynamic/synth_audit/imports.jsonl'
    assert skills_cli._audit_log_path() == tmp_path / 'configured/synth_audit/imports.jsonl'
    assert (tmp_path / 'dynamic/synth_audit').is_dir()
    assert (tmp_path / 'configured/synth_audit').is_dir()


def test_native_audit_and_proposal_marker_constants_share_censused_source():
    import config
    import loader
    import executor_aging
    import proposal_evaluator
    import synt_stage6_verify
    import proposal_actions
    assert loader._AFFINITY_AUDIT_DIR == config.PATH_SYNTH_AUDIT
    assert executor_aging.EFFICACY_AUDIT_DIR == config.PATH_SYNTH_AUDIT
    assert proposal_evaluator._AUDIT_DIR == config.PATH_SYNTH_AUDIT
    assert synt_stage6_verify.VERIFY_AUDIT_DIR == config.PATH_SYNTH_AUDIT
    assert proposal_actions.SYNT_PENDING_DIR == config.PATH_PROPOSAL_ACCEPTS / 'synt_pending'
    assert proposal_actions.CHANGE_PENDING_DIR == config.PATH_PROPOSAL_ACCEPTS / 'change_pending'
    assert proposal_actions.PIPELINE_PENDING_DIR == config.PATH_PROPOSAL_ACCEPTS / 'pipeline_pending'
