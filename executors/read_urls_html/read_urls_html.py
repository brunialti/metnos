#!/usr/bin/env python3
"""read_urls_html — fetch + estrazione contenuto principale di pagine HTML.

Vettoriale: una sola call processa una lista di URL e ritorna entries
con `body_text` (plain text), `title`, `meta` (og:*, description, author),
e `lang` opzionale.

Algoritmo deterministico (no LLM):
    1. HTTP GET con User-Agent dichiarato, follow redirect ×3.
    2. Dispatch su `Content-Type`: text/html → parse HTML sotto;
       application/pdf → PDF-handoff (estrazione testo via
       runtime/pdf_extract, condivisa con read_urls_pdf, 12/6/2026);
       altrimenti skip (error_class=non_html).
    3. Strip `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`,
       `<aside>`, `<form>`.
    4. Preferenza container: `<article>` > `<main>` > `<body>`
       (heuristica readability-lite).
    5. Estrazione meta: og:*, description, author, <time datetime=>.
    6. Plain-text via html.parser stdlib. Trim a 50_000 char.

Cookie: se `auth_cookies_file` puntato a un file Mozilla cookies.txt,
viene caricato e iniettato in ogni Request.

Output: entries=[{url, title, body_text, meta:{...}, lang?, fetched_at}]
        + ok_count, fail_count, failed=[{url, error}].
"""
from __future__ import annotations

import gzip
import html
import http.cookiejar
import io
import json
import multiprocessing
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

# Throttle condiviso (ADR 0103) — modulo runtime/host_throttle.py.
sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from host_throttle import HostThrottle  # noqa: E402
# Estrazione PDF condivisa con read_urls_pdf (§7.3) per il PDF-handoff:
# find_urls ritorna liste miste HTML+PDF e il planner non puo' conoscere
# il Content-Type a priori — il dispatch per tipo vive QUI (12/6/2026).
from pdf_extract import extract_pdf_text  # noqa: E402
# HTTP cache disk-based (ADR 0105).
from http_cache import HttpCache, DEFAULT_TTL_S  # noqa: E402
# Host health tracker per auto-degrade T2→T1 su 429/503 (ADR 0108).
try:
    from host_health import record_response, maybe_block_host  # noqa: E402
except Exception:
    record_response = None  # type: ignore
    maybe_block_host = None  # type: ignore
# Playwright sidecar client per JS-rendering opt-in (ADR 0125).
# Import gated dietro try cosi' il modulo carica anche se la cartella
# `playwright_sidecar/` viene rimossa (degrade graceful).
try:
    from playwright_sidecar import client as _playwright_client  # noqa: E402
except Exception:
    _playwright_client = None  # type: ignore


USER_AGENT = "metnos-crawler/1.2 (+metnos@metnos.com)"

# Meta-refresh detection (8/5/2026, simmetrico al fix di find_urls).
# `<meta http-equiv="refresh" content="N;URL=target">` non e' seguito da
# urllib (solo HTTP 30x). Pattern legittimo (Aruba landing, WordPress redirect
# plugin, IIS legacy). Senza follow, read_urls_html ritorna body vuoto sulla
# pagina-redirect → false-negative. Hop cap 4 per anti-loop. Generalizzato.
_META_REFRESH_RE = re.compile(
    r'<meta\s+[^>]*http-equiv\s*=\s*["\']?refresh["\']?'
    r'[^>]*content\s*=\s*["\']?\s*\d+\s*;\s*url\s*=\s*([^"\'>\s]+)',
    re.IGNORECASE,
)
_META_REFRESH_HOP_CAP = 4


def _extract_meta_refresh(html_text: str) -> str | None:
    """Ritorna l'URL del meta-refresh nei primi 16 KB del <head>, o None."""
    if not html_text:
        return None
    head = html_text[:16384]
    m = _META_REFRESH_RE.search(head)
    if not m:
        return None
    target = m.group(1).strip()
    return target or None
_DEFAULT_MAX_BYTES = 2_000_000
_BODY_TRIM_CHARS = 50_000
# Cap byte per il PDF-handoff: i PDF sono piu' grandi delle pagine HTML,
# il max_bytes utente (default 2 MB) li troncherebbe corrompendo il parse.
# Allineato al default di read_urls_pdf.
_PDF_HANDOFF_MAX_BYTES = 20_000_000

# Parallelismo (ADR 0100). I/O-net bound: scala bene con thread.
# Cap globale = min(32, cpu*4) bilancia FD ulimit + memoria parsing.
# Per-host = 4 di default (conservativo, identifichiamo crawler con UA).
_GLOBAL_MAX = int(os.environ.get(
    "METNOS_READ_URLS_GLOBAL_MAX", min(32, max(1, multiprocessing.cpu_count()) * 4)
))
_PER_HOST_MAX = int(os.environ.get("METNOS_READ_URLS_PER_HOST", "4"))

# Tag i cui contenuti vengono SCARTATI integralmente (rumore di pagina).
# `iframe` resta in DROP per il body_text estratto (non vogliamo includere
# il TAG come testo), ma la sua src viene catturata in `_iframe_srcs` per
# fallback iframe-following (c2.1).
_DROP_TAGS = frozenset({"script", "style", "nav", "header", "footer",
                         "aside", "form", "svg", "iframe"})

# Tag preferiti per l'estrazione del contenuto principale, in ordine
# di priorita' (si sceglie il primo che esiste nella pagina).
_CONTENT_PREFERENCE = ("article", "main", "body")

