#!/usr/bin/env python3
"""Register CAPTCHA handoff module and cluster tests in an explicit private DB.

Uses routed browser fixtures and fake classifiers only. Python network access
and SQLite outside private test roots are denied. Records source fingerprints
and rejects concurrent source changes; no historical checkout composition pin.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
SITES = "tests/runtime/sites/"
PREFIX = "sites_captcha_handoff_20261106"
MODULE = "runtime.sites.captcha_handoff"
MODULE_CASES = (
    SITES + "test_captcha_handoff_cleanup.py",
    SITES + "test_login_image_grid_browser.py::test_late_grid_cleanup_hands_the_real_browser_to_the_person",
)
CLUSTER_CASES = (MODULE_CASES[0], SITES + "test_login_image_grid_browser.py") + tuple(
    SITES + name for name in (
        "test_captcha_grid.py", "test_captcha_checkbox.py", "test_captcha_checkbox_frame_scope.py",
        "test_captcha_checkbox_owner.py", "test_captcha_checkbox_readiness.py",
        "test_captcha_resolution_audit.py", "test_captcha_authority_telemetry.py",
        "test_captcha_crop_guard.py", "test_login_captcha_dispatch.py", "test_lock_scope_held.py",
        "test_navigation_captcha_errors.py", "test_browser_user_handoff.py",
        "test_login_repeated_captcha_browser.py", "test_collection_navigation_checkbox_browser.py",
    ))
SOURCES = ("runtime/playwright_sidecar/session_broker.py", "runtime/spin_supervisor.py",
           "runtime/playwright_sidecar/captcha_grid.py", "runtime/playwright_sidecar/captcha_checkbox.py",
           "runtime/playwright_sidecar/captcha_delegate.py",
           "scripts/register_sites_captcha_handoff_tests.py")


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
    return textwrap.dedent(f'''\
        import hashlib, json, os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='sites-captcha-handoff-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        sources = {SOURCES!r} + tuple(dict.fromkeys(name.split('::', 1)[0] for name in {selectors!r}))
        fingerprints = {{name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}}
        (sandbox / 'sources.json').write_text(json.dumps(fingerprints, indent=2) + '\\n')
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
                '--basetemp', str(sandbox / 'pytest'), *{selectors!r}],
                cwd=root, env=env, stdout=output_file,
                stderr=subprocess.STDOUT, text=True, timeout=900)
        assert fingerprints == {{name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources}}, 'sources changed during tests'
        output = log.read_text()
        print(output[-1800:].strip())
        assert result.returncode == 0, output[-8000:]
        assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
        assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed)\\b', output), output
        assert re.findall(r'\\b([0-9]+) deselected\\b', output) == ({[str(deselected)] if deselected else []!r}), output
    ''')


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", SOURCES[0],
                        "Human CAPTCHA control only after positive final cleanup and unchanged session ownership.")
    for group, selectors in (("module", MODULE_CASES), ("cluster", CLUSTER_CASES)):
        registry.add_case(MODULE, f"{PREFIX}_{group}", group, "integration",
                          "python", _test_code(group, selectors, 0))
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        names = {f"{PREFIX}_{group}" for group in ("module", "cluster")}
        cases = sorted((case for case in registry.cases_for_module(MODULE) if case.name in names),
                       key=lambda case: case.level != "module")
        assert len(cases) == 2
        print({f"{case.module_name}:{case.level}": case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, PREFIX,
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
