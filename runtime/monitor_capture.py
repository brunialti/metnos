# SPDX-License-Identifier: MIT
"""Raccolta Monitor: contesti limitati, nessun I/O nel percorso della query.

Il servizio avvia Collector prima di accettare richieste. Solo il suo thread
normalizza e scrive. Gli hook non modificano risultati o contatori durevoli.
"""
from __future__ import annotations

import atexit
import functools
import json
import logging
import math
import os
import queue
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from monitor_events import MAX_CALLS, SCHEMA_VERSION

log = logging.getLogger(__name__)
TRANSPORT_KEY = "_metnos_monitor_usage_v1"
TRANSPORT_HEADER = "X-Metnos-Monitor"
UPSTREAM_HEADER = "X-Metnos-Monitor-Upstream"
UPSTREAM_ID_ENV = "METNOS_MONITOR_SEGMENT_ID"
UPSTREAM_STARTED_ENV = "METNOS_MONITOR_SEGMENT_STARTED_AT"
TRANSPORT_SCHEMA = "metnos.monitor-transport/1"
MAX_TRANSPORT_JSON_BYTES = 2 * 1024 * 1024
MAX_BUFFERED_CALLS = 8192
_segment = ContextVar("monitor_segment", default=None)
_call = ContextVar("monitor_call", default=None)
_transport = ContextVar("monitor_transport", default=None)
_collector = None


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def process_epoch():
    try:
        stat = Path(f"/proc/{os.getpid()}/stat").read_text()
        return {"pid": os.getpid(), "start_ticks": int(stat[stat.rfind(")") + 2:].split()[19]),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, ValueError, IndexError):
        return None


def _persistable(event):
    # Contatori grezzi copiati dai provider; normalizzazione solo nel thread.
    from monitor_metrics import normalize_provider_metrics
    result = dict(event)
    result["calls"] = []
    for original in event.get("calls", ()):
        call = dict(original)
        response = call.pop("_provider_data", None)
        if "metrics" not in call or response is not None or "_wall_ms" in call:
            call["metrics"] = normalize_provider_metrics(
                call.get("provider") or "unknown", response,
                call_wall_ms=call.pop("_wall_ms", None))
        result["calls"].append(call)
    return result


class Collector:
    """Una coda per processo; ritardi/errori del disco non bloccano il chiamante."""

    def __init__(self, directory=None, *, capacity=128, interval=0.25, writer=None):
        from monitor_store import append_normalized_events, monitor_directory
        self.directory = Path(directory) if directory is not None else monitor_directory()
        self.queue = queue.Queue(maxsize=capacity)
        self.interval = interval
        self.writer = writer or append_normalized_events
        self.epoch = process_epoch()
        self.counter_lock = threading.Lock()
        self.dropped = self.write_errors = self.persisted = 0
        self.buffered_calls = 0
        self.latest_event_at = self.persisted_at = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, name="monitor-writer", daemon=True)

    def start(self):
        self.thread.start()
        self.emit({"schema_version": SCHEMA_VERSION, "event": "process_started",
                   "process_epoch": self.epoch, "started_at": utc_now()})
        return self

    def emit(self, event):
        if self.stopping.is_set():
            self._lost(1)
            return False
        event = {**event, "process_epoch": event.get("process_epoch") or self.epoch,
                 "recorded_at": utc_now()}
        calls = len(event.get("calls", ()))
        if calls:
            with self.counter_lock:
                if self.buffered_calls + calls > MAX_BUFFERED_CALLS:
                    self.dropped += 1
                    return False
                self.buffered_calls += calls
        try:
            self.queue.put_nowait(event)
            return True
        except queue.Full:
            with self.counter_lock:
                self.buffered_calls -= calls
                self.dropped += 1
            return False

    def _lost(self, count):
        with self.counter_lock:
            self.dropped += count

    def _health(self):
        payload = {"schema_version": SCHEMA_VERSION, "process_epoch": self.epoch,
                   "observed_at": utc_now(), "persisted_at": self.persisted_at,
                   "latest_event_at": self.latest_event_at, "persisted_events": self.persisted,
                   "dropped_events": self.dropped, "write_errors": self.write_errors,
                   "queued_events": self.queue.qsize(), "stopped": self.stopping.is_set()}
        payload["buffered_calls"] = self.buffered_calls
        filename = f"health-{os.getpid()}-{(self.epoch or {}).get('start_ticks', 0)}.json"
        temporary = self.directory / (filename + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(payload, output, allow_nan=False)
        temporary.replace(self.directory / filename)

    def _run(self):
        from monitor_events import normalize_event
        last_health = 0.0
        while not self.stopping.is_set() or not self.queue.empty():
            batch = []
            try:
                batch.append(self.queue.get(timeout=self.interval))
            except queue.Empty:
                pass
            deadline = time.monotonic() + self.interval
            while batch and len(batch) < 32 and not self.stopping.is_set():
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    batch.append(self.queue.get(timeout=remaining))
                except queue.Empty:
                    break
            unwritten = len(batch)
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                valid = []
                for position, event in enumerate(batch):
                    try:
                        valid.append(normalize_event(_persistable(event)))
                    except (ValueError, TypeError, OverflowError):
                        self._lost(1)
                        unwritten -= 1
                    if position % 8 == 7:
                        # Cede il GIL fra piccoli gruppi; mai nel chiamante.
                        time.sleep(0)
                if valid:
                    self.writer(self.directory / f"events-{os.getpid()}.jsonl", valid)
                    unwritten = 0
                    self.persisted += len(valid)
                    self.persisted_at = utc_now()
                    self.latest_event_at = max(row["recorded_at"] for row in valid)
                if self.stopping.is_set() or time.monotonic() - last_health >= 2:
                    self._health()
                    last_health = time.monotonic()
            except Exception:
                self.write_errors += 1
                self._lost(unwritten)
                if self.write_errors == 1 or self.write_errors % 100 == 0:
                    log.exception("Monitor writer failed; query continues")
            finally:
                calls = sum(len(event.get("calls", ())) for event in batch)
                if calls:
                    with self.counter_lock:
                        self.buffered_calls -= calls
                for _ in batch:
                    self.queue.task_done()

    def close(self, timeout=1.0):
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)
        return not self.thread.is_alive()


