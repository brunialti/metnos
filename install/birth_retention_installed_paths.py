"""Internal installed path observation; not maintenance admission or full census."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile

import executor_birth_admin_preflight as native
from executor_birth_retention import RetentionError
from install.birth_retention_installed_environment import (
    _capture_installed_environment_once_v1, _capture_installed_environment_v1,
)
from install.birth_retention_path_probe import CUSTODY_PROTOCOL, PATH_KEYS


@dataclass(frozen=True, slots=True)
class _InstalledRuntimeCompositionV1:
    paths: tuple
    terminal_workspace_custody: str


@dataclass(frozen=True, slots=True)
class _InstalledPathsV1:
    environment: object
    observations: tuple
    # Unclassified native services remain a coverage blocker for the coordinator.
    non_python_services: tuple
    non_writing_services: tuple = ()


def _is_non_writing_native_service_v1(materials, entry):
    """Recognize only the authenticated, read-only service-side X display.

    Environment capture already checked executable custody and the loaded unit,
    including its complete manager projection. This classification grants no
    startup exemption: the display remains in the native stopped/empty-unit set.
    """
    from executor_birth_service_catalog import SERVICE_SOURCE_V1

    source = next((item for item in SERVICE_SOURCE_V1
                   if item.entry_id == 'service-side-display'), None)
    if source is None or type(entry) is not native._ServiceCatalogEntryV1:
        return False
    recipe = source.target_recipe
    arguments = (':99', '-screen', '0', '1920x1080x24', '-ac',
                 '-nolisten', 'tcp', '-dpi', '96')
    if ((source.class_name, source.unit_name, recipe.execution_kind,
         recipe.target_executable, recipe.python_module, recipe.target_args,
         recipe.target_working_directory, recipe.target_environment) !=
        ('gated_service', 'metnos-side-display.service', 'native_executable',
         '/usr/bin/Xvfb', None, arguments, '@installation_root@', ())):
        return False
    if ((entry.entry_id, entry.class_name, entry.unit_name, entry.scope,
         entry.execution_kind, entry.target_executable, entry.python_module,
         entry.target_args, entry.target_working_directory, entry.target_environment,
         entry.requires_preflight) !=
        (source.entry_id, source.class_name, source.unit_name, 'system',
         recipe.execution_kind, recipe.target_executable, None, arguments,
         materials.descriptor.installation_root, (), True) or entry.unit_spec is None):
        return False
    directives = {(item.section, item.name): (item.value_type, item.values)
                  for item in entry.unit_spec.directives}
    if (len(directives) != len(entry.unit_spec.directives)
            or ('Service', 'ReadWritePaths') in directives):
        return False
    required = {
        'ProtectSystem': ('scalar', ('strict',)),
        'PrivateTmp': ('boolean', ('yes',)),
        'NoNewPrivileges': ('boolean', ('yes',)),
        'StandardOutput': ('scalar', ('journal',)),
        'StandardError': ('scalar', ('journal',)),
    }
    source_directives = {(item.section, item.name): item.values
                         for item in source.unit_recipe}
    return (('Service', 'ReadWritePaths') not in source_directives
            and all(directives.get(('Service', name)) == value
                    and source_directives.get(('Service', name)) == value[1]
                    for name, value in required.items()))


def _probe_service_v1(materials, entry):
    root = materials.descriptor.installation_root
    if (entry.execution_kind != 'python_module'
            or not native._service_python_binding_v1(entry.target_executable, materials.descriptor)):
        raise RetentionError('retention_inventory_incomplete', 'unsupported path writer')
    environment = dict(native._static_launch_environment_v1(materials, entry))
    python_path = native._trusted_python_path_v1(
        root, entry.target_working_directory, entry.target_executable)
    bootstrap = (
        'import sys, runpy, json, resource; '
        'resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536)); '
        f'sys.path = {list(python_path)!r}; '
        f'p = runpy.run_path({str(Path(root) / "install/birth_retention_path_probe.py")!r}); '
        f'print(json.dumps(p["resolve_runtime_composition"]({root!r}), sort_keys=True))')
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                [entry.target_executable, '-I', '-B', '-c', bootstrap],
                cwd=entry.target_working_directory, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                close_fds=True, timeout=30, umask=0o077)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RetentionError('retention_inventory_incomplete', 'installed path probe') from exc
        output.seek(0)
        raw = output.read(65537)
    if result.returncode or len(raw) > 65536:
        raise RetentionError('retention_inventory_incomplete', 'installed path probe')
    try:
        response = json.loads(raw)
        if (not isinstance(response, dict)
                or set(response) != {'paths', 'terminal_workspace_custody'}
                or response['terminal_workspace_custody'] != CUSTODY_PROTOCOL):
            raise ValueError('runtime composition response')
        paths = response['paths']
        if (not isinstance(paths, dict) or set(paths) != PATH_KEYS
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or not v.startswith('/') or '\0' in v for k, v in paths.items())):
            raise ValueError('path response')
    except (ValueError, UnicodeError) as exc:
        raise RetentionError('retention_inventory_incomplete', 'installed path response') from exc
    return _InstalledRuntimeCompositionV1(
        tuple(sorted(paths.items())), response['terminal_workspace_custody'],
    )


def _capture_installed_paths_v1(*, require_stability, require_held_stability=None):
    """Observe paths within caller exclusion, with full observations at boundaries.

    An explicit held-capability checker lets a stopped administrative phase
    observe each writer once between two compared native captures. Without it,
    retain the existing repeated capture and probe contract. Agreement covers
    native paths and production runtime custody, including constructor overrides.
    """
    if not callable(require_stability):
        raise TypeError('live stability checker required')
    if require_held_stability is not None and not callable(require_held_stability):
        raise TypeError('held stability checker required')
    if os.geteuid() != 0:
        raise RetentionError('retention_service_identity_invalid', 'administrative path probe')
    checker = require_stability if require_held_stability is None else require_held_stability
    def observe(environment):
        rows = []
        non_python = []
        non_writing = []
        for entry in environment.materials.catalog.entries:
            if entry.class_name == 'gated_service':
                checker()
                if entry.execution_kind == 'python_module':
                    rows.append((entry.entry_id, _probe_service_v1(environment.materials, entry)))
                elif _is_non_writing_native_service_v1(environment.materials, entry):
                    non_writing.append(entry.entry_id)
                else:
                    # Authentication and live unit checks already happened in
                    # the environment capture.  Native services are recorded
                    # separately: their writer pertinence is outside this
                    # Python-default-path probe.
                    non_python.append(entry.entry_id)
                checker()
        return tuple(rows), tuple(non_python), tuple(non_writing)
    if require_held_stability is None:
        before = _capture_installed_environment_v1(require_stability=require_stability)
        first = observe(before)
        after = _capture_installed_environment_v1(require_stability=require_stability)
        if before != after:
            raise RetentionError('retention_installation_changed', 'installed paths')
        second = observe(after)
        final = _capture_installed_environment_v1(require_stability=require_stability)
        if after != final or first != second:
            raise RetentionError('retention_installation_changed', 'installed paths')
    else:
        # Deployment, authority and startup locks prevent compliant writers
        # changing code/configuration or launching while these capabilities are
        # held. Keep native authentication and loaded-unit checks at both ends;
        # arbitrary privileged mutations are not covered by advisory locks.
        require_stability()
        before = _capture_installed_environment_once_v1(require_stability=checker)
        first = observe(before)
        final = _capture_installed_environment_once_v1(require_stability=checker)
        if before != final:
            raise RetentionError('retention_installation_changed', 'installed paths')
    observations, non_python_services, non_writing_services = first
    if not observations or any(paths != observations[0][1] for _, paths in observations):
        raise RetentionError('retention_inventory_incomplete', 'writer paths disagree')
    require_stability()
    return _InstalledPathsV1(final, observations, non_python_services, non_writing_services)
