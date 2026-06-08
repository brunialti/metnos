"""SchedulerDaemon weakref handle module — set/get/clear + GC behaviour."""
from __future__ import annotations

import gc

from scheduler_v2 import daemon_handle


def test_get_active_returns_none_when_unset():
    daemon_handle.clear()
    assert daemon_handle.get_active() is None


def test_set_get_clear_roundtrip():
    class FakeDaemon:
        def kick(self):
            pass

    daemon_handle.clear()
    d = FakeDaemon()
    daemon_handle.set_active(d)  # type: ignore[arg-type]
    got = daemon_handle.get_active()
    assert got is d
    daemon_handle.clear()
    assert daemon_handle.get_active() is None


def test_weakref_releases_after_gc():
    """If the only reference to the daemon is the weakref, get_active returns None."""
    class FakeDaemon:
        def kick(self):
            pass

    daemon_handle.clear()
    d = FakeDaemon()
    daemon_handle.set_active(d)  # type: ignore[arg-type]
    assert daemon_handle.get_active() is d
    del d
    gc.collect()
    assert daemon_handle.get_active() is None


def test_set_active_overwrites_previous():
    class FakeDaemon:
        def kick(self):
            pass

    daemon_handle.clear()
    d1 = FakeDaemon()
    d2 = FakeDaemon()
    daemon_handle.set_active(d1)  # type: ignore[arg-type]
    daemon_handle.set_active(d2)  # type: ignore[arg-type]
    assert daemon_handle.get_active() is d2
    daemon_handle.clear()
