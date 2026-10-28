"""Explicit executor support inventory shared by Birth and device packaging.

No discovery and no host runtime fallback. Monitor is a required dependency of
run_stdio even when event collection is disabled.
"""
from __future__ import annotations

COMMON_SUPPORT_FILES = (
    'executor_helpers.py',
    'config.py',
    'worker_policy.py',
    'executor_workers.py',
    'parallel_walk.py',
    'platform_policy.py',
    'tabular_projection.py',
    'spreadsheet_cells.py',
    'path_alias.py',
    'observed_dates.py',
    'tabular_records.py',
    'sites_observed.py',
    'tabular/__init__.py',
    'tabular/model.py',
    'tabular/compile.py',
    'tabular/api.py',
    'backends/__init__.py',
    'backends/files/__init__.py',
    'backends/files/local.py',
    'pdf_extract.py',
    'backends/files/tabular_local.py',
    'backends/files/tabular_xlsx.py',
    'monitor_capture.py',
    'monitor_events.py',
    'monitor_metrics.py',
)

FUNCTIONAL_SUPPORT_FILES = COMMON_SUPPORT_FILES + (
    'messages.py',
    'i18n.py',
    'logging_setup.py',
    'hashutil.py',
    'backend_resolver.py',
    'vocab.py',
    'skill_registry.py',
    'skills_paths.py',
    'detection_lexicon.py',
    'detection_lexicon_seed_resolvers.py',
)

DEVICE_SUPPORT_FILES = {name: name for name in COMMON_SUPPORT_FILES}
DEVICE_SUPPORT_FILES.update({'windows_desktop_apps.py': 'windows_desktop_apps.py', 'messages.py': 'device_shim/messages.py', 'messages_i18n.json': 'device_shim/messages_i18n.json'})
