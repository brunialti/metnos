#!/usr/bin/env python3
"""Test host_health auto-degrade T2→T1 (ADR 0108)."""
import sys
import time
import tempfile
import shutil
from pathlib import Path



def _isolate(tmpdir):
    """Redirect storage paths to tmpdir."""
    import host_health
    base = Path(tmpdir)
    host_health.HEALTH_PATH = base / "host_health.json"
    host_health.BLOCKED_PATH = base / "blocked_origins.json"
    return host_health


def test_record_429_under_threshold_no_block():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        hh.record_response("a.example", 429)
        hh.record_response("a.example", 429)
        assert hh.maybe_block_host("a.example") is False
        assert hh.is_blocked("a.example") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_429_threshold_triggers_block():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        for _ in range(3):
            hh.record_response("b.example", 429)
        assert hh.maybe_block_host("b.example") is True
        assert hh.is_blocked("b.example") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_503_counts_too():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        hh.record_response("c.example", 503)
        hh.record_response("c.example", 503)
        hh.record_response("c.example", 503)
        assert hh.maybe_block_host("c.example") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_200_does_not_count():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        for _ in range(10):
            hh.record_response("d.example", 200)
        assert hh.maybe_block_host("d.example") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_block_ttl_expire_24h():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        for _ in range(3):
            hh.record_response("e.example", 429)
        hh.maybe_block_host("e.example")
        # Force TTL to past:
        import json
        data = json.loads(hh.BLOCKED_PATH.read_text())
        data["ttl"]["e.example"] = time.time() - 60
        hh.BLOCKED_PATH.write_text(json.dumps(data))
        # is_blocked should now return False (cleanup lazy)
        assert hh.is_blocked("e.example") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cleanup_expired_removes():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        for _ in range(3):
            hh.record_response("f.example", 429)
        hh.maybe_block_host("f.example")
        import json
        data = json.loads(hh.BLOCKED_PATH.read_text())
        data["ttl"]["f.example"] = time.time() - 60
        hh.BLOCKED_PATH.write_text(json.dumps(data))
        n = hh.cleanup_expired()
        assert n == 1
        assert hh.is_blocked("f.example") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_idempotent_block():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        for _ in range(3):
            hh.record_response("g.example", 429)
        assert hh.maybe_block_host("g.example") is True
        assert hh.maybe_block_host("g.example") is True  # refresh TTL
        import json
        data = json.loads(hh.BLOCKED_PATH.read_text())
        # Solo 1 entry per host
        assert data["hosts"].count("g.example") == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_window_prune_old_events():
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        # Inject vecchi (oltre window)
        import json
        old_ts = time.time() - 2 * hh.WINDOW_S
        hh.HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        hh.HEALTH_PATH.write_text(json.dumps({
            "hosts": {
                "h.example": {"events": [
                    {"ts": old_ts, "code": 429},
                    {"ts": old_ts, "code": 429},
                ]}
            }
        }))
        hh.record_response("h.example", 429)  # prune + 1 nuovo
        assert hh.maybe_block_host("h.example") is False  # solo 1 in window


    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_manual_listed_host_no_ttl():
    """Host listato a mano (senza TTL) resta blocked permanente."""
    tmp = tempfile.mkdtemp()
    try:
        hh = _isolate(tmp)
        import json
        hh.BLOCKED_PATH.parent.mkdir(parents=True, exist_ok=True)
        hh.BLOCKED_PATH.write_text(json.dumps({"hosts": ["manual.example"]}))
        assert hh.is_blocked("manual.example") is True
        assert hh.cleanup_expired() == 0
        assert hh.is_blocked("manual.example") is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
