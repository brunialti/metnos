#!/usr/bin/python3.12
"""Read-only root diagnosis using the exact installed preflight, without launch."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import traceback


PREFLIGHT = Path("/usr/libexec/metnos/executor-birth-v1/preflight.py")
SHA256 = "0fd620bf0d519024368dfec61e5a39f39f22fa7c7e8a8642457be978c0cd15ca"


def emit(message: str) -> None:
    print(message, flush=True)
    subprocess.run(
        ["/usr/bin/logger", "--tag", "metnos-boot-diagnosis", "--", message],
        check=False, timeout=5,
    )


def installed_preflight() -> dict:
    info = PREFLIGHT.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (info.st_uid, info.st_gid) != (0, 0) or info.st_mode & 0o022
            or hashlib.sha256(PREFLIGHT.read_bytes()).hexdigest() != SHA256):
        raise RuntimeError("installed preflight identity differs")
    return runpy.run_path(str(PREFLIGHT), run_name="metnos_root_diagnosis")


def diagnose(module: dict) -> int:
    module["require_linux_before_io_v1"]()
    try:
        # Same fixed-root check as the public CLI, but retain its internal error.
        # Never call launch, change validation, or replace a live module.
        module["_run_operational_command_v1"](
            module["parse_cli_v1"](["check", "--entry-id", "service-http"]),
        )
    except Exception as error:
        code, status = module["_public_failure_v1"](error)
        emit(f"PREFLIGHT_DIAGNOSIS_REFUSED code={code} exit={status}")
        if isinstance(error, module["PreflightError"]):
            emit("DETAIL " + error.detail[:1600])
        # Function names and lines only: no private locals, source or exception payload.
        for frame in traceback.extract_tb(error.__traceback__)[-12:]:
            emit(f"FRAME {Path(frame.filename).name}:{frame.lineno} {frame.name}")
        return status
    emit("PREFLIGHT_DIAGNOSIS_OK; no services were started")
    return 0


def main() -> int:
    if os.geteuid() != 0 or len(sys.argv) != 1:
        print("DIAGNOSIS_REFUSED root and zero arguments required", file=sys.stderr)
        return 78
    try:
        return diagnose(installed_preflight())
    except Exception as error:
        emit(f"DIAGNOSIS_UNAVAILABLE {type(error).__name__}")
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
