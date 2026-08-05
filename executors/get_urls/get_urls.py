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


def _failed(url: str, error: str, error_class: str,
            error_code: str, **extra) -> dict:
    return {
        "failed": True,
        "url": url,
        "error": error,
        "error_class": error_class,
        "error_code": error_code,
        **extra,
    }


def _invalid_result(error: str, error_code: str) -> dict:
    return {
        "ok": False,
        "entries": [],
        "ok_count": 0,
        "fail_count": 1,
        "failed": [{
            "url": "",
            "error": error,
            "error_class": "invalid_input",
            "error_code": error_code,
        }],
        "summary": error,
        "error": error,
        "error_class": "invalid_input",
        "error_code": error_code,
    }


def _fetch_one(url: str, method: str, timeout: int) -> dict:
    """Fetch a single URL. Returns either an entry dict (success) or
    a failed dict with url/error (failure)."""
    if not url:
        return _failed("", _msg("ERR_ARG_MISSING", arg="url"),
                       "invalid_input", "url_missing")
    try:
        parsed = urlparse(url)
    except Exception as e:
        return _failed(
            url, _msg("ERR_ARG_INVALID", arg="url", reason=str(e)),
            "invalid_input", "invalid_url")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _failed(
            url, _msg("ERR_NOT_APPLICABLE", what=f"scheme '{parsed.scheme}'"),
            "invalid_input", "invalid_url_scheme")

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
        return _failed(
            url, f"http error {e.code}: {e.reason}",
            "http", f"http_{e.code}", status_code=e.code)
    except urllib.error.URLError as e:
        return _failed(url, f"url error: {e.reason}",
                       "network", "network_error")
    except TimeoutError:
        return _failed(url, _msg("ERR_TIMEOUT"), "timeout", "timeout")
    except Exception as e:
        return _failed(url, f"unexpected: {type(e).__name__}: {e}",
                       "unknown", "unexpected_error")


def invoke(args):
    if not isinstance(args, dict):
        return _invalid_result(_msg("ERR_ARGS_NOT_OBJECT"),
                               "args_not_object")
    method = args.get("method", "GET")
    timeout = args.get("timeout_s", 10)

    if not isinstance(method, str):
        return _invalid_result(_msg("ERR_ARG_NOT_STRING", arg="method"),
                               "method_not_string")
    if method not in ("GET", "HEAD"):
        return _invalid_result(
            _msg("ERR_NOT_APPLICABLE", what=f"method '{method}'"),
            "method_unsupported")
    if (isinstance(timeout, bool) or not isinstance(timeout, int)
            or not 1 <= timeout <= 60):
        return _invalid_result(_msg(
            "ERR_ARG_INVALID", arg="timeout_s", reason="expected 1..60"),
                               "timeout_invalid")

    # Accept either `url` (singular, manifest contract) or `urls` (vectorial).
    urls_arg = args.get("urls")
    url_arg = args.get("url")
    if url_arg is not None and urls_arg is not None:
        return _invalid_result(
            _msg("ERR_ARG_INVALID", arg="url/urls",
                 reason="the two forms are mutually exclusive"),
            "url_urls_conflict")
    if urls_arg is None:
        if url_arg is not None and not isinstance(url_arg, str):
            return _invalid_result(_msg("ERR_ARG_NOT_STRING", arg="url"),
                                   "url_not_string")
        urls = [url_arg] if url_arg else []
    elif isinstance(urls_arg, list):
        urls = list(urls_arg)
    else:
        return _invalid_result(
            _msg("ERR_ARG_NOT_LIST_OF", arg="urls", of="strings"),
            "urls_not_array")

    if not urls:
        return _invalid_result(_msg("ERR_ARG_MISSING", arg="url"),
                               "url_missing")

    entries: list = []
    failed: list = []
    for u in urls:
        if not isinstance(u, str) or not u:
            failed.append({
                "url": str(u),
                "error": _msg("ERR_INVALID_URL"),
                "error_class": "invalid_input",
                "error_code": "invalid_url",
            })
            continue
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

    result = {
        "ok": ok,
        "entries": entries,
        "ok_count": ok_count,
        "fail_count": fail_count,
        "failed": failed,
        "summary": summary,
    }
    if entries and failed:
        result["partial"] = True
    if not entries and failed:
        result["error"] = failed[0]["error"]
        result["error_class"] = failed[0].get("error_class", "unknown")
        result["error_code"] = failed[0].get("error_code", "url_failed")
    return result


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
