#!/usr/bin/python3.12
"""Record the abandonment of the crossing the machine proves unattestable.

Root-only, one write. It stops no service, restarts nothing, publishes no
release and rewrites nothing signed. It takes the deployment lock, lets the
preflight prove the permanent contradiction from the signed history, and
publishes one immutable control document beside the transaction, which keeps
its truthful HEAD_REQUIRED record.

The product modules are loaded from the private worktree because the installed
release predates this capability. They are pinned by content: a changed module
refuses to run rather than acting on an unreviewed one.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

WORKTREE = Path("/opt/metnos/.claude/worktrees/rm0008-reboot")
PINNED = {
    "runtime/executor_birth_admin_preflight.py":
        "a1402081db618bd04771325c8f9a436f3e479c95f7021bb5a27d28136dda2ab9",
    "runtime/executor_birth_ownership_coordinator.py":
        "5aeb0755566f50ddbabcdd6c4e17ed980feb4d8709c2615030a53d209deb88db",
    "install/birth_authority_provisioner.py":
        "804841dc587f83a2b7eceb1e4c99bfdd9db40a05e406dc226234dbbad44e8acb",
}
COORDINATOR = Path(
    "/var/lib/metnos/executor-birth/coordinator-v1/transactions-v2"
)


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise SystemExit(f"REFUSED {detail}")


def main() -> int:
    require(len(sys.argv) == 1, "arguments")
    require(hasattr(os, "geteuid") and os.geteuid() == 0, "effective identity")
    for relative, digest in PINNED.items():
        observed = hashlib.sha256((WORKTREE / relative).read_bytes()).hexdigest()
        require(observed == digest, f"module changed {relative}")

    sys.path[:0] = [str(WORKTREE), str(WORKTREE / "runtime")]
    from install.birth_authority_provisioner import (
        abandon_unattestable_transition_v2,
    )

    before = sorted(path.name for path in COORDINATOR.iterdir())
    print("TRANSACTIONS", len(before), flush=True)

    abandonment = abandon_unattestable_transition_v2()
    print("ABANDONED", json.dumps(abandonment.as_value(), sort_keys=True),
          flush=True)

    from executor_birth_ownership_coordinator import (
        _abandonment_for_transaction_v2, _resolve_ownership_coordinator_at_v2,
    )

    graph = _resolve_ownership_coordinator_at_v2(
        COORDINATOR.parent, root_owned=True,
    )
    reread = _abandonment_for_transaction_v2(graph, graph.transactions[-1])
    require(reread == abandonment, "reread binding")
    latest = graph.transactions[-1].latest
    print("REREAD_OK sequence", latest.sequence, "state", latest.state.value,
          "release", latest.release_sequence, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
