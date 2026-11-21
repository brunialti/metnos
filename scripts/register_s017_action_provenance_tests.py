#!/usr/bin/env python3
"""Register B1 replay and its immediate regression group; run only at release."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "module": ("tests/runtime/engine/test_s017_action_provenance.py",),
    "cluster": ("tests/runtime/engine/test_clause_decontamination.py",
                "tests/runtime/engine/test_required_action_domain.py",
                "tests/runtime/engine/test_implicit_actions_adr0129.py",
                "tests/runtime/i18n/test_detection_lexicon_residual_nz.py"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry
    registry = Registry.open(args.db)
    module = "runtime.s017_action_provenance"
    try:
        registry.add_module(module, "runtime", "runtime/prefilter.py", __doc__)
        for level, paths in GROUPS.items():
            code = f'''import os, subprocess, sys, tempfile
from pathlib import Path
with tempfile.TemporaryFile(mode="w+") as log:
    result = subprocess.run([os.environ.get("METNOS_TEST_PYTHON", sys.executable), "-B",
        "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *{paths!r}],
        cwd=Path(_RT).parent, env={{**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}},
        stdout=log, stderr=subprocess.STDOUT, text=True, timeout=180)
    log.seek(0)
    output = log.read()
print(output[-4000:])
assert result.returncode == 0, output[-8000:]
'''
            registry.add_case(module, f"s017_action_provenance_{level}", level,
                              "integration", "python", code)
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