def start_collector(directory=None):
    global _collector
    if _collector is None:
        _collector = Collector(directory).start()
        atexit.register(stop_collector)
    return _collector


def stop_collector():
    global _collector
    collector, _collector = _collector, None
    if collector:
        collector.close()


class Segment:
    def __init__(self, *, owner_id=None, channel=None, origin="interactive", job_ref=None):
        self.collector = _collector
        self.metadata = {"schema_version": SCHEMA_VERSION, "segment_id": uuid.uuid4().hex,
                         "owner_id": owner_id, "channel": channel or None,
                         "origin": origin, "job_ref": job_ref, "started_at": utc_now()}
        self.start_ns = time.perf_counter_ns()
        self.lock = threading.Lock()
        self.calls = []
        self.started = self.closed = self.dropped_calls = self.dropped_events = self.pending = 0
        self.unknown_children = False
        self.finished = False
        self.deferred = False
        self.outcome = "completed"
        self.wait_expires_at = None
        self._emit("segment_started")

    def _emit(self, event, **fields):
        if self.collector and not self.collector.emit({**self.metadata, "event": event, **fields}):
            self.dropped_events += 1

    def bind(self, *, turn_id=None, parent_turn_id=None, owner_id=None, channel=None):
        with self.lock:
            for name, value in (("turn_id", turn_id), ("parent_turn_id", parent_turn_id),
                                ("owner_id", owner_id), ("channel", channel)):
                if value and not self.metadata.get(name):
                    self.metadata[name] = value
            self._emit("segment_started")

    def add(self, call):
        with self.lock:
            self.closed += 1
            if self.finished:
                self._emit("call_late", calls=[call],
                           calls_started=None if self.pending or self.unknown_children else self.started,
                           calls_closed=self.closed, dropped_calls=self.dropped_calls)
            elif len(self.calls) < MAX_CALLS:
                self.calls.append(call)
            else:
                self.dropped_calls += 1

    def finish(self, outcome=None, *, wait_expires_at=None):
        with self.lock:
            if self.finished:
                return
            self.finished = True
            self._emit("segment_finished", finished_at=utc_now(),
                       active_ms=(time.perf_counter_ns() - self.start_ns) / 1_000_000,
                       outcome=outcome or self.outcome,
                       wait_expires_at=wait_expires_at or self.wait_expires_at,
                       calls=self.calls,
                       calls_started=None if self.pending or self.unknown_children else self.started,
                       calls_closed=self.closed, dropped_calls=self.dropped_calls,
                       dropped_events=self.dropped_events)
            self.calls = []


@contextmanager
def segment_scope(*, independent=False, **metadata):
    existing = None if independent else _segment.get()
    segment = existing or (Segment(**metadata) if _collector else None)
    token = _segment.set(segment)
    try:
        yield segment
    except BaseException:
        if segment and not existing:
            segment.finish("error")
        raise
    finally:
        if segment and not existing and not segment.deferred:
            segment.finish()
        _segment.reset(token)


def bind_turn(log):
    segment = _segment.get()
    if segment:
        segment.bind(turn_id=getattr(log, "turn_id", None),
                     parent_turn_id=getattr(log, "parent_turn_id", None),
                     owner_id=getattr(log, "owner_user_id", None),
                     channel=getattr(log, "channel", None))


