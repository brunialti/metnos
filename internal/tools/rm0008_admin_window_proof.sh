#!/bin/bash
# Read-only installed startup proof for the reviewed standalone candidate.
set -euo pipefail
test "${2:-}" = read-only
/usr/bin/python3 -I -B -S - "$1" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import types

source = Path("/opt/metnos/.claude/worktrees/rm0009-development/runtime/executor_birth_admin_preflight.py")
expected = "4c4f360d58f00854bd17795af75b30549daf70108a1dec8a6f15b8a607ea9a85"
payload = source.read_bytes()
if hashlib.sha256(payload).hexdigest() != expected:
    raise RuntimeError("reviewed_admin_source_changed")
module = types.ModuleType("reviewed_admin_window")
module.__file__ = str(source)
sys.modules[module.__name__] = module
exec(compile(payload, str(source), "exec"), vars(module))
output = os.open(str(Path(sys.argv[1]) / "result.json"),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
root = module.OWNERSHIP_ROOT
reads = set()
denied = {"writes": 0, "private_keys": 0, "history_enumerations": 0}

def audit(event, args):
    if event == "open":
        path, _mode, flags = args
        if isinstance(path, (str, bytes)):
            path = Path(os.fsdecode(path))
            # subprocess.DEVNULL opens the null device read/write for stdin;
            # it is not a persistent file mutation.
            if path != Path(os.devnull) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                denied["writes"] += 1
                raise PermissionError("proof_named_write_denied")
            if "-private-" in path.name or path.suffix == ".key":
                denied["private_keys"] += 1
                raise PermissionError("proof_private_key_read_denied")
            if path.is_relative_to(root):
                reads.add(path.relative_to(root).as_posix())
    elif event in {"os.listdir", "os.scandir"}:
        path = args[0]
        if isinstance(path, int):
            path = os.readlink(f"/proc/self/fd/{path}")
        if isinstance(path, (str, bytes)):
            path = Path(os.fsdecode(path))
            if path.is_relative_to(root) and not path.is_relative_to(module.RELEASE_ROOT):
                denied["history_enumerations"] += 1
                raise PermissionError("proof_history_enumeration_denied")

def http_pid():
    return int(subprocess.check_output(
        ["/usr/bin/systemctl", "show", "metnos-http.service", "-p", "MainPID", "--value"],
        timeout=5,
    ).strip())

pid = http_pid()
sys.addaudithook(audit)
signal.alarm(180)
start = time.monotonic()
result = {"source_sha256": expected, "scope": "candidate_read_only_not_deployment"}
lease = None
try:
    lease = module._LaunchGateLeaseV1(module._acquire_startup_gate_shared_v1())
    materials, entry = module._attest_service_startup_v1("service-http")
    heads = {path for path in reads if path.startswith("chain-v1/heads-v1/") and path.endswith(".json")}
    transactions = {path.split("/")[2] for path in reads if path.startswith("coordinator-v1/transactions-v2/")}
    if not 1 <= len(heads) <= 3 or not 1 <= len(transactions) <= 3:
        raise RuntimeError("unbounded_admin_history_read")
    result.update(status="verified", release_sequence=materials.distribution.facts.release_sequence,
                  required_head_id=materials.transaction.head_id, entry_id=entry.entry_id,
                  selected_heads=len(heads), selected_transactions=len(transactions),
                  http_pid_before=pid, http_pid_after=http_pid())
except Exception as exc:
    result.update(status="refused", error=type(exc).__name__, detail=str(exc)[:300])
finally:
    if lease is not None:
        lease.close()
    signal.alarm(0)
    result.update(elapsed_seconds=round(time.monotonic() - start, 3), denied=denied)
    encoded = json.dumps(result, sort_keys=True).encode() + b"\n"
    if len(encoded) > 8192:
        raise RuntimeError("proof_result_size_limit")
    os.write(output, encoded)
    os.fsync(output)
    os.close(output)
raise SystemExit(0 if result["status"] == "verified" else 1)
PY