# Heuristic JS-rendering detection (c2.3): se il rapporto
# text_chars/html_chars e' sotto questa soglia, la pagina e' probabilmente
# scheletro caricato via JS. SPA tipiche stanno sotto 0.02.
_JS_RENDER_TEXT_RATIO_THRESHOLD = 0.05
_JS_RENDER_MIN_HTML_BYTES = 5000  # pagine corte (errore 404, redirect) escluse
# Sotto questa soglia di testo estratto, su HTML sostanziale (>= MIN), la
# pagina e' CONCLUSIVAMENTE non leggibile (SPA non idratata, anti-bot,
# paywall): vale come segnale js_rendered da solo, senza la soglia >=2.
_JS_RENDER_EMPTY_TEXT_BYTES = 64

# Keyword (lowercase) che indicano un PDF rilevante per data extraction.
_PDF_RELEVANT_KEYWORDS = (
    "calendario", "risultati", "classifica", "girone", "giornata",
    "convocazione", "comunicato", "regolamento", "elenco",
)


class _ReaderParser(HTMLParser):
    """Estrae title + meta + body_text dal tag preferito.

    Strategia: registriamo la pos di apertura di ogni tag candidato
    (article/main/body); per ognuno accumuliamo il testo dentro al tag
    saltando i drop tags. Alla fine scegliamo il primo container che
    ha contenuto non vuoto fra article→main→body.
    """
    def __init__(self):
        super().__init__()
        self.title_parts: list[str] = []
        self.in_title = False
        # meta: og:*, description, author
        self.meta: dict[str, str] = {}
        # tag publishing date: <time datetime="...">
        self._time_attr_pending: str | None = None
        # buffer per ogni candidato
        self._buf: dict[str, list[str]] = {k: [] for k in _CONTENT_PREFERENCE}
        # stack di apertura: per sapere quali container sono attivi
        self._open: list[str] = []
        # stack di skip per drop tags
        self._skip_depth = 0
        # lang dell'<html>
        self.lang: str | None = None
        # c2.1: srcs degli iframe (fallback follow se body_text vuoto)
        self.iframe_srcs: list[str] = []
        # c2.2: anchor a documenti PDF/DOC con anchor_text rilevante
        self.linked_documents: list[dict] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        # c2.3: signal di SPA / JS-rendering
        self.has_noscript_warning = False
        self._in_noscript = False
        self._noscript_text: list[str] = []
        self.script_count = 0
        self.has_root_div = False  # <div id="root"> | "app" | "__next" tipici SPA

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "html":
            lang = attrs_d.get("lang")
            if lang:
                self.lang = lang.strip()[:8]
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (attrs_d.get("name") or attrs_d.get("property") or "").lower()
            if name and name in ("description", "author") or name.startswith("og:"):
                content = (attrs_d.get("content") or "").strip()
                if content:
                    self.meta[name] = content[:500]
        elif tag == "time":
            dt = attrs_d.get("datetime")
            if dt and "published" not in self.meta:
                self.meta["published"] = dt.strip()[:32]
        elif tag == "iframe":
            # c2.1: cattura src per fallback follow
            src = (attrs_d.get("src") or "").strip()
            if src:
                self.iframe_srcs.append(src)
            # iframe e' anche in _DROP_TAGS → skip body content
            self._skip_depth += 1
        elif tag == "script":
            self.script_count += 1
            self._skip_depth += 1
        elif tag == "noscript":
            self._in_noscript = True
            self._skip_depth += 1
        elif tag == "div":
            div_id = (attrs_d.get("id") or "").strip().lower()
            if div_id in ("root", "app", "__next", "__nuxt", "main-app"):
                self.has_root_div = True
        elif tag == "a":
            href = (attrs_d.get("href") or "").strip()
            if href and (href.lower().endswith(".pdf") or
                          ".pdf?" in href.lower() or
                          ".pdf#" in href.lower()):
                self._anchor_href = href
                self._anchor_text = []
        elif tag in _DROP_TAGS:
            self._skip_depth += 1
        elif tag in _CONTENT_PREFERENCE:
            self._open.append(tag)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "noscript":
            self._in_noscript = False
            if self._skip_depth > 0:
                self._skip_depth -= 1
            # se il noscript contiene parole chiave "javascript"/"abilitare js",
            # segnala come avviso SPA
            txt = " ".join(self._noscript_text).lower()
            if any(k in txt for k in ("javascript", "abilitare", "enable js",
                                         "browser does not support")):
                self.has_noscript_warning = True
            self._noscript_text = []
        elif tag in ("script", "iframe"):
            # entrambi nel _skip_depth tracking
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag == "a" and self._anchor_href is not None:
            # c2.2: se anchor_text rilevante, registra il PDF
            anchor_text_str = " ".join(" ".join(self._anchor_text).split()).strip()
            anchor_text_lower = anchor_text_str.lower()
            relevance = sum(1 for kw in _PDF_RELEVANT_KEYWORDS
                            if kw in anchor_text_lower)
            self.linked_documents.append({
                "href": self._anchor_href,
                "anchor_text": anchor_text_str[:200],
                "relevance_score": relevance,
                "kind": "pdf",
            })
            self._anchor_href = None
            self._anchor_text = []
        elif tag in _DROP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _CONTENT_PREFERENCE and tag in self._open:
            # rimuove l'ultima occorrenza
            for i in range(len(self._open) - 1, -1, -1):
                if self._open[i] == tag:
                    del self._open[i]
                    break

    def handle_data(self, data):
        # text dentro <noscript> va catturato anche se siamo in skip_depth
        # (per la detection JS-rendering c2.3)
        if self._in_noscript:
            self._noscript_text.append(data)
        if self._skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
            return
        # text dentro un anchor PDF (per anchor_text relevance)
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        # accumula nel container piu' specifico aperto attualmente
        for tag in _CONTENT_PREFERENCE:
            if tag in self._open:
                self._buf[tag].append(data)
                # accumula solo nel piu' specifico (article > main > body)
                # cosi' che article win quando la pagina ne ha uno.
                break

    def title(self) -> str:
        return " ".join("".join(self.title_parts).split()).strip()

    def body_text(self) -> str:
        for tag in _CONTENT_PREFERENCE:
            text = " ".join("".join(self._buf[tag]).split()).strip()
            if text:
                return text[:_BODY_TRIM_CHARS]
        return ""


