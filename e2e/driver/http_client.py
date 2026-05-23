"""http_client.py — client HTTP async per E2E driver.

Wrapper su aiohttp.ClientSession con:
  - admin auth via header `Authorization: Bearer <admin_key>`
  - helper `.admin_get/post`, `.chat`, `.run_job`
  - assertion `.assert_clean` con lint deterministico
  - timeout default ragionevoli

Conventions:
  - response JSON sempre via `Accept: application/json` (no HTML mix)
  - rotte admin require Bearer; rotte agent autenticate anche via cookie
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass
class ChatResponse:
    """Risposta del turno chat (subset JSON di /agent/turn)."""
    final_kind: str
    final_text: str
    final_html: Optional[str]
    turn_id: Optional[str]
    steps: list[dict]
    error: Optional[str] = None
    raw: dict = None  # type: ignore


class E2EClient:
    """Client HTTP async per il simulatore.

    Esempio:
        async with E2EClient(server.url, server.admin_key) as drv:
            proposals = await drv.admin_get("/admin/changes?state=proposed")
            r = await drv.chat("che ore sono")
    """

    def __init__(self, base_url: str, admin_key: str, *,
                 timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.admin_key = admin_key
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "E2EClient":
        self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("E2EClient used outside context manager")
        return self._session

    # --- admin endpoints --------------------------------------------------

    def _admin_headers(self, extra: Optional[dict] = None) -> dict:
        h = {
            "Authorization": f"Bearer {self.admin_key}",
            "Accept": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    async def admin_get(self, path: str) -> dict:
        async with self.session.get(self.base_url + path,
                                      headers=self._admin_headers()) as r:
            text = await r.text()
            if r.status >= 400:
                raise RuntimeError(
                    f"admin_get {path} → {r.status}: {text[:500]}"
                )
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"admin_get {path} → non-JSON response: {text[:500]}"
                )

    async def admin_post(self, path: str,
                          body: Optional[dict] = None,
                          *, htmx: bool = False) -> dict:
        """POST admin. Se `htmx=True`, aggiunge HX-Request header e
        ritorna text/html in chiave `_html`."""
        headers = self._admin_headers()
        if htmx:
            headers["HX-Request"] = "true"
            headers["Accept"] = "text/html"
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        async with self.session.post(self.base_url + path, **kwargs) as r:
            text = await r.text()
            if r.status >= 400:
                raise RuntimeError(
                    f"admin_post {path} → {r.status}: {text[:500]}"
                )
            if htmx:
                return {"_html": text, "status": r.status}
            try:
                return json.loads(text) if text.strip() else {}
            except json.JSONDecodeError:
                return {"_text": text}

    # --- agent endpoints --------------------------------------------------

    async def chat(self, query: str, *,
                    lang: Optional[str] = None,
                    user_id: str = "host",
                    timeout_s: float = 300.0) -> ChatResponse:
        """POST /agent/turn con query utente. Ritorna ChatResponse.

        timeout_s separato perche' i turni possono essere lunghi
        (synth, multi-step planner). Default 120s.
        """
        body = {"query": query, "user_id": user_id}
        if lang:
            body["lang"] = lang
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with self.session.post(
            self.base_url + "/agent/turn",
            json=body,
            headers={"Accept": "application/json"},
            timeout=timeout,
        ) as r:
            text = await r.text()
            if r.status >= 400:
                return ChatResponse(
                    final_kind="error", final_text="", final_html=None,
                    turn_id=None, steps=[],
                    error=f"HTTP {r.status}: {text[:500]}", raw={},
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return ChatResponse(
                    final_kind="error", final_text="", final_html=None,
                    turn_id=None, steps=[],
                    error=f"non-JSON response: {text[:500]}", raw={},
                )
            # Server schema (vedi runtime/http_routes_agent.py): `final_message`
            # e `final_message_html`. Manteniamo `final_text`/`final_html` come
            # alias semantici lato client per leggibilita' nei test.
            final_text = (data.get("final_message")
                           or data.get("final_text")
                           or "")
            final_html = (data.get("final_message_html")
                           or data.get("final_html"))
            return ChatResponse(
                final_kind=data.get("final_kind", "unknown"),
                final_text=final_text,
                final_html=final_html,
                turn_id=data.get("turn_id"),
                steps=data.get("steps") or data.get("steps_summary") or [],
                raw=data,
            )

    # --- jobs (scheduler v2 callbacks) ------------------------------------

    async def run_job(self, callback_key: str,
                        payload: Optional[dict] = None) -> dict:
        """Invoca uno scheduler callback via `POST /admin/jobs/{key}/fire`.
        Ritorna `{ok, callback, result}` o solleva su errore."""
        body = payload or {}
        return await self.admin_post(f"/admin/jobs/{callback_key}/fire", body)

    # --- health ------------------------------------------------------------

    async def health(self) -> dict:
        async with self.session.get(self.base_url + "/agent/health",
                                     headers={"Accept": "application/json"}) as r:
            text = await r.text()
            return {"status": r.status, "body": text}
