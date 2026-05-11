#!/usr/bin/env python3
"""web_fetch — executor di Metnos v1.1."""
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


USER_AGENT = "Metnos/1.1 web_fetch"
MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB cap di sicurezza


def invoke(args):
    url = args.get("url")
    method = args.get("method", "GET")
    timeout = args.get("timeout_s", 10)

    if not url:
        return {"ok": False, "error": "missing required arg 'url'"}
    if method not in ("GET", "HEAD"):
        return {"ok": False, "error": f"only GET and HEAD supported, got '{method}'"}

    try:
        parsed = urlparse(url)
    except Exception as e:
        return {"ok": False, "error": f"invalid url: {e}"}
    if parsed.scheme not in ("http", "https"):
        return {"ok": False, "error": f"unsupported scheme '{parsed.scheme}'"}

    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})

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
                "ok": True,
                "content": text if text is not None else "",
                "metadata": {
                    "url": url,
                    "method": method,
                    "status": status,
                    "host": parsed.hostname,
                    "bytes": len(body),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "binary": text is None and len(body) > 0,
                },
            }
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "error": f"http error {e.code}: {e.reason}",
            "metadata": {"url": url, "method": method, "status": e.code, "host": parsed.hostname},
        }
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url error: {e.reason}"}
    except TimeoutError:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"unexpected: {type(e).__name__}: {e}"}


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        result = {"ok": False, "error": "empty input"}
    else:
        try:
            args = json.loads(raw)
            result = invoke(args)
        except json.JSONDecodeError as e:
            result = {"ok": False, "error": f"invalid input json: {e}"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
