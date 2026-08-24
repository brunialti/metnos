"""Privacy-minimal reliability trends derived from canonical turn records.

The analyser never emits queries, answers, arguments, paths, actor identities
or turn identifiers.  It consumes only structural outcome fields already
written by ``TurnLog`` and groups executor work through the canonical naming
grammar, so new domains require no per-executor map.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import config as _C
from naming_grammar import parse_name


SCHEMA_VERSION = "metnos.reliability-snapshot/1"
_MAX_LINE_BYTES = 4 * 1024 * 1024
_AWAITING_KINDS = frozenset({"ask", "needs_inputs", "input_required"})


def _step_results(record: dict) -> list[tuple[str, dict]]:
    out = []
    for step in record.get("steps") or []:
        if not isinstance(step, dict):
            continue
        result = step.get("result")
        if isinstance(result, dict):
            out.append((str(step.get("chosen_tool") or ""), result))
    return out


def _positive_effect(record: dict, results: list[tuple[str, dict]]) -> bool:
    counts = record.get("effect_counts")
    if isinstance(counts, dict):
        for key in ("items", "mutations", "produced", "processed"):
            value = counts.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return True
    return any(result.get("ok") is True for _, result in results)


def _is_timeout(record: dict, results: list[tuple[str, dict]]) -> bool:
    classes = {str(record.get("error_class") or "")}
    classes.update(str(result.get("error_class") or "") for _, result in results)
    return bool(classes & {"timeout", "remote_timeout"})


def _domain(results: list[tuple[str, dict]]) -> str:
    objects = set()
    for tool, _ in results:
        parsed = parse_name(tool)
        if parsed is not None:
            objects.add(parsed.obj)
    if not objects:
        return "conversation"
    if len(objects) == 1:
        return next(iter(objects))
    return "multi-domain"


def classify_turn(record: dict) -> dict[str, object]:
    """Classify one record from structural evidence, without text heuristics."""
    results = _step_results(record)
    failed_steps = [result for _, result in results if result.get("ok") is False]
    explicit_partial = any(result.get("partial") is True for _, result in results)
    positive = _positive_effect(record, results)
    final_kind = str(record.get("final_kind") or "")
    false_success = record.get("false_success_detected") is True

    if final_kind in _AWAITING_KINDS:
        outcome = "awaiting_input"
    elif false_success:
        outcome = "failed"
    elif explicit_partial or (failed_steps and positive):
        outcome = "partial"
    elif final_kind == "answer" and not failed_steps:
        outcome = "completed"
    else:
        outcome = "failed"

    if failed_steps:
        origin = "executor"
    elif outcome == "failed" and not results:
        origin = "planning"
    elif outcome == "failed":
        origin = "runtime"
    else:
        origin = "none"

    start = record.get("ts_start")
    end = record.get("ts_end")
    latency_ms = None
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        latency_ms = round((end - start) * 1000)
    return {
        "outcome": outcome,
        "failure_origin": origin,
        "domain": _domain(results),
        "false_success": false_success,
        "timeout": _is_timeout(record, results),
        "recovery": str(record.get("match_source") or "") == "recovery",
        "version": str(record.get("metnos_version") or "pre-telemetry"),
        "latency_ms": latency_ms,
    }


def read_turn_records(directory: Path, *, since: float | None = None,
                      until: float | None = None) -> tuple[list[dict], int]:
    records: list[dict] = []
    malformed = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            with path.open("rb") as handle:
                for raw in handle:
                    if len(raw) > _MAX_LINE_BYTES:
                        malformed += 1
                        continue
                    try:
                        value = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        malformed += 1
                        continue
                    if not isinstance(value, dict):
                        malformed += 1
                        continue
                    ts = value.get("ts_start")
                    if not isinstance(ts, (int, float)):
                        malformed += 1
                        continue
                    if since is not None and ts < since:
                        continue
                    if until is not None and ts >= until:
                        continue
                    records.append(value)
        except OSError:
            malformed += 1
    return records, malformed


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def build_snapshot(records: Iterable[dict], *, malformed_records: int = 0) -> dict:
    classified = [classify_turn(record) for record in records]
    dimensions: dict[str, Counter] = {
        "outcomes": Counter(), "failure_origins": Counter(),
        "domains": Counter(), "versions": Counter(),
    }
    latency: dict[str, list[int]] = defaultdict(list)
    flags = Counter()
    for item in classified:
        dimensions["outcomes"][str(item["outcome"])] += 1
        dimensions["failure_origins"][str(item["failure_origin"])] += 1
        dimensions["domains"][str(item["domain"])] += 1
        dimensions["versions"][str(item["version"])] += 1
        if isinstance(item["latency_ms"], int):
            latency[str(item["domain"])].append(int(item["latency_ms"]))
        for flag in ("false_success", "timeout", "recovery"):
            if item[flag] is True:
                flags[flag] += 1
    eligible = sum(dimensions["outcomes"][key] for key in ("completed", "partial", "failed"))
    completed = dimensions["outcomes"]["completed"]
    ratio = round(completed / eligible, 6) if eligible else None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "records": {"accepted": len(classified), "malformed": malformed_records},
        "rates": {"eligible": eligible, "completion": ratio},
        "flags": dict(sorted(flags.items())),
        **{name: dict(sorted(values.items())) for name, values in dimensions.items()},
        "latency_p95_ms_by_domain": {
            domain: _p95(values) for domain, values in sorted(latency.items())
        },
        "coverage_gaps": [
            "client-side UI failures are not represented by server turn records",
            "orphaned live sessions require a separate lifecycle source",
            "external dependency causes require an explicit executor error origin",
        ],
    }


def _parse_time(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=Path, default=_C.PATH_TURNS)
    parser.add_argument("--since", type=_parse_time)
    parser.add_argument("--until", type=_parse_time)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    records, malformed = read_turn_records(
        args.turns, since=args.since, until=args.until)
    payload = build_snapshot(records, malformed_records=malformed)
    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temp = args.out.with_name(f".{args.out.name}.{time.time_ns()}.tmp")
        temp.write_text(output, encoding="utf-8")
        temp.replace(args.out)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
