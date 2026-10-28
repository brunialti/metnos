#!/usr/bin/env python3
"""Register SITEMNGT step A (22/10/2026): the values of listed records are
copied from the rows the collector bound, as the browser shows them; the
model only indicates, once per list shape, which numbered column holds each
requested field (closed, checked choice). Rows are found without case (the
site's style may change it), reach the reader with their fields, and a new
sheet names a column as the site does when its rows agree.

Requires an explicit registry path. Fresh private roots, a repository i18n
seed, network and outside SQLite denied; invented pages only. The cluster
declares the composition against the LAB tools commit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "43df5bec5"
SITES = "tests/runtime/sites/"
ENTRIES = "tests/runtime/entries/"
BACKENDS = "tests/runtime/backends/"
MODULES = {
    "runtime.entries.list_copy": {
        "source": "runtime/list_columns.py",
        "description": "Listed values copied from their rows; the model only indicates the columns.",
        "prefix": "entries_list_copy_20261023",
        "module": (ENTRIES + "test_list_columns.py",
                   SITES + "test_collection_list_rows.py",
                   SITES + "test_collection_list_copy_browser.py",
                   BACKENDS + "test_site_column_headers.py"),
        "cluster": (ENTRIES + "test_extract_entries_foreign_section.py",
                    ENTRIES + "test_extract_entries_redundant_snapshots.py",
                    ENTRIES + "test_extract_entries_identity_evidence.py",
                    ENTRIES + "test_extract_entries_value_labels.py",
                    ENTRIES + "test_identity_contract_wire.py",
                    ENTRIES + "test_record_identity.py",
                    ENTRIES + "test_record_identity_completion_across.py",
                    SITES + "test_collection_view_identity_conflicts.py",
                    SITES + "test_collection_detail_coverage.py",
                    SITES + "test_collection_record_views_integrity.py",
                    SITES + "test_sites_record_view_caps.py",
                    SITES + "test_collection_native_snapshots.py",
                    SITES + "test_collection_producer_enrichment.py",
                    SITES + "test_collection_list_reuse_browser.py",
                    BACKENDS + "test_tabular_review_20261020.py"),
        "deselected": {"module": 0, "cluster": 4},
    },
}
OPT_IN = ("not test_installed_local_model_keeps_object_filter_and_continuation_separate"
          " and not test_prompt_positive_example_is_valid_closed_json")
PREEXISTING_FAILURES = (SITES + "test_collection_producer_enrichment.py::test_prompt_positive_example_is_valid_closed_json",
                        SITES + "test_sites_record_views.py", SITES + "test_sites_collection_sources.py",
                        # Red on 2c63612ae too (8/8): the collection observation
                        # ends in provider_error under this test's own stubs.
                        SITES + "test_collection_record_views_browser.py")
COMPOSITION = (
    "executors/read_sites/manifest.toml",
    "executors/read_sites/read_sites.py",
    "runtime/backends/files/local.py",
    "runtime/builtin_executor_contracts/extract_entries/implementation.py.src",
    "runtime/builtin_executor_contracts/extract_entries/manifest.toml",
    "runtime/extract_entries.py",
    "runtime/list_columns.py",
    "runtime/playwright_sidecar/session_broker.py",
    "runtime/prompts/en/.lang_state.json",
    "runtime/prompts/en/extract_entries_list_columns.j2",
    "runtime/prompts/it/.lang_state.json",
    "runtime/prompts/it/extract_entries_list_columns.j2",
    "runtime/sites_collection_sources.py",
    "runtime/tabular_records.py",
    "runtime/time_window_parser.py",
    "scripts/register_entries_list_copy_tests.py",
    BACKENDS + "test_site_column_headers.py",
    ENTRIES + "test_list_columns.py",
    SITES + "test_collection_list_copy_browser.py",
    SITES + "test_collection_list_rows.py",
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
        sandbox = Path(tempfile.mkdtemp(prefix='entries-list-copy-{group}-'))
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
            results, counts = run_cases(cases, registry, "entries_list_copy_20261023",
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
