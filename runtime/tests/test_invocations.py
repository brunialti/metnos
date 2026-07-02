"""test_invocations — coda invocazioni firmate + canonical JSON (executor remoti).

Copre: canonical bytes deterministici (contratto col client Rust), firma
server (server_sig), claim atomico, idempotenza §6.4, verifica firma device
sui bytes GREZZI (float-safe §6.3), redelivery orfane, cursor.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

import devices  # noqa: E402
import invocations  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class DeviceKey:
    """Coppia di chiavi device + registrazione in un DB temporaneo."""

    def __init__(self, db_path: Path, name: str = "test-dev"):
        self.priv = Ed25519PrivateKey.generate()
        pub_raw = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.pub_b64 = _b64u(pub_raw)
        token = devices.generate_token(name, db_path=db_path)
        self.device = devices.consume_token(token, self.pub_b64, db_path=db_path)

    def sign(self, raw: bytes) -> str:
        return _b64u(self.priv.sign(raw))


class CanonicalTests(unittest.TestCase):
    def test_deterministic_sorted_compact(self):
        b = invocations.canonical_bytes({"b": 1, "a": {"z": True, "e": ["x", 2]}})
        self.assertEqual(b, b'{"a":{"e":["x",2],"z":true},"b":1}')

    def test_non_ascii_not_escaped(self):
        b = invocations.canonical_bytes({"k": "città"})
        self.assertEqual(b, "{\"k\":\"città\"}".encode("utf-8"))

    def test_rejects_float(self):
        with self.assertRaises(invocations.InvocationError):
            invocations.canonical_bytes({"x": 1.5})


class InvocationQueueTests(unittest.TestCase):
    def setUp(self):
        # Pin la dir executor al repo: altri test nel suite ricaricano `config`
        # con una root temporanea (§7.11), lasciando PATH_EXECUTORS altrove.
        import config as _C
        self._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "devices.db"
        self.key = DeviceKey(self.db)
        self.device_id = self.key.device.id

    def tearDown(self):
        import config as _C
        _C.PATH_EXECUTORS = self._orig_exec
        self._tmp.cleanup()

    def test_enqueue_and_server_sig_verifies(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"},
            db_path=self.db)
        wire = invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertIsNotNone(wire)
        self.assertEqual(wire["invocation_id"], inv_id)
        # server_sig verifica sui bytes canonici del payload senza il campo sig.
        sig = wire.pop("server_sig")
        server_pub = invocations.server_public_key_b64()
        self.assertTrue(invocations.verify_payload(server_pub, sig, wire))

    def test_next_invocation_empty(self):
        self.assertIsNone(invocations.next_invocation(self.device_id, db_path=self.db))

    def test_cursor_excludes_seen(self):
        a = invocations.enqueue_invocation(self.device_id, "find_packages",
                                           {"package_name": "git"}, db_path=self.db)
        b = invocations.enqueue_invocation(self.device_id, "find_packages",
                                           {"package_name": "curl"}, db_path=self.db)
        first = invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertEqual(first["invocation_id"], a)
        # con cursor = a, la prossima e' b (a e' delivered e non ancora scaduta)
        second = invocations.next_invocation(self.device_id, cursor=a, db_path=self.db)
        self.assertEqual(second["invocation_id"], b)

    def _complete(self, inv_id, *, ok=True, entries=None, tamper=False):
        result = {
            "invocation_id": inv_id,
            "device_id": self.device_id,
            "ok": ok,
            "entries": entries if entries is not None else [{"path": "/usr/bin/git"}],
            "n_processed": 1,
            "elapsed_ms": 5,
            "sandbox": "bwrap",
        }
        raw = json.dumps(result).encode("utf-8")
        sig = self.key.sign(raw)
        if tamper:
            raw = raw + b" "  # bytes diversi da quelli firmati
        return invocations.complete_invocation(
            result, raw_body=raw, sig_b64=sig, db_path=self.db)

    def test_complete_valid_sig(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(info["result"]["entries"][0]["path"], "/usr/bin/git")

    def test_complete_bad_sig_rejected(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        with self.assertRaises(invocations.SignatureError):
            self._complete(inv_id, tamper=True)
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "delivered")  # non registrato

    def test_idempotent_first_result_wins(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id, ok=True))
        # secondo result (anche con ok diverso) non sovrascrive (§6.4)
        self.assertTrue(self._complete(inv_id, ok=False, entries=[]))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(len(info["result"]["entries"]), 1)

    def test_float_entries_survive_raw_verification(self):
        """entries con float (punteggi) NON rompono la firma (§6.3 raw)."""
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_files", {"q": "x"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        self.assertTrue(self._complete(inv_id, entries=[{"score": 1.5, "id": "a"}]))
        info = invocations.get_invocation(inv_id, db_path=self.db)
        self.assertEqual(info["state"], "done")
        self.assertEqual(info["result"]["entries"][0]["score"], 1.5)

    def test_result_from_other_device_rejected(self):
        inv_id = invocations.enqueue_invocation(
            self.device_id, "find_packages", {"package_name": "git"}, db_path=self.db)
        invocations.next_invocation(self.device_id, db_path=self.db)
        other = DeviceKey(self.db, name="intruder")
        result = {
            "invocation_id": inv_id, "device_id": other.device.id,
            "ok": True, "entries": [], "n_processed": 0,
            "elapsed_ms": 1, "sandbox": "none",
        }
        raw = json.dumps(result).encode("utf-8")
        sig = other.sign(raw)
        with self.assertRaises(invocations.SignatureError):
            invocations.complete_invocation(result, raw_body=raw, sig_b64=sig,
                                            db_path=self.db)

    def test_unknown_executor_rejected(self):
        with self.assertRaises(invocations.InvocationError):
            invocations.enqueue_invocation(
                self.device_id, "no_such_executor_xyz", {}, db_path=self.db)


if __name__ == "__main__":
    unittest.main()
