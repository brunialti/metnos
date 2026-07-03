"""test_admin_device_test_invoke — POST /admin/devices/{id}/test-invoke
(W3.3, design doc §16.4): trigger manuale di un'invocazione reale su un
device appaiato, per la validazione E2E su hardware Windows senza il
bypass diretto-al-DB dello script bash di test.

Va attraverso la pipeline REALE (enqueue → next_invocation lato "client"
simulato → complete_invocation → wait_result): non un mock del protocollo.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RUNTIME))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import AioHTTPTestCase  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class AdminTestInvokeTests(AioHTTPTestCase):
    """App minima: solo la route sotto test, niente auth middleware (già
    coperta a parte da http_auth.py: /admin/* -> role=admin, invariato)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        td = Path(cls._tmp.name)
        cls._orig_db = os.environ.get("METNOS_DEVICES_DB")
        os.environ["METNOS_DEVICES_DB"] = str(td / "devices.db")
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
        import loader
        import http_routes_admin
        importlib.reload(devices)
        importlib.reload(invocations)
        importlib.reload(http_routes_admin)
        self.devices = devices
        self.invocations = invocations
        self.loader = loader

        self.priv = Ed25519PrivateKey.generate()
        pub_raw = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        token = devices.generate_token("win-test")
        self.device = devices.consume_token(token, _b64u(pub_raw))

        app = web.Application()
        app.router.add_post(
            "/admin/devices/{id}/test-invoke",
            http_routes_admin.admin_device_test_invoke)
        return app

    def _sign(self, raw: bytes) -> str:
        return _b64u(self.priv.sign(raw))

    async def _simulate_client_completes(self, delay_s: float = 0.3):
        """Simula il client: dopo un breve delay, preleva la prossima
        invocazione in coda e la completa con un result onesto."""
        await asyncio.sleep(delay_s)
        loop = asyncio.get_running_loop()
        inv = await loop.run_in_executor(
            None, lambda: self.invocations.next_invocation(self.device.id))
        self.assertIsNotNone(inv, "nessuna invocazione in coda da completare")
        result = {
            "invocation_id": inv["invocation_id"], "device_id": self.device.id,
            "ok": True, "entries": [{"path": "/tmp/x.py", "loc": 3}],
            "n_processed": 1, "elapsed_ms": 5, "sandbox": "job-object",
        }
        raw = json.dumps(result).encode()
        await loop.run_in_executor(
            None, lambda: self.invocations.complete_invocation(
                result, raw_body=raw, sig_b64=self._sign(raw)))

    async def test_missing_executor_field_400(self):
        resp = await self.client.post(
            f"/admin/devices/{self.device.id}/test-invoke", json={})
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertEqual(data["error"], "missing_field")

    async def test_unknown_device_404(self):
        resp = await self.client.post(
            "/admin/devices/ffffffffffffffffffffffffffffffff/test-invoke",
            json={"executor": "compute_files_loc"})
        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertEqual(data["error"], "unknown_device")

    async def test_revoked_device_404(self):
        self.devices.revoke_device(self.device.id)
        resp = await self.client.post(
            f"/admin/devices/{self.device.id}/test-invoke",
            json={"executor": "compute_files_loc"})
        self.assertEqual(resp.status, 404)

    async def test_unknown_executor_404(self):
        resp = await self.client.post(
            f"/admin/devices/{self.device.id}/test-invoke",
            json={"executor": "this_executor_does_not_exist_xyz"})
        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertEqual(data["error"], "unknown_executor")

    async def test_invalid_json_body_400(self):
        resp = await self.client.post(
            f"/admin/devices/{self.device.id}/test-invoke",
            data=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)

    async def test_full_roundtrip_returns_result(self):
        # Un executor VERO deve esistere firmato nel catalogo (gate §8) —
        # compute_files_loc e' fra i promossi platforms=["linux","windows"]
        # (W3.2, oggi stesso), quindi e' il candidato naturale per il
        # runbook W3.3.
        deliver = asyncio.ensure_future(self._simulate_client_completes())
        try:
            resp = await self.client.post(
                f"/admin/devices/{self.device.id}/test-invoke",
                json={"executor": "compute_files_loc",
                     "args": {"paths": ["/tmp"]}, "deadline_ms": 5000})
        finally:
            await deliver
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data["state"], "done")
        self.assertTrue(data["result"]["ok"])
        self.assertEqual(data["result"]["sandbox"], "job-object")

    async def test_timeout_returns_202_pending(self):
        # Nessun "client" a completare: la deadline (minima, per un test
        # veloce) scade e l'endpoint risponde onestamente pending, mai un
        # timeout silenzioso (§12).
        resp = await self.client.post(
            f"/admin/devices/{self.device.id}/test-invoke",
            json={"executor": "compute_files_loc", "deadline_ms": 100})
        self.assertEqual(resp.status, 202)
        data = await resp.json()
        self.assertIn(data["state"], ("queued", "delivered"))


if __name__ == "__main__":
    unittest.main()
