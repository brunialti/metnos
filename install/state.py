# SPDX-License-Identifier: AGPL-3.0-only
"""Sentinel-based install state.

Each phase writes a JSON sentinel in ``$METNOS_STATE/install/`` when it
completes successfully. Re-running the installer skips phases whose
sentinel exists and is consistent with the current manifest hash.

Design:

- Idempotency by default — re-running is safe and cheap.
- Resume after interruption — pick up at the first phase whose sentinel
  is missing.
- Force a single phase to re-run via ``--force-phase N`` (handled in
  ``__main__``); this deletes the sentinel before invoking the phase.
- Per-phase notes (e.g. which optional components were installed in
  phase 2) are stored inside the sentinel JSON so subsequent phases can
  query the choices without re-prompting.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any


@dataclass
class PhaseRecord:
    phase: int
    name: str
    started_at: float
    finished_at: float | None = None
    success: bool = False
    notes: dict[str, Any] = field(default_factory=dict)


def _state_dir() -> Path:
    """Return ``$METNOS_STATE/install/``, creating it if missing."""
    base = os.environ.get("METNOS_USER_STATE") or str(Path.home() / ".local" / "state" / "metnos")
    d = Path(base) / "install"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sentinel_path(phase: int) -> Path:
    return _state_dir() / f"phase{phase}.done"


def is_done(phase: int) -> bool:
    return sentinel_path(phase).exists()


def load(phase: int) -> PhaseRecord | None:
    p = sentinel_path(phase)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return PhaseRecord(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def start(phase: int, name: str) -> PhaseRecord:
    rec = PhaseRecord(phase=phase, name=name, started_at=time.time())
    return rec


def commit(rec: PhaseRecord, notes: dict[str, Any] | None = None) -> None:
    """Write the sentinel marking the phase as successfully done."""
    rec.finished_at = time.time()
    rec.success = True
    if notes:
        rec.notes.update(notes)
    sentinel_path(rec.phase).write_text(json.dumps(asdict(rec), indent=2))


def clear(phase: int) -> None:
    """Delete a phase sentinel — re-run will redo the phase."""
    p = sentinel_path(phase)
    if p.exists():
        p.unlink()


def clear_all() -> None:
    """Wipe all sentinels (cold start)."""
    for f in _state_dir().glob("phase*.done"):
        f.unlink()


def summary() -> list[dict[str, Any]]:
    """Return list of phase status dicts for the summary banner."""
    out = []
    for phase in range(1, 7):
        rec = load(phase)
        out.append({
            "phase": phase,
            "done": rec is not None and rec.success,
            "name": rec.name if rec else "",
            "finished_at": rec.finished_at if rec else None,
            "notes": rec.notes if rec else {},
        })
    return out
