"""Generate the RM-0008 Python inventory from the staged public tree.

This program is intentionally narrow: it accepts no roots, exclusions, or
classifications from the caller. It must run inside the materialized public
worktree after ``git add -A`` and before the public commit is created.
"""
from __future__ import annotations

import argparse
import unicodedata
from pathlib import PurePosixPath

try:
    from .certification_v1 import (
        INVENTORY_PATH,
        REPO_ROOT,
        canonical_json_bytes,
        tracked_python_index_paths,
        validate_production_inventory,
        write_canonical_json,
    )
except ImportError:  # Direct execution by the private publication gate.
    from certification_v1 import (
        INVENTORY_PATH,
        REPO_ROOT,
        canonical_json_bytes,
        tracked_python_index_paths,
        validate_production_inventory,
        write_canonical_json,
    )


def _tracked_public_python_paths() -> list[str]:
    ordered = tracked_python_index_paths()
    for path_text in ordered:
        parts = PurePosixPath(path_text).parts
        if (
            path_text != unicodedata.normalize("NFC", path_text)
            or "\\" in path_text
            or path_text.startswith("/")
            or not path_text.endswith(".py")
            or "//" in path_text
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise RuntimeError(f"non-canonical public Python path: {path_text!r}")
    return ordered


def build_production_inventory_v1() -> dict[str, object]:
    files: list[dict[str, str]] = []
    for path_text in _tracked_public_python_paths():
        classification = (
            "test"
            if path_text == "conftest.py" or path_text.startswith("tests/")
            else "documentation"
            if path_text.startswith("docs/")
            else "productive"
        )
        files.append({"classification": classification, "path": path_text})
    return {
        "files": files,
        "inventory_id": "rm-0008-production-python",
        "schema_version": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="replace the canonical inventory; default is validation only",
    )
    arguments = parser.parse_args()
    generated = build_production_inventory_v1()
    if arguments.write:
        write_canonical_json(INVENTORY_PATH, generated)
    elif INVENTORY_PATH.read_bytes() != canonical_json_bytes(generated):
        raise RuntimeError("production inventory is stale for the public Git index")
    validate_production_inventory(enforce_filesystem=False)
    print(f"RM-0008 public Python inventory: {len(generated['files'])} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
