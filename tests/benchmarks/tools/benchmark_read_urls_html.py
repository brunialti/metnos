#!/usr/bin/env python3
"""Benchmark worker scaling and cross-call pressure for ``read_urls_html``."""
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]
_EXECUTOR_DIR = _ROOT / "executors" / "read_urls_html"
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


def _stable_result(value):
    if isinstance(value, dict):
        return {
            key: _stable_result(item)
            for key, item in value.items()
            if key not in {"fetched_at", "render_ms"}
        }
    if isinstance(value, list):
        return [_stable_result(item) for item in value]
    return value


def _digest(result: dict) -> str:
    payload = json.dumps(
        _stable_result(result), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_group(module, invoke_args: dict, concurrent_calls: int):
    if concurrent_calls == 1:
        started = time.perf_counter_ns()
        result = module.invoke(invoke_args)
        elapsed = (time.perf_counter_ns() - started) / 1_000_000
        return elapsed, [elapsed], [result]

    barrier = threading.Barrier(concurrent_calls + 1)
    results: list[dict | None] = [None] * concurrent_calls
    elapsed_calls = [0.0] * concurrent_calls
    errors: list[BaseException] = []

    def _invoke(index: int) -> None:
        try:
            barrier.wait()
            started = time.perf_counter_ns()
            results[index] = module.invoke(invoke_args)
            elapsed_calls[index] = (
                time.perf_counter_ns() - started
            ) / 1_000_000
        except BaseException as exc:
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


class _ServerMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.active = 0
            self.max_active = 0
            self.requests = 0
            self.active_by_port: dict[int, int] = {}
            self.max_by_port: dict[int, int] = {}

    def begin(self, port: int) -> None:
        with self._lock:
            self.active += 1
            self.requests += 1
            self.max_active = max(self.max_active, self.active)
            active = self.active_by_port.get(port, 0) + 1
            self.active_by_port[port] = active
            self.max_by_port[port] = max(self.max_by_port.get(port, 0), active)

    def end(self, port: int) -> None:
        with self._lock:
            self.active = max(0, self.active - 1)
            self.active_by_port[port] = max(
                0, self.active_by_port.get(port, 0) - 1,
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "requests": self.requests,
                "peak_global_requests": self.max_active,
                "peak_requests_per_host": max(self.max_by_port.values(), default=0),
            }


_METRICS = _ServerMetrics()
_DELAY_S = 0.04


def _make_body(body_kib: int) -> bytes:
    prefix = (
        b"<html lang='it'><head><title>Pagina benchmark</title></head>"
        b"<body><article>"
    )
    suffix = b"</article></body></html>"
    target = max(len(prefix) + len(suffix), body_kib * 1024)
    text_size = target - len(prefix) - len(suffix)
    unit = b"contenuto deterministico metnos "
    payload = (unit * (text_size // len(unit) + 1))[:text_size]
    return prefix + payload + suffix


_BODY = _make_body(6)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args) -> None:
        pass

    def do_GET(self) -> None:
        port = int(self.server.server_address[1])
        _METRICS.begin(port)
        try:
            time.sleep(_DELAY_S)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_BODY)))
            self.end_headers()
            self.wfile.write(_BODY)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _METRICS.end(port)


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def _child(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(_RUNTIME_DIR))
    sys.path.insert(0, str(_EXECUTOR_DIR))
    import read_urls_html  # noqa: E402

    read_urls_html._GLOBAL_MAX = args.worker_count
    read_urls_html._PER_HOST_MAX = args.per_host_limit
    read_urls_html._playwright_client = None
    invoke_args = {
        "urls": json.loads(args.urls_json),
        "cache_ttl_s": 0,
        "follow_iframes": False,
        "js_render": False,
        "timeout_s": 10.0,
    }
    baseline_rss_kib = _rss_kib()
    group_samples: list[float] = []
    call_samples: list[float] = []
    results: list[dict] = []

    with _ProcessSampler() as sampler:
        for _ in range(args.warmups):
            _run_group(read_urls_html, invoke_args, args.concurrent_calls)
        for _ in range(args.iterations):
            group_ms, calls_ms, group_results = _run_group(
                read_urls_html, invoke_args, args.concurrent_calls,
            )
            group_samples.append(group_ms)
            call_samples.extend(calls_ms)
            results.extend(group_results)

    digests = sorted({_digest(result) for result in results})
    ok_count = sum(int(result.get("ok_count", 0)) for result in results)
    fail_count = sum(int(result.get("fail_count", 0)) for result in results)
    print(json.dumps({
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
        "ok_count": ok_count,
        "fail_count": fail_count,
        "error_rate": round(fail_count / max(1, ok_count + fail_count), 8),
    }, sort_keys=True))
    return 0