def _build_opener(cookies_file: str | None):
    handlers = []
    if cookies_file:
        cp = Path(os.path.expanduser(cookies_file))
        if not cp.exists():
            raise FileNotFoundError(f"auth_cookies_file not found: {cp}")
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(str(cp), ignore_discard=True, ignore_expires=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    # Permetti follow redirect (default behavior dell'opener stdlib: gia' fa
    # max 10 redirect; non si imposta limite a 3 con stdlib, ma in pratica
    # la maggior parte dei siti usa <= 3 hop).
    return urllib.request.build_opener(*handlers)


def _classify_http_error(code: int) -> str:
    """Mappa HTTP status → error_class deterministica (ADR 0101)."""
    if code == 403:
        return "forbidden"
    if code == 429:
        return "rate_limited"
    if code == 404:
        return "not_found"
    if 500 <= code < 600:
        return "server_error"
    return "unknown"


def _pdf_entry(pdf_body: bytes, final_url: str) -> tuple[dict | None, dict | None]:
    """Costruisce l'entry per un URL che serve PDF (handoff 12/6/2026).

    Senza handoff i PDF in liste miste da find_urls finivano in failed[]
    (error_class=non_html) e scattava MSG_PARTIAL_ITEM_FAILURE («risultato
    incompleto») su documenti in realta' leggibili. Estrazione condivisa
    con read_urls_pdf via runtime/pdf_extract (§7.3: oggetto unitario).
    Shape entry allineata alle entry HTML (§2.10) + marker `content_kind`.
    """
    try:
        info = extract_pdf_text(pdf_body, trim_chars=_BODY_TRIM_CHARS)
    except ImportError as e:
        return None, {"error": str(e), "error_class": "unknown"}
    except Exception as e:
        return None, {
            "error": f"pdf parse error: {type(e).__name__}: {e}",
            "error_class": "unknown",
        }
    entry = {
        "url": final_url,
        "title": info["title"],
        "body_text": info["body_text"],
        "meta": ({"author": info["author"]} if info.get("author") else {}),
        "lang": None,
        "fetched_at": time.time(),
        "iframe_urls": [],
        "linked_documents": [],
        "js_rendered": False,
        "js_signals": [],
        "notice": None,
        "content_kind": "pdf",
        "n_pages": info["n_pages"],
        "n_pages_read": info["n_pages_read"],
        "used_lib": info["used_lib"],
    }
    return entry, None


def _classify_url_error(reason) -> str:
    """Mappa urllib URLError.reason → error_class (timeout vs network)."""
    if isinstance(reason, TimeoutError):
        return "timeout"
    rs = str(reason).lower()
    if "timed out" in rs or "timeout" in rs:
        return "timeout"
    return "network"


def _invalid_result(error: str, code: str) -> dict:
    """Canonical fail-closed shape for invocation-level input errors."""
    return {
        "ok": False,
        "ok_count": 0,
        "fail_count": 0,
        "entries": [],
        "failed": [],
        "error": error,
        "error_class": "invalid_input",
        "error_code": code,
    }


# Script inline (no src) che CONTENGONO DATI (molte coppie "chiave":valore),
# non codice di framework. Generale: filtro per densita' JSON, non per dominio.
_INLINE_SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>',
                               re.IGNORECASE | re.DOTALL)


def _json_islands_text(html_text: str, cap: int = 16000) -> str:
    """Testo dei dati JSON embedded nell'HTML (SPA hydration / script json /
    __DATA__). Estratto SENZA browser: rende l'importo/indirizzo di una SPA
    leggibili a valle. Soglia densita' (`":` molte volte) per scartare il
    codice JS dei framework. Cap totale per non gonfiare il prompt."""
    out: list = []
    total = 0
    for m in _INLINE_SCRIPT_RE.finditer(html_text or ""):
        s = (m.group(1) or "").strip()
        if s.count('":') < 3:          # non e' un payload-dati → salta (JS code)
            continue
        take = s[:max(0, cap - total)]
        if not take:
            break
        out.append(take)
        total += len(take)
        if total >= cap:
            break
    return ("\n[dati-embedded]\n" + "\n".join(out)) if out else ""


