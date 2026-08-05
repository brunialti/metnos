#!/usr/bin/env python3
"""Find an executable command in the effective system ``PATH``."""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error": _msg("ERR_ARGS_NOT_OBJECT"),
            "error_class": "invalid_input",
            "error_code": "args_not_object",
            "ok_count": 0,
            "fail_count": 1,
            "entries": [],
            "failed": [],
        }
    package_name = args.get("package_name")

    entries = []
    failed = []
    ok_count = 0
    fail_count = 0

    normalized_name = package_name.strip() if isinstance(package_name, str) else ""
    if not normalized_name:
        fail_count = 1
        error = _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="package_name")
    elif (len(normalized_name) > 128 or "/" in normalized_name
          or "\\" in normalized_name or normalized_name in {".", ".."}):
        fail_count = 1
        error = _msg(
            "ERR_ARG_INVALID", arg="package_name",
            reason="simple command name required",
        )
    else:
        error = ""
    if error:
        failed.append({
            "error": error,
            "error_class": "invalid_input",
            "error_code": "package_name_invalid",
        })
        return {
            "ok": False,
            "error": error,
            "error_class": "invalid_input",
            "error_code": "package_name_invalid",
            "ok_count": 0,
            "fail_count": fail_count,
            "entries": [],
            "failed": failed
        }

    try:
        # shutil.which is the standard way to find an executable in PATH
        path = shutil.which(normalized_name)

        if path:
            entries.append({
                "package_name": normalized_name,
                "path": path
            })
            ok_count = 1
    except (OSError, TypeError):
        fail_count = 1
        error = _msg("ERR_OP_FAILED", reason="executable lookup")
        failed.append({
            "package_name": normalized_name,
            "error": error,
            "error_class": "resource_unavailable",
            "error_code": "executable_lookup_failed",
        })
        return {
            "ok": False,
            "error": error,
            "error_class": "resource_unavailable",
            "error_code": "executable_lookup_failed",
            "ok_count": 0,
            "fail_count": fail_count,
            "entries": [],
            "failed": failed,
        }

    return {
        "ok": True,
        "ok_count": ok_count,
        "fail_count": 0,
        "entries": entries,
        "failed": [],
        "found": bool(entries),
    }

def main():
    run_stdio(invoke, allow_empty=True)

if __name__ == "__main__":
    main()
