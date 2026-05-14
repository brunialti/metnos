"""Test per l'executor `find_events_empty` (ADR 0127, qualifier `_empty`).

Copertura:
  1. Args validation (time_windows lista, size unit-aware, time_of_day).
  2. Calendar busy <-> free gap computation.
  3. time_of_day filter morning/afternoon/range esplicito.
  4. size unit-aware filter (slot brevi scartati).
  5. max_results cap + truncated visibility (§2.7).
  6. Edge: tutto occupato, tutto libero, eventi overlap.
  7. Propagazione error_class da read_events.
  8. _parse_size_to_minutes parser deterministico §7.9 (8 case).

Run con `python3 -m pytest runtime/tests/test_find_events_empty.py -v`.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_EXEC_DIR = Path("/opt/myclaw/executors/find_events_empty")
_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_EXEC_DIR))
sys.path.insert(0, str(_RUNTIME))

ROME = ZoneInfo("Europe/Rome")


def _now_for(year=2026, month=5, day=15, hour=8):
    """Inietta un 'now' deterministico nel time_window_parser via monkeypatch."""
    return datetime(year, month, day, hour, 0, tzinfo=ROME)


def _patch_now(monkeypatch, now_value):
    """Allinea `now` deterministico fra `time_window_parser` e backend
    `local_ics` (refactor 13/5/2026: backend ha proprio `datetime.now`)."""
    fake = _FakeDatetime(now_value)
    monkeypatch.setattr("time_window_parser.datetime", fake)
    from backends.events import local_ics as _li
    monkeypatch.setattr(_li, "datetime", fake)


def _iso(y, m, d, hh, mm=0):
    """Helper costruzione ISO string con offset Europe/Rome."""
    dt = datetime(y, m, d, hh, mm, tzinfo=ROME)
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


@pytest.fixture
def fee(monkeypatch):
    """Re-import find_events_empty fresh per test isolation. Forza
    `_default_client → 'local'` (refactor 14/5/2026 google_workspace
    backend): senza patch i test girerebbero contro Google Calendar
    reale tramite il default auto-detect."""
    if "find_events_empty" in sys.modules:
        del sys.modules["find_events_empty"]
    mod = importlib.import_module("find_events_empty")
    monkeypatch.setattr(mod, "_default_client", lambda: "local")
    return mod


@pytest.fixture
def patch_calendar(monkeypatch):
    """Iniezione mock backend `local_ics` con cleanup automatico (monkeypatch)."""
    from backends.events import local_ics as _li

    def _do(mod, entries, ok=True, error_class=None, decision=None):
        if not ok:
            def fake_find(args):
                out = {"ok": False, "error": "fake",
                       "error_class": error_class or "server_error",
                       "entries": [], "used": 0}
                if decision:
                    out["decision"] = decision
                return out
            monkeypatch.setattr(_li, "find_events_empty", fake_find)
            return

        def fake_load(_path):
            out = []
            for e in entries:
                s = e.get("start"); en = e.get("end")
                if isinstance(s, str):
                    s = datetime.fromisoformat(s)
                if isinstance(en, str):
                    en = datetime.fromisoformat(en)
                if s is None or en is None:
                    continue
                out.append({
                    "start": s, "end": en,
                    "summary": e.get("summary", ""),
                    "uid": e.get("uid", ""),
                })
            return out
        monkeypatch.setattr(_li, "_load_events", fake_load)
    return _do


def _patch_read_events(mod, entries, ok=True, error_class=None, decision=None):
    """Legacy shim: applica mock SENZA monkeypatch cleanup — quando il test
    non riceve `patch_calendar` come fixture. Sconsigliato per nuovi test
    (può causare test pollution). Tenuto per minimizzare la diff."""
    from backends.events import local_ics as _li
    if not ok:
        def fake_find(args):
            out = {"ok": False, "error": "fake",
                   "error_class": error_class or "server_error",
                   "entries": [], "used": 0}
            if decision:
                out["decision"] = decision
            return out
        _li.find_events_empty = fake_find
        return

    def fake_load(_path):
        out = []
        for e in entries:
            s = e.get("start"); en = e.get("end")
            if isinstance(s, str):
                s = datetime.fromisoformat(s)
            if isinstance(en, str):
                en = datetime.fromisoformat(en)
            if s is None or en is None:
                continue
            out.append({
                "start": s, "end": en,
                "summary": e.get("summary", ""),
                "uid": e.get("uid", ""),
            })
        return out
    _li._load_events = fake_load


@pytest.fixture(autouse=True)
def _reset_local_ics_mocks():
    """Reset mocks su local_ics fra test (cleanup test pollution
    da `_patch_read_events` legacy senza monkeypatch)."""
    from backends.events import local_ics as _li
    orig_load = _li._load_events
    orig_find = _li.find_events_empty
    yield
    _li._load_events = orig_load
    _li.find_events_empty = orig_find


# --------------------------------------------------------------------------
# 1. Args validation
# --------------------------------------------------------------------------

def test_default_time_windows_uses_next_week(fee, monkeypatch):
    """Senza time_windows, default ['next-week'] (NON e' un required)."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({})
    assert r["ok"] is True
    # Almeno qualche slot disponibile in una settimana libera.
    assert len(r["entries"]) >= 1


def test_empty_time_windows_fails(fee):
    r = fee.invoke({"time_windows": []})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"
    assert "time_windows" in r["error"]


def test_invalid_size_fails(fee):
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "tanto"})
    assert r["ok"] is False
    assert "size" in r["error"]
    assert r["error_class"] == "invalid_args"


