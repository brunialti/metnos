#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register browser history races and the surrounding chat lifecycle cluster."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.chat.history"
GROUPS = {
    "module": ("tests/simulator/web/test_chat_history_live_race_browser.py",
               "tests/simulator/web/test_chat_history_order_browser.py"),
    "cluster": ("tests/simulator/web/test_chat_history_recovery_browser.py",
                "tests/simulator/web/test_chat_history_order_boundary_browser.py",
                "tests/simulator/web/test_chat_history_order_compatibility_browser.py",
                "tests/simulator/web/test_chat_history_legacy_identity_browser.py",
                "tests/simulator/web/test_chat_dialog_lifecycle_browser.py",
                "tests/simulator/web/test_recent_attachment_history_browser.py",
                "tests/runtime/test_published_docs.py"),
}


def register(path: Path) -> dict[str, int]:
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    try:
        if not registry._module_id(MODULE):
            registry.add_module(MODULE, "runtime", "runtime/templates/chat.html",
                                "Recent history preserves chronological order and live interactions.")
        registry.add_dependency(MODULE, "runtime.chat_dialog")
        cases = {}
        for group, files in GROUPS.items():
            name = f"delayed_history_20261016_{group}"
            code = (
                "import os, re, subprocess, sys\n"
                "from pathlib import Path\n"
                "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
                "'-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', "
                f"*{files!r}], cwd=Path(_RT).parent, "
                "env={**os.environ, 'PYTHONPATH': 'runtime:.'}, capture_output=True, text=True)\n"
                "print(result.stdout[-800:].strip())\n"
                "assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]\n"
                "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', result.stdout), result.stdout\n"
            )
            registry.add_case(MODULE, name, group, "integration", "python", code)
            cases[group] = next(case.id for case in registry.cases_for_module(MODULE)
                                if case.name == name)
        return cases
    finally:
        registry.conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    args = parser.parse_args()
    print(register(args.db))
