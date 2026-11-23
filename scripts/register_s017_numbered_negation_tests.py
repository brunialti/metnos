#!/usr/bin/env python3
"""Register S-017 B4.1 and its bounded regression group, without running them."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = "runtime.s017_numbered_negation_20261123"
GROUPS = {
    "module": ("tests/runtime/engine/test_s017_numbered_negation.py",),
    "cluster": (
        "tests/runtime/engine/test_intent_reply_grammar.py",
        "tests/runtime/engine/test_intent_meta_object.py",
        "tests/runtime/engine/test_intent_fonte_aperta.py",
        "tests/runtime/engine/test_clause_decontamination.py",
        "tests/runtime/engine/test_implicit_actions_adr0129.py",
        "tests/runtime/engine/test_compound_transform_action.py",
        "tests/runtime/test_planning_measure.py",
        "tests/runtime/infra/test_prompts_lint.py",
        "tests/runtime/i18n/test_prompts_frontmatter.py",
        "tests/runtime/i18n/test_prompts_symmetry.py",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT / "runtime/testing"), str(ROOT / "runtime")]
    from registry import Registry
    registry = Registry.open(args.db)
    try:
        registry.add_module(MODULE, "runtime", "runtime/intent_extractor.py", __doc__)
        for level, paths in GROUPS.items():
            code = f'''import os, subprocess, sys, tempfile
from pathlib import Path
with tempfile.TemporaryFile(mode="w+") as log:
    result = subprocess.run([os.environ.get("METNOS_TEST_PYTHON", sys.executable), "-B",
        "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
        "-p", "pytest_asyncio.plugin", *{paths!r}],
        cwd=Path(_RT).parent,
        env={{**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}},
        stdout=log, stderr=subprocess.STDOUT, text=True, timeout=90)
    log.seek(0)
    output = log.read()
print(output[-4000:])
assert result.returncode == 0, output[-8000:]
'''
            registry.add_case(MODULE, f"s017_numbered_negation_{level}", level,
                              "integration", "python", code)
        names = registry.conn.execute(
            "SELECT c.id, c.name FROM test_cases c JOIN modules m ON m.id=c.module_id "
            "WHERE m.name=? ORDER BY c.id", (MODULE,)).fetchall()
        print(", ".join(f"{row['id']}:{row['name']}" for row in names))
    finally:
        registry.conn.close()


if __name__ == "__main__":
    main()
