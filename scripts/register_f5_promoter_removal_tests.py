#!/usr/bin/env python3
"""Register the removal of F5 and of the nightly promoter (9/11/2026).

Roberto cancelled F5 (preexercise, feedback-driven quarantine, certificate)
and approved removing the promoter that ran empty since 13/5. This script:

1. retires the registry modules whose source was removed, with their cases;
2. drops removed test files from the remaining cases that listed them;
3. registers one module and one cluster case for the change, and runs them.

The module case covers the files the change touched; the cluster case adds
the neighbouring Birth, retention, durable-workload and channel suites.
The census case is excluded: it is red on the base commit too (RM-0005 F6).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.testing.registry import DEFAULT_DB, Registry  # noqa: E402
from runtime.testing.runner import run_module  # noqa: E402

BASE = "a99a0d068"
MODULE = "runtime.f5_promoter_removal"
RENAMED = {
    "tests/internal/test_f5_authority_launcher.py": "tests/internal/test_synth_review_launcher.py",
}
# Whole cases that exercised only removed behaviour.
RETIRED_CASES = {
    ("agent_runtime", "fast_path_execution_receipt"),
    ("agent_runtime", "fast_path_execution_receipt_f5_cluster"),
}
# Registry modules without a source of their own, retired with the feature.
RETIRED_MODULES = {
    "jobs.promoter", "jobs.promoter_state", "executor_birth_feedback",
    "install.certification", "install.f5_authority", "install_f5_authority",
    "rm0008_f5_native_evidence", "e2e.certification.f5_native_probe",
    "tests.e2e.certification.f5_native_scenarios",
}
MODULE_TESTS = (
    "tests/portable/test_synth_review.py",
    "tests/internal/test_synth_review_launcher.py",
    "tests/runtime/executors/test_executor_lifecycle_state.py",
    "tests/runtime/executors/test_executor_birth_shadow.py",
    "tests/runtime/executors/test_executor_birth_operational.py",
    "tests/runtime/executors/test_executor_birth_predecessor.py",
    "tests/runtime/executors/test_executor_birth_bootstrap.py",
    "tests/runtime/executors/test_executor_scheduler.py",
    "tests/runtime/durable_workloads/test_execution_bridge.py",
    "tests/runtime/scheduler_v2/test_nightly_orchestrator.py",
    "tests/runtime/channels/test_telegram_authorization_buttons.py",
    "tests/runtime/infra/test_installer_operator_authority.py",
    "tests/portable/test_contract_boundary_policy_projection.py",
)
CLUSTER_TESTS = MODULE_TESTS + (
    "tests/runtime/executors/test_executor_birth_approval.py",
    "tests/runtime/executors/test_executor_birth_approval_store.py",
    "tests/runtime/executors/test_executor_birth_terminal_evidence.py",
    "tests/runtime/executors/test_executor_birth_historical_publication.py",
    "tests/runtime/contracts/test_contract_store.py",
    "tests/runtime/durable_workloads/test_admission_compiler.py",
    "tests/runtime/learning/test_change_intent_adapters.py",
    "tests/runtime/http/test_admin_catalog_guard.py",
    "tests/runtime/i18n/test_seed_i18n_gate_keys.py",
    "tests/runtime/remote/test_device_shim_i18n.py",
    "tests/runtime/infra/test_turn_log_line_separators.py",
    "tests/portable/rm0008_2b",
    "tests/portable/test_executor_birth_current_continuity.py",
    "tests/portable/test_executor_birth_retention_authority.py",
    "tests/portable/test_birth_retention_feedback.py",
    "tests/portable/test_birth_retention_turns.py",
    "tests/portable/test_birth_retention_turn_archives.py",
    "tests/portable/test_birth_retention_journals.py",
    "tests/portable/test_birth_retention_reviews.py",
    "tests/portable/test_birth_retention_review_links.py",
    "tests/portable/test_birth_retention_admission_links.py",
    "tests/portable/test_birth_retention_exclusion.py",
)


def _removed_paths() -> list[str]:
    output = subprocess.check_output(
        ["git", "diff", "--name-status", BASE, "HEAD"], cwd=ROOT, text=True)
    removed = []
    for line in output.splitlines():
        status, *paths = line.split("\t")
        if status == "D" or status.startswith("R"):
            removed.append(paths[0])
    return removed


def _strip(code: str, path: str) -> str:
    replacement = RENAMED.get(path)
    if replacement is not None:
        return code.replace(path, replacement)
    for quote in ("'", '"'):
        token = f"{quote}{path}{quote}"
        code = code.replace(token + ", ", "").replace(", " + token, "").replace(token, "")
    return re.sub(r"[ \t]+" + re.escape(path) + r"(?=[\s'\"]|$)", "", code)


def _retire(registry: Registry, removed: list[str]) -> None:
    conn = registry.conn
    sources = set(removed)
    dead = [row[0] for row in conn.execute("SELECT id, name, source_path FROM modules")
            if row[2] in sources or row[1] in RETIRED_MODULES]
    for module_id in dead:
        conn.execute("DELETE FROM test_runs WHERE case_id IN "
                     "(SELECT id FROM test_cases WHERE module_id=?)", (module_id,))
        conn.execute("DELETE FROM test_cases WHERE module_id=?", (module_id,))
        conn.execute("DELETE FROM module_dependencies WHERE module_id=? OR depends_on_id=?",
                     (module_id, module_id))
        conn.execute("DELETE FROM modules WHERE id=?", (module_id,))
    for case_id, module, name, code in conn.execute(
            "SELECT t.id, m.name, t.name, t.test_code FROM test_cases t "
            "JOIN modules m ON m.id=t.module_id").fetchall():
        if (module, name) in RETIRED_CASES:
            conn.execute("DELETE FROM test_runs WHERE case_id=?", (case_id,))
            conn.execute("DELETE FROM test_cases WHERE id=?", (case_id,))
            continue
        updated = code or ""
        for path in removed:
            if path in updated:
                updated = _strip(updated, path)
        if updated != (code or ""):
            conn.execute("UPDATE test_cases SET test_code=? WHERE id=?", (updated, case_id))
    conn.commit()


def _pytest_case(paths: tuple[str, ...]) -> str:
    """Portable tests run in their own session, as in CI (importlib mode)."""
    portable = [path for path in paths if path.startswith("tests/portable/")]
    other = [path for path in paths if not path.startswith("tests/portable/")]
    return (
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')\n"
        "python = os.environ.get('METNOS_TEST_PYTHON') or sys.executable\n"
        "base = [python, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider']\n"
        "runs = [base + " + repr(other) + ", base + ['--import-mode=importlib', "
        "'--confcutdir=tests/portable', *" + repr(portable) + "]]\n"
        "for command in runs:\n"
        "    result = subprocess.run(command, cwd=Path(_RT).parent, env=env, "
        "capture_output=True, text=True, timeout=1800)\n"
        "    print(result.stdout[-4000:])\n    print(result.stderr[-2000:])\n"
        "    assert result.returncode == 0\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()
    registry = Registry.open(args.db)
    try:
        _retire(registry, _removed_paths())
        registry.add_module(MODULE, "runtime", "runtime/executor_lifecycle_state.py",
                            "F5 and nightly promoter removed; Synth review keeps its own entry")
        registry.add_case(MODULE, "f5_promoter_removal_module", "module", "integration",
                          "python", _pytest_case(MODULE_TESTS))
        registry.add_case(MODULE, "f5_promoter_removal_cluster", "cluster", "integration",
                          "python", _pytest_case(CLUSTER_TESTS))
        if args.no_run:
            return
        # The registry runner starts snippets with the system interpreter; the
        # suites need the one running this script (the project environment).
        os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
        # The module has no registry neighbours, so its cluster is the module
        # itself: one run executes both the module and the cluster case.
        results, counts = run_module(MODULE, registry)
        print(counts)
        for result in results:
            if result.status != "pass":
                print(result.output, result.failure)
        assert len(results) == 2 and all(result.status == "pass" for result in results)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
