#!/usr/bin/env python3
"""Run the eight-case RM-0006 quick gate against isolated Metnos HTTP."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from certification.coordinator import canonical_json, read_jsonl, run_batch
from certification.http_collector import (
    QUICK_FLOW_IDS,
    build_observation,
    cleanup_quick_fixture,
    prepare_quick_fixture,
    tree_digest,
)
from certification.golden_matrix import CASE_PATH
from certification.run_synthetic import build_manifest
from driver.http_client import E2EClient
from driver.server import E2EServer


def _quick_cases() -> list[dict]:
    by_flow = {
        case["logical_flow_id"]: case for case in read_jsonl(CASE_PATH)
        if case["locale"] == "it"
    }
    return [by_flow[flow_id] for flow_id in QUICK_FLOW_IDS]


def _manifest(cases: list[dict], cycles: list[int]) -> dict:
    manifest = build_manifest(cases, cycles=cycles)
    manifest.update({
        "certification_id": "rm0006-c2-quick-v1",
        "oracle_version": "rm0006-golden-oracle/2",
        "platform": "isolated-metnos-http",
        "fixture": "rm0006-quick-v1",
        "locales": ["it"],
    })
    return manifest


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()


async def _collect_cycle(
    server: E2EServer, cases: list[dict], cycle: int,
    observations_path: Path, completed: set[tuple[str, int]],
) -> None:
    async with E2EClient(server.url, server.admin_key, timeout_s=300) as client:
        for case in cases:
            key = (case["case_id"], cycle)
            if key in completed:
                continue
            before = prepare_quick_fixture()
            response = await client.chat(case["request"], lang=case["locale"])
            raw = response.raw or {
                "final_kind": "error", "final_message": response.error or "",
                "steps_summary": [], "total_ms": case["budgets"]["deadline_s"] * 1000,
                "transport_error": response.error or "empty HTTP response",
            }
            observation = build_observation(
                case, cycle, raw, user_data=server.user_data,
                before_digest=before, after_digest=tree_digest(
                    Path("/tmp/metnos-certification-fixture"),
                ),
            )
            _append(observations_path, observation)
            completed.add(key)
            print(
                f"cycle={cycle} case={case['case_id']} "
                f"terminal={observation['terminal']} plan={observation['plan']} "
                f"error={response.error or '-'}",
                flush=True,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=2)
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    cases = _quick_cases()
    cycles = list(range(1, args.cycles + 1))
    observations_path = args.output / "observations.redacted.jsonl"
    existing = read_jsonl(observations_path)
    completed = {(item["case_id"], item["cycle"]) for item in existing}
    try:
        for cycle in cycles:
            pending = any((case["case_id"], cycle) not in completed for case in cases)
            if not pending:
                continue
            server = E2EServer.spawn(seed_realistic=True, ready_timeout_s=60)
            try:
                asyncio.run(_collect_cycle(
                    server, cases, cycle, observations_path, completed,
                ))
            finally:
                server.shutdown(cleanup=True)
        observations = read_jsonl(observations_path)
        summary = run_batch(
            output_dir=args.output,
            manifest=_manifest(cases, cycles),
            cases=cases,
            observations=observations,
        )
    finally:
        cleanup_quick_fixture()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["objective_achieved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
