#!/usr/bin/env python3
"""http_cache — cache disk-based per fetch HTTP (ADR 0105).

Usata da `read_urls_html` (e in prospettiva `read_urls_pdf` / `find_urls`)
per evitare re-fetch di pagine gia' scaricate di recente.

Storage:
    ~/.cache/metnos/http/<sha256(url)[:2]>/<sha256(url)>.json

Sharding: prime 2 hex char → ~256 dirs, evita 1 dir gigante.

TTL:
- Default 900 s (15 min) — riprese rapide, dialoghi multi-turn.
- Override env `METNOS_HTTP_CACHE_TTL_S` o arg per-call `cache_ttl_s`.
- `cache_ttl_s=0` disabilita la cache (no read, no write).

Cache key:
- sha256(url canonical) dove canonical = lowercase netloc, no default port,
  no fragment.

Cache value JSON:
    {
      "url": str,            # url originale (post canonical)
      "ts": float,           # epoch seconds
      "ctype": str,          # Content-Type
      "body_b64": str,       # body decompresso (utf-8 text), base64
      "headers": dict        # subset utile (Content-Encoding, ...)
    }

API:
    HttpCache(ttl_s=DEFAULT_TTL_S)
        .get(url) -> dict | None      # None se miss/scaduto/disabled
        .put(url, ctype, body, headers) -> None
        .clear_older_than(seconds)    # used by weekly cleanup

Cleanup: chiamato dal scheduler weekly (vedi runtime/recurring_tasks.py).
Determinismo §7.9: niente LLM, write atomico via tmp+rename.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

CACHE_ROOT = Path.home() / ".cache" / "metnos" / "http"
DEFAULT_TTL_S = int(os.environ.get("METNOS_HTTP_CACHE_TTL_S", "900"))
CLEANUP_OLDER_THAN_S = 7 * 24 * 3600  # 7 giorni


def _canonical_url(url: str) -> str:
    """Lowercase netloc, strip default port (80/443), strip fragment."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return url
    netloc = (p.hostname or "").lower()
    if p.port and not (
        (p.scheme == "http" and p.port == 80)
        or (p.scheme == "https" and p.port == 443)
    ):
        netloc = f"{netloc}:{p.port}"
    return urllib.parse.urlunsplit((p.scheme.lower(), netloc, p.path,
                                     p.query, ""))


def _key_for(url: str) -> str:
    canon = _canonical_url(url)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _path_for(key: str) -> Path:
    return CACHE_ROOT / key[:2] / f"{key}.json"


class HttpCache:
    """Cache HTTP disk-based con TTL.

    `ttl_s=0` disabilita: get() ritorna sempre None, put() e' no-op.
    """

    def __init__(self, ttl_s: int = DEFAULT_TTL_S):
        self.ttl_s = max(0, int(ttl_s))

    def enabled(self) -> bool:
        return self.ttl_s > 0

    def get(self, url: str) -> Optional[dict]:
        """Ritorna dict cached se hit valido, None altrimenti.

        Dict ha chiavi: url, ts, ctype, body (bytes decoded), headers.
        """
        if not self.enabled():
            return None
        key = _key_for(url)
        p = _path_for(key)
        if not p.is_file():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            return None
        ts = float(rec.get("ts", 0))
        if (time.time() - ts) > self.ttl_s:
            return None  # scaduto, no cleanup qui (lazy via weekly task)
        try:
            body = base64.b64decode(rec.get("body_b64", ""))
        except Exception:
            return None
        return {
            "url": rec.get("url", url),
            "ts": ts,
            "ctype": rec.get("ctype", ""),
            "body": body,
            "headers": rec.get("headers", {}),
        }

    def put(self, url: str, ctype: str, body: bytes,
            headers: Optional[dict] = None) -> None:
        """Scrive cache entry. No-op se ttl_s=0. Atomic write."""
        if not self.enabled():
            return
        key = _key_for(url)
        p = _path_for(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        rec = {
            "url": _canonical_url(url),
            "ts": time.time(),
            "ctype": ctype or "",
            "body_b64": base64.b64encode(body or b"").decode("ascii"),
            "headers": dict(headers or {}),
        }
        tmp = p.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            os.rename(tmp, p)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def clear_older_than(self, seconds: int = CLEANUP_OLDER_THAN_S) -> int:
        """Rimuove entries piu' vecchie di `seconds`. Ritorna count rimosse."""
        if not CACHE_ROOT.is_dir():
            return 0
        now = time.time()
        removed = 0
        for shard in CACHE_ROOT.iterdir():
            if not shard.is_dir():
                continue
            for entry in shard.iterdir():
                if not entry.is_file() or entry.suffix != ".json":
                    continue
                try:
                    age = now - entry.stat().st_mtime
                except Exception:
                    continue
                if age > seconds:
                    try:
                        entry.unlink()
                        removed += 1
                    except Exception:
                        pass
        return removed


def cleanup_weekly() -> int:
    """Helper: chiamabile dal scheduler weekly. Rimuove >7d, ritorna count."""
    return HttpCache(ttl_s=DEFAULT_TTL_S).clear_older_than(CLEANUP_OLDER_THAN_S)


if __name__ == "__main__":
    # smoke
    c = HttpCache(ttl_s=60)
    c.put("https://example.com/foo", "text/html", b"<html>hi</html>")
    print(c.get("https://example.com/foo"))
    print("removed:", c.clear_older_than(0))
