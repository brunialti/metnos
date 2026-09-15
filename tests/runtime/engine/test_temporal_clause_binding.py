"""One temporal mention must not fabricate both a period and a lower bound."""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from args_extractor import regex_extract
from engine.dispatch import _fill_clause_args
from engine.types import Framework, Intent, StepSpec
from time_window_resolver import resolve_time_window
import temporal_resolution as temporal


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))
SCHEMA = {"type": "object", "properties": {
    "account": {"type": "string"},
    **{field: {"type": "string", "format": "time-window"}
       for field in ("time_window", "since", "before")},
}}


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    monkeypatch.setattr(temporal, "temporal_now", lambda *_args, **_kwargs: NOW)
    monkeypatch.setattr(temporal, "_interpret", lambda *_args, **_kwargs:
                        pytest.fail("This deterministic binding needs no model"))


@pytest.mark.parametrize("query,expected", [
    ("leggi mail tiscali di giovedì", "weekday-3"),
    ("read tiscali mail on Thursday", "weekday-3"),
    ("leggi mail nelle ultime 48 ore", "last-48h"),
    ("read mail from the last 48 hours", "last-48h"),
])
def test_one_period_never_creates_since_or_before(query, expected):
    assert regex_extract(query, SCHEMA) == {"time_window": expected}


@pytest.mark.parametrize("field", ["since", "before"])
def test_standalone_bound_is_not_inferred_from_a_bare_period(field):
    schema = {"properties": {field: {"type": "string", "format": "time-window"}}}
    assert regex_extract("Thursday", schema) == {}


@pytest.mark.parametrize("tool,query", [
    ("read_messages", "leggi mail tiscali di giovedì"),
    ("read_objects", "read objects on Thursday"),
])
def test_monostep_guard_to_form_corrects_stale_today_without_fabricating_a_bound(tool, query):
    from orchestration import _apply_dialog_values
    from backends.messages.email_metnos import _read_time_bounds

    original = {"account": "fixture", "time_window": "today"}
    framework = Framework(steps=[StepSpec(tool, deepcopy(original)), StepSpec("final_answer", {})])
    catalog = [SimpleNamespace(name=tool, args_schema=SCHEMA)]
    _fill_clause_args(framework, Intent(verb="read", object="messages"), query, catalog)
    planned = framework.steps[0].args
    assert planned == original
    assert "since" not in planned and "before" not in planned

    canonical = resolve_time_window(tool, planned, query, SCHEMA)
    assert canonical["time_window"] == "weekday-3"
    resolved = temporal.resolve_temporal_args(tool, canonical, query, SCHEMA)
    form = temporal.temporal_form_request(tool, resolved, SCHEMA)["needs_inputs"]
    assert [item["var"] for item in form["dialog"]] == ["time_window"]
    callback = form["on_complete"]
    assert "since" not in callback["args_base"] and "before" not in callback["args_base"]
    choices = form["dialog"][0]["schema"]["choices"]
    selected = choices[-1]["value"]
    resumed = _apply_dialog_values(callback, {"time_window": selected})
    assert temporal.resolve_temporal_args(tool, resumed, "", SCHEMA) == resumed
    lower, upper, exclusive = _read_time_bounds(resumed["time_window"], None, None, now=NOW)
    assert lower.date().isoformat() == upper.date().isoformat() == "2026-09-17"
    assert lower.timestamp() < upper.timestamp()
    assert exclusive is False


@pytest.mark.parametrize("bounds", [
    {"since": "2026-09-10"},
    {"before": "2026-09-11"},
    {"since": "2026-09-10", "before": "2026-09-11"},
])
def test_explicit_planner_endpoints_are_not_removed_or_overwritten(bounds):
    original = {"time_window": "today", **bounds}
    framework = Framework(steps=[StepSpec("read_objects", deepcopy(original)), StepSpec("final_answer", {})])
    catalog = [SimpleNamespace(name="read_objects", args_schema=SCHEMA)]
    query = "read objects on Thursday"
    _fill_clause_args(framework, Intent(verb="read", object="objects"), query, catalog)
    assert framework.steps[0].args == original
    assert resolve_time_window("read_objects", framework.steps[0].args, query, SCHEMA) == original
