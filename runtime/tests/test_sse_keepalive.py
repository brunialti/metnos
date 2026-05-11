"""test_sse_keepalive — Fix A + Fix C smoke (5/5/2026).

Tre case deterministici, niente daemon live, niente network reale:
  1. _sse_keepalive_loop emette `: keepalive\\n\\n` con cadenza
     SSE_KEEPALIVE_INTERVAL_S; lo sostituiamo a 0.05s per il test.
  2. close_active_sse chiama write_eof su tutte le SSE registrate
     in app["sse_responses"] e poi le rimuove.
  3. Il regex JS-side `^event:\\s*(\\w+)\\s*\\n+data:\\s*(.+)$` NON
     matcha un comment SSE `: keepalive` (test deterministico in
     Python via re.match — copia letterale del regex).
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Jinja2 e' system-package su questa macchina (vedi test_gallery.py).
_SYSDIST = "/usr/lib/python3/dist-packages"
if Path(_SYSDIST).exists() and _SYSDIST not in sys.path:
    sys.path.append(_SYSDIST)


class _FakeStreamResponse:
    """Doppio test minimale di web.StreamResponse: cattura le write."""

    def __init__(self, *, raise_on_write: bool = False):
        self.writes: list[bytes] = []
        self.eof_called = 0
        self._raise = raise_on_write

    async def write(self, data: bytes) -> None:
        if self._raise:
            raise ConnectionResetError("client gone")
        self.writes.append(data)

    async def write_eof(self) -> None:
        self.eof_called += 1


@pytest.mark.asyncio
async def test_keepalive_emits_comment_periodically(monkeypatch):
    """Il loop emette `: keepalive\\n\\n` su intervalli ripetuti.

    Comprimo l'intervallo a 0.05s e lascio girare ~0.18s → mi aspetto
    ~3 emissioni.
    """
    import http_routes_agent
    monkeypatch.setattr(http_routes_agent, "SSE_KEEPALIVE_INTERVAL_S", 0.05)

    resp = _FakeStreamResponse()
    task = asyncio.create_task(http_routes_agent._sse_keepalive_loop(resp))
    await asyncio.sleep(0.18)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Almeno 2 keepalive (timing safety: 0.18 / 0.05 = 3.6, ma JIT/CI varia)
    assert len(resp.writes) >= 2, f"expected >= 2 keepalive, got {len(resp.writes)}"
    for w in resp.writes:
        assert w == b": keepalive\n\n", f"unexpected payload: {w!r}"


@pytest.mark.asyncio
async def test_keepalive_loop_exits_on_connection_error(monkeypatch):
    """Se response.write solleva ConnectionResetError, il loop esce
    silenzioso (non rilancia).
    """
    import http_routes_agent
    monkeypatch.setattr(http_routes_agent, "SSE_KEEPALIVE_INTERVAL_S", 0.01)

    resp = _FakeStreamResponse(raise_on_write=True)
    # Il loop deve completarsi da solo dopo il primo write fallito.
    await asyncio.wait_for(
        http_routes_agent._sse_keepalive_loop(resp), timeout=0.5
    )


@pytest.mark.asyncio
async def test_close_active_sse_calls_write_eof_and_clears():
    """close_active_sse itera app['sse_responses'] e chiama write_eof
    su ognuna; il set viene svuotato a fine."""
    import http_routes_agent

    r1, r2, r3 = (_FakeStreamResponse() for _ in range(3))
    fake_app = {"sse_responses": {r1, r2, r3}}

    await http_routes_agent.close_active_sse(fake_app)

    assert r1.eof_called == 1
    assert r2.eof_called == 1
    assert r3.eof_called == 1
    assert fake_app["sse_responses"] == set()


def test_js_regex_ignores_keepalive_comment():
    """Il regex copia-conforme di chat.html non matcha un comment SSE.

    Regex JS originale (sintassi compatibile Python re):
        ^event:\\s*(\\w+)\\s*\\n+data:\\s*(.+)$  con flag s (DOTALL)

    Un comment SSE non comincia con `event:` quindi non deve matchare.
    """
    pattern = re.compile(r"^event:\s*(\w+)\s*\n+data:\s*(.+)$", re.DOTALL)

    # I comment SSE che il server emette
    assert pattern.match(": keepalive") is None
    assert pattern.match(": keepalive\n") is None

    # Caso positivo di sanity: un evento reale matcha
    m = pattern.match("event: thinking\ndata: {\"message\":\"hi\"}")
    assert m is not None
    assert m.group(1) == "thinking"


def test_js_startswith_colon_filter():
    """Nuovo guard esplicito in chat.html: `if(ev.startsWith(':')) continue;`.
    Verifica copy-paste-coerente che la stringa che il server emette
    inizia effettivamente con `:`."""
    server_payload = b": keepalive\n\n"
    # Quando il client splitta su \n\n, il singolo evento e' ": keepalive"
    # (senza il doppio newline finale, che e' il delimiter).
    text = server_payload.decode("utf-8").rstrip("\n")
    assert text.startswith(":")
