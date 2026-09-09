#!/usr/bin/python3.12
"""Read what the chain says about the next release. Reads only, changes nothing.

Root-only because the coordinator journal is root-owned. It opens no lock,
writes no file, stops no service and publishes nothing: it reports the facts a
build helper must be pinned to, so the pins are measured instead of assumed.

The product modules come from the private worktree because the installed
release predates this capability, and they are pinned by content: a changed
module refuses to run rather than reporting from an unreviewed one.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

WORKTREE = Path("/opt/metnos/.claude/worktrees/rm0008-reboot")
PINNED = {
    "runtime/executor_birth_ownership_coordinator.py":
        "5aeb0755566f50ddbabcdd6c4e17ed980feb4d8709c2615030a53d209deb88db",
}
OWNERSHIP = Path("/var/lib/metnos/executor-birth/coordinator-v1")


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
    from executor_birth_ownership_coordinator import (
        _abandonment_for_transaction_v2, _resolve_ownership_coordinator_at_v2,
    )

    graph = _resolve_ownership_coordinator_at_v2(OWNERSHIP, root_owned=True)
    print("PENDING_CLAIMS", len(graph.pending_claims), flush=True)
    print("TRANSACTIONS", len(graph.transactions), flush=True)
    require(bool(graph.transactions), "no transaction to read")

    current = graph.transactions[-1]
    latest = current.latest
    abandonment = _abandonment_for_transaction_v2(graph, current)
    print("LATEST", json.dumps({
        "release_sequence": latest.release_sequence,
        "phase": latest.sequence,
        "state": latest.state.value,
        "closed_build_id": latest.closed_build_id,
        "head_id": latest.head_id,
        "previous_head_id": latest.previous_head_id,
        "previous_closed_build_id": latest.previous_closed_build_id,
        "claim_source_id": current.claim.source_id,
    }, sort_keys=True), flush=True)
    print("ABANDONED", "yes" if abandonment is not None else "no", flush=True)
    if abandonment is not None:
        print("ABANDONMENT", json.dumps(abandonment.as_value(), sort_keys=True),
              flush=True)

    # What `_next_release_edge_v1` will derive for a source it has never seen.
    verified = latest.state.value == "PREFLIGHT_VERIFIED" and latest.sequence == 6
    if verified or abandonment is not None:
        print("NEXT_RELEASE_SEQUENCE", latest.release_sequence + 1, flush=True)
        print("NEXT_PREVIOUS_BUILD", latest.closed_build_id, flush=True)
    else:
        print("NEXT_RELEASE refused: the crossing is neither verified nor "
              "abandoned", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
