#!/usr/bin/env python3
"""Check or update the fixed standalone boundary-policy projection."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile


REPO_ROOT_V1 = Path(__file__).resolve().parents[2]
TARGET_V1 = REPO_ROOT_V1 / "runtime" / "executor_birth_admin_preflight.py"
sys.path.insert(0, str(REPO_ROOT_V1 / "runtime"))

from contract_boundary_analyzer_projection import (  # noqa: E402
    ContractBoundaryAnalyzerProjectionError,
    check_generated_region_v1 as check_analyzer_region_v1,
    replace_generated_region_v1 as replace_analyzer_region_v1,
)
from contract_boundary_projection import (  # noqa: E402
    ContractBoundaryProjectionError,
    check_generated_region_v1 as check_policy_region_v1,
    replace_generated_region_v1 as replace_policy_region_v1,
)
from executor_birth_legacy_state_preflight_projection import (  # noqa: E402
    LegacyStatePreflightProjectionError,
    check_generated_region_v1 as check_legacy_state_region_v1,
    replace_generated_region_v1 as replace_legacy_state_region_v1,
)


def _arguments_v1(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    return parser.parse_args(argv)


def _write_temporary_v1(content: bytes, mode: int) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{TARGET_V1.name}.", dir=TARGET_V1.parent,
    )
    temporary = Path(temporary_name)
    retained = False
    try:
        os.fchmod(descriptor, mode)
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        retained = True
        return temporary
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not retained:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked_parent_v1():
    if os.name != "posix":
        raise OSError("projection writer requires POSIX directory locks")
    import fcntl

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(TARGET_V1.parent, flags)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write_v1(expected: bytes, content: bytes) -> None:
    with _locked_parent_v1() as parent_descriptor:
        mode = TARGET_V1.stat().st_mode & 0o777
        temporary = _write_temporary_v1(content, mode)
        try:
            if TARGET_V1.read_bytes() != expected:
                raise ContractBoundaryProjectionError("target_changed")
            os.replace(temporary, TARGET_V1)
            os.fsync(parent_descriptor)
        finally:
            temporary.unlink(missing_ok=True)


def _check_all_regions_v1(source: bytes) -> bool:
    return (
        check_policy_region_v1(source)
        and check_analyzer_region_v1(source)
        and check_legacy_state_region_v1(source)
    )


def _replace_all_regions_v1(source: bytes) -> bytes:
    with_policy = replace_policy_region_v1(source)
    with_analyzer = replace_analyzer_region_v1(with_policy)
    return replace_legacy_state_region_v1(with_analyzer)


def main(argv: list[str] | None = None) -> int:
    args = _arguments_v1(argv)
    try:
        source = TARGET_V1.read_bytes()
        if args.check:
            return 0 if _check_all_regions_v1(source) else 1
        updated = _replace_all_regions_v1(source)
        if updated == source:
            return 0
        _atomic_write_v1(source, updated)
        return 0
    except (
        ContractBoundaryAnalyzerProjectionError,
        ContractBoundaryProjectionError,
        LegacyStatePreflightProjectionError,
        OSError,
    ) as exc:
        print(f"contract_boundary_projection_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
