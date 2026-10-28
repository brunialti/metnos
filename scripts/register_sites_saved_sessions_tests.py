#!/usr/bin/env python3
"""Register saved site sessions and the pause after an abandoned challenge.

Requires an explicit registry path. Fresh private roots, a repository i18n
seed, network and outside SQLite denied; synthetic sites, invented cookies and
keys only. The cluster declares the composition against e9477f00 (context).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "e9477f00c"
SITES = "tests/runtime/sites/"
ENTRIES = "tests/runtime/entries/"
MODULES = {
    "runtime.sites.saved_sessions": {
        "source": "runtime/sites_saved_sessions.py",
        "description": "A verified login is saved encrypted and reused; an abandoned challenge pauses the next login.",
        "prefix": "sites_saved_sessions_20261020",
        "module": tuple(SITES + name for name in ('test_sites_saved_sessions.py', 'test_sites_saved_sessions_browser.py', 'test_authenticated_landing_anchor.py')),
        "cluster": tuple(SITES + name for name in ('test_captcha_authority_telemetry.py', 'test_captcha_checkbox.py', 'test_captcha_checkbox_browser.py', 'test_captcha_checkbox_frame_scope.py', 'test_captcha_checkbox_frame_scope_browser.py', 'test_captcha_checkbox_owner.py', 'test_captcha_checkbox_owner_browser.py', 'test_captcha_checkbox_readiness.py', 'test_captcha_checkbox_readiness_browser.py', 'test_captcha_crop_guard.py', 'test_captcha_crop_guard_browser.py', 'test_captcha_resolution_audit.py', 'test_credential_origin_remember.py', 'test_login_actionability_browser.py', 'test_login_captcha.py', 'test_login_captcha_dispatch.py', 'test_login_choice_audit.py', 'test_login_choice_audit_browser.py', 'test_login_document_wait_budget.py', 'test_login_entry_shared_verb.py', 'test_login_loading_document_browser.py', 'test_login_navigation.py', 'test_login_non_chiude_un_successo.py', 'test_login_otp_browser.py', 'test_login_pending_submission_browser.py', 'test_login_post_submit_audit.py', 'test_login_post_submit_stability.py', 'test_login_preparation_failure_browser.py', 'test_login_robot_disabled_fields_browser.py', 'test_login_robot_gate_browser.py', 'test_login_robot_message_context_browser.py', 'test_login_robot_user_control_observation_browser.py', 'test_login_sso_redirect_browser.py', 'test_login_submission_diagnostics.py', 'test_login_submit_observation_browser.py', 'test_manual_login_observation_budget.py', 'test_sites_cooldown.py')),
        "deselected": {"module": 0, "cluster": 7},
    },
}
# The real CAPTCHA library is not installed in the test interpreter.
OPT_IN = ("not test_installed_local_model_keeps_object_filter_and_continuation_separate"
          " and not test_real_library_click_stays_in_provider_frame_and_never_accepts_consent"
          " and not test_real_login_attempt_keeps_captcha_otp_and_credentials_separate")
# test_login_urls.py opens local sockets, refused by this sandbox: run apart.
PREEXISTING_FAILURES = tuple(SITES + name for name in ('test_browser_user_handoff_browser.py', 'test_click_dispatch_terminal.py', 'test_credential_ui_forms.py', 'test_login_cookie_precondition.py', 'test_login_navigation_browser.py', 'test_login_robot_user_control_browser.py', 'test_login_stato_non_azione.py'))
COMPOSITION = (
    "install/data/i18n_seed.sqlite",
    "runtime/credentials.py",
    "runtime/playwright_sidecar/session_broker.py",
    "runtime/sites_cooldown.py",
    "runtime/sites_saved_sessions.py",
    "scripts/register_sites_saved_sessions_tests.py",
    "scripts/sync_collection_i18n.py",
    SITES + "test_sites_saved_sessions.py",
    SITES + "test_sites_saved_sessions_browser.py",
)


def _test_code(group: str, selectors: tuple[str, ...], deselected: int) -> str:
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_SITES_TEST_ROOT']).resolve()
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                raise RuntimeError('Network forbidden in offline Sites tests')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private Sites test DBs allowed')
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
                print(hashlib.sha256((root / name).read_bytes()).hexdigest(), name, flush=True)
        ''')
    return textwrap.dedent(f'''\
        import os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='sites-saved-sessions-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.:tests/runtime/sites',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_SITES_TEST_ROOT': str(sandbox),
               'METNOS_INSTALL_ROOT': str(root), 'METNOS_SITES_SIM': '1',
               'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0',
               'METNOS_SITES_FRONTIER_ROUTES': '0',
               'METNOS_EXECUTOR_ASSIGNED_WORKERS': '1',
               'METNOS_OWNER_USER_ID': 'pytest-sites-owner',
               'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
                   or str(Path.home() / '.cache/ms-playwright')}}
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
                '--basetemp', str(sandbox / 'pytest'), '-k', {OPT_IN!r}, *{selectors!r}],
                cwd=root, env=env, stdout=output_file,
                stderr=subprocess.STDOUT, text=True, timeout=220)
        output = log.read_text()
        print(output[-1800:].strip())
        assert result.returncode == 0, output[-8000:]
        assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
        assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed)\\b', output), output
        assert re.findall(r'\\b([0-9]+) deselected\\b', output) == ({[str(deselected)] if deselected else []!r}), output
    ''') + composition


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    for module, spec in MODULES.items():
        registry.add_module(module, "sites", spec["source"], spec["description"])
        groups = {"module": spec["module"], "cluster": spec["module"] + spec["cluster"]}
        for group, selectors in groups.items():
            registry.add_case(module, f"{spec['prefix']}_{group}", group, "integration",
                              "python", _test_code(group, selectors, spec["deselected"][group]))
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
            results, counts = run_cases(cases, registry, "sites_saved_sessions_20261020",
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
