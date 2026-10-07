"""Read authenticated per-service inputs without importing runtime configuration.

This internal reader neither applies an environment nor bootstraps maintenance.
The coordinator supplies its live stability boundary. Per-service signed values
remain distinct: absent overrides require resolution by the installed code and
are not evidence of equal storage paths or complete F6 coverage.
"""
from dataclasses import dataclass

import executor_birth_admin_preflight as native
from executor_birth_account_identity import (
    metnos_xdg_layout_v1, resolve_posix_account_snapshot_v1,
)
from executor_birth_retention import RetentionError


@dataclass(frozen=True, slots=True)
class _InstalledEnvironmentV1:
    selected: object
    materials: object
    account: object
    layout: object
    target_environments: tuple
    launch_environments: tuple


def _capture_installed_environment_v1(*, require_stability):
    """Borrow a coordinator boundary; never open Birth or inherit caller env.

    The output is observational. Baseline identity environment remains owned by
    the native launcher; this reader exposes its authenticated descriptor and
    signed target inputs without reproducing launch or granting execution.
    """
    if not callable(require_stability):
        raise TypeError('live stability checker required')

    def capture():
        require_stability()
        authenticated = native._authenticate_fixed_ownership_snapshot_v1()
        selected, materials = native._load_installed_preflight_materials_v1(
            authenticated, review_sources=False)
        descriptor = materials.descriptor
        account = resolve_posix_account_snapshot_v1(descriptor.service_user)
        record = account.record
        if ((record.name, record.uid, record.gid, record.home, record.shell,
             account.supplementary_gids) !=
            (descriptor.service_user, descriptor.service_uid, descriptor.service_gid,
             descriptor.service_home, descriptor.service_shell,
             descriptor.service_supplementary_gids)):
            raise RetentionError('retention_service_identity_invalid')
        layout = metnos_xdg_layout_v1(record)
        namespace = layout.environment()
        namespace['SHELL'] = descriptor.service_shell
        namespace['METNOS_INSTALL_ROOT'] = descriptor.installation_root
        environments = []
        launch_environments = []
        for entry in materials.catalog.entries:
            if entry.class_name != 'gated_service':
                continue
            native._check_installed_service_v1(materials, entry)
            values = tuple((item.name, item.value) for item in entry.target_environment)
            launch_values = native._static_launch_environment_v1(materials, entry)
            if any(name in namespace and value != namespace[name]
                   for name, value in launch_values):
                raise RetentionError('retention_service_identity_invalid', 'signed XDG layout')
            environments.append((entry.entry_id, values))
            launch_environments.append((entry.entry_id, launch_values))
        if not environments:
            raise RetentionError('retention_inventory_incomplete', 'no installed services')
        require_stability()
        return _InstalledEnvironmentV1(selected, materials, account, layout,
                                       tuple(environments), tuple(launch_environments))

    before = capture()
    after = capture()
    if before != after:
        raise RetentionError('retention_installation_changed', 'installed environment')
    require_stability()
    return after
