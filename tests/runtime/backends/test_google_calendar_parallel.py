from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
GOOGLE_API = (
    ROOT / "executors" / "skills" / "google-workspace" / "scripts"
    / "google_api.py"
)


def _load_google_api():
    spec = importlib.util.spec_from_file_location(
        "metnos_test_google_api_parallel", GOOGLE_API)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_calendars_use_runtime_budget_and_emit_sorted_events(
        monkeypatch, capsys) -> None:
    google_api = _load_google_api()
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "3")
    monkeypatch.setattr(google_api, "_gws_binary", lambda: "gws")
    lock = threading.Lock()
    active = 0
    maximum = 0

    def fake_gws(parts, *, params=None, body=None):
        nonlocal active, maximum
        del body
        if parts == ["calendar", "calendarList", "list"]:
            return {"items": [
                {"id": name, "accessRole": "owner"}
                for name in ("late", "early", "middle")
            ]}
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        calendar_id = params["calendarId"]
        starts = {
            "late": "2026-07-23T10:00:00Z",
            "early": "2026-07-21T10:00:00Z",
            "middle": "2026-07-22T10:00:00Z",
        }
        with lock:
            active -= 1
        return {"items": [{
            "id": calendar_id, "summary": calendar_id,
            "start": {"dateTime": starts[calendar_id]},
            "end": {"dateTime": starts[calendar_id]},
        }]}

    monkeypatch.setattr(google_api, "_run_gws", fake_gws)
    google_api.calendar_list(SimpleNamespace(
        start="2026-07-20T00:00:00Z", end="2026-07-24T00:00:00Z",
        calendar="all", max=20,
    ))

    events = json.loads(capsys.readouterr().out)
    assert maximum == 3
    assert [event["_calendar_id"] for event in events] == [
        "early", "middle", "late"]
