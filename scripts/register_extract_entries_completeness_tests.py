#!/usr/bin/env python3
"""Register offline source-completeness regressions in an explicit Native DB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.entries.completeness"
CASE_PREFIX = "extract_entries_completeness_20261019"
MODULE_TESTS = (
    "tests/runtime/entries/test_extract_entries_source_completeness.py",
)
GROUPS = {
    "module": MODULE_TESTS,
    "cluster": MODULE_TESTS + (
        "tests/runtime/entries/test_extract_completeness.py",
        "tests/runtime/sites/test_collection_producer_enrichment.py",
    ),
}


def _test_code(group: str, selectors: tuple[str, ...]) -> str:
    # The normal parent conftest copies installed DBs. These pure fixtures
    # bootstrap from repository resources in fresh roots instead.
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_EXTRACT_TEST_ROOT']).resolve()
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                raise RuntimeError('Network forbidden in offline extraction tests')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private extraction test DBs allowed')
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
        import os, re, subprocess, sys, tempfile
        from pathlib import Path
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='extract-completeness-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_EXTRACT_TEST_ROOT': str(sandbox),
               'METNOS_INSTALL_ROOT': str(root), 'METNOS_SITES_SIM': '1',
               'METNOS_COLLECTION_LOCAL_MODEL_TEST': '0',
               'METNOS_SITES_FRONTIER_ROUTES': '0',
               'METNOS_EXECUTOR_ASSIGNED_WORKERS': '1',
               'METNOS_OWNER_USER_ID': 'pytest-extract-owner'}}
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
                stderr=subprocess.STDOUT, text=True, timeout=180)
        output = log.read_text()
        print(output[-2200:].strip())
        assert result.returncode == 0, output[-8000:]
        assert not re.search(r'\\b[1-9][0-9]* skipped\\b', output), output
    ''')


def register(path: Path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "entries", "runtime/extract_entries.py",
                        "Completeness belongs to each observed source, not unrelated extraction calls.")
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
