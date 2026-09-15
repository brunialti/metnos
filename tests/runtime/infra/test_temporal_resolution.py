"""Common execution/form boundary; language model answers are simulated here."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import json

import pytest

import temporal_resolution as temporal
from time_window_parser import resolve_time_bounds

NOW = datetime(2026, 9, 15, 12, 30, 5, 123456, tzinfo=ZoneInfo("Europe/Rome"))
WINDOW = {"properties": {"time_window": {"type": "string"}}}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clear_cache = temporal._interpret.cache_clear
    monkeypatch.setattr(temporal, "temporal_now", lambda *args, **kwargs: NOW)
    clear_cache()
    yield
    clear_cache()


@pytest.mark.parametrize("query,expected", [
    ("leggi mail nelle ultime due ore", "last-2h"),
    ("read mail from the last two hours", "last-2h"),
    ("cerca file di due giorni fa", "today-2d"),
    ("find files from two days ago", "today-2d"),
    ("cerca foto del 15 settembre 2026", "2026-09-15"),
    ("find photos from September 15, 2026", "2026-09-15"),
])
def test_common_language_is_fast_and_does_not_call_a_model(monkeypatch, query, expected):
    monkeypatch.setattr(temporal, "_interpret", lambda *_args: pytest.fail("model not needed"))
    assert temporal.resolve_temporal_args("read_objects", {}, query, WINDOW) == {"time_window": expected}


def test_ambiguous_weekday_uses_frozen_selectable_bounds_without_a_model(monkeypatch):
    monkeypatch.setattr(temporal, "_interpret", lambda *_args: pytest.fail("model not needed"))
    args = temporal.resolve_temporal_args("read_objects", {}, "leggi quelli di giovedì", WINDOW)
    form = temporal.temporal_form_request("read_objects", args, WINDOW)["needs_inputs"]
    values = [item["value"] for item in form["dialog"][0]["schema"]["choices"]]
    assert values == [
        "2026-09-10T00:00:00+02:00/2026-09-10T23:59:59.999999+02:00",
        "2026-09-17T00:00:00+02:00/2026-09-17T23:59:59.999999+02:00"]
    before = resolve_time_bounds(values[0], NOW)
    assert resolve_time_bounds(values[0], NOW + timedelta(days=7)) == before
    from orchestration import _apply_dialog_values
    resumed = _apply_dialog_values(form["on_complete"], {"time_window": values[0]})
    assert "_temporal_choices" not in resumed
    assert temporal.resolve_temporal_args("read_objects", resumed, "", WINDOW) == resumed


def test_unqualified_weekday_also_offers_today_when_it_matches(monkeypatch):
    now = NOW + timedelta(days=2)
    monkeypatch.setattr(temporal, "temporal_now", lambda *_args, **_kwargs: now)
    args = temporal.resolve_temporal_args("read_objects", {}, "Thursday", WINDOW)
    form = temporal.temporal_form_request("read_objects", args, WINDOW)["needs_inputs"]
    choices = form["dialog"][0]["schema"]["choices"]
    assert [choice["value"][:10] for choice in choices] == ["2026-09-17", "2026-09-10", "2026-09-24"]


@pytest.mark.parametrize("kind,value,expected", [
    ("date", "dopodomani", "2026-09-17"),
    ("date", "September 16, 2026", "2026-09-16"),
    ("date-time", "tomorrow@09:00", "2026-09-16T09:00:00+02:00"),
    ("date-time", "now-1h", "2026-09-15T11:30:05.123456+02:00"),
])
def test_typed_fields_are_not_tied_to_a_domain(kind, value, expected):
    schema = {"properties": {"arbitrary_field": {"type": "string", "format": kind}}}
    result = temporal.resolve_temporal_args("create_objects", {"arbitrary_field": value}, "", schema)
    assert result == {"arbitrary_field": expected}


def test_a_day_does_not_invent_a_clock_for_datetime():
    schema = {"properties": {"start": {"type": "string", "format": "date-time"}}}
    result = temporal.resolve_temporal_args("create_objects", {"start": "tomorrow"}, "", schema)
    form = temporal.temporal_form_request("create_objects", result, schema)
    assert form["needs_inputs"]["dialog"][0]["schema"] == {"kind": "text"}


def test_vector_clarification_preserves_valid_siblings_and_positions():
    schema = {"properties": {"days": {"type": "array", "items": {"type": "string", "format": "date"}}}}
    result = temporal.resolve_temporal_args("get_objects", {"days": ["today", "Thursday", "tomorrow"]}, "", schema)
    form = temporal.temporal_form_request("get_objects", result, schema)["needs_inputs"]
    variable = form["dialog"][0]["var"]
    assert form["on_complete"]["list_args"]["days"] == ["2026-09-15", {"var": variable}, "2026-09-16"]
    from orchestration import _apply_dialog_values
    resumed = _apply_dialog_values(form["on_complete"], {variable: "2026-09-17"})
    assert resumed["days"] == ["2026-09-15", "2026-09-17", "2026-09-16"]


@pytest.mark.parametrize("query", [
    "leggi le mail da ieri alle 10:00 a oggi alle 11:00",
    "read messages from yesterday at 10:00 to today at 11:00",
    "leggi le mail delle ultime due ore e dieci minuti",
])
def test_complex_language_uses_one_bounded_fallback(monkeypatch, query):
    calls = []
    def interpret(*args):
        calls.append(args)
        return {"status": "resolved", "expression": "yesterday@10:00/today@11:00", "alternatives": []}
    monkeypatch.setattr(temporal, "_interpret", interpret)
    result = temporal.resolve_temporal_args("read_objects", {}, query, WINDOW)
    assert result["time_window"] == "yesterday@10:00/today@11:00"
    assert len(calls) == 1


def test_model_failure_requests_clarification_instead_of_using_a_partial_day(monkeypatch):
    monkeypatch.setattr(temporal, "_interpret", lambda *_args: (_ for _ in ()).throw(TimeoutError()))
    result = temporal.resolve_temporal_args("read_objects", {}, "da ieri alle 10:00 a oggi alle 11:00", WINDOW)
    form = temporal.temporal_form_request("read_objects", result, WINDOW)
    assert form["needs_inputs"]["dialog"][0]["schema"] == {"kind": "text"}


def test_absent_temporal_intent_does_not_call_a_model(monkeypatch):
    monkeypatch.setattr(temporal, "_interpret", lambda *_args: pytest.fail("no temporal request"))
    assert temporal.resolve_temporal_args("read_objects", {}, "read my mail", WINDOW) == {}
    assert temporal.resolve_temporal_args("write_objects", {}, "write tomorrow", WINDOW) == {}


def test_explicit_unbounded_window_never_asks_or_calls_a_model(monkeypatch):
    monkeypatch.setattr(temporal, "_interpret", lambda *_args: pytest.fail("explicit unbounded period"))
    assert temporal.resolve_temporal_args("find_objects", {"time_window": "all"}, "", WINDOW) == {"time_window": "all"}
    schema = {"properties": {"periods": {"type": "array", "items": {"type": "string", "format": "time-window"}}}}
    assert temporal.resolve_temporal_args("get_objects", {"periods": ["all", "today"]}, "", schema) == {"periods": ["all", "today"]}


@pytest.mark.parametrize("kind", ["date", "date-time"])
def test_unbounded_window_is_not_a_scalar_date(kind):
    schema = {"properties": {"start": {"type": "string", "format": kind}}}
    result = temporal.resolve_temporal_args("get_objects", {"start": "all"}, "", schema)
    assert temporal.temporal_form_request("get_objects", result, schema)


def test_a_fallback_model_cannot_discard_the_temporal_constraint(monkeypatch):
    response = {"status": "resolved", "expression": "all", "alternatives": []}
    monkeypatch.setattr("llm_router.LLMRouter", lambda: SimpleNamespace(provider=lambda _tier: SimpleNamespace(
        mode="local", chat=lambda *_args, **_kw: SimpleNamespace(text=json.dumps(response)))))
    with pytest.raises(ValueError, match="unbounded_temporal_interpretation"):
        temporal._interpret("dalle 10:00 di ieri", "time_window", "it", "2026-09-15", "Europe/Rome")


def test_local_model_call_has_a_budget_and_cache(monkeypatch):
    calls = []
    def chat(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps({"status": "resolved", "expression": "last-1h30min", "alternatives": []}))
    monkeypatch.setattr("llm_router.LLMRouter", lambda: SimpleNamespace(provider=lambda _tier: SimpleNamespace(mode="local", chat=chat)))
    call = ("nelle ultime due ore meno mezz'ora", "time_window", "it", "2026-09-15", "Europe/Rome")
    assert temporal._interpret(*call) == temporal._interpret(*call)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 240
    assert calls[0]["request_timeout_s"] == 6
    assert "grammar" in calls[0]


def test_fallback_cannot_invent_an_absolute_date(monkeypatch):
    response = {"status": "resolved", "expression": "2042-04-23", "alternatives": []}
    monkeypatch.setattr("llm_router.LLMRouter", lambda: SimpleNamespace(provider=lambda _tier: SimpleNamespace(
        mode="local", chat=lambda *_args, **_kw: SimpleNamespace(text=json.dumps(response)))))
    with pytest.raises(ValueError, match="ungrounded_absolute_date"):
        temporal._interpret("dalle 10:00 di ieri", "time_window", "it", "2026-09-15", "Europe/Rome")


def test_common_scope_resolver_applies_temporal_fields_without_a_known_domain():
    from args_resolver import resolve_scope_args
    assert resolve_scope_args("read_unknown_objects", {}, WINDOW, actor="fixture", query="ultime due ore") == {"time_window": "last-2h"}


def test_declared_temporal_format_is_independent_of_argument_name():
    schema = {"properties": {"receipt_start": {"type": "string", "format": "time-window"}}}
    args = temporal.resolve_temporal_args("get_objects", {"receipt_start": "Thursday"}, "", schema)
    form = temporal.temporal_form_request("get_objects", args, schema)
    assert len(form["needs_inputs"]["dialog"][0]["schema"]["choices"]) == 2


@pytest.mark.parametrize("pending", [None, "bad", [None], [{}], [{"index": -1}],
                                    [{"index": 100}], [{"index": True}], [{"index": "0"}]])
def test_malformed_private_form_state_does_not_raise_or_index_other_arguments(pending):
    schema = {"properties": {"days": {"type": "array", "items": {"type": "string", "format": "date"}}}}
    args = {"days": ["today"], "_temporal_choices": {"days": pending}}
    assert temporal.temporal_form_request("get_objects", args, schema) is None


def test_private_form_state_cannot_request_non_temporal_fields():
    schema = {"properties": {"password": {"type": "string"}}}
    args = {"_temporal_choices": {"password": [{"index": None, "options": []}]}}
    assert temporal.temporal_form_request("get_objects", args, schema) is None


def test_form_uses_a_localized_label_instead_of_a_raw_schema_field():
    schema = {"properties": {"opaque_internal_field": {"type": "string", "format": "date"}}}
    args = temporal.resolve_temporal_args("get_objects", {"opaque_internal_field": "Thursday"}, "", schema)
    form = temporal.temporal_form_request("get_objects", args, schema)
    prompt = form["needs_inputs"]["dialog"][0]["prompt"]
    assert "opaque_internal_field" not in prompt and "<missing:" not in prompt
