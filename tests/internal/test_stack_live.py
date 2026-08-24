"""Explicitly armed live gate for the integrated stack contract.

Normal suites skip this file.  A maintainer must create the sentinel under
``/tmp`` for one intentional run; output and the report contain only health
metadata and turn identifiers, never messages, keys or catalog payloads.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import stack_reconcile as stack


SENTINEL = Path("/tmp/metnos-stack-live-enable")
REPORT = Path("/tmp/metnos_stack_live_report.json")
LIVE_ADMIN_KEY_PATH = Path(os.environ.get(
    "METNOS_LIVE_ADMIN_KEY_PATH", "/nonexistent/metnos-live-admin.key",
))


def _turn(cycle: int) -> dict:
    body = json.dumps({
        "query": "che ore sono?",
        "actor": "stack-live-gate",
        "conversation_id": f"stack-live-gate-{cycle}",
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8770/agent/turn",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + stack._admin_key(LIVE_ADMIN_KEY_PATH),
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    steps = payload.get("steps_summary") or []
    assert payload.get("final_kind") == "answer"
    assert payload.get("turn_id")
    assert steps
    assert all(step.get("ok") is True for step in steps)
    return {
        "turn_id": payload["turn_id"],
        "final_kind": payload["final_kind"],
        "tools": [step.get("tool") for step in steps],
    }


@pytest.mark.skipif(not SENTINEL.is_file(), reason="live stack gate not armed")
def test_two_live_stack_cycles_are_ready_and_quiescent(tmp_path):
    assert LIVE_ADMIN_KEY_PATH.is_file(), (
        "METNOS_LIVE_ADMIN_KEY_PATH must name the live admin key"
    )
    reconciler = stack.StackReconciler(
        report_path=tmp_path / "last.json",
        admin_key_path=LIVE_ADMIN_KEY_PATH,
    )
    system_http = reconciler.systemctl.show("metnos-http.service", "system")
    user_target = reconciler.systemctl.show(stack.TARGET_UNIT, "user")
    integrated_ownership = (
        system_http.get("ActiveState") != "active"
        and user_target.get("ActiveState") == "active"
    )
    cycles = []
    for number in (1, 2):
        before = reconciler.check(require_quiescent=True)
        turn = _turn(number)
        after = reconciler.check(require_quiescent=True)
        cycles.append({
            "cycle": number,
            "ok": True,
            "before_ready": before["ready"],
            "after_ready": after["ready"],
            "turn": turn,
        })
    report = {
        "schema_version": 1,
        "profile": (
            "live-integrated-user-target" if integrated_ownership
            else "live-legacy-host-with-integrated-health"
        ),
        "ok": True,
        "live_cutover_performed": integrated_ownership,
        "ownership": {
            "system_http_active": system_http.get("ActiveState") == "active",
            "system_http_enabled": system_http.get("UnitFileState") == "enabled",
            "user_target_active": user_target.get("ActiveState") == "active",
            "user_target_enabled": user_target.get("UnitFileState") == "enabled",
        },
        "cycles": cycles,
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
