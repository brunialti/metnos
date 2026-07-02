"""test_placement — funzione pura choose_placement (§10 executor remoti)."""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import placement  # noqa: E402


@dataclass
class FakeDevice:
    id: str
    name: str
    last_heartbeat: str | None
    revoked_at: str | None = None


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.fresh = _iso(self.now - timedelta(seconds=5))
        self.stale = _iso(self.now - timedelta(seconds=120))

    def test_no_placement_is_server(self):
        self.assertEqual(placement.choose_placement(None, None, [], now=self.now),
                         placement.SERVER)

    def test_scope_any_is_server(self):
        self.assertEqual(
            placement.choose_placement({"scope": "any"}, None, [], now=self.now),
            placement.SERVER)

    def test_scope_server_forced_even_with_device_hint(self):
        dev = FakeDevice("d-1", "laptop", self.fresh)
        self.assertEqual(
            placement.choose_placement(
                {"scope": "server"}, {"device": "laptop"}, [dev], now=self.now),
            placement.SERVER)

    def test_scope_device_single_available(self):
        dev = FakeDevice("d-1", "laptop", self.fresh)
        self.assertEqual(
            placement.choose_placement({"scope": "device"}, None, [dev], now=self.now),
            "d-1")

    def test_scope_device_none_available_raises(self):
        dev = FakeDevice("d-1", "laptop", self.stale)
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement({"scope": "device"}, None, [dev], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_NONE_AVAILABLE")

    def test_scope_device_ambiguous_raises(self):
        d1 = FakeDevice("d-1", "laptop", self.fresh)
        d2 = FakeDevice("d-2", "desktop", self.fresh)
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement({"scope": "device"}, None, [d1, d2], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_AMBIGUOUS")

    def test_user_override_by_name(self):
        d1 = FakeDevice("d-1", "laptop", self.fresh)
        d2 = FakeDevice("d-2", "desktop", self.fresh)
        self.assertEqual(
            placement.choose_placement(
                {"scope": "device"}, {"device": "desktop"}, [d1, d2], now=self.now),
            "d-2")

    def test_user_override_unknown_device_raises(self):
        d1 = FakeDevice("d-1", "laptop", self.fresh)
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement(
                {"scope": "device"}, {"device": "phone"}, [d1], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_UNKNOWN")

    def test_user_override_unreachable_raises(self):
        d1 = FakeDevice("d-1", "laptop", self.stale)
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement(
                {"scope": "device"}, {"device": "laptop"}, [d1], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_UNREACHABLE")

    def test_revoked_device_not_available(self):
        dev = FakeDevice("d-1", "laptop", self.fresh, revoked_at=_iso(self.now))
        self.assertFalse(placement.is_available(dev, self.now))


if __name__ == "__main__":
    unittest.main()
