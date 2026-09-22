#!/usr/bin/env python3
"""Read files through the selected storage backend.

The manifest defines scalar and vector inputs, parsing options, output and
truncation metadata. This dispatcher preserves the backend result unchanged;
it does not impose the historical single-file-only interface.

Local reads are available on server and device. The Google Workspace backend
is loaded lazily on first use, so its server-only dependencies are not needed
for local device reads. An explicit download destination selects its download
operation; other requests use the backend's read operation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from backends.files import local  # noqa: E402

# Keep server-only provider dependencies out of local device imports.
# Store modules, resolving their read function at call time.
_HANDLERS = {
    "local": local,
}


def _backend(client: str):
    b = _HANDLERS.get(client)
    if b is None and client == "google_workspace":
        try:
            from backends.files import google_workspace as _gw  # lazy, server-only
        except ImportError:
            # A missing device provider becomes a structured response below.
            return None
        _HANDLERS[client] = _gw
        b = _gw
    return b


def invoke(args):
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_code": "ERR_ARG_INVALID",
            "error": _msg("ERR_ARG_INVALID", arg="args", reason="must be an object"),
        }
    client = args.get("client") or "local"
    backend = _backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client '{client}'")}
    # Download bytes only when the caller provides an explicit destination.
    if client == "google_workspace" and (args.get("dst_path") or args.get("dst_dir")):
        return backend.download(args)
    # Resolve the method at call time, including in isolated backend tests.
    return backend.read(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
