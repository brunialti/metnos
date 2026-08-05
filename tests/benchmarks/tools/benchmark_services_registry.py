#!/usr/bin/env python3
"""Compare fresh and reused pools for ``services_registry.snapshots``.

The ``fresh`` condition reconstructs the implementation used before the
process-wide pool was introduced: every call creates a bounded executor, maps
the same snapshot function over the same catalog, and waits for shutdown.  The
``reused`` condition calls the production implementation unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable


_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME = str(_ROOT / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

import services_registry as registry  # noqa: E402


DEFAULT_ITERATIONS = {
    "synthetic": 500,
    "manager": 60,
    "full": 12,
}


def _fresh_snapshots(*, probe_endpoints: bool) -> list[dict]:
    """Reconstruct the old one-pool-per-call implementation exactly."""
    with ThreadPoolExecutor(
            max_workers=min(8, len(registry.SERVICES)),
            thread_name_prefix="metnos_service_probe_fresh") as pool:
        return list(pool.map(
            lambda service: registry._safe_snapshot(  # noqa: SLF001
                service, probe_endpoints,
            ),
            registry.SERVICES,
        ))


def _elapsed_ms(call: Callable[[], list[dict]]) -> tuple[float, list[dict]]:
    started = time.perf_counter_ns()
    rows = call()
    return (time.perf_counter_ns() - started) / 1_000_000, rows


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       int(percentile * len(ordered) + 0.999999) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": round(statistics.fmean(values), 4),
        "p50_ms": round(statistics.median(values), 4),
        "p95_ms": round(_percentile(values, 0.95), 4),
        "min_ms": round(min(values), 4),
        "max_ms": round(max(values), 4),
    }


def _assert_same_catalog(fresh: list[dict], reused: list[dict]) -> None:
    fresh_keys = [row.get("key") for row in fresh]
    reused_keys = [row.get("key") for row in reused]
    expected = [service.key for service in registry.SERVICES]
    if fresh_keys != expected or reused_keys != expected:
        raise RuntimeError(
            "benchmark conditions returned a different catalog or order",
        )


def _run_case(mode: str, *, iterations: int, warmups: int) -> dict:
    probe_endpoints = mode == "full"
    original_snapshot_one = registry.snapshot_one
    if mode == "synthetic":
        registry.snapshot_one = lambda spec, *, probe_endpoint=True: {
            "key": spec.key,
            "installed": True,
        }

    try:
        fresh_call = lambda: _fresh_snapshots(
            probe_endpoints=probe_endpoints,
        )
        reused_call = lambda: registry.snapshots(
            probe_endpoints=probe_endpoints,
        )

        for _ in range(warmups):
            reused_call()
            fresh_call()

        samples = {"fresh": [], "reused": []}
        last_rows: dict[str, list[dict]] = {}
        for index in range(iterations):
            order = (("fresh", fresh_call), ("reused", reused_call))
            if index % 2:
                order = tuple(reversed(order))
            for name, call in order:
                elapsed, rows = _elapsed_ms(call)
                samples[name].append(elapsed)
                last_rows[name] = rows

        _assert_same_catalog(last_rows["fresh"], last_rows["reused"])
        fresh = _summary(samples["fresh"])
        reused = _summary(samples["reused"])
        fresh_p50 = fresh["p50_ms"]
        reused_p50 = reused["p50_ms"]
        return {
            "mode": mode,
            "iterations_per_condition": iterations,
            "warmups_per_condition": warmups,
            "probe_endpoints": probe_endpoints,
            "fresh_pool": fresh,
            "reused_pool": reused,
            "p50_delta_ms": round(reused_p50 - fresh_p50, 4),
            "p50_delta_percent": round(
                100.0 * (reused_p50 - fresh_p50) / fresh_p50, 2,
            ) if fresh_p50 else 0.0,
            "p50_speedup": round(fresh_p50 / reused_p50, 3)
            if reused_p50 else None,
        }
    finally:
        registry.snapshot_one = original_snapshot_one


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=(*DEFAULT_ITERATIONS, "all"), default="all",
        help="synthetic isolates pool overhead; manager skips HTTP probes",
    )
    parser.add_argument(
        "--iterations", type=int,
        help="override the per-mode default sample count",
    )
    parser.add_argument("--warmups", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.iterations is not None and args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    if args.warmups < 0:
        raise SystemExit("--warmups must not be negative")

    modes = tuple(DEFAULT_ITERATIONS) if args.mode == "all" else (args.mode,)
    result = {
        "benchmark": "services_registry_snapshot_pool",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "catalog_services": len(registry.SERVICES),
            "pool_workers": min(8, len(registry.SERVICES)),
        },
        "cases": [
            _run_case(
                mode,
                iterations=(args.iterations
                            if args.iterations is not None
                            else DEFAULT_ITERATIONS[mode]),
                warmups=args.warmups,
            )
            for mode in modes
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
