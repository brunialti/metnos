"""test_agent_server_remote — endpoint HTTP poll/result/heartbeat/executor.

Verifica firma sui bytes grezzi (§6.3), rifiuto firma assente/errata, bundle
executor verificato server-side, idempotenza via /agent/result.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class AgentServerRemoteTests(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        # Isola SOLO il DB device (temp); HOME resta reale così la chiave
        # 'author' (firma invocazioni/bundle) è raggiungibile.
        cls._tmp = tempfile.TemporaryDirectory()
        td = Path(cls._tmp.name)
        cls._orig_db = os.environ.get("METNOS_DEVICES_DB")
        os.environ["METNOS_DEVICES_DB"] = str(td / "devices.db")
        # Pin la dir executor al repo (altri test ricaricano config, §7.11).
        import config as _C
        cls._orig_exec = _C.PATH_EXECUTORS
        _C.PATH_EXECUTORS = _RUNTIME.parent / "executors"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        import config as _C
        _C.PATH_EXECUTORS = cls._orig_exec
        if cls._orig_db is not None:
            os.environ["METNOS_DEVICES_DB"] = cls._orig_db
        else:
            os.environ.pop("METNOS_DEVICES_DB", None)

    async def get_application(self):
        import importlib
        import devices
        import invocations
        import agent_server
        importlib.reload(devices)
        importlib.reload(invocations)
        importlib.reload(agent_server)
        self.devices = devices
        self.invocations = invocations
        # Device di test.
        self.priv = Ed25519PrivateKey.generate()
        pub_raw = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.pub_b64 = _b64u(pub_raw)
        token = devices.generate_token("test-dev")
        self.device = devices.consume_token(token, self.pub_b64)
        return agent_server.make_app()

    def _sign(self, raw: bytes) -> str:
        return _b64u(self.priv.sign(raw))

    async def _signed_post(self, path: str, body: dict):
        raw = json.dumps(body).encode("utf-8")
        return await self.client.post(
            path, data=raw,
            headers={"X-Metnos-Device-Sig": self._sign(raw),
                     "Content-Type": "application/json"})

    async def test_health(self):
        resp = await self.client.get("/agent/health")
        self.assertEqual(resp.status, 200)

    async def test_poll_missing_signature_rejected(self):
        resp = await self.client.post("/agent/poll", json={"device_id": self.device.id})
        self.assertEqual(resp.status, 401)

    async def test_poll_bad_signature_rejected(self):
        raw = json.dumps({"device_id": self.device.id, "block_ms": 0}).encode()
        resp = await self.client.post(
            "/agent/poll", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(b"\x00" * 64),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403)

    async def test_poll_empty_then_invocation(self):
        # coda vuota → invocation null
        resp = await self._signed_post(
            "/agent/poll", {"device_id": self.device.id, "block_ms": 0})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIsNone(data["invocation"])

        # accoda → poll la ritorna firmata
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        resp = await self._signed_post(
            "/agent/poll", {"device_id": self.device.id, "block_ms": 0})
        data = await resp.json()
        self.assertIsNotNone(data["invocation"])
        self.assertEqual(data["invocation"]["invocation_id"], inv_id)
        self.assertIn("server_sig", data["invocation"])

    async def test_result_roundtrip_and_idempotency(self):
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        self.invocations.next_invocation(self.device.id)
        result = {
            "invocation_id": inv_id, "device_id": self.device.id, "ok": True,
            "entries": [{"path": "/usr/bin/git"}], "n_processed": 1,
            "elapsed_ms": 3, "sandbox": "none",
        }
        resp = await self._signed_post("/agent/result", result)
        self.assertEqual(resp.status, 200)
        info = self.invocations.get_invocation(inv_id)
        self.assertEqual(info["state"], "done")

        # secondo result (ok:false) NON sovrascrive (§6.4)
        result2 = dict(result, ok=False, entries=[])
        resp = await self._signed_post("/agent/result", result2)
        self.assertEqual(resp.status, 200)
        info = self.invocations.get_invocation(inv_id)
        self.assertEqual(info["state"], "done")
        self.assertEqual(len(info["result"]["entries"]), 1)

    async def test_result_bad_signature_rejected(self):
        inv_id = self.invocations.enqueue_invocation(
            self.device.id, "find_packages", {"package_name": "git"})
        result = {
            "invocation_id": inv_id, "device_id": self.device.id, "ok": True,
            "entries": [], "n_processed": 0, "elapsed_ms": 1, "sandbox": "none",
        }
        raw = json.dumps(result).encode()
        resp = await self.client.post(
            "/agent/result", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(b"\x00" * 64),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403)

    async def test_heartbeat_updates_profile(self):
        resp = await self._signed_post(
            "/agent/heartbeat",
            {"device_id": self.device.id, "profile": {"cpu_count": 8}})
        self.assertEqual(resp.status, 200)
        dev = self.devices.get_device(self.device.id)
        self.assertIsNotNone(dev.last_heartbeat)
        self.assertIn("cpu_count", dev.profile_json or "")

    async def test_executor_bundle_verified(self):
        resp = await self.client.get("/agent/executor/find_packages")
        self.assertEqual(resp.status, 200)
        bundle = await resp.json()
        self.assertEqual(bundle["name"], "find_packages")
        self.assertIn("find_packages.py", bundle["files"])
        # digest del codice = quello atteso.
        expected_manifest_sha, expected_code_sha = self.invocations.executor_shas(
            "find_packages")
        manifest_bytes = base64.b64decode(bundle["manifest_toml"])
        import hashlib
        self.assertEqual(hashlib.sha256(manifest_bytes).hexdigest(),
                         expected_manifest_sha)

    async def test_executor_bundle_unknown_404(self):
        resp = await self.client.get("/agent/executor/no_such_xyz")
        self.assertEqual(resp.status, 404)

    async def test_shim_bundle_signed(self):
        resp = await self.client.get("/agent/shim")
        self.assertEqual(resp.status, 200)
        bundle = await resp.json()
        self.assertIn("executor_helpers.py", bundle["files"])
        self.assertIn("messages.py", bundle["files"])
        self.assertIn("sig", bundle)


if __name__ == "__main__":
    unittest.main()
