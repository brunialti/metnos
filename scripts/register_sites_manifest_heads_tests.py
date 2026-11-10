#!/usr/bin/env python3
"""Register and run the §2.5 heads of act_sites, login_sites and read_sites within HEAD_MAX (10/11/2026)."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
MODULE = "executors.sites.manifest_heads"
GROUPS = {
    "module": (
        "tests/runtime/skills/test_manifest_description_schema.py",
        "tests/runtime/skills/test_manifest_language_state_release_check.py",
    ),
    "cluster": (
        "tests/runtime/skills/test_manifest_description_schema.py",
        "tests/runtime/skills/test_manifest_language_state_release_check.py",
        "tests/runtime/skills/test_manifest_lint_pattern_args.py",
        "tests/runtime/skills/test_manifest_routing_hygiene.py",
        "tests/runtime/engine/test_tool_schema_slim.py",
        "tests/runtime/executors/test_executor_catalog_docs.py",
        "tests/runtime/engine/test_site_goal_read.py",
        "tests/runtime/engine/test_aprire_un_sito_non_e_leggere.py",
        "tests/runtime/engine/test_sites_web_source_binding.py",
        "tests/runtime/sites/test_ambito_dichiarato.py",
        "tests/runtime/sites/test_document_acquisition.py",
        "tests/runtime/sites/test_login_stato_non_azione.py",
    ),
}


def register(path):
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry

    registry = Registry.open(path)
    registry.add_module(MODULE, "sites", "executors/act_sites/manifest.toml",
                        "Heads of act_sites, login_sites and read_sites within HEAD_MAX, IT and EN aligned.")
    names = set()
    for level, selectors in GROUPS.items():
        name = f"sites_manifest_heads_20261110_{level}"
        names.add(name)
        code = textwrap.dedent(f'''\
            import os, re, subprocess, sys, tempfile
            from pathlib import Path
            root = Path(_RT).parent
            fd, name = tempfile.mkstemp(prefix='sites-manifest-heads-{level}-', suffix='.log')
            os.close(fd)
            log = Path(name)
            with log.open('w') as output:
                result = subprocess.run([
                    os.environ.get('METNOS_TEST_PYTHON', sys.executable), '-B',
                    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                    *{selectors!r}], cwd=root,
                    env={{**os.environ, 'PYTHONPATH': 'runtime:.'}},
                    stdout=output, stderr=subprocess.STDOUT, text=True, timeout=300)
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
            results, counts = run_cases(cases, registry, "sites_manifest_heads_20261110", verbose=True)
            for result in results:
                print(result.case.name, result.status, result.output, result.failure)
            return 0 if counts == {"pass": len(cases)} else 1
        return 0
    finally:
        registry.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
