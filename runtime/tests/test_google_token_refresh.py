"""Fallback di refresh OAuth google (`_google_auth_common.ensure_fresh_token`,
esposto anche come `google_workspace._ensure_fresh_token`).

Resilienza: l'access token google scade ~1h. Il fallback rinnova in automatico
un token scaduto (refresh_token) e lo risalva; su fallimento (rete/revoca) torna
False → il chiamante ritorna needs_inputs (no traceback). Test deterministici,
NIENTE rete: la classe Credentials e' mockata.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_RUNTIME = str(Path(__file__).resolve().parent.parent)
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

from backends.files import google_workspace as gw
from backends import _google_auth_common as gac


class _FakeCreds:
    def __init__(self, *, valid, expired, refresh_token, raise_on_refresh=False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._raise = raise_on_refresh
        self.refreshed = False

    def refresh(self, _request):
        if self._raise:
            raise RuntimeError("network down / refresh_token revoked")
        self.refreshed = True
        self.valid = True
        self.expired = False

    def to_json(self):
        return json.dumps({"token": "NEW_ACCESS", "expiry": "2099-01-01T00:00:00Z",
                           "refresh_token": self.refresh_token, "scopes": ["s"]})


@pytest.fixture
def tokdir(tmp_path, monkeypatch):
    d = tmp_path / "gw"
    d.mkdir()
    monkeypatch.setattr(gac, "_skill_home", lambda name: d)
    return d


def _write_token(d):
    (d / "google_token.json").write_text(json.dumps({
        "token": "x", "refresh_token": "r", "scopes": ["s"],
        "client_id": "c", "client_secret": "s", "token_uri": "u",
        "account": "me", "type": "authorized_user", "universe_domain": "googleapis.com",
    }))


def _patch_creds(monkeypatch, fake):
    import google.oauth2.credentials as goc
    monkeypatch.setattr(goc.Credentials, "from_authorized_user_info",
                        classmethod(lambda cls, info, scopes=None: fake))


def test_no_token_file_returns_false(tokdir):
    assert gw._ensure_fresh_token() is False


def test_valid_token_true_no_refresh(tokdir, monkeypatch):
    _write_token(tokdir)
    fake = _FakeCreds(valid=True, expired=False, refresh_token="r")
    _patch_creds(monkeypatch, fake)
    assert gw._ensure_fresh_token() is True
    assert fake.refreshed is False        # token valido: nessun refresh inutile


def test_expired_refreshes_and_saves(tokdir, monkeypatch):
    _write_token(tokdir)
    fake = _FakeCreds(valid=False, expired=True, refresh_token="r")
    _patch_creds(monkeypatch, fake)
    assert gw._ensure_fresh_token() is True
    assert fake.refreshed is True
    saved = json.loads((tokdir / "google_token.json").read_text())
    assert saved["token"] == "NEW_ACCESS"      # token rinnovato risalvato
    assert saved["account"] == "me"            # campi extra preservati


def test_refresh_failure_returns_false_graceful(tokdir, monkeypatch):
    _write_token(tokdir)
    fake = _FakeCreds(valid=False, expired=True, refresh_token="r", raise_on_refresh=True)
    _patch_creds(monkeypatch, fake)
    # rete giù / refresh_token revocato → False, NESSUNA eccezione propagata
    assert gw._ensure_fresh_token() is False


def test_expired_without_refresh_token_false(tokdir, monkeypatch):
    _write_token(tokdir)
    fake = _FakeCreds(valid=False, expired=True, refresh_token=None)
    _patch_creds(monkeypatch, fake)
    assert gw._ensure_fresh_token() is False
