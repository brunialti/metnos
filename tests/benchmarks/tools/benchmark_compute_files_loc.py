#!/usr/bin/env python3
"""Benchmark real worker scaling and overlap for ``compute_files_loc``."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_EXECUTOR_DIR = _ROOT / "executors" / "compute_files_loc"
_RUNTIME_DIR = _ROOT / "runtime"


def _positive_csv(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(dict.fromkeys(int(value) for value in raw.split(",")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated integers") from exc
    if not values or any(value < 1 for value in values):
        raise argparse.ArgumentTypeError("values must be positive")
    return values


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       int(percentile * len(ordered) + 0.999999) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "mean_ms": round(statistics.fmean(values), 3),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }


def _rss_kib() -> int:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


class _ProcessSampler:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self.peak_rss_kib = _rss_kib()
        self.peak_threads = len(threading.enumerate())
        self.peak_executor_threads = 0
        self._thread = threading.Thread(
            target=self._sample,
            name="metnos_benchmark_sampler",
            daemon=True,
        )

    def _sample(self) -> None:
        while not self.stop.wait(0.002):
            threads = threading.enumerate()
            self.peak_rss_kib = max(self.peak_rss_kib, _rss_kib())
            self.peak_threads = max(self.peak_threads, len(threads))
            self.peak_executor_threads = max(
                self.peak_executor_threads,
                sum(thread.name.startswith("ThreadPoolExecutor-")
                    for thread in threads),
            )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop.set()
        self._thread.join(timeout=1)


def _digest(result: dict) -> str:
    payload = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_group(module, args: dict, concurrent_calls: int):
    if concurrent_calls == 1:
        started = time.perf_counter_ns()
        result = module.invoke(args)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        return elapsed, [elapsed], [result]

    barrier = threading.Barrier(concurrent_calls + 1)
    results: list[dict | None] = [None] * concurrent_calls
    elapsed_calls: list[float] = [0.0] * concurrent_calls
    errors: list[BaseException] = []

    def _invoke(index: int) -> None:
        try:
            barrier.wait()
            started = time.perf_counter_ns()
            results[index] = module.invoke(args)
            elapsed_calls[index] = (
                time.perf_counter_ns() - started
            ) / 1_000_000
        except BaseException as exc:  # diagnostic must report worker crashes
            errors.append(exc)

    threads = [
        threading.Thread(
            target=_invoke, args=(index,), name=f"metnos_bench_call_{index}",
        )
        for index in range(concurrent_calls)
    ]
    for thread in threads:
        thread.start()
    started = time.perf_counter_ns()
    barrier.wait()
    for thread in threads:
        thread.join()
    elapsed_group = (time.perf_counter_ns() - started) / 1_000_000
    if errors:
        raise errors[0]
    return elapsed_group, elapsed_calls, [
        result for result in results if isinstance(result, dict)
    ]


def _child(args: argparse.Namespace) -> int:
    os.environ["METNOS_COMPUTE_LOC_WORKERS"] = str(args.worker_count)
    sys.path.insert(0, str(_RUNTIME_DIR))
    sys.path.insert(0, str(_EXECUTOR_DIR))
    import compute_files_loc  # noqa: E402

    compute_files_loc._LOC_WORKERS = args.worker_count
    invoke_args = {
        "paths": [str(Path(args.root).resolve())],
        "include_ext": [args.include_ext],
        "max_files": args.max_files,
    }
    baseline_rss_kib = _rss_kib()
    group_samples: list[float] = []
    call_samples: list[float] = []
    results: list[dict] = []

    with _ProcessSampler() as sampler:
        for _ in range(args.warmups):
            _run_group(compute_files_loc, invoke_args, args.concurrent_calls)
        for _ in range(args.iterations):
            group_ms, calls_ms, group_results = _run_group(
                compute_files_loc, invoke_args, args.concurrent_calls,
            )
            group_samples.append(group_ms)
            call_samples.extend(calls_ms)
            results.extend(group_results)

    digests = sorted({_digest(result) for result in results})
    total_files = sum(int(result.get("total_files", 0)) for result in results)
    failed_files = sum(int(result.get("fail_count", 0)) for result in results)
    payload = {
        "worker_limit": args.worker_count,
        "concurrent_calls": args.concurrent_calls,
        "iterations": args.iterations,
        "warmups": args.warmups,
        "group_latency": _summary(group_samples),
        "call_latency": _summary(call_samples),
        "baseline_rss_kib": baseline_rss_kib,
        "peak_rss_kib": sampler.peak_rss_kib,
        "rss_delta_kib": max(0, sampler.peak_rss_kib - baseline_rss_kib),
        "peak_process_threads": sampler.peak_threads,
        "peak_executor_threads": sampler.peak_executor_threads,
        "result_digests": digests,
        "structurally_stable": len(digests) == 1,
        "sample_total_files": results[0].get("total_files") if results else 0,
        "sample_total_lines": results[0].get("total_lines") if results else 0,
        "failed_files": failed_files,
        "error_rate": round(
            failed_files / max(1, total_files + failed_files), 8,
        ),
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(_ROOT))
    parser.add_argument("--include-ext", default=".py")
    parser.add_argument("--max-files", type=int, default=50_000)
    parser.add_argument("--workers", type=_positive_csv, default=(1, 2, 4, 8, 16))
    parser.add_argument(
        "--concurrent-calls", type=_positive_csv, default=(1, 2),
    )
    parser.add_argument("--iterations", type=int, default=6)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-count", type=int, default=1,
                        help=argparse.SUPPRESS)
    parser.add_argument("--concurrent-call-count", type=int,
                        dest="concurrent_calls", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.iterations < 1 or args.warmups < 0 or args.max_files < 1:
        raise SystemExit("iterations/max-files must be positive; warmups >= 0")
    if args._child:
        if args.worker_count < 1 or not args.concurrent_calls:
            raise SystemExit("invalid child configuration")
        return _child(args)

    cases = []
    with tempfile.TemporaryDirectory(prefix="metnos-compute-loc-bench-") as state:
        for concurrent_calls in args.concurrent_calls:
            for worker_count in args.workers:
                command = [
                    sys.executable, str(Path(__file__).resolve()),
                    "--_child",
                    "--root", args.root,
                    "--include-ext", args.include_ext,
                    "--max-files", str(args.max_files),
                    "--iterations", str(args.iterations),
                    "--warmups", str(args.warmups),
                    "--worker-count", str(worker_count),
                    "--concurrent-call-count", str(concurrent_calls),
                ]
                env = os.environ.copy()
                env["METNOS_USER_STATE"] = state
                completed = subprocess.run(
                    command, capture_output=True, text=True, check=False,
                    env=env,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"benchmark child failed: {completed.stderr[-1000:]}",
                    )
                cases.append(json.loads(completed.stdout))

    all_digests = {
        digest for case in cases for digest in case["result_digests"]
    }
    output = {
        "benchmark": "compute_files_loc_worker_scaling",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "workload": {
            "root": str(Path(args.root).resolve()),
            "include_ext": args.include_ext,
            "max_files": args.max_files,
        },
        "equivalent_across_all_cases": len(all_digests) == 1,
        "unique_result_digests": sorted(all_digests),
        "cases": cases,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
