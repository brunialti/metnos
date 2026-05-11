#!/usr/bin/env python3
"""Test http_cache (ADR 0105)."""
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, "/opt/myclaw/runtime")


def _isolate(tmpdir):
    """Redirect CACHE_ROOT to tmpdir."""
    import http_cache
    http_cache.CACHE_ROOT = Path(tmpdir) / "http"
    return http_cache


def test_canonical_url_lowercase_netloc():
    hc = _isolate(tempfile.mkdtemp())
    assert hc._canonical_url("HTTPS://EXAMPLE.COM/Foo") == "https://example.com/Foo"


def test_canonical_url_strips_default_port():
    hc = _isolate(tempfile.mkdtemp())
    assert hc._canonical_url("https://example.com:443/x") == "https://example.com/x"
    assert hc._canonical_url("http://example.com:80/x") == "http://example.com/x"


def test_canonical_url_strips_fragment():
    hc = _isolate(tempfile.mkdtemp())
    assert hc._canonical_url("https://example.com/x#frag") == "https://example.com/x"


def test_canonical_url_keeps_query():
    hc = _isolate(tempfile.mkdtemp())
    assert hc._canonical_url("https://EXAMPLE.com/x?a=1") == "https://example.com/x?a=1"


def test_put_and_get_roundtrip():
    tmp = tempfile.mkdtemp()
    try:
        hc = _isolate(tmp)
        cache = hc.HttpCache(ttl_s=300)
        cache.put("https://example.com/foo", "text/html", b"<html>hi</html>",
                  {"X-Foo": "bar"})
        got = cache.get("https://example.com/foo")
        assert got is not None
        assert got["body"] == b"<html>hi</html>"
        assert got["ctype"] == "text/html"
        assert got["headers"].get("X-Foo") == "bar"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_disabled_cache_ttl_zero():
    tmp = tempfile.mkdtemp()
    try:
        hc = _isolate(tmp)
        cache = hc.HttpCache(ttl_s=0)
        assert not cache.enabled()
        cache.put("https://example.com/foo", "text/html", b"x")  # no-op
        assert cache.get("https://example.com/foo") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_expired_returns_none():
    tmp = tempfile.mkdtemp()
    try:
        hc = _isolate(tmp)
        cache = hc.HttpCache(ttl_s=1)
        cache.put("https://example.com/foo", "text/html", b"x")
        time.sleep(1.2)
        assert cache.get("https://example.com/foo") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sharding_creates_subdirs():
    tmp = tempfile.mkdtemp()
    try:
        hc = _isolate(tmp)
        cache = hc.HttpCache(ttl_s=300)
        cache.put("https://a.example/1", "text/html", b"a")
        cache.put("https://b.example/2", "text/html", b"b")
        # Should have at least 1 shard subdir under CACHE_ROOT.
        shards = list(hc.CACHE_ROOT.iterdir())
        assert len(shards) >= 1
        for s in shards:
            assert len(s.name) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_clear_older_than_removes_entries():
    tmp = tempfile.mkdtemp()
    try:
        hc = _isolate(tmp)
        cache = hc.HttpCache(ttl_s=3600)
        cache.put("https://example.com/foo", "text/html", b"x")
        # Force mtime in past:
        for shard in hc.CACHE_ROOT.iterdir():
            for entry in shard.iterdir():
                old = time.time() - 10 * 24 * 3600
                os.utime(entry, (old, old))
        removed = cache.clear_older_than(7 * 24 * 3600)
        assert removed >= 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_canonical_url_consistent_keys():
    """Same URL with diff fragment/case → same key."""
    hc = _isolate(tempfile.mkdtemp())
    a = hc._key_for("HTTPS://Example.Com/x#frag")
    b = hc._key_for("https://example.com/x")
    assert a == b


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
