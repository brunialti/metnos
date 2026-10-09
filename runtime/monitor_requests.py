# SPDX-License-Identifier: MIT
"""Vista di una richiesta, ricavata dai segmenti e dalle chiamate osservati."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from monitor_metrics import request_metrics


def process_alive(epoch: dict[str, Any] | None) -> bool | None:
    """PID e avvio devono coincidere: un PID riusato non è il vecchio processo."""
    if not epoch:
        return None
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        stat = Path(f"/proc/{epoch['pid']}/stat").read_text()
        # Il nome del processo fra parentesi può contenere spazi o parentesi.
        ticks = int(stat[stat.rfind(")") + 2:].split()[19])
        return boot == epoch["boot_id"] and ticks == epoch["start_ticks"]
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, IndexError):
        return None


def derive_request(
    request_id: str, segments: list[dict[str, Any]], calls: list[dict[str, Any]], *,
    chain_complete: bool, now: datetime | None = None,
    alive: Callable[[dict[str, Any] | None], bool | None] = process_alive,
) -> dict[str, Any]:
    ordered = sorted(segments, key=lambda row: (row["started_at"] or "", row["segment_id"]))
    first, last = ordered[0], ordered[-1]
    opened = [row for row in ordered if row["finished_at"] is None]
    status = "completed"
    if opened:
        observations = [alive(row["process_epoch"]) for row in opened]
        status = "interrupted" if False in observations else (
            "in_progress" if all(observations) else "unknown")
    elif last["outcome"] == "awaiting_input":
        status = "awaiting_input"
        expires = last.get("wait_expires_at")
        instant = now or datetime.now(timezone.utc)
        if expires and instant >= datetime.fromisoformat(expires.replace("Z", "+00:00")):
            status = "expired"
    elif last["outcome"] in {"expired", "abandoned"}:
        status = "expired"
    final = status == "completed"
    wall = active = None
    timestamps_complete = chain_complete and all(
        row["started_at"] and row["finished_at"] for row in ordered)
    if timestamps_complete and final and not any(
        row.get("metadata_reasons", {}).get("finished_at") == "clock_rollback" for row in ordered
    ):
        start = datetime.fromisoformat(first["started_at"].replace("Z", "+00:00"))
        end = max(datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
                  for row in ordered)
        wall = (end - start).total_seconds() * 1000 if end >= start else None
    if chain_complete and not opened and all(row["active_ms"] is not None for row in ordered):
        active = sum(row["active_ms"] for row in ordered)
    wait = 0.0 if chain_complete else None
    for position, row in enumerate(ordered):
        if row["outcome"] != "awaiting_input":
            continue
        if (position + 1 >= len(ordered) or not row["finished_at"] or
                not ordered[position + 1]["started_at"] or wait is None):
            wait = None
            break
        before = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))
        after = datetime.fromisoformat(ordered[position + 1]["started_at"].replace("Z", "+00:00"))
        if after < before or row.get("metadata_reasons", {}).get("finished_at") == "clock_rollback":
            wait = None
            break
        wait += (after - before).total_seconds() * 1000
    expected_known = all(row["calls_started"] is not None for row in ordered)
    declared = sum(row["calls_started"] or 0 for row in ordered)
    dropped = sum(row["dropped_calls"] or 0 for row in ordered)
    expected = max(declared, len(calls) + dropped)
    complete = chain_complete and expected_known and declared == len(calls) and not dropped and all(
        row["calls_closed"] == row["calls_started"] for row in ordered)
    complete = complete and not any(row["dropped_events"] for row in ordered)
    metrics = request_metrics(calls, e2e_wall_ms=wall, e2e_active_ms=active,
                              expected_calls=expected, final=final and complete)
    for phase in ("prefill", "generation"):
        metrics[phase]["total_calls"] = expected
        metrics[phase]["total_calls_known"] = expected_known and chain_complete
    for total in metrics["tokens"].values():
        total["complete"] = total["complete"] and complete
        total["total_calls_known"] = expected_known and chain_complete
    finished = max((row["finished_at"] or "" for row in ordered), default="") or None
    return {"request_id": request_id, "owner_id": first["owner_id"],
            "channel": first["channel"], "origin": first["origin"],
            "started_at": first["started_at"],
            "finished_at": finished if final else None,
            "status": status, "outcome": last["outcome"],
            "e2e_wall_ms": wall, "e2e_active_ms": active,
            "user_wait_ms": wait,
            "coverage": {"chain_complete": chain_complete,
                         "calls_complete": complete, "dropped_calls": dropped,
                         "dropped_events": sum(row["dropped_events"] or 0 for row in ordered),
                         "calls_observed": len(calls),
                         "calls_expected": expected if expected_known else None},
            "models": sorted({(row["provider"], row["level"], row["physical_model_key"])
                              for row in calls}, key=lambda pair: str(pair)),
            "metrics": metrics}
