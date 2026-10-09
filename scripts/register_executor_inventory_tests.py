"""Register and check executor inventory and documentation in a selected DB.

The declared cases are offline. No operational test database, live model,
credentials, or running service is used by this verification.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.testing.registry import Registry
from runtime.testing.runner import run_cluster, run_module
from scripts.register_monitor_tests import pytest_code

MODULES = {
    "http_routes_admin": ("runtime/http_routes_admin.py", [
        "tests/runtime/http/test_executor_inventory_consistency.py",
        "tests/runtime/executors/test_executor_catalog_metadata.py",
    ]),
    "ui_surfaces": ("runtime/ui_surfaces.py", [
        "tests/runtime/executors/test_ui_reference_docs.py",
        "tests/runtime/tutor/test_tutor_ui_access.py",
        "tests/runtime/tutor/test_tutor_f1.py::test_settings_navigation_and_tutor_share_the_canonical_ui_registry",
    ]),
    "generate_executor_catalog": ("scripts/generate_executor_catalog.py", [
        "tests/runtime/executors/test_executor_catalog_docs.py",
    ]),
    "generate_domain_reference": ("scripts/generate_domain_reference.py", [
        "tests/runtime/executors/test_domain_reference_docs.py",
    ]),
    "generate_ui_reference": ("scripts/generate_ui_reference.py", [
        "tests/runtime/executors/test_ui_reference_docs.py",
    ]),
    "published_docs": ("runtime/published_docs.py", [
        "tests/runtime/test_published_docs.py",
        "tests/runtime/http/test_quicktour_pdf_publication.py",
        "tests/internal/test_static_docs_deploy.py",
        "tests/runtime/tutor/test_photo_indexing_documentation.py",
        "tests/runtime/tutor/test_temporal_documentation.py",
    ]),
}
DEPENDENCIES = (
    ("http_routes_admin", "ui_surfaces"),
    ("generate_executor_catalog", "published_docs"),
    ("generate_domain_reference", "published_docs"),
    ("generate_ui_reference", "published_docs"),
    ("generate_ui_reference", "ui_surfaces"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    registry = Registry.open(args.db)
    try:
        for name, (source, files) in MODULES.items():
            registry.add_module(name, "runtime", source, "Executor inventory and public documentation")
            registry.add_case(name, "current_inventory_public_docs", "module", "integration", "python", pytest_code(files))
        for name, dependency in DEPENDENCIES:
            registry.add_dependency(name, dependency)
        failures = []
        for scope, runner in (("module", run_module), ("cluster", run_cluster)):
            for name in MODULES:
                results, counts = runner(name, registry)
                print(name, scope, counts, flush=True)
                for result in results:
                    if result.status != "pass":
                        failures.append((name, scope, result.case.name, result.output, result.failure))
        if failures:
            raise RuntimeError(failures)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
