#!/usr/bin/env python3
"""Build and verify the complete signed Tutor catalog from local sources.

The compiler always materializes a complete candidate database.  It may reuse
an unchanged embedding vector only when both the source text hash and the
embedding-model fingerprint match; every card and knowledge unit is still
written to, signed, and verified in the new candidate. Public documentation is
read from this repository's ``docs/`` directory; no website is downloaded.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from tutor.catalog import (  # noqa: E402
    compile_catalog,
    load_cards,
    load_knowledge_units,
    verify_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and verify the signed Metnos Tutor catalog.")
    parser.add_argument(
        "--force", action="store_true",
        help="materialize a complete candidate even when inputs are unchanged",
    )
    args = parser.parse_args()

    digest = compile_catalog(force=args.force)
    if not verify_catalog():
        raise SystemExit("Tutor catalog verification failed")
    print(
        "Tutor catalog ready: "
        f"source={digest} cards={len(load_cards())} "
        f"knowledge_units={len(load_knowledge_units())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