def finish_turn(log):
    segment = _segment.get()
    if not segment:
        return
    bind_turn(log)
    outcome = "awaiting_input" if getattr(log, "final_kind", "") in {"ask", "get_inputs"} else (
        "error" if getattr(log, "final_kind", "") == "error" else "completed")
    segment.outcome = outcome


def observe_wait(payload):
    segment = _segment.get() or _transport.get()
    if not segment or not isinstance(payload, dict):
        return
    # Stessa scadenza attestata del dialogo: nessun timeout Monitor aggiunto.
    from dialog_pending import _started_ts, DEFAULT_TTL_S
    started = _started_ts(payload)
    if started:
        try:
            expires = started + int(payload.get("timeout_s") or DEFAULT_TTL_S)
            segment.wait_expires_at = datetime.fromtimestamp(expires, timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError, OverflowError, OSError):
            pass


def request_capture(function):
    @functools.wraps(function)
    async def wrapped(request):
        with segment_scope(owner_id=request.get("authenticated_user_id"), channel="http") as segment:
            response = await function(request)
            if segment and response.status >= 400:
                segment.outcome = "error"
            return response
    return wrapped


def defer_current():
    segment = _segment.get()
    if segment:
        segment.deferred = True


def finish_current():
    segment = _segment.get()
    if segment:
        segment.finish()


def set_outcome(value):
    segment = _segment.get()
    if segment:
        segment.outcome = value


@contextmanager
def call_scope(*, provider, model=None, level=None, kind="chat", binding_revision=None):
    segment, transport = _segment.get(), _transport.get()
    if segment is None and transport is None and _collector is None:
        yield None
        return
    parent = _call.get()
    record = {"call_id": uuid.uuid4().hex, "parent_call_id": parent.get("call_id") if parent else None,
              "provider": provider, "physical_model_requested": model,
              "level": level, "kind": kind, "binding_revision": binding_revision,
              "origin_process": getattr(_collector, "origin_process", None),
              "started_at": utc_now(), "status": "ok"}
    start = time.perf_counter_ns()
    token = _call.set(record)
    target = transport or segment
    if target:
        with target.lock:
            target.started += 1
    try:
        yield record
    except BaseException as exc:
        record["status"] = "timeout" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else (
            "cancelled" if type(exc).__name__ == "CancelledError" else "error")
        record["error_code"] = record["status"]
        raise
    finally:
        record["_wall_ms"] = (time.perf_counter_ns() - start) / 1_000_000
        record["finished_at"] = utc_now()
        _call.reset(token)
        if target:
            target.add(record)
        elif _collector:
            _collector.emit({"schema_version": SCHEMA_VERSION, "event": "unattributed",
                             "segment_id": "unattributed:" + record["call_id"],
                             "started_at": record["started_at"], "finished_at": record["finished_at"],
                             "calls": [record], "calls_started": 1, "calls_closed": 1})


def observe_response(data):
    """Copia solo campi numerici e nome attestato; mai risposte o prompt."""
    record = _call.get()
    if record is None or not isinstance(data, dict):
        return
    usage = data.get("usage")
    selected = {}
    def numeric(value):
        return (type(value) is int and 0 <= value <= 2**63 - 1) or (
            type(value) is float and math.isfinite(value) and value >= 0)
    if isinstance(usage, dict):
        for name in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens",
                     "cache_creation_input_tokens", "cache_read_input_tokens"):
            if numeric(usage.get(name)):
                selected[name] = usage[name]
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict) and numeric(details.get("cached_tokens")):
            selected["prompt_tokens_details"] = {"cached_tokens": details.get("cached_tokens")}
    timings = data.get("timings")
    record["_provider_data"] = {"usage": selected, "timings": {
        name: timings[name] for name in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n")
        if numeric(timings.get(name))
    } if isinstance(timings, dict) else {}}
    if isinstance(data.get("model"), str):
        record["physical_model_reported"] = data["model"][:256]


def provider_call(provider):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(self, *args, **kwargs):
            from llm_telemetry import current_tier
            tools = (len(args) > 1 and args[1] is True) or (
                args and isinstance(args[0], dict) and bool(args[0].get("tools")))
            with call_scope(provider=provider, model=self.model, level=current_tier(),
                            kind="tools" if tools else "chat",
                            binding_revision=getattr(self, "_monitor_binding_revision", None)):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate


def upstream_metadata(value):
    """Validate the two content-free fields allowed across process boundaries."""
    from monitor_events import identifier, timestamp
    if not isinstance(value, dict) or set(value) != {"segment_id", "started_at"}:
        return None
    try:
        return {"segment_id": identifier(value["segment_id"], required=True),
                "started_at": timestamp(value["started_at"], required=True)}
    except ValueError:
        return None


