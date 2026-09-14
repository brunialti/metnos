"""Provider-chain outcome propagation must preserve actionable failures."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import geo_provider


def test_provider_loader_is_literal_complete_and_closed():
    assert geo_provider._load("google").__name__ == "google_places_client"
    assert geo_provider._load("photon").__name__ == "photon_client"
    assert geo_provider._load("unreviewed") is None
    with pytest.raises(TypeError):
        geo_provider.PROVIDERS["unreviewed"] = "unreviewed_client"


def test_forward_search_preserves_rate_limit_across_empty_fallback(monkeypatch):
    providers = {
        "google": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: ([], "rate_limited")),
        "photon": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: ([], "photon")),
    }
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google", "photon"])
    monkeypatch.setattr(geo_provider, "_load", providers.get)

    matches, status = geo_provider.forward_search("farmacia")

    assert matches == []
    assert status == "rate_limited"


def test_forward_search_later_success_wins_over_earlier_failure(monkeypatch):
    expected = [{"name": "Farmacia", "lat": 1.0, "lon": 2.0}]
    providers = {
        "google": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: ([], "error_http_503")),
        "photon": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: (expected, "photon")),
    }
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google", "photon"])
    monkeypatch.setattr(geo_provider, "_load", providers.get)

    matches, status = geo_provider.forward_search("farmacia")

    assert matches == expected
    assert status == "photon"


def test_http_429_is_canonicalized_for_executor(monkeypatch):
    provider = types.SimpleNamespace(
        forward_search=lambda *_a, **_k: ([], "error_http_429:quota"))
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google"])
    monkeypatch.setattr(geo_provider, "_load", lambda _name: provider)

    assert geo_provider.forward_search("farmacia") == ([], "rate_limited")


def test_generic_provider_failure_is_canonicalized(monkeypatch):
    provider = types.SimpleNamespace(
        forward_search=lambda *_a, **_k: ([], "error_http_503"))
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google"])
    monkeypatch.setattr(geo_provider, "_load", lambda _name: provider)

    assert geo_provider.forward_search("farmacia") == ([], "error")


def test_missing_api_is_error_only_without_completed_fallback(monkeypatch):
    providers = {
        "google": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: ([], "no_api_key")),
        "photon": types.SimpleNamespace(
            forward_search=lambda *_a, **_k: ([], "photon")),
    }
    monkeypatch.setattr(geo_provider, "_load", providers.get)
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google"])
    assert geo_provider.forward_search("farmacia") == ([], "error")

    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google", "photon"])
    assert geo_provider.forward_search("farmacia") == ([], "photon")


def test_provider_exception_is_not_reported_as_empty_success(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(geo_provider, "_chain", lambda: ["google"])
    monkeypatch.setattr(geo_provider, "_load", lambda _: types.SimpleNamespace(forward_search=fail))
    assert geo_provider.forward_search("pharmacy") == ([], "error")


@pytest.mark.parametrize("status", ["error_TimeoutError", "error_http_503", "rate_limited"])
def test_photon_does_not_expand_radius_or_fallback_after_transport_failure(monkeypatch, status):
    import photon_client
    calls = []
    monkeypatch.setattr(photon_client, "_autotag_for_query", lambda _: None)
    def fail(*args):
        calls.append(args)
        return [], status
    monkeypatch.setattr(photon_client, "_photon_call", fail)
    assert photon_client.forward_search(
        "pharmacy", near={"lat": 45, "lon": 9}, bounded=True) == ([], status)
    assert len(calls) == 1


def test_photon_keeps_expanding_after_a_successful_empty_response(monkeypatch):
    import photon_client
    expected = [{"name": "Pharmacy", "distance_km": 0.2}]
    calls = []
    monkeypatch.setattr(photon_client, "_autotag_for_query", lambda _: None)
    def search(*args):
        calls.append(args)
        return (expected if len(calls) == 2 else []), "photon"
    monkeypatch.setattr(photon_client, "_photon_call", search)
    assert photon_client.forward_search(
        "pharmacy", max_results=1, near={"lat": 45, "lon": 9}, bounded=True) == (expected, "photon")
    assert len(calls) == 2


def test_photon_uses_the_service_registry_endpoint(monkeypatch):
    import importlib
    import photon_client
    import services_registry
    with monkeypatch.context() as patch:
        patch.setenv("METNOS_PHOTON_URL", "http://geo.example.test:2322/")
        importlib.reload(photon_client)
        assert photon_client.PHOTON_BASE == services_registry.endpoint("photon")
        assert photon_client.PHOTON_BASE == "http://geo.example.test:2322"
    importlib.reload(photon_client)
