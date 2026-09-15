#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Repeatable isolated gate for metnos.target/reconcile lifecycle contracts.

The gate never writes user units and never controls the live managers.  It
runs the fresh/upgrade/pilot/rollback state machine, composite HTTP contract,
installer regressions and ``systemd-analyze verify`` twice without edits
between cycles, then writes machine-readable and Markdown evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / "internal" / "reports" / "stack_lifecycle.json"
TESTS = (
    "tests/runtime/http/test_http_routes_stack.py",
    "tests/runtime/infra/test_stack_reconcile.py",
    "tests/runtime/infra/test_stack_migration.py",
    "tests/runtime/infra/test_stack_units.py",
    "tests/runtime/engine/test_phase5_stack_target.py",
    "tests/runtime/sites/test_playwright_contract.py",
    "tests/runtime/sites/test_playwright_install_contract.py",
    "tests/runtime/infra/test_services_registry.py",
    "tests/runtime/skills/test_install_llama_completion.py",
    "tests/runtime/skills/test_install_llm_service.py",
    "tests/runtime/sites/test_sidecar_searxng.py",
)


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _cycle(number: int) -> dict:
    print(f"stack lifecycle cycle {number}/2: start", flush=True)
    started = time.monotonic()
    command = [sys.executable, "-m", "pytest", "-q", *TESTS]
    # A test-owned child may inherit stdout. PIPE-based communicate() would
    # then wait for that unrelated descriptor even after pytest has exited.
    # A regular temporary log makes the wait depend only on the pytest PID.
    with tempfile.TemporaryDirectory(prefix="metnos-stack-gate-") as temp:
        log_path = Path(temp) / "pytest.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                text=True, check=False, timeout=300,
            )
        output = log_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"(\d+) passed", output)
    row = {
        "cycle": number,
        "ok": result.returncode == 0 and match is not None,
        "exit_code": result.returncode,
        "passed": int(match.group(1)) if match else 0,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "summary_tail": "\n".join(output.splitlines()[-8:]),
    }
    print(
        f"stack lifecycle cycle {number}/2: "
        f"{'green' if row['ok'] else 'red'} ({row['passed']} passed)",
        flush=True,
    )
    return row


def _markdown(report: dict) -> str:
    lines = [
        "# Metnos stack lifecycle gate",
        "",
        f"- Profile: `{report['profile']}`",
        f"- Result: `{'green' if report['ok'] else 'red'}`",
        f"- Generated: `{report['generated_at']}`",
        "- Live ownership observed by this isolated gate: `false`",
        "",
        "| Cycle | Result | Passed | Duration ms |",
        "|---:|---|---:|---:|",
    ]
    for row in report["cycles"]:
        lines.append(
            f"| {row['cycle']} | {'green' if row['ok'] else 'red'} | "
            f"{row['passed']} | {row['duration_ms']} |"
        )
    lines += [
        "",
        "The isolated profile validates unit semantics, composite readiness,",
        "fresh/upgrade behavior, two-cycle pilot evidence, rollback and guarded",
        "cutover failure recovery. It neither inspects nor changes live service",
        "ownership; that evidence belongs to `stack_live.*`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    cycles = [_cycle(1), _cycle(2)]
    report = {
        "schema_version": 1,
        "profile": "isolated-systemd-contract",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ok": all(row["ok"] for row in cycles),
        "live_cutover_performed": None,
        "live_ownership_observed": False,
        "coverage": [
            "composite_health", "catalog_parity", "fingerprint",
            "quiescence", "bounded_watchdog", "fresh_install",
            "legacy_upgrade", "two_cycle_pilot", "rollback",
            "guarded_cutover", "systemd_unit_verify",
        ],
        "host_only_companion_gate": {
            "tests": ["tests/runtime/http/test_http_server.py"],
            "reason": "requires local socket bind unavailable in isolated profile",
        },
        "cycles": cycles,
    }
    _atomic(args.report, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _atomic(args.report.with_suffix(".md"), _markdown(report))
    print(json.dumps({
        "ok": report["ok"],
        "profile": report["profile"],
        "cycles": [{"cycle": row["cycle"], "passed": row["passed"], "ok": row["ok"]}
                   for row in cycles],
    }, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