def _fetch_one(url: str, opener, timeout_s: float, max_bytes: int,
               throttle: "HostThrottle | None" = None,
               cache: "HttpCache | None" = None,
               _meta_refresh_hops_left: int = _META_REFRESH_HOP_CAP,
               ) -> tuple[dict | None, dict | None]:
    """Ritorna (entry, None) o (None, {"error": str, "error_class": str}).

    `error_class` (ADR 0101) classifica deterministicamente la causa:
    forbidden|rate_limited|not_found|server_error|timeout|non_html|
    network|unknown. Permette al PLANNER di reagire (regola Z.ter/Z.quater)
    senza re-fetch dello stesso URL.

    Se `throttle` e' fornito, acquisisce uno slot per-host prima del fetch
    e lo rilascia in finally. Sequence: parsing HTML viene eseguito DOPO
    release per non tenere occupato lo slot durante il lavoro CPU-locale.

    Se `cache` e' fornito ed enabled, prima del fetch HTTP tenta lookup
    nel disk-cache (ADR 0105). Hit valido = no HTTP request, body cached
    riusato per il parsing.
    """
    host = urllib.parse.urlparse(url).netloc
    final_url = url
    text: str | None = None
    ctype = ""
    cache_hit = False
    is_pdf = False
    pdf_body: bytes | None = None
    # Meta-refresh hop tracking (8/5/2026): se la response root e' un piccolo
    # `<meta http-equiv="refresh">`, segui il redirect (urllib non lo fa).
    # Inseriamo il follow DOPO il fetch (sotto), questa variabile traccia hop.

    if cache is not None and cache.enabled():
        cached = cache.get(url)
        if cached is not None:
            ctype = cached.get("ctype", "")
            if "text/html" in ctype.lower():
                body = cached.get("body", b"")
                try:
                    text = body.decode("utf-8", errors="replace")
                except Exception:
                    text = body.decode("latin-1", errors="replace")
                final_url = cached.get("url", url)
                cache_hit = True

    if not cache_hit:
        if throttle is not None:
            throttle.acquire(host)
        try:
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "gzip, deflate, identity",
                })
                with opener.open(req, timeout=timeout_s) as resp:
                    ctype = resp.headers.get("Content-Type", "")
                    ctype_l = ctype.lower()
                    # PDF-handoff (12/6/2026, vedi _pdf_entry): riconosci dal
                    # Content-Type, o da path .pdf con ctype generico
                    # (alcuni server servono PDF come octet-stream).
                    url_path = urllib.parse.urlparse(url).path.lower()
                    is_pdf = ("pdf" in ctype_l
                              or ("text/html" not in ctype_l
                                  and url_path.endswith(".pdf")))
                    if not is_pdf and "text/html" not in ctype_l:
                        return None, {
                            "error": _msg("ERR_NON_HTML_CONTENT", ctype=ctype),
                            "error_class": "non_html",
                        }
                    body = resp.read(_PDF_HANDOFF_MAX_BYTES if is_pdf
                                     else max_bytes)
                    # Decompressione body se Content-Encoding (server-side
                    # cache come Varnish gzip-pa anche senza Accept-Encoding).
                    enc = (resp.headers.get("Content-Encoding") or "").lower()
                    resp_headers = dict(resp.headers.items())
                    final_url = resp.geturl()
                if "gzip" in enc:
                    try:
                        body = gzip.GzipFile(fileobj=io.BytesIO(body)).read()
                    except Exception:
                        pass  # fall-through; body raw decode probabilmente
                              # darà testo vuoto → js_rendered detection
                elif "deflate" in enc:
                    try:
                        body = zlib.decompress(body, -zlib.MAX_WBITS)
                    except Exception:
                        try:
                            body = zlib.decompress(body)
                        except Exception:
                            pass
                if is_pdf:
                    # Bytes binari: niente decode/cache (ADR 0105 cachea
                    # solo text/html). Parse dopo il release dello slot.
                    pdf_body = body
                else:
                    try:
                        text = body.decode("utf-8", errors="replace")
                    except (UnicodeDecodeError, LookupError):
                        text = body.decode("latin-1", errors="replace")
                # ADR 0108: registra successo per host_health (resetta backoff).
                if record_response is not None:
                    try:
                        record_response(host, 200)
                    except Exception:
                        pass
                # ADR 0105: scrivi cache solo su success + text/html.
                if not is_pdf and cache is not None and cache.enabled():
                    try:
                        cache.put(final_url, ctype, body, resp_headers)
                    except Exception:
                        pass
            except urllib.error.HTTPError as e:
                # ADR 0108: registra 429/503 per auto-degrade.
                if record_response is not None and e.code in (429, 503):
                    try:
                        record_response(host, e.code)
                        if maybe_block_host is not None:
                            maybe_block_host(host)
                    except Exception:
                        pass
                return None, {
                    "error": f"http error {e.code}: {e.reason}",
                    "error_class": _classify_http_error(e.code),
                }
            except urllib.error.URLError as e:
                return None, {
                    "error": f"url error: {e.reason}",
                    "error_class": _classify_url_error(e.reason),
                }
            except TimeoutError:
                return None, {
                    "error": _msg("ERR_TIMEOUT"),
                    "error_class": "timeout",
                }
            except Exception as e:
                return None, {
                    "error": f"unexpected: {type(e).__name__}: {e}",
                    "error_class": "unknown",
                }
        finally:
            # Rilascia lo slot APPENA fatto il fetch network: il parsing HTML
            # che segue e' lavoro locale CPU-bound, non deve trattenere lo slot.
            if throttle is not None:
                throttle.release(host)

    # PDF-handoff (12/6/2026): parsing CPU-locale DOPO il release dello
    # slot per-host (come il parsing HTML sotto).
    if is_pdf:
        return _pdf_entry(pdf_body or b"", final_url)

    # Meta-refresh follow (8/5/2026, simmetrico al fix di find_urls).
    # Se la pagina e' una landing-redirect (`<meta http-equiv="refresh">`),
    # segui il target prima di parsare (urllib non lo fa per HTTP 30x).
    # Hop cap = 4 (anti-loop) tramite _meta_refresh_hops_left decrementato.
    if text and _meta_refresh_hops_left > 0:
        mr_target = _extract_meta_refresh(text)
        if mr_target:
            new_url = urllib.parse.urljoin(final_url, mr_target)
            if new_url != final_url and new_url != url:
                return _fetch_one(
                    new_url, opener, timeout_s, max_bytes,
                    throttle=throttle, cache=cache,
                    _meta_refresh_hops_left=_meta_refresh_hops_left - 1,
                )

    p = _ReaderParser()
    try:
        p.feed(text)
    except Exception as e:
        return None, {
            "error": f"html parse error: {type(e).__name__}: {e}",
            "error_class": "unknown",
        }
    body_text = p.body_text()
    body_text = html.unescape(body_text)
    title = html.unescape(p.title())

    # c2.3: detection JS-rendering. Pagina probabilmente SPA se:
    # - text/html ratio molto basso E
    # - HTML non microscopico (404 corti escluso) E
    # - presenza di div root SPA O noscript warning O molti script
    js_rendered = False
    js_signals: list[str] = []
    html_bytes = len(text)
    text_bytes = len(body_text)
    if html_bytes >= _JS_RENDER_MIN_HTML_BYTES:
        ratio = text_bytes / html_bytes
        if ratio < _JS_RENDER_TEXT_RATIO_THRESHOLD:
            js_signals.append(f"text/html ratio {ratio:.3f} < threshold")
        if p.has_root_div:
            js_signals.append("root SPA div presente")
        if p.has_noscript_warning:
            js_signals.append("noscript: avviso JS richiesto")
        if p.script_count >= 5 and ratio < 0.10:
            js_signals.append(f"{p.script_count} script + low text ratio")
    js_rendered = len(js_signals) >= 2
    # §2.8 caso degenere conclusivo: HTML sostanziale ma testo ~nullo =
    # estrazione fallita con CERTEZZA, non "poco testo" ambiguo. Un solo
    # segnale basta. Senza questo, una pagina a ratio 0.000 (1 segnale) tornava
    # ok con body vuoto (silent failure) e il planner non attivava il retry
    # js_render -> il sidecar Playwright (gia' presente, ADR 0125) restava
    # inutilizzato e a valle si fabbricavano messaggi vuoti.
    if (html_bytes >= _JS_RENDER_MIN_HTML_BYTES
            and text_bytes < _JS_RENDER_EMPTY_TEXT_BYTES):
        js_rendered = True
        if not any(s.startswith("body vuoto") for s in js_signals):
            js_signals.append(
                f"body vuoto ({text_bytes}b su {html_bytes}b HTML)")

    # c2.2: ordina linked_documents per relevance_score desc, tieni top-10
    linked_docs = sorted(p.linked_documents,
                          key=lambda d: -d.get("relevance_score", 0))
    # Risolvi href relativi a URL assoluti
    resolved_docs: list[dict] = []
    for d in linked_docs[:10]:
        try:
            d["url"] = urllib.parse.urljoin(final_url, d.get("href", ""))
        except Exception:
            d["url"] = d.get("href", "")
        resolved_docs.append(d)

    # c2.1: iframe srcs (assolutizzati). Il fallback iframe-following
    # avviene a livello invoke() se body_text e' vuoto.
    iframe_urls: list[str] = []
    for src in p.iframe_srcs[:5]:
        try:
            iframe_urls.append(urllib.parse.urljoin(final_url, src))
        except Exception:
            iframe_urls.append(src)

    notice_parts: list[str] = []
    if js_rendered:
        notice_parts.append(
            "Pagina probabilmente render lato JS (SPA): il contenuto reale "
            "viene caricato dal browser dopo l'HTML iniziale. Metnos non "
            "esegue JavaScript, quindi puoi vedere solo lo scheletro. "
            "Signal: " + "; ".join(js_signals)
        )
    if iframe_urls and len(body_text.strip()) < 200:
        notice_parts.append(
            f"Pagina contiene {len(iframe_urls)} iframe; il contenuto utile "
            f"e' probabilmente dentro l'iframe. Riprova con read_urls_html "
            f"sull'URL: {iframe_urls[0]}"
        )

    entry = {
        "url": final_url,
        "title": title,
        # JSON-island appesa SOLO all'output (dopo la detection js_rendered e il
        # fallback iframe, che usano il testo VISIBILE): molte SPA spediscono i
        # DATI come JSON embedded nell'HTML → leggibili a valle (extract_entries)
        # senza renderizzare JS. Generale §7.3. Bug 5303699e (bollette SPA).
        "body_text": body_text + _json_islands_text(text),
        "meta": p.meta,
        "lang": p.lang,
        "fetched_at": time.time(),
        "iframe_urls": iframe_urls,
        "linked_documents": resolved_docs,
        "js_rendered": js_rendered,
        "js_signals": js_signals,
        "notice": "; ".join(notice_parts) if notice_parts else None,
    }
    # ADR 0101: se SPA rilevata, esponi error_class=js_rendered sull'entry
    # cosi' il PLANNER (regola Z.ter) reagisce anche su success-con-skeleton.
    if js_rendered:
        entry["error_class"] = "js_rendered"
    return entry, None


