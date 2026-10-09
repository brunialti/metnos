#!/usr/bin/env python3
"""Differenziale offline: provider reale, risposta sintetica, solo file temporanei.

Non chiama modelli o servizi. I risultati descrivono questo processo e il
carico simulato; la verifica sul servizio pubblicato resta distinta.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import resource
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "runtime"), str(ROOT)]

import llm_provider
import llm_telemetry
import monitor_capture as capture
from monitor_store import MonitorIndex
from monitor_views import build_view


def stats(samples):
    ordered = sorted(samples)
    return {"count": len(ordered), "mean_us": sum(ordered) / len(ordered),
            "median_us": ordered[len(ordered) // 2],
            "p95_us": ordered[math.ceil(len(ordered) * .95) - 1], "max_us": ordered[-1]}


def measure_hooks(samples, directory):
    # Nessun writer: entrambe le code sono limitate; si misurano separatamente
    # consegna riuscita e scarto a coda piena, senza costi di preparazione eventi.
    collector = capture.Collector(directory, capacity=samples + 51)
    event = {"schema_version": capture.SCHEMA_VERSION, "event": "segment_finished",
             "segment_id": "synthetic", "calls": [{"call_id": "synthetic-call",
             "provider": "openai", "_wall_ms": 5,
             "_provider_data": {"usage": {"prompt_tokens": 17, "completion_tokens": 9}}}]}
    for _ in range(50):
        collector.emit(event)
    times = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        collector.emit(event)
        times.append((time.perf_counter_ns() - start) / 1000)
    accepted = stats(times)
    collector.emit(event)
    times = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        collector.emit(event)
        times.append((time.perf_counter_ns() - start) / 1000)
    full_queue = stats(times)
    hooks = {}
    for enabled in (False, True):
        with capture.transport_capture(enabled=enabled):
            def invocation():
                with capture.call_scope(provider="openai", level="wise", model="synthetic"):
                    capture.observe_response({"usage": {"prompt_tokens": 17, "completion_tokens": 9}})
            for _ in range(50):
                invocation()
            times = []
            for _ in range(samples):
                start = time.perf_counter_ns()
                invocation()
                times.append((time.perf_counter_ns() - start) / 1000)
            hooks["enabled" if enabled else "disabled"] = stats(times)
    return {"enqueue": accepted, "full_queue": full_queue, "lost": collector.dropped,
            "call_capture": hooks}


def run_case(mode, workers, samples, delay, directory):
    release_writer = threading.Event()
    writer_entered = threading.Event()
    stop_reader = threading.Event()
    reads, read_errors = [], []
    reader = None
    collector = None
    if mode != "off":
        writer = None
        if mode in {"blocked", "full"}:
            def writer(_path, _events):
                writer_entered.set()
                release_writer.wait()
        collector = capture.Collector(directory, capacity=16 if mode == "full" else 128,
                                      writer=writer).start()
        if mode in {"blocked", "full"}:
            assert writer_entered.wait(2), "synthetic writer not ready"
        if mode == "full":
            while not collector.queue.full():
                collector.emit({"schema_version": capture.SCHEMA_VERSION,
                                "event": "process_started", "started_at": capture.utc_now()})
    capture._collector = collector
    provider = llm_provider.OpenAIProvider(model="synthetic-model", api_key="synthetic-only")
    response = json.dumps({"model": "synthetic-physical", "usage": {
        "prompt_tokens": 17, "completion_tokens": 9},
        "choices": [{"message": {"content": "same answer"}}]}).encode()

    def wire(_request, *, timeout):
        time.sleep(delay)
        return io.BytesIO(response)

    def query(_number):
        begin = time.perf_counter_ns()
        with capture.segment_scope(owner_id="synthetic", channel="benchmark"), \
                llm_telemetry.tier_context("wise"), llm_telemetry.count_model_calls():
            llm_telemetry.mark_call_started()
            result = provider.chat("synthetic system", "synthetic query", think=False)
            assert (result.text, result.in_tokens, result.out_tokens) == ("same answer", 17, 9)
            assert llm_telemetry.current_model_calls() == 1
        # Il risultato è disponibile solo dopo la chiusura del segmento:
        # questa misura include quindi tutta la consegna al Monitor.
        return (time.perf_counter_ns() - begin) / 1000

    def monitor_open():
        # Stessa lettura dell'API; aggiornamento ogni cinque secondi.
        # Il primo giro cade durante le chiamate, a raccolta già iniziata.
        if stop_reader.wait(.25):
            return
        try:
            now = datetime.now(timezone.utc)
            period = {"from": (now - timedelta(hours=1)).isoformat(),
                      "to": (now + timedelta(hours=1)).isoformat()}
            with MonitorIndex(directory) as index:
                while not stop_reader.is_set():
                    begin = time.perf_counter()
                    index.ingest(directory.glob("events-*.jsonl"))
                    source = index.aggregate_source(period)
                    build_view(source["requests"], source["calls"],
                               from_at=source["filters"]["from"], to_at=source["filters"]["to"])
                    reads.append(time.perf_counter() - begin)
                    if stop_reader.wait(5):
                        break
        except Exception as exc:
            read_errors.append(type(exc).__name__ + ": " + str(exc))

    try:
        with patch.object(llm_provider, "_api_urlopen", wire), \
                patch.object(llm_provider._telemetry, "record", lambda **_event: None):
            for _ in range(20):
                query(0)
            cpu = time.process_time()
            start = time.perf_counter()
            if mode == "open":
                reader = threading.Thread(target=monitor_open, name="monitor-offline-read", daemon=True)
                reader.start()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                elapsed = list(pool.map(query, range(samples)))
            result = {"mode": mode, "workers": workers, **stats(elapsed),
                      "wall_seconds": time.perf_counter() - start,
                      "cpu_seconds": time.process_time() - cpu,
                      "queue_events": collector.queue.qsize() if collector else None,
                      "dropped_events": collector.dropped if collector else None}
    finally:
        stop_reader.set()
        if reader:
            reader.join(5)
            assert not reader.is_alive(), "temporary Monitor reader did not stop"
        capture._collector = None
        release_writer.set()
        if collector:
            assert collector.close(), "temporary collector did not stop"
    result.update(monitor_reads=len(reads), monitor_read_seconds=reads, monitor_read_errors=read_errors)
    assert not read_errors, read_errors
    if mode == "open":
        assert reads, "temporary Monitor did not read during queries"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--simulated-ms", type=float, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1000 or not 0 < args.simulated_ms <= 100:
        parser.error("at least 1000 samples and 0 < simulated-ms <= 100 required")
    runs = []
    with tempfile.TemporaryDirectory(prefix="monitor-benchmark-") as temporary:
        directory = Path(temporary)
        hooks = measure_hooks(args.samples, directory / "hooks")
        for workers in (1, 4):
            # Due baseline ai lati dei casi on mostrano la variabilità temporale.
            for number, mode in enumerate(("off", "normal", "open", "blocked", "full", "off")):
                result = run_case(mode, workers, args.samples, args.simulated_ms / 1000,
                                  directory / f"{workers}-{number}")
                runs.append(result)
                print(json.dumps(result), flush=True)
    result = {"scope": "offline synthetic provider; no production acceptance",
              "simulated_ms": args.simulated_ms, "hooks": hooks, "runs": runs,
              "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"hooks": hooks, "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    main()
