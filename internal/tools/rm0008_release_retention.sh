#!/bin/bash
# Run the reviewed release-tree maintenance through the existing admin runner.
set -euo pipefail
case "${2:-}" in preview|apply) ;; *) exit 64 ;; esac
/opt/metnos/.venv/bin/python -I -B - "$1" "$2" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

source = Path("/opt/metnos/.claude/worktrees/rm0009-development/internal/tools/rm0008_release_cycle.py")
expected = "ec5baca6b8c78af75a436c5393c031a43479385c436b2997f7f7bdb076fcd151"
payload = source.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise RuntimeError("release_retention_source_changed")
namespace = {"__name__": "reviewed_release_retention", "__file__": str(source)}
exec(compile(payload, str(source), "exec"), namespace)
root = namespace["ROOT"] / "releases-v1"

def allocated():
    return int(subprocess.check_output(
        ["/usr/bin/du", "-s", "--block-size=1", str(root)], timeout=30,
    ).split()[0])

def http_pid():
    return int(subprocess.check_output(
        ["/usr/bin/systemctl", "show", "metnos-http.service", "-p", "MainPID", "--value"],
        timeout=5,
    ).strip())

result = {"mode": sys.argv[2], "source_sha256": expected, "scope": "obsolete_release_code_only"}
before, pid = allocated(), http_pid()
try:
    result.update(namespace["prune_releases"](keep=2, apply=sys.argv[2] == "apply"))
    result.update(status="completed", allocated_before=before, allocated_after=allocated(),
                  http_pid_before=pid, http_pid_after=http_pid())
except Exception as exc:
    result.update(status="not_completed", error=type(exc).__name__, detail=str(exc)[:300])
encoded = json.dumps(result, sort_keys=True).encode() + b"\n"
if len(encoded) > 8192:
    raise RuntimeError("retention_result_size_limit")
with (Path(sys.argv[1]) / "result.json").open("xb") as stream:
    stream.write(encoded)
    stream.flush()
    os.fsync(stream.fileno())
raise SystemExit(0 if result["status"] == "completed" else 1)
PY
