"""Cleaning up after yourself must not destroy what you did not create.

When one of the requested urls needs an allowlist approval, `open_sites`
rebuilds the whole vector on replay and therefore closes the contexts it just
opened — otherwise consent would be awaited with orphan browsers alive.

But a REUSED session existed before this call, and the plan in flight may
already be acting on it. Closing it killed a session mid-plan: the following
`act_sites` came back `session_lost` right after a navigation that had in fact
succeeded (real turn ff47fa654af84d70, 2026-08-07).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def open_sites(monkeypatch):
    sys.path.insert(0, str(_ROOT / "runtime"))
    spec = importlib.util.spec_from_file_location(
        "open_sites_under_test", _ROOT / "executors" / "open_sites" / "open_sites.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _prepara(modulo, monkeypatch, chiuse: list[str]):
    """Two urls: one reused session, one that asks for consent."""
    esiti = iter([
        {"ok": True, "session_id": "gia-viva", "url": "https://a.test/",
         "title": "A", "reused": True},
        {"ok": False, "error_class": "approval_required",
         "extra_hosts": ["cdn.b.test"], "approval_token": "t",
         "approved_allowlist": ["b.test", "cdn.b.test"]},
    ])
    monkeypatch.setattr(modulo.session_client, "session_open",
                        lambda **kw: next(esiti))
    monkeypatch.setattr(
        modulo.session_client, "session_close",
        lambda **kw: chiuse.append(kw.get("session_id")) or {"count": 1})
    monkeypatch.setattr(modulo, "_msg", lambda code, **kw: code, raising=False)


def test_una_sessione_riusata_non_viene_chiusa(open_sites, monkeypatch) -> None:
    chiuse: list[str] = []
    _prepara(open_sites, monkeypatch, chiuse)
    monkeypatch.setenv("METNOS_ACTOR", "roberto")

    open_sites.invoke({"urls": ["https://a.test/", "https://b.test/"]})

    assert "gia-viva" not in chiuse, (
        "closing a session this call only reused kills a plan in flight")


def test_una_sessione_aperta_qui_viene_chiusa(open_sites, monkeypatch) -> None:
    """The other half: what this call opened must not be left orphaned."""
    chiuse: list[str] = []
    esiti = iter([
        {"ok": True, "session_id": "aperta-ora", "url": "https://a.test/",
         "title": "A"},
        {"ok": False, "error_class": "approval_required",
         "extra_hosts": ["cdn.b.test"], "approval_token": "t",
         "approved_allowlist": ["b.test", "cdn.b.test"]},
    ])
    monkeypatch.setattr(open_sites.session_client, "session_open",
                        lambda **kw: next(esiti))
    monkeypatch.setattr(
        open_sites.session_client, "session_close",
        lambda **kw: chiuse.append(kw.get("session_id")) or {"count": 1})
    monkeypatch.setenv("METNOS_ACTOR", "roberto")

    open_sites.invoke({"urls": ["https://a.test/", "https://b.test/"]})

    assert chiuse == ["aperta-ora"]
