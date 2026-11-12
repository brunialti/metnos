#!/usr/bin/env python3
"""Register and run the tests of the second pass of the RM-0008/0009/0010 review: accepted rejections reach the planner, the safety store in the sandbox, private state, the admin pages (10/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.rm_review_followup_20261110"
GROUPS = {
    "module": (
        "tests/runtime/engine/test_accepted_rejection_reaches_planner.py",
        "tests/runtime/engine/test_one_clause_one_action.py",
        "tests/runtime/safety/test_safety_store_in_sandbox.py",
        "tests/runtime/infra/test_sandbox_secret_masks.py",
        "tests/runtime/learning/test_change_intents_ui.py",
        "tests/runtime/http/test_executor_revive.py",
    ),
    "cluster": (
        "tests/runtime/learning/test_change_applier.py",
        "tests/runtime/learning/test_change_intent_adapters.py",
        "tests/runtime/learning/test_introvertive_loop_stress.py",
        "tests/runtime/infra/test_turn_feedback.py",
        "tests/runtime/executors/test_executor_lifecycle_state.py",
        "tests/runtime/safety/test_set_signatures_contract.py",
        "tests/runtime/safety/test_safety_admin_sudoer.py",
        "tests/runtime/http/test_change_row_escaping.py",
        "tests/runtime/learning/test_change_observer.py",
        "tests/runtime/engine/test_autopath_prune_aging.py",
        "tests/runtime/engine/test_fastpath_lifecycle.py",
        "tests/runtime/remote/test_server_named_folders.py",
        "tests/runtime/skills/test_admission_layer7.py",
        "tests/runtime/skills/test_manifest_routing_hygiene.py",
        "tests/runtime/infra/test_config_args_marking_policy.py",
        "tests/runtime/executors/test_executor_catalog_docs.py",
        "tests/runtime/learning/test_synth_stage4_description.py",
        "tests/runtime/learning/test_synth_stage6_verify.py",
        "tests/runtime/learning/test_synth_delivery_chain.py",
        "tests/runtime/engine/test_output_policy_normalize.py",
        "tests/runtime/learning/test_synth_attempts.py",
        "tests/runtime/learning/test_synth_candidate_admission.py",
        "tests/runtime/i18n/test_seed_i18n_gate_keys.py",
        "tests/runtime/infra/test_installer_operator_authority.py",
        "tests/runtime/infra/test_sandbox_relative_paths.py",
        "tests/runtime/learning/test_synt_undo_claim.py",
        "tests/runtime/learning/test_single_decision_cycle.py",
        "tests/runtime/learning/test_generated_tools_push.py",
        "tests/runtime/learning/test_automatic_work_courtesy.py",
        "tests/runtime/http/test_http_routes_stack.py",
        "tests/runtime/infra/test_store_registry_isolation.py",
        "tests/runtime/infra/test_configuration_isolation_a.py",
        "tests/runtime/infra/test_configuration_isolation_b.py",
        "tests/runtime/executors/test_ui_reference_docs.py",
        "tests/runtime/infra/test_reload_isolation_a.py",
        "tests/runtime/infra/test_reload_isolation_b.py",
        "tests/runtime/executors/test_birth_tests_private_tmp.py",
        "tests/runtime/contracts/test_code_file_paths.py",
        "tests/runtime/executors/test_parallel_recursive_executors.py",
        "tests/runtime/executors/test_applicable_input_boundary.py",
        "tests/runtime/executors/test_array_args_boundary.py",
        "tests/runtime/durable_workloads/test_direct_partial_result.py",
        "tests/runtime/durable_workloads/test_execution_bridge.py",
        "tests/runtime/durable_workloads/test_domain_outcome.py",
        "tests/runtime/durable_workloads/test_direct_invocation.py",
        "tests/runtime/durable_workloads/test_fencing_and_recovery.py",
        "tests/runtime/infra/test_lre_answers.py",
        "tests/runtime/durable_workloads/test_job_notices.py",
        "tests/runtime/durable_workloads/test_outbox.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "learning", "runtime/engine/dispatch.py",
                        "Review of 10/11/2026, second pass: an accepted rejection is avoided by the engine, the "
                        "safety store is the real one in a sandbox, state is private, admin pages show errors.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"rm_review_followup_20261110_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='rm-review-followup-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=180)
            output = log.read_text()
            print('Detailed test log:', log)
            print(output[-2400:].strip())
            assert result.returncode == 0, output[-8000:]
            assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
            assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
        ''')
        registry.add_case(MODULE, name, level, "integration", "python", code)
    return registry, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry, names = register(args.db)
    try:
        cases = sorted((case for case in registry.cases_for_module(MODULE) if case.name in names),
                       key=lambda case: (case.level != "module", case.name))
        print({case.name: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, "rm_review_followup_20261110", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
