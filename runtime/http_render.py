"""http_render — content negotiation (Accept) + ETag helper.

Una sola coppia di funzioni serve tutti gli endpoint admin che ritornano
una collezione: `negotiate_collection` decide JSON vs HTML guardando
l'header `Accept`; `serve_with_etag` calcola SHA-256 del payload e
risponde 304 se l'`If-None-Match` matcha.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _jinja_msg(key: str, **kwargs) -> str:
    """Funzione Jinja globale `msg(key, **vars)` → i18n.sqlite.

    Lingua corrente via `messages.get` (env METNOS_LANG, default it).
    Se la chiave manca per la lingua, `i18n.get` ritorna fallback nativo
    (chiave fra `[]`) per visibilita' al developer.
    """
    import messages as _msgs
    return _msgs.get(key, **kwargs)


_jinja_env.globals["msg"] = _jinja_msg


def _jinja_linkify(text) -> "object":
    """Filtro `linkify`: testo → HTML ESCAPED con gli URL http(s) resi
    ancore cliccabili (target=_blank). Nato per i prompt dei dialog che
    portano un link operativo (picker Google Photos, 10/7: l'utente doveva
    copiare l'URL a mano). Escape PRIMA, ancore DOPO: mai HTML utente crudo."""
    import re
    from markupsafe import Markup, escape
    s = str(text or "")
    out: list[str] = []
    pos = 0
    for m in re.finditer(r"https?://[^\s<>\"']+", s):
        out.append(str(escape(s[pos:m.start()])))
        url = m.group(0)
        eu = str(escape(url))
        out.append(f'<a href="{eu}" target="_blank" rel="noopener">{eu}</a>')
        pos = m.end()
    out.append(str(escape(s[pos:])))
    return Markup("".join(out))


_jinja_env.filters["linkify"] = _jinja_linkify


def render_template(name: str, **ctx) -> str:
    """Render del template Jinja `name` con il contesto `ctx`."""
    return _jinja_env.get_template(name).render(**ctx)


def wants_html(request: web.Request) -> bool:
    """True se l'Accept del chiamante preferisce HTML (default JSON)."""
    accept = request.headers.get("Accept", "")
    if "text/html" in accept:
        return True
    return False


def etag_for(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:16]


def _error(status: int, code: str, message: str) -> web.Response:
    """Risposta JSON d'errore uniforme (condivisa da http_routes_agent/admin)."""
    return web.json_response({"error": code, "message": message}, status=status)


def serve_with_etag(
    request: web.Request,
    payload_bytes: bytes,
    *,
    content_type: str,
) -> web.Response:
    """Serve `payload_bytes` con header ETag; 304 se If-None-Match matcha."""
    etag = etag_for(payload_bytes)
    inm = request.headers.get("If-None-Match", "").strip().strip('"')
    if inm == etag:
        return web.Response(status=304, headers={"ETag": f'"{etag}"'})
    return web.Response(
        body=payload_bytes,
        content_type=content_type,
        headers={"ETag": f'"{etag}"', "Cache-Control": "private, max-age=15"},
    )


def negotiate_collection(
    request: web.Request,
    *,
    json_payload: dict | list,
    template: str,
    template_ctx: dict,
) -> web.Response:
    """Sceglie HTML (se Accept) o JSON; in entrambi i casi applica ETag."""
    if wants_html(request):
        body = render_template(template, **template_ctx).encode("utf-8")
        return serve_with_etag(request, body, content_type="text/html")
    body = json.dumps(json_payload, ensure_ascii=False, default=str).encode("utf-8")
    return serve_with_etag(request, body, content_type="application/json")
