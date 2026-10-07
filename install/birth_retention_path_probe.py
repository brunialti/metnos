"""Read-only path probe, executed in a fresh installed administrative process.

This observes selected paths of the listed native writers, not a complete owner
census. No store, worker, bridge or cleanup object is constructed here.
"""
import os
from pathlib import Path

import approval_registry
import config as c
import durable_runtime_registry as registry
import durable_workloads
from durable_workloads import artifacts
from durable_workloads import migrations
from durable_workloads import runtime_bindings
from durable_workloads import source_authority
import llm_cost_sink
import proposals_cleanup
import proposals_state

_MODULES = ('config', 'approval_registry', 'proposals_state',
            'proposals_cleanup', 'durable_workloads.migrations',
            'durable_workloads.artifacts', 'durable_workloads.source_authority',
            'durable_workloads.runtime_bindings', 'durable_runtime_registry',
            'llm_cost_sink')

_CONFIG_PATHS = (
    'PATH_USER_CONFIG', 'PATH_USER_DATA', 'PATH_USER_STATE', 'PATH_USER_CACHE',
    'PATH_WORKSPACE', 'PATH_TURNS', 'PATH_AUDIT', 'PATH_INDEX_IMAGE',
    'PATH_DURABLE_ARTIFACTS', 'DB_CHANGE_INTENTS')
PATH_KEYS = frozenset((*_CONFIG_PATHS, 'approvals', 'proposals_state', 'scheduler',
                       'workloads', 'synth_history', 'introvertiva', 'llm_cost',
                       'lre_artifact_store', 'lre_source_authority'))
CUSTODY_PROTOCOL = 'durable-job-metadata-v1'


def _resolve_composition(installation_root):
    # config.ensure_dirs is an import side effect for non-root callers.
    if os.geteuid() != 0:
        raise RuntimeError('administrative path probe requires root')
    root = Path(installation_root).resolve(strict=True)
    parents = ('durable_workloads',)
    # The administrative child receives an authenticated, installation-bound
    # sys.path before this probe is loaded.  Static imports keep the closed
    # build free of a reflective loader boundary; exact origins are checked
    # immediately afterwards, before any writer factory is inspected.
    modules = {
        'config': c,
        'approval_registry': approval_registry,
        'proposals_state': proposals_state,
        'proposals_cleanup': proposals_cleanup,
        'durable_workloads': durable_workloads,
        'durable_workloads.migrations': migrations,
        'durable_workloads.artifacts': artifacts,
        'durable_workloads.source_authority': source_authority,
        'durable_workloads.runtime_bindings': runtime_bindings,
        'durable_runtime_registry': registry,
        'llm_cost_sink': llm_cost_sink,
    }
    for name, module in modules.items():
        relative = name.replace('.', '/')
        expected = root / 'runtime' / (
            relative + ('/__init__.py' if name in parents else '.py'))
        if Path(module.__file__).resolve() != expected:
            raise RuntimeError('path module outside selected installation: ' + name)
    worker_factory, bridge_factory = registry.production_factories()
    factory = bridge_factory.__self__
    if (factory is not worker_factory.__self__
            or type(factory) is not runtime_bindings.RuntimeFactory):
        raise RuntimeError('unrecognized production LRE factory')
    custody = factory.selected_terminal_workspace_custody()
    if (custody != CUSTODY_PROTOCOL
            or custody != factory.DETACHED_WORKSPACE_CUSTODY):
        raise RuntimeError('unrecognized terminal workspace custody')
    artifact_root, source_path = factory.selected_storage_paths()
    paths = {
        'PATH_USER_CONFIG': c.PATH_USER_CONFIG,
        'PATH_USER_DATA': c.PATH_USER_DATA,
        'PATH_USER_STATE': c.PATH_USER_STATE,
        'PATH_USER_CACHE': c.PATH_USER_CACHE,
        'PATH_WORKSPACE': c.PATH_WORKSPACE,
        'PATH_TURNS': c.PATH_TURNS,
        'PATH_AUDIT': c.PATH_AUDIT,
        'PATH_INDEX_IMAGE': c.PATH_INDEX_IMAGE,
        'PATH_DURABLE_ARTIFACTS': c.PATH_DURABLE_ARTIFACTS,
        'DB_CHANGE_INTENTS': c.DB_CHANGE_INTENTS,
    }
    paths.update(
        approvals=approval_registry.default_db_path(),
        proposals_state=proposals_state.DB_PATH,
        scheduler=c.DB_SCHEDULER_V2,
        workloads=migrations.default_db_path(),
        synth_history=proposals_cleanup.SYNT_PROPOSALS_DIR,
        introvertiva=proposals_cleanup.INTROVERTIVA_DIR,
        llm_cost=llm_cost_sink._default_path(),
        lre_artifact_store=artifacts.ArtifactStore.root_path(artifact_root),
        lre_source_authority=source_authority.authority_file_path(source_path))
    return ({name: str(Path(value).resolve()) for name, value in sorted(paths.items())},
            custody)


def resolve_paths(installation_root):
    return _resolve_composition(installation_root)[0]


def resolve_runtime_composition(installation_root):
    paths, custody = _resolve_composition(installation_root)
    return {'paths': paths, 'terminal_workspace_custody': custody}
