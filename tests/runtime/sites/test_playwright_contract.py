"""Fail-closed alignment contract for Playwright clients and sidecar."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "runtime"))


def _aligned_status(contract) -> dict:
    return {
        "contract_loaded": contract.LOADED_FINGERPRINT,
        "contract_current": contract.LOADED_FINGERPRINT,
        "contract_aligned": True,
    }


def test_fingerprint_changes_with_source_content(tmp_path):
    from playwright_sidecar import contract

    source = tmp_path / "boundary.py"
    source.write_text("VALUE = 1\n")
    before = contract.source_fingerprint([source])
    source.write_text("VALUE = 2\n")
    after = contract.source_fingerprint([source])

    assert before.startswith(f"{contract.CONTRACT_SCHEME}:")
    assert before != after


def test_contract_surface_contains_broker_clients_and_agentic_executor():
    from playwright_sidecar import contract

    names = {path.name for path in contract._contract_source_files()}
    assert {
        "client.py", "contract.py", "server.py", "session_broker.py",
        "session_client.py", "agentic_executor.py", "credentials.py",
        "sites_audit.py", "sites_origin.py", "sites_url_scrub.py",
    }.issubset(names)


def test_server_rejects_old_client_before_handler(monkeypatch):
    from playwright_sidecar import contract, server

    called = False

    class Request:
        path = "/session/act"
        headers = {}

    async def handler(_request):
        nonlocal called
        called = True
        return server.web.json_response({"ok": True})

    monkeypatch.setattr(contract, "source_status",
                        lambda: _aligned_status(contract))
    response = asyncio.run(server._contract_middleware(Request(), handler))
    payload = json.loads(response.body)

    assert response.status == 409
    assert payload["error_class"] == contract.ERROR_CLASS
    assert payload["error_code"] == "client_sidecar_contract_mismatch"
    assert payload["peer_contract"] == "missing"
    assert called is False


def test_server_rejects_stale_loaded_source_before_handler(monkeypatch):
    from playwright_sidecar import contract, server

    called = False

    class Request:
        path = "/render"
        headers = {contract.HEADER_NAME: contract.LOADED_FINGERPRINT}

    async def handler(_request):
        nonlocal called
        called = True
        return server.web.json_response({"ok": True})

    stale = _aligned_status(contract)
    stale.update({"contract_current": "changed", "contract_aligned": False})
    monkeypatch.setattr(contract, "source_status", lambda: stale)
    response = asyncio.run(server._contract_middleware(Request(), handler))
    payload = json.loads(response.body)

    assert response.status == 503
    assert payload["error_code"] == "sidecar_source_stale"
    assert called is False


def test_server_labels_aligned_response(monkeypatch):
    from playwright_sidecar import contract, server

    class Request:
        path = "/session/read"
        headers = {contract.HEADER_NAME: contract.LOADED_FINGERPRINT}

    async def handler(_request):
        return server.web.json_response({"ok": True})

    monkeypatch.setattr(contract, "source_status",
                        lambda: _aligned_status(contract))
    response = asyncio.run(server._contract_middleware(Request(), handler))

    assert response.status == 200
    assert response.headers[contract.HEADER_NAME] == contract.LOADED_FINGERPRINT


class _Response:
    def __init__(self, payload: dict, headers: dict):
        self._body = json.dumps(payload).encode()
        self.headers = headers
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self._body


def test_session_client_sends_contract_and_accepts_same_server(monkeypatch):
    from playwright_sidecar import contract, session_client

    seen_headers = {}

    def urlopen(request, timeout):
        del timeout
        seen_headers.update({key.lower(): value
                             for key, value in request.header_items()})
        return _Response(
            {"ok": True, "session_id": "s1"},
            {contract.HEADER_NAME: contract.LOADED_FINGERPRINT})

    monkeypatch.setattr(contract, "source_status",
                        lambda: _aligned_status(contract))
    monkeypatch.setattr(session_client.urllib.request, "urlopen", urlopen)
    result = session_client._post("/session/open", {"owner": "test"})

    assert result == {"ok": True, "session_id": "s1"}
    assert seen_headers[contract.HEADER_NAME.lower()] == (
        contract.LOADED_FINGERPRINT)


def test_new_client_rejects_old_server_without_contract(monkeypatch):
    from playwright_sidecar import contract, session_client

    monkeypatch.setattr(contract, "source_status",
                        lambda: _aligned_status(contract))
    monkeypatch.setattr(
        session_client.urllib.request, "urlopen",
        lambda _request, timeout: _Response({"ok": True}, {}))

    result = session_client._post("/session/read", {})

    assert result["ok"] is False
    assert result["error_class"] == contract.ERROR_CLASS
    assert result["peer_contract"] == "missing"


def test_stale_client_does_not_make_http_request(monkeypatch):
    from playwright_sidecar import contract, session_client

    stale = _aligned_status(contract)
    stale.update({"contract_current": "changed", "contract_aligned": False})
    monkeypatch.setattr(contract, "source_status", lambda: stale)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("stale client must not call the sidecar")

    monkeypatch.setattr(session_client.urllib.request, "urlopen", forbidden)
    result = session_client._post("/session/act", {})

    assert result["error_class"] == contract.ERROR_CLASS
    assert result["error_code"] == "client_source_stale"
