from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "executors" / "find_urls" / "find_urls.py"
SPEC = importlib.util.spec_from_file_location(
    "find_urls_parallel_executor", MODULE_PATH)
assert SPEC and SPEC.loader
find_urls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = find_urls
SPEC.loader.exec_module(find_urls)


class _Response:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self._body


class _Opener:
    def __init__(self, pages: dict[str, str]):
        self._pages = pages

    def open(self, request, timeout=None):
        del timeout
        url = getattr(request, "full_url", request)
        return _Response(self._pages[str(url)])


class _NoopThrottle:
    def __init__(self, **_kwargs):
        pass

    def acquire(self, _host):
        pass

    def release(self, _host):
        pass


def test_parallel_bfs_is_structurally_equivalent_to_serial(monkeypatch):
    root = "https://parallel.test/"
    pages = {
        root: """
            <html><title>Root</title><body>
              <a href='/a'>A</a><a href='/b'>B</a><a href='/c'>C</a>
            </body></html>
        """,
        root + "a": "<html><title>A</title></html>",
        root + "b": "<html><title>B</title></html>",
        root + "c": "<html><title>C</title></html>",
    }
    monkeypatch.setattr(
        find_urls.urllib.request, "build_opener", lambda *_args: _Opener(pages))
    monkeypatch.setattr(find_urls, "_try_sitemap", lambda *_args: [])
    monkeypatch.setattr(find_urls, "_resolve_tier", lambda *_args: 3)
    monkeypatch.setattr(find_urls, "HostThrottle", _NoopThrottle)
    monkeypatch.setattr(find_urls.time, "time", lambda: 123456.0)
    monkeypatch.setattr(find_urls.time, "sleep", lambda _seconds: None)
    monkeypatch.delenv("METNOS_FIND_URLS_GLOBAL_MAX", raising=False)
    args = {
        "seed_urls": [root],
        "max_depth": 1,
        "max_pages": 20,
        "max_per_domain": 0,
        "respect_robots": False,
        "rate_limit_ms": 50,
    }

    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "1")
    serial = find_urls._invoke_default(args)
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "32")
    parallel = find_urls._invoke_default(args)

    assert serial == parallel
    assert parallel["ok"] is True
    assert [entry["url"] for entry in parallel["entries"]] == [
        root, root + "a", root + "b", root + "c",
    ]


def test_web_budget_override_can_only_lower_central_assignment(monkeypatch):
    monkeypatch.setenv("METNOS_EXECUTOR_ASSIGNED_WORKERS", "12")
    monkeypatch.setenv("METNOS_FIND_URLS_GLOBAL_MAX", "64")
    assert find_urls._host_capacity()["global_max"] == 12

    monkeypatch.setenv("METNOS_FIND_URLS_GLOBAL_MAX", "5")
    assert find_urls._host_capacity()["global_max"] == 5
