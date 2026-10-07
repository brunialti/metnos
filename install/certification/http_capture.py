"""Capture one real F5 HTTP turn and its independently retained native record.

This adapter does not answer approvals, retry a turn, or certify a case. The
focused scenario supplies native postconditions to the existing external oracle.
Keep its output private: test queries and executor results are retained exactly.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from install.certification.oracle import CertificationError
from install.certification.focused import frozen_requests, focused_observation


def load_turn_record(user_data: Path, turn_id: str) -> dict | None:
    turns = user_data / "turns"
    if not turns.is_dir() or not turn_id:
        return None
    for path in sorted(turns.glob("*.jsonl"), reverse=True):
        for raw in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if record.get("turn_id") == turn_id:
                return record
    return None

def _save(path: Path, value: dict) -> None:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(data)




async def capture_turn(client, *, request: str, locale: str, deadline_s: int,
                       user_data: Path, output: Path, seen_turns: set[str]) -> dict:
    """Send exactly one turn; reject missing, stale, reused or unbound evidence.

    ``seen_turns`` belongs to the entire run, including both focused cycles.
    A failed capture stays on disk and cannot be replaced by a successful retry.
    An approval response is retained as-is for the caller to stop on; it is never
    answered here. HTTP usage may include calls made after the native turn ended.
    """
    if not request or locale not in {"it", "en"} or type(deadline_s) is not int or deadline_s < 1:
        raise CertificationError("invalid_http_capture_request")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    requested = {"request": request, "locale": locale, "deadline_s": deadline_s}
    _save(output / "request.json", requested)
    try:
        started = time.time()
        response = await client.chat(request, lang=locale, timeout_s=deadline_s)
        finished = time.time()
        _save(output / "http.json", {"raw": response.raw, "error": response.error,
                                    "started_at": started, "finished_at": finished})
        raw = response.raw
        if response.error or not isinstance(raw, dict):
            raise CertificationError("http_turn_transport_failed")
        turn_id = raw.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id or turn_id != response.turn_id:
            raise CertificationError("http_turn_identity_missing")
        if turn_id in seen_turns:
            raise CertificationError("http_turn_identity_reused")
        seen_turns.add(turn_id)
        record = load_turn_record(user_data, turn_id)
        _save(output / "native.json", {"record": record})
        if not isinstance(record, dict):
            raise CertificationError("native_turn_missing")
        if record.get("turn_id") != turn_id or record.get("user_query") != request:
            raise CertificationError("native_turn_request_mismatch")
        times = [record.get("ts_start"), record.get("ts_end")]
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in times)
                or not started <= times[0] <= times[1] <= finished):
            raise CertificationError("native_turn_time_mismatch")
        usage = [raw.get("model_calls"), record.get("model_calls")]
        if any(type(value) is not int or value < 0 for value in usage):
            raise CertificationError("actual_model_usage_missing")
        if usage[0] < usage[1]:
            raise CertificationError("http_model_usage_less_than_native")
        captured = {**requested, "http": raw, "native": record}
        _save(output / "capture.json", captured)
        return captured
    except BaseException as error:
        _save(output / "failure.json", {"error_type": type(error).__name__, "detail": str(error)})
        raise


