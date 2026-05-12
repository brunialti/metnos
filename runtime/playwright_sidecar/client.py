#!/usr/bin/env python3
"""Client sincrono per il sidecar Playwright (ADR 0125, Phase 1).

Usato dagli executor (`read_urls_html`) tramite `urllib.request` per evitare
di trascinare aiohttp client nel processo executor (sandbox bubblewrap).

Contratti:
    is_up(host, port, timeout_s=1.0) -> bool
        Probe non distruttivo. Timeout corto (default 1s) per non penalizzare
        gli executor quando il sidecar e' down. §2.8: True solo su 200 +
        json `ok=True`; tutto il resto = False (degrade graceful nel caller).

    render(url, *, wait_ms=2000, viewport=None, timeout_s=30.0,
           host="127.0.0.1", port=8771) -> dict
        Ritorna sempre un dict. In success: {"ok": True, "body_text",
        "body_html", "title", "final_url", "render_ms"}. In failure:
        {"ok": False, "error", "error_class"} con error_class fra:
        timeout|network|forbidden|not_found|server_error|unknown|sidecar_down.

Determinismo §7.9: nessun LLM, nessun retry, nessun side effect oltre la
chiamata HTTP. La non-determinismo intrinseca del browser e' isolata
dietro l'HTTP boundary del sidecar.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771
PROBE_TIMEOUT_S = 1.0
DEFAULT_RENDER_TIMEOUT_S = 30.0


def is_up(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          timeout_s: float = PROBE_TIMEOUT_S) -> bool:
    """Probe veloce su GET /health.

    Ritorna True solo se: TCP connect ok + HTTP 200 + json `ok=True`.
    Tutto il resto (connection refused, timeout, 5xx, json malformato,
    `ok=False`) ritorna False.
    """
    url = f"http://{host}:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                return False
            data = resp.read(4096)
            try:
                obj = json.loads(data.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                return False
            return bool(obj.get("ok"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            socket.timeout, ConnectionError, OSError):
        return False


def render(url: str, *,
           wait_ms: int = 2000,
           viewport: dict | None = None,
           timeout_s: float = DEFAULT_RENDER_TIMEOUT_S,
           host: str = DEFAULT_HOST,
           port: int = DEFAULT_PORT) -> dict:
    """POST /render. Ritorna sempre dict, fail-loud §2.8 in caso di errore.

    `viewport`: {"w": int, "h": int} oppure {"width", "height"}; None = default.
    `timeout_s`: HTTP timeout client-side; il server applica anche il suo
    timeout interno (~30s hard cap).
    """
    if not isinstance(url, str) or not url:
        return {"ok": False, "error": "url required (str)",
                "error_class": "unknown"}
    payload: dict = {"url": url, "wait_ms": int(wait_ms)}
    if viewport is not None:
        payload["viewport"] = viewport
    body = json.dumps(payload).encode("utf-8")
    endpoint = f"http://{host}:{port}/render"
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as e:
                return {"ok": False,
                        "error": f"invalid json response: {e}",
                        "error_class": "unknown"}
            if not isinstance(obj, dict):
                return {"ok": False,
                        "error": "response not a dict",
                        "error_class": "unknown"}
            # Server gia' restituisce ok=false in caso di errore di render,
            # con `error_class` corretto. Pass-through.
            return obj
    except urllib.error.HTTPError as e:
        # Il server in genere ritorna 200 anche su render-failure (con
        # ok=false nel body). Un HTTPError qui = errore del sidecar stesso
        # (5xx, 400 input invalido). Proviamo a leggere il body, fallback
        # generico.
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            obj = json.loads(err_body)
            if isinstance(obj, dict) and not obj.get("ok"):
                return obj
        except Exception:
            pass
        return {"ok": False,
                "error": f"sidecar http error {e.code}: {e.reason}",
                "error_class": "sidecar_down"}
    except urllib.error.URLError as e:
        # Connection refused / DNS / network — il sidecar non e' attivo.
        return {"ok": False,
                "error": f"sidecar unreachable: {e.reason}",
                "error_class": "sidecar_down"}
    except socket.timeout:
        return {"ok": False,
                "error": f"sidecar timeout after {timeout_s}s",
                "error_class": "timeout"}
    except Exception as e:
        return {"ok": False,
                "error": f"unexpected: {type(e).__name__}: {e}",
                "error_class": "unknown"}
