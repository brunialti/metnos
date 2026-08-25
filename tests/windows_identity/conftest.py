"""Opt-in hook for the historical RM-0008 Windows diagnostic snapshot."""
from __future__ import annotations

import os


def pytest_sessionfinish(session, exitstatus: int) -> None:
    """Record D only after a green manually-dispatched identity calibration."""
    if exitstatus != 0 or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return
    from rm0008_increment_2a_windows_diagnostics import main

    result = main()
    if result != 0:
        raise RuntimeError("RM-0008 Windows diagnostics did not complete")
