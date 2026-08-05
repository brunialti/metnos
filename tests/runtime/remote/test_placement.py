"""test_placement — funzione pura choose_placement (§10 executor remoti)."""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

_RUNTIME = (Path(__file__).resolve().parents[3] / "runtime")

import placement  # noqa: E402


@dataclass
class FakeDevice:
    id: str
    name: str
    last_heartbeat: str | None
    revoked_at: str | None = None
    os_family: str | None = None


@dataclass
class PollAwareDevice:
    id: str
    name: str
    last_heartbeat: str | None
    last_poll: str | None
    revoked_at: str | None = None
    os_family: str | None = None


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

    def test_fresh_heartbeat_with_stale_worker_poll_is_not_available(self):
        dev = PollAwareDevice(
            "d-1", "laptop", self.fresh, self.stale,
        )
        self.assertFalse(placement.is_available(dev, self.now))

    def test_fresh_heartbeat_and_worker_poll_are_available(self):
        dev = PollAwareDevice(
            "d-1", "laptop", self.fresh, self.fresh,
        )
        self.assertTrue(placement.is_available(dev, self.now))

    def test_migrated_device_without_first_poll_is_not_available(self):
        dev = PollAwareDevice(
            "d-1", "laptop", self.fresh, None,
        )
        self.assertFalse(placement.is_available(dev, self.now))

    # --- gate platforms (W3.0/W3.2, §16.1/§16.3) --------------------------

    def test_scope_device_windows_no_platforms_declared_raises(self):
        dev = FakeDevice("d-1", "laptop-win", self.fresh, os_family="windows")
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement(
                {"scope": "device"}, None, [dev], now=self.now,
                executor_name="find_packages")
        self.assertEqual(cm.exception.code, "ERR_DEVICE_PLATFORM_UNSUPPORTED")
        self.assertEqual(cm.exception.fmt["os"], "windows")
        self.assertEqual(cm.exception.fmt["executor"], "find_packages")

    def test_scope_device_windows_with_platforms_declared_passes(self):
        dev = FakeDevice("d-1", "laptop-win", self.fresh, os_family="windows")
        self.assertEqual(
            placement.choose_placement(
                {"scope": "device"}, None, [dev], now=self.now,
                platforms=["linux", "windows"]),
            "d-1")

    def test_scope_device_no_os_family_defaults_linux(self):
        # Device pairato ma senza os_family valorizzato: default "linux",
        # coerente col default del manifest (mai jolly universale).
        dev = FakeDevice("d-1", "laptop", self.fresh, os_family=None)
        self.assertEqual(
            placement.choose_placement({"scope": "device"}, None, [dev], now=self.now),
            "d-1")

    def test_user_override_platform_mismatch_raises(self):
        # Il nome esplicito non scavalca l'incompatibilita': l'utente ha
        # scelto il device, non il crash remoto che ne conseguirebbe.
        dev = FakeDevice("d-1", "laptop-win", self.fresh, os_family="windows")
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement(
                {"scope": "device"}, {"device": "laptop-win"}, [dev], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_PLATFORM_UNSUPPORTED")

    def test_user_override_platform_match_passes(self):
        dev = FakeDevice("d-1", "laptop-win", self.fresh, os_family="windows")
        self.assertEqual(
            placement.choose_placement(
                {"scope": "device"}, {"device": "laptop-win"}, [dev], now=self.now,
                platforms=["linux", "windows"]),
            "d-1")

    def test_scope_device_picks_compatible_among_mixed(self):
        # Due device disponibili, uno solo compatibile: NON e' ambiguo,
        # il filtro platforms scarta l'incompatibile prima della scelta.
        d_linux = FakeDevice("d-1", "server-nas", self.fresh, os_family="linux")
        d_win = FakeDevice("d-2", "laptop-win", self.fresh, os_family="windows")
        self.assertEqual(
            placement.choose_placement(
                {"scope": "device"}, None, [d_linux, d_win], now=self.now),
            "d-1")

    def test_scope_device_all_incompatible_raises_platform_not_none(self):
        # Device raggiungibili ma NESSUNO compatibile: errore distinto da
        # "nessun device raggiungibile" (diagnosi diversa, §2.8).
        dev = FakeDevice("d-1", "laptop-win", self.fresh, os_family="windows")
        with self.assertRaises(placement.PlacementError) as cm:
            placement.choose_placement({"scope": "device"}, None, [dev], now=self.now)
        self.assertEqual(cm.exception.code, "ERR_DEVICE_PLATFORM_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
