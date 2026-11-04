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
         "tests/runtime/remote/test_server_filesystem_protocol.py",
         "tests/runtime/remote/test_server_filesystem_host.py",
         "tests/runtime/remote/test_document_parse_worker.py",
         "tests/runtime/infra/test_installer_managed_filesystem_host.py",
         "tests/runtime/remote/test_device_filesystem.py",
         "tests/runtime/remote/test_device_filesystem_host.py",
         "tests/runtime/remote/test_filesystem_permissions_ui.py",
         "tests/runtime/http/test_inline_form_provenance.py",
         "tests/runtime/http/test_http_server.py",
         "tests/runtime/remote/test_device_filesystem_relay.py"),
    ),
    "filesystem_permissions_integration": (
        "cluster",
        ("tests/runtime/safety/test_sandbox_filesystem_host_boundary.py",
         "tests/runtime/engine/test_repo_slug_punctuation.py",
         "tests/runtime/i18n/test_args_extractor_i18n.py",
         "tests/runtime/engine/test_args_extractor_lang.py",
         "tests/runtime/remote/test_invocations.py",
         "tests/runtime/remote/test_late_result_a0.py",
         "tests/runtime/remote/test_agent_server_remote.py",
         "tests/runtime/remote/test_target_context_observation.py",
         "tests/runtime/remote/test_fs_listing_device_routing.py",
         "tests/runtime/engine/test_conversation_routing.py",
         "tests/runtime/engine/test_copy_not_invented.py",
         "tests/runtime/engine/test_undo_single_run.py",
         "tests/runtime/engine/test_named_folder.py",
         "tests/runtime/engine/test_table_pagination.py",
         "tests/runtime/engine/test_read_content_presentation.py",
         "tests/runtime/engine/test_output_policy_normalize.py",
         "tests/runtime/engine/test_finalizer_unico.py",
         "tests/runtime/engine/test_orchestration.py",
         "tests/runtime/http/test_dialog_async_completion.py",
         "tests/runtime/http/test_recent_attachment_history.py",
         "tests/runtime/entries/test_dialog_choice_form_http.py",
         "tests/runtime/infra/test_dialog_consent_once.py",
         "tests/runtime/infra/test_dialog_pending_conversation_scope.py",
         "tests/simulator/web/test_chat_dialog_lifecycle_browser.py",
         "tests/runtime/engine/test_machine_name_question.py",
         "tests/runtime/executors/test_find_packages_inventory.py",
         "tests/runtime/executors/test_spreadsheet_on_the_server.py",
         "tests/runtime/executors/test_executor_birth_property_runner.py",
         "tests/runtime/executors/test_complex_document_report_pipeline.py",
         "tests/runtime/entries/test_extract_entries_parser.py",
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
                "print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[-1000:])\n"
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
