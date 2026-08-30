"""Shared bootstrap and non-empty-suite gate for public portable tests."""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


PORTABLE_ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = PORTABLE_ROOT.parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

# Redirect mutable process defaults before portable test modules import the
# runtime.  Most store tests also inject ``store_root`` explicitly; this
# session boundary keeps future omissions away from an installed instance.
_PORTABLE_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="metnos-portable-tests-"))
for _name, _relative in {
    "HOME": "home",
    "USERPROFILE": "home",
    "XDG_DATA_HOME": "home/.local/share",
    "XDG_STATE_HOME": "home/.local/state",
    "XDG_CONFIG_HOME": "home/.config",
    "METNOS_USER_DATA": "home/.local/share/metnos",
    "METNOS_USER_STATE": "home/.local/state/metnos",
    "METNOS_USER_CONFIG": "home/.config/metnos",
}.items():
    os.environ[_name] = str(_PORTABLE_SESSION_ROOT / _relative)


@atexit.register
def _remove_portable_session_root() -> None:
    shutil.rmtree(_PORTABLE_SESSION_ROOT, ignore_errors=True)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Do not let an empty public certification suite appear successful."""
    portable_items = (
        item
        for item in session.items
        if Path(str(item.path)).resolve().is_relative_to(PORTABLE_ROOT)
    )
    if next(portable_items, None) is None:
        raise pytest.UsageError(
            "tests/portable contains no real tests; M4 certification cannot run"
        )