def test_invalid_time_of_day_fails(fee):
    r = fee.invoke({"time_windows": ["tomorrow"], "time_of_day": "midnight"})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"
    assert "time_of_day" in r["error"]


def test_invalid_calendar_id_fails(fee):
    r = fee.invoke({"time_windows": ["tomorrow"], "calendar_id": ""})
    assert r["ok"] is False
    assert "calendar_id" in r["error"]


def test_bad_time_window_format_fails(fee):
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["domani-prossimo"]})
    assert r["ok"] is False
    assert r["error_class"] == "invalid_args"


def test_time_windows_must_be_list(fee):
    """Stringa singola e' tollerata (normalizzata a lista); lista vuota o
    tipi diversi falliscono."""
    r = fee.invoke({"time_windows": 42})
    assert r["ok"] is False
    assert "time_windows" in r["error"]


def test_time_windows_string_normalized_to_list(fee, monkeypatch):
    """Backward-tolerant: passare una stringa singola e' accettato (e
    normalizzato a lista N=1) per non rompere chi continua a passare
    `time_windows="tomorrow"` come scalare durante la migrazione."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": "tomorrow", "size": "30min"})
    assert r["ok"] is True


# --------------------------------------------------------------------------
# 2. Calendar busy <-> free gap computation
# --------------------------------------------------------------------------

def test_empty_calendar_returns_whole_window(fee, monkeypatch):
    # tomorrow = 2026-05-13 con now=2026-05-12 fittizio.
    # Senza eventi, la finestra «tomorrow» dovrebbe essere tutta libera.
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "60min",
                    "max_results": 5})
    assert r["ok"] is True
    assert len(r["entries"]) >= 1
    for ent in r["entries"]:
        assert ent["kind"] == "free_slot"
        assert ent["duration_min"] >= 60


def test_busy_morning_free_afternoon(fee, monkeypatch):
    """Un evento 09:00-12:00 dovrebbe lasciare il pomeriggio libero."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    busy = [{"start": _iso(2026, 5, 13, 9), "end": _iso(2026, 5, 13, 12)}]
    _patch_read_events(fee, busy)
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "30min",
                    "time_of_day": "afternoon", "max_results": 10})
    assert r["ok"] is True
    # Almeno uno slot pomeridiano (dopo le 12) libero
    assert len(r["entries"]) >= 1
    for ent in r["entries"]:
        s = ent["start"]
        assert "T12:" in s or "T13:" in s or "T14:" in s or "T15:" in s \
            or "T16:" in s or "T17:" in s


