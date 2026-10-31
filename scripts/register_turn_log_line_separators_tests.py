#!/usr/bin/env python3
"""Register the turn log kept whole across line separators (29/10/2026; real
turn Enel 520a346a on release 208: completed, absent from /admin/turns. Its
row decoded when split on "\\n" only; ``str.splitlines`` also breaks on
U+0085/U+2028/U+2029, which ``json.dumps(ensure_ascii=False)`` leaves raw).

One JSONL encoder escapes those three characters for every turn writer; one
reader splits on "\\n" only and counts undecodable rows. Requires an explicit
registry path. Fresh private roots, a repository i18n seed, network and
outside SQLite denied; invented text only. The cluster declares the
composition against the base commit.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
BASE = "d6eaf40eb"
MODULES = {
    "runtime.turn_log_line_separators": {
        "source": "runtime/audit_jsonl.py",
        "description": "A turn whose text holds a line separator stays one record for every turn-log writer and reader.",
        "prefix": "turn_log_line_separators_20261029",
        "module": ("tests/runtime/infra/test_turn_log_line_separators.py",),
        "cluster": (
            "tests/runtime/http/test_turns_routes.py",
            "tests/runtime/http/test_turn_outcome.py",
            # test_http_session_endpoints needs a loopback server and probes
            # the model service: outside the network guard, not registered.
            "tests/runtime/http/test_photo_endpoint.py",
            "tests/runtime/http/test_deliverable_attachment_delivery.py",
            "tests/runtime/runtime/test_observability.py",
            "tests/runtime/learning/test_proposals_eta_index.py",
            "tests/runtime/learning/test_telos_introspect_fastpath.py",
            "tests/runtime/learning/test_telos_introspect_dedup.py",
            "tests/runtime/learning/test_promoter_daemon.py",
            "tests/runtime/learning/test_promoter_digest.py",
            "tests/runtime/learning/test_promoter_v2_commentary.py",
            "tests/runtime/learning/test_promoter_v2_perf_savings.py",
            "tests/runtime/tutor/test_tutor_f2_repair.py",
            # The rest of test_tutor_f1 needs the signed catalog and the real
            # embedding model, red on the base too: only its telemetry case.
            "tests/runtime/tutor/test_tutor_f1.py::test_tutor_telemetry_records_the_request",
            "tests/runtime/infra/test_deferred_turns.py",
            "tests/runtime/infra/test_audit_jsonl_bounded.py",
            "tests/runtime/infra/test_audit_rotation_retention.py",
            "tests/runtime/infra/test_audit_snapshot_retention.py",
            "tests/runtime/infra/test_reliability.py",
            "tests/runtime/infra/test_turn_feedback.py",
            "tests/runtime/safety/test_scrub_credentials.py",
            "tests/portable/test_birth_retention_turns.py",
        ),
    },
}
COMPOSITION = (
    "runtime/agent_runtime.py",
    "runtime/audit_jsonl.py",
    "runtime/http_routes_admin.py",
    "runtime/http_routes_agent.py",
    "runtime/jobs/promoter_example.py",
    "runtime/observability.py",
    "runtime/proposals_eta_index.py",
    "runtime/telos_introspect.py",
    "runtime/tutor/telemetry.py",
    "scripts/build_prefilter_corpus.py",
    "scripts/register_turn_log_line_separators_tests.py",
    "tests/runtime/infra/test_turn_log_line_separators.py",
)


def _test_code(group: str, selectors: tuple[str, ...]) -> str:
    guarded_pytest = textwrap.dedent('''\
        import os, shutil, sys
        from pathlib import Path
        from urllib.parse import unquote, urlsplit
        sandbox = Path(os.environ['METNOS_TURN_LOG_TEST_ROOT']).resolve()
        def guard(event, args):
            if event in {'socket.connect', 'socket.sendto'}:
                raise RuntimeError('Network forbidden in offline turn-log tests')
            if event == 'sqlite3.connect':
                name = os.fsdecode(args[0])
                if name == ':memory:':
                    return
                if name.startswith('file:'):
                    name = unquote(urlsplit(name).path)
                if not Path(name).resolve().is_relative_to(sandbox):
                    raise RuntimeError('Only private turn-log test DBs allowed')
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
        sandbox = Path(tempfile.mkdtemp(prefix='turn-log-line-separators-{group}-'))
        print('Private test roots:', sandbox, flush=True)
        env = {{**os.environ, 'PYTHONPATH': 'runtime:.',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'METNOS_TURN_LOG_TEST_ROOT': str(sandbox),
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
            results, counts = run_cases(cases, registry, "turn_log_line_separators_20261029",
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
