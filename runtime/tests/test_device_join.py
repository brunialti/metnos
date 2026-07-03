"""test_device_join — flusso install-at-the-fly dalla UI (§5 design doc).

Copre: join session lifecycle (stati monotoni, expiry), pagina join no-auth,
installer personalizzato (server+token baked, marca downloaded), aggancio
register→registered e heartbeat→heartbeat, boundary 404/410.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class JoinSessionModelTests(unittest.TestCase):
    """Lifecycle sqlite puro (senza HTTP)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["METNOS_DEVICES_DB"] = str(Path(self._tmp.name) / "devices.db")
        import importlib
        import devices
        importlib.reload(devices)
        self.devices = devices

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("METNOS_DEVICES_DB", None)

    def test_lifecycle_monotonic(self):
        s = self.devices.create_join_session("pc-1", server_url="http://x:8765")
        self.assertEqual(s["state"], "created")
        self.assertTrue(self.devices.mark_join_state(s["join_id"], "opened"))
        # regressione bloccata
        self.assertFalse(self.devices.mark_join_state(s["join_id"], "created"))
        # idempotenza: stesso stato due volte = secondo no-op
        self.assertFalse(self.devices.mark_join_state(s["join_id"], "opened"))
        self.assertTrue(self.devices.mark_join_state(s["join_id"], "downloaded"))

    def test_register_link_and_heartbeat(self):
        s = self.devices.create_join_session("pc-2")
        pub = _b64u(b"k" * 32)
        dev = self.devices.consume_token(s["token"], pub, os_family="linux")
        self.assertTrue(
            self.devices.mark_join_registered_by_token(s["token"], dev.id))
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")
        self.devices.heartbeat(dev.id)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "heartbeat")

    def test_expiry_computed(self):
        s = self.devices.create_join_session("pc-3", ttl_seconds=1)
        self.assertEqual(s["state"], "created")
        time.sleep(1.2)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "expired")

    def test_registered_never_expires(self):
        s = self.devices.create_join_session("pc-4", ttl_seconds=1)
        pub = _b64u(b"j" * 32)
        dev = self.devices.consume_token(s["token"], pub)
        self.devices.mark_join_registered_by_token(s["token"], dev.id)
        time.sleep(1.2)
        # una registrazione riuscita non torna 'expired' dopo il TTL token
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")

    def test_unknown_join(self):
        self.assertIsNone(self.devices.get_join_session("deadbeef00000000"))


class JoinHttpTests(AioHTTPTestCase):
    """Route /agent/client/join/* + aggancio register reale."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        td = Path(cls._tmp.name)
        cls._orig_db = os.environ.get("METNOS_DEVICES_DB")
        os.environ["METNOS_DEVICES_DB"] = str(td / "devices.db")
        # Mirror finto: installer minimi con i punti d'iniezione reali.
        cls._mirror = td / "client"
        cls._mirror.mkdir(parents=True)
        (cls._mirror / "install.sh").write_text(
            "#!/bin/sh\n: \"${METNOS_SERVER:?}\"\n: \"${METNOS_TOKEN:?}\"\n")
        (cls._mirror / "install.ps1").write_text(
            "if (-not $env:METNOS_SERVER) { throw 'x' }\n")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        if cls._orig_db is not None:
            os.environ["METNOS_DEVICES_DB"] = cls._orig_db
        else:
            os.environ.pop("METNOS_DEVICES_DB", None)

    async def get_application(self):
        import importlib
        import devices
        import invocations
        import agent_mirror
        import agent_server
        importlib.reload(devices)
        importlib.reload(invocations)
        importlib.reload(agent_server)
        self.devices = devices
        agent_mirror.MIRROR_CLIENT_DIR = self._mirror
        return agent_server.make_app()

    def _new_session(self, name="pc-http", **kw):
        return self.devices.create_join_session(name, **kw)

    async def test_join_page_marks_opened(self):
        s = self._new_session()
        resp = await self.client.get(f"/agent/client/join/{s['join_id']}")
        self.assertEqual(resp.status, 200)
        text = await resp.text()
        self.assertIn(s["join_id"], text)
        self.assertIn("pc-http", text)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "opened")

    async def test_join_page_unknown_404(self):
        resp = await self.client.get("/agent/client/join/ffffffffffffffff")
        self.assertEqual(resp.status, 404)

    async def test_installer_bakes_token_and_marks_downloaded(self):
        s = self._new_session("pc-sh")
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=linux")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertIn(s["token"], body)
        self.assertIn("METNOS_SERVER=", body)
        self.assertTrue(body.startswith("#!/bin/sh"))
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "downloaded")

    async def test_installer_windows_prelude(self):
        s = self._new_session("pc-ps1")
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=windows")
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertTrue(body.startswith("$env:METNOS_SERVER"))
        self.assertIn(s["token"], body)
        self.assertIn("MetnosClientSetup.ps1",
                      resp.headers.get("Content-Disposition", ""))

    async def test_installer_expired_410(self):
        s = self._new_session("pc-exp", ttl_seconds=1)
        time.sleep(1.2)
        resp = await self.client.get(
            f"/agent/client/join/{s['join_id']}/installer?platform=linux")
        self.assertEqual(resp.status, 410)

    async def test_register_advances_session(self):
        s = self._new_session("pc-reg")
        priv = Ed25519PrivateKey.generate()
        pub_b64 = _b64u(priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw))
        resp = await self.client.post("/agent/register", json={
            "token": s["token"], "public_key": pub_b64, "os_family": "linux"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "registered")

        # heartbeat firmato → sessione completata
        body = {"device_id": data["device_id"]}
        raw = json.dumps(body).encode()
        resp = await self.client.post(
            "/agent/heartbeat", data=raw,
            headers={"X-Metnos-Device-Sig": _b64u(priv.sign(raw)),
                     "Content-Type": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            self.devices.get_join_session(s["join_id"])["state"], "heartbeat")

    async def test_status_endpoint(self):
        s = self._new_session("pc-status")
        resp = await self.client.get(f"/agent/client/join/{s['join_id']}/status")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["state"], "created")
        self.assertEqual(data["device_name"], "pc-status")


if __name__ == "__main__":
    unittest.main()
