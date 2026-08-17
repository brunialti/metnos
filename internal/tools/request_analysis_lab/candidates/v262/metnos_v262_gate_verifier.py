#!/usr/bin/env python3
"""Offline verifier for the one-run V26.2 native inference authorization."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "/tmp")
import metnos_v262_minimal_phase1_runner as candidate  # noqa: E402


GATE = Path("/tmp/metnos_v262_minimal_phase1_preinference_gate.json")
LOCK = Path("/tmp/metnos_v262_minimal_phase1_gate.lock.json")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify() -> dict[str, object]:
    lock = json.loads(LOCK.read_text())
    gate = json.loads(GATE.read_text())
    checks: dict[str, bool] = {
        "gate_locked": lock["gate_sha256"] == sha(GATE),
        "candidate_freeze_locked": lock["freeze_sha256"] == sha(candidate.FREEZE_PATH),
        "audit_locked": lock["audit_sha256"] == sha(Path(gate["audit_path"])),
        "review_locked": lock["independent_review_sha256"] == sha(Path(gate["independent_review_path"])),
        "gate_freeze_binding": gate["freeze_sha256"] == lock["freeze_sha256"],
        "gate_audit_binding": gate["audit_sha256"] == lock["audit_sha256"],
        "gate_review_binding": gate["independent_review_sha256"] == lock["independent_review_sha256"],
        "inference_allowed": gate.get("inference_allowed") is True,
        "single_native_run": gate.get("authorized_native_runs") == 1,
        "single_repetition": gate.get("authorized_repetitions") == 1,
        "frozen_34_controls": gate.get("authorized_case_count") == 34,
        "route_integration_blocked": gate.get("one_call_route_integration_allowed") is False,
        "runtime_cutover_blocked": gate.get("runtime_cutover_eligible") is False,
    }
    review = json.loads(Path(gate["independent_review_path"]).read_text())
    reviewed = review["reviewed_candidate"]
    freeze = json.loads(candidate.FREEZE_PATH.read_text())
    checks.update({
        "review_recommends_candidate": review["decision"]["recommended_single_candidate"] == candidate.VERSION,
        "review_approves_one_run": review["decision"]["recommendation"] == "approve_one_native_k1_after_gate_flip",
        "review_runner_binding": reviewed["runner_sha256"] == freeze["runner_sha256"],
        "review_freeze_binding": reviewed["freeze_sha256"] == sha(candidate.FREEZE_PATH),
        "review_schema_binding": reviewed["schema_sha256"] == freeze["schema_sha256"],
        "review_prompt_binding": reviewed["prompt_sha256"] == freeze["prompt_sha256"],
        "review_mutations_binding": reviewed["mutations_sha256"] == freeze["mutations_sha256"],
    })
    candidate.verify_freeze()
    mutations = candidate.mutation_tests()["summary"]
    checks["mutations_27_of_27"] = mutations == {"tests": 27, "passed": 27, "failed": 0}
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError(f"V26.2 gate verification failed: {failed}")
    return {
        "status": "verified_one_native_k1_only",
        "checks": len(checks), "passed": len(checks), "failed": 0,
        "gate_sha256": sha(GATE), "freeze_sha256": sha(candidate.FREEZE_PATH),
        "audit_sha256": sha(Path(gate["audit_path"])),
        "independent_review_sha256": sha(Path(gate["independent_review_path"])),
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
