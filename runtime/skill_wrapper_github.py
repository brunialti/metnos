"""skill_wrapper_github - helper specifici per executor della skill `github`.

Regola del 3 (§7.2): i pattern qui sono replicati da >=3 wrapper github
(issues/pulls/comments/workflows). Centralizzati per evitare duplicazione.

Funzioni pure (no side effects globali, no LLM, niente IO state):
- parse_link_header(value) -> dict           pagination URLs (next/prev/first/last)
- paginate(client, url, params, max_pages)   generator che segue Link header
- extract_rate_limit(headers) -> dict        X-RateLimit-* normalizzati
- should_retry_after(headers, status) -> int|None   Retry-After o X-RateLimit-Reset

Determinismo §7.9: nessun LLM, nessun cache module-level mutabile.
"""
from __future__ import annotations

import re
import time
from typing import Any, Generator, Optional


# RFC 5988 Link header. Esempio:
#   <https://api.github.com/.../issues?page=2>; rel="next",
#   <https://api.github.com/.../issues?page=5>; rel="last"
_LINK_ENTRY_RE = re.compile(
    r'<(?P<url>[^>]+)>\s*;\s*rel\s*=\s*"(?P<rel>[^"]+)"'
)


def parse_link_header(value: Optional[str]) -> dict[str, str]:
    """Estrae {rel: url} dal Link header (vuoto -> {}).

    DEVI: passare la stringa raw del header (None tollerato).
    OK: parse_link_header('<u1>; rel="next", <u2>; rel="last"')
        -> {"next":"u1","last":"u2"}
    ERRORE: passare lista di header (Python httpx ritorna gia' stringa).
    """
    out: dict[str, str] = {}
    if not value or not isinstance(value, str):
        return out
    for m in _LINK_ENTRY_RE.finditer(value):
        rel = m.group("rel")
        url = m.group("url")
        if rel and url:
            out[rel] = url
    return out


def paginate(
    client: Any,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    max_pages: int = 10,
    timeout_s: float = 20.0,
) -> Generator[Any, None, None]:
    """Generator che segue il header `Link: rel="next"` fino a max_pages.

    Yields i parsed JSON body di ogni pagina (list o dict, dipende dall'API).
    DEVI: passare client httpx-compatible (`request(method, url, ...)`).
    NON DEVI: usare per request POST/PATCH (pagination e' GET-only).
    Stop conditions:
      - max_pages raggiunto
      - assenza rel="next" nel Link header
      - errore HTTP (>=400) o di rete → yield un MARKER
        `{"_github_error": {...}}` e stop. §2.8: NON spacciare un risultato
        parziale (rate-limit/timeout) per completo; il consumer distingue
        troncatura da fine-paginazione e legge `retry_after_s`/`rate_limit`.
    """
    if max_pages <= 0:
        return
    cur_url: Optional[str] = url
    cur_params = dict(params or {})
    pages_done = 0
    while cur_url and pages_done < max_pages:
        try:
            resp = client.request(
                "GET", cur_url, params=cur_params if pages_done == 0 else None,
                headers=headers, timeout=timeout_s,
            )
        except Exception as e:
            # Timeout/connection error: prima crashava il consumer del generator.
            yield {"_github_error": {
                "status": None, "error": str(e),
                "error_class": "network", "pages_done": pages_done,
            }}
            return
        try:
            body = resp.json()
        except Exception:
            body = None
        if resp.status_code >= 400:
            yield {"_github_error": {
                "status": resp.status_code,
                "retry_after_s": should_retry_after(resp.headers, resp.status_code),
                "rate_limit": extract_rate_limit(resp.headers),
                "body": body,
                "pages_done": pages_done,
            }}
            return
        yield body
        links = parse_link_header(resp.headers.get("Link"))
        cur_url = links.get("next")
        pages_done += 1


def extract_rate_limit(headers: Any) -> dict[str, Any]:
    """Estrae X-RateLimit-* normalizzati. Accetta dict o Mapping-like.

    Output keys (tutti opzionali, presenti solo se header c'e'):
      - limit (int)
      - remaining (int)
      - reset_epoch (int)
      - reset_in_s (float, calcolato vs time.time())
      - resource (str)
      - used (int)
    """
    out: dict[str, Any] = {}
    if headers is None:
        return out
    def _get(name: str) -> Optional[str]:
        try:
            return headers.get(name)
        except Exception:
            return None

    for src, dst, caster in (
        ("X-RateLimit-Limit",     "limit",       int),
        ("X-RateLimit-Remaining", "remaining",   int),
        ("X-RateLimit-Reset",     "reset_epoch", int),
        ("X-RateLimit-Used",      "used",        int),
    ):
        v = _get(src)
        if v is not None:
            try:
                out[dst] = caster(v)
            except (TypeError, ValueError):
                pass
    res = _get("X-RateLimit-Resource")
    if res:
        out["resource"] = res
    if "reset_epoch" in out:
        delta = out["reset_epoch"] - time.time()
        if delta > 0:
            out["reset_in_s"] = round(delta, 1)
    return out


def should_retry_after(headers: Any, status: int) -> Optional[float]:
    """Ritorna i secondi di attesa raccomandati per 429/503, None altrimenti.

    Precedenza: Retry-After (secondi o HTTP-date) > X-RateLimit-Reset (epoch).
    Cap a 600s per sicurezza (oltre, fallisci immediatamente).
    """
    if status not in (429, 503) or headers is None:
        return None
    def _get(name: str) -> Optional[str]:
        try:
            return headers.get(name)
        except Exception:
            return None

    ra = _get("Retry-After")
    if ra:
        try:
            v = float(ra)
            if 0 < v <= 600:
                return v
        except ValueError:
            pass
    reset = _get("X-RateLimit-Reset")
    if reset:
        try:
            delta = float(reset) - time.time()
            if 0 < delta <= 600:
                return delta
        except ValueError:
            pass
    return None