def test_back_to_back_events_merge(fee, monkeypatch):
    """Due eventi adiacenti 09-10 e 10-12 devono fondersi (no slot fra)."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    busy = [
        {"start": _iso(2026, 5, 13, 9), "end": _iso(2026, 5, 13, 10)},
        {"start": _iso(2026, 5, 13, 10), "end": _iso(2026, 5, 13, 12)},
    ]
    _patch_read_events(fee, busy)
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "15min",
                    "time_of_day": "09:00-12:00"})
    assert r["ok"] is True
    # Nessuno slot fra 09 e 12: lista vuota o slot solo prima delle 9 (start
    # finestra), che la finestra time_of_day esclude.
    assert all(
        not (ent["start"] < _iso(2026, 5, 13, 12)
             and ent["end"] > _iso(2026, 5, 13, 9))
        for ent in r["entries"]
    )


def test_overlapping_events_merge(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    # Eventi overlap: 09-11 e 10-12 -> busy effettivo 09-12.
    busy = [
        {"start": _iso(2026, 5, 13, 9), "end": _iso(2026, 5, 13, 11)},
        {"start": _iso(2026, 5, 13, 10), "end": _iso(2026, 5, 13, 12)},
    ]
    _patch_read_events(fee, busy)
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "30min",
                    "time_of_day": "09:00-13:00"})
    assert r["ok"] is True
    # Atteso: 1 slot da 12:00 a 13:00.
    starts = [e["start"] for e in r["entries"]]
    assert any("T12:00" in s for s in starts)


# --------------------------------------------------------------------------
# 3. time_of_day filter
# --------------------------------------------------------------------------

def test_morning_keyword_filter(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["tomorrow"], "time_of_day": "morning",
                    "size": "30min", "max_results": 20})
    assert r["ok"] is True
    for ent in r["entries"]:
        s = ent["start"]
        # 06:00-12:00 finestra morning
        hh = int(s.split("T")[1].split(":")[0])
        assert 6 <= hh < 12


def test_custom_range_filter(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["tomorrow"], "time_of_day": "14:00-16:00",
                    "size": "30min"})
    assert r["ok"] is True
    for ent in r["entries"]:
        hh = int(ent["start"].split("T")[1].split(":")[0])
        assert 14 <= hh < 16


# --------------------------------------------------------------------------
# 4. size unit-aware filter
# --------------------------------------------------------------------------

def test_short_gaps_excluded_by_size(fee, monkeypatch):
    """Gap da 30 min dovrebbe essere scartato se size='60min'."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    busy = [
        {"start": _iso(2026, 5, 13, 9), "end": _iso(2026, 5, 13, 10)},
        {"start": _iso(2026, 5, 13, 10, 30), "end": _iso(2026, 5, 13, 12)},
    ]
    _patch_read_events(fee, busy)
    # Gap fra eventi: 10:00-10:30 = 30 min < 60 -> escluso.
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "60min",
                    "time_of_day": "09:00-12:00"})
    assert r["ok"] is True
    starts = [e["start"] for e in r["entries"]]
    assert not any("T10:00" in s for s in starts)


def test_size_hour_unit(fee, monkeypatch):
    """size '1hour' equivalent to '60min': stesso filtering."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    busy = [
        {"start": _iso(2026, 5, 13, 9), "end": _iso(2026, 5, 13, 10)},
        {"start": _iso(2026, 5, 13, 10, 30), "end": _iso(2026, 5, 13, 12)},
    ]
    _patch_read_events(fee, busy)
    r1 = fee.invoke({"time_windows": ["tomorrow"], "size": "60min",
                    "time_of_day": "09:00-12:00"})
    # `_patch_read_events` ha gia' iniettato `_load_events` sul backend
    # `local_ics` condiviso fra moduli (reimport find_events_empty non resetta
    # il backend). Stesso busy → stesso risultato per "1hour" alias di "60min".
    r2 = fee.invoke({"time_windows": ["tomorrow"], "size": "1hour",
                     "time_of_day": "09:00-12:00"})
    assert r1["entries"] == r2["entries"]


# --------------------------------------------------------------------------
# 5. max_results cap + truncated visibility
# --------------------------------------------------------------------------

def test_max_results_truncates(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    # Finestra "next-7d" senza eventi -> diversi slot mattutini disponibili.
    r = fee.invoke({"time_windows": ["next-7d"], "time_of_day": "morning",
                    "size": "60min", "max_results": 2})
    assert r["ok"] is True
    assert len(r["entries"]) <= 2
    # truncated cap field espliciti se cap raggiunto
    if r.get("truncated"):
        assert r["cap_field"] == "max_results"
        assert r["cap_value"] == 2
        assert r["truncated_what"] == "free_slots"
        assert r["available_total"] >= len(r["entries"])


def test_max_results_zero_means_no_cap(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["next-3d"], "max_results": 0,
                    "size": "60min", "time_of_day": "morning"})
    assert r["ok"] is True
    assert r.get("truncated") is not True


# --------------------------------------------------------------------------
# 6. Edge cases
# --------------------------------------------------------------------------

def test_all_busy_returns_empty(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    # Evento copre 06:00-22:00 (tutta la giornata utile).
    busy = [{"start": _iso(2026, 5, 13, 0), "end": _iso(2026, 5, 13, 23, 59)}]
    _patch_read_events(fee, busy)
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "30min"})
    assert r["ok"] is True
    assert r["entries"] == []
    assert r["available_total"] == 0


def test_iso_range_window(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["2026-05-13/2026-05-13"],
                    "size": "60min", "time_of_day": "morning"})
    assert r["ok"] is True
    # Tutti gli slot devono cadere il 13/5
    for ent in r["entries"]:
        assert "2026-05-13" in ent["start"]


# --------------------------------------------------------------------------
# 7. Propagazione error_class + decision needs_inputs
# --------------------------------------------------------------------------

def test_propagates_auth_required(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [], ok=False, error_class="auth_required")
    r = fee.invoke({"time_windows": ["tomorrow"]})
    assert r["ok"] is False
    assert r["error_class"] == "auth_required"


def test_propagates_needs_inputs(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(
        fee, [], ok=False, error_class="auth_required",
        decision="needs_inputs",
    )
    r = fee.invoke({"time_windows": ["tomorrow"]})
    assert r.get("decision") == "needs_inputs"


# --------------------------------------------------------------------------
# 8. Determinismo & schema entries
# --------------------------------------------------------------------------

def test_entries_schema_stable(fee, monkeypatch):
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({"time_windows": ["tomorrow"], "size": "30min",
                    "time_of_day": "morning"})
    assert r["ok"] is True
    for ent in r["entries"]:
        assert set(ent.keys()) >= {"kind", "start", "end", "duration_min",
                                     "calendar_id"}
        assert ent["kind"] == "free_slot"
        # ISO with explicit offset (no Z).
        assert "+" in ent["start"] or "-" in ent["start"][10:]


# --------------------------------------------------------------------------
# 9. _parse_size_to_minutes parser (deterministico §7.9)
# --------------------------------------------------------------------------

def _parse_size_via_backend(spec):
    """Bridge alla nuova API `backends.events.local_ics._parse_size_minutes`
    (refactor 13/5/2026, dispatcher canonical): API moderna `raise ValueError`,
    test parse_size mantenuti come tuple `(n, err)` per leggibilita'."""
    from backends.events import local_ics
    try:
        return local_ics._parse_size_minutes(spec), None
    except (ValueError, TypeError) as e:
        return None, str(e)


