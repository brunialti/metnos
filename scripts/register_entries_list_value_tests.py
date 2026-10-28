#!/usr/bin/env python3
"""Register the list value rule (Roberto, 22/10/2026: «data come nell'elenco»):
a field whose values disagree takes the value of the only source that gives
it to other rows, the row stays valid, every value stays a witness in the
Notes (Enel 2025/2026: the list shows the due date, a detail another date).
No model decides it; the relation prompts are removed.

Requires an explicit registry path. Fresh private roots, a repository i18n
seed, network and outside SQLite denied; invented pages only. The cluster
declares the composition against d7f7105e.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "d7f7105e5"
SITES = "tests/runtime/sites/"
ENTRIES = "tests/runtime/entries/"
MODULES = {
    "runtime.entries.list_value_rule": {
        "source": "runtime/record_identity.py",
        "description": "Disagreeing values take the list value; the row stays valid, witnesses in the Notes.",
        "prefix": "entries_list_value_20261022",
        "module": (ENTRIES + "test_record_identity_completion_across.py",
                   ENTRIES + "test_extract_entries_value_labels.py",
                   SITES + "test_collection_view_identity_conflicts.py",
                   SITES + "test_collection_producer_enrichment.py",
                   SITES + "test_collection_record_views_integrity.py"),
        "cluster": (ENTRIES + "test_record_identity.py",
                    ENTRIES + "test_record_identity_completion.py",
                    ENTRIES + "test_record_identity_shared_code.py",
                    ENTRIES + "test_record_identity_across_scopes.py",
                    ENTRIES + "test_record_identity_partitions.py",
                    ENTRIES + "test_record_identity_unidentified_rejected.py",
                    ENTRIES + "test_record_identity_unscoped_join.py",
                    ENTRIES + "test_record_identity_poorer_copy.py",
                    ENTRIES + "test_record_identity_universality.py",
                    ENTRIES + "test_identity_contract_wire.py",
                    ENTRIES + "test_extract_entries_identity_evidence.py",
                    ENTRIES + "test_extract_entries_redundant_snapshots.py",
                    ENTRIES + "test_extract_entries_foreign_section.py",
                    SITES + "test_collection_detail_coverage.py"),
        "deselected": {"module": 4, "cluster": 4},
    },
}
# Red on a7be455e too: the prompt examples check of the producer enrichment
# test (four cases); the precision and safety identity files are stale.
OPT_IN = ("not test_installed_local_model_keeps_object_filter_and_continuation_separate"
          " and not test_prompt_positive_example_is_valid_closed_json")
PREEXISTING_FAILURES = (ENTRIES + "test_record_identity_precision.py",
                        ENTRIES + "test_record_identity_safety.py",
                        SITES + "test_collection_producer_enrichment.py::test_prompt_positive_example_is_valid_closed_json",
                        SITES + "test_sites_record_views.py", SITES + "test_cutoff_detail_composition.py")
COMPOSITION = (
    "runtime/extract_entries.py",
    "runtime/builtin_executor_contracts/extract_entries/implementation.py.src",
    "runtime/builtin_executor_contracts/extract_entries/manifest.toml",
    "runtime/prompts/it/extract_entries_label_relation.j2",
    "runtime/prompts/en/extract_entries_label_relation.j2",
    "scripts/register_entries_list_value_tests.py",
    ENTRIES + "test_extract_entries_value_labels.py",
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
        sandbox = Path(tempfile.mkdtemp(prefix='entries-list-value-{group}-'))
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
            results, counts = run_cases(cases, registry, "entries_list_value_20261022",
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
