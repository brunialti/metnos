"""Conformance and honest-failure gates for standardized ``get_places``."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"

from executors.get_places import get_places  # noqa: E402
from executors.find_places import find_places  # noqa: E402
from loader import Catalog, _load_dir_into_catalog  # noqa: E402
from prefilter import rank  # noqa: E402


MANIFEST = ROOT / "executors" / "get_places" / "manifest.toml"
FIND_MANIFEST = ROOT / "executors" / "find_places" / "manifest.toml"


def _catalog() -> Catalog:
    value = Catalog()
    _load_dir_into_catalog(
        ROOT / "executors", value, False, is_synthesized=False)
    return value


def test_manifest_declares_closed_reverse_geocoding_authority() -> None:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["platforms"] == ["linux"]
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["args"]["requires_one_of"] == [[
        "coords", "entries", "from_step"]]
    assert manifest["capabilities"] == [{
        "name": "network:http",
        "hint": [
            "maps.googleapis.com", "127.0.0.1", "localhost",
        ],
    }]
    assert "schema_inline" in manifest["output"]


def test_find_places_is_server_only_with_closed_geocoding_authority() -> None:
    manifest = tomllib.loads(FIND_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["executor_standard"] == "metnos.executor/1.0"
    assert manifest["platforms"] == ["linux"]
    assert manifest["placement"] == {"scope": "server", "device_ok": False}
    assert manifest["capabilities"] == [{
        "name": "network:http",
        "hint": ["nominatim.openstreetmap.org", "localhost"],
    }]


def test_find_places_forwards_runtime_language(monkeypatch) -> None:
    calls = []

    def forward(query, **kwargs):
        calls.append((query, kwargs))
        return [], "nominatim"

    monkeypatch.setattr(find_places, "_geo_forward", forward)
    result = find_places.invoke({"queries": ["pharmacy"], "_lang": "en-US"})

    assert result["ok"] is True
    assert calls[0][1]["lang"] == "en"


def test_find_places_propagates_canonical_provider_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        find_places, "_geo_forward", lambda *_a, **_k: ([], "rate_limited"))
    limited = find_places.invoke({"queries": ["pharmacy"]})
    assert limited["ok"] is False
    assert limited["fail_count"] == 1
    assert limited["failed"][0]["error_code"] == "ERR_EXT_SVC_LIMIT"

    monkeypatch.setattr(
        find_places, "_geo_forward", lambda *_a, **_k: ([], "error"))
    failed = find_places.invoke({"queries": ["pharmacy"]})
    assert failed["ok"] is False
    assert failed["fail_count"] == 1
    assert failed["failed"][0]["error_code"] == "WARN_EXT_SVC_DEGRADED"


def test_invalid_root_and_conflicting_sources_are_typed() -> None:
    root = get_places.invoke([])
    conflict = get_places.invoke({"coords": [], "entries": []})

    assert root["error_class"] == "invalid_input"
    assert root["error_code"] == "args_not_object"
    assert conflict["error_class"] == "invalid_input"
    assert conflict["error_code"] == "coordinates_conflict"


def test_empty_and_missing_gps_remain_valid_results() -> None:
    empty = get_places.invoke({"coords": []})
    missing = get_places.invoke({"entries": [{"path": "/tmp/photo.jpg"}]})

    assert empty == {
        "ok": True,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
        "places_resolved": 0,
        "places_unknown": 0,
        "places_failed": 0,
    }
    assert missing["ok"] is True
    assert missing["entries"] == [{
        "path": "/tmp/photo.jpg", "place": "unknown"}]
    assert missing["places_unknown"] == 1


def test_success_observes_resolved_place(monkeypatch) -> None:
    monkeypatch.setattr(
        get_places, "reverse_geocode", lambda _lat, _lon: "torino")

    result = get_places.invoke({"coords": [{"lat": 45.07, "lon": 7.69}]})

    assert result["ok"] is True
    assert result["entries"] == [{
        "lat": 45.07, "lon": 7.69, "place": "torino"}]
    assert result["places_resolved"] == 1


def test_dependency_failure_is_not_reported_as_unknown_success(monkeypatch) -> None:
    monkeypatch.setattr(
        get_places, "reverse_geocode", lambda _lat, _lon: None)

    result = get_places.invoke({"coords": [{"lat": 45.07, "lon": 7.69}]})

    assert result["ok"] is False
    assert result["ok_count"] == 0
    assert result["fail_count"] == 1
    assert result["entries"] == []
    assert result["error_class"] == "dependency_unavailable"
    assert result["error_code"] == "ERR_EXT_SVC_UNAVAILABLE"
    assert result["places_failed"] == 1


def test_mixed_resolution_is_explicitly_partial(monkeypatch) -> None:
    monkeypatch.setattr(
        get_places,
        "reverse_geocode",
        lambda lat, _lon: "torino" if lat == 45.0 else None,
    )

    result = get_places.invoke({
        "coords": [{"lat": 45, "lon": 7}, {"lat": 46, "lon": 8}],
    })

    assert result["ok"] is False
    assert result["partial"] is True
    assert result["ok_count"] == 1
    assert result["fail_count"] == 1
    assert result["entries"][0]["place"] == "torino"
    assert result["failed"][0]["error_code"] == "ERR_EXT_SVC_UNAVAILABLE"


def test_out_of_range_coordinates_fail_before_backend(monkeypatch) -> None:
    touched = []
    monkeypatch.setattr(
        get_places, "reverse_geocode",
        lambda *_args: touched.append(True),
    )

    result = get_places.invoke({"coords": [{"lat": 91, "lon": 7}]})

    assert result["ok"] is False
    assert result["error_code"] == "coordinates_invalid"
    assert touched == []


def test_natural_paraphrases_remain_routable() -> None:
    entries = list(_catalog().executors.values())
    for query in (
        "dimmi quale luogo corrisponde a queste coordinate GPS",
        "reverse geocode these latitude and longitude coordinates",
    ):
        names = [item.name for item in rank(query, entries, k=8, min_score=1)]
        assert "get_places" in names, (query, names)