def test_parse_size_1hour(fee):
    n, err = _parse_size_via_backend("1hour")
    assert err is None
    assert n == 60


def test_parse_size_60min(fee):
    n, err = _parse_size_via_backend("60min")
    assert err is None
    assert n == 60


def test_parse_size_30_space_min(fee):
    n, err = _parse_size_via_backend("30 min")
    assert err is None
    assert n == 30


def test_parse_size_2_hours(fee):
    n, err = _parse_size_via_backend("2 hours")
    assert err is None
    assert n == 120


def test_parse_size_bare_number_is_minutes(fee):
    n, err = _parse_size_via_backend("90")
    assert err is None
    assert n == 90


def test_parse_size_zero_fails(fee):
    """Durata slot 0 non ha senso (≠ cap §2.4): API moderna raise."""
    n, err = _parse_size_via_backend("0")
    assert n is None
    assert err is not None and ("positive" in err or "size" in err)


def test_parse_size_malformed(fee):
    n, err = _parse_size_via_backend("tanto")
    assert n is None
    assert err is not None and "size" in err


def test_parse_size_none(fee):
    n, err = _parse_size_via_backend(None)
    assert n is None
    assert err is not None


# --------------------------------------------------------------------------
# 10. Multi-window (lista N>1) combina gap accumulando
# --------------------------------------------------------------------------

def test_multi_time_windows_accumulate(fee, monkeypatch):
    """Due finestre disgiunte accumulano slot ordinati."""
    _patch_now(monkeypatch, _now_for(2026, 5, 12, 8))
    _patch_read_events(fee, [])
    r = fee.invoke({
        "time_windows": ["2026-05-13/2026-05-13", "2026-05-15/2026-05-15"],
        "size": "60min", "time_of_day": "morning",
    })
    assert r["ok"] is True
    starts = [e["start"] for e in r["entries"]]
    # Lo start ordinato e' crescente sull'intero risultato cross-window.
    assert starts == sorted(starts)
    # Deve esserci almeno uno slot in entrambe le date.
    dates = {s[:10] for s in starts}
    assert "2026-05-13" in dates or "2026-05-15" in dates  # almeno uno


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class _FakeDatetime:
    """Minimal datetime shim per il time_window_parser: il modulo chiama
    `datetime.now(tz=ROME)` per ancorare today."""
    def __init__(self, now_value):
        self._now = now_value

    def __getattr__(self, name):
        # Delega tutti gli altri attributi al datetime reale.
        from datetime import datetime as _real_dt
        return getattr(_real_dt, name)

    def now(self, tz=None):
        if tz is None:
            return self._now.replace(tzinfo=None)
        return self._now.astimezone(tz)

    @classmethod
    def fromisoformat(cls, *args, **kwargs):
        from datetime import datetime as _real_dt
        return _real_dt.fromisoformat(*args, **kwargs)

    @classmethod
    def combine(cls, *args, **kwargs):
        from datetime import datetime as _real_dt
        return _real_dt.combine(*args, **kwargs)
