#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register explicit filesystem grants and the runtime boundaries they cross."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "filesystem_permissions": (
        "module",
        ("tests/runtime/remote/test_filesystem_permissions.py",
         "tests/runtime/remote/test_device_filesystem.py",
         "tests/runtime/remote/test_filesystem_permissions_ui.py",
         "tests/runtime/remote/test_device_filesystem_relay.py"),
    ),
    "filesystem_permissions_integration": (
        "cluster",
        ("tests/runtime/remote/test_invocations.py",
         "tests/runtime/remote/test_late_result_a0.py",
         "tests/runtime/remote/test_agent_server_remote.py",
         "tests/runtime/remote/test_target_context_observation.py",
         "tests/runtime/remote/test_fs_listing_device_routing.py",
         "tests/runtime/engine/test_conversation_routing.py",
         "tests/runtime/engine/test_copy_not_invented.py",
         "tests/runtime/engine/test_undo_single_run.py",
         "tests/runtime/engine/test_named_folder.py",
         "tests/runtime/engine/test_table_pagination.py",
         "tests/runtime/engine/test_machine_name_question.py",
         "tests/runtime/executors/test_find_packages_inventory.py",
         "tests/runtime/executors/test_spreadsheet_on_the_server.py",
         "tests/runtime/engine/test_fast_path.py",
         "tests/runtime/infra/test_reliability.py",
         "tests/runtime/infra/test_reverse_patterns_declared.py",
         "tests/runtime/infra/test_reverse_patterns_authenticated_dispatch.py",
         "tests/runtime/executors/test_executor_standard.py",
         "tests/runtime/executors/test_executor_standard_filesystem_batch.py",
         "tests/runtime/executors/test_executor_standard_move.py",
         "tests/runtime/executors/test_executor_standard_delete.py",
         "tests/runtime/safety/test_capability_registry.py"),
    ),
}


def register(path: Path) -> dict[str, int]:
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    try:
        registry.add_module("filesystem_permissions", "runtime",
                            "runtime/filesystem_permissions.py",
                            "Account-scoped extensions, native file broker and permissions UI")
        registry.add_module("filesystem_permissions_integration", "runtime",
                            "runtime/device_filesystem.py",
                            "Signed invocation, device routing, conversation and reliability boundaries")
        registry.add_dependency("filesystem_permissions_integration", "filesystem_permissions")
        cases = {}
        for module, (level, files) in GROUPS.items():
            code = (
                "import os, re, subprocess, sys\n"
                "from pathlib import Path\n"
                "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
                "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
                f"*{files!r}], cwd=Path(_RT).parent, "
                "env={**os.environ, 'PYTHONPATH': 'runtime:.'}, "
                "capture_output=True, text=True, timeout=180)\n"
                "print(result.stdout.strip())\n"
                "assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]\n"
                "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', result.stdout), result.stdout\n"
            )
            name = f"filesystem_access_20261030_{level}"
            registry.add_case(module, name, level, "integration", "python", code)
            cases[level] = next(c.id for c in registry.cases_for_module(module) if c.name == name)
        return cases
    finally:
        registry.conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    args = parser.parse_args()
    print(register(args.db))
