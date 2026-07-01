#!/usr/bin/env python3
"""
find_packages.py
Verifica se un comando o un pacchetto specifico è installato sul sistema operativo.
"""

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
    package_name = args.get("package_name")
    
    results = []
    failed = []
    ok_count = 0
    fail_count = 0

    if not package_name or not isinstance(package_name, str):
        fail_count = 1
        failed.append({"error": _msg("ERR_ARG_NOT_STRING", arg="package_name")})
        return {
            "ok": False,
            "ok_count": 0,
            "fail_count": fail_count,
            "results": [],
            "failed": failed
        }

    try:
        # shutil.which is the standard way to find an executable in PATH
        path = shutil.which(package_name)
        
        if path:
            results.append({
                "package_name": package_name,
                "path": path
            })
            ok_count = 1
        else:
            fail_count = 1
            failed.append({
                "package_name": package_name,
                "error": _msg("ERR_PACKAGE_NOT_FOUND", name=package_name)
            })
            
    except Exception as e:
        fail_count = 1
        failed.append({
            "package_name": package_name,
            "error": str(e)
        })

    return {
        "ok": fail_count == 0,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "results": results,
        "failed": failed
    }

def main():
    run_stdio(invoke, allow_empty=True)

if __name__ == "__main__":
    main()