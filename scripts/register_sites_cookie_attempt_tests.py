#!/usr/bin/env python3
"""Register focused cookie-attempt obligations in an explicit Native DB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.sites.cookie_attempt_budget"
CASE_PREFIX = "sites_cookie_attempt_budget_20261019"
TEST_FILE = "tests/runtime/sites/test_cookie_attempt_budget.py"
GROUPS = {
    "module": (
        TEST_FILE + "::test_explicit_attempt_budget_still_blocks_its_fifth_fresh_classification",
        TEST_FILE + "::test_direct_calls_start_new_attempts_without_resetting_accounting",
    ),
    "cluster": (TEST_FILE,),
}
EXPECTED_PASSES = {"module": 2, "cluster": 11}
FOCUSED_TEST_SHA256 = "cb41f9f2e74ff88b9feb69c60b85c6826a608a8c723c63ece9996818eb75c91f"
BASE = "8d1efe697c328a32e436fc7f45a0e13b22faeb50"
QUALIFIED_FILES = {
    "87a309c8408e86c655809d2361b5066501172f73": (
        "runtime/playwright_sidecar/cookie_privacy.py",
        "runtime/playwright_sidecar/session_broker.py",
        "tests/runtime/sites/test_login_cookie_precondition.py",
    ),
    "a9b75cad8c98061b5f8c0020f61724bce4b1aec0": (
        "scripts/register_extract_entries_parser_tests.py",
        "tests/runtime/entries/test_extract_entries_record_shapes.py",
    ),
    "06ded983a546dd5a260ef4b46e78cff1208af2be": (
        "runtime/builtin_executor_contracts/extract_entries/implementation.py.src",
        "runtime/builtin_executor_contracts/extract_entries/manifest.toml",
        "runtime/extract_entries.py",
        "scripts/register_extract_entries_completeness_tests.py",
        "tests/runtime/entries/test_extract_entries_source_completeness.py",
    ),
}


def _test_code(group: str, selectors: tuple[str, ...]) -> str:
    # The normal parent conftest copies installed DBs. Anonymous fixtures
    # instead use fresh roots and a repository seed, with network/DB guards.
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_COOKIE_TEST_ROOT']).resolve()
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                raise RuntimeError('Network forbidden in offline cookie tests')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private cookie test DBs allowed')
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
            qualified = {QUALIFIED_FILES!r}
            expected_paths = {{'scripts/register_sites_cookie_attempt_tests.py', {TEST_FILE!r}}}
            import hashlib
            assert hashlib.sha256((root / {TEST_FILE!r}).read_bytes()).hexdigest() == {FOCUSED_TEST_SHA256!r}
            for revision, paths in qualified.items():
                for name in paths:
                    expected_paths.add(name)
                    expected = subprocess.check_output(
                        ['git', 'show', revision + ':' + name], cwd=root)
                    assert (root / name).read_bytes() == expected, name
            changed = subprocess.check_output(
                ['git', 'diff', '--name-only', {BASE!r}, 'HEAD'], cwd=root,
                text=True).splitlines()
            assert set(changed) == expected_paths, changed
            assert not subprocess.check_output(
                ['git', 'status', '--porcelain'], cwd=root), 'Candidate is dirty'
            print('Composition: PASS (3 cookie + 2 parser + 5 completeness files unchanged; focused test digest verified)', flush=True)
        ''')
    return textwrap.dedent(f'''\
        import os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='sites-cookie-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_COOKIE_TEST_ROOT': str(sandbox),
               'METNOS_INSTALL_ROOT': str(root), 'METNOS_SITES_SIM': '0',
               'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0',
               'METNOS_SITES_FRONTIER_ROUTES': '0',
               'METNOS_EXECUTOR_ASSIGNED_WORKERS': '1',
               'METNOS_OWNER_USER_ID': 'pytest-cookie-owner'}}
        for name, part in {{'METNOS_USER_DATA': 'data',
                           'METNOS_USER_STATE': 'state',
                           'METNOS_USER_CONFIG': 'config',
                           'METNOS_WORKSPACE': 'workspace',
                           'METNOS_INDEX_ROOT': 'index',
                           'XDG_DATA_HOME': 'xdg-data',
                           'XDG_STATE_HOME': 'xdg-state',
                           'XDG_CONFIG_HOME': 'xdg-config',
                           'TMPDIR': 'tmp'}}.items():
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
                stderr=subprocess.STDOUT, text=True, timeout=100)
        output = log.read_text()
        print(output[-1800:].strip())
        assert result.returncode == 0, output[-8000:]
        counts = re.findall(r'\\b([0-9]+) passed\\b', output)
        assert counts == [{str(EXPECTED_PASSES[group])!r}], output
        assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
    ''') + composition


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", "runtime/playwright_sidecar/cookie_privacy.py",
                        "Classification budget belongs to one resolution attempt; click limits persist.")
    for group, selectors in GROUPS.items():
        registry.add_case(MODULE, f"{CASE_PREFIX}_{group}", group,
                          "integration", "python", _test_code(group, selectors))
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = register(args.db)
    try:
        names = {f"{CASE_PREFIX}_{group}" for group in GROUPS}
        cases = sorted((case for case in registry.cases_for_module(MODULE)
                        if case.name in names), key=lambda case: case.level != "module")
        assert len(cases) == len(GROUPS)
        print({case.level: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, CASE_PREFIX, verbose=True)
            for result in results:
                print(result.case.module_name, result.case.level, result.status,
                      result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
