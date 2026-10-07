"""Internal installed path observation; not maintenance admission or full census."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile

import executor_birth_admin_preflight as native
from executor_birth_retention import RetentionError
from install.birth_retention_installed_environment import _capture_installed_environment_v1
from install.birth_retention_path_probe import CUSTODY_PROTOCOL, PATH_KEYS


@dataclass(frozen=True, slots=True)
class _InstalledRuntimeCompositionV1:
    paths: tuple
    terminal_workspace_custody: str


@dataclass(frozen=True, slots=True)
class _InstalledPathsV1:
    environment: object
    observations: tuple
    non_python_services: tuple


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


def _capture_installed_paths_v1(*, require_stability):
    """Observe every gated service twice within caller exclusion.

    Unsupported writers refuse. Agreement covers observed native paths and the
    production runtime custody protocol. Explicit constructor overrides remain
    visible because the child reads the selected production factory itself.
    """
    if not callable(require_stability):
        raise TypeError('live stability checker required')
    if os.geteuid() != 0:
        raise RetentionError('retention_service_identity_invalid', 'administrative path probe')
    before = _capture_installed_environment_v1(require_stability=require_stability)
    def observe(environment):
        rows = []
        non_python = []
        for entry in environment.materials.catalog.entries:
            if entry.class_name == 'gated_service':
                require_stability()
                if entry.execution_kind == 'python_module':
                    rows.append((entry.entry_id, _probe_service_v1(environment.materials, entry)))
                else:
                    # Authentication and live unit checks already happened in
                    # the environment capture.  Native services are recorded
                    # separately: their writer pertinence is outside this
                    # Python-default-path probe.
                    non_python.append(entry.entry_id)
                require_stability()
        return tuple(rows), tuple(non_python)
    first = observe(before)
    after = _capture_installed_environment_v1(require_stability=require_stability)
    if before != after:
        raise RetentionError('retention_installation_changed', 'installed paths')
    second = observe(after)
    final = _capture_installed_environment_v1(require_stability=require_stability)
    if after != final or first != second:
        raise RetentionError('retention_installation_changed', 'installed paths')
    observations, non_python_services = first
    if not observations or any(paths != observations[0][1] for _, paths in observations):
        raise RetentionError('retention_inventory_incomplete', 'writer paths disagree')
    require_stability()
    return _InstalledPathsV1(final, observations, non_python_services)
