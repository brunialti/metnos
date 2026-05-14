"""Web backend playwright_stub — JS-render via sidecar (NOT YET ENABLED).

Stub esplicito per il `client="playwright"` dei verbi web. Quando
`runtime/playwright_sidecar/` viene avviato (porta 8771, Chromium
headless ~200MB RAM, ADR 0125), questo stub potra' essere sostituito
da un'implementazione che dispatcha al sidecar. Per ora ritorna sempre
`ok=false` con `error_class="not_implemented"` cosi' il PLANNER reagisce
onestamente (§2.8 no silent failure).

Differenza con `read_urls_html(js_render=true)`:
- `read_urls_html(js_render=true)` chiama il sidecar SOLO come fallback
  su pagine gia' rilevate come SPA dalla pipeline urllib (regola del
  fast path: prima provo HTTP economico, poi JS opt-in se serve).
- `read_urls_html(client="playwright")` (questo stub) forzerebbe il
  rendering JS direttamente, saltando l'HTTP veloce. Non ancora attivo.

Funzioni: stesso contratto di `httpx_default` (read_html / read_pdf /
find / login). Tutte ritornano `ok:false, error_class:"not_implemented"`.
"""
from __future__ import annotations


_NOT_IMPL_MSG = (
    "Playwright JS-render backend non ancora attivato come dispatcher "
    "client esclusivo. Per JS-render usa `read_urls_html(js_render=true)` "
    "che routa al sidecar SOLO sulle pagine SPA detectate."
)


def _not_impl_dict() -> dict:
    return {
        "ok": False,
        "error": _NOT_IMPL_MSG,
        "error_class": "not_implemented",
        "entries": [],
        "failed": [],
    }


def read_html(args: dict) -> dict:
    return _not_impl_dict()


def read_pdf(args: dict) -> dict:
    return _not_impl_dict()


def find(args: dict) -> dict:
    return _not_impl_dict()


def login(args: dict) -> dict:
    return _not_impl_dict()
