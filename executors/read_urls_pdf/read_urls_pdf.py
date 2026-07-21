#!/usr/bin/env python3
"""read_urls_pdf — fetch + estrazione testo da URL che servono PDF.

Scarica una lista di URL e ne estrae il testo via `runtime/pdf_extract`
(pypdf, fallback pdfminer.six — helper CONDIVISO con il PDF-handoff di
read_urls_html, §7.3). Ritorna entries con `body_text`, `title`,
`author`, `n_pages`. Skippa Content-Type non-PDF e file > max_bytes.

OCR fallback: hook ma non implementato (richiede Tesseract via pdftoppm
+ tesseract; piu' costoso). `ocr_fallback=true` produrra' una nota di
escalation invece dell'OCR.

Cookie: come read_urls_html, supporta `auth_cookies_file` Mozilla.

Output: entries=[{url, title, body_text, author?, n_pages, fetched_at}]
        + ok_count, fail_count, failed=[{url, error}].
"""
from __future__ import annotations

import http.cookiejar
import json
import multiprocessing
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Throttle condiviso (ADR 0103) — modulo runtime/host_throttle.py.
sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402
from executor_helpers import run_stdio  # noqa: E402
from host_throttle import HostThrottle  # noqa: E402
# Estrazione PDF condivisa (§7.3) — re-export _has_* per i test esistenti.
from pdf_extract import (  # noqa: E402
    extract_pdf_text as _extract_pdf_text,
    has_pypdf as _has_pypdf,
    has_pdfminer as _has_pdfminer,
)


USER_AGENT = "metnos-crawler/1.1 (+contact@metnos.com)"
_DEFAULT_MAX_BYTES = 20_000_000  # 20 MB cap per PDF
_BODY_TRIM_CHARS = 200_000  # 200 KB di testo (PDF lunghi possono essere copiosi)

# Parallelismo (ADR 0100). I/O-net dominante; il parsing pypdf rilascia il
# GIL parzialmente (legge bytes), quindi thread sufficienti.
_GLOBAL_MAX = int(os.environ.get(
    "METNOS_READ_URLS_GLOBAL_MAX",
    min(16, max(1, multiprocessing.cpu_count()) * 2)
))
_PER_HOST_MAX = int(os.environ.get("METNOS_READ_URLS_PER_HOST", "4"))


def _build_opener(cookies_file: str | None):
    handlers = []
    if cookies_file:
        cp = Path(os.path.expanduser(cookies_file))
        if not cp.exists():
            raise FileNotFoundError(f"auth_cookies_file not found: {cp}")
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(str(cp), ignore_discard=True, ignore_expires=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    return urllib.request.build_opener(*handlers)


def _fetch_one(url: str, opener, timeout_s: float, max_bytes: int,
               max_pages: int, ocr_fallback: bool,
               throttle: "HostThrottle | None" = None) -> tuple[dict | None, str | None]:
    host = urllib.parse.urlparse(url).netloc
    if throttle is not None:
        throttle.acquire(host)
    try:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout_s) as resp:
                ctype = resp.headers.get("Content-Type", "").lower()
                # Accettiamo application/pdf E content disposition con .pdf
                if "pdf" not in ctype:
                    # fallback: guarda l'URL — alcuni server servono PDF con
                    # generic content-type
                    if not url.lower().endswith(".pdf"):
                        return None, f"non-pdf content-type: {ctype}"
                body = resp.read(max_bytes)
                final_url = resp.geturl()
        except urllib.error.HTTPError as e:
            return None, f"http error {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return None, f"url error: {e.reason}"
        except TimeoutError:
            return None, f"timeout after {timeout_s}s"
        except Exception as e:
            return None, f"unexpected: {type(e).__name__}: {e}"
    finally:
        # Rilascia lo slot subito dopo il fetch network: il parsing PDF e'
        # CPU-locale e non deve trattenere lo slot per-host.
        if throttle is not None:
            throttle.release(host)

    # Parse in-memory (helper condiviso runtime/pdf_extract, §7.3).
    try:
        info = _extract_pdf_text(body, max_pages=max_pages,
                                 trim_chars=_BODY_TRIM_CHARS)
    except ImportError as e:
        return None, str(e)
    except Exception as e:
        return None, f"pdf parse error: {type(e).__name__}: {e}"
    # OCR fallback hook: se body_text vuoto e flag attivo, segnaliamo
    # che l'OCR sarebbe necessario ma non e' implementato qui.
    if not info["body_text"].strip() and ocr_fallback:
        info["needs_ocr"] = True
        info["ocr_note"] = (
            "PDF parser ha estratto 0 char di testo: probabile PDF "
            "scansionato. OCR non implementato in read_urls_pdf; "
            "considera read_files_ocr dopo download manuale."
        )
    return {
        "url": final_url,
        "title": info["title"],
        "author": info["author"],
        "body_text": info["body_text"],
        "n_pages": info["n_pages"],
        "n_pages_read": info["n_pages_read"],
        "used_lib": info["used_lib"],
        "fetched_at": time.time(),
        **({"needs_ocr": True, "ocr_note": info["ocr_note"]}
           if info.get("needs_ocr") else {}),
    }, None


