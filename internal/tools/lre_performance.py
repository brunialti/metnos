"""Bounded, read-only LRE timing report; no prompts, paths or results emitted.

Model latency is summed call time, not device compute time. It may overlap
inside an attempt and must never be subtracted blindly from wall time.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import time


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps require an explicit timezone")
    return result.astimezone(timezone.utc)


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row["state"] == "succeeded" and row["ended_at"]]
    durations = []
    intervals = []
    calls: dict[str, dict] = {}
    incomplete = 0
    for row in completed:
        start, end = instant(row["started_at"]), instant(row["ended_at"])
        duration = (end - start).total_seconds()
        if duration < 0:
            raise ValueError("negative attempt duration")
        durations.append(duration)
        intervals.append((start, end))
        usage = json.loads(row["metrics_json"]).get("llm_usage", {})
        mode = row["model_mode"]
        if (mode not in {"none", "llm"}
                or (mode == "none" and usage.get("records"))
                or (mode == "llm" and (
                usage.get("schema_version") != "metnos.durable-model-usage/2"
                or usage.get("usage_missing") is not False
                or usage.get("cost_unknown") is not False
                or usage.get("dropped") != 0))):
            incomplete += 1
        for record in usage.get("records", []):
            kind = record["kind"]
            item = calls.setdefault(kind, {"calls": 0, "latency_ms": 0,
                                          "output_tokens": 0, "missing_counters": 0})
            item["calls"] += 1
            for source, target in (("latency_ms", "latency_ms"), ("out_tokens", "output_tokens")):
                value = record.get(source)
                if value is None:
                    item["missing_counters"] += 1
                elif type(value) is not int or value < 0:
                    raise ValueError("invalid model counter")
                else:
                    item[target] += value
    # Sweep completed attempt intervals: touching endpoints do not overlap.
    events = sorted((point, delta) for start, end in intervals if end > start
                    for point, delta in ((start, 1), (end, -1)))
    active = peak = 0
    occupied = 0.0
    previous = None
    for point, delta in events:
        if previous is not None and active:
            occupied += (point - previous).total_seconds()
        active += delta
        peak = max(peak, active)
        previous = point
    span = ((max(end for _, end in intervals) - min(start for start, _ in intervals)).total_seconds()
            if intervals else None)
    return {
        "attempt_states": dict(Counter(row["state"] for row in rows)),
        "successful_attempts": len(completed),
        "mean_attempt_seconds": statistics.mean(durations) if durations else None,
        "median_attempt_seconds": statistics.median(durations) if durations else None,
        "sum_attempt_seconds": sum(durations),
        "completed_attempt_span_seconds": span,
        "completed_attempt_occupied_seconds": occupied,
        "peak_completed_attempt_overlap": peak,
        "successful_attempts_per_minute": 60 * len(completed) / span if span else None,
        "attempts_with_incomplete_model_usage": incomplete,
        "model_calls": calls,
        "comparison_limit": "Different inputs and overlapping model calls are not controlled speedup measurements.",
    }


def read_report(database: Path, *, workload: str, stage: str, since: str,
                until: str, max_attempts: int = 1000, timeout: float = 5) -> dict:
    start, end = instant(since), instant(until)
    if start >= end or not 1 <= max_attempts <= 10000 or not 0 < timeout <= 30:
        raise ValueError("invalid observation bounds")
    deadline = time.monotonic() + timeout
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True,
                         timeout=min(timeout, 3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        # Julian-day comparisons round fractions. Widen the SQL window, then
        # compare exact instants in Python; never silently drop boundary rows.
        cursor = db.execute("""
            SELECT a.state,a.started_at,a.ended_at,a.metrics_json,
                   json_extract(a.model_snapshot_json,'$.mode') AS model_mode
            FROM workloads w JOIN units u
              ON u.owner_user_id=w.owner_user_id AND u.revision_id=w.active_revision_id
            JOIN stages s ON s.owner_user_id=u.owner_user_id AND s.id=u.stage_id
            JOIN attempts a ON a.owner_user_id=u.owner_user_id AND a.unit_id=u.id
            WHERE w.id=? AND s.stage_key=?
              AND julianday(a.started_at)>=julianday(?)-1.0/86400
              AND julianday(a.started_at)<=julianday(?)+1.0/86400
            ORDER BY a.started_at LIMIT ?
        """, (workload, stage, start.isoformat(), end.isoformat(), max_attempts + 1))
        records = []
        size = 0
        for row in cursor:
            size += len(row["metrics_json"].encode())
            if size > 32 * 1024 * 1024 or time.monotonic() > deadline:
                raise ValueError("observation budget reached; choose a smaller time window")
            records.append(row)
    if len(records) > max_attempts:
        raise ValueError("attempt limit reached; choose a smaller time window")
    rows = [dict(row) for row in records if start <= instant(row["started_at"]) < end]
    return {"since": start.isoformat(), "until": end.isoformat(),
            "window_selects": "attempt start; completion may be outside the window",
            **summarize(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    args = parser.parse_args()
    print(json.dumps(read_report(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
