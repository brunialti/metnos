"""The installer emits working, private-LAN Web UI connection details."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

import http_auth
import http_routes_admin
from install.phases import phase5_systemd, phase6_firstboot


ROOT = Path(__file__).resolve().parents[3]


def test_connection_urls_are_exact_and_never_placeholders():
    local, lan = phase6_firstboot._connection_urls(
        9443, "0.0.0.0", lan_addresses=("192.168.50.8", "10.0.0.4"),
    )
    assert local == "http://127.0.0.1:9443/"
    assert lan == (
        "http://192.168.50.8:9443/",
        "http://10.0.0.4:9443/",
    )
    assert "<" not in "".join((local, *lan))


def test_loopback_install_does_not_claim_remote_reachability():
    assert phase6_firstboot._connection_urls(
        8770, "127.0.0.1", lan_addresses=("192.168.1.2",),
    ) == ("http://127.0.0.1:8770/", ())


def test_install_summary_records_real_lan_url_and_security_boundary(
        monkeypatch, tmp_path):
    monkeypatch.setenv("METNOS_USER_DATA", str(tmp_path))
    summary = phase6_firstboot._write_summary(
        [{"phase": 6, "name": "First boot", "done": True, "notes": {}}],
        port=9443,
        bind_host="0.0.0.0",
        lan_urls=("http://192.168.50.8:9443/",),
    ).read_text()
    assert "http://192.168.50.8:9443/" in summary
    assert "http://127.0.0.1:9443/" in summary
    assert "<this-machine-ip>" not in summary
    assert "do not forward this port" in summary


def test_http_unit_renders_selected_listener(monkeypatch):
    monkeypatch.setenv("METNOS_INSTALL_ROOT", str(ROOT))
    monkeypatch.setattr(
        phase5_systemd.llm_manager, "find_completion_bin", lambda: None,
    )
    template = (ROOT / "install/units/metnos-http.service.tmpl").read_text()
    lan = phase5_systemd._substitute(template, 9443, "en", "0.0.0.0")
    local = phase5_systemd._substitute(template, 9443, "en", "127.0.0.1")
    assert "Environment=METNOS_HTTP_HOST=0.0.0.0" in lan
    assert "Environment=METNOS_HTTP_HOST=127.0.0.1" in local
    assert "--host ${METNOS_HTTP_HOST} --port ${METNOS_HTTP_PORT}" in lan


def test_installer_onboard_token_is_valid_once(monkeypatch):
    key = "ab" * 32
    monkeypatch.setattr(phase6_firstboot.time, "time", lambda: 1_000)
    token = phase6_firstboot._onboard_token(key)
    assert http_auth.consume_admin_onboard_token(token, key, now=1_001)
    assert not http_auth.consume_admin_onboard_token(token, key, now=1_001)


def test_onboard_route_sets_http_cookie_for_direct_lan_access(monkeypatch):
    key = "cd" * 32
    token = phase6_firstboot._onboard_token(key)
    request = SimpleNamespace(
        app={"admin_key": key},
        query={"t": token},
        remote="192.168.50.4",
        scheme="http",
        headers={},
    )
    with pytest.raises(web.HTTPFound) as raised:
        asyncio.run(http_routes_admin.admin_onboard(request))
    response = raised.value
    assert response.location == "/admin"
    assert response.cookies[http_auth.ADMIN_COOKIE]["secure"] is False
    assert "/admin/onboard" in http_auth.ANON_EXACT_PATHS


def test_forwarded_scheme_is_used_only_from_trusted_proxy():
    trusted = SimpleNamespace(
        remote="127.0.0.1", scheme="http",
        headers={"X-Forwarded-Proto": "https"},
    )
    untrusted = SimpleNamespace(
        remote="203.0.113.7", scheme="http",
        headers={"X-Forwarded-Proto": "https"},
    )
    assert http_auth.external_request_scheme(trusted) == "https"
    assert http_auth.external_request_scheme(untrusted) == "http"
