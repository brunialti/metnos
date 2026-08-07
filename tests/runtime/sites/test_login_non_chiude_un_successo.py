"""A successful login must not close the session it just validated.

`login_sites` closes the session after a definitive failure, so a dead context
neither lingers nor eats the next turn's quota — and its comment always said
exactly that. The condition, however, looked only at the presence of a reason
code.

Since a SUCCESSFUL login started carrying one — `already_authenticated`,
because being logged in already is a state and not an error — every reused
authenticated session was closed the moment it was recognised as valid, and
the next step died with `session_lost` after a login reported as `ok`
(turns ff47fa654af84d70 and 9f6e712a24504032, 2026-08-07).

The property: closing follows a FAILURE, never a code.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def login_sites():
    sys.path.insert(0, str(_ROOT / "runtime"))
    spec = importlib.util.spec_from_file_location(
        "login_sites_under_test",
        _ROOT / "executors" / "login_sites" / "login_sites.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _esegui(modulo, monkeypatch, esito: dict) -> list[str]:
    chiuse: list[str] = []
    monkeypatch.setattr(modulo.session_client, "session_login",
                        lambda **kw: dict(esito, session_id=kw["session_id"]))
    monkeypatch.setattr(
        modulo.session_client, "session_close",
        lambda **kw: chiuse.append(kw.get("session_id")) or {"count": 1})
    monkeypatch.setenv("METNOS_ACTOR", "roberto")
    modulo.invoke({"session_ids": ["viva"]})
    return chiuse


def test_una_sessione_gia_autenticata_resta_aperta(login_sites, monkeypatch) -> None:
    chiuse = _esegui(login_sites, monkeypatch,
                     {"ok": True, "logged_in": True,
                      "reason_code": "already_authenticated"})
    assert chiuse == [], "chiusa la sessione appena riconosciuta come valida"


def test_un_login_riuscito_senza_codice_resta_aperto(login_sites, monkeypatch) -> None:
    chiuse = _esegui(login_sites, monkeypatch, {"ok": True, "logged_in": True})
    assert chiuse == []


def test_un_fallimento_definitivo_chiude(login_sites, monkeypatch) -> None:
    """L'altra meta': un context morto non resta ne' consuma la quota."""
    chiuse = _esegui(login_sites, monkeypatch,
                     {"ok": False, "logged_in": False,
                      "reason_code": "bad_credentials"})
    assert chiuse == ["viva"]


@pytest.mark.parametrize("reason", ["two_factor_required",
                                    "two_factor_push_required",
                                    "captcha_required", "approval_pending"])
def test_cio_che_attende_l_utente_resta_aperto(login_sites, monkeypatch,
                                               reason) -> None:
    chiuse = _esegui(login_sites, monkeypatch,
                     {"ok": False, "logged_in": False, "reason_code": reason})
    assert chiuse == []
