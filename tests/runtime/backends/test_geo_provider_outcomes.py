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
