"""Read the native record the service retained for one real HTTP turn."""
from __future__ import annotations

import json
from pathlib import Path


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