def _profile_urls(servers: list[_Server], count: int, profile: str) -> list[str]:
    if profile == "same_host":
        ports = [servers[0].server_address[1]] * count
    else:
        ports = [servers[index % len(servers)].server_address[1]
                 for index in range(count)]
    return [
        f"http://127.0.0.1:{port}/page/{index}"
        for index, port in enumerate(ports)
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=_positive_csv,
                        default=(1, 2, 4, 8, 16, 32))
    parser.add_argument("--concurrent-calls", type=_positive_csv, default=(1, 2))
    parser.add_argument("--profiles", default="same_host,multi_host")
    parser.add_argument("--iterations", type=int, default=6)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--url-count", type=int, default=20)
    parser.add_argument("--server-count", type=int, default=5)
    parser.add_argument("--delay-ms", type=float, default=40.0)
    parser.add_argument("--body-kib", type=int, default=6)
    parser.add_argument("--per-host-limit", type=int, default=4)
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker-count", type=int, default=1,
                        help=argparse.SUPPRESS)
    parser.add_argument("--concurrent-call-count", type=int,
                        dest="concurrent_calls", help=argparse.SUPPRESS)
    parser.add_argument("--urls-json", default="[]", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if (args.iterations < 1 or args.warmups < 0 or args.url_count < 1
            or args.server_count < 1 or args.delay_ms < 0
            or args.per_host_limit < 1 or args.body_kib < 1):
        raise SystemExit("invalid benchmark bounds")
    if args._child:
        return _child(args)

    profiles = tuple(item.strip() for item in args.profiles.split(",")
                     if item.strip())
    if not profiles or set(profiles) - {"same_host", "multi_host"}:
        raise SystemExit("profiles must be same_host and/or multi_host")

    global _BODY, _DELAY_S
    _DELAY_S = args.delay_ms / 1000.0
    _BODY = _make_body(args.body_kib)
    servers = [_Server(("127.0.0.1", 0), _Handler)
               for _ in range(args.server_count)]
    server_threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in servers
    ]
    for thread in server_threads:
        thread.start()

    cases = []
    try:
        with tempfile.TemporaryDirectory(
                prefix="metnos-read-html-bench-") as state:
            for profile in profiles:
                urls = _profile_urls(servers, args.url_count, profile)
                for concurrent_calls in args.concurrent_calls:
                    for worker_count in args.workers:
                        _METRICS.reset()
                        command = [
                            sys.executable, str(Path(__file__).resolve()),
                            "--_child",
                            "--iterations", str(args.iterations),
                            "--warmups", str(args.warmups),
                            "--worker-count", str(worker_count),
                            "--concurrent-call-count", str(concurrent_calls),
                            "--per-host-limit", str(args.per_host_limit),
                            "--urls-json", json.dumps(urls),
                        ]
                        env = os.environ.copy()
                        env.update({
                            "METNOS_USER_DATA": state,
                            "METNOS_USER_STATE": state,
                            "METNOS_USER_CONFIG": state,
                            "XDG_CACHE_HOME": state,
                        })
                        completed = subprocess.run(
                            command, capture_output=True, text=True,
                            check=False, env=env,
                        )
                        if completed.returncode:
                            raise RuntimeError(
                                "benchmark child failed: "
                                + completed.stderr[-1500:],
                            )
                        case = json.loads(completed.stdout)
                        case["profile"] = profile
                        case["server_pressure"] = _METRICS.snapshot()
                        cases.append(case)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in server_threads:
            thread.join(timeout=2)

    equivalence = {}
    for profile in profiles:
        digests = {
            digest for case in cases if case["profile"] == profile
            for digest in case["result_digests"]
        }
        equivalence[profile] = {
            "equivalent": len(digests) == 1,
            "unique_result_digests": sorted(digests),
        }
    print(json.dumps({
        "benchmark": "read_urls_html_worker_scaling",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "workload": {
            "url_count": args.url_count,
            "server_count": args.server_count,
            "delay_ms": args.delay_ms,
            "body_kib": args.body_kib,
            "per_host_limit": args.per_host_limit,
        },
        "equivalence": equivalence,
        "cases": cases,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
