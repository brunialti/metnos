#!/usr/bin/env python3
"""Run the identical synthetic checkbox matrix with the installed browser engine.

No pytest/bootstrap, HOME changes, downloads, copied binaries or user profiles.
The authenticated caller must provide private mutable METNOS/XDG roots and
network isolation; this program only intercepts synthetic context traffic.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT)]


class Patches:
    def __init__(self, stack):
        self.stack = stack

    def setattr(self, target, name, value=None):
        if isinstance(target, str):
            self.stack.enter_context(patch(target, name))
        else:
            self.stack.enter_context(patch.object(target, name, value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-engine", choices=("chromium", "camoufox"), default="camoufox")
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    from playwright_sidecar import browser_engine
    browser_engine.load_configuration()
    engine = browser_engine.selected()
    if engine != args.expected_engine:
        raise RuntimeError("installed_browser_engine_mismatch")
    from tests.runtime.sites.captcha_checkbox_fixture import run_provider_checkbox_matrix
    args.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="checkbox-synthetic-", dir=args.work_root) as work, ExitStack() as stack:
        proof = run_provider_checkbox_matrix(Patches(stack), Path(work), engine=engine)
    print(json.dumps(proof, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