class TransportBuffer:
    def __init__(self, upstream=None):
        self.started = self.closed = self.dropped_calls = 0
        self.pending = 0
        self.unknown_children = False
        self.calls = []
        self.wait_expires_at = None
        self.lock = threading.Lock()
        self.upstream = upstream_metadata(upstream if upstream is not None else {
            "segment_id": os.environ.get(UPSTREAM_ID_ENV),
            "started_at": os.environ.get(UPSTREAM_STARTED_ENV)})
        self.collector = _collector
        self.delivered = False

    def _late(self, calls):
        if calls and self.upstream and self.collector:
            # Counts belong to the enclosing segment, not to this child.
            self.collector.emit({"schema_version": SCHEMA_VERSION, "event": "call_late",
                                 **self.upstream, "calls": calls})

    def add(self, call):
        with self.lock:
            self.closed += 1
            if self.delivered:
                self._late([call])
            elif len(self.calls) < MAX_CALLS:
                self.calls.append(call)
            else:
                self.dropped_calls += 1

    def export(self):
        with self.lock:
            self.delivered = True
            envelope = {"schema_version": TRANSPORT_SCHEMA,
                    "calls_started": None if self.pending or self.unknown_children else self.started,
                    "calls_closed": self.closed, "dropped_calls": self.dropped_calls,
                    "calls": list(self.calls), "wait_expires_at": self.wait_expires_at}
        # La franchigia di trasporto è distinta dal limite del risultato.
        # Si misura solo qui, alla consegna del sottoprocesso o del browser.
        retained = []
        used = 1024  # Campi scalari, separatori e conteggi dell'involucro.
        for call in envelope["calls"]:
            try:
                size = len(json.dumps(call, allow_nan=False).encode("utf-8")) + 2
            except (TypeError, ValueError, RecursionError):
                size = MAX_TRANSPORT_JSON_BYTES
            if used + size <= MAX_TRANSPORT_JSON_BYTES:
                retained.append(call)
                used += size
            else:
                envelope["dropped_calls"] += 1
        envelope["calls"] = retained
        return envelope

    def cancel_delivery(self):
        """Preserve completed measurements when HTTP delivery is cancelled."""
        with self.lock:
            if self.delivered:
                return
            self.delivered = True
            calls, self.calls = self.calls, []
            self._late(calls)


@contextmanager
def transport_capture(*, enabled=None, upstream=None):
    active = os.environ.get("METNOS_CAPTURE_MONITOR") == "1" if enabled is None else enabled
    sink = TransportBuffer(upstream) if active else None
    token = _transport.set(sink)
    try:
        yield sink
    finally:
        _transport.reset(token)


class ChildCapture:
    def __init__(self, segment):
        self.segment = segment
        self.done = False
        with segment.lock:
            segment.pending += 1

    def finish(self, result):
        if self.done:
            return
        self.done = True
        envelope = result.pop(TRANSPORT_KEY, None) if isinstance(result, dict) else None
        with self.segment.lock:
            self.segment.pending -= 1
            valid = isinstance(envelope, dict) and envelope.get("schema_version") == TRANSPORT_SCHEMA
            if valid:
                started, closed, dropped = (envelope.get(name) for name in (
                    "calls_started", "calls_closed", "dropped_calls"))
                calls = envelope.get("calls")
                valid = all(type(n) is int and 0 <= n <= 10**12 for n in (closed, dropped))
                valid = valid and (started is None or type(started) is int and 0 <= started <= 10**12)
                valid = valid and isinstance(calls, list) and len(calls) <= MAX_CALLS and (
                    (started is None or closed <= started) and len(calls) + dropped == closed and
                    all(isinstance(call, dict) and isinstance(call.get("call_id"), str) for call in calls))
            if not valid:
                self.segment.unknown_children = True
                return
            self.segment.started += started or 0
            self.segment.closed += dropped
            self.segment.dropped_calls += dropped
            if envelope.get("wait_expires_at"):
                from monitor_events import timestamp
                try:
                    self.segment.wait_expires_at = timestamp(envelope["wait_expires_at"])
                except ValueError:
                    pass
            if started is None:
                self.segment.unknown_children = True
        for call in calls:
            self.segment.add(call)


def prepare_child(env):
    segment = _transport.get() or _segment.get()
    if segment is None:
        return None
    env["METNOS_CAPTURE_MONITOR"] = "1"
    upstream = segment.upstream if isinstance(segment, TransportBuffer) else {
        name: segment.metadata[name] for name in ("segment_id", "started_at")}
    if upstream:
        env[UPSTREAM_ID_ENV] = upstream["segment_id"]
        env[UPSTREAM_STARTED_ENV] = upstream["started_at"]
    return ChildCapture(segment)
