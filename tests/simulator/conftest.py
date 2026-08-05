"""Shared bootstrap for opt-in domain and planner simulators."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "runtime"
for path in (str(REPO_ROOT), str(RUNTIME_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
