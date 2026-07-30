"""Resolve the skill home directory for standalone google-workspace scripts.

These scripts may run outside the Metnos process (system Python, nix env,
CI). The skill home is where the OAuth token + client secret live; the
Metnos runtime injects ``METNOS_SKILL_HOME`` pointing at
``<user-data>/skills/google-workspace``. All scripts under
``google-workspace/scripts/`` import from here instead of duplicating the
``Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_skill_home() -> Path:
    """Return the skill home directory.

    Source of truth = ``METNOS_SKILL_HOME`` (injected by the runtime).
    Fallback (standalone use) = ``~/.local/share/metnos/skills/google-workspace``.
    """
    val = os.environ.get("METNOS_SKILL_HOME", "").strip()
    if val:
        return Path(val)
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "metnos" / "skills" / "google-workspace"


def display_skill_home() -> str:
    """Return a user-friendly ``~/``-shortened display string."""
    home = get_skill_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)