def _invoke_default(args: dict) -> dict:
    """Implementazione default httpx (urllib). Il dispatcher `invoke()`
    instrada qui via `backends.urls.httpx_default`."""
    urls = args.get("urls")
    if isinstance(urls, str):
        urls = [urls]
    if urls is None:
        urls = []
    if not isinstance(urls, list):
        return {"ok": False, "error": _msg("ERR_ARG_NOT_LIST_OF", arg="urls", of="strings")}

    auth_cookies_file = args.get("auth_cookies_file")
    timeout_s = float(args.get("timeout_s", 15.0))
    max_bytes = int(args.get("max_bytes", _DEFAULT_MAX_BYTES))
    max_pages = int(args.get("max_pages_per_doc", 100))
    ocr_fallback = bool(args.get("ocr_fallback", False))
    if max_bytes <= 0:
        max_bytes = _DEFAULT_MAX_BYTES
    if max_pages <= 0:
        max_pages = 100

    if not urls:
        return {"ok": True, "ok_count": 0, "fail_count": 0,
                "entries": [], "failed": []}

    try:
        opener = _build_opener(auth_cookies_file)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}

    valid_jobs: list[tuple[int, str]] = []
    failed: list[dict] = []
    for i, url in enumerate(urls):
        if not isinstance(url, str) or not url:
            failed.append({"url": str(url), "error": _msg("ERR_INVALID_URL"), "_idx": i})
            continue
        valid_jobs.append((i, url))

    # Parallel fetch (ADR 0100). 1 URL → sync, N URL → ThreadPool con
    # throttle per-host.
    entries_indexed: list[tuple[int, dict]] = []
    if len(valid_jobs) == 1:
        i, url = valid_jobs[0]
        ent, err = _fetch_one(url, opener, timeout_s, max_bytes,
                              max_pages, ocr_fallback, None)
        if ent is None:
            failed.append({"url": url, "error": err or "unknown", "_idx": i})
        else:
            entries_indexed.append((i, ent))
    elif valid_jobs:
        throttle = HostThrottle(per_host_limit=_PER_HOST_MAX)
        workers = min(_GLOBAL_MAX, len(valid_jobs))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_fetch_one, url, opener, timeout_s, max_bytes,
                              max_pages, ocr_fallback, throttle): (i, url)
                    for i, url in valid_jobs}
            for fut in as_completed(futs):
                i, url = futs[fut]
                try:
                    ent, err = fut.result()
                except Exception as e:
                    ent, err = None, f"worker error: {type(e).__name__}: {e}"
                if ent is None:
                    failed.append({"url": url, "error": err or "unknown",
                                   "_idx": i})
                else:
                    entries_indexed.append((i, ent))

    entries_indexed.sort(key=lambda t: t[0])
    entries = [e for _, e in entries_indexed]
    failed.sort(key=lambda d: d.get("_idx", 0))
    for d in failed:
        d.pop("_idx", None)

    # §2.8/§2.1: successo PARZIALE = successo (fetch remoto, fallimenti di
    # singoli URL esterni sono la norma). ok=False solo se ZERO contenuto E
    # c'erano URL. Fallimenti visibili in fail_count/failed (§2.7).
    result = {
        "ok": len(entries) > 0 or len(failed) == 0,
        "ok_count": len(entries),
        "fail_count": len(failed),
        "entries": entries,
        "failed": failed,
    }
    if entries and failed:
        result["partial"] = True
    return result


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
    return backend.read_pdf(args)


def main():
    run_stdio(invoke, default=str)


if __name__ == "__main__":
    main()
