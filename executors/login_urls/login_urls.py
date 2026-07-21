#!/usr/bin/env python3
"""login_urls — login HTTP/cookie con credenziali cifrate (ADR 0082).

Workflow:
    1. Cerca `~/.config/metnos/cookies/<domain>.txt` esistente. Se presente
       e contiene almeno un cookie session (per i nomi dichiarati nel
       payload credenziale), e `force=False` → ritorna `cached:true`.
    2. Carica le credenziali via `runtime.credentials.load(domain)`. Se
       assente → fail con istruzione utente.
    3. GET sul `login_url` per estrarre eventuale csrf_token (cerca un
       <input> il cui name matcha `csrf|csrf_token|_token|authenticity_token`).
    4. POST sul `login_url` con `form_data` + csrf token (auto-iniettato
       sotto il name scoperto).
    5. Verifica successo: o (a) il jar contiene un cookie il cui name e'
       in `session_cookie_names`, o (b) status 302/303 di redirect, o
       (c) la response page NON contiene piu' un campo password.
    6. Salva il jar in `cookies/<domain>.txt` mode 0600.

Output: {ok, cached:bool, session_cookies:[names], expires_at, login_url,
        domain}.

Capability: ["network.read", "network.write", "auth.password_storage"].
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# Import diretto dal runtime per accedere a credentials.py
_RUNTIME = Path(__file__).resolve().parent.parent.parent / "runtime"
sys.path.insert(0, str(_RUNTIME))

from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402


COOKIES_DIR = Path.home() / ".config" / "metnos" / "cookies"
USER_AGENT = "metnos-crawler/1.1 (+contact@metnos.com)"

_CSRF_NAMES = re.compile(r"^(csrf|csrf_token|_token|authenticity_token)$",
                          re.IGNORECASE)


class _LoginFormParser(HTMLParser):
    """Cerca <input name=...> che potrebbero contenere un csrf token, e
    rileva la presenza di un campo password (per detection di login
    fallito = pagina rinvia il form di login)."""
    def __init__(self):
        super().__init__()
        self.csrf_name: str | None = None
        self.csrf_value: str | None = None
        self.has_password_input = False
        self.action: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "form":
            act = attrs_d.get("action")
            if act:
                self.action = act
        elif tag == "input":
            name = attrs_d.get("name") or ""
            value = attrs_d.get("value") or ""
            type_ = (attrs_d.get("type") or "text").lower()
            if type_ == "password":
                self.has_password_input = True
            elif _CSRF_NAMES.match(name):
                self.csrf_name = name
                self.csrf_value = value


def _ensure_cookies_dir():
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(COOKIES_DIR, 0o700)


def _cookie_file_for(domain: str) -> Path:
    # Sanity: domain senza separatori path
    if "/" in domain or "\\" in domain or ".." in domain:
        raise ValueError(f"invalid domain: {domain!r}")
    return COOKIES_DIR / f"{domain}.txt"


def _load_existing_jar(path: Path) -> http.cookiejar.MozillaCookieJar | None:
    if not path.exists():
        return None
    jar = http.cookiejar.MozillaCookieJar()
    try:
        jar.load(str(path), ignore_discard=True, ignore_expires=True)
        return jar
    except Exception:
        return None


def _has_session_cookies(jar: http.cookiejar.MozillaCookieJar,
                         names: list[str]) -> list[str]:
    """Ritorna la lista di nomi (subset di `names`) presenti nel jar."""
    if not jar or not names:
        return []
    found = []
    for cookie in jar:
        if cookie.name in names and cookie.value:
            # filtra i cookie scaduti
            now = time.time()
            if cookie.expires and cookie.expires < now:
                continue
            found.append(cookie.name)
    return sorted(set(found))


def _earliest_expiry(jar: http.cookiejar.MozillaCookieJar,
                     names: list[str]) -> int | None:
    out = None
    for cookie in jar:
        if cookie.name in names and cookie.expires:
            if out is None or cookie.expires < out:
                out = cookie.expires
    return out


def _invoke_default(args: dict) -> dict:
    """Implementazione default httpx (urllib + credentials). Il dispatcher
    `invoke()` instrada qui via `backends.urls.httpx_default`."""
    domain = args.get("domain")
    force = bool(args.get("force", False))
    timeout_s = float(args.get("timeout_s", 15.0))

    if not domain or not isinstance(domain, str):
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="domain")}

    _ensure_cookies_dir()

    try:
        cookie_path = _cookie_file_for(domain)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    # Carica payload credenziale
    try:
        import credentials
    except ImportError as e:
        return {"ok": False, "error": f"credentials module unavailable: {e}"}
    try:
        payload = credentials.load(domain)
    except Exception as e:
        return {"ok": False,
                "error": f"credential load failed for {domain}: {e}"}

    if payload is None:
        return {"ok": False,
                "error": (f"no stored credentials for domain {domain!r}; "
                          "use credentials.store(domain, {login_url, "
                          "method, form_data, session_cookie_names}) first.")}
    login_url = payload.get("login_url")
    method = (payload.get("method") or "POST").upper()
    form_data = dict(payload.get("form_data") or {})
    session_cookie_names = list(payload.get("session_cookie_names") or [])

    if not login_url:
        return {"ok": False, "error": _msg("ERR_ARG_MISSING", arg="login_url")}

    # 1. Cookie cached?
    if not force:
        jar_existing = _load_existing_jar(cookie_path)
        if jar_existing:
            present = _has_session_cookies(jar_existing, session_cookie_names)
            if present:
                return {
                    "ok": True,
                    "cached": True,
                    "session_cookies": present,
                    "expires_at": _earliest_expiry(jar_existing, session_cookie_names),
                    "login_url": login_url,
                    "domain": domain,
                }

    # 2. Esegui login: jar fresco (no cookie precedenti potenzialmente scaduti)
    jar = http.cookiejar.MozillaCookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # 2a. GET login_url per estrarre csrf
    csrf_name: str | None = None
    csrf_value: str | None = None
    try:
        req = urllib.request.Request(login_url, headers={"User-Agent": USER_AGENT})
        with opener.open(req, timeout=timeout_s) as resp:
            ctype = resp.headers.get("Content-Type", "").lower()
            if "text/html" in ctype:
                body = resp.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
                p = _LoginFormParser()
                try:
                    p.feed(body)
                except Exception:
                    pass
                csrf_name = p.csrf_name
                csrf_value = p.csrf_value
    except urllib.error.URLError as e:
        return {"ok": False, "error": _msg("ERR_OP_FAILED", reason=str(e))}
    except Exception as e:
        return {"ok": False, "error": _msg("ERR_OP_FAILED", reason=str(e))}

    if csrf_name and csrf_value and csrf_name not in form_data:
        form_data[csrf_name] = csrf_value

    # 2b. POST login_url
    try:
        encoded = urllib.parse.urlencode(form_data).encode("utf-8")
        req = urllib.request.Request(
            login_url,
            data=encoded if method == "POST" else None,
            method=method,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with opener.open(req, timeout=timeout_s) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "").lower()
            # Leggi al massimo 1 MB (verifica login form residuo)
            body = b""
            if "text/html" in ctype:
                body = resp.read(1 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        # 401/403/500 sono ovvi failure; lasciamo passare 302 (redirect ok)
        if e.code in (302, 303):
            status = e.code
            body = b""
        else:
            return {"ok": False,
                    "error": _msg("ERR_OP_FAILED", reason=f"{e.code}: {e.reason}")}
    except urllib.error.URLError as e:
        return {"ok": False, "error": _msg("ERR_OP_FAILED", reason=str(e.reason))}
    except Exception as e:
        return {"ok": False, "error": _msg("ERR_OP_FAILED", reason=str(e))}

    # 3. Verifica successo
    found_cookies = _has_session_cookies(jar, session_cookie_names) \
        if session_cookie_names else []
    success = bool(found_cookies)
    if not success and status in (302, 303):
        success = True
    if not success and body:
        # se la pagina post-POST non ha piu' un campo password, assumiamo successo
        try:
            text = body.decode("utf-8", errors="replace")
            p2 = _LoginFormParser()
            p2.feed(text)
            if not p2.has_password_input:
                success = True
        except Exception:
            pass

    if not success:
        return {
            "ok": False,
            "error": (f"login appears to have failed for {domain}: status={status}, "
                       f"no session cookie among {session_cookie_names}"),
            "domain": domain,
        }

    # 4. Salva jar su disco
    try:
        jar.save(str(cookie_path), ignore_discard=True, ignore_expires=True)
        os.chmod(cookie_path, 0o600)
    except Exception as e:
        return {"ok": False,
                "error": _msg("ERR_OP_FAILED", reason=str(e))}

    return {
        "ok": True,
        "cached": False,
        "session_cookies": found_cookies or [c.name for c in jar],
        "expires_at": _earliest_expiry(jar, session_cookie_names),
        "login_url": login_url,
        "domain": domain,
        "cookie_file": str(cookie_path),
    }


# --- Dispatcher (refactor 13/5/2026, ADR pending) -------------------------
_DEFAULT_CLIENT = "httpx"


def _resolve_backend(client: str):
    if client == "httpx":
        from backends.urls import httpx_default
        return httpx_default
    if client == "playwright":
        from backends.urls import playwright_stub
        return playwright_stub
    return None


def invoke(args: dict) -> dict:
    client = args.get("client") or _DEFAULT_CLIENT
    backend = _resolve_backend(client)
    if backend is None:
        return {"ok": False,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"client {client!r}")}
    return backend.login(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
