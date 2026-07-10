# SPDX-License-Identifier: AGPL-3.0-only
"""session_client — client sincrono per il session-broker (spec sites §3.1).

Usato dagli executor `open/login/read/close_sites` (subprocess in sandbox) per
parlare col broker via `urllib.request`, senza trascinare aiohttp. Gli executor
NON vedono mai un segreto: passano `owner`/`url`/`session_id` e ricevono
metadata (§10.6). Determinismo §7.9: nessun LLM, nessun retry silenzioso.

Contratto: ogni funzione ritorna SEMPRE un dict. In caso di sidecar irraggiungibile
→ `{ok:false, error_class:"sidecar_down"}` (degrade onesto §2.8).
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771
DEFAULT_TIMEOUT_S = 90.0  # login può includere navigazioni lente


def _post(endpoint: str, payload: dict, *, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    url = f"http://{host}:{port}{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"invalid json response: {e}",
                        "error_class": "unknown"}
            return obj if isinstance(obj, dict) else {
                "ok": False, "error": "response not a dict", "error_class": "unknown"}
    except urllib.error.HTTPError as e:
        try:
            obj = json.loads(e.read().decode("utf-8", errors="replace"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        return {"ok": False, "error": f"broker http {e.code}",
                "error_class": "sidecar_down"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"broker unreachable: {e.reason}",
                "error_class": "sidecar_down"}
    except socket.timeout:
        return {"ok": False, "error": f"broker timeout after {timeout_s}s",
                "error_class": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "error_class": "unknown"}


def session_open(*, owner: str, url: str, allowlist=None,
                 session_label: str = "", **kw) -> dict:
    return _post("/session/open", {"owner": owner, "url": url,
                                   "allowlist": allowlist,
                                   "session_label": session_label}, **kw)


def session_read(*, session_id: str, include_screenshot: bool = True,
                 include_forms: bool = False, **kw) -> dict:
    return _post("/session/read", {"session_id": session_id,
                                   "include_screenshot": include_screenshot,
                                   "include_forms": include_forms}, **kw)


def session_login(*, session_id: str, domain: str | None = None,
                  form_hint: str | None = None, **kw) -> dict:
    return _post("/session/login", {"session_id": session_id, "domain": domain,
                                    "form_hint": form_hint}, **kw)


def session_screenshot(*, session_id: str, **kw) -> dict:
    return _post("/session/screenshot", {"session_id": session_id}, **kw)


def session_close(*, session_id: str | None = None, owner: str | None = None,
                  all: bool = False, **kw) -> dict:
    return _post("/session/close", {"session_id": session_id, "owner": owner,
                                    "all": all}, **kw)
