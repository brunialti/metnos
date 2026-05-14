"""Web backend httpx_default — crawler builtin (no JS).

Builtin backend per il `client="httpx"` dei verbi web. La logica HTTP/parsing
risiede nei moduli executor (`executors/<name>/<name>.py`) che gia'
contengono: throttle thread-safe (`HostThrottle`), HTTP cache disk-based
(`HttpCache`), host health tracker (`record_response`/`maybe_block_host`),
soft-fail con error_class (ADR 0101), parallelizzazione I/O bound, JS-
render opt-in via sidecar Playwright (ADR 0125).

Questo modulo e' il punto di indirezione canonical: espone i 4 verbi
sotto i nomi `read_html`/`read_pdf`/`find`/`login` (no underscore, no
`urls`/`session` suffix) per uniformita' con altri backend Metnos
(es. `messaging/email_metnos.send/read/find/delete/move`,
`files/local.find/read/write/...`, `calendar/local_ics.read/create/delete`).

Architettura (decisione 13/5/2026):
- Le funzioni qui non duplicano logica: chiamano il `_invoke_default` di
  ogni executor (rinominato dall'originale `invoke` durante il refactor
  dispatcher). Tutti i nomi di modulo lato executor restano accessibili
  (`OWNED_FILE`, `COOKIES_DIR`, `_has_pypdf`, `_playwright_client`, ecc.)
  per i test che li monkey-patchano.
- Vantaggio del thin re-export: il backend e' UN punto di accesso
  canonico, ma la logica resta concentrata in un file singolo per
  executor — facile evoluzione (es. introdurre `httpx` HTTP/2 sostituira'
  il backend in un punto, non in 4).

Predisposizione plugin esterni (ADR pending): quando arrivera' il loader
plugin esterni, `_HANDLERS` lato executor sara' arricchito con backend
extra senza toccare `httpx_default` (precedenza builtin > plugin).

Funzioni:
- `read_html(args) -> dict`
- `read_pdf(args) -> dict`
- `find(args) -> dict`
- `login(args) -> dict`

Contratto: vedi le rispettive `invoke()` originali negli executor
`read_urls_html`/`read_urls_pdf`/`find_urls`/`login_session`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Path dei 4 executor (siblings nella cartella `executors/`).
_EXECUTORS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "executors"
for _sub in ("read_urls_html", "read_urls_pdf", "find_urls", "login_session"):
    _p = str(_EXECUTORS_DIR / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import dei moduli executor. Lazy-safe: questo backend e' import-ato
# dal dispatcher `invoke()` di ogni executor (vedi sezione _HANDLERS la),
# ma non c'e' circular perche' qui non chiamiamo `invoke` (rinominato a
# `_invoke_default`): chiamiamo direttamente l'implementazione storica.
import read_urls_html as _ru_html  # noqa: E402
import read_urls_pdf as _ru_pdf  # noqa: E402
import find_urls as _f_urls  # noqa: E402
import login_session as _login  # noqa: E402


def read_html(args: dict) -> dict:
    """Fetch + estrazione contenuto principale di pagine HTML.

    Args:
        urls: list[str] (richiesto)
        auth_cookies_file?: str
        timeout_s?: float (default 10.0)
        max_bytes?: int (default 2_000_000)
        cache_ttl_s?: int (default 900s, 0=disable, ADR 0105)
        follow_iframes?: bool (default True)
        js_render?: bool (default False, opt-in sidecar Playwright)

    Returns:
        {ok, ok_count, fail_count, entries=[{url, title, body_text,
         meta, lang, fetched_at, iframe_urls?, linked_documents?,
         js_rendered?, error_class?}], failed=[{url, error, error_class}]}
    """
    return _ru_html._invoke_default(args)


def read_pdf(args: dict) -> dict:
    """Fetch + estrazione testo da URL che servono PDF.

    Args:
        urls: list[str] (richiesto)
        auth_cookies_file?: str
        timeout_s?: float (default 15.0)
        max_bytes?: int (default 20_000_000)
        max_pages_per_doc?: int (default 100)
        ocr_fallback?: bool (default False)

    Returns:
        {ok, ok_count, fail_count, entries=[{url, title, author,
         body_text, n_pages, used_lib, fetched_at, needs_ocr?}],
         failed=[{url, error}]}
    """
    return _ru_pdf._invoke_default(args)


def find(args: dict) -> dict:
    """Discovery URL e documenti (BFS multi-tier, ADR 0081).

    Due modi mutualmente esclusivi:
    - SEARCH: `search_query=<naturale>` (SearXNG + LLM rerank).
    - CRAWL:  `seed_urls=[...]` + `topic=[...]` per ranking BM25.

    Vedi `find_urls/manifest.toml` per arg schema completo.

    Returns:
        {ok, ok_count, fail_count, entries, discovered_documents,
         discovery_strategy, robots_skipped, metadata, truncated?,
         truncated_what?, used?, available_total?, cap_field?, cap_value?,
         error_class? (ADR 0101)}
    """
    return _f_urls._invoke_default(args)


def login(args: dict) -> dict:
    """Login form web + persist cookie jar (ADR 0082).

    Args:
        domain: str (richiesto; deve avere credenziale salvata)
        force?: bool (default False)
        timeout_s?: float (default 15.0)

    Returns:
        {ok, cached, session_cookies, expires_at, login_url, domain,
         cookie_file?}
    """
    return _login._invoke_default(args)