def _fetch_one_with_retry(url: str, opener, timeout_s: float, max_bytes: int,
                          throttle: "HostThrottle | None" = None,
                          cache: "HttpCache | None" = None):
    """Retry only transient transport failures, with a small bounded delay."""
    last = (None, {"error": "unknown", "error_class": "unknown"})
    for attempt in range(3):
        last = _fetch_one(url, opener, timeout_s, max_bytes, throttle, cache)
        entry, error = last
        if entry is not None or not isinstance(error, dict):
            return last
        if error.get("error_class") not in {"network", "timeout"}:
            return last
        if attempt < 2:
            time.sleep(0.25 * (attempt + 1))
    return last


def _invoke_default(args: dict) -> dict:
    """Implementazione default httpx (urllib). Mantenuta nel modulo
    executor per permettere ai test di patchare `_playwright_client`,
    `record_response`, ecc. tramite l'oggetto modulo.

    Il dispatcher `invoke()` la chiama via `backends.urls.httpx_default`.
    """
    urls = args.get("urls")
    if urls is None:
        return _invalid_result(_msg("ERR_ARG_MISSING", arg="urls"),
                               "urls_missing")
    if not isinstance(urls, list):
        return _invalid_result(
            _msg("ERR_ARG_NOT_LIST_OF", arg="urls", of="strings"),
            "urls_not_array",
        )

    auth_cookies_file = args.get("auth_cookies_file")
    if (auth_cookies_file is not None
            and (not isinstance(auth_cookies_file, str)
                 or not auth_cookies_file.strip())):
        return _invalid_result(
            _msg("ERR_ARG_NOT_NONEMPTY_STRING", arg="auth_cookies_file"),
            "auth_cookies_file_invalid",
        )
    timeout_raw = args.get("timeout_s", 10.0)
    if (isinstance(timeout_raw, bool)
            or not isinstance(timeout_raw, (int, float))
            or not 1.0 <= float(timeout_raw) <= 60.0):
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="timeout_s",
                 reason="expected a number in range 1..60"),
            "timeout_invalid",
        )
    timeout_s = float(timeout_raw)
    max_bytes_raw = args.get("max_bytes", _DEFAULT_MAX_BYTES)
    if (isinstance(max_bytes_raw, bool)
            or not isinstance(max_bytes_raw, int)
            or max_bytes_raw < 0):
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="max_bytes",
                 reason="expected a non-negative integer"),
            "max_bytes_invalid",
        )
    max_bytes = max_bytes_raw
    if max_bytes <= 0:
        max_bytes = _DEFAULT_MAX_BYTES
    # ADR 0105: HTTP cache disk-based. cache_ttl_s=0 disabilita.
    cache_ttl_raw = args.get("cache_ttl_s", DEFAULT_TTL_S)
    if (isinstance(cache_ttl_raw, bool)
            or not isinstance(cache_ttl_raw, int)
            or cache_ttl_raw < 0):
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="cache_ttl_s",
                 reason="expected a non-negative integer"),
            "cache_ttl_invalid",
        )
    cache_ttl_s = cache_ttl_raw
    cache = HttpCache(ttl_s=cache_ttl_s) if cache_ttl_s > 0 else None

    follow_iframes = args.get("follow_iframes", True)
    if not isinstance(follow_iframes, bool):
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="follow_iframes",
                 reason="expected a boolean"),
            "follow_iframes_not_boolean",
        )
    # ADR 0125: opt-in JS-rendering via sidecar Playwright. Default false.
    js_render = args.get("js_render", False)
    if not isinstance(js_render, bool):
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="js_render",
                 reason="expected a boolean"),
            "js_render_not_boolean",
        )

    if not urls:
        return {"ok": True, "ok_count": 0, "fail_count": 0,
                "entries": [], "failed": []}

    try:
        opener = _build_opener(auth_cookies_file)
    except FileNotFoundError as e:
        return {
            **_invalid_result(str(e), "auth_cookies_file_not_found"),
            "error_class": "not_found",
        }
    except Exception as e:
        return {
            **_invalid_result(_msg("ERR_OP_FAILED", reason=str(e)),
                              "auth_cookies_file_invalid"),
            "error_class": "invalid_content",
        }

    # ADR 0125: opt-in JS-rendering via sidecar Playwright. Default false
    # per backwards compat con throughput. Quando true:
    #   - entries con `error_class="js_rendered"` (SPA detected dopo fetch)
    #     vengono ri-richieste al sidecar.
    #   - failed con `error_class="js_rendered"` analogamente.
    # Se il sidecar e' down, lasciamo lo stato pre-existing (degrade graceful).

    # Pre-validate + assegna indice originale per output deterministico.
    valid_jobs: list[tuple[int, str]] = []
    failed: list[dict] = []
    for i, url in enumerate(urls):
        if not isinstance(url, str) or not url:
            failed.append({"url": str(url), "error": _msg("ERR_INVALID_URL"),
                           "error_class": "invalid_input",
                           "error_code": "invalid_url", "_idx": i})
            continue
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            parsed = None
        if (parsed is None or parsed.scheme.lower() not in {"http", "https"}
                or not parsed.hostname):
            failed.append({"url": url, "error": _msg("ERR_INVALID_URL"),
                           "error_class": "invalid_input",
                           "error_code": "invalid_url", "_idx": i})
            continue
        valid_jobs.append((i, url))

    # Parallel fetch primario (ADR 0100). Throttle per-host conservativo per
    # evitare di stressare un singolo dominio quando l'utente passa molti
    # URL stesso host. Se 1 solo URL, fall-back a sync (no overhead pool).
    def _failed_entry(url: str, err, i: int) -> dict:
        """Normalizza err (dict|str|None) in failed-entry con error_class."""
        if isinstance(err, dict):
            return {"url": url, "error": err.get("error", "unknown"),
                    "error_class": err.get("error_class", "unknown"),
                    "error_code": err.get("error_code") or (
                        "url_" + str(err.get("error_class", "unknown"))),
                    "_idx": i}
        return {"url": url, "error": str(err) if err else "unknown",
                "error_class": "unknown", "error_code": "url_unknown",
                "_idx": i}

    entries_indexed: list[tuple[int, dict]] = []
    if len(valid_jobs) == 1:
        i, url = valid_jobs[0]
        ent, err = _fetch_one_with_retry(
            url, opener, timeout_s, max_bytes, None, cache)
        if ent is None:
            failed.append(_failed_entry(url, err, i))
        else:
            entries_indexed.append((i, ent))
    elif valid_jobs:
        throttle = HostThrottle(per_host_limit=_PER_HOST_MAX)
        workers = min(_GLOBAL_MAX, len(valid_jobs))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_one_with_retry, url, opener,
                              timeout_s, max_bytes,
                              throttle, cache): (i, url)
                    for i, url in valid_jobs}
            for fut in as_completed(futs):
                i, url = futs[fut]
                try:
                    ent, err = fut.result()
                except Exception as e:
                    ent, err = None, {
                        "error": f"worker error: {type(e).__name__}: {e}",
                        "error_class": "unknown",
                    }
                if ent is None:
                    failed.append(_failed_entry(url, err, i))
                else:
                    entries_indexed.append((i, ent))

    # Stage 2: iframe-follow same-host. Sequenziale per-entry (1 sub-fetch
    # per pagina, no chain). Parallelizzato per-pagina.
    if follow_iframes and entries_indexed:
        followups: list[tuple[int, str]] = []  # (entry_idx_in_list, iframe_url)
        for pos, (_, ent) in enumerate(entries_indexed):
            if len(ent.get("body_text", "").strip()) >= 200:
                continue
            if not ent.get("iframe_urls"):
                continue
            seed_host = urllib.parse.urlparse(ent["url"]).netloc
            for iframe_url in ent["iframe_urls"]:
                ihost = urllib.parse.urlparse(iframe_url).netloc
                if ihost and ihost == seed_host:
                    followups.append((pos, iframe_url))
                    break  # 1 follow max per pagina
        if followups:
            throttle2 = HostThrottle(per_host_limit=_PER_HOST_MAX)
            workers = min(_GLOBAL_MAX, len(followups))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_fetch_one, ifu, opener, timeout_s,
                                  max_bytes, throttle2, cache): (pos, ifu)
                        for pos, ifu in followups}
                for fut in as_completed(futs):
                    pos, ifu = futs[fut]
                    try:
                        sub_ent, _ = fut.result()
                    except Exception:
                        sub_ent = None
                    if sub_ent and sub_ent.get("body_text", "").strip():
                        ent = entries_indexed[pos][1]
                        ent["body_text"] = sub_ent["body_text"]
                        ent["iframe_followed"] = ifu
                        ent["notice"] = (
                            f"Contenuto estratto da iframe same-host: {ifu}"
                        )

    # Stage 3 (ADR 0125): JS-rendering via sidecar Playwright. Bersaglia:
    #   - entries con `error_class="js_rendered"` (SPA detected post-fetch);
    #   - failed con `error_class="js_rendered"` (urllib non riusciva a
    #     scaricare HTML utile e abbiamo classificato come SPA).
    # Il sidecar e' single-instance §7.4: rendering sequenziale.
    #
    # §7.9 AUTO-ESCALATION deterministica: se il fetch httpx ha prodotto pagine
    # SPA e il chiamante NON ha gia' chiesto js_render, escala da solo quando il
    # sidecar e' UP. Necessario perche' il path attivo (engine-v2 plan-then-
    # execute) emette l'intera pipeline in una sola call e NON ha un punto di
    # retry LLM per onorare la regola planner js_rendered_retry: il fix deve
    # vivere nell'executor che rileva la SPA, non nel planner. Degrada con
    # grazia se il sidecar e' giu' (entries restano flaggate error_class).
    _has_spa = (any(e.get("error_class") == "js_rendered"
                    for _, e in entries_indexed)
                or any(f.get("error_class") == "js_rendered" for f in failed))
    auto_escalate = (not js_render) and _has_spa
    do_render = js_render or auto_escalate
    js_render_count = 0
    js_render_attempted = 0
    js_render_sidecar_up = False
    if do_render and _playwright_client is not None:
        js_render_sidecar_up = _playwright_client.is_up()
        if js_render_sidecar_up:
            # Bersagli da entries (modifica in-place via pos).
            for pos, (idx, ent) in enumerate(entries_indexed):
                if ent.get("error_class") != "js_rendered":
                    continue
                target_url = ent.get("url") or urls[idx] if idx < len(urls) else None
                if not target_url:
                    continue
                js_render_attempted += 1
                resp = _playwright_client.render(target_url)
                if not resp.get("ok"):
                    # Sidecar fallito: lascia entry as-is + annota error_class
                    # dal sidecar (timeout/network/...). Non sovrascrive un
                    # rendering riuscito.
                    ent["js_render_error"] = resp.get("error", "unknown")
                    ent["js_render_error_class"] = resp.get(
                        "error_class", "unknown")
                    continue
                # Success: aggiorna entry con HTML/text renderizzati.
                ent["body_text"] = (resp.get("body_text") or "")[
                    :_BODY_TRIM_CHARS]
                ent["body_html_rendered"] = True
                ent["title"] = resp.get("title") or ent.get("title", "")
                ent["url"] = resp.get("final_url") or ent["url"]
                ent["render_ms"] = resp.get("render_ms")
                # Pulisci il marker SPA: ora la pagina e' stata renderizzata
                # davvero. ADR 0101 honest: error_class non piu' applicabile.
                ent.pop("error_class", None)
                ent["js_rendered"] = False
                ent["js_signals"] = []
                ent["js_rendered_via_sidecar"] = True
                # Riformula notice per chiarire al PLANNER cosa e' successo.
                ent["notice"] = (
                    "Pagina renderizzata via sidecar Playwright "
                    "(JS-rendering opt-in, ADR 0125)."
                )
                js_render_count += 1
            # Bersagli da failed: promuove a entries quelli che ora vengono
            # renderizzati con successo.
            promoted: list[int] = []
            for fpos, fail in enumerate(failed):
                if fail.get("error_class") != "js_rendered":
                    continue
                target_url = fail.get("url")
                if not target_url:
                    continue
                js_render_attempted += 1
                resp = _playwright_client.render(target_url)
                if not resp.get("ok"):
                    fail["js_render_error"] = resp.get("error", "unknown")
                    fail["js_render_error_class"] = resp.get(
                        "error_class", "unknown")
                    continue
                # Promote failed → entries.
                new_entry = {
                    "url": resp.get("final_url") or target_url,
                    "title": resp.get("title") or "",
                    "body_text": (resp.get("body_text") or "")[
                        :_BODY_TRIM_CHARS],
                    "body_html_rendered": True,
                    "meta": {},
                    "lang": None,
                    "fetched_at": time.time(),
                    "iframe_urls": [],
                    "linked_documents": [],
                    "js_rendered": False,
                    "js_signals": [],
                    "js_rendered_via_sidecar": True,
                    "render_ms": resp.get("render_ms"),
                    "notice": "Pagina renderizzata via sidecar Playwright "
                              "(JS-rendering opt-in, ADR 0125).",
                }
                entries_indexed.append((fail.get("_idx", 999), new_entry))
                promoted.append(fpos)
                js_render_count += 1
            # Rimuovi promossi da failed (in reverse per indici stabili).
            for fpos in reversed(promoted):
                failed.pop(fpos)

    # Riordina per indice originale (deterministico, indipendente da ordine
    # di completamento dei worker).
    entries_indexed.sort(key=lambda t: t[0])
    entries: list[dict] = [e for _, e in entries_indexed]
    failed.sort(key=lambda d: d.get("_idx", 0))
    for d in failed:
        d.pop("_idx", None)

    # §2.8/§2.1/§2.6: successo PARZIALE = successo. Se almeno una pagina e'
    # stata letta (ok_count>0) lo step e' ok=True e le entries fluiscono al
    # consumer (summary/extract); i fallimenti (429/404/non_html di SINGOLI
    # URL esterni) restano visibili in fail_count/failed (§2.7). ok=False SOLO
    # se ZERO contenuto E c'erano URL da leggere (fallimento totale onesto).
    # Bug pre-fix: `ok = len(failed)==0` scartava 17 pagine buone per 1 URL 429
    # → il planner trattava lo step come fallito → loop_break/resa.
    result = {
        "ok": len(entries) > 0 or len(failed) == 0,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
    }
    if entries and failed:
        result["partial"] = True
    elif failed:
        result["error"] = failed[0].get("error") or "unknown"
        result["error_class"] = failed[0].get("error_class") or "unknown"
        result["error_code"] = failed[0].get("error_code") or "url_unknown"
    # Telemetria JS-render (ADR 0125): esposta quando il rendering e' stato
    # ingaggiato (opt-in esplicito O auto-escalation §7.9). Turn senza SPA
    # restano puliti.
    if do_render:
        result["js_render_count"] = js_render_count
        result["js_render_attempted"] = js_render_attempted
        result["js_render_sidecar_available"] = js_render_sidecar_up
        result["js_render_auto"] = auto_escalate
    return result


