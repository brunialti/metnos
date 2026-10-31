#!/usr/bin/env python3
"""Register the image grid answered by three standing-out tiles or reloaded
(29/10/2026 evening; Roberto: «analisi indipendente del codice e degli
algoritmi; alla fine prova comunque a fare dei giri di ottimizzazione per
aumentare la % di successo»). The independent review found a solved grid
blocked by the site's own navigation, unscored late replacements, a stale
provider message read as a refusal and grid clicks spent from the login's
action counter.

Requires an explicit registry path. Fresh private roots, a repository i18n
seed, network and outside SQLite denied; invented pages and coloured tiles
only, the classifier replaced by a colour reader. The cluster declares the
composition against the base commit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "13bcc8a52"
SITES = "tests/runtime/sites/"
MODULES = {
    "runtime.sites.captcha_grid_reload": {
        "source": "runtime/playwright_sidecar/captcha_grid.py",
        "description": "A reCAPTCHA 3x3 grid is answered when exactly three tiles stand out, otherwise reloaded up to the draw limit; then the person answers.",
        "prefix": "sites_captcha_reload_20261029",
        "module": (SITES + "test_captcha_grid.py",
                   SITES + "test_login_image_grid_browser.py"),
        # The cluster of the 29/10 grid register, in two runs: one run of all
        # of it exceeds the runner's 240 s for a cluster case.
        "cluster": (SITES + "test_login_repeated_captcha_browser.py",
                    SITES + "test_collection_navigation_checkbox_browser.py",
                    SITES + "test_navigation_captcha_errors.py",
                    SITES + "test_un_sito_fermo_non_e_un_dovere_di_chi_chiede.py")
                   + tuple(SITES + name for name in ('test_captcha_authority_telemetry.py', 'test_captcha_checkbox.py', 'test_captcha_checkbox_frame_scope.py', 'test_captcha_checkbox_frame_scope_browser.py', 'test_captcha_checkbox_owner.py', 'test_captcha_checkbox_owner_browser.py', 'test_captcha_checkbox_readiness.py', 'test_captcha_checkbox_readiness_browser.py', 'test_captcha_crop_guard.py', 'test_captcha_crop_guard_browser.py', 'test_captcha_resolution_audit.py', 'test_credential_origin_remember.py', 'test_login_actionability_browser.py', 'test_login_captcha.py', 'test_login_captcha_dispatch.py', 'test_login_choice_audit.py')),
        "cluster_b": tuple(SITES + name for name in ('test_login_choice_audit_browser.py', 'test_login_document_wait_budget.py', 'test_login_entry_shared_verb.py', 'test_login_landing_reopen.py', 'test_login_loading_document_browser.py', 'test_login_navigation.py', 'test_login_non_chiude_un_successo.py', 'test_login_otp_browser.py', 'test_login_pending_submission_browser.py', 'test_login_post_submit_audit.py', 'test_login_post_submit_stability.py', 'test_login_preparation_failure_browser.py', 'test_login_robot_disabled_fields_browser.py', 'test_login_robot_gate_browser.py', 'test_login_robot_message_context_browser.py', 'test_login_robot_user_control_observation_browser.py', 'test_login_sso_redirect_browser.py', 'test_login_submission_diagnostics.py', 'test_login_submit_observation_browser.py', 'test_manual_login_observation_budget.py', 'test_sites_cooldown.py', 'test_browser_user_handoff.py', 'test_sites_saved_sessions.py')),
        "deselected": {"module": 0, "cluster": 7, "cluster_b": 0},
    },
}
# The local CAPTCHA library is installed only in the service environment.
OPT_IN = ("not test_installed_local_model_keeps_object_filter_and_continuation_separate"
          " and not test_prompt_positive_example_is_valid_closed_json"
          " and not test_real_library_click_stays_in_provider_frame_and_never_accepts_consent"
          " and not test_real_login_attempt_keeps_captcha_otp_and_credentials_separate")
# Red on the base too, same assertions, left out as in the 29/10 register of
# the appearances: test_captcha_checkbox_browser.py and the browser files
# test_browser_user_handoff_browser, test_login_navigation_browser,
# test_login_robot_user_control_browser and test_sites_record_dispatch_browser.
PREEXISTING_FAILURES = ()
COMPOSITION = (
    "decisions/0191-intelligent-mandate-sites-robot.md",
    "docs/en/architecture/intelligent_executors.html",
    "docs/it/architecture/intelligent_executors.html",
    "install/INSTALL_NOTES.md",
    "install/download_models.sh",
    "install/manifest.toml",
    "runtime/playwright_sidecar/captcha_grid.py",
    "runtime/playwright_sidecar/session_broker.py",
    "runtime/prompts/en/sites_challenge_target.j2",
    "runtime/prompts/en/sites_challenge_target_reverse.j2",
    "runtime/prompts/it/sites_challenge_target.j2",
    "runtime/prompts/it/sites_challenge_target_reverse.j2",
    "scripts/register_sites_captcha_reload_tests.py",
    SITES + "test_captcha_grid.py",
    SITES + "test_captcha_resolution_audit.py",
    SITES + "test_login_image_grid_browser.py",
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
                path = root / name
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'deleted'
                print(digest, name, flush=True)
        ''')
    return textwrap.dedent(f'''\
        import os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='sites-captcha-reload-{group}-'))
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
                stderr=subprocess.STDOUT, text=True, timeout=900)
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
        groups = {"module": spec["module"], "cluster": spec["module"] + spec["cluster"],
                  "cluster_b": spec["cluster_b"]}
        for group, selectors in groups.items():
            registry.add_case(module, f"{spec['prefix']}_{group}", group.split("_")[0], "integration",
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
            names = {f"{spec['prefix']}_{group}" for group in ("module", "cluster", "cluster_b")}
            found = sorted((case for case in registry.cases_for_module(module)
                            if case.name in names), key=lambda case: (case.level != "module", case.name))
            assert len(found) == 3, module
            cases.extend(found)
        print({case.name: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, "sites_captcha_reload_20261029",
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
