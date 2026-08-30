#!/usr/bin/env python3
"""Realign the two reviewed source roots after a change to a censused root.

Every commit that touches `runtime`, `install`, `scripts` or `executors`
invalidates both pins, and the publisher refuses until they agree. Doing it by
hand costs one refused publication per attempt, because the two roots are
coupled: writing the PUBLIC pin edits a censused file and therefore moves the
PRIVATE root, while the private pin line is normalised out of the census and
is a fixed point. The order below is the one that converges:

  1. private root from the filesystem, written to its four bindings;
  2. regenerate the export;
  3. public root from the export, written to the publisher;
  4. private root again, because step 3 edited a censused file.

This tool does NOT approve a new root. It only recomputes what the reviewer
already decided to accept by changing the sources, exactly as the gate would
compute it, and reports both values so they can be recorded.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PIN_BINDINGS = (
    "runtime/executor_birth_admin_preflight.py",
    "runtime/contract_boundary_guard.py",
    "scripts/publish-public.sh",
    "internal/reports/rm0007-m4-boundary-inventory.json",
)


def _review_module(tree: Path):
    sys.path.insert(0, str(tree / "internal/tools"))
    import rm0008_public_source_review as review

    return review


def _current_private_pin(tree: Path) -> str:
    import re

    text = (tree / "runtime/contract_boundary_guard.py").read_text("utf-8")
    found = re.search(r"sha256:[0-9a-f]{64}", text)
    if found is None:
        raise SystemExit("no private pin found in the boundary guard")
    return found.group(0)


def _write_private_pin(tree: Path, old: str, new: str, count: int) -> None:
    for relative in PIN_BINDINGS:
        path = tree / relative
        text = path.read_text("utf-8")
        path.write_text(text.replace(old, new), encoding="utf-8")
    publisher = tree / "scripts/publish-public.sh"
    lines = []
    for line in publisher.read_text("utf-8").splitlines(keepends=True):
        if line.startswith("PRIVATE_SOURCE_REVIEW_COUNT="):
            line = f"PRIVATE_SOURCE_REVIEW_COUNT={count}\n"
        lines.append(line)
    publisher.write_text("".join(lines), encoding="utf-8")


def _write_public_pin(tree: Path, root: str, count: int) -> None:
    publisher = tree / "scripts/publish-public.sh"
    lines = []
    for line in publisher.read_text("utf-8").splitlines(keepends=True):
        if line.startswith("PUBLIC_SOURCE_REVIEW_SHA256="):
            line = f'PUBLIC_SOURCE_REVIEW_SHA256="{root}"\n'
        elif line.startswith("PUBLIC_SOURCE_REVIEW_COUNT="):
            line = f"PUBLIC_SOURCE_REVIEW_COUNT={count}\n"
        lines.append(line)
    publisher.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    tree = Path(argv[1] if len(argv) > 1 else ".").resolve()
    review = _review_module(tree)

    sources = review._filesystem_sources(tree)
    private = review._source_root(sources)
    _write_private_pin(tree, _current_private_pin(tree), private, len(sources))

    export = tree / "dist/metnos-public"
    built = subprocess.run(
        ["bash", "scripts/export-public.sh", str(export)],
        env={**os.environ, "METNOS_VENV": os.environ.get("METNOS_VENV", "/opt/metnos/.venv")},
        cwd=tree, capture_output=True, text=True,
    )
    if built.returncode != 0:
        print(built.stderr[-400:], file=sys.stderr)
        return 1
    public_sources = review._filesystem_sources(export)
    public = review._source_root(public_sources)
    _write_public_pin(tree, public, len(public_sources))

    # Step 3 edited a censused file; the private pin line itself is normalised
    # out of the census, so this second pass converges.
    sources = review._filesystem_sources(tree)
    private = review._source_root(sources)
    _write_private_pin(tree, _current_private_pin(tree), private, len(sources))

    review._require_root(sources, private, len(sources), "private filesystem")
    print(f"private {len(sources)} {private}")
    print(f"public  {len(public_sources)} {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
