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

from playwright_sidecar import contract as _contract

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8771
DEFAULT_TIMEOUT_S = 90.0
LOGIN_TIMEOUT_S = 150.0


def _local_contract_failure() -> dict | None:
    status = _contract.source_status()
    if status["contract_aligned"]:
        return None
    return _contract.failure("client_source_stale", process="session_client")


def _response_contract_failure(headers) -> dict | None:
    received = headers.get(_contract.HEADER_NAME) if headers is not None else None
    if _contract.same_fingerprint(received, _contract.LOADED_FINGERPRINT):
        return None
    return _contract.failure(
        "client_sidecar_contract_mismatch",
        peer_fingerprint=received or "missing",
        process="session_client")


def _post(endpoint: str, payload: dict, *, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict:
    local_failure = _local_contract_failure()
    if local_failure is not None:
        return local_failure
    url = f"http://{host}:{port}{endpoint}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            _contract.HEADER_NAME: _contract.LOADED_FINGERPRINT,
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            contract_failure = _response_contract_failure(resp.headers)
            if contract_failure is not None:
                return contract_failure
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
                contract_failure = _response_contract_failure(e.headers)
                if contract_failure is not None:
                    if obj.get("error_class") == _contract.ERROR_CLASS:
                        return obj
                    return contract_failure
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
                 session_label: str = "", approval_token: str | None = None,
                 task_name: str | None = None,
                 credential_mode: str = "default",
                 stealth: bool = False,
                 stealth_techniques: list[str] | None = None,
                 browser_mode: str = "headless",
                 lang: str | None = None,
                 **kw) -> dict:
    return _post("/session/open", {"owner": owner, "url": url,
                                   "allowlist": allowlist,
                                   "session_label": session_label,
                                   "approval_token": approval_token,
                                   "task_name": task_name,
                                   "credential_mode": credential_mode,
                                   "stealth": bool(stealth),
                                   "stealth_techniques": (
                                       list(stealth_techniques or [])),
                                   "browser_mode": browser_mode,
                                   "lang": lang}, **kw)


def session_read(*, session_id: str, owner: str | None = None,
                 include_screenshot: bool = True,
                 include_forms: bool = False, **kw) -> dict:
    return _post("/session/read", {"session_id": session_id, "owner": owner,
                                   "include_screenshot": include_screenshot,
                                   "include_forms": include_forms}, **kw)


def session_login(*, session_id: str, owner: str | None = None,
                  domain: str | None = None,
                  form_hint: str | None = None,
                  approval_token: str | None = None,
                  one_time_code: str | None = None,
                  credential_mode: str = "default", **kw) -> dict:
    kw.setdefault("timeout_s", LOGIN_TIMEOUT_S)
    return _post("/session/login", {"session_id": session_id, "owner": owner,
                                    "domain": domain,
                                    "form_hint": form_hint,
                                    "approval_token": approval_token,
                                    "one_time_code": one_time_code,
                                    "credential_mode": credential_mode}, **kw)


def session_screenshot(*, session_id: str, owner: str | None = None, **kw) -> dict:
    return _post("/session/screenshot", {"session_id": session_id,
                                          "owner": owner}, **kw)


def session_close(*, session_id: str | None = None, owner: str | None = None,
                  all: bool = False, **kw) -> dict:
    return _post("/session/close", {"session_id": session_id, "owner": owner,
                                    "all": all}, **kw)


def session_act(*, session_id: str, owner: str, action: str,
                value_ref: str | None = None,
                approval_token: str | None = None,
                goal_query: str | None = None, **kw) -> dict:
    return _post("/session/act", {
        "session_id": session_id, "owner": owner, "action": action,
        "value_ref": value_ref, "approval_token": approval_token,
        "goal_query": goal_query,
    }, **kw)


def _primitive(endpoint: str, *, session_id: str, owner: str,
               approval_token: str | None = None, **payload) -> dict:
    return _post(f"/session/{endpoint}", {
        "session_id": session_id, "owner": owner,
        "approval_token": approval_token, **payload,
    })


def session_goto(*, session_id: str, owner: str, url: str, **kw) -> dict:
    return _primitive("goto", session_id=session_id, owner=owner, url=url, **kw)


def session_click(*, session_id: str, owner: str, target: str, **kw) -> dict:
    return _primitive("click", session_id=session_id, owner=owner,
                      target=target, **kw)


def session_fill(*, session_id: str, owner: str, target: str,
                 value_ref: str | None = None, **kw) -> dict:
    return _primitive("fill", session_id=session_id, owner=owner,
                      target=target, value_ref=value_ref, **kw)


def session_submit(*, session_id: str, owner: str, target: str, **kw) -> dict:
    return _primitive("submit", session_id=session_id, owner=owner,
                      target=target, **kw)


def session_wait(*, session_id: str, owner: str, seconds: int = 2, **kw) -> dict:
    return _primitive("wait", session_id=session_id, owner=owner,
                      seconds=seconds, **kw)
