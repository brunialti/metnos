#!/usr/bin/env python3
"""get_urls — executor Metnos: HTTP GET/HEAD, entries-pattern output."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.environ.get("METNOS_RUNTIME") or next(
    str(p / "runtime") for p in Path(__file__).resolve().parents
    if (p / "runtime" / "config.py").is_file()))
from messages import get as _msg  # noqa: E402


USER_AGENT = "Metnos/1.1 web_fetch"
MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB cap di sicurezza


def _fetch_one(url: str, method: str, timeout: int) -> dict:
    """Fetch a single URL. Returns either an entry dict (success) or
    a failed dict with url/error (failure)."""
    if not url:
        return {"failed": True, "url": "", "error": _msg("ERR_ARG_MISSING", arg="url")}
    try:
        parsed = urlparse(url)
    except Exception as e:
        return {"failed": True, "url": url, "error": _msg("ERR_ARG_INVALID", arg="url", reason=str(e))}
    if parsed.scheme not in ("http", "https"):
        return {"failed": True, "url": url,
                "error": _msg("ERR_NOT_APPLICABLE", what=f"scheme '{parsed.scheme}'")}

    req = urllib.request.Request(
        url, method=method, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read(MAX_BODY_BYTES) if method == "GET" else b""
            text = None
            if body:
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
            return {
                "url": url,
                "method": method,
                "status_code": status,
                "host": parsed.hostname,
                "body_text": text if text is not None else "",
                "bytes": len(body),
                "content_type": resp.headers.get("Content-Type", ""),
                "binary": text is None and len(body) > 0,
            }
    except urllib.error.HTTPError as e:
        return {"failed": True, "url": url,
                "error": f"http error {e.code}: {e.reason}",
                "status_code": e.code}
    except urllib.error.URLError as e:
        return {"failed": True, "url": url, "error": f"url error: {e.reason}"}
    except TimeoutError:
        return {"failed": True, "url": url, "error": _msg("ERR_TIMEOUT")}
    except Exception as e:
        return {"failed": True, "url": url,
                "error": f"unexpected: {type(e).__name__}: {e}"}


def invoke(args):
    method = args.get("method", "GET")
    timeout = args.get("timeout_s", 10)

    if method not in ("GET", "HEAD"):
        return {"ok": False, "entries": [], "ok_count": 0, "fail_count": 1,
                "failed": [{"url": "", "error": _msg("ERR_NOT_APPLICABLE", what=f"method '{method}'")}],
                "summary": _msg("ERR_NOT_APPLICABLE", what=f"method '{method}'")}

    # Accept either `url` (singular, manifest contract) or `urls` (vectorial).
    urls_arg = args.get("urls")
    if urls_arg is None:
        url = args.get("url")
        urls = [url] if url else []
    elif isinstance(urls_arg, list):
        urls = list(urls_arg)
    else:
        urls = [str(urls_arg)]

    if not urls:
        return {"ok": False, "entries": [], "ok_count": 0, "fail_count": 1,
                "failed": [{"url": "", "error": _msg("ERR_ARG_MISSING", arg="url")}],
                "summary": _msg("ERR_ARG_MISSING", arg="url")}

    entries: list = []
    failed: list = []
    for u in urls:
        r = _fetch_one(u, method, timeout)
        if r.get("failed"):
            failed.append({k: v for k, v in r.items() if k != "failed"})
        else:
            entries.append(r)

    ok_count = len(entries)
    fail_count = len(failed)
    ok = ok_count > 0 and fail_count == 0

    if ok_count == 1 and fail_count == 0:
        e = entries[0]
        summary = (f"{e['method']} {e['url']} → HTTP {e['status_code']}, "
                   f"{e['bytes']} bytes ({e['content_type']})")
    elif ok_count == 0:
        summary = f"all {fail_count} URL(s) failed"
    else:
        summary = f"{ok_count} URL(s) fetched, {fail_count} failed"

    return {
        "ok": ok,
        "entries": entries,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "failed": failed,
        "summary": summary,
    }


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        result = {"ok": False, "entries": [], "ok_count": 0, "fail_count": 1,
                  "failed": [{"url": "", "error": _msg("ERR_EMPTY_INPUT")}],
                  "summary": _msg("ERR_EMPTY_INPUT")}
    else:
        try:
            args = json.loads(raw)
            result = invoke(args)
        except json.JSONDecodeError as e:
            result = {"ok": False, "entries": [], "ok_count": 0, "fail_count": 1,
                      "failed": [{"url": "", "error": _msg("ERR_JSON_INVALID")}],
                      "summary": f"invalid input json: {e}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
