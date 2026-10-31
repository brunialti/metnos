#!/usr/bin/env python3
"""Register the JSONL stores kept whole across line separators (29/10/2026;
follow-up of release 209, which fixed the turn log only).

Deferred requests, next-visit notices, owner locations and nine internal
audit/telemetry readers split their rows with ``str.splitlines``, which also
breaks on U+0085/U+2028/U+2029 left raw by ``json.dumps(ensure_ascii=False)``:
a deferred request or notice holding one was lost silently. Lenient readers
use ``audit_jsonl.read_jsonl_records``; the fail-closed receipt reader splits
bytes; writers of user text use ``audit_jsonl.jsonl_line``. Requires an
explicit registry path. Fresh private roots, a repository i18n seed, network
and outside SQLite denied; invented text only. The cluster declares the
composition against the base commit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "7cf8b97b5"
MODULES = {
    "runtime.jsonl_store_line_separators": {
        "source": "runtime/audit_jsonl.py",
        "description": "A record whose text holds a line separator stays whole in every JSONL store read with splitlines.",
        "prefix": "jsonl_store_line_separators_20261029",
        "module": ("tests/runtime/infra/test_jsonl_store_line_separators.py",),
        "cluster": (
            "tests/runtime/infra/test_deferred_turns.py",
            "tests/runtime/channels/test_a2_telegram_push.py",
            "tests/runtime/engine/test_recovery_wrong_type_dir.py",
            "tests/runtime/infra/test_turn_log_line_separators.py",
            # test_invocations and test_late_result_a0 sign with the author key,
            # absent from the private roots: red on the base too, not registered.
            "tests/runtime/executors/test_host_location_chain.py",
            "tests/runtime/executors/test_executor_standard_system_domain.py",
            "tests/runtime/learning/test_introvertiva.py",
            "tests/runtime/infra/test_audit_snapshot_retention.py",
            "tests/runtime/infra/test_lifecycle_summary.py",
            "tests/runtime/learning/test_proposals_cleanup.py",
            "tests/runtime/skills/test_skill_admission_smoke_case.py",
            "tests/runtime/engine/test_guard_dormancy.py",
            "tests/runtime/learning/test_synth_orphan_cleanup.py",
            "tests/runtime/skills/test_skills_cli_publication.py",
            "tests/runtime/skills/test_skill_enablement.py",
            # Two cases of test_task_mandates drive a browser context the private
            # roots lack, red on the base too: only its mandate and topology cases.
            "tests/runtime/infra/test_task_mandates.py::test_sites_mandate_uses_only_verified_exact_hosts",
            "tests/runtime/infra/test_task_mandates.py::test_explicit_hosts_never_treats_url_paths_as_domains",
            "tests/runtime/infra/test_task_mandates.py::test_credential_scope_is_rechecked_and_revocable",
            "tests/runtime/infra/test_task_mandates.py::test_credential_mandate_is_the_interactive_default",
            "tests/runtime/infra/test_task_mandates.py::test_unscoped_exact_topology_does_not_shadow_scoped_binding",
            "tests/runtime/infra/test_task_mandates.py::test_www_only_topology_uses_scoped_base_binding",
            "tests/runtime/infra/test_task_mandates.py::test_www_alias_does_not_authorize_arbitrary_subdomain",
            "tests/runtime/infra/test_task_mandates.py::test_query_can_narrow_credential_mandate_without_a_command_grammar",
            "tests/runtime/infra/test_task_mandates.py::test_credential_and_login_mandates_fail_closed_without_lexicon",
            "tests/runtime/infra/test_task_mandates.py::test_new_site_credentials_choose_mandate_before_storage",
            "tests/runtime/infra/test_task_mandates.py::test_set_credentials_manifest_keeps_mandate_choice_form_only",
            "tests/runtime/infra/test_task_mandates.py::test_recurring_task_persists_integrity_checked_mandate",
            "tests/runtime/infra/test_task_mandates.py::test_recurring_task_init_retires_ownerless_legacy_db",
            "tests/runtime/infra/test_task_mandates.py::test_task_read_scope_allows_goal_but_never_post",
            "tests/runtime/infra/test_task_mandates.py::test_www_login_uses_verified_root_credential_scope",
            "tests/runtime/infra/test_task_mandates.py::test_scheduled_scope_carries_task_identity",
            "tests/runtime/sites/test_credential_origin_remember.py",
            "tests/runtime/sites/test_sites_saved_sessions.py",
            "tests/runtime/runtime/test_observability.py",
            "tests/portable/test_birth_retention_import_audit.py",
            "tests/portable/test_birth_retention_sites.py",
            "tests/portable/test_birth_retention_introvertiva.py",
        ),
    },
}
COMPOSITION = (
    "runtime/admin/proposals_cli.py",
    "runtime/cli/skills_cli.py",
    "runtime/credential_mandates.py",
    "runtime/deferred_turns.py",
    "runtime/introvertiva.py",
    "runtime/lifecycle_summary.py",
    "runtime/location_store.py",
    "runtime/observability.py",
    "runtime/prefilter_stats.py",
    "runtime/proposals_cleanup.py",
    "runtime/skill_audit.py",
    "runtime/synt.py",
    "runtime/synth_orphan_cleanup.py",
    "runtime/user_notices.py",
    "scripts/register_jsonl_store_line_separators_tests.py",
    "tests/runtime/infra/test_jsonl_store_line_separators.py",
)


def _test_code(group: str, selectors: tuple[str, ...]) -> str:
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_JSONL_STORE_TEST_ROOT']).resolve()
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                raise RuntimeError('Network forbidden in offline JSONL store tests')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private JSONL store test DBs allowed')
        sys.addaudithook(guard)
        seed = sandbox / 'i18n-seed.sqlite'
        shutil.copyfile(Path(os.environ['METNOS_INSTALL_ROOT']) /
                        'install/data/i18n_seed.sqlite', seed)
        import i18n
        i18n._SEED_DB_PATH = seed
        import pytest
        raise SystemExit(pytest.main(sys.argv[1:]))
    ''')
    composition = ""
    if group == "cluster":
        composition = textwrap.dedent(f'''\
            import hashlib
            changed = subprocess.check_output(
                ['git', 'diff', '--name-only', {BASE!r}, 'HEAD'], cwd=root, text=True).splitlines()
            assert set(changed) == set({COMPOSITION!r}), changed
            assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root), 'dirty'
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
            print('Composition:', head, 'over', {BASE[:12]!r}, flush=True)
            for name in sorted(changed):
                path = root / name
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'deleted'
                print(digest, name, flush=True)
        ''')
    return textwrap.dedent(f'''\
        import os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='jsonl-store-line-separators-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_JSONL_STORE_TEST_ROOT': str(sandbox),
               'METNOS_INSTALL_ROOT': str(root)}}
        for name, part in {{'HOME': 'home', 'METNOS_USER_DATA': 'data',
                           'METNOS_USER_STATE': 'state', 'METNOS_USER_CONFIG': 'config',
                           'METNOS_WORKSPACE': 'workspace', 'METNOS_INDEX_ROOT': 'index',
                           'XDG_DATA_HOME': 'xdg-data', 'XDG_STATE_HOME': 'xdg-state',
                           'XDG_CONFIG_HOME': 'xdg-config', 'TMPDIR': 'tmp'}}.items():
            (sandbox / part).mkdir()
            env[name] = str(sandbox / part)
        env['METNOS_LOG_FILE'] = str(sandbox / 'state/test.log')
        log = sandbox / 'pytest.log'
        print('Detailed test log:', log, flush=True)
        with log.open('w') as output_file:
            result = subprocess.run([
                os.environ.get('METNOS_TEST_PYTHON', sys.executable),
                '-B', '-u', '-c', {guarded_pytest!r}, '--noconftest',
                '-o', 'addopts=', '-q', '--tb=short', '-p', 'no:cacheprovider',
                '--basetemp', str(sandbox / 'pytest'), *{selectors!r}],
                cwd=root, env=env, stdout=output_file,
                stderr=subprocess.STDOUT, text=True, timeout=900)
        output = log.read_text()
        print(output[-1800:].strip())
        assert result.returncode == 0, output[-8000:]
        assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
        assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
    ''') + composition


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    for module, spec in MODULES.items():
        registry.add_module(module, "runtime", spec["source"], spec["description"])
        groups = {"module": spec["module"], "cluster": spec["module"] + spec["cluster"]}
        for group, selectors in groups.items():
            registry.add_case(module, f"{spec['prefix']}_{group}", group, "integration",
                              "python", _test_code(group, selectors))
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        cases = []
        for module, spec in MODULES.items():
            names = {f"{spec['prefix']}_{group}" for group in ("module", "cluster")}
            found = sorted((case for case in registry.cases_for_module(module)
                            if case.name in names), key=lambda case: case.level != "module")
            assert len(found) == 2, module
            cases.extend(found)
        print({f"{case.module_name}:{case.level}": case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, "jsonl_store_line_separators_20261029",
                                        verbose=True)
            for result in results:
                print(result.case.module_name, result.case.level, result.status,
                      result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
