#!/usr/bin/env python3
"""Register literal-criterion module/cluster tests in an explicit private DB.

Offline cases deny network and shared SQLite access. An optional local-model
case requires an explicit configuration file; all prompts and rows are synthetic.
It never starts services, changes the installed model or writes production data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.entries.classify_criteria"
MODULE_TESTS = (
    "tests/runtime/entries/test_classify_entries_evidence.py",
    "tests/runtime/entries/test_classify_entries_honesty.py",
)
CLUSTER_TESTS = MODULE_TESTS + (
    "tests/runtime/backends/test_spreadsheet_existing_workbook.py",
    "tests/runtime/engine/test_existing_sheet_planning.py",
)
MODEL_TESTS = ("tests/runtime/entries/test_classify_entries_criteria_real_llm.py",)


def _test_code(group, selectors, model_config=None):
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_CLASSIFY_TEST_ROOT']).resolve()
        model_test = os.environ.get('METNOS_CLASSIFY_CRITERIA_MODEL_TEST') == '1'
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                address = args[1] if event == 'socket.connect' else args[-1]
                if not (model_test and isinstance(address, tuple)
                        and address[0] in {'127.0.0.1', '::1'}):
                    raise RuntimeError('Only the explicitly enabled local model is allowed')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private test databases are allowed')
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
        import os, re, shutil, subprocess, sys, tempfile, tomllib
        from pathlib import Path
        from urllib.parse import urlsplit
        root = Path(_RT).parent
        sandbox = Path(tempfile.mkdtemp(prefix='classify-criteria-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_CLASSIFY_TEST_ROOT': str(sandbox),
               'METNOS_INSTALL_ROOT': str(root),
               'METNOS_CLASSIFY_CRITERIA_MODEL_TEST': {'1' if model_config else '0'!r},
               'METNOS_CLASSIFY_CRITERIA_EVIDENCE': str(sandbox / 'model-evidence.jsonl'),
               'METNOS_EXECUTOR_ASSIGNED_WORKERS': '1',
               'METNOS_OWNER_USER_ID': 'pytest-classify-owner'}}
        for name, part in {{'HOME': 'home', 'METNOS_USER_DATA': 'data',
                           'METNOS_USER_STATE': 'state', 'METNOS_USER_CONFIG': 'config',
                           'METNOS_WORKSPACE': 'workspace', 'METNOS_INDEX_ROOT': 'index',
                           'XDG_DATA_HOME': 'xdg-data', 'XDG_STATE_HOME': 'xdg-state',
                           'XDG_CONFIG_HOME': 'xdg-config', 'TMPDIR': 'tmp'}}.items():
            (sandbox / part).mkdir()
            env[name] = str(sandbox / part)
        env['METNOS_LOG_FILE'] = str(sandbox / 'state/test.log')
        env['METNOS_LLM_TIERS_CONFIG'] = str(sandbox / 'config/llm_tiers.toml')
        source_config = {str(model_config) if model_config else None!r}
        if source_config:
            config = tomllib.loads(Path(source_config).read_text())
            for name in ('fast', 'middle', 'wise'):
                tier = config[name]
                assert tier['provider'] == 'llamacpp', 'Local model only'
                url = tier.get('endpoint') or tier.get('base_url')
                assert urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'), url
            shutil.copyfile(source_config, env['METNOS_LLM_TIERS_CONFIG'])
        log = sandbox / 'pytest.log'
        print('Detailed test log:', log, flush=True)
        with log.open('w') as output:
            result = subprocess.run([
                os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B', '-u', '-c',
                {guarded_pytest!r}, '--noconftest', '-o', 'addopts=', '-q',
                '--tb=short', '-p', 'no:cacheprovider', '--basetemp',
                str(sandbox / 'pytest'), *{selectors!r}], cwd=root, env=env,
                stdout=output, stderr=subprocess.STDOUT, text=True, timeout=225)
        output = log.read_text()
        print(output[-2400:].strip())
        assert result.returncode == 0, output[-8000:]
        assert re.search(r'\\b[1-9][0-9]* passed\\b', output), output
        assert not re.search(r'\\b[1-9][0-9]* (?:skipped|xfailed|xpassed|deselected)\\b', output), output
    ''')


def register(path, model_config=None):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "entries", "runtime/classify_entries.py",
                        "Literal criteria with a quote from each row and honest uncertainty.")
    groups = [("module", "module", MODULE_TESTS, None),
              ("cluster", "cluster", CLUSTER_TESTS, None)]
    if model_config:
        groups.append(("local_model", "cluster", MODEL_TESTS, model_config))
    names = set()
    for group, level, selectors, config in groups:
        name = f"classify_criteria_20261106_{group}"
        names.add(name)
        registry.add_case(MODULE, name, level, "integration", "python",
                          _test_code(group, selectors, config))
    return registry, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--local-model-config", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry, names = register(args.db, args.local_model_config)
    try:
        cases = sorted((case for case in registry.cases_for_module(MODULE) if case.name in names),
                       key=lambda case: (case.level != "module", case.name))
        print({case.name: case.id for case in cases}, flush=True)
        if args.verify:
            from runner import run_cases

            os.environ.setdefault("METNOS_TEST_PYTHON", sys.executable)
            results, counts = run_cases(cases, registry, "classify_criteria_20261106", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