# --- Dispatcher (refactor 13/5/2026, ADR pending) -------------------------
# Routing client → backend builtin in `runtime/backends/web/`. Pattern
# allineato a send_messages/read_messages/find_files (§2.5, §7.2). I test
# che patchano nomi modulo (`_playwright_client`, `record_response`, etc.)
# continuano a funzionare perche' `_invoke_default` resta in questo file.
_DEFAULT_CLIENT = "httpx"

def _resolve_backend(client: str):
    """Lazy import per evitare circular (backends.urls.httpx_default importa
    questo modulo e ne lega `_invoke_default` come implementazione)."""
    if client == "httpx":
        from backends.urls import httpx_default
        return httpx_default
    if client == "playwright":
        from backends.urls import playwright_stub
        return playwright_stub
    return None


def invoke(args: dict) -> dict:
    if not isinstance(args, dict):
        return _invalid_result(
            _msg("ERR_ARGS_NOT_OBJECT"), "args_not_object")
    client = args.get("client") or _DEFAULT_CLIENT
    if not isinstance(client, str):
        return _invalid_result(
            _msg("ERR_ARG_NOT_STRING", arg="client"), "client_not_string")
    backend = _resolve_backend(client)
    if backend is None:
        return _invalid_result(
            _msg("ERR_NOT_APPLICABLE", what=f"client {client!r}"),
            "client_unsupported",
        )
    return backend.read_html(args)


def main():
    run_stdio(invoke)


if __name__ == "__main__":
    main()
