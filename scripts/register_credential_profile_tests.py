#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register the profile defaults and their user/credential boundaries."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "module": ("tests/runtime/sites/test_invocation_names.py",
               "tests/runtime/sites/test_site_invocations.py",
               "tests/runtime/sites/test_credential_ui_forms.py"),
    "cluster": ("tests/runtime/http/test_users.py",
                "tests/runtime/http/test_user_prefs.py",
                "tests/runtime/safety/test_credential_forms_injection.py",
                "tests/runtime/entries/test_get_inputs_credentials_flow.py"),
}


def register(path: Path) -> dict[str, int]:
    sys.path.insert(0, str(ROOT / "runtime/testing"))
    from registry import Registry

    registry = Registry.open(path)
    try:
        cases = {}
        for group, files in GROUPS.items():
            code = (
                "import os, re, subprocess, sys\n"
                "from pathlib import Path\n"
                "result = subprocess.run([os.environ.get('METNOS_TEST_PYTHON', sys.executable), "
                "'-B', '-m', 'pytest', '-q', '-x', '--tb=short', '-p', 'no:cacheprovider', "
                f"*{files!r}], cwd=Path(_RT).parent, "
                "env={**os.environ, 'PYTHONPATH':'runtime:.'}, capture_output=True, text=True)\n"
                "print(result.stdout.strip())\n"
                "assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-2000:]\n"
                "assert not re.search(r'\\b[1-9][0-9]* skipped\\b', result.stdout), result.stdout\n"
            )
            name = f"credential_profile_defaults_20261014_{group}"
            registry.add_case(
                "credential_injection", name,
                group, "integration", "python", code)
            cases[group] = next(case.id for case in registry.cases_for_module(
                "credential_injection") if case.name == name)
        return cases
    finally:
        registry.conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/testing/tests.db")
    args = parser.parse_args()
    print(register(args.db))
