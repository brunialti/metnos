"""BrowserSurface — seam del due-browser (P0) e della fase extension (ADR 0191).

Direzione dipendenze UNICA (B1): `session_broker.op_*` -> `BrowserProvider` /
`PlaywrightSurface` -> Playwright. La surface NON richiama mai il broker; il
broker NON importa Playwright e NON lancia browser (owner = `server.py`).

P0 introduce SOLO il confine di launch (`BrowserProvider`) + una surface
owner-bound sottile che possiede un `context`+`page` di sessione. La migrazione
completa delle primitive (enumerate/fill/click/read) dentro `PlaywrightSurface`
appartiene alla fase `extension`, non al critical path headless (ADR 0191 §1.1).
"""
from __future__ import annotations

from typing import Awaitable, Callable

# Un provider riceve `stealth: bool` e ritorna il Browser (honest o, lazy, lo
# stealth). Il provider POSSIEDE i browser e vive in `server.py` (unico owner di
# Playwright). Tipizzato `object` per non importare Playwright nel broker.
BrowserProvider = Callable[[bool], Awaitable[object]]


class PlaywrightSurface:
    """Possiede ESATTAMENTE un `context` + `page` di sessione.

    In P0 e' un handle passivo: il broker continua a leggere `surface.page` /
    `surface.context` (o le copie in `session[sid]`). Il ciclo di vita del
    context resta gestito dal broker (reaper / `op_close`); `close()` qui e'
    idempotente e usato solo dalla fase extension.
    """

    __slots__ = ("context", "page", "stealth")

    def __init__(self, context, page, *, stealth: bool = False) -> None:
        self.context = context
        self.page = page
        self.stealth = bool(stealth)

    async def close(self) -> None:
        ctx = self.context
        self.context = None
        self.page = None
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
