#!/usr/bin/env python3
"""Fail closed when the standalone Birth boundary policy is stale."""
from __future__ import annotations

from pathlib import Path
import sys


REPOSITORY_ROOT_V1 = Path(__file__).resolve().parents[1]
PREFLIGHT_V1 = (
    REPOSITORY_ROOT_V1 / "runtime" / "executor_birth_admin_preflight.py"
)
sys.path.insert(0, str(REPOSITORY_ROOT_V1 / "runtime"))

from contract_boundary_projection import (  # noqa: E402
    ContractBoundaryProjectionError,
    check_generated_region_v1,
)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("contract_boundary_projection_error:arguments", file=sys.stderr)
        return 2
    try:
        source = PREFLIGHT_V1.read_bytes()
        if check_generated_region_v1(source):
            return 0
        print("contract_boundary_projection_error:stale", file=sys.stderr)
        return 1
    except (ContractBoundaryProjectionError, OSError) as exc:
        print(f"contract_boundary_projection_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
