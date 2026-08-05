"""MetisRecovery non deve iniettare `read_urls_html` in un turno `sites`.

Regressione turn 4769cf88: `read_sites` produce entries con `url` ma senza
`body_text`; `describe_entries` segnala `needs_content_fetch`; il recovery
inseriva `read_urls_html` (GET senza cookie di sessione) e cio' ri-triggerava un
`open_sites` ridondante → `quota_exceeded`. Il content-fetch recovery ora e'
soppresso sui turni con un produttore `sites` (fail-honest). §7.9.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RUNTIME = str((Path(__file__).resolve().parents[3] / "runtime"))

from engine.recovery_metis import MetisRecovery
from engine.types import StepRun, RunResult


class _CatalogEntry:
    def __init__(self, name):
        self.name = name


_CATALOG = [_CatalogEntry("read_urls_html")]


def _step(tool, result, args=None):
    return StepRun(step_idx=0, tool=tool, args=args or {}, result=result,
                   ok=bool(result.get("ok", True)), latency_ms=0)


def _sites_run():
    """open→login→act→read→describe con read_sites che ritorna url senza body."""
    return RunResult(steps=[
        _step("open_sites", {"ok": True,
                             "entries": [{"session_id": "s", "url": "https://www.booking.com"}]}),
        _step("login_sites", {"ok": True, "entries": [{"session_id": "s", "logged_in": True}]}),
        _step("act_sites", {"ok": True, "entries": [{"session_id": "s"}]}),
        _step("read_sites", {"ok": True,
                             "entries": [{"session_id": "s",
                                          "url": "https://account.booking.com/trips"}]}),
        _step("describe_entries", {"ok": False,
                                   "error_class": "needs_content_fetch",
                                   "entries": []}),
    ])


def test_sites_turn_does_not_inject_read_urls_html():
    r = MetisRecovery()
    fixed = r._fix_needs_content_fetch(_sites_run(), _CATALOG)
    assert fixed is None  # guard sites: nessun read_urls_html iniettato


def test_public_web_turn_still_injects_read_urls_html():
    """Regressione inversa: il caso web pubblico (find_urls senza sessione) DEVE
    continuare a ricevere read_urls_html."""
    run = RunResult(steps=[
        _step("find_urls", {"ok": True,
                            "entries": [{"url": "https://rocm.docs.amd.com/x"}]}),
        _step("describe_entries", {"ok": False,
                                   "error_class": "needs_content_fetch",
                                   "entries": []}),
    ])
    fixed = MetisRecovery()._fix_needs_content_fetch(run, _CATALOG)
    assert fixed is not None
    tools = [s.tool for s in fixed.steps]
    assert "read_urls_html" in tools
    assert tools[0] == "find_urls"          # produttore preservato
    assert tools[-1] == "describe_entries"  # consumer ricablato
