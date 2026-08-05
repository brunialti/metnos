"""BrowserSurface — seam delle superfici browser e fase extension (ADR 0191).

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

# Il provider riceve `(browser_mode, launch_stealth)`. `browser_mode` seleziona
# la superficie headless o il browser grafico pilotato; il secondo argomento e'
# true solo se la selezione include una tecnica LAUNCH. Vive in `server.py`,
# unico owner di Playwright/browser.
BrowserProvider = Callable[[str, bool], Awaitable[object]]


class PlaywrightSurface:
    """Possiede un `context`+`page` e la selezione stealth della sessione.

    In P0 e' un handle passivo: il broker continua a leggere `surface.page` /
    `surface.context` (o le copie in `session[sid]`). Il ciclo di vita del
    context resta gestito dal broker (reaper / `op_close`); `close()` qui e'
    idempotente e usato solo dalla fase extension.
    """

    __slots__ = (
        "context", "page", "browser_mode", "stealth", "stealth_techniques")

    def __init__(self, context, page, *, browser_mode: str = "headless",
                 stealth_techniques=()) -> None:
        self.context = context
        self.page = page
        self.browser_mode = browser_mode
        self.stealth_techniques = tuple(stealth_techniques)
        self.stealth = bool(self.stealth_techniques)

    async def close(self) -> None:
        ctx = self.context
        self.context = None
        self.page = None
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
